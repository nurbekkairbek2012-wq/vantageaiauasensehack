"""Загрузка растровых окон и построение временного ряда композитов.

Ключевая идея: мы никогда не работаем с отдельным снимком. Один снимок ничего
не доказывает — на нём стройка неотличима от свалки. Работаем с **месячными
композитами за восемь лет**, и решение принимается по форме временного ряда.

Порядок обработки, и почему он именно такой:

1. Загрузка окна по AOI через odc-stac (ленивая, dask).
2. Маска облаков по каналу SCL — попиксельно, а не по облачности сцены.
   Сцена с 50% облаков может быть полностью чистой над нашей плиткой.
3. Коррекция смещения Processing Baseline 04.00. Если её пропустить, все
   индексы скачком меняются 25 января 2022 года и детектор изменений находит
   «разрыв» одновременно во всей области.
4. Месячный композит медианой. Медиана, а не среднее: она устойчива к
   остаточным облакам и теням, которые SCL пропустил.
5. Отбраковка месяцев, где валидных наблюдений слишком мало.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import numpy as np

from .aoi import AOI
from .config import Settings
from .indices import S2_BASELINE_04_DATE, S2_BASELINE_04_OFFSET, S2_REFLECTANCE_SCALE

log = logging.getLogger(__name__)

#: Каналы Sentinel-2, которые нужно масштабировать в отражение.
#: SCL — классификационная маска, её масштабировать нельзя.
_REFLECTANCE_BANDS = ("B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12")


def load_s2_stack(
    aoi: AOI,
    settings: Settings,
    items: Sequence,
    *,
    chunks: dict | None = None,
):
    """Собрать ленивый куб Sentinel-2 (time, y, x) по списку STAC-элементов.

    Возвращает :class:`xarray.Dataset` в рабочей проекции с каналами из
    ``settings.sentinel2.bands``. Пиксели не читаются, пока не потребуется
    результат — odc-stac строит dask-граф.
    """
    from odc.stac import load as odc_load

    ds = odc_load(
        items,
        bands=tuple(settings.sentinel2.bands),
        crs=settings.project.crs_working,
        resolution=settings.sentinel2.resolution_m,
        bbox=aoi.bbox,
        chunks=chunks or {"time": 1, "x": 1024, "y": 1024},
        groupby="solar_day",  # склеиваем полосы одной съёмки в одну дату
        resampling="nearest",
    )
    log.info(
        "Загружен куб S2: %d дат, %d x %d пикселей",
        ds.sizes.get("time", 0),
        ds.sizes.get("x", 0),
        ds.sizes.get("y", 0),
    )
    return ds


def apply_scl_mask(ds, settings: Settings):
    """Замаскировать облака, тени, снег и no-data по каналу SCL.

    SCL (Scene Classification Layer) — продукт Sen2Cor уровня L2A. Он не
    идеален: тонкий перистый облачный покров и тени на краю сцены часто
    пропускаются. Именно поэтому дальше берётся медиана по месяцу, а не
    единственное наблюдение.
    """
    import xarray as xr

    if "SCL" not in ds:
        raise KeyError("в кубе нет канала SCL — маску облаков построить нечем")

    bad = xr.zeros_like(ds["SCL"], dtype=bool)
    for cls in settings.sentinel2.scl_mask_classes:
        bad = bad | (ds["SCL"] == cls)

    masked = ds.drop_vars("SCL")
    for name in masked.data_vars:
        masked[name] = masked[name].where(~bad)
    masked = masked.assign_coords(valid_mask=~bad)
    return masked


def to_reflectance(ds):
    """Перевести DN в отражение 0..1 с поправкой Processing Baseline 04.00.

    До 2022-01-25: reflectance = DN / 10000
    После:         reflectance = (DN - 1000) / 10000

    ESA ввела смещение, чтобы хранить отрицательные значения после
    атмосферной коррекции. Для нас это означает, что необработанный ряд имеет
    искусственный разрыв ровно на этой дате — по всей планете сразу.
    """
    import xarray as xr

    if "time" not in ds.dims:
        raise KeyError("ожидается куб с измерением time")

    times = ds["time"].values.astype("datetime64[D]")
    offset = xr.DataArray(
        np.where(times >= S2_BASELINE_04_DATE, S2_BASELINE_04_OFFSET, 0.0),
        dims="time",
        coords={"time": ds["time"]},
        name="baseline_offset",
    )

    out = ds.copy()
    for name in out.data_vars:
        if name in _REFLECTANCE_BANDS:
            out[name] = (out[name] + offset) / S2_REFLECTANCE_SCALE
            out[name].attrs["units"] = "reflectance"
    return out


def monthly_composite(ds, settings: Settings):
    """Свести куб к одному значению на месяц (медиана) и отбраковать пустые месяцы.

    Медиана устойчива к выбросам: остаточное облако, которое SCL пропустил,
    сместит среднее, но не медиану из 3–6 наблюдений.

    Месяц, где валидных пикселей меньше ``min_valid_fraction``, помечается
    как пропуск (NaN) целиком, а не заполняется мусором: детектор изменений
    умеет работать с пропусками, но не умеет отличать «мало данных» от
    «поверхность изменилась».
    """
    grouped = ds.resample(time=settings.time.composite_freq)
    composite = grouped.median(skipna=True, keep_attrs=True)

    if "valid_mask" in ds.coords:
        valid_fraction = (
            ds["valid_mask"].astype("float32").resample(time=settings.time.composite_freq).mean()
        )
        enough = valid_fraction >= settings.sentinel2.min_valid_fraction
        for name in composite.data_vars:
            composite[name] = composite[name].where(enough)
        composite = composite.assign_coords(valid_fraction=valid_fraction)

    # Оставляем только пригодные для спектрального анализа месяцы
    months = composite["time"].dt.month
    keep = months.isin(settings.time.valid_months)
    composite = composite.sel(time=keep)

    log.info("Месячных композитов после отбраковки: %d", composite.sizes.get("time", 0))
    return composite


def build_feature_cube(aoi: AOI, settings: Settings, items: Sequence):
    """Полный путь от STAC-элементов до куба спектральных признаков.

    Возвращает Dataset с переменными ndvi, bsi, pmli, ndwi, ndmi, nbr
    по месячной сетке. Это вход детектора изменений.
    """
    from .indices import compute_all

    ds = load_s2_stack(aoi, settings, items)
    ds = apply_scl_mask(ds, settings)
    ds = to_reflectance(ds)
    ds = monthly_composite(ds, settings)
    return compute_all(ds)


def series_to_matrix(cube, variable: str) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    """Развернуть куб (time, y, x) в матрицу (time, n_pixels) для детектора.

    Возвращает (матрица, даты, исходная форма (ny, nx)). Обратная операция —
    :func:`matrix_to_raster`.
    """
    da = cube[variable]
    values = np.asarray(da.transpose("time", "y", "x").values, dtype="float32")
    n_t, ny, nx = values.shape
    return values.reshape(n_t, ny * nx), da["time"].values, (ny, nx)


def matrix_to_raster(flat: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Свернуть результат детектора обратно в растр (ny, nx)."""
    return flat.reshape(shape)


__all__ = [
    "apply_scl_mask",
    "build_feature_cube",
    "load_s2_stack",
    "matrix_to_raster",
    "monthly_composite",
    "series_to_matrix",
    "to_reflectance",
]
