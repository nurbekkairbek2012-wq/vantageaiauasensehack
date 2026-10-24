"""Обучение сиамской сети.

Три вещи, которые здесь сделаны намеренно и которые придётся объяснять.

**Метрика — не accuracy.** Кандидатов-«не свалок» в выборке кратно больше,
поэтому модель, всегда отвечающая «не свалка», получит высокую точность и
будет бесполезна. Основная метрика — PR-AUC (площадь под кривой
точность-полнота), она честно ведёт себя при дисбалансе классов.

**Порог решения подбирается, а не берётся 0.5.** Цена ошибок разная:
пропустить свалку — потерять объект, ложно указать на карьер — потратить
выезд инспектора. Порог выбирается на валидации по F-мере с указанным
приоритетом полноты.

**Аугментация только геометрическая.** Повороты и отражения законны:
свалка не имеет выделенного направления. А вот менять яркость нельзя —
это ровно тот сигнал, который модель должна выучить.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..chips import ChannelStats, ChipDataset, train_val_split
from ..config import ModelCfg
from .siamese import SiameseConfig, build_model

log = logging.getLogger(__name__)


@dataclass
class TrainingHistory:
    """История обучения — то, что показывают на слайде вместо одной цифры."""

    train_loss: list[float] = field(default_factory=list)
    val_loss: list[float] = field(default_factory=list)
    val_pr_auc: list[float] = field(default_factory=list)
    best_epoch: int = -1
    best_pr_auc: float = 0.0

    def as_dict(self) -> dict:
        return {
            "train_loss": self.train_loss,
            "val_loss": self.val_loss,
            "val_pr_auc": self.val_pr_auc,
            "best_epoch": self.best_epoch,
            "best_pr_auc": self.best_pr_auc,
        }


@dataclass
class TrainedModel:
    """Обученная модель со всем, что нужно для воспроизводимого применения."""

    model: object
    stats: ChannelStats
    channels: list[str]
    threshold: float
    history: TrainingHistory
    config: SiameseConfig

    def save(self, path: str | Path) -> None:
        import torch

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "state_dict": self.model.state_dict(),
                "config": self.config.__dict__,
                "channels": self.channels,
                "threshold": self.threshold,
                "stats_mean": self.stats.mean,
                "stats_std": self.stats.std,
                "history": self.history.as_dict(),
            },
            path,
        )
        log.info("Модель сохранена: %s", path)

    @classmethod
    def load(cls, path: str | Path) -> TrainedModel:
        import torch

        payload = torch.load(Path(path), map_location="cpu", weights_only=False)
        config = SiameseConfig(**payload["config"])
        model = build_model(config)
        model.load_state_dict(payload["state_dict"])
        model.eval()
        history = TrainingHistory(**payload["history"])
        return cls(
            model=model,
            stats=ChannelStats(mean=payload["stats_mean"], std=payload["stats_std"]),
            channels=list(payload["channels"]),
            threshold=float(payload["threshold"]),
            history=history,
            config=config,
        )


# --------------------------------------------------------------------------- #
#  Метрики
# --------------------------------------------------------------------------- #


def pr_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Площадь под кривой точность-полнота (average precision).

    Реализовано вручную, без sklearn: на защите нужно уметь объяснить, что
    именно измеряется. Считается как сумма приростов полноты, взвешенных
    на достигнутую точность — стандартное определение average precision.
    """
    y_true = np.asarray(y_true).astype(int)
    y_score = np.asarray(y_score, dtype=float)
    if y_true.size == 0 or y_true.sum() == 0:
        return 0.0

    order = np.argsort(-y_score)
    y_sorted = y_true[order]
    tp = np.cumsum(y_sorted)
    fp = np.cumsum(1 - y_sorted)
    precision = tp / np.maximum(tp + fp, 1)
    recall = tp / y_true.sum()

    recall_gain = np.diff(np.concatenate([[0.0], recall]))
    return float(np.sum(precision * recall_gain))


def pick_threshold(
    y_true: np.ndarray, y_score: np.ndarray, *, recall_weight: float = 2.0
) -> tuple[float, dict[str, float]]:
    """Подобрать порог решения по F-бета на валидации.

    ``recall_weight`` больше единицы означает, что полнота важнее точности:
    лучше показать инспектору лишний карьер, чем не показать свалку.
    Ложное срабатывание стоит одного выезда, пропуск — необнаруженного
    объекта, который продолжает расти и дорожать в уборке.
    """
    y_true = np.asarray(y_true).astype(int)
    y_score = np.asarray(y_score, dtype=float)
    if y_true.sum() == 0:
        return 0.5, {"f_beta": 0.0, "precision": 0.0, "recall": 0.0}

    beta2 = recall_weight**2
    best = (0.5, -1.0, 0.0, 0.0)
    for threshold in np.unique(np.round(y_score, 3)):
        predicted = y_score >= threshold
        tp = int(np.sum(predicted & (y_true == 1)))
        fp = int(np.sum(predicted & (y_true == 0)))
        fn = int(np.sum(~predicted & (y_true == 1)))
        if tp == 0:
            continue
        precision = tp / (tp + fp)
        recall = tp / (tp + fn)
        f_beta = (1 + beta2) * precision * recall / (beta2 * precision + recall)
        if f_beta > best[1]:
            best = (float(threshold), float(f_beta), precision, recall)

    threshold, f_beta, precision, recall = best
    return threshold, {"f_beta": f_beta, "precision": precision, "recall": recall}


# --------------------------------------------------------------------------- #
#  Аугментация
# --------------------------------------------------------------------------- #


def augment_pair(before: np.ndarray, after: np.ndarray, rng: np.random.Generator):
    """Согласованная геометрическая аугментация пары чипов.

    Обе эпохи преобразуются ОДИНАКОВО: иначе сеть увидит смещение объекта
    между эпохами и решит, что это и есть изменение.

    Яркостная аугментация сознательно не применяется — именно спектральные
    значения несут сигнал, и их искажение стёрло бы то, что нужно выучить.
    """
    k = int(rng.integers(0, 4))
    if k:
        before = np.rot90(before, k, axes=(-2, -1))
        after = np.rot90(after, k, axes=(-2, -1))
    if rng.random() < 0.5:
        before = np.flip(before, axis=-1)
        after = np.flip(after, axis=-1)
    if rng.random() < 0.5:
        before = np.flip(before, axis=-2)
        after = np.flip(after, axis=-2)
    return np.ascontiguousarray(before), np.ascontiguousarray(after)


# --------------------------------------------------------------------------- #
#  Обучение
# --------------------------------------------------------------------------- #


def train(
    dataset: ChipDataset,
    cfg: ModelCfg,
    *,
    device: str = "cpu",
    recall_weight: float = 2.0,
    progress: bool = True,
) -> TrainedModel:
    """Обучить сиамскую сеть на размеченных парах чипов."""
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    if dataset.labels is None:
        raise ValueError("для обучения нужны метки: dataset.labels пуст")
    if len(np.unique(dataset.labels)) < 2:
        raise ValueError("в выборке представлен только один класс — обучать нечему")

    torch.manual_seed(cfg.seed)
    rng = np.random.default_rng(cfg.seed)

    train_idx, val_idx = train_val_split(len(dataset), cfg.val_fraction, cfg.seed)
    # Статистика нормировки — только по обучающей части, иначе утечка данных
    stats = ChannelStats.fit(dataset, train_idx)

    train_before = stats.apply(dataset.before[train_idx])
    train_after = stats.apply(dataset.after[train_idx])
    train_y = dataset.labels[train_idx].astype("float32")

    val_before = torch.from_numpy(stats.apply(dataset.before[val_idx]))
    val_after = torch.from_numpy(stats.apply(dataset.after[val_idx]))
    val_y = dataset.labels[val_idx].astype(int)

    config = SiameseConfig(
        in_channels=dataset.n_channels,
        embedding_dim=cfg.embedding_dim,
        dropout=cfg.dropout,
    )
    model = build_model(config).to(device)
    log.info("Параметров в модели: %s", f"{model.n_parameters():,}".replace(",", " "))

    # Взвешивание положительного класса: свалок в выборке меньше,
    # без веса модель научится отвечать «не свалка» всегда.
    n_pos = max(1, int(train_y.sum()))
    n_neg = max(1, int(len(train_y) - n_pos))
    pos_weight = torch.tensor([n_neg / n_pos], dtype=torch.float32, device=device)
    criterion = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=cfg.epochs)

    loader = DataLoader(
        TensorDataset(
            torch.from_numpy(train_before), torch.from_numpy(train_after), torch.from_numpy(train_y)
        ),
        batch_size=min(cfg.batch_size, len(train_idx)),
        shuffle=True,
        drop_last=False,
    )

    history = TrainingHistory()
    best_state: dict | None = None
    epochs_without_improvement = 0

    for epoch in range(cfg.epochs):
        model.train()
        epoch_losses = []
        for batch_before, batch_after, batch_y in loader:
            b_np, a_np = augment_pair(batch_before.numpy(), batch_after.numpy(), rng)
            batch_before = torch.from_numpy(b_np).to(device)
            batch_after = torch.from_numpy(a_np).to(device)
            batch_y = batch_y.to(device)

            optimizer.zero_grad()
            logits = model(batch_before, batch_after)
            loss = criterion(logits, batch_y)
            loss.backward()
            # Обрезка градиента: при малой выборке одиночный тяжёлый батч
            # иначе способен развалить обучение
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            epoch_losses.append(float(loss.item()))

        scheduler.step()

        model.eval()
        with torch.no_grad():
            val_logits = model(val_before.to(device), val_after.to(device))
            val_loss = float(criterion(val_logits, torch.from_numpy(val_y.astype("float32")).to(device)))
            val_scores = torch.sigmoid(val_logits).cpu().numpy()

        score = pr_auc(val_y, val_scores)
        history.train_loss.append(float(np.mean(epoch_losses)))
        history.val_loss.append(val_loss)
        history.val_pr_auc.append(score)

        if score > history.best_pr_auc:
            history.best_pr_auc = score
            history.best_epoch = epoch
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1

        if progress and (epoch % 5 == 0 or epoch == cfg.epochs - 1):
            log.info(
                "Эпоха %d/%d: loss=%.4f val_loss=%.4f PR-AUC=%.3f",
                epoch + 1, cfg.epochs, history.train_loss[-1], val_loss, score,
            )

        if epochs_without_improvement >= cfg.early_stopping_patience:
            log.info("Ранняя остановка на эпохе %d (лучшая — %d)", epoch + 1, history.best_epoch + 1)
            break

    if best_state is not None:
        model.load_state_dict(best_state)

    model.eval()
    with torch.no_grad():
        final_scores = torch.sigmoid(model(val_before.to(device), val_after.to(device))).cpu().numpy()
    threshold, metrics = pick_threshold(val_y, final_scores, recall_weight=recall_weight)
    log.info(
        "Порог решения %.3f: точность %.2f, полнота %.2f, PR-AUC %.3f",
        threshold, metrics["precision"], metrics["recall"], history.best_pr_auc,
    )

    return TrainedModel(
        model=model,
        stats=stats,
        channels=list(dataset.channels),
        threshold=threshold,
        history=history,
        config=config,
    )


__all__ = [
    "TrainedModel",
    "TrainingHistory",
    "augment_pair",
    "pick_threshold",
    "pr_auc",
    "train",
]
