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

    python scripts/merge_cities.py astana:outputs_real astana_east
                                   astana_southeast astana_west
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
PREFIX = {
    "astana": "AST",
    "astana_east": "AVE",
    "astana_southeast": "ASE",
    "astana_west": "AZP",
    "astana_industrial_west": "APZ",
    "astana_south": "AUG",
}

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


def drop_duplicates_by_place(objects):
    """Убрать объекты, найденные дважды в перекрывающихся областях.

    Границы областей перекрываются намеренно: свалка на краю иначе
    разрезалась бы пополам. Но объект в полосе пересечения находится
    ДВАЖДЫ — по разу в каждом прогоне, — и получает два разных номера.

    Так вышло с полем строительного мусора у железнодорожных путей:
    C00061 в северном кольце и C00056 на юге, расстояние между
    центрами — ноль метров. После слияния сайт показал бы девять свалок
    как десять, а сумма ущерба выросла бы на несуществующий объект.

    Ловится только по месту: номера разные, площади разные (контур
    обрезается границей области), а точка одна.

    Оставляется больший по площади: у него контур обрезан меньше.
    """
    if objects.empty or "geometry" not in objects:
        return objects

    import geopandas as gpd

    # Пятьдесят метров: перекрытие смещает центр обрезанного контура, но
    # не на сотни метров. Меньший порог пропустил бы дубли, больший начал
    # бы склеивать соседние свалки — а они бывают рядом.
    #
    # Пересечение буферов, а не «ближайший сосед»: у sjoin_nearest ближайшим
    # к точке всегда оказывается она сама, и пары не находятся вовсе.
    probe = gpd.GeoDataFrame(
        {"_i": range(len(objects))},
        geometry=objects.geometry.centroid.buffer(25.0), crs=objects.crs)
    pairs = gpd.sjoin(probe, probe, predicate="intersects", how="inner")
    pairs = pairs[pairs["_i_left"] < pairs["_i_right"]]
    if pairs.empty:
        return objects


    # запасное значение здесь — вычисляемый ряд, а не константа.
    area = objects["area_m2"] if "area_m2" in objects else objects.geometry.area  # noqa: SIM401
    drop = set()
    for left, right in zip(pairs["_i_left"], pairs["_i_right"], strict=True):
        loser = right if area.iat[left] >= area.iat[right] else left
        drop.add(int(loser))

    kept = objects.iloc[[i for i in range(len(objects)) if i not in drop]]
    log.info("%-24s убрано дублей из перекрытия областей: %d",
             "candidates.geojson", len(drop))
    return kept.reset_index(drop=True)


def main() -> int:
    import geopandas as gpd
    import pandas as pd

    picked = sources(sys.argv[1:] or ["astana:outputs_real", "astana_east",
                                     "astana_southeast", "astana_west",
                                     "astana_industrial_west", "astana_south"])
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
                if city not in PREFIX:
                    # Запасной вариант city[:3] дал бы «AST» и северному
                    # кольцу, и югу, и юго-востоку. Номера столкнулись бы
                    # молча: карточка показывала бы один объект, а печать
                    # акта брала бы другой.
                    raise SystemExit(
                        f"для области {city} не заведена приставка в PREFIX — "
                        "добавьте её, иначе номера объектов столкнутся")
                tag = PREFIX[city]
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

        if layer == "candidates.geojson":
            joined = drop_duplicates_by_place(joined)

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

    # Слияние копирует в выгрузку НАПРЯМУЮ и тем самым обходит фильтр
    # публикации: объединённый список содержит и отвергнутые объекты со
    # всех поясов. Без этого вызова они разом вернулись бы на сайт — тот
    # же тихий откат, что уже ловился проверкой в конце досчёта.
    import subprocess

    log.info("")
    log.info("── Фильтр публикации по объединённому набору")
    subprocess.run([sys.executable, "scripts/publish_filter.py",
                    "--outputs", str(MERGED)], check=False)
    subprocess.run([sys.executable, "scripts/make_bot_index.py"], check=False)

    log.info("")
    log.info("── Объединено ──")
    for city, count in totals.items():
        log.info("  %-10s объектов %d", city, count)
    log.info("на сайт скопировано файлов: %d", copied)
    return 0


if __name__ == "__main__":
    sys.exit(main())
