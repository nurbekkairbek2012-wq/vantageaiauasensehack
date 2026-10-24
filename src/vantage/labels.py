"""Автоматическая разметка: где взять примеры, не размечая руками.

Идея, которая экономит команде день работы
------------------------------------------
Размечать вручную нужно не всё. Значительную часть обучающей выборки
можно собрать из открытых данных, и для Астаны они есть.

**Положительные примеры.** В OpenStreetMap размечены официальные
полигоны ТБО, площадки временного накопления, мусороперегрузочные
станции. Это законные объекты — но нас интересует не их законность,
а их **спектральная и текстурная подпись**. Поверхность из отходов
выглядит одинаково независимо от того, есть ли на неё разрешение.

Различие «законно / незаконно» проводит контекстный фильтр
(:mod:`vantage.context`), который вычитает известные полигоны из
кандидатов. Модель же учится отвечать на другой вопрос: похоже ли это
на поверхность из отходов.

**Отрицательные примеры — самая ценная часть.** Карьеры, стройплощадки,
отвалы грунта из OSM это не просто «не свалки». Это **трудные
отрицательные примеры**: они выглядят почти так же и именно на них
модель ошибается. Обучение на случайной степи в качестве негатива дало
бы отличную метрику и бесполезную модель.

Что всё равно остаётся руками
-----------------------------
Кандидаты, не попавшие ни в одну категорию OSM. Их и надо смотреть
глазами — но их в разы меньше, чем всех кандидатов подряд.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd

from .aoi import AOI
from .config import Settings

log = logging.getLogger(__name__)

#: Теги OSM, обозначающие поверхность из отходов — положительные примеры.
POSITIVE_TAGS = {
    "landuse": ["landfill"],
    "amenity": ["waste_disposal", "waste_transfer_station", "recycling"],
    "man_made": ["spoil_heap"],
}

#: Теги OSM, обозначающие трудные отрицательные примеры: объекты,
#: которые выглядят похоже, но свалками не являются.
HARD_NEGATIVE_TAGS = {
    "landuse": ["quarry", "construction", "brownfield", "greenfield", "farmyard"],
    "natural": ["sand", "scree", "bare_rock"],
    "man_made": ["works"],
}

#: Доля перекрытия, при которой кандидат считается совпавшим с объектом OSM.
MIN_OVERLAP_FRACTION = 0.30


@dataclass
class LabelReport:
    """Сколько меток удалось собрать автоматически и сколько осталось."""

    positives: int
    hard_negatives: int
    unlabelled: int

    @property
    def total(self) -> int:
        return self.positives + self.hard_negatives + self.unlabelled

    @property
    def automatic_fraction(self) -> float:
        return 0.0 if not self.total else (self.positives + self.hard_negatives) / self.total

    def to_text(self) -> str:
        return (
            f"размечено автоматически {self.positives + self.hard_negatives} из {self.total} "
            f"({self.automatic_fraction:.0%}): положительных {self.positives}, "
            f"трудных отрицательных {self.hard_negatives}. "
            f"Руками осталось посмотреть {self.unlabelled}."
        )


def build_osm_query(aoi: AOI, tags: dict[str, list[str]]) -> str:
    """Overpass QL для набора тегов."""
    min_lon, min_lat, max_lon, max_lat = aoi.bbox
    bbox = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    parts = []
    for key, values in tags.items():
        pattern = "|".join(values)
        parts.append(f'  way["{key}"~"^({pattern})$"]({bbox});')
        parts.append(f'  relation["{key}"~"^({pattern})$"]({bbox});')
    body = "\n".join(parts)
    return f"[out:json][timeout:180];\n(\n{body}\n);\nout geom;"


def fetch_reference_objects(
    aoi: AOI,
    settings: Settings,
    tags: dict[str, list[str]],
    *,
    use_cache: bool = True,
) -> gpd.GeoDataFrame:
    """Загрузить объекты OSM по набору тегов в рабочей проекции."""
    from .context import OverpassClient, overpass_to_gdf

    client = OverpassClient(settings.paths.resolve("data_cache"))
    payload = client.query(build_osm_query(aoi, tags), use_cache=use_cache)
    gdf = overpass_to_gdf(payload, target_crs=settings.project.crs_working)
    # Точки и линии для разметки бесполезны: нужна площадь перекрытия
    return gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)


def overlap_fraction(candidates: gpd.GeoDataFrame, reference: gpd.GeoDataFrame) -> np.ndarray:
    """Какая доля площади кандидата перекрыта объектами эталонного слоя.

    Доля, а не факт пересечения: касание углом на один пиксель не должно
    объявлять кандидата совпавшим. Порог задан в MIN_OVERLAP_FRACTION.
    """
    if candidates.empty or reference is None or reference.empty:
        return np.zeros(len(candidates))

    from shapely.ops import unary_union

    merged = unary_union(reference.geometry.values)
    areas = candidates.geometry.area.to_numpy()
    intersected = candidates.geometry.intersection(merged).area.to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(areas > 0, intersected / areas, 0.0)


def auto_label(
    candidates: gpd.GeoDataFrame,
    *,
    positives: gpd.GeoDataFrame | None = None,
    hard_negatives: gpd.GeoDataFrame | None = None,
    min_overlap: float = MIN_OVERLAP_FRACTION,
) -> tuple[gpd.GeoDataFrame, LabelReport]:
    """Разметить кандидатов по совпадению с объектами OSM.

    Добавляет колонки:
        label        1 — поверхность из отходов, 0 — трудный отрицательный,
                     None — требует ручного просмотра;
        label_source откуда метка: ``osm_positive``, ``osm_negative``, ``manual``.

    Приоритет у положительных: если кандидат перекрыт и полигоном ТБО,
    и стройплощадкой, это скорее всего полигон, рядом с которым идёт
    стройка, а не наоборот.
    """
    result = candidates.copy()
    n = len(result)

    positive_overlap = overlap_fraction(result, positives) if positives is not None else np.zeros(n)
    negative_overlap = (
        overlap_fraction(result, hard_negatives) if hard_negatives is not None else np.zeros(n)
    )

    labels: list[int | None] = []
    sources: list[str] = []
    for i in range(n):
        if positive_overlap[i] >= min_overlap:
            labels.append(1)
            sources.append("osm_positive")
        elif negative_overlap[i] >= min_overlap:
            labels.append(0)
            sources.append("osm_negative")
        else:
            labels.append(None)
            sources.append("manual")

    # Тип задаётся явно. Без этого pandas выводит его из данных: список
    # только из None остаётся object, а смесь None и чисел молча
    # становится float64 с NaN. Проверка `label is None` тогда работает
    # на одних выборках и ломается на других — ошибка, которая проявится
    # ровно в тот момент, когда данных станет больше.
    result["label"] = pd.array(labels, dtype="Int64")
    result["label_source"] = sources
    result["osm_positive_overlap"] = positive_overlap
    result["osm_negative_overlap"] = negative_overlap

    report = LabelReport(
        positives=sources.count("osm_positive"),
        hard_negatives=sources.count("osm_negative"),
        unlabelled=sources.count("manual"),
    )
    log.info("Автоматическая разметка: %s", report.to_text())
    return result, report


def harvest_labels(
    aoi: AOI,
    settings: Settings,
    candidates: gpd.GeoDataFrame,
    *,
    use_cache: bool = True,
) -> tuple[gpd.GeoDataFrame, LabelReport]:
    """Полный цикл: загрузить эталоны из OSM и разметить кандидатов."""
    positives = fetch_reference_objects(aoi, settings, POSITIVE_TAGS, use_cache=use_cache)
    negatives = fetch_reference_objects(aoi, settings, HARD_NEGATIVE_TAGS, use_cache=use_cache)
    log.info(
        "Эталоны OSM: положительных объектов %d, трудных отрицательных %d",
        len(positives), len(negatives),
    )
    return auto_label(candidates, positives=positives, hard_negatives=negatives)


def manual_queue(labelled: gpd.GeoDataFrame, *, limit: int | None = None) -> gpd.GeoDataFrame:
    """Очередь на ручной просмотр, отсортированная по полезности.

    Сверху — кандидаты с самой высокой оценкой модели: их разметка даёт
    больше всего информации. Если модель ещё не обучена, порядок по
    площади: крупные объекты и важнее, и различимее на снимке.
    """
    queue = labelled[labelled["label"].isna()].copy()
    sort_key = "probability" if "probability" in queue.columns else "area_m2"
    if sort_key in queue.columns:
        queue = queue.sort_values(sort_key, ascending=False)
    return queue.head(limit) if limit else queue


def class_balance(labelled: gpd.GeoDataFrame) -> dict[str, int]:
    """Баланс классов — проверить до обучения, а не после.

    Модель, обученная на выборке из одних отрицательных примеров,
    не упадёт: она просто научится всегда отвечать «не свалка».
    """
    labels = labelled["label"].dropna()
    return {
        "positive": int((labels == 1).sum()),
        "negative": int((labels == 0).sum()),
        "unlabelled": int(labelled["label"].isna().sum()),
    }


__all__ = [
    "HARD_NEGATIVE_TAGS",
    "MIN_OVERLAP_FRACTION",
    "POSITIVE_TAGS",
    "LabelReport",
    "auto_label",
    "build_osm_query",
    "class_balance",
    "fetch_reference_objects",
    "harvest_labels",
    "manual_queue",
    "overlap_fraction",
]
