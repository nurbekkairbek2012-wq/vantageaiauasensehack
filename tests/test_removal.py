"""Тесты контроля устранения.

Главный проверяемый сценарий — не «убрали», а **«засыпали грунтом»**.
Ложное «убрано» подрывает доверие акимата к системе быстрее, чем
отсутствие функции целиком: по такому объекту закрывается акт,
оплачивается работа, а отходы остаются на месте и продолжают
выделять метан.

Отличает эти два случая только тепловой признак: он единственный
видит сквозь присыпку.
"""

from __future__ import annotations

import numpy as np
import pytest

from vantage.config import RemovalCfg
from vantage.removal import (
    MIN_POST_OBSERVATIONS,
    REMOVAL_SIGNALS,
    assess_removal,
    bsi_normalized,
    count_consecutive,
    ndvi_recovered,
    needs_field_check,
    signal_disappeared,
    summarize,
)

CFG = RemovalCfg(
    min_agreeing_signals=2,
    consecutive_clear_passes=2,
    ndvi_recovery_threshold=0.75,
    bsi_still_high_threshold=0.10,
)

NDVI_BASELINE = 0.36
BSI_BASELINE = 0.06


def series(value: float, n: int = 8, noise: float = 0.01, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (np.full(n, value) + rng.normal(0, noise, n)).astype("float32")


# --------------------------------------------------------------------------- #
#  Отдельные признаки
# --------------------------------------------------------------------------- #


class TestNdviRecovery:
    def test_full_recovery_is_detected(self):
        recovered, level = ndvi_recovered(series(0.34), NDVI_BASELINE, CFG)
        assert recovered
        assert level == pytest.approx(0.94, abs=0.05)

    def test_still_bare_is_not_recovered(self):
        recovered, _ = ndvi_recovered(series(0.09), NDVI_BASELINE, CFG)
        assert not recovered

    def test_comparison_is_relative_to_baseline_not_absolute(self):
        """У степи baseline 0.36, у поливного луга 0.60.

        Единый абсолютный порог был бы бессмысленным для одного из них.
        """
        steppe, _ = ndvi_recovered(series(0.30), 0.36, CFG)
        meadow, _ = ndvi_recovered(series(0.30), 0.60, CFG)
        assert steppe
        assert not meadow

    def test_empty_series_is_not_recovery(self):
        recovered, level = ndvi_recovered(np.array([np.nan, np.nan]), NDVI_BASELINE, CFG)
        assert not recovered
        assert np.isnan(level)

    def test_zero_baseline_does_not_divide_by_zero(self):
        recovered, _ = ndvi_recovered(series(0.30), 0.0, CFG)
        assert not recovered


class TestBsiNormalization:
    def test_returned_to_background(self):
        normalized, excess = bsi_normalized(series(0.07), BSI_BASELINE, CFG)
        assert normalized
        assert abs(excess) < 0.05

    def test_still_mineral_surface(self):
        normalized, excess = bsi_normalized(series(0.28), BSI_BASELINE, CFG)
        assert not normalized
        assert excess > CFG.bsi_still_high_threshold


class TestSignalDisappearance:
    def test_signal_below_threshold_counts_as_gone(self):
        gone, level = signal_disappeared(series(0.02), threshold=0.05)
        assert gone
        assert level < 0.05

    def test_signal_above_threshold_remains(self):
        gone, _ = signal_disappeared(series(2.4), threshold=0.5)
        assert not gone

    def test_all_nan_is_not_disappearance(self):
        """Отсутствие данных — не доказательство отсутствия признака."""
        gone, level = signal_disappeared(np.full(5, np.nan), threshold=0.5)
        assert not gone
        assert np.isnan(level)


class TestConsecutiveCounting:
    def test_counts_from_the_end(self):
        """Важно, что признак держится СЕЙЧАС, а не держался два года назад."""
        assert count_consecutive(np.array([True, True, True, False, True, True])) == 2

    def test_all_true(self):
        assert count_consecutive(np.array([True, True, True])) == 3

    def test_interrupted_at_the_end(self):
        assert count_consecutive(np.array([True, True, False])) == 0

    def test_empty_series(self):
        assert count_consecutive(np.array([], dtype=bool)) == 0


# --------------------------------------------------------------------------- #
#  Сводное решение
# --------------------------------------------------------------------------- #


class TestRemovalScenarios:
    def test_cleared_site_is_recognised(self):
        """Расчищено: трава вернулась, грунт ушёл, тепло пропало."""
        result = assess_removal(
            "C1", CFG,
            ndvi_post=series(0.33),
            ndvi_baseline=NDVI_BASELINE,
            bsi_post=series(0.07),
            bsi_baseline=BSI_BASELINE,
            pmli_post=series(0.01),
            sar_incoherence_post=series(0.2),
            thermal_anomaly_post=series(0.1),
        )
        assert result.status == "possibly_removed"
        assert result.n_agreeing >= CFG.min_agreeing_signals
        assert result.confidence > 0.7

    def test_covered_with_soil_is_flagged_separately(self):
        """ГЛАВНЫЙ СЦЕНАРИЙ МОДУЛЯ.

        Растительность вернулась, поверхность выглядит чистой — но
        тепловая аномалия на месте. Органика под слоем грунта
        продолжает разлагаться. Формально объект выглядит убранным,
        по нему может быть закрыт акт и оплачена работа.
        """
        result = assess_removal(
            "C2", CFG,
            ndvi_post=series(0.33),
            ndvi_baseline=NDVI_BASELINE,
            bsi_post=series(0.07),
            bsi_baseline=BSI_BASELINE,
            pmli_post=series(0.01),
            sar_incoherence_post=series(0.2),
            thermal_anomaly_post=series(2.6),  # тепло осталось
        )
        assert result.status == "possibly_covered"
        assert any("засыпан" in w for w in result.warnings)
        assert "ЗАСЫПАН" in result.to_text()

    def test_active_landfill_is_not_marked_removed(self):
        result = assess_removal(
            "C3", CFG,
            ndvi_post=series(0.08),
            ndvi_baseline=NDVI_BASELINE,
            bsi_post=series(0.30),
            bsi_baseline=BSI_BASELINE,
            pmli_post=series(0.14),
            sar_incoherence_post=series(2.1),
            thermal_anomaly_post=series(2.4),
        )
        assert result.status == "active"
        assert result.confidence == 0.0

    def test_single_good_pass_is_not_enough(self):
        """Один удачный снимок — не доказательство.

        Облако ушло, освещение удачное, NDVI подскочил. Требование
        подтверждения на нескольких проходах подряд закрывает это.
        """
        ndvi = np.array([0.08, 0.09, 0.08, 0.09, 0.10, 0.34], dtype="float32")
        result = assess_removal(
            "C4", CFG,
            ndvi_post=ndvi,
            ndvi_baseline=NDVI_BASELINE,
            bsi_post=series(0.28),
            bsi_baseline=BSI_BASELINE,
            thermal_anomaly_post=series(2.2),
        )
        assert result.status == "active"
        assert result.consecutive_passes < CFG.consecutive_clear_passes

    def test_overgrown_but_not_cleared_is_caught_by_thermal(self):
        """Свалку не трогали — её затянуло бурьяном.

        NDVI вернулся, но тепло на месте. Это тот же класс ошибки,
        что и присыпка, и ловится тем же признаком.
        """
        result = assess_removal(
            "C5", CFG,
            ndvi_post=series(0.31),
            ndvi_baseline=NDVI_BASELINE,
            bsi_post=series(0.09),
            bsi_baseline=BSI_BASELINE,
            thermal_anomaly_post=series(3.1),
        )
        assert result.status == "possibly_covered"


class TestDataSufficiency:
    def test_too_few_observations_gives_no_verdict(self):
        result = assess_removal(
            "C6", CFG,
            ndvi_post=np.array([0.33, 0.34], dtype="float32"),
            ndvi_baseline=NDVI_BASELINE,
            bsi_post=np.array([0.07, 0.06], dtype="float32"),
            bsi_baseline=BSI_BASELINE,
        )
        assert result.status == "insufficient_data"
        assert result.confidence == 0.0
        assert f"минимум {MIN_POST_OBSERVATIONS}" in result.warnings[0]

    def test_nan_observations_do_not_count(self):
        ndvi = np.array([0.33, np.nan, np.nan, np.nan, np.nan], dtype="float32")
        result = assess_removal(
            "C7", CFG,
            ndvi_post=ndvi,
            ndvi_baseline=NDVI_BASELINE,
            bsi_post=series(0.07, n=5),
            bsi_baseline=BSI_BASELINE,
        )
        assert result.status == "insufficient_data"

    def test_missing_thermal_lowers_confidence_and_warns(self):
        """Без теплового признака вывоз и присыпку различить нельзя,
        и система обязана об этом сказать, а не молча уверять."""
        result = assess_removal(
            "C8", CFG,
            ndvi_post=series(0.33),
            ndvi_baseline=NDVI_BASELINE,
            bsi_post=series(0.07),
            bsi_baseline=BSI_BASELINE,
            pmli_post=series(0.01),
        )
        assert result.status == "possibly_removed"
        assert result.confidence <= 0.7
        assert any("присыпк" in w for w in result.warnings)


class TestNeverCertain:
    def test_confidence_never_reaches_one(self):
        """Полной уверенности без выезда не бывает."""
        result = assess_removal(
            "C9", CFG,
            ndvi_post=series(0.36, n=20),
            ndvi_baseline=NDVI_BASELINE,
            bsi_post=series(0.05, n=20),
            bsi_baseline=BSI_BASELINE,
            pmli_post=series(0.0, n=20),
            sar_incoherence_post=series(0.05, n=20),
            thermal_anomaly_post=series(0.0, n=20),
        )
        assert result.confidence <= 0.95

    def test_text_never_says_removed_flatly(self):
        result = assess_removal(
            "C10", CFG,
            ndvi_post=series(0.34),
            ndvi_baseline=NDVI_BASELINE,
            bsi_post=series(0.07),
            bsi_baseline=BSI_BASELINE,
            thermal_anomaly_post=series(0.1),
        )
        text = result.to_text()
        assert "вероятно" in text
        assert "уверенность" in text


class TestReporting:
    def _mixed(self):
        return [
            assess_removal(
                "A", CFG, ndvi_post=series(0.34), ndvi_baseline=NDVI_BASELINE,
                bsi_post=series(0.07), bsi_baseline=BSI_BASELINE,
                thermal_anomaly_post=series(0.1),
            ),
            assess_removal(
                "B", CFG, ndvi_post=series(0.33), ndvi_baseline=NDVI_BASELINE,
                bsi_post=series(0.07), bsi_baseline=BSI_BASELINE,
                thermal_anomaly_post=series(2.8),
            ),
            assess_removal(
                "C", CFG, ndvi_post=series(0.08), ndvi_baseline=NDVI_BASELINE,
                bsi_post=series(0.30), bsi_baseline=BSI_BASELINE,
                thermal_anomaly_post=series(2.5),
            ),
        ]

    def test_summary_counts_every_status(self):
        stats = summarize(self._mixed())
        assert stats["possibly_removed"] == 1
        assert stats["possibly_covered"] == 1
        assert stats["active"] == 1
        assert sum(stats.values()) == 3

    def test_field_check_queue_prioritises_suspected_cover(self):
        """Подозрение на присыпку проверяется раньше очевидных свалок:
        по такому объекту формально может быть закрыт акт."""
        assert needs_field_check(self._mixed()) == ["B"]


class TestSignalRegistry:
    def test_all_declared_signals_are_evaluated(self):
        result = assess_removal(
            "C11", CFG,
            ndvi_post=series(0.34), ndvi_baseline=NDVI_BASELINE,
            bsi_post=series(0.07), bsi_baseline=BSI_BASELINE,
            pmli_post=series(0.01),
            sar_incoherence_post=series(0.2),
            thermal_anomaly_post=series(0.1),
        )
        assert set(result.signals) == set(REMOVAL_SIGNALS)
