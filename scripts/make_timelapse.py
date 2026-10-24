"""Записать таймлапс заранее, чтобы посетитель скачивал его мгновенно.

── Почему заранее ──────────────────────────────────────────────────────

Запись в браузере идёт в реальном времени: восемнадцать секунд ролика
пишутся восемнадцать секунд. Ускорить это нельзя — MediaRecorder ставит
метки времени по стенным часам, и кадры, поданные быстрее, дают не тот же
ролик за меньшее время, а ролик короче.

Значит, ждать должен не посетитель, а сборка. Здесь браузер запускается
один раз после прогона, жмёт ту же кнопку и кладёт готовый файл в
web-next/public. На сайте кнопка становится обычной ссылкой на файл —
скачивание начинается сразу.

── Почему тот же код, а не отдельный ───────────────────────────────────

Ролик пишется той же функцией, что и в браузере (components/recordMap.ts):
второй путь отрисовки означал бы, что скачанный файл и показанное на
экране расходятся, и расхождение обнаружится на защите.

    python scripts/make_timelapse.py
"""

import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DIST = Path("web-next/dist")
TARGET = Path("web-next/public/timelapse.webm")
PORT = 8099

#: Ширина окна записи. Шире 1280 файл толстеет вдвое, а на проекторе
#: разницы не видно.
VIEWPORT = {"width": 1280, "height": 760}


def serve(directory: Path, port: int) -> subprocess.Popen:
    """Локальный сервер над собранным сайтом, отдельным процессом.

    Через file:// не работает: страница читает data/*.geojson запросами, а
    для файлового протокола браузер их запрещает.

    Отдельным процессом, а не потоком: синхронный Playwright крутит
    собственный цикл событий, и http.server в потоке того же процесса
    роняет его с «This event loop is already running».
    """
    process = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--directory", str(directory)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/timelapse.html", timeout=1).read(1)
            return process
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    process.terminate()
    raise RuntimeError("локальный сервер не поднялся")


def main() -> int:
    if not (DIST / "timelapse.html").exists():
        print(f"нет собранного сайта в {DIST} — сначала npm run build в web-next")
        return 1

    from playwright.sync_api import sync_playwright

    server = serve(DIST, PORT)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(viewport=VIEWPORT, accept_downloads=True)
            page = context.new_page()

            # Страница сама проверяет, лежит ли рядом готовый ролик, и если
            # лежит — показывает ссылку на него вместо кнопки записи. Нам
            # нужна именно запись: файл с прошлого прогона устарел вместе с
            # данными. Отвечаем «нет файла» на эту проверку, и страница
            # переходит на запасной путь.
            page.route(
                "**/timelapse.webm",
                lambda route: route.fulfill(status=404) if route.request.method == "HEAD" else route.continue_(),
            )

            page.goto(f"http://127.0.0.1:{PORT}/timelapse.html", wait_until="networkidle", timeout=90_000)
            # Тайлы подложки грузятся после карты; записывать до того, как
            # они доехали, значит получить серые квадраты в первых кадрах.
            page.wait_for_timeout(6000)

            # Нажимается кнопка ЗАПИСИ, а не ссылка на готовый файл.
            # Названия разные не для красоты: пока файла нет, страница
            # показывает «Записать видео», а когда он появится — обычную
            # ссылку «Скачать видео». Ищи скрипт первую — и на второй
            # прогон он скачивал бы прошлый ролик вместо нового.
            with page.expect_download(timeout=180_000) as pending:
                page.get_by_role("button", name="Записать видео").click()
            download = pending.value

            TARGET.parent.mkdir(parents=True, exist_ok=True)
            download.save_as(str(TARGET))
            browser.close()
    finally:
        server.terminate()

    size = TARGET.stat().st_size
    print(f"записано: {TARGET} ({size // 1024} КБ)")
    if size < 200_000:
        print("подозрительно мало — проверьте, что подложка успела загрузиться")
        return 1

    # Копия в собранный сайт, чтобы файл был доступен и без пересборки.
    shutil.copy2(TARGET, DIST / TARGET.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
