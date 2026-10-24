"""Контекстный отсев — превращение тысяч кандидатов в десятки.

Это тот шаг, где выигрывается защита. Детектор изменений находит всё, что
необратимо изменилось: карьеры, стройки, новые дороги, отвалы грунта,
снегосвалки, распаханную целину. Свалок среди них — единицы процентов.

Отсев делается не моделью, а **знанием предметной области**, и именно
поэтому его легко объяснить на Q&A:

    вычитаем    известные объекты из OpenStreetMap — полигоны, карьеры,
                стройки, кладбища, промзоны, сельхозугодья, воду;
    вычитаем    снегосвалки — в Астане они есть, спектрально почти
                неотличимы от свалки, и про них обязательно спросят;
    оставляем   только то, что ближе 300 м к проезжей дороге: без подъезда
                для самосвала стихийная свалка физически не образуется;
    оставляем   кольцо 1.5–15 км от жилья: ближе — заметят и пожалуются,
                дальше — невыгодно везти.

Источник данных — Overpass API (OpenStreetMap). Ответ кешируется на диск:
Overpass ограничивает частоту запросов, а во время отладки пайплайн
перезапускается десятки раз.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely.ops import unary_union

from .aoi import AOI, WGS84
from .config import ContextCfg, Settings

log = logging.getLogger(__name__)

#: Зеркала Overpass в порядке обхода.
#:
#: Список длинный не от избытка осторожности: публичные зеркала Overpass
#: регулярно уходят в перегрузку и отвечают 504 или обрывают соединение,
#: причём все сразу. Контекстный отсев без них не работает вообще, поэтому
#: запас нужен, а результат обязательно кешируется на диск.
#:
#: Внимание: региональные инстансы (overpass.osm.ch и подобные) содержат
#: данные только своей страны и на запрос по Казахстану молча возвращают
#: пустой ответ — не ошибку. Такие зеркала сюда добавлять нельзя.
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    # maps.mail.ru убрано 23 августа. Оно отвечает быстро и всегда пустым:
    # это региональное зеркало с данными по России, и запрос по Казахстану
    # возвращает ноль элементов, а не ошибку. Ровно тот случай, о котором
    # предупреждает абзац выше, — и он всё равно был в списке.
    #
    # Вреда от него больше, чем пользы: каждый круг обхода тратился на
    # зеркало, которое не может помочь по определению.
)

#: Зеркала, чьему пустому ответу можно верить.
#:
#: Это полные копии планетной базы. Если такое зеркало отвечает «ничего не
#: найдено», значит объектов в области действительно нет, и ждать другого
#: ответа бессмысленно — можно прождать все круги и остаться ни с чем.
#:
#: Региональные инстансы сюда не попадают никогда: их пустой ответ означает
#: «этого региона у меня нет», а вовсе не «объектов нет».
GLOBAL_ENDPOINTS = frozenset({
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
})

#: Сколько раз обойти весь список зеркал, прежде чем сдаться.
#:
#: Было два круга. Ночью 23 августа этого не хватило: сбор датасета по
#: Казахстану ловил 500 и 502 подряд от всех трёх публичных зеркал, и
#: главный положительный класс — landuse=landfill — не собрался вовсе.
#: Прогоны по городам в ту же ночь падали по той же причине.
#:
#: Шесть кругов с удвоением дают 15+30+60+120+240 — почти восемь минут
#: ожидания. Для прогона, идущего часами, это ничто; для ночного Overpass,
#: который лежит десятками минут, это разница между «досчитали» и «пояс
#: пропал».
OVERPASS_ROUNDS = 6

#: Пауза между кругами, секунды, растущая.
#:
#: Восемь секунд Overpass не хватает: перегруженное зеркало отвечает
#: отказом ровно так же быстро, как здоровое — успехом, и четыре круга по
#: восемь секунд укладываются в полминуты, за которую ничего не меняется.
#: Пауза удваивается: 15, 30, 60, 120 — суммарно четыре минуты, и за это
#: время нагрузка обычно спадает.
OVERPASS_BACKOFF_S = 15

#: Типы дорог, по которым реально может проехать самосвал.
#: Тропинки и велодорожки исключены намеренно — по ним отходы не вывозят.
DRIVABLE_HIGHWAYS = (
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "service", "track",
)

#: Теги, которыми в OSM размечены населённые пункты.
SETTLEMENT_PLACES = ("city", "town", "village", "hamlet", "suburb", "neighbourhood")


@dataclass(frozen=True)
class ContextLayers:
    """Векторные слои контекста, приведённые к рабочей проекции."""

    excluded: gpd.GeoDataFrame     # объекты, которые вычитаются целиком
    roads: gpd.GeoDataFrame        # проезжие дороги
    settlements: gpd.GeoDataFrame  # населённые пункты
    crs: str
    #: Земли, где свалка не возникает. Нужны прогнозу, а не детектору;
    #: пустой слой означает «не запрашивали», а не «таких земель нет».
    implausible: gpd.GeoDataFrame | None = None

    def is_empty(self) -> bool:
        return self.excluded.empty and self.roads.empty and self.settlements.empty


# --------------------------------------------------------------------------- #
#  Загрузка OSM
# --------------------------------------------------------------------------- #


class OverpassClient:
    """Минимальный клиент Overpass с кешем на диске и переключением зеркал."""

    def __init__(self, cache_dir: Path, *, timeout: int = 180) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

    def query(self, ql: str, *, use_cache: bool = True) -> dict:
        """Выполнить запрос Overpass QL и вернуть разобранный GeoJSON-подобный ответ."""
        key = hashlib.sha256(ql.encode("utf-8")).hexdigest()[:16]
        cache_file = self.cache_dir / f"overpass_{key}.json"

        if use_cache and cache_file.exists():
            log.debug("Overpass: ответ взят из кеша %s", cache_file.name)
            return json.loads(cache_file.read_text(encoding="utf-8"))

        last_error: Exception | None = None
        for attempt in range(OVERPASS_ROUNDS):
            for endpoint in OVERPASS_ENDPOINTS:
                try:
                    log.info("Overpass: запрос к %s", endpoint)
                    response = requests.post(
                        endpoint,
                        data={"data": ql},
                        timeout=self.timeout,
                        headers={"User-Agent": "VANTAGE/0.1 (hackathon research)"},
                    )
                    response.raise_for_status()
                    payload = response.json()

                    # Пустой ответ бывает и правдой, и подлогом, и различать
                    # их надо по зеркалу.
                    #
                    # Региональный инстанс на чужой регион отвечает 200 и
                    # пустым списком — принять это за «объектов нет» значит
                    # тихо испортить весь отсев. Именно из-за этого правило
                    # и появилось.
                    #
                    # Но глобальное зеркало, отвечающее пустотой, говорит
                    # правду: таких объектов в области действительно нет.
                    # Пока правило было общим, прогон по восточному поясу
                    # Астаны не мог пройти НИКОГДА: один из запросов там
                    # честно возвращает ноль элементов, и это считалось
                    # отказом шесть кругов подряд.
                    if not payload.get("elements"):
                        if endpoint in GLOBAL_ENDPOINTS:
                            log.info(
                                "Overpass %s: объектов нет — принимаю как ответ", endpoint
                            )
                            cache_file.write_text(json.dumps(payload), encoding="utf-8")
                            return payload
                        log.warning("Overpass %s вернул пустой ответ, пробуем следующее", endpoint)
                        last_error = RuntimeError("пустой ответ")
                        continue

                    cache_file.write_text(json.dumps(payload), encoding="utf-8")
                    return payload
                except Exception as exc:
                    log.warning("Overpass %s недоступен: %s", endpoint, exc)
                    last_error = exc

            if attempt + 1 < OVERPASS_ROUNDS:
                # Пауза удваивается с каждым кругом. Постоянная не помогает:
                # перегруженное зеркало отвечает отказом так же быстро, как
                # здоровое — успехом, и четыре круга по восемь секунд
                # укладываются в полминуты, за которую ничего не меняется.
                pause = OVERPASS_BACKOFF_S * (2 ** attempt)
                log.info("Все зеркала не ответили, ждём %d с и пробуем снова (круг %d из %d)",
                         pause, attempt + 2, OVERPASS_ROUNDS)
                time.sleep(pause)

        raise RuntimeError(
            f"все зеркала Overpass недоступны после {OVERPASS_ROUNDS} кругов: {last_error}"
        )


def _bbox_clause(aoi: AOI) -> str:
    """Overpass принимает bbox в порядке (юг, запад, север, восток)."""
    min_lon, min_lat, max_lon, max_lat = aoi.bbox
    return f"{min_lat},{min_lon},{max_lat},{max_lon}"


def build_exclusion_query(aoi: AOI, cfg: ContextCfg) -> str:
    """Overpass QL для объектов, которые надо вычесть из кандидатов."""
    bbox = _bbox_clause(aoi)
    landuse = "|".join(cfg.exclude_landuse)
    natural = "|".join(cfg.exclude_natural)
    return f"""
[out:json][timeout:180];
(
  way["landuse"~"^({landuse})$"]({bbox});
  relation["landuse"~"^({landuse})$"]({bbox});
  way["natural"~"^({natural})$"]({bbox});
  relation["natural"~"^({natural})$"]({bbox});
  way["amenity"="waste_disposal"]({bbox});
  way["amenity"="waste_transfer_station"]({bbox});
  way["man_made"="works"]({bbox});
  way["building"]({bbox});
  way["aeroway"]({bbox});
);
out geom;
""".strip()


#: Земли, на которых стихийная свалка не возникает — не потому, что там
#: чисто, а потому, что за участком следят. Университетский кампус, парк,
#: ботанический сад, кладбище, аэродром, воинская часть, водоём: у каждого
#: есть хозяин, охрана или забор, и самосвал туда не заезжает.
#:
#: Список НЕ совпадает со списком исключений для находок, и это не
#: небрежность. Карьер и промзона из находок вычитаются — там законное
#: изменение поверхности, которое детектор путает со свалкой. Но
#: предсказывать свалку в карьере как раз осмысленно: заброшенный карьер —
#: одно из самых частых мест сброса. Прогноз и детекция отвечают на разные
#: вопросы, и запретные зоны у них разные.
IMPLAUSIBLE_FOR_DUMPING = {
    "leisure": ["park", "garden", "nature_reserve", "pitch", "stadium", "golf_course"],
    "landuse": ["forest", "recreation_ground", "cemetery", "military", "education"],
    "amenity": ["university", "college", "school", "hospital", "kindergarten"],
    "natural": ["water", "wetland"],
    "boundary": ["protected_area", "national_park"],
    "aeroway": ["aerodrome", "runway", "apron"],
    "waterway": ["riverbank"],
}


def build_implausible_query(aoi: AOI) -> str:
    """Overpass QL для земель, где стихийная свалка не возникает.

    Нужен прогнозу, а не детектору. Модель риска обучена на признаках вида
    «далеко от жилья, близко к дороге» и ничего не знает о том, кому
    принадлежит участок. Ботанический сад Назарбаев Университета
    удовлетворяет обоим признакам идеально — и получил высший класс риска
    на первом же прогоне. Ошибка не в весах: признака, который отличал бы
    охраняемую территорию от пустыря, в модели просто нет.
    """
    bbox = _bbox_clause(aoi)
    clauses = []
    for key, values in IMPLAUSIBLE_FOR_DUMPING.items():
        pattern = "|".join(values)
        for kind in ("way", "relation"):
            clauses.append(f'  {kind}["{key}"~"^({pattern})$"]({bbox});')
    body = chr(10).join(clauses)
    return f"""
[out:json][timeout:180];
(
{body}
);
out geom;
""".strip()


def build_roads_query(aoi: AOI) -> str:
    bbox = _bbox_clause(aoi)
    highways = "|".join(DRIVABLE_HIGHWAYS)
    return f"""
[out:json][timeout:180];
way["highway"~"^({highways})$"]({bbox});
out geom;
""".strip()


def build_settlements_query(aoi: AOI) -> str:
    bbox = _bbox_clause(aoi)
    places = "|".join(SETTLEMENT_PLACES)
    return f"""
[out:json][timeout:180];
(
  node["place"~"^({places})$"]({bbox});
  way["place"~"^({places})$"]({bbox});
  relation["place"~"^({places})$"]({bbox});
  way["landuse"="residential"]({bbox});
);
out geom;
""".strip()


def overpass_to_gdf(payload: dict, *, target_crs: str) -> gpd.GeoDataFrame:
    """Преобразовать ответ Overpass в GeoDataFrame в рабочей проекции."""
    from shapely.geometry import LineString, Point, Polygon

    records: list[dict] = []
    for element in payload.get("elements", []):
        geometry = None
        etype = element.get("type")

        if etype == "node" and "lat" in element:
            geometry = Point(element["lon"], element["lat"])
        elif "geometry" in element:
            coords = [(p["lon"], p["lat"]) for p in element["geometry"]]
            if len(coords) < 2:
                continue
            # Замкнутый контур — площадной объект, иначе линия
            if len(coords) >= 4 and coords[0] == coords[-1]:
                try:
                    geometry = Polygon(coords)
                    if not geometry.is_valid:
                        geometry = geometry.buffer(0)
                except Exception:
                    geometry = LineString(coords)
            else:
                geometry = LineString(coords)

        if geometry is None or geometry.is_empty:
            continue

        tags = element.get("tags", {})
        records.append({"osm_id": element.get("id"), "osm_type": etype, "geometry": geometry, **tags})

    if not records:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=WGS84).to_crs(target_crs)

    gdf = gpd.GeoDataFrame(records, geometry="geometry", crs=WGS84)
    return gdf.to_crs(target_crs)


def fetch_context(
    aoi: AOI,
    settings: Settings,
    *,
    cache_dir: Path | None = None,
    use_cache: bool = True,
) -> ContextLayers:
    """Загрузить все контекстные слои для области."""
    client = OverpassClient(cache_dir or settings.paths.resolve("data_cache"))
    crs = settings.project.crs_working

    excluded = overpass_to_gdf(
        client.query(build_exclusion_query(aoi, settings.context), use_cache=use_cache),
        target_crs=crs,
    )
    roads = overpass_to_gdf(
        client.query(build_roads_query(aoi), use_cache=use_cache), target_crs=crs
    )
    settlements = overpass_to_gdf(
        client.query(build_settlements_query(aoi), use_cache=use_cache), target_crs=crs
    )

    implausible = overpass_to_gdf(
        client.query(build_implausible_query(aoi), use_cache=use_cache), target_crs=crs
    )

    log.info(
        "Контекст загружен: %d исключаемых, %d дорог, %d населённых пунктов, "
        "%d охраняемых участков",
        len(excluded), len(roads), len(settlements), len(implausible),
    )

    # Пустой слой дорог — не «в этой области нет дорог», а «Overpass не
    # ответил». Разница решающая: отсев считает расстояние до ближайшей
    # дороги, и при пустом слое оно бесконечно у всех, то есть КАЖДЫЙ
    # кандидат выбывает с причиной «нет подъезда».
    #
    # Так и произошло в ночь на 22 августа: прогоны по Алматы и Шымкенту
    # отсеяли всё до последнего объекта, отчёт выглядел осмысленно —
    # «257 мелких, 242 без подъезда», — и только отсутствие причины
    # «совпал с объектом OSM» выдало, что слоёв не было вовсе. Молчаливый
    # отказ, дающий правдоподобный результат, опаснее падения.
    if roads.empty:
        raise RuntimeError(
            "Overpass не вернул ни одной дороги по области. Это отказ сервиса, "
            "а не свойство местности: без слоя дорог контекстный отсев "
            "забракует всех кандидатов с причиной «нет подъезда». "
            "Повторите позже или смените зеркало."
        )
    if excluded.empty:
        log.warning(
            "Слой исключаемых объектов пуст. Для города это невозможно — "
            "проверьте ответ Overpass: карьеры, стройки и вода не будут отсеяны."
        )

    return ContextLayers(
        excluded=excluded,
        roads=roads,
        settlements=settlements,
        crs=crs,
        implausible=implausible,
    )


# --------------------------------------------------------------------------- #
#  Применение фильтров
# --------------------------------------------------------------------------- #


def distance_to_layer(
    candidates: gpd.GeoDataFrame, layer: gpd.GeoDataFrame
) -> np.ndarray:
    """Расстояние в метрах от каждого кандидата до ближайшего объекта слоя.

    Пустой слой даёт бесконечность: отсутствие данных не должно молча
    превращаться в «расстояние ноль» и пропускать всё подряд.
    """
    if layer.empty or candidates.empty:
        return np.full(len(candidates), np.inf)
    if candidates.crs != layer.crs:
        raise ValueError(
            f"проекции не совпадают: кандидаты {candidates.crs}, слой {layer.crs}. "
            "Расстояния можно считать только в одной метрической проекции."
        )
    joined = gpd.sjoin_nearest(
        candidates[["geometry"]], layer[["geometry"]], how="left", distance_col="_dist"
    )
    # sjoin_nearest может вернуть несколько строк на кандидата при равных расстояниях
    return joined.groupby(joined.index)["_dist"].min().reindex(candidates.index).to_numpy()


def apply_context_filter(
    candidates: gpd.GeoDataFrame,
    layers: ContextLayers,
    cfg: ContextCfg,
) -> gpd.GeoDataFrame:
    """Применить весь контекстный отсев и объяснить решение по каждому кандидату.

    Возвращает исходный набор с добавленными колонками:

        dist_road_m, dist_settlement_m, area_m2 — измеренные величины;
        reject_reason  — почему кандидат отклонён (None, если прошёл);
        passes_context — итоговое решение.

    Причина отклонения сохраняется намеренно: слайд «где мы ошиблись» и
    ответ на вопрос «а почему вы выкинули вот это» требуют не факта
    отсева, а его объяснения.
    """
    if candidates.empty:
        return candidates.assign(
            reject_reason=pd.Series(dtype=object), passes_context=False
        )
    if candidates.crs != layers.crs:
        candidates = candidates.to_crs(layers.crs)

    result = candidates.copy()
    result["area_m2"] = result.geometry.area
    result["dist_road_m"] = distance_to_layer(result, layers.roads)
    result["dist_settlement_m"] = distance_to_layer(result, layers.settlements)

    # Пересечение с исключаемыми объектами
    if layers.excluded.empty:
        overlaps = np.zeros(len(result), dtype=bool)
    else:
        excluded_union = unary_union(layers.excluded.geometry.values)
        overlaps = result.geometry.intersects(excluded_union).to_numpy()

    # Порядок проверок = порядок объяснения на защите: от самой очевидной
    # причины к самой тонкой.
    reasons: list[str | None] = []
    for i in range(len(result)):
        area = result["area_m2"].iat[i]
        if area < cfg.min_area_m2:
            reasons.append("площадь ниже порога разрешения Sentinel-2")
        elif area > cfg.max_area_m2:
            reasons.append("площадь слишком велика — это полигон, а не стихийная свалка")
        elif overlaps[i]:
            reasons.append("пересекается с известным объектом OSM (карьер, стройка, застройка, вода)")
        elif result["dist_road_m"].iat[i] > cfg.max_distance_to_road_m:
            reasons.append("нет подъезда: далеко от проезжей дороги")
        elif result["dist_settlement_m"].iat[i] < cfg.min_distance_to_settlement_m:
            reasons.append("слишком близко к жилью")
        elif result["dist_settlement_m"].iat[i] > cfg.max_distance_to_settlement_m:
            reasons.append("слишком далеко от жилья — невыгодно везти")
        else:
            reasons.append(None)

    # dtype=object задан явно. В pandas 3 список строк с None внутри
    # становится StringDtype, и None молча превращается в nan — а «нет
    # причины отклонения» это именно None, отсутствие значения. Проверка
    # `reason is None` после такого превращения перестаёт работать, и
    # прошедший отсев кандидат выглядит отклонённым по причине «nan».
    result["reject_reason"] = pd.Series(reasons, index=result.index, dtype=object)
    result["passes_context"] = [r is None for r in reasons]

    log.info(
        "Контекстный отсев: %d из %d кандидатов прошли (%.1f%%)",
        int(result["passes_context"].sum()),
        len(result),
        100.0 * result["passes_context"].mean(),
    )
    return result


def rejection_report(filtered: gpd.GeoDataFrame) -> dict[str, int]:
    """Сколько кандидатов отсеяно по каждой причине.

    Это готовая таблица для слайда «где мы ошиблись» и для ответа на
    вопрос жюри «а как вы отличаете свалку от стройки».
    """
    if "reject_reason" not in filtered.columns:
        raise KeyError("сначала примените apply_context_filter")
    counts = filtered["reject_reason"].fillna("ПРОШЁЛ ОТСЕВ").value_counts()
    return {str(k): int(v) for k, v in counts.items()}


__all__ = [
    "DRIVABLE_HIGHWAYS",
    "OVERPASS_ENDPOINTS",
    "SETTLEMENT_PLACES",
    "ContextLayers",
    "OverpassClient",
    "apply_context_filter",
    "build_exclusion_query",
    "build_roads_query",
    "build_settlements_query",
    "distance_to_layer",
    "fetch_context",
    "overpass_to_gdf",
    "rejection_report",
]
