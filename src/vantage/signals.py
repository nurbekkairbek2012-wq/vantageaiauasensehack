"""Дополнительные физические признаки для найденных кандидатов.

Оптическая ветка (NDVI, BSI) находит объект и датирует его. Она же и
ошибается: карьер, стройка и отвал грунта дают ровно ту же картину —
растительность исчезла, открытого грунта стало больше. Разделяют их два
других признака, и оба считаются здесь.

**Радар.** У свалки поверхность меняется от прохода к проходу: привозят,
сгребают, уплотняют. У карьера стенки стоят неделями. Поэтому смотрим не
на абсолютную нестабильность, а на её **прирост после разрыва** — до и
после одного и того же места.

**Тепло.** Анаэробное разложение органики греет тело свалки. Признак
считается по холодному сезону: летом разница теряется в прогреве
поверхности, а зимой тело свалки видно как пятно на снегу. Тот же признак
с обратным знаком отделяет снегосвалку, которая холоднее фона.

Почему отдельным проходом, а не внутри плиточного прогона
---------------------------------------------------------
Оптика, радар и тепло — три разные коллекции с разным разрешением и
разной сеткой дат. Тянуть их одновременно означает утроить самый дорогой
шаг ради признаков, которые нужны десяткам объектов, а не миллионам
пикселей. Здесь они считаются только там, где кандидат уже найден.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import geopandas as gpd
import numpy as np

from .aoi import AOI
from .config import Settings

log = logging.getLogger(__name__)

#: Запас вокруг облака кандидатов при загрузке вспомогательных коллекций.
#: Тепловому признаку нужен фон вокруг объекта, иначе аномалию не с чем
#: сравнивать; радиус фона берётся из конфигурации, буфер — с запасом.
SIGNAL_MARGIN_M = 2_000.0

#: Сторона блока, которым обрабатывается радарная ветка. Подобрана из
#: памяти: 5x5 км при 20 м — это 250x250 пикселей, около пятисот проходов
#: и двух поляризаций, то есть порядка 250 МБ на блок.
SAR_BLOCK_M = 5_000.0

#: Разрешение, на котором считается радарная ветка. Sentinel-1 RTC лежит
#: с шагом 10 м, но признак — это дисперсия по времени, и в ней от
#: удвоения разрешения не прибавляется ничего, кроме вчетверо большего
#: массива и вчетверо более долгой загрузки.
SAR_RESOLUTION_M = 20.0


@dataclass
class SignalReport:
    """Что удалось посчитать, а что нет. Пустая ячейка — тоже результат."""

    sar_covered: int = 0
    thermal_covered: int = 0
    pmli_covered: int = 0
    total: int = 0
    notes: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []

    def to_text(self) -> str:
        return (
            f"объектов {self.total}: радар {self.sar_covered}, "
            f"тепло {self.thermal_covered}, полимеры {self.pmli_covered}"
            + ("; " + "; ".join(self.notes) if self.notes else "")
        )


def bounding_aoi(candidates: gpd.GeoDataFrame, settings: Settings, *, margin_m: float) -> AOI:
    """Общая область вокруг всех кандидатов — одна загрузка вместо N.

    Загружать вспомогательные коллекции по объекту означает N раз оплатить
    сетевые накладные расходы. Кандидаты после контекстного отсева лежат
    десятками и на одной территории, поэтому дешевле взять их общий охват.
    """
    if candidates.empty:
        raise ValueError("нет кандидатов — не для чего считать признаки")

    working = candidates.to_crs(settings.project.crs_working)
    min_x, min_y, max_x, max_y = working.total_bounds
    from shapely.geometry import box

    from .aoi import WGS84, reproject_geometry

    geometry = box(min_x - margin_m, min_y - margin_m, max_x + margin_m, max_y + margin_m)
    return AOI(
        name="signals",
        geometry=reproject_geometry(geometry, settings.project.crs_working, WGS84),
        crs_working=settings.project.crs_working,
    )


def zonal_median(
    candidates: gpd.GeoDataFrame,
    values: np.ndarray,
    transform,
    *,
    crs: str,
) -> np.ndarray:
    """Медиана растра внутри каждого полигона.

    Медиана, а не среднее: край объекта всегда смешанный, и один
    краевой пиксель с фоновым значением не должен смещать оценку.
    Объекты мельче пикселя вспомогательной коллекции получают NaN —
    честнее пустая ячейка, чем значение соседнего поля.
    """
    from rasterio.features import geometry_mask

    out = np.full(len(candidates), np.nan, dtype="float32")
    shapes = candidates.to_crs(crs).geometry
    for i, geometry in enumerate(shapes):
        if geometry is None or geometry.is_empty:
            continue
        mask = ~geometry_mask(
            [geometry], out_shape=values.shape, transform=transform, invert=False
        )
        selected = values[mask]
        selected = selected[np.isfinite(selected)]
        if selected.size:
            out[i] = float(np.median(selected))
    return out


def attach_thermal(
    candidates: gpd.GeoDataFrame,
    settings: Settings,
    *,
    aoi: AOI | None = None,
) -> tuple[gpd.GeoDataFrame, int]:
    """Добавить колонку ``thermal_anomaly`` — превышение над фоном, K.

    Тепловая ветка дешёвая: Landsat снимает раз в 8-16 суток, холодных
    месяцев в году пять, и за восемь лет набирается меньше сотни сцен —
    против полутысячи у Sentinel-2.
    """
    from .catalog import StacCatalog
    from .thermal import (
        COLD_SEASON_MONTHS,
        build_thermal_stack,
        cold_season_composite,
        radius_in_pixels,
        thermal_anomaly,
    )

    result = candidates.copy()
    result["thermal_anomaly"] = np.nan
    if result.empty:
        return result, 0

    area = aoi or bounding_aoi(candidates, settings, margin_m=SIGNAL_MARGIN_M)
    items = StacCatalog().search_items(
        collection=settings.landsat.collection,
        aoi=area,
        start=settings.time.start,
        end=settings.time.end,
        query={"eo:cloud_cover": {"lt": settings.sentinel2.max_scene_cloud_pct}},
    )
    cold = [item for item in items if _month_of(item) in COLD_SEASON_MONTHS]
    if not cold:
        log.warning("Тепловая ветка: сцен в холодные месяцы не нашлось")
        return result, 0

    log.info("Тепловая ветка: %d сцен Landsat в холодном сезоне", len(cold))
    stack = build_thermal_stack(area, settings, cold)
    temperature = cold_season_composite(stack, settings.landsat.thermal_asset).compute()

    radius_px = radius_in_pixels(
        settings.landsat.background_radius_m, settings.landsat.resolution_m
    )
    anomaly = thermal_anomaly(temperature.values, radius_px=radius_px)

    transform = _transform_of(temperature)
    values = zonal_median(result, anomaly, transform, crs=settings.project.crs_working)
    result["thermal_anomaly"] = values
    covered = int(np.isfinite(values).sum())
    log.info("Тепловая аномалия посчитана для %d из %d объектов", covered, len(result))
    return result, covered


def attach_sar(
    candidates: gpd.GeoDataFrame,
    settings: Settings,
    *,
    resolution_m: float = SAR_RESOLUTION_M,
    block_m: float = SAR_BLOCK_M,
) -> tuple[gpd.GeoDataFrame, int]:
    """Добавить колонку ``sar_incoherence`` — прирост нестабильности, дБ.

    Считается блоками, а не одной загрузкой на всю область. Причина
    арифметическая: Sentinel-1 проходит над Астаной каждые 6-12 суток, за
    восемь лет это около пятисот проходов, и куб 20x20 км в двух
    поляризациях весит порядка четырёх гигабайт. Блок 5x5 км — двести
    пятьдесят мегабайт, и в память помещается на любой машине.

    Разрыв у каждого объекта свой, поэтому «до» и «после» считаются по
    его собственной дате. Объекты без даты разрыва признак не получают:
    сравнивать нечего.
    """
    from .catalog import StacCatalog
    from .sar import combined_polarization, incoherence_change

    result = candidates.copy()
    result["sar_incoherence"] = np.nan
    if result.empty or "break_date" not in result.columns:
        return result, 0

    working = result.to_crs(settings.project.crs_working)
    catalog = StacCatalog()
    out = np.full(len(result), np.nan, dtype="float32")

    for positions in _spatial_blocks(working, block_m):
        subset = result.iloc[positions]
        area = bounding_aoi(subset, settings, margin_m=SIGNAL_MARGIN_M / 4)
        try:
            items = catalog.search_items(
                collection=settings.sentinel1.collection,
                aoi=area,
                start=settings.time.start,
                end=settings.time.end,
            )
        except Exception as exc:
            log.warning("Радар: блок из %d объектов пропущен (%s)", len(positions), exc)
            continue
        if not items:
            continue

        stack = _load_sar(area, settings, items, resolution_m=resolution_m).compute()
        values = combined_polarization(stack, tuple(settings.sentinel1.polarizations))
        dates = np.asarray(stack["time"].values, dtype="datetime64[D]")
        transform = _transform_of(stack)
        n_t, ny, nx = values.shape
        flat = values.reshape(n_t, ny * nx)
        log.info("Радар: блок %d объектов, %d проходов, %dx%d px", len(positions), n_t, ny, nx)

        # Разрывы группируются по дате: у объектов одного месяца индекс в
        # радарном ряду совпадает, а incoherence_change — самая дорогая
        # операция модуля, и повторять её на каждый объект незачем.
        breaks = _break_indices(subset["break_date"], dates)
        for index, local in _group_by_value(breaks).items():
            if index < 0:
                continue
            change = incoherence_change(flat, np.full(flat.shape[1], index, dtype="int64"))
            rows = [positions[i] for i in local]
            out[rows] = zonal_median(
                result.iloc[rows], change.reshape(ny, nx), transform,
                crs=settings.project.crs_working,
            )
        del stack, values, flat

    result["sar_incoherence"] = out
    covered = int(np.isfinite(out).sum())
    log.info("Радарный признак посчитан для %d из %d объектов", covered, len(result))
    return result, covered


def attach_signals(
    candidates: gpd.GeoDataFrame,
    settings: Settings,
    *,
    thermal: bool = True,
    sar: bool = True,
) -> tuple[gpd.GeoDataFrame, SignalReport]:
    """Досчитать радарный и тепловой признаки для готового списка объектов.

    Каждая ветка падает независимо: недоступная коллекция оставляет свою
    колонку пустой, но не роняет остальные признаки и не роняет прогон.
    """
    report = SignalReport(total=len(candidates))
    result = candidates

    if thermal:
        try:
            result, report.thermal_covered = attach_thermal(result, settings)
        except Exception as exc:
            log.warning("Тепловая ветка не отработала: %s", exc)
            report.notes.append(f"тепло: {exc}")
    if sar:
        try:
            result, report.sar_covered = attach_sar(result, settings)
        except Exception as exc:
            log.warning("Радарная ветка не отработала: %s", exc)
            report.notes.append(f"радар: {exc}")

    if "pmli_response" in result.columns:
        report.pmli_covered = int(np.isfinite(result["pmli_response"]).sum())

    log.info("Признаки: %s", report.to_text())
    return result, report


# --------------------------------------------------------------------------- #
#  Полимеры из уже нарезанных чипов
# --------------------------------------------------------------------------- #


def pmli_response_from_chips(dataset, *, centre_px: int = 16) -> dict[str, float]:
    """Отклик полимеров как прирост PMLI между эпохами «до» и «после».

    Считается по центральной части чипа: край окна почти всегда захватывает
    фон вокруг объекта, и включать его в оценку — значит систематически
    занижать признак у мелких объектов.

    Отдельная функция, а не шаг пайплайна: чипы уже нарезаны основным
    прогоном, и признак достаётся из них бесплатно, без единого запроса.
    """
    if "pmli" not in dataset.channels:
        raise KeyError("в чипах нет канала pmli")

    channel = dataset.channels.index("pmli")
    size = dataset.before.shape[-1]
    half = min(centre_px, size) // 2
    lo, hi = size // 2 - half, size // 2 + half
    if hi <= lo:
        lo, hi = 0, size

    before = dataset.before[:, channel, lo:hi, lo:hi]
    after = dataset.after[:, channel, lo:hi, lo:hi]
    with np.errstate(invalid="ignore"):
        delta = np.nanmedian(after, axis=(1, 2)) - np.nanmedian(before, axis=(1, 2))
    return {cid: float(value) for cid, value in zip(dataset.candidate_ids, delta, strict=True)}


# --------------------------------------------------------------------------- #
#  Вспомогательное
# --------------------------------------------------------------------------- #


def _month_of(item) -> int:
    stamp = item.properties.get("datetime") or item.properties.get("start_datetime") or ""
    return int(stamp[5:7])


def _transform_of(array):
    """Аффинное преобразование куба odc-stac."""
    import rasterio.transform

    x = np.asarray(array["x"].values, dtype=float)
    y = np.asarray(array["y"].values, dtype=float)
    res_x = float(x[1] - x[0])
    res_y = float(y[1] - y[0])
    return rasterio.transform.from_origin(
        west=float(x[0]) - res_x / 2,
        north=float(y[0]) - res_y / 2,
        xsize=abs(res_x),
        ysize=abs(res_y),
    )


def _load_sar(aoi: AOI, settings: Settings, items, *, resolution_m: float):
    """Куб Sentinel-1 в дБ на заданном разрешении."""
    from odc.stac import load as odc_load

    from .sar import resolve_polarization_assets, to_db

    resolved = resolve_polarization_assets(items, settings.sentinel1.polarizations)
    ds = odc_load(
        items,
        bands=tuple(resolved.values()),
        crs=settings.project.crs_working,
        resolution=resolution_m,
        bbox=aoi.bbox,
        chunks={"time": 1, "x": 1024, "y": 1024},
        groupby="solar_day",
        resampling="bilinear",
    )
    ds = ds.rename({actual: canonical for canonical, actual in resolved.items()})

    out = ds.copy()
    for name in out.data_vars:
        out[name] = to_db(out[name])
        out[name].attrs["units"] = "dB"
    return out


def _break_indices(break_dates, series_dates: np.ndarray) -> np.ndarray:
    """Позиция даты разрыва каждого объекта в ряду вспомогательной коллекции."""
    import pandas as pd

    parsed = pd.to_datetime(break_dates, errors="coerce")
    out = np.full(len(parsed), -1, dtype="int64")
    for i, stamp in enumerate(parsed):
        if stamp is None or pd.isna(stamp):
            continue
        position = int(np.searchsorted(series_dates, np.datetime64(stamp.date(), "D")))
        # Оба сегмента должны существовать: разрыв на самом краю ряда
        # оставляет одну из двух дисперсий неопределённой.
        if 0 < position < len(series_dates):
            out[i] = position
    return out


def _spatial_blocks(working: gpd.GeoDataFrame, block_m: float) -> list[list[int]]:
    """Разбить объекты на пространственные группы по сетке ``block_m``.

    Группировка по сетке, а не кластеризация: нужна не красота разбиения,
    а гарантия, что охват одной группы не превысит размер блока — от него
    напрямую зависит объём загружаемого куба.
    """
    points = working.geometry.representative_point()
    keys = [
        (int(np.floor(p.x / block_m)), int(np.floor(p.y / block_m)))
        for p in points
    ]
    groups: dict[tuple[int, int], list[int]] = {}
    for position, key in enumerate(keys):
        groups.setdefault(key, []).append(position)
    return list(groups.values())


def _group_by_value(values: np.ndarray) -> dict[int, list[int]]:
    groups: dict[int, list[int]] = {}
    for position, value in enumerate(values):
        groups.setdefault(int(value), []).append(position)
    return groups


__all__ = [
    "SAR_BLOCK_M",
    "SAR_RESOLUTION_M",
    "SIGNAL_MARGIN_M",
    "SignalReport",
    "attach_sar",
    "attach_signals",
    "attach_thermal",
    "bounding_aoi",
    "pmli_response_from_chips",
    "zonal_median",
]
