"""Спектральные индексы — физическая основа детекции.

Свалка не имеет собственного «цвета». Она опознаётся комбинацией независимых
физических откликов, каждый из которых по отдельности неспецифичен:

    NDVI  — растительность исчезает и не возвращается (главный дискриминатор
            против пашни: у поля NDVI восстанавливается каждую весну);
    BSI   — доля открытого грунта и минерального материала растёт;
    PMLI  — отклик полимеров в коротковолновом ИК: у полиэтилена и ПЭТ
            обертоны C–H дают поглощение в районе 1730 и 2100–2300 нм,
            что частично попадает в каналы B11 (1610 нм) и B12 (2190 нм);
    NDWI  — служебный: отсекает воду, которая иначе даёт ложный «голый грунт»;
    NDMI  — влажность, помогает отличать свежий грунт от сухого мусора.

Все функции работают с xarray.DataArray (ленивые dask-массивы) и одинаково
корректно — с numpy. Отражения ожидаются как reflectance 0..1
(см. :func:`scale_reflectance`).

Литература по PMLI: Lu, Di, Ye et al., Plastic-Mulched Landcover Index.
BSI: Rikimaru et al., Bare Soil Index.
"""

from __future__ import annotations

from typing import TypeVar

import numpy as np

try:  # xarray необязателен для юнит-тестов на numpy
    import xarray as xr

    ArrayLike = TypeVar("ArrayLike", np.ndarray, "xr.DataArray")
except ImportError:  # pragma: no cover
    xr = None  # type: ignore[assignment]
    ArrayLike = TypeVar("ArrayLike", bound=np.ndarray)  # type: ignore[misc]


# Масштаб хранения Sentinel-2 L2A: DN = reflectance * 10000
S2_REFLECTANCE_SCALE = 10_000.0
# Смещение, введённое в Processing Baseline 04.00 (снимки с 2022-01-25)
S2_BASELINE_04_OFFSET = -1000.0
S2_BASELINE_04_DATE = np.datetime64("2022-01-25")


def scale_reflectance(dn, *, offset: float = 0.0):
    """Перевести целочисленные DN в отражение 0..1.

    Параметр ``offset`` нужен для снимков Processing Baseline >= 04.00,
    где ESA добавила смещение -1000. Если его не учесть, все индексы
    поедут на границе 2022 года и детектор изменений найдёт «разрыв»
    во всей области сразу — классическая ловушка Sentinel-2.
    """
    return (dn + offset) / S2_REFLECTANCE_SCALE


def baseline_offset_for(dates) -> np.ndarray:
    """Вернуть массив смещений (0 или -1000) для каждой даты наблюдения."""
    dates = np.asarray(dates, dtype="datetime64[D]")
    return np.where(dates >= S2_BASELINE_04_DATE, S2_BASELINE_04_OFFSET, 0.0)


def _normalized_difference(a, b, eps: float = 1e-6):
    """(a - b) / (a + b) с защитой от деления на ноль."""
    denom = a + b
    # Там, где сумма близка к нулю, индекс не определён -> NaN, а не inf
    if xr is not None and isinstance(denom, xr.DataArray):
        return xr.where(np.abs(denom) < eps, np.nan, (a - b) / denom)
    denom = np.where(np.abs(denom) < eps, np.nan, denom)
    return (a - b) / denom


def ndvi(nir, red):
    """Normalized Difference Vegetation Index.

    NDVI = (B08 - B04) / (B08 + B04)

    Растительность сильно отражает в NIR и поглощает в красном.
    Ключ к детекции свалки — не абсолютное значение, а **необратимость**
    падения: см. :mod:`vantage.change`.
    """
    return _normalized_difference(nir, red)


def bsi(swir1, red, nir, blue):
    """Bare Soil Index.

    BSI = ((B11 + B04) - (B08 + B02)) / ((B11 + B04) + (B08 + B02))

    Растёт при появлении открытого грунта и минерального строительного
    мусора — самой массовой фракции стихийной свалки.
    """
    return _normalized_difference(swir1 + red, nir + blue)


def pmli(swir1, red):
    """Plastic-Mulched Landcover Index — отклик полимеров в SWIR.

    PMLI = (B11 - B04) / (B11 + B04)

    Индекс придуман для пластиковой мульчи на полях, но физика та же:
    полимеры дают характерное поведение в коротковолновом ИК. На 20-метровом
    B11 он не является доказательством наличия пластика — он является
    **одним из пяти голосов** в ансамбле признаков.
    """
    return _normalized_difference(swir1, red)


def ndwi(green, nir):
    """Normalized Difference Water Index (McFeeters).

    NDWI = (B03 - B08) / (B03 + B08)

    Служебный индекс: вода и мокрый солончак дают высокий BSI и низкий NDVI,
    то есть маскируются под свалку. Всё, что здесь > ~0.2, выбрасывается.
    """
    return _normalized_difference(green, nir)


def ndmi(nir, swir1):
    """Normalized Difference Moisture Index.

    NDMI = (B08 - B11) / (B08 + B11)

    Помогает отличить свежевскрытый влажный грунт (стройка, карьер)
    от сухой массы отходов.
    """
    return _normalized_difference(nir, swir1)


def nbr(nir, swir2):
    """Normalized Burn Ratio.

    NBR = (B08 - B12) / (B08 + B12)

    Свалки регулярно горят. Резкое падение NBR при одновременном росте
    тепловой аномалии — сильный дополнительный признак действующей свалки,
    а не просто нарушенного грунта.
    """
    return _normalized_difference(nir, swir2)


#: Индексы, используемые как признаки. Порядок фиксирован — от него зависит
#: порядок каналов в чипах и порядок весов в панели объяснимости.
FEATURE_INDICES: tuple[str, ...] = ("ndvi", "bsi", "pmli", "ndwi", "ndmi", "nbr")


def compute_all(ds: "xr.Dataset") -> "xr.Dataset":
    """Посчитать все индексы для Dataset с каналами Sentinel-2.

    Ожидаются переменные B02, B03, B04, B08, B11, B12 в отражении 0..1.
    Возвращает новый Dataset только с индексами (каналы не дублируются,
    чтобы не раздувать память при ленивых вычислениях).
    """
    if xr is None:  # pragma: no cover
        raise ImportError("для compute_all нужен xarray")

    required = {"B02", "B03", "B04", "B08", "B11", "B12"}
    missing = required - set(ds.data_vars)
    if missing:
        raise KeyError(f"в Dataset отсутствуют каналы: {sorted(missing)}")

    out = xr.Dataset(
        {
            "ndvi": ndvi(ds["B08"], ds["B04"]),
            "bsi": bsi(ds["B11"], ds["B04"], ds["B08"], ds["B02"]),
            "pmli": pmli(ds["B11"], ds["B04"]),
            "ndwi": ndwi(ds["B03"], ds["B08"]),
            "ndmi": ndmi(ds["B08"], ds["B11"]),
            "nbr": nbr(ds["B08"], ds["B12"]),
        },
        attrs=dict(ds.attrs),
    )
    for name in out.data_vars:
        out[name].attrs["long_name"] = name.upper()
        out[name].attrs["valid_range"] = (-1.0, 1.0)
    return out


__all__ = [
    "S2_REFLECTANCE_SCALE",
    "S2_BASELINE_04_OFFSET",
    "FEATURE_INDICES",
    "scale_reflectance",
    "baseline_offset_for",
    "ndvi",
    "bsi",
    "pmli",
    "ndwi",
    "ndmi",
    "nbr",
    "compute_all",
]
