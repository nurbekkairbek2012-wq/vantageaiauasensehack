"""Тесты на наблюдаемость разрыва — почему конец ряда нельзя объявлять свалкой.

Ошибка, которую ловят эти тесты, была найдена не рассуждением, а первым
настоящим прогоном по плитке 22 км². Детектор вернул 29 кандидатов, и у
25 из них дата разрыва оказалась одна и та же — апрель 2026, последний
месяц периода.

Причина. Признак свалки — необратимость: NDVI упал и не вернулся. Проверка
смотрит в окно 18 месяцев после разрыва. Если разрыв найден за три месяца
до конца ряда, окно пустое, возвращаться некуда, и проверка честно
отвечает «не восстановился» — по любому весеннему падению растительности.

Лечение двухступенчатое, и вторая ступень не менее важна первой:

1. разрывы позже ``min_observed_after_months`` до конца ряда не годятся;
2. это ограничение накладывается **на область поиска**, а не на результат.

Второе выяснилось сразу после первого. Разрыв ищется как максимум
статистики по всем допустимым точкам. Если у пикселя есть настоящий разрыв
2021 года и шумовой конкурент на хвосте, максимум уходит на хвост — и
отбраковка постфактум выбрасывает пиксель целиком вместе с настоящей
находкой. На той же плитке так терялся объект с падением NDVI 0.49 → 0.09.
"""

from __future__ import annotations

import numpy as np

from vantage.change import detect, find_breakpoint, last_observable_index
from vantage.config import ChangeCfg

VALID_MONTHS = (4, 5, 6, 7, 8, 9, 10)
YEARS = range(2018, 2026)


def monthly_dates() -> np.ndarray:
    return np.array(
        [f"{y}-{m:02d}-15" for y in YEARS for m in VALID_MONTHS], dtype="datetime64[D]"
    )


def config(*, min_observed_after_months: int) -> ChangeCfg:
    return ChangeCfg(
        min_segment_months=6,
        min_ndvi_drop=0.12,
        min_bsi_rise=0.06,
        recovery_tolerance=0.5,
        recovery_window_months=18,
        breakpoint_zscore=3.0,
        min_observed_after_months=min_observed_after_months,
    )


def seasonal(dates: np.ndarray, amplitude: float = 0.22, base: float = 0.34) -> np.ndarray:
    month = dates.astype("datetime64[M]").astype(int) % 12 + 1
    return base + amplitude * np.cos(2.0 * np.pi * (month - 7) / 12.0)


def landfill_pixel(dates: np.ndarray, break_at: int, rng: np.random.Generator):
    """Ряд со свалкой: после разрыва сезонность исчезает окончательно."""
    ndvi = seasonal(dates).copy()
    bsi = 0.10 - 0.35 * (ndvi - 0.34)
    ndvi[break_at:] = 0.08
    bsi[break_at:] = 0.30
    noise = rng.normal(0, 0.015, dates.size)
    return (ndvi + noise).astype("float32"), (bsi + noise).astype("float32")


class TestSearchWindow:
    def test_limit_is_none_when_disabled(self):
        assert last_observable_index(monthly_dates(), 0) is None

    def test_limit_leaves_required_window(self):
        dates = monthly_dates()
        limit = last_observable_index(dates, 18)
        assert limit is not None
        # После найденной точки в ряду ещё не меньше 18 месяцев.
        remaining_days = (dates[-1] - dates[limit]).astype(int)
        assert remaining_days >= 18 * 30
        # А после следующей — уже меньше: граница выбрана самой поздней.
        assert (dates[-1] - dates[limit + 1]).astype(int) < 18 * 30

    def test_series_shorter_than_window_finds_nothing(self):
        short = monthly_dates()[:7]
        assert last_observable_index(short, 18) == -1

    def test_search_respects_max_index(self):
        """Область поиска сужается, и упор в стенку не выдаётся за находку.

        Раньше здесь проверялось обратное: что ограниченный поиск вернёт
        какую-нибудь точку внутри разрешённой области. Проверка закрепляла
        поломку. Ограничение не убирает пиксель, чьё изменение произошло
        позже предела, — оно переименовывает его, приписывая дату границы.
        На настоящем прогоне это дало 21 объект ровно на последнем
        допустимом индексе и всплеск свалок в календаре, которого не было.

        Разрыв на 50-м отсчёте при пределе 40 наблюдать нечем. Честный
        ответ — -1, а не «разрыв на сороковом».
        """
        rng = np.random.default_rng(3)
        residuals = rng.normal(0, 0.02, (61, 1)).astype("float32")
        residuals[50:, 0] -= 0.4  # сильный разрыв на хвосте

        free, _ = find_breakpoint(residuals, 4)
        limited, _ = find_breakpoint(residuals, 4, max_index=40)

        assert free[0] >= 45
        assert limited[0] == -1

    def test_real_break_survives_a_later_competitor(self):
        """Находка в середине ряда не приносится в жертву защите от стенки.

        Ради этого сужение области поиска и делалось: у пикселя с настоящим
        разрывом в середине может оказаться конкурент на хвосте, и отбраковка
        постфактум выбросила бы пиксель целиком. Защита от стенки срабатывает
        только когда лучшая внутренняя точка стоит вплотную к границе.
        """
        rng = np.random.default_rng(7)
        residuals = rng.normal(0, 0.02, (61, 1)).astype("float32")
        residuals[20:, 0] -= 0.5   # настоящий разрыв в середине
        residuals[52:, 0] -= 0.2   # конкурент за пределом наблюдаемости

        limited, z = find_breakpoint(residuals, 4, max_index=40)
        assert 15 <= limited[0] <= 25, "разрыв середины ряда обязан уцелеть"
        assert z[0] > 0

    def test_guard_can_be_switched_off(self):
        """Отключаемо: поведение до исправления нужно для сравнения."""
        rng = np.random.default_rng(3)
        residuals = rng.normal(0, 0.02, (61, 1)).astype("float32")
        residuals[50:, 0] -= 0.4

        without, _ = find_breakpoint(residuals, 4, max_index=40, edge_guard=False)
        assert 0 <= without[0] <= 40


class TestTailBreaksAreRejected:
    def test_break_at_the_very_end_is_not_detected(self):
        """Тот самый «апрель 2026»: проверять необратимость нечем.

        Разрыв ставится за восемь наблюдений до конца — это около года,
        то есть меньше окна восстановления, но ещё внутри допустимой
        области поиска. Ближе к концу ряда его не нашёл бы и детектор
        без ограничения: там не хватает длины сегмента.
        """
        dates = monthly_dates()
        rng = np.random.default_rng(11)
        ndvi, bsi = landfill_pixel(dates, dates.size - 8, rng)

        result = detect(ndvi[:, None], bsi[:, None], dates, config(min_observed_after_months=18))
        assert not result.has_break[0]

    def test_without_the_gate_the_same_pixel_is_detected(self):
        """Контрольный: без ограничения детектор действительно срабатывает.

        Без этой проверки предыдущий тест мог бы проходить по любой другой
        причине, и защита от регрессии оказалась бы мнимой.
        """
        dates = monthly_dates()
        rng = np.random.default_rng(11)
        ndvi, bsi = landfill_pixel(dates, dates.size - 8, rng)

        result = detect(ndvi[:, None], bsi[:, None], dates, config(min_observed_after_months=0))
        assert result.has_break[0]


class TestRealBreaksSurvive:
    def test_mid_series_break_is_still_found(self):
        dates = monthly_dates()
        rng = np.random.default_rng(7)
        ndvi, bsi = landfill_pixel(dates, 21, rng)

        result = detect(ndvi[:, None], bsi[:, None], dates, config(min_observed_after_months=18))
        assert result.has_break[0]
        assert abs(int(result.break_index[0]) - 21) <= 2

    def test_real_break_is_not_lost_to_a_tail_competitor(self):
        """ГЛАВНАЯ ПРОВЕРКА ФАЙЛА.

        У пикселя два разрыва: настоящий в середине ряда и более сильный
        на хвосте. Без ограничения области поиска максимум статистики
        уходит на хвост — и отбраковка результата постфактум выбросила бы
        пиксель целиком, вместе с настоящей находкой. Сужение области
        поиска возвращает середину.

        Проверка идёт на уровне поиска, а не всего детектора: именно там
        живёт разница между двумя способами наложить ограничение, и
        только так видно, что теряется, а что остаётся.
        """
        rng = np.random.default_rng(1)
        residuals = rng.normal(0, 0.02, (56, 1)).astype("float32")
        residuals[21:, 0] -= 0.20      # настоящий разрыв в середине ряда
        residuals[49:53, 0] -= 0.70    # более сильная просадка на хвосте

        free, _ = find_breakpoint(residuals, 4)
        limited, limited_z = find_breakpoint(residuals, 4, max_index=42)

        assert free[0] >= 43, "без ограничения максимум уходит на хвост"
        # Отбраковка постфактум увидела бы free[0] за границей и выбросила
        # бы пиксель целиком. Сужение области поиска возвращает середину.
        assert 18 <= limited[0] <= 24
        assert limited_z[0] > 3.0, "найденный разрыв остаётся значимым"

    def test_observable_flag_is_reported(self):
        dates = monthly_dates()
        rng = np.random.default_rng(2)
        ndvi, bsi = landfill_pixel(dates, 21, rng)

        result = detect(ndvi[:, None], bsi[:, None], dates, config(min_observed_after_months=18))
        assert result.observable is not None
        assert bool(result.observable[0])
