"""Снять картинки для питч-деки прямо с работающего сайта.

── Зачем ───────────────────────────────────────────────────────────────

Деку собирает человек, и это правильно. Но картинки в ней должны быть из
настоящего прогона, а не нарисованные заново: нарисованные расходятся с
сайтом при первом же пересчёте, и расхождение находят на защите.

Здесь снимаются именно те блоки, которые несут доказательство, каждый
отдельным файлом и в двойном разрешении — чтобы на проекторе не сыпался
текст.

── Что снимается ───────────────────────────────────────────────────────

    pixel        график вегетации одного пикселя за восемь лет — ядро метода
    signals      пять независимых признаков и что каждый отсекает
    funnel       воронка отсева: сколько кандидатов и почему выбыли
    money        когда объекты появились и сколько стоит каждый
    scale        измеренная скорость: сто два километра в час
    limits       чего система не может — названное самими
    map          рабочая карта со списком
    forecast     прогноз: точки объезда вместо раскрашенной области
    timelapse    восемь лет за двадцать секунд
    citizen      сколько лет объект лежит незамеченным
    economy      из чего складываются потери: вывоз − вторсырьё + климат
    priority     очередь по деньгам и накопленная доля суммы

    python scripts/deck_assets.py
"""

import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DIST = Path("web-next/dist")
OUT = Path("docs/deck")
PORT = 8095

#: Блок и селектор, по которому он ищется. Селекторы намеренно простые:
#: сложный «третий section внутри main» ломается от любой перестановки, а
#: пропавшая картинка в наборе для деки заметна не сразу.
#: Порядковые номера разделов сверены с живой страницей 23 августа: раздел
#: отбраковок с лендинга убран, добавлен раздел про масштаб, и прежние
#: номера снимали не то. Снимок «rejected» приходил весом 86 КБ — пустой
#: блок, и это было видно только по размеру файла.
#: Номера разделов сдвинулись 24 августа: на лендинг вторым добавлен
#: раздел «свалка — это ресурс, за который платят дважды», и всё, что
#: ниже, съехало на единицу. Селекторы правились вместе с ним — иначе
#: набор собирается молча и не тем.
SHOTS: tuple[tuple[str, str, str], ...] = (
    ("pixel",    "index.html",    "main > section:nth-of-type(1)"),   # график вегетации
    ("signals",  "index.html",    "main > section:nth-of-type(4)"),   # пять признаков
    ("funnel",   "index.html",    "main > section:nth-of-type(5)"),   # воронка отсева
    ("money",    "index.html",    "main > section:nth-of-type(6)"),   # когда и почём
    ("scale",    "index.html",    "main > section:nth-of-type(8)"),   # масштаб
    ("limits",   "index.html",    "main > section:nth-of-type(9)"),   # границы
    ("citizen",  "citizen.html",  "section:nth-of-type(1)"),
    # Раскладка потерь на слагаемые: вывоз минус вторсырьё плюс климат.
    # Это ответ на кейс трека, и на слайде он должен быть снимком
    # работающего экрана, а не таблицей, набранной в деке заново.
    ("economy",  "economy.html",  "main > section:nth-of-type(2)"),
    # Приоритет: список, отсортированный по деньгам, с накопленной долей.
    ("priority", "economy.html",  "main > section:nth-of-type(3)"),
)

#: Страницы, снимаемые целиком: карта и прогноз — это интерфейс, и резать
#: их на куски бессмысленно.
FULL: tuple[tuple[str, str], ...] = (
    ("map", "map.html"),
    ("forecast", "forecast.html"),
    ("timelapse", "timelapse.html"),
)


def serve(directory: Path, port: int) -> subprocess.Popen:
    process = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--directory", str(directory)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=1).read(1)
            return process
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    process.terminate()
    raise RuntimeError("локальный сервер не поднялся")


def main() -> int:
    if not (DIST / "index.html").exists():
        print(f"нет собранного сайта в {DIST}")
        return 1

    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    server = serve(DIST, PORT)
    made = 0
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            # Двойное разрешение: слайд растягивается на весь экран
            # проектора, и снимок один в один по пикселям расплывается.
            context = browser.new_context(viewport={"width": 1440, "height": 900},
                                          device_scale_factor=2)

            for name, page_name, selector in SHOTS:
                page = context.new_page()
                page.goto(f"http://{'127.0.0.1'}:{PORT}/{page_name}",
                          wait_until="networkidle", timeout=60_000)
                page.wait_for_timeout(5000)
                # Липкая шапка накрывает верх блока, когда Playwright
                # прокручивает его в вид: заголовок уезжает под навигацию
                # и на слайде выглядит обрезанным. На живом сайте это
                # нормальное поведение, в снимке для деки — брак.
                page.add_style_tag(content=".sticky { position: static !important; }")
                page.wait_for_timeout(300)
                try:
                    page.locator(selector).first.screenshot(path=str(OUT / f"{name}.png"))
                    print(f"снят: {name}.png")
                    made += 1
                except Exception as error:
                    print(f"НЕ снят: {name} — {str(error)[:90]}")
                page.close()

            for name, page_name in FULL:
                page = context.new_page()
                page.goto(f"http://127.0.0.1:{PORT}/{page_name}",
                          wait_until="networkidle", timeout=60_000)
                # Карты грузят подложку после первой отрисовки; без паузы
                # в кадр попадают серые квадраты.
                page.wait_for_timeout(8000)
                page.screenshot(path=str(OUT / f"{name}.png"))
                print(f"снят: {name}.png")
                made += 1
                page.close()

            browser.close()
    finally:
        server.terminate()

    print()
    print(f"── Готово: {made} картинок в {OUT} ──")
    print("Вставлять как есть. Перерисовывать не надо: числа на них из прогона,")
    print("и нарисованные заново разойдутся с сайтом при первом пересчёте.")
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())
