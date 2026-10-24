"""Детекция необратимых изменений во временном ряду — ядро системы.

Здесь принимается главное решение: этот пиксель менялся так, как меняется
свалка, или так, как меняется всё остальное.

Почему нельзя просто сравнить два снимка
----------------------------------------
На паре снимков «2019 и 2025» свалка неотличима от убранного поля, стройки,
сгоревшей травы или просто засушливого года. Разделяет их **форма ряда**:

    пашня          — NDVI падает каждую осень и возвращается каждую весну
    засуха         — NDVI просел на один сезон и восстановился
    стройка        — NDVI упал и вернулся после озеленения / или площадь мала
    карьер         — NDVI упал, но BSI вырос при высоком NDMI (влажный грунт)
    СВАЛКА         — NDVI упал резко, НЕ вернулся, BSI вырос и остался высоким

Алгоритм
--------
1. **Удаление сезонности.** Ряд раскладывается на гармоники годового цикла
   (2 гармоники + линейный тренд) взвешенным МНК с учётом пропусков.
   Именно этот шаг отделяет пашню: её сезонный ход полностью уходит в модель
   и в остатках ничего не остаётся.
2. **Поиск точки разрыва.** По остаткам вычисляется статистика типа Чоу:
   для каждой допустимой точки разделения сравниваются средние до и после,
   нормированные на стандартную ошибку. Берётся максимум по модулю.
3. **Проверка величины.** Разрыв должен сопровождаться падением NDVI и ростом
   BSI не меньше пороговых значений из конфигурации.
4. **Проверка необратимости.** В окне восстановления после разрыва NDVI не
   должен вернуться к исходному уровню. Если вернулся — это сезонное или
   временное нарушение, а не свалка.

Всё считается векторно по пикселям, чанками по памяти.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from .config import ChangeCfg

log = logging.getLogger(__name__)

#: Регуляризация нормального уравнения: защищает от вырожденной матрицы,
#: когда у пикселя мало валидных наблюдений и гармоники становятся коллинеарны.
_RIDGE = 1e-6

#: Минимальное число валидных наблюдений, чтобы вообще пытаться считать.
#: p=6 параметров модели + запас на степени свободы.
_MIN_VALID_OBS = 12


@dataclass
class BreakpointResult:
    """Результат детекции по каждому пикселю (плоские массивы длины N)."""

    has_break: np.ndarray
    break_index: np.ndarray
    zscore: np.ndarray
    ndvi_before: np.ndarray
    ndvi_after: np.ndarray
    ndvi_drop: np.ndarray
    bsi_rise: np.ndarray
    recovered: np.ndarray
    n_valid: np.ndarray
    #: Хватило ли наблюдений после разрыва, чтобы судить о необратимости.
    #: Разрыв в конце ряда «не восстановился» просто потому, что смотреть
    #: было не на что, — и такой вывод не является выводом.
    observable: np.ndarray | None = None

    def __len__(self) -> int:
        return int(self.has_break.size)

    def summary(self) -> dict[str, float | int]:
        n = len(self)
        detected = int(np.count_nonzero(self.has_break))
        return {
            "pixels": n,
            "detected": detected,
            "detected_pct": round(100.0 * detected / n, 3) if n else 0.0,
            "rejected_recovered": int(np.count_nonzero(self.recovered)),
            "insufficient_data": int(np.count_nonzero(self.n_valid < _MIN_VALID_OBS)),
        }


# --------------------------------------------------------------------------- #
#  Шаг 1. Сезонная модель
# --------------------------------------------------------------------------- #


def harmonic_design(dates: np.ndarray, *, n_harmonics: int = 2, with_trend: bool = True) -> np.ndarray:
    """Построить матрицу плана для гармонической регрессии.

    Столбцы: [1, t, cos(2πt), sin(2πt), cos(4πt), sin(4πt), ...]
    где t — время в годах от начала ряда.

    Две гармоники — сознательный выбор: первая описывает годовой цикл
    «зима-лето», вторая — асимметрию весеннего роста и осеннего спада.
    Больше гармоник начинают подгоняться под шум и «съедать» настоящий разрыв.
    """
    t = _years_since_start(dates)
    cols = [np.ones_like(t)]
    if with_trend:
        cols.append(t)
    for k in range(1, n_harmonics + 1):
        cols.append(np.cos(2.0 * np.pi * k * t))
        cols.append(np.sin(2.0 * np.pi * k * t))
    return np.column_stack(cols).astype("float64")


def _years_since_start(dates: np.ndarray) -> np.ndarray:
    d = np.asarray(dates, dtype="datetime64[D]").astype("int64")
    return (d - d[0]) / 365.25


#: Среднее число дней в календарном месяце (365.25 / 12).
_DAYS_PER_MONTH = 30.4375


def months_to_observations(dates: np.ndarray, months: int, *, minimum: int = 3) -> int:
    """Перевести календарные месяцы в число наблюдений ряда.

    Тонкость, которая легко становится ошибкой. В конфигурации окна заданы
    в **календарных месяцах** — так их формулирует человек. Но ряд состоит
    из композитов только за пригодные месяцы (апрель–октябрь), поэтому в году
    не 12 шагов, а 7. Индексировать массив числом «18» в надежде получить
    полтора года — значит на самом деле получить 2.6 года.

    Здесь пересчёт делается по фактической плотности наблюдений в ряду.
    """
    d = np.asarray(dates, dtype="datetime64[D]").astype("int64")
    if d.size < 2:
        return minimum
    span_months = (d[-1] - d[0]) / _DAYS_PER_MONTH
    if span_months <= 0:
        return minimum
    obs_per_month = d.size / span_months
    return max(minimum, round(months * obs_per_month))


def recovery_window_stops(dates: np.ndarray, break_index: np.ndarray, months: int) -> np.ndarray:
    """Для каждого пикселя — индекс конца окна восстановления (не включая).

    Считается по календарным датам, а не по числу шагов: окно «18 месяцев
    после разрыва» должно означать именно 18 месяцев, независимо от того,
    сколько наблюдений в него попало.
    """
    d = np.asarray(dates, dtype="datetime64[D]")
    limit_days = np.int64(round(months * _DAYS_PER_MONTH))
    n_t = d.size
    safe_k = np.clip(break_index, 0, n_t - 1)
    deadline = d[safe_k] + limit_days.astype("timedelta64[D]")
    return np.searchsorted(d, deadline, side="right").astype("int32")


def last_observable_index(dates: np.ndarray, min_after_months: int) -> int | None:
    """Последняя точка ряда, после которой ещё остаётся ``min_after_months``.

    Возвращает ``None``, если ограничения нет. Если ряд короче требуемого
    окна, возвращает ``-1`` — искать разрыв негде, и лучше честно не найти
    ничего, чем найти то, что нельзя проверить.
    """
    if min_after_months <= 0:
        return None
    d = np.asarray(dates, dtype="datetime64[D]").astype("int64")
    if d.size == 0:
        return -1
    # int() не лишний: _DAYS_PER_MONTH делает выражение numpy-скаляром.
    limit = d[-1] - int(round(min_after_months * _DAYS_PER_MONTH))  # noqa: RUF046
    fits = np.nonzero(d <= limit)[0]
    return int(fits[-1]) if fits.size else -1


def observed_after_months(dates: np.ndarray, break_index: np.ndarray) -> np.ndarray:
    """Сколько календарных месяцев ряда осталось после каждой точки разрыва."""
    d = np.asarray(dates, dtype="datetime64[D]").astype("int64")
    if d.size == 0:
        return np.zeros_like(break_index, dtype="float32")
    safe_k = np.clip(break_index, 0, d.size - 1)
    return ((d[-1] - d[safe_k]) / _DAYS_PER_MONTH).astype("float32")


def deseasonalize(
    y: np.ndarray,
    design: np.ndarray,
    *,
    chunk_size: int = 200_000,
) -> np.ndarray:
    """Убрать сезонность и тренд, вернуть остатки той же формы (T, N).

    Взвешенный МНК с весом 0 на пропусках, решаемый пакетно: нормальные
    уравнения строятся сразу для всех пикселей чанка, затем один вызов
    ``np.linalg.solve`` на (N, p, p). Это на порядки быстрее цикла по пикселям
    и при этом остаётся обычным МНК, который легко объяснить.
    """
    n_t, n_pix = y.shape
    p = design.shape[1]
    if design.shape[0] != n_t:
        raise ValueError(f"design имеет {design.shape[0]} строк, а ряд — {n_t} моментов")

    residuals = np.full_like(y, np.nan, dtype="float32")
    # Предвычисляем попарные произведения столбцов плана: (T, p*p)
    xx = (design[:, :, None] * design[:, None, :]).reshape(n_t, p * p)

    for start in range(0, n_pix, chunk_size):
        stop = min(start + chunk_size, n_pix)
        block = y[:, start:stop].astype("float64")
        mask = np.isfinite(block)
        w = mask.astype("float64")
        filled = np.where(mask, block, 0.0)

        # Нормальные уравнения: XtWX (n, p, p), XtWy (n, p)
        xtwx = (w.T @ xx).reshape(-1, p, p)
        xtwy = (design.T @ (w * filled)).T

        # Регуляризация — иначе пиксели с редкими наблюдениями дают
        # вырожденную матрицу и LinAlgError на весь чанк.
        xtwx[:, np.arange(p), np.arange(p)] += _RIDGE

        n_valid = mask.sum(axis=0)
        usable = n_valid >= _MIN_VALID_OBS

        beta = np.zeros((stop - start, p), dtype="float64")
        if np.any(usable):
            beta[usable] = np.linalg.solve(xtwx[usable], xtwy[usable][..., None])[..., 0]

        fitted = design @ beta.T  # (T, n)
        resid = np.where(mask, block - fitted, np.nan)
        resid[:, ~usable] = np.nan
        residuals[:, start:stop] = resid.astype("float32")

    return residuals


# --------------------------------------------------------------------------- #
#  Шаг 2. Поиск точки разрыва
# --------------------------------------------------------------------------- #


def _nan_cumsum_and_count(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mask = np.isfinite(x)
    filled = np.where(mask, x, 0.0)
    return np.cumsum(filled, axis=0), np.cumsum(mask, axis=0)


def find_breakpoint(
    residuals: np.ndarray,
    min_segment: int,
    *,
    max_index: int | None = None,
    edge_guard: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Найти наиболее вероятную точку разрыва в остатках.

    Для каждой допустимой точки k сравниваются средние остатков до и после,
    нормированные на стандартную ошибку разности (статистика типа Чоу).
    Возвращает (индекс разрыва, |z|). Индекс -1 означает «данных не хватает».

    Ограничение ``min_segment`` принципиально: разрыв на второй месяц ряда
    статистически неотличим от выброса, а разрыв за месяц до конца ряда
    невозможно проверить на необратимость.

    ``max_index`` сужает область поиска сверху. Это не то же самое, что
    отбросить неподходящий результат потом: у пикселя разрыв ищется как
    **максимум** статистики по всем допустимым точкам, и если у настоящего
    разрыва 2021 года окажется конкурент на хвосте ряда, максимум уйдёт
    туда. Отбраковка постфактум выбросила бы такой пиксель целиком вместе
    с настоящей находкой; сужение области поиска — оставляет её.

    ── Стенка ──────────────────────────────────────────────────────────

    У сужения есть своя цена, и она обнаружилась на распределении дат.
    Ограничение не убирает пиксели, у которых настоящее изменение
    произошло ПОЗЖЕ предела наблюдаемости, — оно переименовывает их. Лучшая
    доступная точка для такого пикселя всегда лежит вплотную к границе, и
    он получает дату границы.

    На кольцевом прогоне это дало 21 объект ровно на последнем допустимом
    индексе и подъём частот на подходе к нему: 43 → 17, 44 → 9, 45 → 7,
    46 → 12, 47 → 15, 48 → 21. В календаре это выглядело как всплеск свалок
    в 2024 году после провала в 2022–2023. Всплеска не было — была стенка.

    ``edge_guard`` отличает находку от упора в стенку: если лучшая точка
    без ограничения лежит ЗА пределом, а лучшая внутри — вплотную к нему,
    у пикселя нет наблюдаемого разрыва, и честный ответ -1. Разрыв в
    середине ряда при этом уцелеет: там лучшая внутренняя точка не у стенки.
    """
    n_t, n_pix = residuals.shape
    if n_t < 2 * min_segment + 1:
        return np.full(n_pix, -1, dtype="int32"), np.zeros(n_pix, dtype="float32")

    stop_full = n_t - min_segment
    stop = stop_full
    if max_index is not None:
        stop = min(stop, int(max_index) + 1)
    if stop <= min_segment:
        return np.full(n_pix, -1, dtype="int32"), np.zeros(n_pix, dtype="float32")

    cumsum, count = _nan_cumsum_and_count(residuals)
    total_sum = cumsum[-1]
    total_n = count[-1]

    # Общее СКО остатков — знаменатель статистики.
    # Считаем только по столбцам, где вообще есть данные: nanstd по полностью
    # пустому пикселю выдаёт предупреждение и NaN, а таких пикселей на краю
    # плитки бывает много.
    sd = np.full(n_pix, np.nan, dtype="float64")
    non_empty = total_n > 1
    if np.any(non_empty):
        with np.errstate(invalid="ignore", divide="ignore"):
            sd[non_empty] = np.nanstd(residuals[:, non_empty], axis=0)
    sd = np.where(np.isfinite(sd) & (sd > 1e-6), sd, np.nan)

    best_z = np.zeros(n_pix, dtype="float64")
    best_k = np.full(n_pix, -1, dtype="int32")
    # Лучшая точка БЕЗ ограничения — нужна, чтобы отличить находку у
    # границы от пикселя, чьё изменение произошло за ней.
    best_z_free = np.zeros(n_pix, dtype="float64")
    best_k_free = np.full(n_pix, -1, dtype="int32")

    for k in range(min_segment, stop_full):
        n1 = count[k - 1].astype("float64")
        n2 = (total_n - count[k - 1]).astype("float64")
        with np.errstate(invalid="ignore", divide="ignore"):
            mean1 = cumsum[k - 1] / n1
            mean2 = (total_sum - cumsum[k - 1]) / n2
            se = sd * np.sqrt(1.0 / n1 + 1.0 / n2)
            z = np.abs(mean2 - mean1) / se
        z = np.where(np.isfinite(z) & (n1 >= min_segment) & (n2 >= min_segment), z, 0.0)

        better_free = z > best_z_free
        best_z_free = np.where(better_free, z, best_z_free)
        best_k_free = np.where(better_free, k, best_k_free)

        if k < stop:
            better = z > best_z
            best_z = np.where(better, z, best_z)
            best_k = np.where(better, k, best_k)

    if edge_guard and max_index is not None and stop < stop_full:
        wall = stop - 1
        margin = max(1, min_segment // 2)
        propped = (best_k >= wall - margin) & (best_k_free > int(max_index))
        best_k = np.where(propped, -1, best_k)
        best_z = np.where(propped, 0.0, best_z)

    return best_k, best_z.astype("float32")


# --------------------------------------------------------------------------- #
#  Шаги 3-4. Величина изменения и необратимость
# --------------------------------------------------------------------------- #


def _segment_mean(values: np.ndarray, start: np.ndarray, stop: np.ndarray) -> np.ndarray:
    """Среднее по [start, stop) для каждого пикселя со своими границами."""
    n_t = values.shape[0]
    idx = np.arange(n_t)[:, None]
    within = (idx >= start[None, :]) & (idx < stop[None, :])
    mask = within & np.isfinite(values)
    counts = mask.sum(axis=0)
    sums = np.where(mask, values, 0.0).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(counts > 0, sums / counts, np.nan)


def _segment_max(values: np.ndarray, start: np.ndarray, stop: np.ndarray) -> np.ndarray:
    n_t = values.shape[0]
    idx = np.arange(n_t)[:, None]
    within = (idx >= start[None, :]) & (idx < stop[None, :])
    masked = np.where(within & np.isfinite(values), values, -np.inf)
    out = masked.max(axis=0)
    return np.where(np.isfinite(out), out, np.nan)


def detect(
    ndvi: np.ndarray,
    bsi: np.ndarray,
    dates: np.ndarray,
    cfg: ChangeCfg,
    *,
    n_harmonics: int = 2,
) -> BreakpointResult:
    """Полная детекция необратимого изменения.

    ``ndvi`` и ``bsi`` — матрицы (T, N): T месячных композитов, N пикселей.
    ``dates`` — массив дат длины T.
    """
    if ndvi.shape != bsi.shape:
        raise ValueError(f"формы NDVI {ndvi.shape} и BSI {bsi.shape} не совпадают")
    n_t, n_pix = ndvi.shape
    if len(dates) != n_t:
        raise ValueError(f"дат {len(dates)}, а моментов ряда {n_t}")

    n_valid = np.isfinite(ndvi).sum(axis=0).astype("int32")

    # Шаг 1: сезонная модель. Ищем разрыв в остатках, а не в сыром ряде.
    design = harmonic_design(dates, n_harmonics=n_harmonics)
    residuals = deseasonalize(ndvi, design)

    # Шаг 2: точка разрыва.
    # Календарные месяцы из конфига переводятся в число наблюдений ряда:
    # в году 7 композитов, а не 12 (см. months_to_observations).
    min_segment_obs = months_to_observations(dates, cfg.min_segment_months)
    # Верхняя граница поиска: дальше по ряду не остаётся наблюдений, на
    # которых проверка необратимости могла бы что-то показать.
    last_observable = last_observable_index(dates, cfg.min_observed_after_months)
    break_index, zscore = find_breakpoint(residuals, min_segment_obs, max_index=last_observable)
    found = break_index >= 0

    zeros = np.zeros(n_pix, dtype="int32")
    k = np.where(found, break_index, 0).astype("int32")
    n_t_arr = np.full(n_pix, n_t, dtype="int32")

    # Шаг 3: величина изменения — по сырым индексам, не по остаткам:
    # именно абсолютное падение NDVI и рост BSI объясняются на защите.
    ndvi_before = _segment_mean(ndvi, zeros, k)
    ndvi_after = _segment_mean(ndvi, k, n_t_arr)
    bsi_before = _segment_mean(bsi, zeros, k)
    bsi_after = _segment_mean(bsi, k, n_t_arr)

    ndvi_drop = ndvi_before - ndvi_after
    bsi_rise = bsi_after - bsi_before

    # Шаг 4: необратимость. Если в окне восстановления NDVI поднялся
    # обратно хотя бы наполовину — это сезонное или временное нарушение
    # (засушливый год, разовая расчистка, пожар), а не свалка.
    # Окно отсчитывается по календарным датам, а не по числу шагов ряда.
    recovery_stop = recovery_window_stops(dates, k, cfg.recovery_window_months)
    ndvi_peak_after = _segment_max(ndvi, k, recovery_stop)
    recovery_level = ndvi_before - cfg.recovery_tolerance * ndvi_drop
    recovered = np.isfinite(ndvi_peak_after) & (ndvi_peak_after >= recovery_level)

    # Наблюдаемость гарантирована сужением области поиска выше; массив
    # остаётся в результате для отчётности и для случая, когда detect
    # вызывают с уже готовыми индексами разрыва.
    observable = observed_after_months(dates, k) >= cfg.min_observed_after_months

    has_break = (
        found
        & (zscore >= cfg.breakpoint_zscore)
        & (ndvi_drop >= cfg.min_ndvi_drop)
        & (bsi_rise >= cfg.min_bsi_rise)
        & ~recovered
        & observable
        & (n_valid >= _MIN_VALID_OBS)
    )

    result = BreakpointResult(
        has_break=has_break,
        break_index=np.where(has_break, break_index, -1).astype("int32"),
        zscore=zscore,
        ndvi_before=ndvi_before.astype("float32"),
        ndvi_after=ndvi_after.astype("float32"),
        ndvi_drop=ndvi_drop.astype("float32"),
        bsi_rise=bsi_rise.astype("float32"),
        recovered=recovered,
        n_valid=n_valid,
        observable=observable,
    )
    log.info("Детекция изменений: %s", result.summary())
    return result


def break_dates(result: BreakpointResult, dates: np.ndarray) -> np.ndarray:
    """Перевести индексы разрыва в даты (NaT там, где разрыва нет).

    Это то, что позволяет сказать на защите «эта свалка появилась в мае
    2021 года» — а такое утверждение производит впечатление сильнее,
    чем сам факт обнаружения.
    """
    out = np.full(len(result), np.datetime64("NaT"), dtype="datetime64[D]")
    valid = result.break_index >= 0
    out[valid] = np.asarray(dates, dtype="datetime64[D]")[result.break_index[valid]]
    return out


__all__ = [
    "BreakpointResult",
    "break_dates",
    "deseasonalize",
    "detect",
    "find_breakpoint",
    "harmonic_design",
    "last_observable_index",
    "months_to_observations",
    "observed_after_months",
    "recovery_window_stops",
]
