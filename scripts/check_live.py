"""Проверить, что живая ссылка открывается у постороннего, а не у нас.

── Зачем ───────────────────────────────────────────────────────────────

24 августа выяснилось, что адрес, напечатанный в деке и в README, отдаёт
**страницу входа в Vercel**, а не сайт. У проекта включена защита
развёртывания: мы залогинены в Vercel, у нас ссылка открывается, и
проверить её «на глаз» из своего браузера невозможно в принципе — она
всегда будет работать.

Именно так её и увидел проверяющий: «не отдалось в проверяемом виде».
Живая ссылка — одно из двух допустимых подтверждений работающего MVP, и
стена авторизации на ней стоит дороже любых недоделок интерфейса.

── Что проверяется ─────────────────────────────────────────────────────

Не «отвечает ли сервер». Сервер отвечает 200 и на странице логина.
Проверяется, что пришёл именно наш сайт:

  * в HTML есть имя продукта — не заголовок чужой страницы;
  * data/candidates.geojson отдаётся как JSON, а не как HTML;
  * в нём столько же объектов, сколько в локальной выгрузке;
  * страницы, на которые ведёт навигация, существуют.

Последняя проверка неочевидна и нужна: `economy.html` появился отдельной
точкой входа Vite, и сборка без него прошла бы молча — Vite не считает
пропущенную точку входа ошибкой.

    python scripts/check_live.py                      # адрес по умолчанию
    python scripts/check_live.py https://другой.адрес
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "web-next/public/data/candidates.geojson"

#: Рабочий адрес. Проверен 24 августа: отдаёт сайт постороннему, все
#: семь страниц и данные. Прежний — hakathon-ll-1c21 — отдавал
#: страницу входа в Vercel, и именно на нём эта проверка и появилась.
DEFAULT_URL = "https://hakathon-lyart.vercel.app"

#: Страницы, которые обязаны открываться: все, на которые ведёт навигация.
PAGES = ("index.html", "map.html", "economy.html", "timelapse.html",
         "forecast.html", "citizen.html", "label.html")

#: Признаки чужой страницы на нашем адресе. Стена авторизации отвечает
#: кодом 200 и выглядит как обычная страница — отличает её содержимое.
INTRUDERS = ("login – vercel", "log in to vercel", "vercel authentication",
             "sign in to continue", "deployment not found", "404: not_found")

TIMEOUT = 25


def fetch(url: str) -> tuple[int, str, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.status, response.headers.get("content-type", ""), response.read()


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL).rstrip("/")
    print(f"── Проверяю {base}\n")
    problems: list[str] = []

    # 1. Главная страница: наша ли она вообще.
    try:
        status, kind, body = fetch(f"{base}/index.html")
        text = body.decode("utf-8", "replace")
        low = text.lower()
        found = next((word for word in INTRUDERS if word in low), None)
        if found:
            problems.append(
                f"на {base}/index.html отвечает чужая страница (найдено «{found}»). "
                "Скорее всего включена защита развёртывания: "
                "Vercel → Project → Settings → Deployment Protection → отключить")
            print(f"ПЛОХО index.html         {status}, но это не наш сайт: «{found}»")
        elif "vantage" not in low:
            problems.append("на главной нет имени продукта — отдаётся не наша сборка")
            print(f"ПЛОХО index.html         {status}, имени продукта в HTML нет")
        else:
            print(f"OK    index.html         {status}, {len(body) // 1024} КБ")
    except (urllib.error.URLError, OSError) as error:
        problems.append(f"index.html недоступен: {error}")
        print(f"ПЛОХО index.html         {error}")
        # Дальше идти незачем: если главной нет, нет и остального.
        return report(problems)

    # 2. Остальные страницы навигации.
    #
    # Кода ответа и типа содержимого мало: стена авторизации отдаёт 200 и
    # text/html на любой адрес, и первая версия этой проверки бодро писала
    # «OK» напротив шести страниц, каждая из которых была одной и той же
    # страницей входа. Признак нашей страницы — имя продукта в <title>.
    for page in PAGES[1:]:
        try:
            status, kind, body = fetch(f"{base}/{page}")
            low = body.decode("utf-8", "replace").lower()
            ok = status == 200 and "text/html" in kind and "vantage ai" in low
            print(f"{'OK   ' if ok else 'ПЛОХО'} {page:<18} {status}, {len(body) // 1024} КБ")
            if not ok:
                reason = ("отдана чужая страница" if "vantage ai" not in low
                          else f"код {status}, тип {kind}")
                problems.append(f"{page}: {reason}")
        except (urllib.error.URLError, OSError) as error:
            problems.append(f"{page}: {error}")
            print(f"ПЛОХО {page:<18} {error}")

    # 3. Данные карты. Тип содержимого здесь важнее кода ответа: страница
    #    логина и подстановка index.html вместо файла обе отдают 200 и HTML.
    for name in ("candidates.geojson", "economy.json"):
        url = f"{base}/data/{name}"
        try:
            status, kind, body = fetch(url)
            if "html" in kind.lower():
                problems.append(f"data/{name} отдаётся как HTML — файла на сервере нет")
                print(f"ПЛОХО data/{name:<12} отдан HTML вместо данных")
                continue
            payload = json.loads(body.decode("utf-8"))
            count = (len(payload.get("features", [])) if name.endswith("geojson")
                     else len(payload.get("objects", [])))
            print(f"OK    data/{name:<12} {status}, объектов {count}")
            if name == "candidates.geojson" and LOCAL.exists():
                local = len(json.loads(LOCAL.read_text(encoding="utf-8"))["features"])
                if count != local:
                    problems.append(
                        f"на сервере {count} объектов, локально {local} — "
                        "опубликована сборка от прошлого прогона")
                    print(f"      ↑ локально {local}: сервер отдаёт старую выгрузку")
        except (urllib.error.URLError, OSError, ValueError) as error:
            problems.append(f"data/{name}: {error}")
            print(f"ПЛОХО data/{name:<12} {error}")

    return report(problems)


def report(problems: list[str]) -> int:
    print()
    if not problems:
        print("── Ссылка открывается у постороннего. Это и есть подтверждение MVP ──")
        return 0
    print(f"── Ссылка НЕ годится как подтверждение MVP: {len(problems)} проблем ──")
    for problem in problems:
        print(f"   · {problem}")
    print()
    print("Проверять из своего браузера бесполезно: мы залогинены, и у нас")
    print("откроется в любом случае. Годится окно в режиме инкогнито или")
    print("этот скрипт.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
