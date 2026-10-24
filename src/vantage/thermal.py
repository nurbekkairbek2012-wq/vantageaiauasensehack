"""Тепловая аномалия — пятый физический признак.

Идея
----
Внутри свалки идёт анаэробное разложение органики. Это экзотермический
процесс: тело свалки греется само, без внешнего источника. Разница с
окружающим фоном обычно составляет единицы градусов — мало для глаза,
достаточно для теплового канала Landsat.

Признак ценен тем, что он **прямо указывает на присутствие органики**,
то есть отличает свалку от того, с чем её путают чаще всего:

    карьер, отвал грунта   — холодные, разлагаться нечему
    стройплощадка          — холодная
    снегосвалка            — ХОЛОДНЕЕ фона, а не теплее
    действующая свалка     — теплее фона

Снегосвалка — отдельный случай, ради которого стоит держать этот
признак. В Астане они есть, спектрально почти неотличимы от свалки,
и на Q&A про них спрашивают. Тепловой канал разделяет их однозначно
и в нужную сторону: снег даёт отрицательную аномалию.

Когда признак работает лучше всего
----------------------------------
Зимой. Летом солнце прогревает любую тёмную поверхность, и разница
между свалкой и асфальтом теряется в шуме солнечного нагрева. Зимой,
на фоне снега, собственное тепло свалки видно контрастнее всего —
вплоть до протаявшего пятна.

Поэтому тепловая ветка пайплайна работает по ХОЛОДНОМУ сезону, тогда
как спектральная — по тёплому. Это единственный признак с обратной
сезонностью, и это осознанно.
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

#: Месяцы, пригодные для тепловой ветки: снег на земле, солнце низко.
COLD_SEASON_MONTHS: tuple[int, ...] = (11, 12, 1, 2, 3)

#: Превышение над фоном, считающееся «полной силой» признака, кельвины.
#:
#: Было 3.0 — по физике процесса: поверхность свалки, под которой идёт
#: анаэробное разложение, зимой теплее фона примерно настолько. Число
#: верное и к делу не относящееся, потому что измеряем мы не поверхность,
#: а пиксель Landsat в 100 метров.
#:
#: Объект в 40 метров занимает шестую часть такого пикселя, остальное —
#: холодный фон, и измеренное превышение падает во столько же раз. На
#: кольцевом прогоне: медиана 0,003 K, девяностый процентиль 0,50 K,
#: максимум 1,31 K. При шкале в 3 K признак не набирал и шестой части силы
#: ни на одном объекте — то есть в ансамбле не участвовал вовсе.
#:
#: 1.5 K — это то, что через прибор видно: чуть выше наблюдённого максимума,
#: с запасом на объекты крупнее наших. Шкала описывает измеримое, а не
#: желаемое; физика самой свалки от этого не меняется.
FULL_SCALE_ANOMALY_K = 1.5

#: Landsat Collection 2 Level-2: температура поверхности хранится
#: масштабированной. ST = DN * 0.00341802 + 149.0 (кельвины).
LANDSAT_ST_SCALE = 0.00341802
LANDSAT_ST_OFFSET = 149.0

#: Порог отрицательной аномалии, при котором объект считается снегосвалкой.
SNOW_DUMP_THRESHOLD_K = -1.5


def _float32(values):
    """Привести к float32, не потеряв обёртку xarray.

    ``np.asarray`` на DataArray возвращает голый массив: пропадают и оси,
    и координаты. Присвоить такой массив обратно в Dataset уже нельзя —
    xarray требует явных имён измерений и падает с MissingDimensionsError.

    Ошибка была настоящей и жила ровно до первого прогона: юнит-тесты
    подают сюда numpy, где разницы нет, а куб из odc-stac — DataArray.
    То есть обе ветки, радарная и тепловая, не могли отработать ни разу,
    и заметно это стало только на настоящих данных.
    """
    if hasattr(values, "dims"):  # xarray.DataArray
        return values.astype("float32")
    return np.asarray(values, dtype="float32")


def to_kelvin(digital_number):
    """Перевести DN теплового продукта Landsat в кельвины.

    Без масштабирования значения выглядят как десятки тысяч, и любое
    сравнение с порогом в кельвинах даёт бессмыслицу, не падая с ошибкой.
    """
    return _float32(digital_number) * LANDSAT_ST_SCALE + LANDSAT_ST_OFFSET


def to_celsius(kelvin):
    return _float32(kelvin) - 273.15


def local_background(
    temperature: np.ndarray,
    radius_px: int,
    *,
    exclude_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Фоновая температура вокруг каждого пикселя.

    Фон считается **медианой** в кольце вокруг точки, а не средним по всей
    сцене. Причина: температура поверхности сильно зависит от рельефа,
    влажности и близости к городу. Аномалия имеет смысл только
    относительно ближайшего окружения — иначе весь городской остров тепла
    попадёт в «аномалии».

    ``exclude_mask`` — пиксели, не участвующие в оценке фона (сам объект,
    вода, застройка).
    """
    from scipy import ndimage

    if radius_px < 1:
        raise ValueError("радиус фона должен быть не меньше одного пикселя")

    data = np.asarray(temperature, dtype="float32")
    if exclude_mask is not None:
        data = np.where(exclude_mask, np.nan, data)

    # Кольцевой элемент: центральная область исключается, чтобы сам
    # объект не входил в оценку собственного фона.
    y, x = np.ogrid[-radius_px : radius_px + 1, -radius_px : radius_px + 1]
    distance = np.hypot(y, x)
    ring = (distance <= radius_px) & (distance > radius_px / 2)

    filled = np.where(np.isfinite(data), data, np.nanmedian(data))
    return ndimage.median_filter(filled, footprint=ring, mode="nearest").astype("float32")


def thermal_anomaly(
    temperature: np.ndarray,
    *,
    radius_px: int = 33,
    exclude_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Превышение температуры над локальным фоном, кельвины.

    Положительное значение — объект теплее окружения (разложение).
    Отрицательное — холоднее (снег, вода, тень).
    """
    background = local_background(temperature, radius_px, exclude_mask=exclude_mask)
    return (np.asarray(temperature, dtype="float32") - background).astype("float32")


def anomaly_strength(values, *, full_scale: float = FULL_SCALE_ANOMALY_K):
    """Нормировать аномалию в шкалу 0..1 для панели признаков.

    Отрицательные аномалии дают ноль, а не отрицательную силу: холодный
    объект не является слабым доказательством свалки, он не является
    доказательством вообще. Признак «холоднее фона» обрабатывается
    отдельно — см. :func:`is_snow_dump`.
    """
    values = np.asarray(values, dtype="float32")
    with np.errstate(invalid="ignore"):
        return np.clip(values / full_scale, 0.0, 1.0)


def is_snow_dump(
    anomaly_k, *, threshold: float = SNOW_DUMP_THRESHOLD_K
) -> np.ndarray:
    """Признак снегосвалки: устойчиво холоднее окружения.

    Именно этот тест отвечает на вопрос «а как вы отличаете свалку от
    снегосвалки», который в Астане задают обязательно. Ответ короткий:
    свалка греется, снегосвалка охлаждает.
    """
    values = np.asarray(anomaly_k, dtype="float32")
    with np.errstate(invalid="ignore"):
        return np.where(np.isfinite(values), values <= threshold, False)


def radius_in_pixels(radius_m: float, resolution_m: float) -> int:
    """Перевести радиус фона из метров в пиксели."""
    if resolution_m <= 0:
        raise ValueError("разрешение должно быть положительным")
    return max(1, round(radius_m / resolution_m))


def build_thermal_stack(aoi, settings, items, *, chunks: dict | None = None):
    """Загрузить тепловой куб Landsat и привести к кельвинам."""
    from odc.stac import load as odc_load

    ds = odc_load(
        items,
        bands=(settings.landsat.thermal_asset,),
        crs=settings.project.crs_working,
        resolution=settings.landsat.resolution_m,
        bbox=aoi.bbox,
        chunks=chunks or {"time": 1, "x": 512, "y": 512},
        groupby="solar_day",
        resampling="bilinear",
    )
    log.info("Загружен тепловой куб Landsat: %d дат", ds.sizes.get("time", 0))

    name = settings.landsat.thermal_asset
    out = ds.copy()
    out[name] = to_kelvin(out[name])
    out[name].attrs["units"] = "K"
    return out


def cold_season_composite(ds, variable: str, months: tuple[int, ...] = COLD_SEASON_MONTHS):
    """Медианный композит по холодному сезону.

    Единичный зимний снимок ненадёжен: облачность, свежий снегопад,
    оттепель. Медиана по нескольким зимним проходам устойчива к каждому
    из этих случаев.
    """
    selected = ds.sel(time=ds["time"].dt.month.isin(list(months)))
    if selected.sizes.get("time", 0) == 0:
        raise ValueError(
            f"нет снимков в холодные месяцы {months}. Расширьте период или список месяцев."
        )
    log.info("Тепловой композит: %d зимних снимков", selected.sizes["time"])
    return selected[variable].median(dim="time", skipna=True)


__all__ = [
    "COLD_SEASON_MONTHS",
    "FULL_SCALE_ANOMALY_K",
    "LANDSAT_ST_OFFSET",
    "LANDSAT_ST_SCALE",
    "SNOW_DUMP_THRESHOLD_K",
    "anomaly_strength",
    "build_thermal_stack",
    "cold_season_composite",
    "is_snow_dump",
    "local_background",
    "radius_in_pixels",
    "thermal_anomaly",
    "to_celsius",
    "to_kelvin",
]
