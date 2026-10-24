"""Собрать PDF из HTML-исходников в docs/print.

Почему через браузер, а не через библиотеку вроде reportlab. Документы
нужно и читать с экрана, и печатать, и править словами — HTML для этого
удобнее всего: правка текста не требует пересборки вёрстки. Браузер уже
установлен ради снимков интерфейса, второй зависимости не появляется.

Шрифты тянутся из сети при сборке и запекаются в PDF, так что готовый
файл открывается везде одинаково.

    python scripts/make_pdfs.py
"""

import sys
from pathlib import Path

SOURCE = Path("docs/print")
TARGET = Path("docs")

PAGES = [
    ("project.html", "Vantage AI — о проекте.pdf"),
    ("script.html", "Vantage AI — сценарий видео.pdf"),
]


def main() -> int:
    from playwright.sync_api import sync_playwright

    missing = [name for name, _ in PAGES if not (SOURCE / name).exists()]
    if missing:
        print("нет исходников:", ", ".join(missing))
        return 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        for name, out in PAGES:
            page.goto((SOURCE / name).resolve().as_uri(), wait_until="networkidle")
            # Шрифты Google подгружаются асинхронно; без ожидания первая
            # сборка выходит системным шрифтом и заметно другой по объёму.
            page.wait_for_timeout(2500)
            target = TARGET / out
            page.pdf(
                path=str(target),
                format="A4",
                print_background=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
            size = target.stat().st_size // 1024
            print(f"{out}: {size} КБ")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
