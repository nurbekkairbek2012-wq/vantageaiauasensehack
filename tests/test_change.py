"""Тесты детектора необратимых изменений.

Это самый важный тестовый файл в проекте. Детектор — то место, где решается,
свалка перед нами или нет, и именно его будут спрашивать на Q&A.

Проверяем не «функция вернула массив», а способность различать сценарии,
которые в реальности и дают ложные срабатывания:

    пашня      — сильная сезонность, но ничего не сломалось  → НЕ детект
    засуха     — NDVI просел и восстановился                 → НЕ детект
    пожар      — резкое падение, но трава отросла            → НЕ детект
    стабильная степь — ничего не происходит                  → НЕ детект
    СВАЛКА     — падение без восстановления, BSI вырос       → ДЕТЕКТ
"""

from __future__ import annotations

import numpy as np
import pytest

from vantage.change import (
    break_dates,
    deseasonalize,
    detect,
    find_breakpoint,
    harmonic_design,
    months_to_observations,
    recovery_window_stops,
)
from vantage.config import ChangeCfg

# Конфигурация, совпадающая с config/default.yaml
CFG = ChangeCfg(
    min_segment_months=6,
    min_ndvi_drop=0.12,
    min_bsi_rise=0.06,
    recovery_tolerance=0.5,
    recovery_window_months=18,
    breakpoint_zscore=3.0,
)

VALID_MONTHS = (4, 5, 6, 7, 8, 9, 10)
YEARS = range(2018, 2026)


def monthly_dates() -> np.ndarray:
    """Даты композитов: только пригодные месяцы, 7 наблюдений в году."""
    return np.array(
        [f"{y}-{m:02d}-15" for y in YEARS for m in VALID_MONTHS],
        dtype="datetime64[D]",
    )


def seasonal_ndvi(dates: np.ndarray, amplitude: float = 0.22, base: float = 0.34) -> np.ndarray:
    """Сезонный ход NDVI с максимумом в июле."""
    month = dates.astype("datetime64[M]").astype(int) % 12 + 1
    phase = 2.0 * np.pi * (month - 7) / 12.0
    return base + amplitude * np.cos(phase)


def make_pixel(
    dates: np.ndarray,
    *,
    scenario: str,
    break_at: int | None = None,
    rng: np.random.Generator | None = None,
    noise: float = 0.015,
) -> tuple[np.ndarray, np.ndarray]:
    """Сгенерировать пару рядов (ndvi, bsi) для одного сценария."""
    rng = rng or np.random.default_rng(0)
    n = dates.size
    ndvi = seasonal_ndvi(dates).copy()
    bsi = 0.10 - 0.35 * (ndvi - 0.34)  # BSI обратно связан с NDVI

    if scenario == "steppe":
        pass

    elif scenario == "farmland":
        # Пашня: та же сезонность, но с большей амплитудой и уборкой в августе
        ndvi = seasonal_ndvi(dates, amplitude=0.34, base=0.38)
        month = dates.astype("datetime64[M]").astype(int) % 12 + 1
        ndvi = np.where(month >= 9, ndvi - 0.22, ndvi)  # после уборки
        bsi = 0.10 - 0.35 * (ndvi - 0.34)

    elif scenario == "landfill":
        assert break_at is not None
        # После разрыва сезонность исчезает: голая поверхность из отходов
        ndvi[break_at:] = 0.08
        bsi[break_at:] = 0.30

    elif scenario == "drought":
        assert break_at is not None
        # Просадка на один сезон, затем полное восстановление
        ndvi[break_at : break_at + 4] -= 0.30
        bsi[break_at : break_at + 4] += 0.14

    elif scenario == "fire":
        assert break_at is not None
        # Резкое падение, трава отрастает за два сезона
        ndvi[break_at : break_at + 7] -= 0.28
        bsi[break_at : break_at + 7] += 0.15

    else:  # pragma: no cover
        raise ValueError(f"неизвестный сценарий: {scenario}")

    ndvi = ndvi + rng.normal(0, noise, n)
    bsi = bsi + rng.normal(0, noise, n)
    return ndvi.astype("float32"), bsi.astype("float32")


def build_matrix(scenarios: list[tuple[str, int | None]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    dates = monthly_dates()
    rng = np.random.default_rng(42)
    ndvi_cols, bsi_cols = [], []
    for scenario, break_at in scenarios:
        n, b = make_pixel(dates, scenario=scenario, break_at=break_at, rng=rng)
        ndvi_cols.append(n)
        bsi_cols.append(b)
    return np.column_stack(ndvi_cols), np.column_stack(bsi_cols), dates


# --------------------------------------------------------------------------- #


class TestUnitConversion:
    def test_months_convert_to_observations(self):
        """7 наблюдений в году, значит 12 месяцев ≈ 7 наблюдений, а не 12."""
        dates = monthly_dates()
        assert months_to_observations(dates, 12) == pytest.approx(7, abs=1)

    def test_recovery_window_respects_calendar(self):
        dates = monthly_dates()
        # Разрыв на индексе 14 (апрель 2020), окно 18 месяцев.
        # stop — граница ПОСЛЕ последнего попавшего наблюдения (как в срезах),
        # поэтому последнее наблюдение внутри окна — это stop-1.
        stops = recovery_window_stops(dates, np.array([14]), months=18)
        last_inside = dates[stops[0] - 1]
        first_outside = dates[stops[0]]
        assert (last_inside - dates[14]).astype(int) <= 548
        assert (first_outside - dates[14]).astype(int) > 548

    def test_window_is_clipped_at_series_end(self):
        """Разрыв в конце ряда не должен выводить индекс за границы массива."""
        dates = monthly_dates()
        stops = recovery_window_stops(dates, np.array([dates.size - 2]), months=18)
        assert stops[0] <= dates.size


class TestHarmonicModel:
    def test_design_matrix_shape(self):
        dates = monthly_dates()
        x = harmonic_design(dates, n_harmonics=2)
        assert x.shape == (dates.size, 6)  # 1 + тренд + 2 гармоники по 2 столбца

    def test_seasonality_is_removed(self):
        """После снятия сезонности остатки чистой пашни должны быть малы."""
        ndvi, _, dates = build_matrix([("farmland", None)])
        resid = deseasonalize(ndvi, harmonic_design(dates))
        assert np.nanstd(resid) < 0.10

    def test_break_survives_deseasonalization(self):
        """А вот разрыв сезонная модель убрать не должна."""
        ndvi, _, dates = build_matrix([("landfill", 25)])
        resid = deseasonalize(ndvi, harmonic_design(dates))
        assert np.nanstd(resid) > 0.05


class TestBreakpointSearch:
    def test_finds_break_near_true_position(self):
        ndvi, _, dates = build_matrix([("landfill", 28)])
        resid = deseasonalize(ndvi, harmonic_design(dates))
        idx, z = find_breakpoint(resid, min_segment=5)
        assert abs(int(idx[0]) - 28) <= 3
        assert z[0] > CFG.breakpoint_zscore

    def test_no_strong_break_in_stable_series(self):
        ndvi, _, dates = build_matrix([("steppe", None)])
        resid = deseasonalize(ndvi, harmonic_design(dates))
        _, z = find_breakpoint(resid, min_segment=5)
        assert z[0] < CFG.breakpoint_zscore

    def test_short_series_returns_no_break(self):
        resid = np.random.default_rng(0).normal(size=(8, 3)).astype("float32")
        idx, z = find_breakpoint(resid, min_segment=6)
        assert (idx == -1).all()
        assert (z == 0).all()


@pytest.fixture(scope="module")
def outcome():
    """Один прогон детектора на шести эталонных сценариях."""
    scenarios = [
        ("landfill", 28),    # 0 — должна быть найдена
        ("landfill", 20),    # 1 — должна быть найдена
        ("farmland", None),  # 2 — не должна
        ("steppe", None),    # 3 — не должна
        ("drought", 24),     # 4 — не должна: восстановилась
        ("fire", 22),        # 5 — не должна: трава отросла
    ]
    ndvi, bsi, dates = build_matrix(scenarios)
    return detect(ndvi, bsi, dates, CFG), dates


class TestScenarioSeparation:
    """Главный тест проекта: детектор обязан различать пять сценариев."""

    def test_landfills_detected(self, outcome):
        result, _ = outcome
        assert result.has_break[0], "свалка с разрывом на индексе 28 не найдена"
        assert result.has_break[1], "свалка с разрывом на индексе 20 не найдена"

    def test_farmland_rejected(self, outcome):
        result, _ = outcome
        assert not result.has_break[2], "пашня принята за свалку — сезонность не снята"

    def test_stable_steppe_rejected(self, outcome):
        result, _ = outcome
        assert not result.has_break[3]

    def test_drought_rejected_as_recovered(self, outcome):
        result, _ = outcome
        assert not result.has_break[4], "засуха принята за свалку"

    def test_fire_rejected_as_recovered(self, outcome):
        result, _ = outcome
        assert not result.has_break[5], "пожар принят за свалку"

    def test_detected_break_index_is_accurate(self, outcome):
        result, _ = outcome
        assert abs(int(result.break_index[0]) - 28) <= 3

    def test_metrics_have_expected_sign(self, outcome):
        result, _ = outcome
        assert result.ndvi_drop[0] >= CFG.min_ndvi_drop
        assert result.bsi_rise[0] >= CFG.min_bsi_rise

    def test_break_date_is_reported(self, outcome):
        result, dates = outcome
        dts = break_dates(result, dates)
        assert not np.isnat(dts[0])
        assert np.isnat(dts[2])  # у пашни разрыва нет
        # Разрыв на индексе 28 при 7 наблюдениях в году — это 2022 год
        assert dts[0].astype("datetime64[Y]").astype(int) + 1970 == 2022

    def test_summary_counts_are_consistent(self, outcome):
        result, _ = outcome
        summary = result.summary()
        assert summary["pixels"] == 6
        assert summary["detected"] == 2


class TestMissingData:
    def test_handles_gaps_without_crashing(self):
        ndvi, bsi, dates = build_matrix([("landfill", 28), ("steppe", None)])
        rng = np.random.default_rng(7)
        gaps = rng.random(ndvi.shape) < 0.25
        ndvi = np.where(gaps, np.nan, ndvi)
        bsi = np.where(gaps, np.nan, bsi)
        result = detect(ndvi, bsi, dates, CFG)
        assert result.has_break[0]
        assert not result.has_break[1]

    def test_all_nan_pixel_is_not_detected(self):
        ndvi, bsi, dates = build_matrix([("landfill", 28), ("steppe", None)])
        ndvi[:, 1] = np.nan
        bsi[:, 1] = np.nan
        result = detect(ndvi, bsi, dates, CFG)
        assert not result.has_break[1]
        assert result.n_valid[1] == 0

    def test_rejects_mismatched_shapes(self):
        ndvi, bsi, dates = build_matrix([("steppe", None)])
        with pytest.raises(ValueError):
            detect(ndvi, bsi[:, :0], dates, CFG)

    def test_rejects_mismatched_dates(self):
        ndvi, bsi, dates = build_matrix([("steppe", None)])
        with pytest.raises(ValueError):
            detect(ndvi, bsi, dates[:-3], CFG)
