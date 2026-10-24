"""Гражданский контур: сообщения жителей и их сопоставление со спутником.

Зачем это нужно
---------------
Спутник видит изменение поверхности, но не видит, что именно там лежит.
Житель видит, что лежит, но не знает, что об этом месте уже известно
системе. Соединение этих двух источников даёт то, чего нет ни у одного
по отдельности:

  * **совпадение** — сообщение попало в уже найденного кандидата.
    Это независимое подтверждение: уверенность растёт, и объект уходит
    в начало очереди на выезд.
  * **несовпадение** — житель нашёл то, чего спутник не увидел. Обычно
    это объект меньше порога разрешения (30–50 м²). Он становится новым
    кандидатом, который иначе не появился бы никогда.

Второй эффект важнее первого: он закрывает главное честное ограничение
всей системы — слепоту к мелким объектам.

Приватность
-----------
Идентификатор отправителя не хранится. Хранится только его хеш с солью:
этого достаточно, чтобы ограничить спам от одного пользователя, и
недостаточно, чтобы восстановить, кто именно сообщил о свалке рядом
с чужим участком. Для гражданского контура это принципиально: человек
должен иметь возможность сообщить, не опасаясь последствий.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

ReportStatus = Literal["new", "matched", "rejected", "confirmed"]

#: Радиус сопоставления сообщения со спутниковым кандидатом, метры.
#: 150 м — это точность бытовой геолокации на телефоне плюс размах руки
#: человека, который фотографирует свалку с обочины, а не стоя на ней.
MATCH_RADIUS_M = 150.0

#: Насколько подтверждение жителем повышает уверенность в объекте.
#: Прибавка сознательно скромная: один человек с телефоном — это
#: свидетельство, а не доказательство.
CONFIRMATION_BOOST = 0.15

#: Антиспам: сколько сообщений принимается от одного отправителя в сутки.
DAILY_LIMIT_PER_SENDER = 10

#: Минимальный интервал между сообщениями одного отправителя, секунды.
MIN_INTERVAL_S = 30

#: Соль для хеширования идентификаторов. В эксплуатации задаётся
#: переменной окружения и не попадает в репозиторий.
SALT_ENV = "VANTAGE_BOT_SALT"

EARTH_RADIUS_M = 6_371_000.0


class RateLimited(Exception):
    """Отправитель превысил лимит сообщений."""


@dataclass
class CitizenReport:
    """Сообщение от жителя."""

    report_id: str
    sender_hash: str
    latitude: float
    longitude: float
    created_at: datetime = field(default_factory=datetime.now)
    photo_id: str | None = None
    comment: str = ""
    status: ReportStatus = "new"
    matched_candidate_id: str | None = None

    def __post_init__(self) -> None:
        if not -90.0 <= self.latitude <= 90.0:
            raise ValueError(f"широта вне диапазона: {self.latitude}")
        if not -180.0 <= self.longitude <= 180.0:
            raise ValueError(f"долгота вне диапазона: {self.longitude}")

    @property
    def has_photo(self) -> bool:
        return bool(self.photo_id)

    def as_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "sender_hash": self.sender_hash,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "created_at": self.created_at.isoformat(timespec="seconds"),
            "photo_id": self.photo_id,
            "comment": self.comment,
            "status": self.status,
            "matched_candidate_id": self.matched_candidate_id,
        }


def hash_sender(sender_id: str | int, *, salt: str | None = None) -> str:
    """Необратимый идентификатор отправителя.

    Соль обязательна: пространство идентификаторов Telegram маленькое,
    и простой SHA-256 без соли перебирается за минуты.
    """
    salt = salt if salt is not None else os.environ.get(SALT_ENV, "")
    if not salt:
        log.warning(
            "%s не задана — хеши отправителей уязвимы к перебору. "
            "Задайте случайную соль перед эксплуатацией.",
            SALT_ENV,
        )
    return hashlib.sha256(f"{salt}:{sender_id}".encode()).hexdigest()[:16]


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между двумя точками по поверхности Земли, метры.

    Формула гаверсинуса, а не евклидово расстояние по градусам: на широте
    Астаны градус долготы короче градуса широты почти вдвое, и наивная
    формула ошибается в разы именно в направлении восток-запад.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------- #
#  Сопоставление
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MatchResult:
    """Итог сопоставления сообщения со спутниковыми кандидатами."""

    report_id: str
    candidate_id: str | None
    distance_m: float | None
    is_new_object: bool

    @property
    def matched(self) -> bool:
        return self.candidate_id is not None

    def to_user_text(self) -> str:
        """Ответ жителю. Тон нейтральный: система не обвиняет и не хвалит."""
        if self.matched:
            return (
                "Спасибо. Этот объект уже есть в системе — ваше сообщение "
                "стало независимым подтверждением и повысило его приоритет "
                "на выезд."
            )
        return (
            "Спасибо. Объекта по этим координатам в системе не было — "
            "мы добавили его на проверку. Такие сообщения особенно ценны: "
            "спутник не видит объекты меньше нескольких десятков метров."
        )


def match_report(
    report: CitizenReport,
    candidates,
    *,
    radius_m: float = MATCH_RADIUS_M,
) -> MatchResult:
    """Найти спутникового кандидата рядом с точкой сообщения.

    ``candidates`` — GeoDataFrame в WGS84 либо любой итерируемый объект
    со строками, имеющими ``candidate_id`` и ``geometry``.
    """
    best_id: str | None = None
    best_distance: float | None = None

    if candidates is not None and len(candidates):
        rows = candidates.to_crs("EPSG:4326") if hasattr(candidates, "to_crs") else candidates
        for _, row in rows.iterrows():
            point = row.geometry.representative_point()
            distance = haversine_m(report.latitude, report.longitude, point.y, point.x)
            if best_distance is None or distance < best_distance:
                best_distance, best_id = distance, str(row["candidate_id"])

    if best_distance is not None and best_distance <= radius_m:
        return MatchResult(report.report_id, best_id, best_distance, is_new_object=False)
    return MatchResult(report.report_id, None, best_distance, is_new_object=True)


def confidence_after_confirmation(
    probability: float | None, n_confirmations: int, *, boost: float = CONFIRMATION_BOOST
) -> float:
    """Уверенность после подтверждений жителями.

    Прибавка убывает: второе подтверждение весит меньше первого, третье —
    меньше второго. Иначе десять сообщений от одного двора вывели бы
    объект в стопроцентную уверенность без единой проверки на месте.
    Потолок 0.99: полной уверенности без выезда не бывает.
    """
    base = 0.5 if probability is None else float(probability)
    if n_confirmations <= 0:
        return base
    # Сумма убывающей геометрической прогрессии: boost * (1 + 1/2 + 1/4 + ...)
    total_boost = boost * sum(0.5**i for i in range(n_confirmations))
    return float(min(0.99, base + total_boost))


# --------------------------------------------------------------------------- #
#  Хранилище сообщений
# --------------------------------------------------------------------------- #


class ReportStore:
    """Журнал сообщений жителей с антиспамом.

    Пишется в JSONL: формат простой, дописывается атомарно построчно и
    читается любым инструментом. База данных здесь была бы избыточна.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.reports: list[CitizenReport] = []
        self.confirmations: dict[str, int] = {}
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def __len__(self) -> int:
        return len(self.reports)

    def check_rate_limit(self, sender_hash: str, *, now: datetime | None = None) -> None:
        """Проверить лимиты отправителя. Бросает :class:`RateLimited`.

        Публичный бот без ограничений превращается в помойку за сутки.
        Ограничение мягкое: десять сообщений в день — это гораздо больше,
        чем отправит настоящий человек, и заметно меньше, чем нужно для
        осмысленного залива мусорных точек.
        """
        now = now or datetime.now()
        recent = [r for r in self.reports if r.sender_hash == sender_hash]

        last_day = [r for r in recent if now - r.created_at < timedelta(days=1)]
        if len(last_day) >= DAILY_LIMIT_PER_SENDER:
            raise RateLimited(
                f"превышен суточный лимит сообщений ({DAILY_LIMIT_PER_SENDER}). "
                "Попробуйте завтра."
            )

        if recent:
            since_last = (now - max(r.created_at for r in recent)).total_seconds()
            if since_last < MIN_INTERVAL_S:
                raise RateLimited(
                    f"слишком часто, подождите {int(MIN_INTERVAL_S - since_last)} с."
                )

    def add(self, report: CitizenReport, *, now: datetime | None = None) -> CitizenReport:
        """Принять сообщение (с проверкой лимитов)."""
        self.check_rate_limit(report.sender_hash, now=now)
        self.reports.append(report)
        if self.path:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(report.as_dict(), ensure_ascii=False) + "\n")
        return report

    def apply_match(self, report: CitizenReport, match: MatchResult) -> CitizenReport:
        """Записать итог сопоставления в сообщение."""
        if match.matched:
            report.status = "matched"
            report.matched_candidate_id = match.candidate_id
            self.confirmations[match.candidate_id] = (
                self.confirmations.get(match.candidate_id, 0) + 1
            )
        else:
            report.status = "new"
        return report

    def confirmations_for(self, candidate_id: str) -> int:
        return self.confirmations.get(candidate_id, 0)

    def unmatched(self) -> list[CitizenReport]:
        """Сообщения, не совпавшие ни с одним спутниковым кандидатом.

        Самая ценная очередь в гражданском контуре: это объекты, которых
        спутник не увидел.
        """
        return [r for r in self.reports if r.status == "new"]

    def stats(self) -> dict:
        return {
            "total": len(self.reports),
            "matched": sum(1 for r in self.reports if r.status == "matched"),
            "new_objects": len(self.unmatched()),
            "unique_senders": len({r.sender_hash for r in self.reports}),
        }


__all__ = [
    "CONFIRMATION_BOOST",
    "DAILY_LIMIT_PER_SENDER",
    "MATCH_RADIUS_M",
    "MIN_INTERVAL_S",
    "SALT_ENV",
    "CitizenReport",
    "MatchResult",
    "RateLimited",
    "ReportStatus",
    "ReportStore",
    "confidence_after_confirmation",
    "hash_sender",
    "haversine_m",
    "match_report",
]
