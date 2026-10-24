"""Пройти по всем страницам собранного сайта и поймать то, что упало.

── Зачем ───────────────────────────────────────────────────────────────

Одна белая страница на защите стоит дороже любой функции. Проверяющий не
станет разбираться, что сломалось и почему, — он закроет вкладку и
поставит балл за то, что увидел.

Проверять руками шесть страниц после каждого прогона никто не будет: это
скучно, и потому это не делается. Здесь то же самое делается за минуту и
не забывается.

── Что именно ловится ──────────────────────────────────────────────────

Ошибки в консоли          — исключения JavaScript, упавшие компоненты;
неудачные запросы         — отсутствующий data/*.geojson после пересчёта;
пустая страница           — сборка прошла, а разметка не отрисовалась;
отсутствие ключевых слов  — страница открылась, но данные не доехали.

Тайлы подложки в неудачные запросы не считаются: поставщик снимков
регулярно отдаёт 404 на отдельные квадраты, это его нормальная работа, а
не наша поломка.

Отменённые запросы (ERR_ABORTED) тоже не считаются, и это не послабление.
Отмена — действие самой страницы, а не отказ сервера: браузер так
сообщает, например, о завершённом HEAD без тела. Первый запуск теста
объявил поломкой работающую проверку наличия ролика — файл лежал на
месте, отдавался с кодом 200, и страница правильно показывала «Скачать
видео». Тест, который кричит на исправное, перестают читать.

    python scripts/smoke.py
"""

import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Вывод форсируется в UTF-8. Без этого скрипт падает на собственной
# рамке: запущенный из цепочки, он получает консольную кодировку cp1251,
# в которой символов ── просто нет, и UnicodeEncodeError убивает отчёт
# ПОСЛЕ того, как проверка уже прошла, — то есть теряется именно
# результат, ради которого всё запускалось.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DIST = Path("web-next/dist")
PORT = 8098

#: Страница и слово, которое обязано на ней появиться. Слово выбрано так,
#: чтобы оно приходило ИЗ ДАННЫХ, а не из вёрстки: заголовок отрисуется и
#: при пустом geojson, а число объектов — нет.
PAGES: tuple[tuple[str, str], ...] = (
    ("index.html", "объектов"),
    ("map.html", "Ущерб"),
    ("timelapse.html", "Скачать"),
    ("forecast.html", "риск"),
    ("citizen.html", "Telegram"),
    ("label.html", "Разметка"),
)

#: Хосты, чьи неудачи не наши. Подложка живёт на чужих серверах и
#: отсутствие отдельного квадрата — обычное дело.
FOREIGN = ("arcgisonline.com", "openstreetmap.org", "basemaps.cartocdn.com",
           "tile.opentopomap.org", "google.com", "gstatic.com")


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
        print(f"нет собранного сайта в {DIST} — сначала npm run build --prefix web-next")
        return 1

    from playwright.sync_api import sync_playwright

    server = serve(DIST, PORT)
    problems: list[str] = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for page_name, expected in PAGES:
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()

                errors: list[str] = []
                failures: list[str] = []
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append(f"исключение: {e}"))
                page.on("requestfailed", lambda r: (
                    failures.append(f"{r.url[:90]} — {r.failure}")
                    if not any(host in r.url for host in FOREIGN)
                    and "ERR_ABORTED" not in (r.failure or "") else None
                ))

                try:
                    page.goto(f"http://127.0.0.1:{PORT}/{page_name}",
                              wait_until="networkidle", timeout=60_000)
                except Exception as error:
                    problems.append(f"{page_name}: не открылась — {error}")
                    context.close()
                    continue

                # Данные приезжают после первой отрисовки; без паузы
                # проверка на слово из данных ложно срабатывает.
                page.wait_for_timeout(3500)

                text = page.inner_text("body")
                if len(text.strip()) < 200:
                    problems.append(f"{page_name}: страница почти пустая ({len(text)} символов)")
                elif expected not in text:
                    problems.append(f"{page_name}: нет слова «{expected}» — данные не доехали")

                for error in errors[:3]:
                    problems.append(f"{page_name}: ошибка в консоли — {error[:120]}")
                for failure in failures[:3]:
                    problems.append(f"{page_name}: запрос не прошёл — {failure}")

                mark = "OK  " if not (errors or failures) else "!!  "
                print(f"{mark}{page_name:16s} {len(text):6d} символов, "
                      f"ошибок {len(errors)}, неудачных запросов {len(failures)}")
                context.close()
            browser.close()
    finally:
        server.terminate()

    print()
    if problems:
        print(f"── Найдено {len(problems)} ──")
        for item in problems:
            print(" •", item)
        return 1

    print("── Все страницы открылись без ошибок ──")
    return 0


if __name__ == "__main__":
    sys.exit(main())
