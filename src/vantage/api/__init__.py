"""HTTP-сервис VANTAGE.

    security.py — роли, токены, журнал обращений, огрубление координат
    store.py    — чтение предрассчитанных артефактов пайплайна
    app.py      — сборка приложения FastAPI

FastAPI импортируется лениво внутри :func:`create_app`: базовый пайплайн
должен работать без веб-зависимостей.

Запуск::

    export VANTAGE_API_TOKENS="dev-operator-token:operator,dev-akimat-token:akimat"
    uvicorn vantage.api:app --reload
"""

from __future__ import annotations

from .security import AccessLog, Role, TokenRegistry
from .store import Store

__all__ = ["AccessLog", "Role", "Store", "TokenRegistry", "create_app"]


def create_app(**kwargs):
    """Ленивая обёртка: не тянет FastAPI, пока приложение не собирают."""
    from .app import create_app as _create_app

    return _create_app(**kwargs)
