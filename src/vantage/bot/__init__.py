"""Двусторонний Telegram-контур.

    reports.py — логика: сопоставление сообщений со спутником, антиспам,
                 приватность отправителя. Тестируется без сети.
    app.py     — связывание с Telegram. Обработчики короткие и делегируют
                 работу в reports.py.

Запуск::

    set VANTAGE_BOT_TOKEN=...
    set VANTAGE_BOT_SALT=...
    set VANTAGE_BOT_SUBSCRIBERS=123456789
    python -m vantage.bot
"""

from __future__ import annotations

from .reports import CitizenReport, MatchResult, ReportStore, match_report

__all__ = ["CitizenReport", "MatchResult", "ReportStore", "match_report"]
