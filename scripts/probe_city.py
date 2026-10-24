"""Узнать за минуту, сработает ли метод в этой области — до трёх часов счёта.

── Зачем ───────────────────────────────────────────────────────────────

Прогон по городу стоит от полутора до трёх часов машинного времени. В
ночь на 22 августа два таких прогона — Алматы и Шымкент — закончились
нулём объектов, и причина оказалась известна заранее, просто её никто не
спросил: области были выбраны внутри жилой застройки.

Контекстный отсев требует от места двух вещей одновременно: подъезда для
самосвала (ближе 300 м к проезжей дороге) и удалённости от жилья (дальше
1,5 км — иначе заметят и без спутника). Земля, не удовлетворяющая обоим
условиям, отсеется независимо от того, что показали снимки.

Долю такой земли можно посчитать по одному запросу к OpenStreetMap, без
единого спутникового снимка. У прежней области Шымкента она оказалась
**0,0%** — ноль объектов был предрешён до начала счёта.

── Чего замер НЕ говорит ───────────────────────────────────────────────

Он мерит, есть ли в области подходящая земля, и молчит о том, из чего
эта земля состоит. Разница выяснилась на Алматы: замер дал 25,9% —
хорошая область, — прогон нашёл восемь объектов, и все восемь оказались
ложными. Орошаемая пашня, пруды, заболоченные русла.

Причина в самом признаке детектора. Он ищет место, где растительность
исчезла НАВСЕГДА; поле, выведенное из оборота, выглядит ровно так же.
Пять физических признаков этого не разделяют: у алматинской пашни сила
признаков 0,575 против медианы 0,505 у подтверждённых свалок Астаны.

Попытка померить долю пашни по OpenStreetMap провалилась и была снята:
для той же области она даёт 4%, тогда как глазами пашня занимает почти
всё. Поля вокруг Алматы просто не размечены. Показатель, дающий ложное
«всё чисто» ровно на том случае, ради которого сделан, хуже
отсутствующего.

Пока такого замера нет, проверка глазами обязательна на новой местности —
и первыми надо смотреть самые крупные находки: в Алматы один объект в
13,7 га давал 85% всей суммы ущерба.

── Что это даёт ────────────────────────────────────────────────────────

Ответ на вопрос «а масштабируется ли ваш метод на другие города»
перестаёт быть обещанием: для любого города это проверяется за минуту, и
ответ бывает отрицательным. Метод рассчитан на окраину со степью за ней;
там, где пригородный пояс сплошной, он не работает, и честнее сказать это
заранее.

    python scripts/probe_city.py 69.72 42.28 69.88 42.38
    python scripts/probe_city.py --city almaty
    python scripts/probe_city.py --around 43.238 76.889 --radius 25
"""

import argparse
import logging
import sys
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.WARNING, format="%(message)s")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Шаг сетки замера. Пятьсот метров — размер ячейки модели риска: мельче
#: считать нечего, крупнее — теряются узкие полосы вдоль дорог, где
#: свалки как раз и возникают.
STEP_M = 500


def share_of_usable(bbox: tuple[float, float, float, float], settings) -> tuple[float, int, int]:
    """Доля земли, проходящей отсев по подъезду и по удалённости от жилья."""
    import geopandas as gpd
    from shapely.geometry import box

    from vantage.aoi import AOI
    from vantage.context import fetch_context

    cfg = settings.context
    crs = settings.project.crs_working
    aoi = AOI.from_bbox(bbox, name="probe", crs_working=crs)
    layers = fetch_context(aoi, settings)
    if layers.roads.empty or layers.settlements.empty:
        raise RuntimeError("слои OSM пусты — замер невозможен, повторите позже")

    area = gpd.GeoDataFrame(geometry=[box(*bbox)], crs=4326).to_crs(crs)
    minx, miny, maxx, maxy = area.total_bounds
    xs = np.arange(minx, maxx, STEP_M)
    ys = np.arange(miny, maxy, STEP_M)
    points = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(np.repeat(xs, len(ys)), np.tile(ys, len(xs))), crs=crs
    )

    to_road = (
        gpd.sjoin_nearest(points[["geometry"]], layers.roads[["geometry"]], distance_col="d")["d"]
        .groupby(level=0).min()
    )
    to_home = (
        gpd.sjoin_nearest(points[["geometry"]], layers.settlements[["geometry"]], distance_col="d")["d"]
        .groupby(level=0).min()
    )

    good = (
        (to_road <= cfg.max_distance_to_road_m)
        & (to_home >= cfg.min_distance_to_settlement_m)
        & (to_home <= cfg.max_distance_to_settlement_m)
    )
    return float(good.mean()), len(layers.roads), len(layers.settlements)


def share_of_industry(bbox: tuple[float, float, float, float], settings) -> float:
    """Доля промышленной земли — второй замер, и он про класс, а не про место.

    Первая попытка мерить пригодность области по доле ПАШНИ провалилась:
    поля вокруг Алматы не размечены в OpenStreetMap, и замер давал 4% там,
    где глазами пашня занимает почти всё.

    С промышленностью наоборот: её размечают, потому что у неё есть
    владелец, адрес и кадастр. И она оказалась откалиброванным
    предсказателем — проверено на трёх областях с известным исходом:

        север Астаны     3,11%   →  3 подтверждённых + 5 опознанных
        восток Астаны    0,00%   →  0 настоящих из 33 находок
        запад Астаны     0,67%   →  считается

    Логика за этим простая. Детектор ищет место, где растительность
    исчезла навсегда. В промзоне такое событие редкое и потому значимое; в
    поле оно рядовое — залежь, смена оборота, заброшенный огород.
    """
    import geopandas as gpd
    from shapely.geometry import box
    from shapely.ops import unary_union

    from vantage.aoi import AOI
    from vantage.context import OverpassClient, _bbox_clause, overpass_to_gdf

    crs = settings.project.crs_working
    aoi = AOI.from_bbox(bbox, name="probe", crs_working=crs)
    clause = _bbox_clause(aoi)
    query = (
        "[out:json][timeout:180];("
        f'way["landuse"~"^(industrial|quarry|construction|railway|landfill)$"]({clause});'
        f'relation["landuse"~"^(industrial|quarry|construction|railway|landfill)$"]({clause});'
        f'way["man_made"="works"]({clause});'
        ");out geom;"
    )
    client = OverpassClient(settings.paths.resolve("data_cache"))
    layer = overpass_to_gdf(client.query(query), target_crs=crs)
    if layer.empty:
        return 0.0
    area = gpd.GeoDataFrame(geometry=[box(*bbox)], crs=4326).to_crs(crs)
    total = float(area.area.iloc[0])
    covered = unary_union(layer.geometry.values).intersection(area.geometry.iloc[0]).area
    return float(covered / total) if total else 0.0


def osm_density(bbox: tuple[float, float, float, float], settings) -> float:
    """Сколько размеченных контуров OSM приходится на квадратный километр.

    Лучший из найденных предсказателей, и не случайно: он мерит ровно то,
    что делает в системе различение.

    Пять физических признаков находят необратимое изменение поверхности —
    измерено, что свалку от склада внутри одной местности они НЕ отличают
    (ROC-AUC 0,500). Отсеивает лишнее контекстный фильтр по OSM, и работает
    он ровно настолько, насколько подробна карта.

    Замерено на пяти областях, порядок оказался строгим:

        запад · промзона   97,8 контура на км²   считается
        север Астаны       94,7                  3 подтверждённых + 5 опознанных
        запад · сёла       32,5                  не запускался
        юго-восток         18,1                  0 настоящих из 9 просмотренных
        восток              6,8                  0 настоящих из 33

    Разница в четырнадцать раз между работающей областью и провальной, и
    ни одного нарушения порядка: чем подробнее карта, тем больше настоящих
    свалок доходит до списка.

    Площадь считается в UTM, а не в веб-Меркаторе. Первый замер этой
    величины был сделан в 3857 и занизил все плотности ровно в 2,5 раза:
    на широте 51° Меркатор раздувает площадь в 1/cos²(51). Отношения между
    областями при этом сохранились, и вывод не изменился — но абсолютные
    числа, по которым калибруются пороги ниже, были неверны.

    Запрос лёгкий: `out count` не отдаёт геометрию, только число.
    """
    import geopandas as gpd
    from shapely.geometry import box

    from vantage.aoi import AOI
    from vantage.context import OverpassClient, _bbox_clause

    crs = settings.project.crs_working
    aoi = AOI.from_bbox(bbox, name="probe", crs_working=crs)
    clause = _bbox_clause(aoi)
    query = (
        "[out:json][timeout:150];("
        f'way["landuse"]({clause});way["building"]({clause});'
        ");out count;"
    )
    client = OverpassClient(settings.paths.resolve("data_cache"))
    payload = client.query(query)
    elements = payload.get("elements") or []
    total = int((elements[0].get("tags") or {}).get("total", 0)) if elements else 0

    area = gpd.GeoDataFrame(geometry=[box(*bbox)], crs=4326).to_crs(crs)
    km2 = float(area.area.iloc[0]) / 1e6
    return total / km2 if km2 else 0.0


def verdict(share: float, industry: float | None = None,
            density: float | None = None) -> str:
    """Словами — чтобы решение принималось без пересчёта в голове.

    Границы откалиброваны на трёх прогонах, два из которых провалились:

        Астана, кольцо      32,2%   хорошая        →  21 объект
        Алматы, прежняя      7,3%   рискованно     →   0 объектов
        Шымкент, прежняя     0,0%   не запускать   →   0 объектов

    То есть замер предсказал бы оба провала до того, как на них ушло
    четыре с половиной часа счёта. Три точки — мало для закона, но
    достаточно, чтобы не запускать область с нулём процентов."""
    # Плотность карты решает раньше всего. Она мерит то, что делает
    # различение: признаки находят изменение, а отсеивает лишнее фильтр по
    # OSM, и работает он ровно настолько, насколько карта подробна.
    #
    # Пороги стоят между замеренными исходами, а не выбраны красиво:
    # 18,1 дал ноль настоящих из девяти, 94,7 — восемь. Между ними
    # непроверенная середина, и она названа непроверенной.
    if density is not None and density < 20:
        return (f"НЕ ЗАПУСКАТЬ: карта пуста ({density:.1f} контура на км² против "
                f"95 там, где метод работает) — отсеивать будет нечем. "
                f"Так выглядели восток (6,8) и юго-восток (18,1): 0 свалок из 42")
    if density is not None and density < 50:
        return (f"неизвестно: карта средняя ({density:.0f} на км²) — между "
                f"провалившимися поясами и работающей промзоной прогонов не было")

    # Промышленность решает раньше площади: восточный пояс имел 23,3%
    # годной земли — «хорошая область» — и ноль настоящих свалок из
    # тридцати трёх находок, потому что промышленной земли там 0,00%.
    if industry is not None and industry < 0.003:
        return ("НЕ ЗАПУСКАТЬ: промышленной земли нет — находки будут "
                "сельскими, как в восточном поясе (0 настоящих из 33)")
    if industry is not None and industry < 0.01:
        return "рискованно: промышленной земли мало, ждите много ложных находок"
    if share < 0.03:
        return "НЕ ЗАПУСКАТЬ: пригодной земли практически нет, ноль объектов предрешён"
    if share < 0.10:
        return "рискованно: объектов будет мало, стоит поискать область получше"
    if share < 0.20:
        return "приемлемо"
    return "хорошая область"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bbox", nargs="*", type=float, help="minlon minlat maxlon maxlat")
    parser.add_argument("--city", help="идентификатор из config/cities.yaml")
    parser.add_argument("--around", nargs=2, type=float, metavar=("LAT", "LON"),
                        help="проверить четыре стороны вокруг точки")
    parser.add_argument("--radius", type=float, default=20, help="км от точки, с --around")
    args = parser.parse_args()

    from vantage import env
    from vantage.config import load_settings

    env.configure()
    settings = load_settings()

    probes: list[tuple[str, tuple[float, float, float, float]]] = []

    if args.city:
        import yaml

        cities = yaml.safe_load(Path("config/cities.yaml").read_text(encoding="utf-8"))["cities"]
        found = next((c for c in cities if c["id"] == args.city), None)
        if found is None:
            print(f"нет города {args.city!r}")
            return 1
        probes.append((found["name"], tuple(found["bbox"])))
    elif args.around:
        lat, lon = args.around
        # Градус широты — 111 км; долготы — меньше на косинус широты.
        dlat = args.radius / 111.0
        dlon = args.radius / (111.0 * max(0.2, np.cos(np.radians(lat))))
        half = 0.08
        for name, (dy, dx) in (("север", (dlat, 0)), ("юг", (-dlat, 0)),
                               ("восток", (0, dlon)), ("запад", (0, -dlon))):
            probes.append((name, (lon + dx - half, lat + dy - half / 2,
                                  lon + dx + half, lat + dy + half / 2)))
    elif len(args.bbox) == 4:
        probes.append(("область", tuple(args.bbox)))
    else:
        parser.print_help()
        return 1

    print(f"Порог отсева: подъезд ≤ {settings.context.max_distance_to_road_m:.0f} м, "
          f"жильё {settings.context.min_distance_to_settlement_m:.0f}–"
          f"{settings.context.max_distance_to_settlement_m:.0f} м")
    print()
    for name, bbox in probes:
        try:
            share, roads, homes = share_of_usable(bbox, settings)
        except Exception as error:
            print(f"{name:12s} — не измерено: {str(error)[:70]}")
            continue
        try:
            industry = share_of_industry(bbox, settings)
        except Exception:
            industry = None
        try:
            density = osm_density(bbox, settings)
        except Exception:
            density = None
        ind = f"{industry * 100:5.2f}%" if industry is not None else "  ?  "
        den = f"{density:5.1f}" if density is not None else "  ?  "
        print(f"{name:12s} годной земли {share * 100:5.1f}%   промышленной {ind}   "
              f"контуров/км² {den}")
        print(f"{'':12s} {verdict(share, industry, density)}")
    print()
    print("Замер не смотрит на снимки — он говорит только о том, есть ли в области")
    print("земля, которую отсев в принципе может пропустить. Это нижняя граница:")
    print("свалок может не оказаться и на хорошей области.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
