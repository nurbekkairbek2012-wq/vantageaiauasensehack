"""Пост-история найденных объектов: что было с ними ПОСЛЕ обнаружения.

Зачем отдельный модуль
----------------------
:mod:`vantage.removal` умеет отвечать на вопрос «убрали или засыпали» с
самого начала, но получал этот вопрос только в тестах: пайплайн никогда не
собирал для него входные данные. Ряды наблюдений после даты разрыва просто
негде было взять — детектор работает по плитке и куб не сохраняет.

Здесь эти ряды собираются. Область берётся не по всей плитке, а по общему
охвату найденных объектов: их десятки, они лежат кучно, и повторно тянуть
всю область ради тридцати полигонов незачем.

Почему тепло собирается по зимам, а не одним композитом
------------------------------------------------------
Различие «убрали» и «засыпали грунтом» держится ровно на одном признаке.
Растительность возвращается в обоих случаях, открытый грунт нормализуется в
обоих. А тело свалки под насыпью продолжает греться, потому что органика
продолжает разлагаться. Чтобы это увидеть, нужна не одна цифра, а ряд: одна
тёплая зима ничего не доказывает, три подряд — доказывают.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import numpy as np

from .aoi import AOI
from .config import Settings

log = logging.getLogger(__name__)

#: Запас вокруг облака объектов при загрузке. Тепловому признаку нужен фон
#: вокруг объекта, иначе аномалию не с чем сравнивать.
MARGIN_M = 1_500.0

#: Сторона блока оптической загрузки. Двести квадратных километров одним
#: куском — это двадцать минут непрерывной сети, и обрыв посреди стоит всей
#: работы. Блок 5x5 км идёт три минуты и кладётся в кеш.
BLOCK_M = 5_000.0


@dataclass
class PostHistory:
    """Ряды наблюдений по одному объекту после даты его обнаружения."""

    candidate_id: str
    ndvi_post: np.ndarray
    bsi_post: np.ndarray
    ndvi_baseline: float
    bsi_baseline: float
    thermal_post: np.ndarray | None = None
    dates: list[str] = field(default_factory=list)

    @property
    def n_observations(self) -> int:
        return int(np.isfinite(self.ndvi_post).sum())


def _zonal_series(cube, variable: str, shapes, transform, crs: str) -> np.ndarray:
    """Медианный ряд значений внутри каждого полигона: (объекты, время).

    Медиана, а не среднее: край объекта всегда смешанный, и один краевой
    пиксель с фоновым значением не должен смещать вывод об устранении.
    """
    from rasterio.features import geometry_mask

    values = np.asarray(cube[variable].transpose("time", "y", "x").values, dtype="float32")
    n_t, ny, nx = values.shape
    out = np.full((len(shapes), n_t), np.nan, dtype="float32")

    for i, geometry in enumerate(shapes.to_crs(crs)):
        if geometry is None or geometry.is_empty:
            continue
        mask = ~geometry_mask([geometry], out_shape=(ny, nx), transform=transform, invert=False)
        if not mask.any():
            continue
        selected = values[:, mask]
        with np.errstate(invalid="ignore"):
            out[i] = np.nanmedian(selected, axis=1)
    return out


def collect_optical(
    candidates: gpd.GeoDataFrame,
    settings: Settings,
    *,
    aoi: AOI | None = None,
    block_m: float = BLOCK_M,
    cache_dir: Path | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Полные ряды NDVI и BSI по каждому объекту за весь период.

    Возвращает ``(ndvi, bsi, даты)``. Разрезание на «до» и «после» делается
    выше: индекс разрыва свой у каждого объекта.

    Считается блоками и с кешем на диске, и это не оптимизация. Один кусок
    на все объекты — это две сотни квадратных километров и двадцать минут
    непрерывной загрузки; любой обрыв сети посреди неё стоил бы всей
    работы, и ровно это и произошло на первом запуске. Блок 5x5 км идёт
    три минуты, кладётся в кеш и при повторе не перекачивается.
    """
    from .signals import _spatial_blocks, bounding_aoi

    area = aoi or bounding_aoi(candidates, settings, margin_m=MARGIN_M)
    log.info("Пост-история: область %.1f км², блоками по %.0f м", area.area_km2, block_m)

    cache = Path(cache_dir) if cache_dir else settings.paths.resolve("data_interim") / "posthistory"
    cache.mkdir(parents=True, exist_ok=True)

    working = candidates.to_crs(settings.project.crs_working)
    blocks = _spatial_blocks(working, block_m)
    log.info("Пост-история: %d блоков", len(blocks))

    ndvi_out: np.ndarray | None = None
    bsi_out: np.ndarray | None = None
    dates_out: np.ndarray | None = None
    failed: list[int] = []

    for number, positions in enumerate(blocks, start=1):
        subset = candidates.iloc[positions]
        try:
            ndvi, bsi, dates = _collect_block(subset, settings, cache, number, len(blocks))
        except Exception as exc:
            log.warning("[%d/%d] блок пропущен: %s", number, len(blocks), exc)
            failed.append(number)
            continue

        if dates_out is None:
            dates_out = dates
            ndvi_out = np.full((len(candidates), dates.size), np.nan, dtype="float32")
            bsi_out = np.full((len(candidates), dates.size), np.nan, dtype="float32")
        elif dates.size != dates_out.size:
            # Разные блоки могут дать разное число композитов: у одного
            # месяц выбракован по облачности, у другого нет. Складывать их
            # в одну матрицу по позиции нельзя — это молча сместит ряды.
            log.warning(
                "[%d/%d] блок пропущен: композитов %d, а у первого блока %d",
                number, len(blocks), dates.size, dates_out.size,
            )
            failed.append(number)
            continue

        ndvi_out[positions] = ndvi  # type: ignore[index]
        bsi_out[positions] = bsi  # type: ignore[index]

    if dates_out is None:
        raise RuntimeError("ни один блок не загрузился — нет сети или STAC недоступен")
    if failed:
        log.warning(
            "Блоков не загрузилось: %d из %d. Повторный запуск доберёт их из сети, "
            "уже посчитанные возьмутся из кеша.",
            len(failed), len(blocks),
        )
    return ndvi_out, bsi_out, dates_out  # type: ignore[return-value]


def _collect_block(
    subset: gpd.GeoDataFrame,
    settings: Settings,
    cache: Path,
    number: int,
    total: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Один блок: из кеша или из сети."""
    from .catalog import StacCatalog
    from .raster import build_feature_cube
    from .signals import _transform_of, bounding_aoi

    area = bounding_aoi(subset, settings, margin_m=MARGIN_M / 3)
    key = "_".join(f"{v:.4f}" for v in area.bbox)
    target = cache / f"{key}.npz"

    if target.exists():
        stored = np.load(target, allow_pickle=False)
        log.info("[%d/%d] блок из кеша, объектов %d", number, total, len(subset))
        return stored["ndvi"], stored["bsi"], stored["dates"].astype("datetime64[D]")

    items = StacCatalog().sentinel2_items(area, settings)
    if not items:
        raise RuntimeError("STAC не вернул сцен")

    cube = build_feature_cube(area, settings, items, variables=("ndvi", "bsi")).compute()
    transform = _transform_of(cube)
    crs = settings.project.crs_working
    ndvi = _zonal_series(cube, "ndvi", subset.geometry, transform, crs)
    bsi = _zonal_series(cube, "bsi", subset.geometry, transform, crs)
    dates = np.asarray(cube["time"].values, dtype="datetime64[D]")

    np.savez_compressed(target, ndvi=ndvi, bsi=bsi, dates=dates.astype("int64"))
    log.info(
        "[%d/%d] блок посчитан: объектов %d, композитов %d",
        number, total, len(subset), dates.size,
    )
    return ndvi, bsi, dates


def collect_thermal_by_season(
    candidates: gpd.GeoDataFrame,
    settings: Settings,
    *,
    aoi: AOI | None = None,
) -> tuple[np.ndarray, list[str]]:
    """Тепловая аномалия по каждому холодному сезону: (объекты, сезоны).

    Один композит на весь период ответил бы «тепло сейчас есть или нет».
    Для контроля устранения нужен ряд: аномалия, которая держится три зимы
    подряд после расчистки, — это присыпка, а не остаточный шум.
    """
    from .catalog import StacCatalog
    from .signals import _transform_of, bounding_aoi, zonal_median
    from .thermal import (
        COLD_SEASON_MONTHS,
        build_thermal_stack,
        cold_season_composite,
        radius_in_pixels,
        thermal_anomaly,
    )

    area = aoi or bounding_aoi(candidates, settings, margin_m=MARGIN_M)
    items = StacCatalog().search_items(
        collection=settings.landsat.collection,
        aoi=area,
        start=settings.time.start,
        end=settings.time.end,
        query={"eo:cloud_cover": {"lt": settings.sentinel2.max_scene_cloud_pct}},
    )

    # Зима принадлежит сезону, который начался осенью: январь 2024 — это
    # сезон 2023/24, и складывать его с ноябрём 2024 было бы ошибкой.
    seasons: dict[int, list] = {}
    for item in items:
        stamp = item.properties.get("datetime") or ""
        if len(stamp) < 7:
            continue
        year, month = int(stamp[:4]), int(stamp[5:7])
        if month not in COLD_SEASON_MONTHS:
            continue
        seasons.setdefault(year if month >= 11 else year - 1, []).append(item)

    labels = sorted(seasons)
    if not labels:
        raise RuntimeError("нет сцен Landsat в холодные месяцы")
    log.info("Пост-история: %d холодных сезонов, %s", len(labels), labels)

    radius_px = radius_in_pixels(settings.landsat.background_radius_m, settings.landsat.resolution_m)
    out = np.full((len(candidates), len(labels)), np.nan, dtype="float32")

    for column, season in enumerate(labels):
        scenes = seasons[season]
        if len(scenes) < 2:
            log.info("Сезон %d: сцен %d — пропуск", season, len(scenes))
            continue
        try:
            stack = build_thermal_stack(area, settings, scenes)
            temperature = cold_season_composite(stack, settings.landsat.thermal_asset).compute()
            anomaly = thermal_anomaly(temperature.values, radius_px=radius_px)
            out[:, column] = zonal_median(
                candidates, anomaly, _transform_of(temperature), crs=settings.project.crs_working
            )
        except Exception as exc:
            log.warning("Сезон %d не посчитан: %s", season, exc)

    return out, [f"{s}/{str(s + 1)[-2:]}" for s in labels]


def build_post_histories(
    candidates: gpd.GeoDataFrame,
    settings: Settings,
    *,
    with_thermal: bool = True,
) -> list[PostHistory]:
    """Собрать пост-историю по всем объектам одним проходом."""
    import pandas as pd

    ndvi, bsi, dates = collect_optical(candidates, settings)

    thermal = None
    if with_thermal:
        try:
            thermal, _labels = collect_thermal_by_season(candidates, settings)
        except Exception as exc:
            log.warning("Тепловая пост-история не собрана: %s", exc)

    histories: list[PostHistory] = []
    break_dates = pd.to_datetime(candidates.get("break_date"), errors="coerce")

    for i in range(len(candidates)):
        stamp = break_dates.iloc[i] if break_dates is not None else None
        if stamp is None or pd.isna(stamp):
            split = len(dates) // 2
        else:
            split = int(np.searchsorted(dates, np.datetime64(stamp.date(), "D")))
        split = int(np.clip(split, 1, len(dates) - 1))

        before_ndvi = ndvi[i, :split]
        before_bsi = bsi[i, :split]
        histories.append(
            PostHistory(
                candidate_id=str(candidates.iloc[i].get("candidate_id", i)),
                ndvi_post=ndvi[i, split:],
                bsi_post=bsi[i, split:],
                ndvi_baseline=float(np.nanmedian(before_ndvi)) if np.isfinite(before_ndvi).any() else np.nan,
                bsi_baseline=float(np.nanmedian(before_bsi)) if np.isfinite(before_bsi).any() else np.nan,
                thermal_post=None if thermal is None else thermal[i],
                dates=[str(d) for d in dates[split:]],
            )
        )

    log.info(
        "Пост-история собрана по %d объектам, медиана наблюдений после разрыва: %d",
        len(histories),
        int(np.median([h.n_observations for h in histories])) if histories else 0,
    )
    return histories


def assess_all(histories: list[PostHistory], settings: Settings):
    """Прогнать контроль устранения по всем объектам."""
    from .removal import assess_removal

    results = []
    for history in histories:
        results.append(
            assess_removal(
                history.candidate_id,
                settings.removal,
                ndvi_post=history.ndvi_post,
                ndvi_baseline=history.ndvi_baseline,
                bsi_post=history.bsi_post,
                bsi_baseline=history.bsi_baseline,
                thermal_anomaly_post=history.thermal_post,
            )
        )
    return results


__all__ = [
    "BLOCK_M",
    "MARGIN_M",
    "PostHistory",
    "assess_all",
    "build_post_histories",
    "collect_optical",
    "collect_thermal_by_season",
]
