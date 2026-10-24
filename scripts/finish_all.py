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


def build_cities() -> bool:
    """Список городов для переключателя на карте."""
    log.info("── Список городов")
    try:
        import geopandas as gpd
        import yaml
        from shapely.geometry import box

        cities = yaml.safe_load(Path("config/cities.yaml").read_text(encoding="utf-8"))["cities"]
        found = gpd.read_file(WEB_DATA / "candidates.geojson")
        out = []
        for city in cities:
            inside = found[found.geometry.intersects(box(*city["bbox"]))] if not found.empty else found
            out.append({
                "id": city["id"], "name": city["name"],
                "center": city["center"], "zoom": city["zoom"],
                "count": len(inside),
            })
            log.info("   %s: %d", city["name"], len(inside))
        (WEB_DATA / "cities.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        results.append(("Список городов", True, ", ".join(f"{c['name']} {c['count']}" for c in out)))
        return True
    except Exception as error:  # шаг не должен ронять цепочку
        results.append(("Список городов", False, str(error)[:160]))
        log.error("   не удалось: %s", error)
        return False


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
