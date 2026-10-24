"""Роли, аутентификация и журнал обращений.

Зачем это вообще есть в проекте на хакатон
------------------------------------------
Карта найденных свалок с точными координатами — чувствительные данные.
Она указывает на конкретные участки и, косвенно, на их владельцев,
причём на основании **вероятностной модели**, а не проверки. Публиковать
такое целиком — значит превращать инструмент службы в публичное обвинение
людей, которых никто не проверял.

Поэтому доступ разделён по ролям:

    public    — только агрегированные зоны риска, без точных координат
                и без актов. Координаты округляются до километра.
    operator  — оператор вывоза: точные координаты и оценка ущерба
                (ему нужно спланировать технику), но без формирования актов.
    akimat    — экологическая служба: всё, включая подтверждение актов.
    admin     — плюс служебные операции.

Каждое обращение к точным данным записывается в журнал: кто, когда,
какой объект. Это требование к любой системе, работающей с адресными
данными, и одновременно ответ на вопрос жюри об этике.

Про аутентификацию честно
-------------------------
Здесь простые bearer-токены из переменной окружения. Для прототипа этого
достаточно, для эксплуатации — нет: нужен нормальный провайдер
идентификации с ротацией ключей. Так и надо говорить на защите: мы знаем,
где граница прототипа, и не выдаём её за продакшен.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

Role = Literal["public", "operator", "akimat", "admin"]

#: Иерархия ролей: каждая следующая включает права предыдущей.
ROLE_ORDER: tuple[Role, ...] = ("public", "operator", "akimat", "admin")

#: Права, которые нельзя вывести из иерархии.
#: Подтверждать акты может только служба, но не оператор вывоза:
#: у него коммерческий интерес в объёме работ.
CAN_APPROVE_ACTS: frozenset[Role] = frozenset({"akimat", "admin"})

#: Переменная окружения с токенами в формате token:role,token:role
TOKENS_ENV = "VANTAGE_API_TOKENS"


class AccessDenied(Exception):
    """Недостаточно прав."""


def role_rank(role: Role) -> int:
    try:
        return ROLE_ORDER.index(role)
    except ValueError as exc:
        raise ValueError(f"неизвестная роль: {role}") from exc


def has_at_least(role: Role, required: Role) -> bool:
    """Достаточно ли роли для требуемого уровня."""
    return role_rank(role) >= role_rank(required)


def require(role: Role, required: Role) -> None:
    if not has_at_least(role, required):
        raise AccessDenied(f"требуется роль «{required}», у запроса роль «{role}»")


# --------------------------------------------------------------------------- #
#  Токены
# --------------------------------------------------------------------------- #


@dataclass
class TokenRegistry:
    """Сопоставление токенов ролям.

    Токены хранятся в виде хешей: даже если реестр окажется в логе или
    в дампе памяти, восстановить исходный токен из него нельзя.
    """

    _hashes: dict[str, Role] = field(default_factory=dict)

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def add(self, token: str, role: Role) -> None:
        if not token or len(token) < 8:
            raise ValueError("токен должен быть не короче 8 символов")
        if role not in ROLE_ORDER:
            raise ValueError(f"неизвестная роль: {role}")
        self._hashes[self._hash(token)] = role

    def resolve(self, token: str | None) -> Role:
        """Определить роль по токену. Без токена — публичный доступ."""
        if not token:
            return "public"
        return self._hashes.get(self._hash(token), "public")

    def __len__(self) -> int:
        return len(self._hashes)

    @classmethod
    def from_env(cls, variable: str = TOKENS_ENV) -> TokenRegistry:
        """Собрать реестр из переменной окружения ``token:role,token:role``.

        Если переменная не задана, реестр пуст и работает только публичный
        доступ. Это осознанный выбор: система без настройки должна быть
        безопасной, а не открытой.
        """
        registry = cls()
        raw = os.environ.get(variable, "").strip()
        if not raw:
            log.warning(
                "%s не задана — доступны только публичные данные. "
                "Задайте токены, чтобы открыть точные координаты и акты.",
                variable,
            )
            return registry

        for pair in raw.split(","):
            if ":" not in pair:
                continue
            token, _, role = pair.strip().partition(":")
            try:
                registry.add(token.strip(), role.strip())  # type: ignore[arg-type]
            except ValueError as exc:
                log.warning("Пропущен некорректный токен: %s", exc)
        log.info("Загружено токенов: %d", len(registry))
        return registry


# --------------------------------------------------------------------------- #
#  Журнал обращений
# --------------------------------------------------------------------------- #


@dataclass
class AccessLogEntry:
    timestamp: datetime
    role: Role
    action: str
    target: str

    def as_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(timespec="seconds"),
            "role": self.role,
            "action": self.action,
            "target": self.target,
        }


class AccessLog:
    """Журнал обращений к точным данным.

    Пишется и в память (для отладки и для панели), и в файл: журнал,
    который исчезает при перезапуске сервиса, журналом не является.
    """

    def __init__(self, path: str | Path | None = None, *, max_memory: int = 1000) -> None:
        self.path = Path(path) if path else None
        self.max_memory = max_memory
        self.entries: list[AccessLogEntry] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, role: Role, action: str, target: str) -> AccessLogEntry:
        entry = AccessLogEntry(datetime.now(), role, action, target)
        self.entries.append(entry)
        if len(self.entries) > self.max_memory:
            del self.entries[: len(self.entries) - self.max_memory]
        if self.path:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.as_dict(), ensure_ascii=False) + "\n")
        return entry

    def recent(self, limit: int = 50) -> list[dict]:
        return [entry.as_dict() for entry in self.entries[-limit:]]


# --------------------------------------------------------------------------- #
#  Огрубление координат
# --------------------------------------------------------------------------- #


def blur_coordinate(value: float, digits: int) -> float:
    """Округлить координату для публичной выдачи.

    Два знака после запятой — это примерно километр по широте. Точка
    перестаёт указывать на конкретный участок и начинает указывать на
    район, что и требуется публичному слою.
    """
    if digits < 0:
        raise ValueError("число знаков не может быть отрицательным")
    return round(float(value), digits)


def sanitize_for_role(record: dict, role: Role, *, precision_digits: int) -> dict:
    """Убрать из записи всё, что не положено видеть этой роли.

    Публичная роль не получает ни точных координат, ни площади, ни сумм
    ущерба, ни идентификатора объекта: по совокупности этих полей объект
    восстанавливается однозначно даже без координат.
    """
    if has_at_least(role, "operator"):
        return dict(record)

    public = {
        "risk_class": record.get("risk_class"),
        "district": record.get("district"),
    }
    if "latitude" in record and "longitude" in record:
        public["latitude"] = blur_coordinate(record["latitude"], precision_digits)
        public["longitude"] = blur_coordinate(record["longitude"], precision_digits)
    return {k: v for k, v in public.items() if v is not None}


__all__ = [
    "CAN_APPROVE_ACTS",
    "ROLE_ORDER",
    "TOKENS_ENV",
    "AccessDenied",
    "AccessLog",
    "AccessLogEntry",
    "Role",
    "TokenRegistry",
    "blur_coordinate",
    "has_at_least",
    "require",
    "role_rank",
    "sanitize_for_role",
]
