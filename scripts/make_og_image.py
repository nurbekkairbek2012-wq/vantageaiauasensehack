"""Картинка превью ссылки и метатеги страниц — из данных, а не руками.

── Зачем ───────────────────────────────────────────────────────────────

Ссылку на продукт отправляют в WhatsApp и Telegram — чиновнику, жюри,
фонду. Без метатегов Open Graph мессенджер показывает голый адрес, и
человек решает, открывать ли, по строке «hakathon-lyart.vercel.app».

Здесь собираются:

  web-next/public/og.png  — 1200 × 630, три числа из выгрузки;
  <meta> в каждой из семи страниц — заголовок, описание, картинка.

Числа на картинке берутся из economy.json: вписанные руками разошлись бы
с сайтом при первом пересчёте, а на превью это увидят раньше, чем сайт.

    python scripts/make_og_image.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web-next"
DATA = WEB / "public/data"
LIVE = "https://hakathon-lyart.vercel.app"

PAGES = {
    "index.html": ("Vantage AI — экологические потери в тенге",
                   "ИИ находит несанкционированные свалки по спутниковым снимкам, считает "
                   "потери в тенге и выстраивает очередь выезда по деньгам."),
    "map.html": ("Vantage AI — карта объектов",
                 "Очередь выезда по деньгам: объекты, снимки до и после, ущерб, черновик акта."),
    "economy.html": ("Vantage AI — экономика",
                     "Сколько стоит вывоз, сколько вернётся вторсырьём и что выгоднее сделать."),
    "timelapse.html": ("Vantage AI — как росло",
                       "Восемь лет спутникового архива за двадцать секунд."),
    "forecast.html": ("Vantage AI — прогноз",
                      "Где риск появления новой свалки выше всего в ближайшие 12 месяцев."),
    "citizen.html": ("Vantage AI — жителям",
                     "Сообщить о свалке через Telegram: точка и фото закрывают то, чего не "
                     "видит спутник."),
    "label.html": ("Vantage AI — разметка", "Инструмент разметки объектов по снимкам."),
}


def ru(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


def card_html(economy: dict) -> str:
    sys.path.insert(0, str(ROOT / "scripts"))
    from fontfaces import font_faces

    s = economy["totals"]["sum_of_medians"]
    q = economy["queue"]
    # Все наборы символов: только кириллица роняла латиницу и цифры в засечки.
    style = font_faces("Oswald", "oswald", (600,)) + font_faces("Golos", "golos-text", (400,))
    stats = [
        (str(q["confirmed"]), "свалок опознано по снимку"),
        (f"{ru(s['mass_t'])} т", "отходов на них"),
        (f"{ru(s['removal_kzt'] / 1e6, 1)} млн ₸", "стоит вывоз"),
    ]
    cells = "".join(f"<div class='c'><div class='n'>{n}</div><div class='l'>{label}</div></div>"
                    for n, label in stats)
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>{style}
    *{{margin:0;padding:0;box-sizing:border-box}}
    body{{width:1200px;height:630px;background:#0d0918;color:#ede9fe;font-family:'Golos',sans-serif;
         padding:64px 72px;display:flex;flex-direction:column;justify-content:space-between;
         background-image:radial-gradient(60% 60% at 70% 20%,rgba(124,58,237,.35),rgba(13,9,24,0) 70%)}}
    .k{{font-family:'Oswald';letter-spacing:.16em;text-transform:uppercase;color:#a78bfa;font-size:22px}}
    h1{{font-family:'Oswald';font-weight:600;font-size:74px;line-height:1.02;margin-top:18px}}
    .row{{display:flex;gap:56px}} .n{{font-family:'Oswald';font-weight:600;font-size:54px;color:#a78bfa}}
    .l{{font-size:22px;color:#b3a5d9;margin-top:6px}}
    .f{{font-size:20px;color:#8578ad}}
    </style></head><body>
    <div><div class="k">Vantage AI · Астана</div>
    <h1>Находим экологические потери,<br>пока они дешёвые</h1></div>
    <div class="row">{cells}</div>
    <div class="f">Спутник находит · ИИ отсеивает · человек решает · {q['pending']} объектов ждут выезда</div>
    </body></html>"""


def render(html: str, out: Path) -> None:
    from playwright.sync_api import sync_playwright

    tmp = ROOT / "_og.html"
    tmp.write_text(html, encoding="utf-8")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 630})
            page.goto(tmp.as_uri())
            page.wait_for_timeout(600)
            page.screenshot(path=str(out))
            browser.close()
    finally:
        tmp.unlink(missing_ok=True)


def tag_pages() -> None:
    """Вписать метатеги в <head> каждой страницы; повторный запуск их обновляет."""
    for name, (title, description) in PAGES.items():
        path = WEB / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        text = text.replace('<html lang="en">', '<html lang="ru">')
        # Старый блок снимается целиком, чтобы повторный запуск его обновлял,
        # а не дописывал второй рядом.
        text = re.sub(r"\n    <!-- og:begin.*?<!-- og:end -->", "", text, flags=re.S)
        url = f"{LIVE}/{'' if name == 'index.html' else name}"
        block = "\n".join([
            "",
            "    <!-- og:begin — вписано scripts/make_og_image.py, руками не править -->",
            f'    <meta name="description" content="{description}" />',
            '    <meta property="og:type" content="website" />',
            '    <meta property="og:site_name" content="Vantage AI" />',
            f'    <meta property="og:title" content="{title}" />',
            f'    <meta property="og:description" content="{description}" />',
            f'    <meta property="og:url" content="{url}" />',
            f'    <meta property="og:image" content="{LIVE}/og.png" />',
            '    <meta property="og:image:width" content="1200" />',
            '    <meta property="og:image:height" content="630" />',
            '    <meta property="og:locale" content="ru_RU" />',
            '    <meta name="twitter:card" content="summary_large_image" />',
            "    <!-- og:end -->",
        ])
        text = re.sub(r"(\s*<title>)", block + r"\1", text, count=1)
        path.write_text(text, encoding="utf-8")


def main() -> int:
    economy = json.loads((DATA / "economy.json").read_text(encoding="utf-8"))
    render(card_html(economy), WEB / "public/og.png")
    tag_pages()
    print("── превью ссылки: web-next/public/og.png, метатеги в семи страницах")
    return 0


if __name__ == "__main__":
    sys.exit(main())
