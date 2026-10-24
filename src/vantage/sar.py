"""Радарная стабильность поверхности — четвёртый физический признак.

Идея
----
Оптические признаки (NDVI, BSI, PMLI) говорят, **что** лежит на
поверхности. Радар говорит другое: насколько поверхность **стабильна
во времени**.

Естественная степь под радаром почти не меняется от прохода к проходу:
рельеф тот же, шероховатость та же, обратное рассеяние стабильно.
Свалка меняется постоянно — привозят новое, ветер разносит плёнку,
техника перекапывает, масса оседает. Каждый проход видит другую
поверхность.

Это делает признак **ортогональным оптике**: он срабатывает там, где
оптика бессильна, и молчит там, где оптика ошибается. Карьер после
выемки грунта оптически выглядит как свалка (голая земля, NDVI упал),
но радарно он стабилен — стенки карьера не меняются неделями.

Что именно считается
--------------------
Строгая интерферометрическая когерентность требует пар SLC-снимков
и обработки фазы, которая не помещается ни в неделю, ни в бесплатные
данные. Мы считаем **прокси когерентности** по амплитуде RTC-продукта:

    incoherence = std(backscatter_dB) по скользящему окну времени

Высокая дисперсия обратного рассеяния = нестабильная поверхность.
Это не то же самое, что настоящая когерентность, и на защите это надо
сказать прямо: мы используем доступный прокси, а не подменяем понятия.

Почему в децибелах
------------------
Обратное рассеяние распределено логнормально: в линейной шкале
дисперсия растёт вместе со средним, и яркие объекты автоматически
выглядят «нестабильными». Перевод в дБ выравнивает это.
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

#: Нижняя граница обратного рассеяния перед логарифмированием.
#: Нули и отрицательные значения в RTC встречаются на воде и в тени
#: рельефа; без отсечки log10 даёт -inf и отравляет всю статистику.
MIN_BACKSCATTER = 1e-5

#: Дисперсия обратного рассеяния, считающаяся «полной нестабильностью».
#: 3 дБ — это двукратное изменение мощности сигнала между проходами.
FULL_SCALE_INCOHERENCE_DB = 3.0


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


def to_db(backscatter):
    """Перевести линейное обратное рассеяние в децибелы.

    sigma0_dB = 10 * log10(sigma0)
    """
    clipped = np.maximum(_float32(backscatter), MIN_BACKSCATTER)
    return 10.0 * np.log10(clipped)


def temporal_incoherence(
    backscatter_db: np.ndarray,
    *,
    min_observations: int = 4,
) -> np.ndarray:
    """Нестабильность поверхности: СКО обратного рассеяния во времени.

    Вход — матрица (T, N): T проходов, N пикселей, значения в дБ.
    Выход — вектор длины N.

    Пиксели с недостаточным числом наблюдений получают NaN, а не ноль:
    «мало данных» и «поверхность стабильна» — принципиально разные
    утверждения, и склеивать их значит врать о наличии доказательства.
    """
    if backscatter_db.ndim != 2:
        raise ValueError(f"ожидается матрица (T, N), получено {backscatter_db.shape}")

    valid = np.isfinite(backscatter_db).sum(axis=0)
    result = np.full(backscatter_db.shape[1], np.nan, dtype="float32")

    enough = valid >= min_observations
    if np.any(enough):
        with np.errstate(invalid="ignore"):
            result[enough] = np.nanstd(backscatter_db[:, enough], axis=0)
    return result


#: Сколько элементов каталога опросить, чтобы узнать имена ассетов.
#: Одного мало: у отдельных сцен бывает только одна поляризация.
_ASSET_PROBE_ITEMS = 5

#: Минимальное число проходов в сегменте для устойчивой оценки дисперсии.
#: Оценка СКО по выборке сама имеет разброс порядка sigma/sqrt(2n): при
#: n=10 и настоящем sigma=3 дБ измеренное значение гуляет примерно на
#: ±0.7 дБ, что сопоставимо с искомым эффектом. При n=40 разброс падает
#: до ±0.35 дБ. Sentinel-1 проходит над точкой каждые 6-12 суток, так что
#: 40 наблюдений — это меньше года, и требование выполнимо.
MIN_OBS_FOR_STABLE_STD = 20


def std_uncertainty(sigma: float, n: int) -> float:
    """Ожидаемая погрешность оценки СКО по выборке размера n.

    Нужна, чтобы не принимать шум оценки за изменение поверхности.
    Приближение для нормального распределения: sigma / sqrt(2n).
    """
    if n < 2:
        return float("inf")
    return float(sigma / np.sqrt(2 * n))


def incoherence_change(
    backscatter_db: np.ndarray,
    break_index: np.ndarray,
    *,
    min_observations: int = MIN_OBS_FOR_STABLE_STD,
) -> np.ndarray:
    """Прирост нестабильности после разрыва — то, что реально нужно.

    Абсолютная нестабильность бесполезна: город, вода и поля с разной
    агротехникой всегда нестабильны. Смысл имеет **изменение**: была
    поверхность стабильной, стала нестабильной.

    ``break_index`` — момент разрыва по каждому пикселю (из
    :mod:`vantage.change`); -1 означает отсутствие разрыва.

    Важно про размер выборки. Оценка СКО сама шумит, и при десятке
    наблюдений в сегменте её разброс сопоставим с искомым эффектом.
    Поэтому порог ``min_observations`` заметно выше, чем кажется нужным:
    сегмент с малым числом проходов честнее пометить как NaN, чем выдать
    случайное число за признак.
    """
    n_t, n_pix = backscatter_db.shape
    if break_index.shape[0] != n_pix:
        raise ValueError("длина break_index не совпадает с числом пикселей")

    result = np.full(n_pix, np.nan, dtype="float32")
    index = np.arange(n_t)[:, None]

    before_mask = index < break_index[None, :]
    after_mask = (index >= break_index[None, :]) & (break_index[None, :] >= 0)

    def masked_std(mask: np.ndarray) -> np.ndarray:
        data = np.where(mask & np.isfinite(backscatter_db), backscatter_db, np.nan)
        counts = np.isfinite(data).sum(axis=0)
        out = np.full(n_pix, np.nan, dtype="float32")
        usable = counts >= min_observations
        if np.any(usable):
            with np.errstate(invalid="ignore"):
                out[usable] = np.nanstd(data[:, usable], axis=0)
        return out

    before = masked_std(before_mask)
    after = masked_std(after_mask)
    valid = np.isfinite(before) & np.isfinite(after)
    result[valid] = after[valid] - before[valid]
    return result


def incoherence_strength(values, *, full_scale: float = FULL_SCALE_INCOHERENCE_DB):
    """Нормировать прирост нестабильности в шкалу 0..1 для панели признаков.

    Насыщение обязательно: рост дисперсии на 10 дБ не «втрое убедительнее»,
    чем на 3 дБ — поверхность не может стать нестабильной дважды.
    """
    values = np.asarray(values, dtype="float32")
    with np.errstate(invalid="ignore"):
        return np.clip(values / full_scale, 0.0, 1.0)


def resolve_polarization_assets(items, polarizations) -> dict[str, str]:
    """Сопоставить VV/VH фактическим именам ассетов в каталоге.

    В конфигурации поляризации записаны так, как их пишут в радиолокации:
    ``VV``, ``VH``. В коллекции ``sentinel-1-rtc`` на Planetary Computer
    ассеты называются ``vv`` и ``vh`` — строчными. Загрузчик odc-stac
    сверяет имена буквально и падает с ``No such band/alias: VV``.

    Менять конфигурацию под конкретного провайдера неправильно: физическое
    имя поляризации не зависит от того, кто раздаёт снимки, а следующий
    каталог назовёт ассеты ещё как-нибудь. Поэтому сопоставление —
    здесь, и оно без учёта регистра.

    Возвращает ``{каноническое имя: имя ассета}``.
    """
    available: set[str] = set()
    for item in list(items)[:_ASSET_PROBE_ITEMS]:
        available.update(getattr(item, "assets", {}) or {})

    resolved: dict[str, str] = {}
    lowered = {name.lower(): name for name in available}
    for canonical in polarizations:
        actual = lowered.get(canonical.lower())
        if actual is not None:
            resolved[canonical] = actual

    if not resolved:
        raise KeyError(
            f"ни одна из поляризаций {tuple(polarizations)} не нашлась среди "
            f"ассетов {sorted(available)}"
        )
    return resolved


def build_sar_stack(aoi, settings, items, *, chunks: dict | None = None):
    """Загрузить куб Sentinel-1 RTC и вернуть обратное рассеяние в дБ.

    Используется RTC-продукт (radiometrically terrain corrected): без
    коррекции рельефа склоны, обращённые к спутнику, всегда ярче, и
    дисперсия по времени начинает отражать геометрию съёмки, а не
    свойства поверхности.
    """
    from odc.stac import load as odc_load

    resolved = resolve_polarization_assets(items, settings.sentinel1.polarizations)
    ds = odc_load(
        items,
        bands=tuple(resolved.values()),
        crs=settings.project.crs_working,
        resolution=settings.sentinel1.resolution_m,
        bbox=aoi.bbox,
        chunks=chunks or {"time": 1, "x": 1024, "y": 1024},
        groupby="solar_day",
        resampling="bilinear",
    )
    # Дальше по коду поляризации зовутся канонически, поэтому имена
    # провайдера остаются только внутри загрузчика.
    ds = ds.rename({actual: canonical for canonical, actual in resolved.items()})
    log.info("Загружен куб Sentinel-1: %d дат, поляризации %s",
             ds.sizes.get("time", 0), ", ".join(resolved))

    out = ds.copy()
    for name in out.data_vars:
        out[name] = to_db(out[name])
        out[name].attrs["units"] = "dB"
    return out


def combined_polarization(ds, polarizations: tuple[str, ...] = ("VV", "VH")):
    """Свести поляризации в один слой усреднением в дБ.

    VV чувствительнее к шероховатости поверхности, VH — к объёмному
    рассеянию на неоднородных объектах (а свалка — именно такой объект).
    Использовать обе и усреднить надёжнее, чем выбирать одну: разные
    типы отходов дают отклик в разных поляризациях.
    """
    available = [p for p in polarizations if p in ds]
    if not available:
        raise KeyError(f"в кубе нет ни одной из поляризаций {polarizations}")
    stacked = np.stack([np.asarray(ds[p].values, dtype="float32") for p in available], axis=0)
    with np.errstate(invalid="ignore"):
        return np.nanmean(stacked, axis=0)


__all__ = [
    "FULL_SCALE_INCOHERENCE_DB",
    "MIN_BACKSCATTER",
    "build_sar_stack",
    "combined_polarization",
    "incoherence_change",
    "incoherence_strength",
    "resolve_polarization_assets",
    "temporal_incoherence",
    "to_db",
]
