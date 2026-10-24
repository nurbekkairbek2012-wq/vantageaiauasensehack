"""Собрать посчитанные города в один набор данных для сайта.

── Что объединяется и что нет ──────────────────────────────────────────

Объединяются только итоговые слои, которые показывает сайт: объекты,
зоны риска, маршрут, воронка. Сырые кандидаты, обучающие выборки и модели
остаются у каждого города своими — см. заголовок finish_city.py о том,
почему считать города вместе нельзя.

── Идентификаторы ──────────────────────────────────────────────────────

Номер кандидата сквозной внутри города: C00012 есть и в Астане, и в
Алматы. Простая склейка молча объединила бы разные объекты — карточка
показывала бы один, а печать акта брала бы другой. Поэтому при слиянии
номер получает приставку города: AST-C00012, ALM-C00007.

Приставка, а не сквозная перенумерация: номер должен оставаться тем же
между прогонами, иначе разметка глазами и кэш доверификации, привязанные
к номеру, теряют связь с объектом при каждом пересчёте.

── Осторожность ────────────────────────────────────────────────────────

Скрипт НЕ пишет в outputs_real. Он собирает всё в outputs_merged и
копирует на сайт. Папка одного города остаётся нетронутой, и вернуться к
ней можно в любой момент.

    python scripts/merge_cities.py astana:outputs_real almaty shymkent
"""

import json
import logging
import shutil
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("merge")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MERGED = Path("outputs_merged")
WEB = Path("web-next/public/data")

#: Приставка к номеру объекта по городу. Три буквы: короче не различить,
#: длиннее — не влезает в подпись на карте.
PREFIX = {"astana": "AST", "almaty": "ALM", "shymkent": "SHY"}

#: Слои, которые склеиваются построчно.
STACKED = ("candidates.geojson", "risk_public.geojson", "patrol.geojson", "registry.geojson")


def sources(argv: list[str]) -> list[tuple[str, Path]]:
    """Разобрать аргументы вида `city` или `city:папка`."""
    out = []
    for item in argv:
        city, _, folder = item.partition(":")
        path = Path(folder or f"outputs_{city}")
        if not path.exists():
            log.warning("нет папки %s — город %s пропущен", path, city)
            continue
        out.append((city, path))
    return out


def main() -> int:
    import geopandas as gpd
    import pandas as pd

    picked = sources(sys.argv[1:] or ["astana:outputs_real", "almaty", "shymkent"])
    if not picked:
        log.error("нечего объединять")
        return 1

    MERGED.mkdir(exist_ok=True)
    totals: dict[str, int] = {}

    for layer in STACKED:
        parts = []
        for city, folder in picked:
            path = folder / layer
            if not path.exists():
                continue
            data = gpd.read_file(path)
            if data.empty:
                continue
            data["city"] = city
            if "candidate_id" in data.columns:
                tag = PREFIX.get(city, city[:3].upper())
                data["candidate_id"] = tag + "-" + data["candidate_id"].astype(str)
            parts.append(data)
            if layer == "candidates.geojson":
                totals[city] = len(data)

        if not parts:
            log.warning("%s: ни одного города", layer)
            continue

        # to_crs у первого слоя задаёт систему: слои разных городов лежат в
        # разных зонах UTM, и складывать их без приведения нельзя.
        target = parts[0].crs
        joined = gpd.GeoDataFrame(
            pd.concat([p.to_crs(target) for p in parts], ignore_index=True),
            crs=target,
        )
        joined.to_file(MERGED / layer, driver="GeoJSON")
        log.info("%-24s объектов %d из %d городов", layer, len(joined), len(parts))

    # Воронка складывается по причинам: у каждого города свои числа, а на
    # лендинге показывается общий отсев.
    raw = 0
    rejected: dict[str, int] = {}
    for _city, folder in picked:
        path = folder / "funnel.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        raw += int(data.get("raw", 0))
        for reason, count in (data.get("rejected") or {}).items():
            rejected[reason] = rejected.get(reason, 0) + int(count)
    if raw:
        (MERGED / "funnel.json").write_text(
            json.dumps({"raw": raw, "rejected": rejected}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        log.info("%-24s сырых %d", "funnel.json", raw)

    # Остальное берётся у Астаны: story и metrics описывают прогон, а не
    # список объектов, и складывать их значило бы получить среднее по
    # больнице.
    for name in ("story.json", "metrics.json", "removal.json"):
        source = picked[0][1] / name
        if source.exists():
            shutil.copy2(source, MERGED / name)

    WEB.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in MERGED.glob("*"):
        if item.is_file():
            shutil.copy2(item, WEB / item.name)
            copied += 1

    log.info("")
    log.info("── Объединено ──")
    for city, count in totals.items():
        log.info("  %-10s объектов %d", city, count)
    log.info("на сайт скопировано файлов: %d", copied)
    return 0


if __name__ == "__main__":
    sys.exit(main())
