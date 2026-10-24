"""Тесты слоя объяснимости и применения модели.

Главный проверяемый тезис: свалку определяет СОГЛАСИЕ независимых
признаков, а не сила одного. Карьер даёт мощный рост BSI при молчании
остальных четырёх — и панель обязана это различать, иначе весь смысл
пятиканальной физики теряется.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import box

from vantage.explain import (
    SIGNAL_FULL_SCALE,
    disagreement,
    evidence_table,
    physical_evidence,
    summarize_attribution,
)

UTM = "EPSG:32642"


# --------------------------------------------------------------------------- #
#  Физическая панель (без модели, без torch)
# --------------------------------------------------------------------------- #


class TestPhysicalEvidence:
    def test_full_signal_gives_full_strength(self):
        ev = physical_evidence("C1", ndvi_drop=SIGNAL_FULL_SCALE["ndvi_drop"])
        assert ev.strength["ndvi_drop"] == pytest.approx(1.0)

    def test_strength_saturates(self):
        """Убрать растительность дважды нельзя — шкала обязана насыщаться."""
        ev = physical_evidence("C1", ndvi_drop=10.0)
        assert ev.strength["ndvi_drop"] == 1.0

    def test_negative_signal_gives_zero(self):
        ev = physical_evidence("C1", ndvi_drop=-0.2)
        assert ev.strength["ndvi_drop"] == 0.0

    def test_landfill_has_agreement_across_signals(self):
        ev = physical_evidence(
            "C1",
            ndvi_drop=0.30,
            bsi_rise=0.20,
            pmli_response=0.12,
            sar_incoherence=2.4,  # дБ прироста дисперсии; полная шкала — 3 дБ
            thermal_anomaly=2.5,
        )
        assert ev.n_agreeing == 5
        assert ev.combined_score > 0.7

    def test_quarry_pattern_is_distinguished_from_landfill(self):
        """Карьер: BSI взлетает, но полимеров нет и тепла нет.

        Именно на этом различии держится весь смысл пяти независимых
        признаков вместо одного.
        """
        quarry = physical_evidence(
            "Q1",
            ndvi_drop=0.30,
            bsi_rise=0.25,
            pmli_response=0.0,
            sar_incoherence=0.05,
            thermal_anomaly=0.0,
        )
        landfill = physical_evidence(
            "L1",
            ndvi_drop=0.30,
            bsi_rise=0.20,
            pmli_response=0.12,
            sar_incoherence=2.4,  # дБ прироста дисперсии; полная шкала — 3 дБ
            thermal_anomaly=2.5,
        )
        assert quarry.combined_score < landfill.combined_score
        assert quarry.n_agreeing < landfill.n_agreeing

    def test_single_strong_signal_does_not_dominate(self):
        """Один максимальный признак при молчании остальных не должен
        давать высокую оценку — иначе теряется идея согласия."""
        ev = physical_evidence(
            "C1", ndvi_drop=1.0, bsi_rise=0.0, pmli_response=0.0,
            sar_incoherence=0.0, thermal_anomaly=0.0,
        )
        assert ev.combined_score <= 0.25

    def test_missing_signals_do_not_zero_the_score(self):
        """Радар и тепло — отдельные ветки пайплайна; их отсутствие
        не должно обнулять оценку, только сузить основание."""
        partial = physical_evidence("C1", ndvi_drop=0.35, bsi_rise=0.25)
        assert partial.combined_score == pytest.approx(1.0)

    def test_top_signals_are_sorted(self):
        ev = physical_evidence("C1", ndvi_drop=0.10, bsi_rise=0.25, pmli_response=0.02)
        names = [name for name, _ in ev.top_signals(2)]
        assert names[0] == "bsi_rise"

    def test_text_is_human_readable(self):
        ev = physical_evidence("C1", ndvi_drop=0.30, bsi_rise=0.20, pmli_response=0.12)
        text = ev.to_text()
        assert "сработало признаков" in text
        assert "растительн" in text

    def test_empty_evidence_says_so(self):
        assert "не выражен" in physical_evidence("C1").to_text()


class TestEvidenceTable:
    def _candidates(self):
        return gpd.GeoDataFrame(
            {
                "candidate_id": ["C00000", "C00001"],
                "ndvi_drop": [0.30, 0.05],
                "bsi_rise": [0.20, 0.01],
                "geometry": [box(0, 0, 10, 10), box(20, 20, 30, 30)],
            },
            crs=UTM,
        )

    def test_builds_one_record_per_candidate(self):
        table = evidence_table(self._candidates())
        assert len(table) == 2
        assert table[0].candidate_id == "C00000"

    def test_strong_candidate_scores_higher(self):
        table = evidence_table(self._candidates())
        assert table[0].combined_score > table[1].combined_score

    def test_missing_columns_are_tolerated(self):
        """Колонок радара и тепла может ещё не быть — падать нельзя."""
        table = evidence_table(self._candidates())
        assert np.isnan(table[0].signals["thermal_anomaly"])


class TestDisagreement:
    def test_flags_model_physics_mismatch(self):
        strong_physics = physical_evidence("A", ndvi_drop=0.35, bsi_rise=0.25, probability=0.05)
        agreeing = physical_evidence("B", ndvi_drop=0.35, bsi_rise=0.25, probability=0.95)
        flagged = disagreement([strong_physics, agreeing])
        assert flagged == ["A"]

    def test_ignores_candidates_without_model_score(self):
        assert disagreement([physical_evidence("A", ndvi_drop=0.35)]) == []


class TestAttributionSummary:
    def test_averages_per_channel(self):
        attribution = {"ndvi": np.array([0.4, 0.6]), "B02": np.array([0.01, -0.01])}
        summary = summarize_attribution(attribution)
        assert summary["ndvi"] == pytest.approx(0.5)
        assert abs(summary["B02"]) < 0.01


# --------------------------------------------------------------------------- #
#  Вклад каналов (нужен torch)
# --------------------------------------------------------------------------- #

torch = pytest.importorskip("torch", reason="нужен PyTorch (pip install -e .[ml])")

from vantage.chips import ChipDataset  # noqa: E402
from vantage.config import ModelCfg  # noqa: E402
from vantage.explain import channel_attribution  # noqa: E402
from vantage.model.infer import Prediction, attach_to_candidates, predict  # noqa: E402
from vantage.model.train import train  # noqa: E402

N_CHANNELS = 4
CHIP = 16

CFG = ModelCfg(
    backbone="custom", pretrained_source="none", embedding_dim=32, dropout=0.1,
    batch_size=16, epochs=10, lr=3e-3, weight_decay=1e-4,
    early_stopping_patience=5, decision_threshold=0.65, val_fraction=0.25, seed=42,
)


def make_dataset(n: int = 100, seed: int = 0) -> ChipDataset:
    """Сигнал живёт ТОЛЬКО в канале ndvi (индекс 2). Остальные — шум."""
    rng = np.random.default_rng(seed)
    labels = (rng.random(n) < 0.4).astype(int)
    before = rng.normal(0, 0.3, (n, N_CHANNELS, CHIP, CHIP)).astype("float32")
    after = before + rng.normal(0, 0.05, before.shape).astype("float32")
    after[labels == 1, 2] -= 2.0
    return ChipDataset(
        before=before, after=after,
        candidate_ids=[f"C{i:05d}" for i in range(n)],
        channels=["B02", "B04", "ndvi", "bsi"],
        labels=labels,
    )


@pytest.fixture(scope="module")
def trained_model():
    return train(make_dataset(), CFG, progress=False)


class TestInference:
    def test_predicts_for_every_candidate(self, trained_model):
        dataset = make_dataset(30, seed=5)
        result = predict(trained_model, dataset)
        assert len(result) == 30
        assert np.all((result.probability >= 0) & (result.probability <= 1))

    def test_separates_classes(self, trained_model):
        dataset = make_dataset(60, seed=7)
        result = predict(trained_model, dataset)
        positive = result.probability[dataset.labels == 1].mean()
        negative = result.probability[dataset.labels == 0].mean()
        assert positive > negative + 0.2

    def test_rejects_wrong_channel_order(self, trained_model):
        """Перепутанный порядок каналов не упадёт сам — он молча выдаст
        мусор. Поэтому проверка обязана быть жёсткой."""
        dataset = make_dataset(10)
        dataset.channels = ["ndvi", "B02", "B04", "bsi"]
        with pytest.raises(ValueError, match="каналов"):
            predict(trained_model, dataset)

    def test_top_k_returns_most_confident(self, trained_model):
        result = predict(trained_model, make_dataset(40, seed=9))
        top = result.top_k(5)
        assert len(top) == 5
        assert top[0][1] >= top[-1][1]

    def test_attaches_to_candidate_table(self, trained_model):
        dataset = make_dataset(5, seed=11)
        result = predict(trained_model, dataset)
        candidates = gpd.GeoDataFrame(
            {
                "candidate_id": dataset.candidate_ids,
                "geometry": [box(i, i, i + 5, i + 5) for i in range(5)],
            },
            crs=UTM,
        )
        merged = attach_to_candidates(candidates, result)
        assert "probability" in merged.columns
        assert merged["probability"].notna().all()

    def test_missing_candidate_is_marked_not_dropped(self, trained_model):
        result = Prediction(
            candidate_ids=["C00000"],
            probability=np.array([0.9]),
            is_landfill=np.array([True]),
            threshold=0.5,
        )
        candidates = gpd.GeoDataFrame(
            {"candidate_id": ["C00000", "C99999"], "geometry": [box(0, 0, 1, 1), box(2, 2, 3, 3)]},
            crs=UTM,
        )
        merged = attach_to_candidates(candidates, result)
        assert len(merged) == 2
        assert bool(merged["is_landfill"].iloc[1]) is False


class TestChannelAttribution:
    def test_finds_the_informative_channel(self, trained_model):
        """Сигнал был только в канале ndvi — окклюзия обязана это показать.

        Если бы главным оказался синий канал, это означало бы, что модель
        зацепилась за артефакт атмосферной коррекции, а не за физику.
        """
        attribution = channel_attribution(trained_model, make_dataset(40, seed=13))
        summary = summarize_attribution(attribution)
        assert max(summary, key=summary.get) == "ndvi"

    def test_returns_value_per_candidate(self, trained_model):
        dataset = make_dataset(20, seed=15)
        attribution = channel_attribution(trained_model, dataset)
        assert set(attribution) == set(dataset.channels)
        for values in attribution.values():
            assert values.shape == (20,)


class TestAttachEvidence:
    """Согласие признаков как отдельная оценка рядом с вероятностью модели.

    Понадобилось после первого настоящего прогона. Положительные примеры
    для сети берутся из полигонов ТБО в OpenStreetMap, но внутри
    существующего полигона детектор изменений не находит ничего: там и в
    2018 году была голая поверхность, разрыва нет. Ноль положительных
    меток — значит, ``probability`` остаётся пустой, и колонку
    уверенности на карте нечем заполнить.
    """

    def _frame(self, **columns):
        import geopandas as gpd
        from shapely.geometry import box

        n = len(next(iter(columns.values())))
        return gpd.GeoDataFrame(
            {
                "candidate_id": [f"C{i:05d}" for i in range(n)],
                **columns,
                "geometry": [box(i, i, i + 1, i + 1) for i in range(n)],
            },
            crs="EPSG:32642",
        )

    def test_adds_score_and_agreement(self):
        from vantage.explain import attach_evidence

        result = attach_evidence(self._frame(ndvi_drop=[0.35], bsi_rise=[0.25]))
        assert result["evidence_score"].iat[0] > 0.9
        assert result["n_agreeing"].iat[0] == 2

    def test_quarry_scores_lower_than_landfill(self):
        """Тот же смысл, что и у пяти признаков: решает согласие, не сила."""
        from vantage.explain import attach_evidence

        result = attach_evidence(
            self._frame(
                ndvi_drop=[0.30, 0.30],
                bsi_rise=[0.20, 0.25],
                pmli_response=[0.12, 0.0],
                sar_incoherence=[2.4, 0.1],
                thermal_anomaly=[2.5, 0.0],
            )
        )
        landfill, quarry = result["evidence_score"]
        assert landfill > quarry
        assert result["n_agreeing"].iat[0] > result["n_agreeing"].iat[1]

    def test_missing_signals_do_not_zero_the_score(self):
        """Непосчитанный признак — это отсутствие данных, а не нулевая сила.

        Радар и тепло считаются отдельной веткой и могут быть недоступны.
        Если бы они обнуляли согласие, объект с двумя сильными оптическими
        признаками выглядел бы хуже, чем он есть.
        """
        from vantage.explain import attach_evidence

        both = attach_evidence(self._frame(ndvi_drop=[0.35], bsi_rise=[0.25]))
        assert both["evidence_score"].iat[0] > 0.9

    def test_empty_input_gets_the_columns_anyway(self):
        import geopandas as gpd

        from vantage.explain import attach_evidence

        empty = gpd.GeoDataFrame(
            {"candidate_id": [], "geometry": []}, geometry="geometry", crs="EPSG:32642"
        )
        result = attach_evidence(empty)
        assert "evidence_score" in result.columns
        assert "n_agreeing" in result.columns
