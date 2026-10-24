"""Контроль устранения: свалку убрали или засыпали?

Зачем нужен отдельный модуль
----------------------------
Продукт, который только находит объекты, закрывает половину задачи.
Вторая половина — проверить, что по акту действительно выехали и убрали.
Без неё система остаётся генератором работы для чиновника, а не
инструментом контроля исполнения.

Почему нельзя смотреть только на NDVI
-------------------------------------
Наивная проверка «растительность вернулась — значит убрали» ломается
тремя способами, и каждый из них подрывает доверие быстрее, чем полное
отсутствие функции:

    засыпали грунтом  — сверху лёг слой земли, через сезон проросла
                        трава. NDVI восстановился, отходы никуда не
                        делись и продолжают выделять метан;
    заросло само      — свалку не тронули, её просто затянуло бурьяном
                        по краям и сверху;
    один хороший снимок — облако ушло, освещение удачное, NDVI подскочил
                        на одном проходе.

Поэтому решение принимается по **согласию нескольких независимых
признаков** и подтверждается **на нескольких последовательных проходах**,
а результат публикуется как уровень уверенности, а не как «убрано».

Ключевое различение
-------------------
Расчищенный участок и засыпанный грунтом ведут себя по-разному:

    расчищено   NDVI вернулся, BSI упал,  тепловая аномалия ушла
    засыпано    NDVI вернулся, BSI НИЗКИЙ или средний, но тепловая
                аномалия ОСТАЛАСЬ — органика под слоем грунта
                продолжает разлагаться

Тепловой признак здесь решающий: он единственный видит сквозь
присыпку. Именно поэтому статус «возможно засыпано» существует
отдельно от «возможно устранено».
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from .config import RemovalCfg

log = logging.getLogger(__name__)

RemovalStatus = Literal["active", "possibly_removed", "possibly_covered", "insufficient_data"]

#: Признаки устранения. Порядок фиксирован — он же порядок в отчёте.
REMOVAL_SIGNALS = (
    "ndvi_recovered",
    "bsi_normalized",
    "pmli_gone",
    "sar_stabilized",
    "thermal_gone",
)

#: Ниже этого числа наблюдений после разрыва вывод не делается вообще.
MIN_POST_OBSERVATIONS = 4


@dataclass
class RemovalAssessment:
    """Вывод по одному объекту."""

    candidate_id: str
    status: RemovalStatus
    confidence: float
    signals: dict[str, bool] = field(default_factory=dict)
    values: dict[str, float] = field(default_factory=dict)
    consecutive_passes: int = 0
    n_observations: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def n_agreeing(self) -> int:
        return sum(1 for value in self.signals.values() if value)

    def to_text(self) -> str:
        """Формулировка для панели и для акта.

        Никогда не говорит «убрано» — только «вероятно, с такой-то
        уверенностью». Ложное «убрано» подрывает доверие акимата
        к системе быстрее, чем отсутствие функции целиком.
        """
        if self.status == "insufficient_data":
            return f"данных после обнаружения недостаточно ({self.n_observations} наблюдений)"
        if self.status == "active":
            return "объект активен: признаков устранения нет"
        if self.status == "possibly_covered":
            return (
                f"возможно ЗАСЫПАН грунтом, а не расчищен (уверенность {self.confidence:.0%}). "
                "Тепловая аномалия сохраняется — органика под слоем продолжает разлагаться. "
                "Требуется выездная проверка."
            )
        return (
            f"вероятно устранён (уверенность {self.confidence:.0%}, "
            f"согласны признаков: {self.n_agreeing} из {len(REMOVAL_SIGNALS)}, "
            f"подтверждено проходов подряд: {self.consecutive_passes})"
        )


# --------------------------------------------------------------------------- #
#  Отдельные признаки
# --------------------------------------------------------------------------- #


def ndvi_recovered(
    ndvi_post: np.ndarray, baseline: float, cfg: RemovalCfg
) -> tuple[bool, float]:
    """Вернулась ли растительность к доле baseline.

    Сравнение идёт с **исходным** уровнем до появления свалки, а не с
    абсолютным порогом: у степного участка baseline 0.35, у поливного
    луга 0.6, и единый порог был бы бессмысленным для одного из них.
    """
    values = np.asarray(ndvi_post, dtype="float32")
    values = values[np.isfinite(values)]
    if values.size == 0 or not np.isfinite(baseline) or baseline <= 0:
        return False, float("nan")
    level = float(np.nanmedian(values)) / baseline
    return level >= cfg.ndvi_recovery_threshold, level


def bsi_normalized(
    bsi_post: np.ndarray, baseline: float, cfg: RemovalCfg
) -> tuple[bool, float]:
    """Упал ли индекс открытого грунта обратно к исходному уровню.

    Остаточное превышение выше ``bsi_still_high_threshold`` — сигнал,
    что поверхность всё ещё минеральная. Само по себе это ещё не
    доказательство присыпки, но в сочетании с тепловой аномалией — да.
    """
    values = np.asarray(bsi_post, dtype="float32")
    values = values[np.isfinite(values)]
    if values.size == 0 or not np.isfinite(baseline):
        return False, float("nan")
    excess = float(np.nanmedian(values)) - baseline
    return excess <= cfg.bsi_still_high_threshold, excess


def signal_disappeared(
    values: np.ndarray, *, threshold: float
) -> tuple[bool, float]:
    """Универсальная проверка «признак пропал»: медиана ниже порога.

    Используется для PMLI, радарной нестабильности и тепловой аномалии —
    у всех трёх смысл одинаковый: было превышение, стало нет.
    """
    data = np.asarray(values, dtype="float32")
    data = data[np.isfinite(data)]
    if data.size == 0:
        return False, float("nan")
    level = float(np.nanmedian(data))
    return level <= threshold, level


def count_consecutive(flags: np.ndarray) -> int:
    """Самая длинная серия подряд идущих True в конце ряда.

    Считается именно с конца: важно, что признак держится **сейчас**,
    а не что он однажды продержался три прохода два года назад.
    """
    values = np.asarray(flags, dtype=bool)
    count = 0
    for flag in reversed(values):
        if not flag:
            break
        count += 1
    return count


# --------------------------------------------------------------------------- #
#  Сводное решение
# --------------------------------------------------------------------------- #


def assess_removal(
    candidate_id: str,
    cfg: RemovalCfg,
    *,
    ndvi_post: np.ndarray,
    ndvi_baseline: float,
    bsi_post: np.ndarray,
    bsi_baseline: float,
    pmli_post: np.ndarray | None = None,
    sar_incoherence_post: np.ndarray | None = None,
    thermal_anomaly_post: np.ndarray | None = None,
    clear_pass_flags: np.ndarray | None = None,
    pmli_threshold: float = 0.05,
    sar_threshold: float = 0.5,
    thermal_threshold: float = 0.5,
) -> RemovalAssessment:
    """Оценить, устранён ли объект.

    Все ``*_post`` — наблюдения ПОСЛЕ даты обнаружения. ``clear_pass_flags``
    отмечает проходы, на которых признаки устранения выполнялись; если не
    передан, считается по восстановлению NDVI попроходно.
    """
    n_obs = int(np.isfinite(np.asarray(ndvi_post, dtype="float32")).sum())

    if n_obs < MIN_POST_OBSERVATIONS:
        return RemovalAssessment(
            candidate_id=candidate_id,
            status="insufficient_data",
            confidence=0.0,
            n_observations=n_obs,
            warnings=[
                f"после обнаружения всего {n_obs} наблюдений, "
                f"нужно минимум {MIN_POST_OBSERVATIONS}"
            ],
        )

    signals: dict[str, bool] = {}
    values: dict[str, float] = {}

    signals["ndvi_recovered"], values["ndvi_level"] = ndvi_recovered(
        ndvi_post, ndvi_baseline, cfg
    )
    signals["bsi_normalized"], values["bsi_excess"] = bsi_normalized(
        bsi_post, bsi_baseline, cfg
    )

    if pmli_post is not None:
        signals["pmli_gone"], values["pmli_level"] = signal_disappeared(
            pmli_post, threshold=pmli_threshold
        )
    if sar_incoherence_post is not None:
        signals["sar_stabilized"], values["sar_level"] = signal_disappeared(
            sar_incoherence_post, threshold=sar_threshold
        )
    if thermal_anomaly_post is not None:
        signals["thermal_gone"], values["thermal_level"] = signal_disappeared(
            thermal_anomaly_post, threshold=thermal_threshold
        )

    # Подтверждение на нескольких последовательных проходах
    if clear_pass_flags is None:
        series = np.asarray(ndvi_post, dtype="float32")
        with np.errstate(invalid="ignore"):
            clear_pass_flags = np.isfinite(series) & (
                series >= ndvi_baseline * cfg.ndvi_recovery_threshold
            )
    consecutive = count_consecutive(clear_pass_flags)

    n_agreeing = sum(1 for value in signals.values() if value)
    enough_signals = n_agreeing >= cfg.min_agreeing_signals
    enough_passes = consecutive >= cfg.consecutive_clear_passes

    warnings: list[str] = []

    # Ключевое различение: тепло видит сквозь присыпку.
    thermal_known = "thermal_gone" in signals
    thermal_still_there = thermal_known and not signals["thermal_gone"]
    vegetation_back = signals["ndvi_recovered"]

    if vegetation_back and thermal_still_there:
        warnings.append(
            "растительность восстановилась, но тепловая аномалия сохраняется — "
            "вероятно, отходы засыпаны грунтом, а не вывезены"
        )
        status: RemovalStatus = "possibly_covered"
        confidence = 0.5 + 0.1 * n_agreeing
    elif enough_signals and enough_passes:
        status = "possibly_removed"
        # Уверенность растёт от числа согласных признаков и длины серии,
        # но никогда не достигает единицы: без выезда её не бывает.
        confidence = min(
            0.95,
            0.35
            + 0.12 * n_agreeing
            + 0.05 * min(consecutive, 4)
            + (0.08 if thermal_known and signals["thermal_gone"] else 0.0),
        )
    else:
        status = "active"
        confidence = 0.0
        if enough_signals and not enough_passes:
            warnings.append(
                f"признаки устранения есть, но подтверждены только на "
                f"{consecutive} проходах подряд из {cfg.consecutive_clear_passes} нужных"
            )

    if not thermal_known and status == "possibly_removed":
        warnings.append(
            "тепловой признак недоступен — отличить вывоз от присыпки грунтом нельзя"
        )
        confidence = min(confidence, 0.7)

    assessment = RemovalAssessment(
        candidate_id=candidate_id,
        status=status,
        confidence=round(float(confidence), 3),
        signals=signals,
        values={k: round(float(v), 4) for k, v in values.items() if np.isfinite(v)},
        consecutive_passes=consecutive,
        n_observations=n_obs,
        warnings=warnings,
    )
    log.info("Контроль устранения %s: %s", candidate_id, assessment.to_text())
    return assessment


def summarize(assessments: list[RemovalAssessment]) -> dict[str, int]:
    """Сводка по всем объектам — панель контроля исполнения."""
    result = dict.fromkeys(("active", "possibly_removed", "possibly_covered", "insufficient_data"), 0)
    for item in assessments:
        result[item.status] += 1
    return result


def needs_field_check(assessments: list[RemovalAssessment]) -> list[str]:
    """Объекты, требующие выезда в первую очередь.

    Это подозрение на присыпку. Такой объект формально выглядит убранным,
    по нему может быть закрыт акт и оплачена работа — а отходы на месте.
    Проверять его надо раньше, чем очевидно активные свалки.
    """
    return [item.candidate_id for item in assessments if item.status == "possibly_covered"]


__all__ = [
    "MIN_POST_OBSERVATIONS",
    "REMOVAL_SIGNALS",
    "RemovalAssessment",
    "RemovalStatus",
    "assess_removal",
    "bsi_normalized",
    "count_consecutive",
    "ndvi_recovered",
    "needs_field_check",
    "signal_disappeared",
    "summarize",
]
