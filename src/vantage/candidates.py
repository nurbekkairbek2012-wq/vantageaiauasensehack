"""Векторизация: от растровой маски детекции к полигонам-кандидатам.

Детектор изменений работает попиксельно и выдаёт булев растр. Но объект
интереса — не пиксель, а связная область: свалка площадью 5000 м² на
десятиметровом Sentinel-2 это примерно 50 пикселей. Здесь растр
превращается в полигоны с атрибутами.

Три шага, и каждый нужен по своей причине:

1. **Морфологическая чистка.** Открытие убирает одиночные сработавшие
   пиксели — статистический шум, который неизбежен при миллионах проверок
   гипотез. Закрытие заращивает дырки внутри пятна: часть пикселей свалки
   могла не пройти порог из-за облачного пропуска в ряду.

2. **Векторизация связных областей** с сохранением геопривязки.

3. **Агрегация атрибутов по полигону.** Дата возникновения берётся как
   медиана по пикселям, а не среднее: одиночный выброс не должен сдвигать
   дату на год. Именно эта дата даёт на защите фразу «объект появился
   в мае 2021 года».
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import shape

from .change import BreakpointResult
from .config import CandidatesCfg, Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RasterGrid:
    """Геопривязка растра: то, без чего пиксели остаются просто числами."""

    transform: tuple[float, float, float, float, float, float]
    crs: str
    shape: tuple[int, int]

    @property
    def pixel_area_m2(self) -> float:
        """Площадь пикселя. Работает только в метрической проекции."""
        a, _, _, _, e, _ = self.transform
        return abs(a * e)

    @classmethod
    def from_cube(cls, cube, crs: str) -> RasterGrid:
        """Извлечь геопривязку из xarray-куба, собранного odc-stac."""
        import rasterio.transform

        x = np.asarray(cube["x"].values, dtype=float)
        y = np.asarray(cube["y"].values, dtype=float)
        if x.size < 2 or y.size < 2:
            raise ValueError("для построения геопривязки нужно минимум 2 пикселя по каждой оси")
        res_x = float(x[1] - x[0])
        res_y = float(y[1] - y[0])
        transform = rasterio.transform.from_origin(
            west=float(x[0]) - res_x / 2,
            north=float(y[0]) - res_y / 2,
            xsize=abs(res_x),
            ysize=abs(res_y),
        )
        return cls(transform=tuple(transform)[:6], crs=crs, shape=(y.size, x.size))


def clean_mask(mask: np.ndarray, cfg: CandidatesCfg) -> np.ndarray:
    """Морфологическая чистка булевой маски.

    Открытие (эрозия + дилатация) убирает одиночные пиксели: при миллионе
    проверок гипотез даже при пороге z=3 ложные срабатывания неизбежны,
    но они изолированы, а настоящая свалка — связное пятно.

    Закрытие (дилатация + эрозия) заращивает дырки: часть пикселей внутри
    объекта могла не пройти порог из-за пропусков в ряду.
    """
    from scipy import ndimage

    result = np.asarray(mask, dtype=bool)
    if cfg.opening_iterations > 0:
        result = ndimage.binary_opening(result, iterations=cfg.opening_iterations)
    if cfg.closing_iterations > 0:
        result = ndimage.binary_closing(result, iterations=cfg.closing_iterations)
    return result


def polygonize(
    mask: np.ndarray,
    grid: RasterGrid,
    *,
    simplify_tolerance_m: float = 0.0,
) -> gpd.GeoDataFrame:
    """Превратить булеву маску в полигоны в проекции растра."""
    from rasterio import features
    from rasterio.transform import Affine

    transform = Affine(*grid.transform)
    geometries = [
        shape(geom)
        for geom, value in features.shapes(
            mask.astype("uint8"), mask=mask, transform=transform
        )
        if value == 1
    ]

    if not geometries:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=grid.crs)

    if simplify_tolerance_m > 0:
        # preserve_topology=True, иначе тонкие вытянутые объекты могут
        # схлопнуться в невалидную геометрию
        geometries = [g.simplify(simplify_tolerance_m, preserve_topology=True) for g in geometries]

    gdf = gpd.GeoDataFrame({"geometry": geometries}, crs=grid.crs)
    gdf["area_m2"] = gdf.geometry.area
    return gdf


def _aggregate(
    values: np.ndarray,
    labels: np.ndarray,
    label_ids: np.ndarray,
    func,
) -> np.ndarray:
    """Агрегировать значения пикселей по меткам связных областей."""
    out = np.full(label_ids.size, np.nan, dtype=float)
    for i, label_id in enumerate(label_ids):
        selection = values[labels == label_id]
        selection = selection[np.isfinite(selection)]
        if selection.size:
            out[i] = func(selection)
    return out


def build_candidates(
    result: BreakpointResult,
    grid: RasterGrid,
    settings: Settings,
    *,
    dates: np.ndarray | None = None,
) -> gpd.GeoDataFrame:
    """Собрать полигоны-кандидаты с агрегированными атрибутами.

    Атрибуты каждого полигона:

        area_m2        площадь в метрической проекции
        n_pixels       сколько пикселей вошло
        break_date     медианная дата разрыва по пикселям
        ndvi_drop      медианное падение NDVI
        bsi_rise       медианный рост BSI
        zscore_max     самый уверенный пиксель области
        zscore_median  типичная уверенность области

    Медиана, а не среднее: край свалки всегда размыт, и краевые пиксели
    со слабым сигналом не должны занижать характеристику объекта.
    """
    from scipy import ndimage

    ny, nx = grid.shape
    mask = result.has_break.reshape(ny, nx)
    mask = clean_mask(mask, settings.candidates)

    if not mask.any():
        log.info("После морфологической чистки не осталось ни одного кандидата")
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=grid.crs)

    labels, n_labels = ndimage.label(mask)
    log.info("Связных областей после чистки: %d", n_labels)

    gdf = polygonize(mask, grid, simplify_tolerance_m=settings.candidates.simplify_tolerance_m)
    if gdf.empty:
        return gdf

    # Сопоставляем полигоны с метками по центроиду: rasterio.features.shapes
    # и scipy.ndimage.label обходят области в разном порядке, поэтому
    # полагаться на совпадение индексов нельзя.
    from rasterio.transform import Affine, rowcol

    transform = Affine(*grid.transform)
    rows, cols = rowcol(
        transform,
        [p.x for p in gdf.geometry.representative_point()],
        [p.y for p in gdf.geometry.representative_point()],
    )
    rows = np.clip(np.asarray(rows), 0, ny - 1)
    cols = np.clip(np.asarray(cols), 0, nx - 1)
    gdf["_label"] = labels[rows, cols]
    gdf = gdf[gdf["_label"] > 0].copy()
    if gdf.empty:
        return gdf

    present = gdf["_label"].to_numpy()

    zscore = result.zscore.reshape(ny, nx)
    ndvi_drop = result.ndvi_drop.reshape(ny, nx)
    bsi_rise = result.bsi_rise.reshape(ny, nx)

    gdf["n_pixels"] = ndimage.sum(mask, labels, index=present).astype(int)
    gdf["zscore_max"] = _aggregate(zscore, labels, present, np.max)
    gdf["zscore_median"] = _aggregate(zscore, labels, present, np.median)
    gdf["ndvi_drop"] = _aggregate(ndvi_drop, labels, present, np.median)
    gdf["bsi_rise"] = _aggregate(bsi_rise, labels, present, np.median)

    # Уровни NDVI до и после разрыва выгружаются отдельно от величины
    # падения. Карточка объекта показывает «было → стало», и без этих
    # двух чисел интерфейсу пришлось бы придумывать базовый уровень —
    # то есть показывать пользователю выдуманное значение как факт.
    ndvi_before = result.ndvi_before.reshape(ny, nx)
    ndvi_after = result.ndvi_after.reshape(ny, nx)
    gdf["ndvi_before"] = _aggregate(ndvi_before, labels, present, np.median)
    gdf["ndvi_after"] = _aggregate(ndvi_after, labels, present, np.median)

    if dates is not None:
        break_index = result.break_index.reshape(ny, nx).astype(float)
        break_index[break_index < 0] = np.nan
        median_index = _aggregate(break_index, labels, present, np.median)
        date_array = np.asarray(dates, dtype="datetime64[D]")
        gdf["break_date"] = [
            date_array[round(idx)] if np.isfinite(idx) else np.datetime64("NaT")
            for idx in median_index
        ]
        # Индекс разрыва нужен чипам: сеть смотрит на пару эпох «до / после»,
        # и границу между ними задаёт именно номер наблюдения, а не дата —
        # ряд идёт не по всем месяцам года, а только по пригодным.
        gdf["break_index"] = [
            # round() от numpy-скаляра возвращает numpy-скаляр, а не int:
            # приведение здесь нужно, чтобы в GeoJSON ушло обычное число.
            int(round(idx)) if np.isfinite(idx) else -1  # noqa: RUF046
            for idx in median_index
        ]

    gdf = gdf.drop(columns=["_label"]).reset_index(drop=True)
    gdf.insert(0, "candidate_id", [f"C{i:05d}" for i in range(len(gdf))])

    log.info(
        "Кандидатов собрано: %d, суммарная площадь %.1f га",
        len(gdf),
        gdf["area_m2"].sum() / 10_000,
    )
    return gdf


#: Атрибуты, которые при склейке плиток усредняются по площади,
#: а не берутся от случайного куска объекта.
_AREA_WEIGHTED = ("ndvi_drop", "bsi_rise", "zscore_median", "ndvi_before", "ndvi_after")

#: Атрибуты, от которых при склейке берётся максимум: уверенность объекта
#: определяется его лучшим пикселем, и разрез по границе плитки не должен
#: её занижать.
_MAX_AGGREGATED = ("zscore_max",)


def merge_across_tiles(parts: list[gpd.GeoDataFrame], *, crs: str) -> gpd.GeoDataFrame:
    """Склеить кандидатов, найденных на разных плитках, в один слой.

    Объект, лежащий на границе двух плиток, находится дважды — двумя
    половинами. Просто сложить таблицы нельзя: получится два кандидата
    вдвое меньшей площади, и оба провалят фильтр по минимальной площади
    или дадут вдвое заниженный ущерб.

    Поэтому геометрии объединяются, распадаются на связные части, и
    атрибуты собираются заново: площадь пересчитывается по итоговой
    геометрии, оценки признаков — как средневзвешенные по площади
    исходных кусков, уверенность — как максимум.
    """
    from shapely.ops import unary_union

    frames = [p for p in parts if p is not None and not p.empty]
    if not frames:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)

    stacked = gpd.GeoDataFrame(
        pd.concat([f.to_crs(crs) for f in frames], ignore_index=True), crs=crs
    )
    stacked["_piece_area"] = stacked.geometry.area

    dissolved = unary_union(stacked.geometry.values)
    pieces = gpd.GeoSeries([dissolved], crs=crs).explode(index_parts=False).reset_index(drop=True)
    merged = gpd.GeoDataFrame({"geometry": pieces}, crs=crs)
    merged["_merged_id"] = range(len(merged))

    # representative_point гарантированно лежит внутри полигона, в отличие
    # от центроида: у вытянутого или подковообразного объекта центроид
    # может оказаться снаружи и не сматчиться ни с одним куском.
    probes = stacked.copy()
    probes["geometry"] = probes.geometry.representative_point()
    joined = gpd.sjoin(probes, merged, how="left", predicate="within")

    records = []
    for merged_id, group in joined.groupby("_merged_id"):
        weights = group["_piece_area"].to_numpy(dtype=float)
        row: dict = {"_merged_id": int(merged_id), "n_pieces": len(group)}
        if "n_pixels" in group:
            row["n_pixels"] = int(group["n_pixels"].sum())
        for column in _AREA_WEIGHTED:
            if column in group:
                values = group[column].to_numpy(dtype=float)
                ok = np.isfinite(values) & (weights > 0)
                row[column] = float(np.average(values[ok], weights=weights[ok])) if ok.any() else np.nan
        for column in _MAX_AGGREGATED:
            if column in group:
                row[column] = float(np.nanmax(group[column].to_numpy(dtype=float)))
        if "break_date" in group:
            dates_series = pd.to_datetime(group["break_date"], errors="coerce")
            row["break_date"] = dates_series.min()
        if "break_index" in group:
            valid = group["break_index"][group["break_index"] >= 0]
            row["break_index"] = int(valid.min()) if len(valid) else -1
        records.append(row)

    attributes = pd.DataFrame.from_records(records)
    out = merged.merge(attributes, on="_merged_id", how="left").drop(columns=["_merged_id"])
    out["area_m2"] = out.geometry.area
    out = out.sort_values("area_m2", ascending=False).reset_index(drop=True)
    out.insert(0, "candidate_id", [f"C{i:05d}" for i in range(len(out))])
    log.info(
        "Склейка плиток: %d кусков → %d объектов, из них разрезанных границей %d",
        len(stacked), len(out), int((out["n_pieces"] > 1).sum()),
    )
    return out


#: Знаков после запятой в выгружаемых координатах.
#: Шесть знаков — это примерно 10 см на широте Астаны, что на порядки
#: точнее исходных 10 метров Sentinel-2. Хранить больше — значит раздувать
#: файл нулями, которые ничего не означают: карта грузится дольше, а
#: офлайн-режим на площадке страдает первым.
GEOJSON_PRECISION = 6


def simplify_for_web(
    gdf: gpd.GeoDataFrame,
    *,
    precision: int = GEOJSON_PRECISION,
    tolerance_deg: float | None = None,
) -> gpd.GeoDataFrame:
    """Подготовить слой к выгрузке на карту: округлить координаты.

    Округление делается через кодирование геометрии в WKT с заданной
    точностью — это единственный способ реально уменьшить файл, потому
    что GeoJSON-драйвер пишет столько знаков, сколько есть в координатах.
    """
    from shapely import wkt

    out = gdf.copy()
    if tolerance_deg:
        out["geometry"] = out.geometry.simplify(tolerance_deg, preserve_topology=True)
    out["geometry"] = [
        wkt.loads(wkt.dumps(geom, rounding_precision=precision)) if geom is not None else None
        for geom in out.geometry
    ]
    return out


def to_geojson(
    gdf: gpd.GeoDataFrame,
    path,
    *,
    crs_output: str = "EPSG:4326",
    precision: int = GEOJSON_PRECISION,
) -> None:
    """Выгрузить кандидатов в GeoJSON для карты.

    Даты приводятся к строкам: GeoJSON не знает типа datetime, и молчаливая
    сериализация превратила бы их в числа наносекунд.
    """
    from pathlib import Path

    out = simplify_for_web(gdf.to_crs(crs_output), precision=precision)
    for column in out.columns:
        if column == "geometry":
            continue
        if np.issubdtype(out[column].dtype, np.datetime64):
            # strftime, а не astype: pandas не разрешает приводить
            # DatetimeArray к datetime64[D] напрямую, а astype(str) на
            # NaT даёт строку 'NaT' вместо null в JSON.
            out[column] = out[column].dt.strftime("%Y-%m-%d").where(out[column].notna(), None)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_file(path, driver="GeoJSON")
    log.info("Записано %d кандидатов в %s", len(out), path)


__all__ = [
    "GEOJSON_PRECISION",
    "RasterGrid",
    "build_candidates",
    "clean_mask",
    "merge_across_tiles",
    "polygonize",
    "simplify_for_web",
    "to_geojson",
]
