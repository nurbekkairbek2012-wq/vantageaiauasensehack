"""Сквозной прогон: от STAC до артефактов на диске.

Каждый шаг пишет свой результат в ``outputs/`` и умеет пропускаться,
если результат уже есть. Это не оптимизация, а требование к работе
за неделю: полный прогон по области идёт часами, и падение на шаге 7
не должно означать повтор шагов 1-6.

Артефакты на выходе — то, что читает сервис и фронтенд:

    candidates.geojson    объекты с атрибутами, деньгами и доказательствами
    risk_private.geojson  точная сетка риска
    risk_public.geojson   агрегированные зоны для публичной карты
    story.json            сценарий демонстрации
    run.json              метаданные прогона: что, когда, сколько

Порядок шагов и их зависимости зафиксированы в PIPELINE_STEPS.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .aoi import AOI
from .config import Economics, Settings, load_economics, load_settings

log = logging.getLogger(__name__)

#: Шаги пайплайна в порядке выполнения. Имя шага = имя артефакта.
#: Перекрытие соседних плиток. Максимальный кандидат по конфигурации —
#: 500 000 м², то есть около 700 м в поперечнике; перекрытие берётся с
#: запасом, чтобы такой объект целиком помещался хотя бы в одну плитку.
TILE_OVERLAP_M = 800.0

#: Сколько раз пробовать плитку, прежде чем признать её пропущенной.
#: Не перестраховка: на прогоне по кольцу две плитки из одиннадцати упали
#: с «Chunk and warp failed» — это оборванное соединение, а не данные.
TILE_ATTEMPTS = 2

#: Пауза перед повтором плитки. Смысл — переждать короткий сетевой сбой,
#: а не устроить второй такой же залп сразу.
TILE_RETRY_PAUSE_S = 20.0

#: Меньше этого числа месячных композитов искать разрыв бессмысленно:
#: нужны два устойчивых участка по обе стороны плюс окно восстановления.
_MIN_COMPOSITES = 24

PIPELINE_STEPS = (
    "scenes",
    "features",
    "change",
    "candidates",
    "context",
    "money",
    "risk",
    "export",
)


@dataclass
class RunReport:
    """Что произошло за прогон — идёт в run.json и в вывод CLI."""

    started_at: str
    aoi_name: str
    aoi_area_km2: float
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    finished_at: str | None = None

    def record(self, step: str, *, seconds: float, **details: Any) -> None:
        self.steps[step] = {"seconds": round(seconds, 1), **details}
        log.info("Шаг %s завершён за %.1f с: %s", step, seconds, details)

    def as_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "aoi": {"name": self.aoi_name, "area_km2": round(self.aoi_area_km2, 1)},
            "steps": self.steps,
            "artifacts": self.artifacts,
        }


class Pipeline:
    """Оркестратор прогона.

    Хранит настройки и каталог артефактов; каждый шаг — отдельный метод,
    который можно вызвать независимо. Это важно и для отладки, и для
    защиты: на вопрос «покажите промежуточный результат» должен быть
    файл, а не пересказ.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        economics: Economics | None = None,
        *,
        outputs: Path | None = None,
        force: bool = False,
    ) -> None:
        self.settings = settings or load_settings()
        self.economics = economics or load_economics()
        self.outputs = Path(outputs) if outputs else self.settings.paths.resolve("outputs")
        self.outputs.mkdir(parents=True, exist_ok=True)
        self.force = force
        self.aoi = AOI.from_settings(self.settings)
        self.report = RunReport(
            started_at=datetime.now().isoformat(timespec="seconds"),
            aoi_name=self.aoi.name,
            aoi_area_km2=self.aoi.area_km2,
        )

    # ------------------------------------------------------------------ #

    def path(self, name: str) -> Path:
        return self.outputs / name

    def exists(self, name: str) -> bool:
        return not self.force and self.path(name).exists()

    # ------------------------------------------------------------------ #
    #  Шаги
    # ------------------------------------------------------------------ #

    def step_scenes(self) -> dict:
        """Найти доступные сцены. Первый шаг: если сцен мало, дальше нет смысла."""
        from .catalog import StacCatalog, summarize

        target = self.path("scenes.json")
        if self.exists("scenes.json"):
            return json.loads(target.read_text(encoding="utf-8"))

        catalog = StacCatalog()
        s2 = catalog.search_sentinel2(self.aoi, self.settings)
        stats = {"sentinel2": summarize(s2)}
        target.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        self.report.artifacts["scenes"] = str(target)
        return stats

    def step_features(
        self,
        tile: AOI,
        *,
        items: list | None = None,
        keep_bands: bool = False,
        variables: tuple[str, ...] = ("ndvi", "bsi"),
    ):
        """Загрузить плитку и построить куб месячных признаков.

        Самый дорогой шаг пайплайна: на плитку 22 км² за восемь лет уходит
        около двух минут и порядка полутысячи HTTP-запросов к облаку.
        Поэтому плитка — единица восстановления после сбоя, а не вся
        область: упавший тайл переигрывается один, а не весь прогон.

        Куб возвращается уже посчитанным, а не ленивым. Детектор изменений
        обращается к каждому пикселю по многу раз, и на ленивом массиве
        это означало бы перечитывать снимки из сети на каждой операции.
        """
        from .catalog import StacCatalog
        from .raster import build_feature_cube

        if items is None:
            items = StacCatalog().sentinel2_items(tile, self.settings)
        if not items:
            raise RuntimeError(f"{tile.name}: STAC не вернул ни одной сцены")

        cube = build_feature_cube(
            tile, self.settings, items, keep_bands=keep_bands, variables=variables
        )
        return cube.compute()

    def process_tile(self, tile: AOI, *, items: list | None = None, keep_bands: bool = False):
        """Полный путь по одной плитке: снимки → куб → разрывы → полигоны.

        Возвращает ``(кандидаты, куб, сетка, даты)``. Куб отдаётся наружу,
        а не выбрасывается: из него же режутся чипы для сети, и загружать
        те же снимки второй раз означало бы удвоить самую дорогую часть
        прогона.
        """
        from .candidates import RasterGrid
        from .raster import series_to_matrix

        variables = ("ndvi", "bsi", "pmli") if keep_bands else ("ndvi", "bsi")
        cube = self.step_features(tile, items=items, keep_bands=keep_bands, variables=variables)

        n_composites = cube.sizes.get("time", 0)
        if n_composites < _MIN_COMPOSITES:
            raise RuntimeError(
                f"{tile.name}: месячных композитов всего {n_composites}, "
                f"нужно минимум {_MIN_COMPOSITES} — искать разрыв бессмысленно"
            )

        ndvi_matrix, dates, shape = series_to_matrix(cube, "ndvi")
        bsi_matrix, _, _ = series_to_matrix(cube, "bsi")
        result = self.step_change(ndvi_matrix, bsi_matrix, dates)

        grid = RasterGrid.from_cube(cube, self.settings.project.crs_working)
        if grid.shape != shape:
            raise RuntimeError(
                f"{tile.name}: форма сетки {grid.shape} не совпадает с растром {shape}"
            )

        candidates = self.step_candidates(result, grid, dates)
        return candidates, cube, grid, dates

    def _process_tile_with_retry(self, tile: AOI, *, keep_bands: bool, attempts: int):
        """Обработать плитку, повторив попытку при сетевом сбое.

        Повтор нужен по измеренной причине, а не на всякий случай: на
        прогоне по кольцу две плитки из одиннадцати упали с «Chunk and
        warp failed» — это оборванное соединение при чтении COG, а не
        свойство данных. Плитка стоит несколько минут, и терять её из-за
        одного разорванного запроса дорого.

        Повторяется вся плитка целиком: odc-stac строит dask-граф на
        сотни запросов, и вычленить из него один упавший нельзя.
        """
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self.process_tile(tile, keep_bands=keep_bands)
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    log.warning(
                        "%s: попытка %d из %d не удалась (%s), повтор через %.0f с",
                        tile.name, attempt, attempts, exc, TILE_RETRY_PAUSE_S,
                    )
                    time.sleep(TILE_RETRY_PAUSE_S)
        raise last_error  # type: ignore[misc]

    def run_tiles(
        self,
        *,
        tile_size_m: float = 10_000,
        aoi: AOI | None = None,
        on_tile=None,
        limit: int | None = None,
        keep_bands: bool = False,
        attempts: int = TILE_ATTEMPTS,
    ):
        """Прогнать область плитками и склеить кандидатов в один слой.

        Плитки идут с перекрытием: объект на стыке иначе находится двумя
        половинами, каждая из которых может не пройти порог минимальной
        площади. Склейку делает :func:`~vantage.candidates.merge_across_tiles`.

        Результат каждой плитки сохраняется в ``outputs/tiles/``. Это не
        оптимизация: полный прогон идёт часами, и падение на сороковой
        плитке не должно означать повтор первых тридцати девяти.

        ``on_tile`` — обратный вызов ``(tile, candidates, cube, grid, dates)``
        для шагов, которым нужен сам растр: чипы для сети, радар, тепло.
        Куб после возврата из него освобождается.

        ``keep_bands`` оставляет в кубе исходные каналы — без них чипы
        не нарезать. Плата за это — куб примерно вчетверо больше, поэтому
        плитки при ``keep_bands=True`` берут меньше.
        """
        import geopandas as gpd

        from .candidates import merge_across_tiles

        area = aoi or self.aoi
        tiles = area.tiles(tile_size_m, overlap_m=TILE_OVERLAP_M)
        if limit is not None:
            tiles = tiles[:limit]

        tile_dir = self.outputs / "tiles"
        tile_dir.mkdir(parents=True, exist_ok=True)

        collected: list = []
        failures: list[dict[str, str]] = []
        started = time.perf_counter()

        for number, tile in enumerate(tiles, start=1):
            cached = tile_dir / f"{tile.name}.geojson"
            if not self.force and cached.exists():
                layer = gpd.read_file(cached)
                if not layer.empty:
                    collected.append(layer.to_crs(self.settings.project.crs_working))
                log.info(
                    "[%d/%d] %s — из кеша, кандидатов %d",
                    number, len(tiles), tile.name, len(layer),
                )
                continue

            try:
                candidates, cube, grid, dates = self._process_tile_with_retry(
                    tile, keep_bands=keep_bands, attempts=attempts
                )
            except Exception as exc:
                log.warning("[%d/%d] %s — пропущена: %s", number, len(tiles), tile.name, exc)
                failures.append({"tile": tile.name, "error": str(exc)})
                continue

            if on_tile is not None:
                # Явная проверка на None: у GeoDataFrame истинность
                # неоднозначна, и `результат or candidates` падает.
                returned = on_tile(tile, candidates, cube, grid, dates)
                if returned is not None:
                    candidates = returned
            del cube

            candidates = candidates.copy()
            candidates["tile"] = tile.name
            # Пустой результат тоже кешируется: «здесь ничего нет» — это
            # такой же результат, и переигрывать его два часа незачем.
            _write_tile(cached, candidates, self.settings.project.crs_output)
            if not candidates.empty:
                collected.append(candidates)
            log.info(
                "[%d/%d] %s — кандидатов %d (%.0f с от начала)",
                number, len(tiles), tile.name, len(candidates),
                time.perf_counter() - started,
            )

        merged = merge_across_tiles(collected, crs=self.settings.project.crs_working)
        self.report.record(
            "features",
            seconds=time.perf_counter() - started,
            tiles=len(tiles),
            failed=len(failures),
            candidates=len(merged),
        )
        if failures:
            self.report.steps["features"]["failures"] = failures
        return merged

    def step_change(self, ndvi: np.ndarray, bsi: np.ndarray, dates: np.ndarray):
        """Детекция необратимых изменений по временным рядам."""
        from .change import detect

        return detect(ndvi, bsi, dates, self.settings.change)

    def step_candidates(self, result, grid, dates: np.ndarray):
        """Векторизация в полигоны с атрибутами."""
        from .candidates import build_candidates

        return build_candidates(result, grid, self.settings, dates=dates)

    def step_context(self, candidates, *, use_cache: bool = True):
        """Контекстный отсев по OpenStreetMap."""
        from .context import apply_context_filter, fetch_context, rejection_report

        layers = fetch_context(self.aoi, self.settings, use_cache=use_cache)
        filtered = apply_context_filter(candidates, layers, self.settings.context)
        return filtered, rejection_report(filtered), layers

    def step_money(self, candidates):
        """Денежный слой по каждому объекту.

        Монте-Карло на объект стоит десятые доли секунды, поэтому по
        десяткам объектов это мгновенно. Если объектов окажутся тысячи,
        число итераций надо снижать — но тогда и интервал станет шире,
        и об этом надо будет сказать честно.
        """
        from .money import assess

        if candidates.empty:
            return candidates

        result = candidates.copy()
        rows = []
        for _, row in result.iterrows():
            area = float(row.get("area_m2", 0.0))
            if area <= 0:
                rows.append({})
                continue
            assessment = assess(area, self.economics)
            rows.append(
                {
                    "damage_p10": assessment.net_damage_kzt.p10,
                    "damage_p50": assessment.net_damage_kzt.p50,
                    "damage_p90": assessment.net_damage_kzt.p90,
                    "mass_t": assessment.mass_t.p50,
                    "co2e_t": assessment.co2e_t.p50,
                    "penalty_kzt": assessment.penalty_kzt,
                    "penalty_article": assessment.penalty_article,
                }
            )
        for key in ("damage_p10", "damage_p50", "damage_p90", "mass_t", "co2e_t",
                    "penalty_kzt", "penalty_article"):
            result[key] = [row.get(key) for row in rows]
        return result

    def step_risk(self, candidates, layers=None):
        """Модель риска и две сетки: точная и публичная."""
        from .risk import (
            aggregate_public,
            build_grid,
            mask_implausible,
            predict_risk,
            spatial_features,
            temporal_labels,
            train_risk_model,
        )

        grid = build_grid(self.aoi, self.settings.risk.grid_cell_m)
        features = spatial_features(
            grid,
            roads=None if layers is None else layers.roads,
            settlements=None if layers is None else layers.settlements,
            existing=candidates,
        )

        # Отсечка: две трети периода на обучение, треть на проверку прогноза
        cutoff = _two_thirds_date(self.settings.time.start, self.settings.time.end)
        y_train, y_future = temporal_labels(grid, candidates, cutoff=cutoff)

        model = train_risk_model(features, y_train, y_future, self.settings.risk, cutoff=cutoff)
        private = predict_risk(model, features)
        # Маска идёт ПОСЛЕ предсказания, а не вместо признака. Обучающих
        # примеров «свалка в парке» ноль по определению, и признак получил бы
        # нулевой вес; запрет — знание о мире, а не закономерность в данных.
        private = mask_implausible(private, None if layers is None else layers.implausible)
        public = aggregate_public(private, self.settings.risk)
        return model, private, public

    def step_registry(self, *, use_cache: bool = True):
        """Публично известные объекты обращения с отходами (OSM).

        Не наш результат, а точка отсчёта: первая сцена демонстрации
        сравнивает «что знают открытые данные» с «что нашёл спутник».
        Без настоящего левого столбца сравнение ничего не стоит.
        """
        from .story import fetch_official_registry

        return fetch_official_registry(self.aoi, self.settings, use_cache=use_cache)

    def step_export(
        self,
        candidates,
        risk_private=None,
        risk_public=None,
        *,
        registry=None,
        risk_model=None,
        is_demo: bool = False,
    ) -> dict[str, str]:
        """Записать артефакты в WGS84 — их читают карта и сервис.

        Пишутся все пять файлов, которые ждёт фронтенд. Раньше шаг
        выгружал только кандидатов и две сетки риска: ``story.json``
        и ``registry.geojson`` появлялись исключительно из генератора
        синтетики, поэтому настоящий прогон оставлял карту без сценария
        и без слоя известных объектов, а метрики модели не выгружались
        вообще ни при каком прогоне.
        """
        from .candidates import simplify_for_web, to_geojson
        from .risk import dissolve_public
        from .story import build_story

        crs_output = self.settings.project.crs_output
        written: dict[str, str] = {}

        if candidates is not None and not candidates.empty:
            target = self.path("candidates.geojson")
            to_geojson(candidates, target, crs_output=crs_output)
            written["candidates"] = str(target)

        for name, layer in (("risk_private", risk_private), ("risk_public", risk_public)):
            if layer is None or layer.empty:
                continue
            target = self.path(f"{name}.geojson")
            export = layer.to_crs(crs_output)
            if name == "risk_public":
                # Смежные ячейки одного класса растворяются: зона риска —
                # это область, а не мозаика квадратов, и файл при этом
                # падает с сотен килобайт до десятков.
                export = simplify_for_web(dissolve_public(export))
            export.to_file(target, driver="GeoJSON")
            written[name] = str(target)

        registry_count = 0
        if registry is not None and not registry.empty:
            registry_count = len(registry)
            target = self.path("registry.geojson")
            simplify_for_web(registry.to_crs(crs_output)).to_file(target, driver="GeoJSON")
            written["registry"] = str(target)

        if candidates is not None and not candidates.empty:
            story = build_story(candidates, registry_count=registry_count, is_demo=is_demo)
            target = self.path("story.json")
            target.write_text(json.dumps(story, ensure_ascii=False, indent=2), encoding="utf-8")
            written["story"] = str(target)

        if risk_model is not None and risk_model.metrics:
            # Интерфейс показывает метрики только когда они есть, и честно
            # пишет «модель не обучена», когда файла нет. Значит, пустые
            # метрики выгружать нельзя — иначе на карте появятся нули,
            # неотличимые от измеренного результата.
            payload = {
                **risk_model.metrics,
                "cutoff": risk_model.cutoff_date,
                "importances": risk_model.importances,
                "is_demo": is_demo,
            }
            target = self.path("metrics.json")
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            written["metrics"] = str(target)

        self.report.artifacts.update(written)
        return written

    # ------------------------------------------------------------------ #

    def finish(self) -> Path:
        """Записать отчёт о прогоне."""
        self.report.finished_at = datetime.now().isoformat(timespec="seconds")
        target = self.path("run.json")
        target.write_text(
            json.dumps(self.report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return target


def _write_tile(path: Path, candidates, crs_output: str) -> None:
    """Сохранить результат плитки, в том числе пустой.

    GeoJSON без единой геометрии драйвер писать отказывается, поэтому
    пустой результат записывается вручную. Без этого плитки без находок
    пересчитывались бы при каждом перезапуске — а их большинство.
    """
    if candidates is None or candidates.empty:
        path.write_text(
            json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8"
        )
        return

    from .candidates import to_geojson

    to_geojson(candidates, path, crs_output=crs_output)


def _two_thirds_date(start: str, end: str) -> str:
    """Дата, отсекающая две трети периода.

    Выбор не произвольный: модели риска нужно достаточно истории для
    обучения и достаточно будущего для честной проверки. Две трети на
    восьмилетнем периоде дают почти три года на валидацию.
    """
    from datetime import date

    d0, d1 = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    return (d0 + (d1 - d0) * 2 // 3).isoformat()


def timed(func, *args, **kwargs) -> tuple[Any, float]:
    """Выполнить и вернуть (результат, секунды)."""
    started = time.perf_counter()
    result = func(*args, **kwargs)
    return result, time.perf_counter() - started


__all__ = [
    "PIPELINE_STEPS",
    "TILE_ATTEMPTS",
    "TILE_OVERLAP_M",
    "TILE_RETRY_PAUSE_S",
    "Pipeline",
    "RunReport",
    "timed",
]
