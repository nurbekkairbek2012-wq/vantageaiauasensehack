"""Слой объяснимости: почему система считает этот объект свалкой.

Модель не должна выдавать вердикт. Она должна выдавать **доказательную
цепочку**. Разница видна на защите мгновенно: «нейросеть решила, что это
свалка» звучит как гадание, а «пять независимых физических признаков
сработали, вот их вклад» — как измерение.

Здесь два независимых объяснения, и оба нужны.

**Физическая панель** (``physical_evidence``) не зависит от модели вообще.
Она показывает силу каждого из пяти признаков в понятных величинах:
насколько упал NDVI, насколько вырос BSI, есть ли отклик полимеров в SWIR,
потеряна ли радарная когерентность, есть ли тепловая аномалия. Это то,
что можно объяснить человеку без слова «нейросеть».

**Вклад каналов в решение модели** (``channel_attribution``) считается
методом окклюзии: канал по очереди заменяется своим средним, и измеряется,
насколько просела вероятность. Метод выбран сознательно — он точный
(никаких приближений градиентом), не требует дополнительных библиотек и
объясняется одной фразой: «убрали канал, посмотрели, что изменилось».

Согласие двух объяснений — сильный аргумент. Расхождение — повод
разбираться, а не выкатывать модель.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)

#: Пять физических признаков свалки в порядке, в котором они показываются
#: в панели. Порядок фиксирован: он совпадает с порядком объяснения на питче.
PHYSICAL_SIGNALS = ("ndvi_drop", "bsi_rise", "pmli_response", "sar_incoherence", "thermal_anomaly")

#: Шкалы нормировки: значение признака, которое считается «полной силой».
#: Величины подобраны по физике процесса, а не по данным, поэтому панель
#: остаётся интерпретируемой и не переобучается под конкретную выборку.
SIGNAL_FULL_SCALE = {
    "ndvi_drop": 0.35,        # падение NDVI на 0.35 — полное исчезновение растительности
    "bsi_rise": 0.25,         # рост BSI на 0.25 — переход к полностью открытой поверхности
    "pmli_response": 0.15,    # отклик полимеров в SWIR
    "sar_incoherence": 0.50,  # потеря когерентности вдвое
    "thermal_anomaly": 3.0,   # превышение фона на 3 K
}


@dataclass(frozen=True)
class Evidence:
    """Доказательная цепочка по одному кандидату."""

    candidate_id: str
    signals: dict[str, float]          # сырые значения признаков
    strength: dict[str, float]         # нормированная сила 0..1
    n_agreeing: int                    # сколько признаков сработали
    combined_score: float              # согласованность признаков 0..1
    probability: float | None = None   # что сказала модель

    def top_signals(self, k: int = 3) -> list[tuple[str, float]]:
        return sorted(self.strength.items(), key=lambda kv: -kv[1])[:k]

    def to_text(self) -> str:
        """Человекочитаемое объяснение — то, что видит инспектор в панели."""
        names = {
            "ndvi_drop": "падение растительности",
            "bsi_rise": "рост открытого грунта",
            "pmli_response": "отклик полимеров в SWIR",
            "sar_incoherence": "нестабильность поверхности по радару",
            "thermal_anomaly": "тепловая аномалия",
        }
        parts = [
            f"{names.get(name, name)} — {value:.0%}"
            for name, value in self.top_signals()
            if value > 0.05
        ]
        if not parts:
            return "ни один физический признак не выражен"
        return f"сработало признаков: {self.n_agreeing} из 5. " + "; ".join(parts)


def _strength(value: float, full_scale: float) -> float:
    """Нормировать признак в шкалу 0..1 с насыщением.

    Насыщение важно: падение NDVI на 0.7 не «вдвое убедительнее», чем на
    0.35 — растительность нельзя убрать дважды. Без насыщения один
    экстремальный признак перевешивал бы согласие всех остальных.
    """
    if not np.isfinite(value) or full_scale <= 0:
        return 0.0
    return float(np.clip(value / full_scale, 0.0, 1.0))


def physical_evidence(
    candidate_id: str,
    *,
    ndvi_drop: float = np.nan,
    bsi_rise: float = np.nan,
    pmli_response: float = np.nan,
    sar_incoherence: float = np.nan,
    thermal_anomaly: float = np.nan,
    probability: float | None = None,
    agreement_threshold: float = 0.3,
) -> Evidence:
    """Собрать физическую панель по одному кандидату.

    ``combined_score`` — среднее по доступным признакам. Именно среднее,
    а не максимум: свалку определяет **согласие** независимых признаков.
    Один сильный признак при молчании остальных — это обычно карьер
    (BSI вырос, но полимеров нет и тепловой аномалии нет).
    """
    raw = {
        "ndvi_drop": ndvi_drop,
        "bsi_rise": bsi_rise,
        "pmli_response": pmli_response,
        "sar_incoherence": sar_incoherence,
        "thermal_anomaly": thermal_anomaly,
    }
    strength = {name: _strength(value, SIGNAL_FULL_SCALE[name]) for name, value in raw.items()}
    available = [v for name, v in strength.items() if np.isfinite(raw[name])]

    return Evidence(
        candidate_id=candidate_id,
        signals=raw,
        strength=strength,
        n_agreeing=sum(1 for v in strength.values() if v >= agreement_threshold),
        combined_score=float(np.mean(available)) if available else 0.0,
        probability=probability,
    )


def evidence_table(candidates, predictions=None) -> list[Evidence]:
    """Построить панель для всех кандидатов из таблицы атрибутов.

    Отсутствующие колонки (радар и тепло считаются отдельными ветками
    пайплайна и могут быть ещё не посчитаны) заполняются NaN и просто
    не участвуют в согласии, а не обнуляют его.
    """
    probs = {}
    if predictions is not None:
        probs = dict(zip(predictions.candidate_ids, predictions.probability, strict=False))

    def value(row, column: str) -> float:
        return float(row[column]) if column in candidates.columns else float("nan")

    result = []
    for _, row in candidates.iterrows():
        cid = str(row["candidate_id"])
        result.append(
            physical_evidence(
                cid,
                ndvi_drop=value(row, "ndvi_drop"),
                bsi_rise=value(row, "bsi_rise"),
                pmli_response=value(row, "pmli_response"),
                sar_incoherence=value(row, "sar_incoherence"),
                thermal_anomaly=value(row, "thermal_anomaly"),
                probability=probs.get(cid),
            )
        )
    return result


# --------------------------------------------------------------------------- #
#  Вклад каналов в решение модели
# --------------------------------------------------------------------------- #


def channel_attribution(
    trained,
    dataset,
    *,
    device: str = "cpu",
    batch_size: int = 32,
) -> dict[str, np.ndarray]:
    """Вклад каждого канала в решение модели методом окклюзии.

    Для каждого канала: заменяем его нулём (после нормировки ноль — это
    среднее по каналу, то есть «сигнала нет»), прогоняем модель заново и
    смотрим, насколько изменилась вероятность. Чем сильнее просела —
    тем важнее был канал.

    Возвращает словарь {имя канала: массив вкладов по кандидатам}.
    Положительное значение — канал поддерживал решение «свалка».

    Метод точный и объясняется одной фразой. Градиентные методы дали бы
    примерно то же за меньшее время, но потребовали бы объяснять, почему
    градиент вообще что-то говорит о важности — лишний риск на Q&A.
    """
    import torch

    model = trained.model.to(device)
    model.eval()

    before = trained.stats.apply(dataset.before)
    after = trained.stats.apply(dataset.after)

    def run(b: np.ndarray, a: np.ndarray) -> np.ndarray:
        out = []
        with torch.no_grad():
            for start in range(0, len(b), batch_size):
                stop = min(start + batch_size, len(b))
                logits = model(
                    torch.from_numpy(b[start:stop]).to(device),
                    torch.from_numpy(a[start:stop]).to(device),
                )
                out.append(torch.sigmoid(logits).cpu().numpy())
        return np.concatenate(out) if out else np.array([])

    baseline = run(before, after)

    attribution: dict[str, np.ndarray] = {}
    for index, name in enumerate(dataset.channels):
        occluded_before = before.copy()
        occluded_after = after.copy()
        occluded_before[:, index] = 0.0
        occluded_after[:, index] = 0.0
        attribution[name] = baseline - run(occluded_before, occluded_after)

    log.info("Вклад каналов посчитан для %d кандидатов", len(baseline))
    return attribution


def summarize_attribution(attribution: dict[str, np.ndarray]) -> dict[str, float]:
    """Средний по выборке вклад каждого канала — для слайда о модели.

    Ожидаемая картина: наибольший вклад у NDVI и BSI. Если вдруг главным
    окажется синий канал, это сигнал, что модель зацепилась за артефакт
    атмосферной коррекции, а не за физику.
    """
    return {name: float(np.mean(values)) for name, values in attribution.items()}


def disagreement(evidence: list[Evidence], *, tolerance: float = 0.35) -> list[str]:
    """Кандидаты, где модель и физика расходятся.

    Это самый ценный список в системе. Модель говорит «свалка», а физические
    признаки молчат — либо модель зацепилась за артефакт, либо признак,
    который она нашла, мы ещё не формализовали. И то и другое надо смотреть
    руками до, а не после защиты.
    """
    out = []
    for item in evidence:
        if item.probability is None:
            continue
        if abs(item.probability - item.combined_score) > tolerance:
            out.append(item.candidate_id)
    return out


__all__ = [
    "PHYSICAL_SIGNALS",
    "SIGNAL_FULL_SCALE",
    "Evidence",
    "channel_attribution",
    "disagreement",
    "evidence_table",
    "physical_evidence",
    "summarize_attribution",
]
