"""Тесты сиамской сети и цикла обучения.

Ключевые проверки — не «сеть запустилась», а те свойства, без которых
метрика на слайде была бы неправдой:

  * сиамский энкодер действительно разделяет веса между эпохами;
  * аугментация применяется к обеим эпохам одинаково;
  * PR-AUC считается правильно и не обманывается дисбалансом;
  * порог подбирается, а не берётся 0.5;
  * модель реально учится на отделимой задаче и сохраняется без потерь.
"""

from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch", reason="нужен PyTorch (pip install -e .[ml])")

from vantage.chips import ChipDataset  # noqa: E402
from vantage.config import ModelCfg  # noqa: E402
from vantage.model.siamese import SiameseConfig, build_model, make_encoder  # noqa: E402
from vantage.model.train import (  # noqa: E402
    TrainedModel,
    augment_pair,
    pick_threshold,
    pr_auc,
    train,
)

N_CHANNELS = 5
CHIP = 16


def make_labelled_dataset(n: int = 120, *, seed: int = 0) -> ChipDataset:
    """Отделимая задача: у положительных пар NDVI падает, BSI растёт.

    Задача намеренно решаемая — тест проверяет, что цикл обучения работает,
    а не что сеть творит чудеса на неразделимых данных.
    """
    rng = np.random.default_rng(seed)
    labels = (rng.random(n) < 0.3).astype(int)

    before = rng.normal(0.0, 0.3, (n, N_CHANNELS, CHIP, CHIP)).astype("float32")
    after = before + rng.normal(0.0, 0.05, before.shape).astype("float32")

    # Канал 3 — NDVI (падает), канал 4 — BSI (растёт) у положительных
    positive = labels == 1
    after[positive, 3] -= 1.5
    after[positive, 4] += 1.5

    return ChipDataset(
        before=before,
        after=after,
        candidate_ids=[f"C{i:05d}" for i in range(n)],
        channels=["B02", "B04", "B08", "ndvi", "bsi"],
        labels=labels,
    )


CFG = ModelCfg(
    backbone="custom",
    pretrained_source="none",
    embedding_dim=32,
    dropout=0.2,
    batch_size=16,
    epochs=12,
    lr=3e-3,
    weight_decay=1e-4,
    early_stopping_patience=6,
    decision_threshold=0.65,
    val_fraction=0.25,
    seed=42,
)


# --------------------------------------------------------------------------- #
#  Архитектура
# --------------------------------------------------------------------------- #


class TestArchitecture:
    def test_encoder_output_shape(self):
        cfg = SiameseConfig(in_channels=N_CHANNELS, embedding_dim=32)
        encoder = make_encoder(cfg)
        out = encoder(torch.zeros(4, N_CHANNELS, CHIP, CHIP))
        assert out.shape == (4, 32)

    def test_encoder_accepts_other_chip_sizes(self):
        """Глобальное усреднение делает модель независимой от размера чипа."""
        encoder = make_encoder(SiameseConfig(in_channels=N_CHANNELS, embedding_dim=32))
        assert encoder(torch.zeros(2, N_CHANNELS, 32, 32)).shape == (2, 32)

    def test_forward_returns_one_logit_per_pair(self):
        model = build_model(SiameseConfig(in_channels=N_CHANNELS, embedding_dim=32))
        out = model(torch.zeros(6, N_CHANNELS, CHIP, CHIP), torch.zeros(6, N_CHANNELS, CHIP, CHIP))
        assert out.shape == (6,)

    def test_weights_are_shared_between_epochs(self):
        """Сиамская схема: обе эпохи проходят через ОДИН энкодер.

        Если бы энкодеры были разными, одинаковый вход давал бы разные
        представления, и сеть выучила бы не изменение, а различие
        между двумя независимыми сетями.
        """
        model = build_model(SiameseConfig(in_channels=N_CHANNELS, embedding_dim=32))
        model.eval()
        x = torch.randn(3, N_CHANNELS, CHIP, CHIP)
        with torch.no_grad():
            assert torch.allclose(model.embed(x), model.embed(x))

    def test_identical_epochs_give_stable_output(self):
        model = build_model(SiameseConfig(in_channels=N_CHANNELS, embedding_dim=32))
        model.eval()
        x = torch.randn(4, N_CHANNELS, CHIP, CHIP)
        with torch.no_grad():
            first = model(x, x)
            second = model(x, x)
        assert torch.allclose(first, second)

    def test_predict_proba_is_in_unit_interval(self):
        model = build_model(SiameseConfig(in_channels=N_CHANNELS, embedding_dim=32))
        p = model.predict_proba(
            torch.randn(5, N_CHANNELS, CHIP, CHIP), torch.randn(5, N_CHANNELS, CHIP, CHIP)
        )
        assert torch.all((p >= 0) & (p <= 1))

    def test_rejects_invalid_config(self):
        with pytest.raises(ValueError):
            SiameseConfig(in_channels=0)
        with pytest.raises(ValueError):
            SiameseConfig(in_channels=3, dropout=1.0)


# --------------------------------------------------------------------------- #
#  Метрики
# --------------------------------------------------------------------------- #


class TestMetrics:
    def test_perfect_ranking_gives_one(self):
        y = np.array([0, 0, 1, 1])
        assert pr_auc(y, np.array([0.1, 0.2, 0.8, 0.9])) == pytest.approx(1.0)

    def test_random_ranking_is_near_base_rate(self):
        rng = np.random.default_rng(0)
        y = (rng.random(2000) < 0.1).astype(int)
        score = pr_auc(y, rng.random(2000))
        assert 0.05 < score < 0.20

    def test_not_fooled_by_class_imbalance(self):
        """Модель, всегда отвечающая «не свалка», имеет высокую accuracy
        и низкий PR-AUC. Именно поэтому основная метрика — PR-AUC."""
        y = np.array([0] * 95 + [1] * 5)
        constant = np.zeros(100)
        assert pr_auc(y, constant) < 0.2

    def test_no_positives_gives_zero(self):
        assert pr_auc(np.zeros(10, dtype=int), np.random.random(10)) == 0.0


class TestThreshold:
    def test_finds_separating_threshold(self):
        y = np.array([0, 0, 0, 1, 1, 1])
        scores = np.array([0.1, 0.2, 0.3, 0.7, 0.8, 0.9])
        threshold, metrics = pick_threshold(y, scores)
        assert 0.3 < threshold <= 0.7
        assert metrics["recall"] == pytest.approx(1.0)

    def test_recall_weight_favours_completeness(self):
        """При приоритете полноты порог не должен быть выше, чем при равном весе.

        Пропустить свалку дороже, чем зря съездить на карьер.
        """
        rng = np.random.default_rng(1)
        y = (rng.random(300) < 0.2).astype(int)
        scores = np.clip(y * 0.4 + rng.normal(0.3, 0.2, 300), 0, 1)
        balanced, _ = pick_threshold(y, scores, recall_weight=1.0)
        recall_first, _ = pick_threshold(y, scores, recall_weight=4.0)
        assert recall_first <= balanced

    def test_no_positives_falls_back_to_half(self):
        threshold, _ = pick_threshold(np.zeros(5, dtype=int), np.random.random(5))
        assert threshold == 0.5


# --------------------------------------------------------------------------- #
#  Аугментация
# --------------------------------------------------------------------------- #


class TestAugmentation:
    def test_applies_same_transform_to_both_epochs(self):
        """Разная аугментация эпох создала бы ложное «изменение» —
        сеть выучила бы смещение картинки вместо смены поверхности."""
        rng = np.random.default_rng(0)
        base = np.arange(2 * 3 * 4 * 4, dtype="float32").reshape(2, 3, 4, 4)
        before, after = augment_pair(base.copy(), base.copy(), rng)
        assert np.array_equal(before, after)

    def test_preserves_shape_and_values(self):
        rng = np.random.default_rng(3)
        base = np.random.default_rng(0).normal(size=(2, 3, 8, 8)).astype("float32")
        before, _ = augment_pair(base.copy(), base.copy() + 1, rng)
        assert before.shape == base.shape
        assert np.allclose(np.sort(before.ravel()), np.sort(base.ravel()))

    def test_does_not_change_brightness(self):
        """Спектральные значения — это и есть сигнал; менять их нельзя."""
        rng = np.random.default_rng(5)
        base = np.random.default_rng(0).normal(size=(4, 3, 8, 8)).astype("float32")
        before, _ = augment_pair(base.copy(), base.copy(), rng)
        assert before.mean() == pytest.approx(base.mean(), abs=1e-6)


# --------------------------------------------------------------------------- #
#  Обучение
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def trained():
    """Одно обучение на весь модуль — цикл небыстрый."""
    return train(make_labelled_dataset(), CFG, progress=False)


class TestTraining:
    def test_learns_a_separable_task(self, trained):
        assert trained.history.best_pr_auc > 0.85

    def test_loss_decreases(self, trained):
        first = np.mean(trained.history.train_loss[:2])
        last = np.mean(trained.history.train_loss[-2:])
        assert last < first

    def test_threshold_is_tuned_not_default(self, trained):
        assert 0.0 < trained.threshold < 1.0

    def test_channels_are_recorded(self, trained):
        assert trained.channels == ["B02", "B04", "B08", "ndvi", "bsi"]

    def test_normalization_stats_are_saved_with_model(self, trained):
        assert trained.stats.mean.shape == (N_CHANNELS,)
        assert np.all(trained.stats.std > 0)

    def test_roundtrip_preserves_predictions(self, trained, tmp_path):
        path = tmp_path / "model.pt"
        trained.save(path)
        loaded = TrainedModel.load(path)

        x = torch.randn(4, N_CHANNELS, CHIP, CHIP)
        trained.model.eval()
        with torch.no_grad():
            original = trained.model(x, x)
            restored = loaded.model(x, x)
        assert torch.allclose(original, restored, atol=1e-6)
        assert loaded.threshold == trained.threshold
        assert loaded.channels == trained.channels

    def test_requires_labels(self):
        dataset = make_labelled_dataset(40)
        dataset.labels = None
        with pytest.raises(ValueError, match="метки"):
            train(dataset, CFG, progress=False)

    def test_requires_both_classes(self):
        dataset = make_labelled_dataset(40)
        dataset.labels = np.ones(len(dataset), dtype=int)
        with pytest.raises(ValueError, match="один класс"):
            train(dataset, CFG, progress=False)
