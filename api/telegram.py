"""Telegram-бот как бессерверная функция Vercel.

── Почему не Fly.io ────────────────────────────────────────────────────

Бот написан на long polling: он сам ходит в Telegram за обновлениями.
Такому боту нужна машина, которая не спит, — а бесплатные хостинги
усыпляют контейнер без входящих запросов. Fly.io машину даёт, но
бесплатный тариф там убрали, и нужна карта.

Webhook снимает вопрос целиком. При нём в Telegram ходить не надо:
Telegram сам присылает обновление на адрес, и между сообщениями никакой
машины не нужно вовсе. Бессерверная функция для этого и сделана — она
существует ровно во время запроса.

Сайт уже стоит на Vercel, там же живёт эта функция. Ни нового сервиса, ни
карты, ни отдельного счёта.

── Что здесь есть и чего нет ───────────────────────────────────────────

Есть: команды, приём геопозиции, сопоставление с найденными объектами,
ответ жителю, оповещение подписчиков, обезличивание отправителя.

Нет антиспама между вызовами и журнала сообщений. Бессерверная функция не
помнит ничего между запросами, а поднимать базу ради счётчика — менять
одну зависимость на другую. Ограничение честнее скрытой поломки: в
`/stats` так и написано.

── Зависимости ─────────────────────────────────────────────────────────

Только стандартная библиотека. Ни requests, ни python-telegram-bot: у
бессерверной функции время холодного старта складывается из импортов, и
каждая библиотека здесь оплачивается задержкой ответа жителю.

── Настройка ───────────────────────────────────────────────────────────

Переменные окружения задаются в Vercel (Settings → Environment Variables):

    VANTAGE_BOT_TOKEN       токен от @BotFather
    VANTAGE_BOT_SECRET      любая длинная случайная строка
    VANTAGE_BOT_SUBSCRIBERS chat_id через запятую, кому слать оповещения
    VANTAGE_BOT_SALT        соль для обезличивания отправителя

Затем один раз зарегистрировать адрес — см. deploy/README.md.
"""

import contextlib
import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

API = "https://api.telegram.org/bot{token}/{method}"

#: Ближе этого — считаем, что житель пишет про уже известный объект.
#: Сто пятьдесят метров: точность геопозиции телефона в городе — десятки
#: метров, и человек редко стоит вплотную к куче.
MATCH_RADIUS_M = 150.0

EARTH_RADIUS_M = 6_371_000.0

#: Неразрывный пробел для разрядов: обычный разорвётся переносом строки.
NBSP = " "

_CANDIDATES: list[dict] | None = None


def candidates() -> list[dict]:
    """Указатель объектов. Читается один раз на живой экземпляр функции."""
    global _CANDIDATES
    if _CANDIDATES is None:
        path = Path(__file__).with_name("candidates.json")
        try:
            _CANDIDATES = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            _CANDIDATES = []
    return _CANDIDATES


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние по поверхности сферы, метры."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def nearest(lat: float, lon: float) -> tuple[dict | None, float | None]:
    best, best_distance = None, None
    for item in candidates():
        distance = haversine_m(lat, lon, item["lat"], item["lon"])
        if best_distance is None or distance < best_distance:
            best, best_distance = item, distance
    return best, best_distance


def spaced(value: int) -> str:
    """Число с разделителями разрядов.

    Отдельной функцией, потому что первая версия заменяла запятые по всей
    строке сообщения и заодно съедала запятые в тексте: «уже известен:
    C00005  площадь 21 103 м²  найден спутником  проверка не проводилась».
    Тест показал это на первом же вызове.
    """
    return f"{value:,}".replace(",", NBSP)


def hash_sender(sender_id) -> str:
    """Обезличенный отправитель.

    Жалоба на свалку рядом с чьим-то забором не должна становиться доносом
    с именем. Соль обязательна: без неё хеш числового идентификатора
    подбирается перебором за секунды.
    """
    salt = os.environ.get("VANTAGE_BOT_SALT", "")
    return hashlib.sha256(f"{salt}:{sender_id}".encode()).hexdigest()[:16]


#: Как выглядит токен: числовой идентификатор бота, двоеточие, ключ.
TOKEN_RE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")


def token() -> str:
    """Токен, выковырянный из того, что положили в переменную.

    BotFather присылает токен внутри длинного поздравительного сообщения,
    и вставить в переменную окружения всё сообщение целиком — ошибка,
    которую делают все. Пробелы и переводы строк внутри значения приводят
    к отказу «URL can't contain control characters», а текст отказа
    содержит само значение — то есть утекает наружу.

    Поэтому здесь не проверка, а извлечение: берём из значения то, что
    похоже на токен, и работаем с ним.
    """
    raw = os.environ.get("VANTAGE_BOT_TOKEN", "")
    found = TOKEN_RE.search(raw)
    return found.group(0) if found else raw.strip()


def webhook_secret() -> str:
    """Секрет для Telegram, выведенный из заданного.

    Telegram принимает в secret_token только латиницу, цифры, дефис и
    подчёркивание, длиной до 256 символов. Человек, которому сказали
    «придумайте длинную случайную строку», вставляет туда пробелы,
    кириллицу и знаки препинания — и setWebhook отвечает 400 без
    объяснения, какое именно поле ему не понравилось.

    Поэтому в Telegram уходит не сам секрет, а его отпечаток: он всегда
    состоит из шестнадцатеричных цифр и всегда одинаковой длины. Тот же
    отпечаток проверяется на входящих обновлениях, так что сравнение
    остаётся точным.
    """
    raw = os.environ.get("VANTAGE_BOT_SECRET", "")
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hide_token(text: str) -> str:
    """Убрать токен из любого текста, который может попасть наружу.

    Страница состояния публичная. Один раз токен уже уехал в неё через
    текст ошибки от urllib — там был полный адрес запроса вместе с
    ключом. Ошибки печатать надо, ключи — нет.
    """
    value = token()
    if value:
        text = text.replace(value, "<токен скрыт>")
    return TOKEN_RE.sub("<токен скрыт>", text)


def ask(method: str, payload: dict | None = None) -> dict:
    """Вызов Telegram с возвратом ответа.

    Нужен там, где ответ важен: проверка состояния вебхука и его
    регистрация. Для отправки сообщений есть `call`, который ответ
    выбрасывает.
    """
    value = token()
    if not value:
        return {"ok": False, "description": "VANTAGE_BOT_TOKEN не задан"}
    request = urllib.request.Request(
        API.format(token=value, method=method),
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        return json.loads(urllib.request.urlopen(request, timeout=10).read())
    except urllib.error.HTTPError as error:
        # У Telegram причина отказа лежит в ТЕЛЕ ответа, а не в коде.
        # Без чтения тела диагностика сводится к «400 Bad Request», по
        # которому нельзя понять ничего.
        try:
            body = json.loads(error.read())
            return {"ok": False, "description": hide_token(str(body.get("description", body)))}
        except Exception:  # тело не разобралось — отдаём хоть код
            return {"ok": False, "description": hide_token(str(error))}
    except Exception as error:
        return {"ok": False, "description": hide_token(str(error))}


def call(method: str, payload: dict) -> None:
    """Вызов Telegram без разбора ответа.

    Telegram недоступен — повторять нечем и некогда: функция живёт доли
    секунды, а житель уже отправил сообщение.
    """
    with contextlib.suppress(Exception):
        ask(method, payload)


def send(chat_id, text: str) -> None:
    call("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML"})


def subscribers() -> list[str]:
    raw = os.environ.get("VANTAGE_BOT_SUBSCRIBERS", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


HELP = (
    "<b>Vantage AI</b> — свалки из космоса.\n\n"
    "Пришлите <b>геопозицию</b> (скрепка → Геопозиция), и я скажу, "
    "знаком ли системе объект в этом месте.\n\n"
    "Спутник не видит кучи меньше тридцати квадратных метров — "
    "их видите только вы.\n\n"
    "/stats — сколько объектов найдено"
)

#: Что известно об объекте после проверки по снимку высокого разрешения.
#: Формулировки разные не для красоты: «проверен, и это не свалка» —
#: полезный ответ, а «найден спутником» без проверки обещает меньше.
STATUS = {
    "landfill": "проверен по снимку высокого разрешения — это свалка",
    "not_landfill": "проверен по снимку: это не свалка, а постройка или площадка",
    "unclear": "найден спутником, по снимку разобрать не удалось — нужен выезд",
}
STATUS_UNKNOWN = "найден спутником, проверка по снимку ещё не проводилась"


def on_location(message: dict) -> None:
    location = message["location"]
    lat, lon = float(location["latitude"]), float(location["longitude"])
    chat_id = message["chat"]["id"]
    sender = hash_sender(message.get("from", {}).get("id", "?"))

    item, distance = nearest(lat, lon)

    if item and distance is not None and distance <= MATCH_RADIUS_M:
        status = STATUS.get(item.get("visual_check"), STATUS_UNKNOWN)
        send(
            chat_id,
            f"Спасибо. Объект в этом месте уже известен: <b>{item['id']}</b>, "
            f"площадь {spaced(item['area_m2'])} м², {status}.\n\n"
            "Ваше сообщение — независимое подтверждение, оно повышает "
            "приоритет на выезд.",
        )
        head = f"Подтверждение известного объекта {item['id']}"
    else:
        send(
            chat_id,
            "Спасибо. Объекта по этим координатам в системе не было — "
            "добавили на проверку.\n\nТакие сообщения ценнее всего: спутник "
            "не разрешает объекты меньше нескольких десятков метров, и "
            "закрыть эту дыру может только человек на месте.",
        )
        head = "Новый объект, спутником не найден"

    nearby = "—"
    if item and distance is not None:
        nearby = f"{item['id']} ({distance:.0f} м)"

    for chat in subscribers():
        send(
            chat,
            f"<b>{head}</b>\n"
            f"Координаты: <code>{lat:.5f}, {lon:.5f}</code>\n"
            f"Ближайший известный: {nearby}\n"
            f"Отправитель: <code>{sender}</code>\n"
            f"Фото: {'есть' if message.get('photo') else 'нет'}",
        )


def on_update(update: dict) -> None:
    message = update.get("message") or update.get("edited_message")
    if not message:
        return
    chat_id = message["chat"]["id"]

    if "location" in message:
        on_location(message)
        return

    text = (message.get("text") or "").strip().lower()

    if text.startswith(("/start", "/help")):
        send(chat_id, HELP)
    elif text.startswith("/stats"):
        items = candidates()
        confirmed = sum(1 for item in items if item.get("visual_check") == "landfill")
        send(
            chat_id,
            f"Найдено объектов: <b>{len(items)}</b>\n"
            f"Подтверждено проверкой по снимку: <b>{confirmed}</b>\n\n"
            "Счётчик сообщений жителей не ведётся: бот работает без "
            "постоянного хранилища.",
        )
    elif message.get("photo"):
        send(
            chat_id,
            "Фотография получена, но без координат она мало что даёт: "
            "по снимку нельзя понять, где это. Пришлите геопозицию "
            "(скрепка → Геопозиция) — тогда объект попадёт в систему.",
        )
    else:
        send(chat_id, HELP)


# Имя класса и методов задано Vercel и BaseHTTPRequestHandler, а не нами.
class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        # Секрет проверяется до разбора тела: адрес функции публичный, и
        # без проверки любой мог бы прислать поддельное обновление.
        secret = webhook_secret()
        if secret and self.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
            self.send_response(403)
            self.end_headers()
            return

        # Telegram повторяет обновление при любом ответе кроме 200, и на
        # разборе одного битого сообщения бот залипал бы навсегда.
        with contextlib.suppress(Exception):
            length = int(self.headers.get("Content-Length") or 0)
            on_update(json.loads(self.rfile.read(length) or b"{}"))

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_GET(self) -> None:
        """Состояние бота и, по запросу, регистрация вебхука.

        Раньше здесь было только «функция жива». Этого мало: когда бот
        молчит, причина почти всегда в вебхуке — он не зарегистрирован,
        или зарегистрирован на другой адрес, или секрет не совпал и все
        обновления отбиваются с 403. Снаружи все три случая выглядят
        одинаково: тишина.

        Спросить у Telegram может сама функция — токен у неё есть. Одна
        страница вместо переписки «а что вы вводили».
        """
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        secret_given = bool(os.environ.get("VANTAGE_BOT_SECRET", ""))
        lines: list[str] = []

        # Регистрация вебхука прямо отсюда, и БЕЗ проверки секрета.
        #
        # Сначала секрет требовался, и это была ловушка: функция помнит
        # переменные окружения с момента сборки, а не текущие. Поменяли
        # секрет в интерфейсе Vercel — функция всё ещё сравнивает со
        # старым, и правильно введённый новый не подходит. Снаружи это
        # выглядит как «ввожу верное, а оно не работает».
        #
        # Проверять здесь и нечего. Действие ведёт вебхук на СОБСТВЕННЫЙ
        # адрес функции — тот, по которому пришёл этот самый запрос.
        # Перенаправить бота на чужой сервер им нельзя, повторный вызов
        # ничего не меняет. Единственное, что защищать стоило, — приём
        # обновлений, и он защищён secret_token в do_POST.
        if query.get("action") == ["set"]:
            host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or ""
            url = f"https://{host}/api/telegram"
            payload = {"url": url, "drop_pending_updates": True}
            if webhook_secret():
                payload["secret_token"] = webhook_secret()
            result = ask("setWebhook", payload)
            lines.append(
                f"Регистрация вебхука на {url}: "
                f"{'успешно' if result.get('ok') else result.get('description')}"
            )
            lines.append("")

        info = ask("getWebhookInfo").get("result", {})
        lines += [
            "Vantage AI bot",
            f"Объектов в указателе: {len(candidates())}",
            f"Токен задан: {'да' if token() else 'НЕТ'}",
            f"Секрет задан: {'да' if secret_given else 'НЕТ'}",
            f"Подписчиков: {len(subscribers()) or 'НЕТ'}",
            "",
            f"Вебхук: {info.get('url') or 'НЕ ЗАРЕГИСТРИРОВАН'}",
            # Задан ли secret_token, Telegram не сообщает вовсе. Здесь
            # раньше стояло has_custom_certificate — это про SSL-сертификат,
            # к секрету отношения не имеет, и строка показывала «да» просто
            # потому, что вебхук существует. Врущая строка в диагностике
            # хуже отсутствующей: по ней и принимают неверные решения.
            f"Ждёт обновлений с: {info.get('ip_address') or '—'}",
            f"Необработанных обновлений: {info.get('pending_update_count', '—')}",
            f"Последняя ошибка: {info.get('last_error_message') or 'нет'}",
        ]

        if not info.get("url"):
            lines += [
                "",
                "Вебхук не зарегистрирован — поэтому бот молчит.",
                "Откройте этот же адрес с ?action=set — и всё.",
            ]

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(hide_token(chr(10).join(lines)).encode())
