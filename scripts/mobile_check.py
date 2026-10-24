"""Проверить страницы на узких экранах — числом, а не на глаз.

── Зачем ───────────────────────────────────────────────────────────────

Половина жюри откроет сайт с телефона. Дважды за две ночи это уже давало
конкретные поломки: лендинг переливался вбок на 382 пикселя, переключатель
подложек рвался на три строки, а карта на карточном экране уезжала под
сгиб — открыв страницу, человек видел список и не понимал, что есть карта.

На глаз такое ловится плохо: браузер разработчика показывает страницу
шире, чем телефон, а перелив вбок заметен только если специально
прокрутить.

── Что меряется ────────────────────────────────────────────────────────

Три числа на каждый экран:

    перелив по горизонтали  — scrollWidth больше clientWidth значит, что
                              страницу можно утащить вбок; это всегда
                              поломка вёрстки, а не решение дизайнера;
    высота до карты         — сколько пикселей надо прокрутить, чтобы
                              увидеть главное; больше одного экрана плохо;
    ошибки в консоли        — то же, что в дымовом тесте, но на узком
                              экране срабатывают другие ветки кода.

    python scripts/mobile_check.py
"""

import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "web-next" / "dist"
PORT = 4183

#: Экраны, на которых проверяем. 360 — самый узкий из живых Android,
#: 390 — iPhone 14/15, 768 — планшет книжной ориентации.
SCREENS = ((360, 800), (390, 844), (768, 1024))

PAGES = ("index.html", "map.html", "forecast.html", "citizen.html", "timelapse.html")


def main() -> int:
    if not DIST.exists():
        print(f"нет сборки {DIST} — сначала npm run build")
        return 1

    from playwright.sync_api import sync_playwright

    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(PORT), "-d", str(DIST)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(1.5)
    bad = 0
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for width, height in SCREENS:
                print(f"\n── {width}x{height} " + "─" * 40)
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=2, is_mobile=True, has_touch=True)
                for name in PAGES:
                    page = context.new_page()
                    errors: list[str] = []
                    page.on("pageerror", lambda e, sink=errors: sink.append(str(e)))
                    page.goto(f"http://127.0.0.1:{PORT}/{name}", wait_until="networkidle")
                    page.wait_for_timeout(700)

                    overflow = page.evaluate(
                        "() => document.documentElement.scrollWidth - "
                        "document.documentElement.clientWidth")
                    # Насколько глубоко лежит карта. Ищем и Leaflet, и любой
                    # элемент с ролью карты: на разных страницах он разный.
                    map_top = page.evaluate("""() => {
                        const el = document.querySelector('.leaflet-container, [data-map], canvas');
                        if (!el) return -1;
                        return Math.round(el.getBoundingClientRect().top + window.scrollY);
                    }""")

                    flag = ""
                    if overflow > 0:
                        flag += f"  ПЕРЕЛИВ {overflow}px"
                        bad += 1
                    # Глубину карты проверяем только там, где карта —
                    # главное на странице. На лендинге она законно лежит
                    # разделом ниже, и требовать её на первом экране значило
                    # бы ломать вёрстку ради метрики.
                    if name == "map.html" and map_top > height:
                        flag += f"  карта под сгибом ({map_top}px)"
                        bad += 1
                    if errors:
                        flag += f"  ОШИБОК {len(errors)}"
                        bad += 1
                    mark = "OK " if not flag else "!! "
                    where = f"карта на {map_top}px" if map_top >= 0 else "карты нет"
                    print(f"  {mark}{name:16s} перелив {overflow:3d}px  {where}{flag}")
                    page.close()
                context.close()
            browser.close()
    finally:
        server.terminate()

    print()
    if bad:
        print(f"── Найдено проблем: {bad} ──")
    else:
        print("── Узкие экраны чистые ──")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
