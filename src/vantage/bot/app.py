"""Telegram-бот: двусторонний канал между системой и людьми.

Исходящий поток — оповещения ответственному лицу: найден новый объект,
зона вышла в высокий риск, объект помечен как вероятно ликвидированный.

Входящий поток — сообщения жителей: фото и геолокация подозрительной
свалки. Каждое сверяется со спутниковыми кандидатами (см.
:mod:`vantage.bot.reports`).

Вся содержательная логика вынесена в ``reports.py`` и покрыта тестами
без сети. Здесь только связывание с Telegram: обработчики короткие и
делегируют работу наружу. Это не стилистика — это условие того, чтобы
логику вообще можно было проверить: тест, требующий живого бота и токена,
на защите не запустится.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass

from .reports import (
    CitizenReport,
    RateLimited,
    ReportStore,
    confidence_after_confirmation,
    hash_sender,
    match_report,
)

log = logging.getLogger(__name__)

TOKEN_ENV = "VANTAGE_BOT_TOKEN"
SUBSCRIBERS_ENV = "VANTAGE_BOT_SUBSCRIBERS"

WELCOME = (
    "Это бот системы VANTAGE. Мы ищем несанкционированные свалки "
    "по спутниковым снимкам вокруг Астаны.\n\n"
    "Если вы видите свалку — пришлите её геолокацию (скрепка → Геопозиция) "
    "и по возможности фото. Мы сверим точку с данными спутника и передадим "
    "информацию в экологическую службу.\n\n"
    "Ваш идентификатор не сохраняется: мы храним только его необратимый хеш, "
    "чтобы ограничить спам."
)

HELP = (
    "Как сообщить о свалке:\n"
    "1. Скрепка → Геопозиция → отправить точку объекта\n"
    "2. Следом можно отправить фото\n\n"
    "Команды:\n"
    "/start — начало\n"
    "/help — эта справка\n"
    "/stats — сколько сообщений получено\n\n"
    "Важно: система даёт оценку вероятности, а не результат проверки. "
    "Решение принимает уполномоченное лицо после выезда."
)

NEED_LOCATION = (
    "Чтобы принять сообщение, нужна геолокация. "
    "Нажмите скрепку и выберите «Геопозиция» — так мы поймём, где именно объект."
)


@dataclass
class BotContext:
    """Всё, что нужно обработчикам: хранилище, кандидаты, подписчики."""

    store: ReportStore
    candidates: object | None = None
    subscribers: tuple[int, ...] = ()

    def candidate_probability(self, candidate_id: str) -> float | None:
        if self.candidates is None or not len(self.candidates):
            return None
        match = self.candidates[self.candidates["candidate_id"] == candidate_id]
        if match.empty or "probability" not in match.columns:
            return None
        return float(match.iloc[0]["probability"])


# --------------------------------------------------------------------------- #
#  Обработка сообщения — чистая функция, тестируется без Telegram
# --------------------------------------------------------------------------- #


def handle_location(
    context: BotContext,
    *,
    sender_id: str | int,
    latitude: float,
    longitude: float,
    photo_id: str | None = None,
    comment: str = "",
) -> tuple[str, CitizenReport | None]:
    """Обработать сообщение с координатами.

    Возвращает (текст ответа жителю, принятое сообщение или None).
    Никаких обращений к Telegram — только логика.
    """
    sender = hash_sender(sender_id)
    report = CitizenReport(
        report_id=uuid.uuid4().hex[:12],
        sender_hash=sender,
        latitude=latitude,
        longitude=longitude,
        photo_id=photo_id,
        comment=comment,
    )

    try:
        context.store.add(report)
    except RateLimited as exc:
        return f"Сообщение не принято: {exc}", None

    match = match_report(report, context.candidates)
    context.store.apply_match(report, match)

    reply = match.to_user_text()
    if match.matched and match.candidate_id:
        confirmations = context.store.confirmations_for(match.candidate_id)
        updated = confidence_after_confirmation(
            context.candidate_probability(match.candidate_id), confirmations
        )
        log.info(
            "Сообщение %s подтвердило объект %s (подтверждений: %d, уверенность: %.2f)",
            report.report_id, match.candidate_id, confirmations, updated,
        )
    else:
        log.info("Сообщение %s: новый объект вне спутниковых кандидатов", report.report_id)

    return reply, report


def format_new_candidate_alert(candidate_row) -> str:
    """Оповещение службе о новом объекте."""
    point = candidate_row.geometry.representative_point()
    area = float(candidate_row.get("area_m2", 0.0))
    probability = candidate_row.get("probability")
    lines = [
        "Обнаружен новый объект",
        f"Координаты: {point.y:.6f}, {point.x:.6f}",
        f"Площадь: {area:,.0f} м²".replace(",", " "),
    ]
    if probability is not None:
        lines.append(f"Оценка модели: {float(probability):.0%}")
    date = candidate_row.get("break_date")
    if date is not None and str(date) not in {"NaT", "None"}:
        lines.append(f"Возник: {str(date)[:10]}")
    lines.append("Статус: требует проверки. Акт формируется по запросу.")
    return "\n".join(lines)


def format_citizen_alert(report: CitizenReport, matched: bool) -> str:
    """Оповещение службе о сообщении жителя."""
    header = "Подтверждение от жителя" if matched else "Новый объект от жителя (спутник не видел)"
    return (
        f"{header}\n"
        f"Координаты: {report.latitude:.6f}, {report.longitude:.6f}\n"
        f"Фото: {'есть' if report.has_photo else 'нет'}\n"
        f"Комментарий: {report.comment or '—'}"
    )


# --------------------------------------------------------------------------- #
#  Связывание с Telegram
# --------------------------------------------------------------------------- #


def build_application(context: BotContext, token: str | None = None):
    """Собрать приложение python-telegram-bot.

    Токен берётся из окружения. Библиотека импортируется здесь, а не на
    уровне модуля: логика гражданского контура должна тестироваться
    и работать без установленного telegram-пакета.
    """
    from telegram import Update
    from telegram.ext import (
        Application,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    token = token or os.environ.get(TOKEN_ENV)
    if not token:
        raise RuntimeError(
            f"не задан токен бота. Установите переменную окружения {TOKEN_ENV}."
        )

    application = Application.builder().token(token).build()

    async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(WELCOME)

    async def help_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(HELP)

    async def stats(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        data = context.store.stats()
        await update.message.reply_text(
            f"Получено сообщений: {data['total']}\n"
            f"Подтвердили известные объекты: {data['matched']}\n"
            f"Нашли новые: {data['new_objects']}"
        )

    async def on_location(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        location = update.message.location
        reply, report = handle_location(
            context,
            sender_id=update.effective_user.id,
            latitude=location.latitude,
            longitude=location.longitude,
            comment=update.message.caption or "",
        )
        await update.message.reply_text(reply)

        if report is not None:
            alert = format_citizen_alert(report, report.status == "matched")
            for chat_id in context.subscribers:
                try:
                    await application.bot.send_message(chat_id=chat_id, text=alert)
                except Exception as exc:
                    log.warning("Не удалось отправить оповещение в %s: %s", chat_id, exc)

    async def on_photo_without_location(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(NEED_LOCATION)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(MessageHandler(filters.LOCATION, on_location))
    application.add_handler(MessageHandler(filters.PHOTO, on_photo_without_location))
    return application


def subscribers_from_env(variable: str = SUBSCRIBERS_ENV) -> tuple[int, ...]:
    """Список chat_id для оповещений: ``123456,789012``."""
    raw = os.environ.get(variable, "").strip()
    if not raw:
        return ()
    result = []
    for part in raw.split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            result.append(int(part))
        else:
            log.warning("Пропущен некорректный chat_id: %r", part)
    return tuple(result)


__all__ = [
    "HELP",
    "NEED_LOCATION",
    "SUBSCRIBERS_ENV",
    "TOKEN_ENV",
    "WELCOME",
    "BotContext",
    "build_application",
    "format_citizen_alert",
    "format_new_candidate_alert",
    "handle_location",
    "subscribers_from_env",
]
