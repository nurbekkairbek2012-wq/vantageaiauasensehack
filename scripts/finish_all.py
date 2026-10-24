"""Всё, что делается после прогона по плиткам, — одной командой.

── Зачем ───────────────────────────────────────────────────────────────

Между «плитки посчитаны» и «сайт показывает правду» лежит десяток шагов:
склейка, признаки, отсев, доверификация, деньги, риск, контроль
устранения, маршрут, чипы, перенос разметки, указатель для бота, список
городов, копирование на сайт.

Каждый из них я запускал руками, и порядок держался в голове. Так забывают
шаг — а забытый шаг не падает с ошибкой, он просто оставляет на сайте
вчерашние числа. Это худший вид поломки: её не видно.

── Как устроено ────────────────────────────────────────────────────────

Каждый шаг изолирован. Упавший шаг не роняет остальные и не прерывает
цепочку: недоступный Overpass или лимит тайлов у поставщика снимков —
обычное дело, и терять из-за них весь пересчёт незачем. В конце
печатается сводка: что прошло, что нет.

Порядок при этом не произволен, и переставлять шаги нельзя. Разметка
глазами переносится ПОСЛЕ выгрузки на сайт, иначе выгрузка её затрёт.
Указатель для бота собирается ПОСЛЕ переноса разметки, иначе бот будет
отвечать «проверка не проводилась» по проверенным объектам.

    python scripts/finish_all.py [--no-verify] [--no-signals]
"""

import json
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("finish_all")

OUTPUTS = Path("outputs_real")
WEB_DATA = Path("web-next/public/data")

#: Что уезжает на сайт. Список совпадает с cli.PUBLIC_WHITELIST не
#: случайно: публикуется ровно то, что решено публиковать, и приватные
#: слои (risk_private, точные вероятности) сюда не попадают никогда.
PUBLISH = (
    "candidates.geojson",
    "risk_public.geojson",
    "registry.geojson",
    "patrol.geojson",
    "story.json",
    "metrics.json",
    "removal.json",
    "funnel.json",
)

results: list[tuple[str, bool, str]] = []


def step(title: str, command: list[str]) -> bool:
    """Выполнить шаг, не роняя цепочку."""
    log.info("── %s", title)
    started = time.time()
    try:
        done = subprocess.run(command, check=False, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except OSError as error:
        results.append((title, False, str(error)))
        log.error("   не запустился: %s", error)
        return False

    took = time.time() - started
    if done.returncode == 0:
        tail = [line for line in (done.stdout or "").splitlines() if line.strip()][-1:]
        note = tail[0][:110] if tail else ""
        results.append((title, True, note))
        log.info("   готово за %.0f с. %s", took, note)
        return True

    tail = [line for line in (done.stderr or done.stdout or "").splitlines() if line.strip()][-1:]
    note = tail[0][:160] if tail else f"код {done.returncode}"
    results.append((title, False, note))
    log.error("   не удалось: %s", note)
    return False


def publish_to_site() -> bool:
    """Скопировать артефакты прогона в папку сайта."""
    log.info("── Выгрузка на сайт")
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in PUBLISH:
        source = OUTPUTS / name
        if source.exists():
            shutil.copy2(source, WEB_DATA / name)
            copied.append(name)
    ok = bool(copied)
    results.append(("Выгрузка на сайт", ok, f"файлов: {len(copied)}"))
    log.info("   скопировано: %d — %s", len(copied), ", ".join(copied) or "нечего")
    return ok


#: Где лежит прогон области. У первого кольца папка исторически другая.
def _outputs_of(city_id: str) -> Path:
    special = Path("outputs_real")
    if city_id == "astana" and special.exists():
        return special
    return Path(f"outputs_{city_id}")


def build_cities() -> bool:
    """Список областей для переключателя на карте.

    Ноль объектов значит две разные вещи, и путать их нельзя. Восточный
    пояс считался четыре часа и просматривался час: 33 находки, ни одной
    настоящей свалки. Западный пояс не запускался вовсе. На кнопке и то и
    другое выглядело как «0», а подсказка обоим говорила «прогон ещё не
    проходил» — про восток это была просто неправда.

    Поэтому область получает состояние:

        found    — есть опубликованные объекты
        empty    — прогон прошёл, настоящих свалок не нашлось
        pending  — прогон не запускался

    Второе — не провал, а результат, и на защите он работает в плюс:
    система умеет говорить «здесь чисто», а не только «здесь свалка».
    """
    log.info("── Список областей")
    try:
        import geopandas as gpd
        import yaml
        from shapely.geometry import box

        cities = yaml.safe_load(Path("config/cities.yaml").read_text(encoding="utf-8"))["cities"]
        found = gpd.read_file(WEB_DATA / "candidates.geojson")

        # Области перекрываются по краям: северное кольцо и юго-восточный
        # пояс делят полосу шириной 6 км. Объект из этой полосы засчитывался
        # обеим, и на кнопке «Юго-восток» стояла единица — при том что
        # настоящих находок там ноль из 63 просмотренных. Кнопка обещала
        # объект, которого в этой области нет.
        #
        # Поэтому объект достаётся первой области, чьи границы его
        # накрывают: порядок в cities.yaml идёт от того, где считали
        # раньше, и это же порядок, в котором объект получил свой номер.
        taken: set = set()
        out = []
        for city in cities:
            if found.empty:
                inside = found
            else:
                hit = found[found.geometry.intersects(box(*city["bbox"]))]
                inside = hit[~hit["candidate_id"].isin(taken)]
                taken.update(inside["candidate_id"])

            # Сколько находок дошло до просмотра. Берётся из папки прогона,
            # а не из выгрузки: в выгрузке отвергнутых уже нет.
            source = _outputs_of(city["id"]) / "candidates.geojson"
            reviewed = 0
            if source.exists():
                try:
                    reviewed = len(gpd.read_file(source))
                except Exception:
                    reviewed = 0

            if len(inside):
                state = "found"
            elif reviewed:
                state = "empty"
            else:
                state = "pending"

            out.append({
                "id": city["id"], "name": city["name"],
                # Короткая подпись обязательна: без неё кнопка получает
                # «Астана · юго-восток», и ряд на телефоне рвётся на три
                # строки. Однажды она уже терялась здесь при пересборке.
                "short": city.get("short", city["name"]),
                "center": city["center"], "zoom": city["zoom"],
                "count": len(inside), "reviewed": reviewed, "state": state,
            })
            log.info("   %-28s объектов %2d, просмотрено %3d — %s",
                     city["name"], len(inside), reviewed, state)
        (WEB_DATA / "cities.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        results.append(("Список городов", True, ", ".join(f"{c['name']} {c['count']}" for c in out)))
        return True
    except Exception as error:  # шаг не должен ронять цепочку
        results.append(("Список городов", False, str(error)[:160]))
        log.error("   не удалось: %s", error)
        return False


def verify_published() -> bool:
    """Убедиться, что в выгрузке не осталось отвергнутых объектов.

    Фильтр публикации стоит последним, но «последним» его делает порядок
    строк в этом файле, а не что-либо ещё. Любой шаг, дописанный ниже и
    переписывающий candidates.geojson целиком, вернул бы отвергнутые на
    сайт — и заметить это было бы нечем: файл на месте, объекты на месте,
    просто их снова пятьдесят вместо четырнадцати.

    Так и случилось в ночь на 23 августа: досчёт вернул на сайт все 49.
    Проверка дешёвая, а тихий откат стоит доверия ко всему списку.
    """
    log.info("── Проверка выгрузки")
    target = WEB_DATA / "candidates.geojson"
    if not target.exists():
        results.append(("Проверка выгрузки", False, "файла нет"))
        return False
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as error:
        results.append(("Проверка выгрузки", False, str(error)[:80]))
        return False

    bad = [
        f["properties"].get("candidate_id")
        for f in data.get("features", [])
        if (f.get("properties") or {}).get("visual_check") == "not_landfill"
    ]
    if bad:
        log.error("   в выгрузке %d отвергнутых объектов — прогоняю фильтр заново", len(bad))
        subprocess.run([sys.executable, "scripts/publish_filter.py"], check=False)
        # Указатель бота собирается ИЗ выгрузки и уже устарел: если его не
        # пересобрать, житель пришлёт точку у склада и получит ответ
        # «объект известен». Хуже, чем молчание.
        subprocess.run([sys.executable, "scripts/make_bot_index.py"], check=False)
        results.append(("Проверка выгрузки", False, f"было отвергнутых: {len(bad)}, фильтр повторён"))
        return False

    count = len(data.get("features", []))
    results.append(("Проверка выгрузки", True, f"объектов {count}, отвергнутых нет"))
    log.info("   объектов %d, отвергнутых нет", count)
    return True


def main() -> int:
    python = sys.executable
    flags = [flag for flag in sys.argv[1:] if flag.startswith("--")]

    if not (OUTPUTS / "tiles").exists() or not list((OUTPUTS / "tiles").glob("*.geojson")):
        log.error("нет плиточных результатов в %s — сначала scripts/run_ring.py", OUTPUTS / "tiles")
        return 1

    # Порядок обязателен, см. заголовок модуля.
    step("Склейка, признаки, отсев, деньги, риск", [python, "scripts/finish_ring.py", *flags])
    step("Контроль устранения", [python, "scripts/check_removal.py"])
    step("Маршрут на месяц", [python, "scripts/make_patrol.py", "20"])
    publish_to_site()
    step("Перенос разметки глазами", [python, "scripts/attach_visual.py"])
    # Доверификация — тоже ПОСЛЕ выгрузки и по той же причине. Внутри
    # finish_ring она выключается флагом --no-verify, и флаг работает не
    # как «пропустить», а как «стереть»: пересчёт переписывает файл без
    # колонок verify_*, и подтверждение двумя источниками исчезает молча.
    # Здесь оно живёт в своём кэше и переживает любой пересчёт.
    step("Доверификация по снимкам", [python, "scripts/attach_verify.py"])
    step("Оценка моделью по снимку", [python, "scripts/attach_chipmodel.py"])
    # Фильтр публикации идёт ПОСЛЕДНИМ и после всех, кто пишет в выгрузку.
    # Любой шаг, переписывающий candidates.geojson целиком, вернул бы на
    # сайт отвергнутые объекты, и заметить это было бы нечем.
    step("Убрать не-свалки из выгрузки", [python, "scripts/publish_filter.py"])
    step("Чипы для разметки", [python, "scripts/export_chips.py"])
    step("Указатель для бота", [python, "scripts/make_bot_index.py"])
    build_cities()
    # Пересборка сайта нужна до записи: ролик пишется по собранной
    # странице, а данные на ней обновились только что.
    step("Сборка сайта", ["npm", "run", "build", "--prefix", "web-next"])
    step("Запись таймлапса", [python, "scripts/make_timelapse.py"])

    verify_published()

    log.info("")
    log.info("── Сводка ─────────────────────────────────────────")
    for title, ok, note in results:
        log.info("%s %-42s %s", "OK  " if ok else "СБОЙ", title, note)

    failed = [title for title, ok, _ in results if not ok]
    if failed:
        log.warning("")
        log.warning("Не прошло: %s", "; ".join(failed))
        log.warning("Остальное посчитано — можно чинить и запускать заново.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
