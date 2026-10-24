"""Применение обученной модели к новым кандидатам.

Отдельный модуль от обучения по одной причине: применение должно работать
без данных обучения, без оптимизатора и без разметки — только модель,
статистика нормировки и порог, сохранённые вместе. Всё, что нужно для
воспроизводимого предсказания, лежит в одном файле ``.pt``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from ..chips import ChipDataset
from .train import TrainedModel

log = logging.getLogger(__name__)

#: Размер батча при применении. Ограничен памятью, а не качеством.
DEFAULT_BATCH = 64


@dataclass(frozen=True)
class Prediction:
    """Результат применения модели ко всем кандидатам."""

    candidate_ids: list[str]
    probability: np.ndarray
    is_landfill: np.ndarray
    threshold: float

    def __len__(self) -> int:
        return int(self.probability.size)

    @property
    def n_positive(self) -> int:
        return int(self.is_landfill.sum())

    def top_k(self, k: int) -> list[tuple[str, float]]:
        """Самые уверенные кандидаты — те, что идут на доверификацию."""
        order = np.argsort(-self.probability)[:k]
        return [(self.candidate_ids[i], float(self.probability[i])) for i in order]

    def as_records(self) -> list[dict]:
        return [
            {
                "candidate_id": self.candidate_ids[i],
                "probability": float(self.probability[i]),
                "is_landfill": bool(self.is_landfill[i]),
            }
            for i in range(len(self))
        ]


def predict(
    trained: TrainedModel,
    dataset: ChipDataset,
    *,
    device: str = "cpu",
    batch_size: int = DEFAULT_BATCH,
) -> Prediction:
    """Предсказать вероятность для каждой пары чипов.

    Порядок каналов проверяется явно: модель, обученная на каналах
    ``[B02, B04, B08, ndvi, bsi]`` и применённая к данным с другим порядком,
    не упадёт — она молча выдаст бессмысленные числа. Это худший вид ошибки,
    поэтому здесь стоит жёсткая проверка.
    """
    import torch

    if list(dataset.channels) != list(trained.channels):
        raise ValueError(
            "порядок или состав каналов не совпадает с обучающим.\n"
            f"  модель ожидает: {trained.channels}\n"
            f"  получено:       {dataset.channels}"
        )

    before = trained.stats.apply(dataset.before)
    after = trained.stats.apply(dataset.after)

    model = trained.model.to(device)
    model.eval()

    scores: list[np.ndarray] = []
    with torch.no_grad():
        for start in range(0, len(dataset), batch_size):
            stop = min(start + batch_size, len(dataset))
            logits = model(
                torch.from_numpy(before[start:stop]).to(device),
                torch.from_numpy(after[start:stop]).to(device),
            )
            scores.append(torch.sigmoid(logits).cpu().numpy())

    probability = np.concatenate(scores) if scores else np.array([], dtype="float32")
    prediction = Prediction(
        candidate_ids=list(dataset.candidate_ids),
        probability=probability,
        is_landfill=probability >= trained.threshold,
        threshold=trained.threshold,
    )
    log.info(
        "Классифицировано %d кандидатов, положительных %d (порог %.3f)",
        len(prediction), prediction.n_positive, trained.threshold,
    )
    return prediction


def attach_to_candidates(candidates, prediction: Prediction):
    """Добавить вероятность и решение в таблицу кандидатов.

    Сопоставление идёт по ``candidate_id``, а не по порядку строк: между
    извлечением чипов и применением модели набор мог быть отфильтрован.
    """
    import pandas as pd

    scores = pd.DataFrame(prediction.as_records()).set_index("candidate_id")
    result = candidates.copy()
    result["probability"] = result["candidate_id"].map(scores["probability"])
    # np.where вместо fillna: map по колонке bool даёт dtype object, и
    # fillna на нём предупреждает о будущей смене поведения pandas.
    # Кандидат без предсказания считается неклассифицированным (False).
    mapped = result["candidate_id"].map(scores["is_landfill"])
    result["is_landfill"] = np.where(mapped.isna(), False, mapped).astype(bool)

    missing = int(result["probability"].isna().sum())
    if missing:
        log.warning("Для %d кандидатов нет предсказания — они помечены как неклассифицированные", missing)
    return result


__all__ = ["DEFAULT_BATCH", "Prediction", "attach_to_candidates", "predict"]
