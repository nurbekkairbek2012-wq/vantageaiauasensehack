"""Сгенерировать QR на Telegram-бота и записать настройки сайта.

Зачем QR вообще. Гражданский контур работает только если о нём знают, а
единственный момент, когда о нём узнаёт целый зал, — это защита. Ссылку с
экрана никто не наберёт, QR сканируют за секунду.

Имя бота НЕ придумывается. Его задаёт тот, кто создал бота у @BotFather, и
пока оно не передано, сайт честно показывает инструкцию вместо кнопки —
кнопка на несуществующего бота хуже, чем её отсутствие.

    python scripts/make_bot_qr.py @vantage_astana_bot
"""

import json
import re
import sys
from pathlib import Path

OUT = Path("web-next/public/data")
OUT.mkdir(parents=True, exist_ok=True)
CONFIG = OUT / "site.json"
QR = Path("web-next/public/bot-qr.svg")

if len(sys.argv) < 2:
    raise SystemExit(
        "укажите имя бота: python scripts/make_bot_qr.py @vantage_astana_bot\n"
        "Имя даёт @BotFather при создании; выдумывать его нельзя."
    )

username = sys.argv[1].lstrip("@").strip()
if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
    raise SystemExit(f"«{username}» не похоже на имя бота в Telegram (5–32 символа, латиница, цифры, _)")

link = f"https://t.me/{username}"

import segno

# scale=1 и никакой рамки: SVG масштабируется вёрсткой, а тихая зона
# добавляется отступом в CSS — так QR остаётся резким на любом экране.
segno.make(link, error="m").save(
    str(QR), kind="svg", scale=1, border=2,
    dark="#0d0918", light="#f5f3ff", svgclass=None, lineclass=None,
)

CONFIG.write_text(
    json.dumps(
        {
            "telegram_bot": username,
            "telegram_link": link,
            "qr": "./bot-qr.svg",
        },
        ensure_ascii=False,
        indent=1,
    ),
    encoding="utf-8",
)

print(f"бот: {link}")
print(f"QR: {QR} ({QR.stat().st_size} байт)")
print(f"настройки сайта: {CONFIG}")
