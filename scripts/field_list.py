"""Список объектов для выезда с камерой — по порядку ценности.

── Зачем ───────────────────────────────────────────────────────────────

Фотография с земли — единственное, что превращает «просмотрено по снимку»
в «подтверждено». Одна такая фотография стоит больше любой правки кода:
она закрывает вопрос «а вы там были?», на который иначе ответить нечем.

Но ехать надо не куда попало. Порядок здесь считается, а не выбирается на
глаз, и считается по тому, сколько знания добавляет поездка.

── Как считается ценность ──────────────────────────────────────────────

**Сначала «не разобрать».** Объект, про который человек посмотрел снимок
0,5 м и не смог решить, — это ровно то место, где спутник исчерпан.
Поездка превращает неизвестность в факт, и превращает в обе стороны: «да,
свалка» и «нет, не свалка» одинаково ценны.

**Потом опознанные как свалка.** Здесь поездка не добавляет знания —
добавляет доказательство. Нужна хотя бы одна фотография такого объекта:
без неё на защите нечего показать рядом со снимком со спутника.

**Внутри группы — от крупных к мелким**, а для дороги отдельно считаются
поездки: точки ближе TRIP_GAP_M друг к другу объединяются в одну поездку,
внутри неё порядок объезда — «к ближайшей ещё не посещённой», и на каждую
поездку готова ссылка на маршрут в Google Maps. Разбросанные по всему
кольцу точки без этого съедают день на дорогу.

    python scripts/field_list.py [--top 8]
"""

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PUBLISHED = Path("web-next/public/data/candidates.geojson")
#: Поездки для бота (api/telegram.py, команда /route).
TRIPS = Path("api/trips.json")

#: Точки ближе этого расстояния друг к другу — одна поездка.
TRIP_GAP_M = 2000.0

#: Откуда начинается объезд: центр Астаны (Байтерек).
START = (51.1283, 71.4305)

#: Ценность поездки по вердикту. «Не разобрать» выше, потому что там
#: поездка добавляет знание, а не подтверждение.
WORTH = {"unclear": 2, "landfill": 1}


def trips(xy, gap_m: float = TRIP_GAP_M, start=None) -> list[list[int]]:
    """Разбить точки на поездки и упорядочить объезд внутри каждой.

    xy — координаты в метрах. Точки, между которыми цепочка шагов короче
    gap_m, попадают в одну поездку (связные компоненты). Внутри поездки:
    первая — ближайшая к start, дальше — к ближайшей ещё не посещённой.
    Поездки — от самой длинной по числу точек к короткой.
    """
    import numpy as np

    xy = np.asarray(xy, dtype=float).reshape(-1, 2)
    n = len(xy)
    parent = list(range(n))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if np.hypot(*(xy[i] - xy[j])) < gap_m:
                parent[root(i)] = root(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(root(i), []).append(i)

    origin = np.asarray(start, dtype=float) if start is not None else xy.mean(axis=0)
    out = []
    for members in groups.values():
        left = list(members)
        here = origin
        route = []
        while left:
            nxt = min(left, key=lambda k: (np.hypot(*(xy[k] - here)), k))
            route.append(nxt)
            left.remove(nxt)
            here = xy[nxt]
        out.append(route)
    return sorted(out, key=lambda r: (-len(r), r[0]))


def route_link(points: list[tuple[float, float]]) -> str:
    """Ссылка на маршрут Google Maps: последняя точка — цель, прочие — остановки."""
    from urllib.parse import quote

    *stops, last = [f"{lat:.5f},{lon:.5f}" for lat, lon in points]
    url = f"https://www.google.com/maps/dir/?api=1&destination={last}&travelmode=driving"
    if stops:
        url += "&waypoints=" + quote("|".join(stops), safe=",")
    return url


def main() -> int:
    import geopandas as gpd

    parser = argparse.ArgumentParser()
    # По умолчанию — весь список: к каждому объекту нужно доказательство,
    # а не только к «не разобрать».
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--out", default="docs/FIELD.md")
    args = parser.parse_args()

    if not PUBLISHED.exists():
        print(f"нет {PUBLISHED}")
        return 1

    data = gpd.read_file(PUBLISHED).to_crs(4326)
    data["worth"] = data["visual_check"].map(WORTH).fillna(0)
    data = data[data["worth"] > 0].copy()
    if data.empty:
        print("нечего снимать")
        return 1

    # Внутри группы — от крупных к мелким: крупные и заметнее на месте, и
    # весомее в оценке ущерба.
    data = data.sort_values(["worth", "area_m2"], ascending=[False, False])

    # Две подтверждённые свалки поднимаются в список принудительно.
    #
    # По чистой ценности знания их обходят все «не разобрать», и первая
    # версия списка состояла из одних неясных объектов. Но на защите нужна
    # фотография именно свалки — рядом со спутниковым снимком того же
    # места. Без неё вся цепочка «спутник нашёл → человек опознал →
    # выехали» обрывается на последнем шаге.
    top = data.head(args.top)
    dumps = data[data["visual_check"] == "landfill"].head(2)
    missing = dumps[~dumps["candidate_id"].isin(top["candidate_id"])]
    if len(missing):
        import pandas as pd
        top = gpd.GeoDataFrame(pd.concat([top, missing], ignore_index=True),
                               crs=data.crs)

    lines = [
        "# Куда ехать с камерой",
        "",
        "Считается скриптом `scripts/field_list.py`, а не выбирается на глаз.",
        "Пересобрать после каждого пересчёта: номера объектов живут до",
        "следующего прогона.",
        "",
        "**Одна фотография с земли стоит больше любой правки кода.** Она",
        "закрывает вопрос «а вы там были?», на который иначе ответить нечем.",
        "",
        "## Порядок",
        "",
        "**Сначала «не разобрать».** Это места, где спутник исчерпан: человек",
        "посмотрел снимок 0,5 м и не смог решить. Поездка превращает",
        "неизвестность в факт — и «да, свалка», и «нет, не свалка» одинаково",
        "ценны.",
        "",
        "**Потом опознанные как свалка.** Там поездка добавляет не знание, а",
        "доказательство: без кадра с земли акт держится только на снимке.",
        "",
        "## Список",
        "",
        "| # | Объект | Что известно | Площадь | Возникла | Координаты | Карта |",
        "|---:|---|---|---:|---|---|---|",
    ]
    said = {"unclear": "**не разобрать** — ради этого и ехать",
            "landfill": "опознана как свалка"}
    for i, row in enumerate(top.itertuples(), 1):
        point = row.geometry.centroid
        coords = f"{point.y:.5f}, {point.x:.5f}"
        # Пробел вместо запятой в разряде тысяч ставится ТОЛЬКО в площади.
        # Первая версия применяла replace ко всей строке разом и заодно
        # ломала ссылку: в адресе Google Maps координаты разделяются
        # запятой, и «?q=51.18308 71.51942» ведёт в никуда.
        area = f"{row.area_m2:,.0f}".replace(",", " ")
        link = f"[открыть](https://www.google.com/maps?q={point.y:.5f},{point.x:.5f})"
        lines.append(
            f"| {i} | `{row.candidate_id}` | {said.get(row.visual_check, '—')} | "
            f"{area} м² | {str(row.break_date)[:7]} | `{coords}` | {link} |"
        )

    # Поездки: считаются в метрах, в той же проекции, что прогон.
    metric = top.to_crs(32642)
    xy = [(p.x, p.y) for p in metric.geometry.centroid]
    origin = gpd.GeoSeries(gpd.points_from_xy([START[1]], [START[0]]), crs=4326).to_crs(32642)
    plan = trips(xy, start=(origin.x.iloc[0], origin.y.iloc[0]))
    rows = list(top.itertuples())
    lines += [
        "",
        "## Поездки",
        "",
        f"Точки ближе {TRIP_GAP_M / 1000:.0f} км друг к другу собраны в одну поездку, объезд — от",
        "центра города к ближайшей ещё не посещённой точке. Ссылка открывает",
        "маршрут в Google Maps на телефоне; номера — из списка выше.",
        "",
        "| Поездка | Объекты по порядку объезда | «Не разобрать» | Маршрут |",
        "|---:|---|---:|---|",
    ]
    bot_trips = []
    for k, route in enumerate(plan, 1):
        order = " → ".join(f"{i + 1} `{rows[i].candidate_id}`" for i in route)
        unsure = sum(rows[i].visual_check == "unclear" for i in route)
        stops = [(rows[i].geometry.centroid.y, rows[i].geometry.centroid.x) for i in route]
        lines.append(f"| {k} | {order} | {unsure} | [открыть маршрут]({route_link(stops)}) |")
        bot_trips.append({"trip": k, "ids": [str(rows[i].candidate_id) for i in route],
                          "unclear": int(unsure), "url": route_link(stops)})
    # Те же поездки — боту: команда /route отдаёт их выездной группе.
    TRIPS.write_text(json.dumps(bot_trips, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # Места, найденные сканированием снимка (scripts/scan_review.py sites):
    # их просмотрел только ИИ-проверяющий, поэтому они — отдельным списком,
    # не на карте и не в суммах. Выезд к ним решает, есть ли там свалка.
    scanned = []
    for path in sorted(Path("data/scan").glob("new_sites_*.geojson")):
        frame = gpd.read_file(path)
        scanned += [r for r in frame.itertuples() if bool(getattr(r, "new", False))]
    if scanned:
        lines += [
            "",
            "## Найдено сканированием снимка — проверить",
            "",
            "Эти места нашёл второй способ поиска — сканирование снимка высокого",
            "разрешения (AI_RESULTS.md, 1ц). Их нет ни среди находок детектора, ни",
            "на открытой карте госмониторинга. **Их смотрел только ИИ-проверяющий,",
            "человек ещё нет**: на карту и в суммы они не входят, пока выезд или",
            "просмотр человеком их не подтвердит.",
            "",
            "| # | Окон со свалкой | Координаты | Карта |",
            "|---:|---:|---|---|",
        ]
        for k, r in enumerate(sorted(scanned, key=lambda r: -r.windows), 1):
            p = r.geometry
            lines.append(f"| {k} | {r.windows} | `{p.y:.5f}, {p.x:.5f}` | "
                         f"[открыть](https://www.google.com/maps?q={p.y:.5f},{p.x:.5f}) |")
    lines += [
        "",
        "## Что снимать на месте",
        "",
        "Четыре кадра на объект, и все четыре нужны для акта:",
        "",
        "1. **Общий план** — видно границы кучи и что вокруг.",
        "2. **Вблизи** — видно, из чего она: бытовой мусор, строительный,",
        "   грунт. От этого зависит и оценка ущерба, и статья.",
        "3. **Подъезд** — колеи, ворота, съезд с дороги. Показывает, что",
        "   возят, а не занесло ветром.",
        "4. **Экран телефона с координатами** рядом с кучей — это привязка",
        "   кадра к месту, понятная человеку без компьютера.",
        "",
        "**Геометка и дата в камере телефона включаются до выезда.** Без них",
        "выезд не засчитается: программа сверяет координаты из кадра с",
        "контуром объекта.",
        "",
        "## Что делать с фотографиями",
        "",
        "1. Скопировать кадры с телефона **файлами** — не пересылать через",
        "   мессенджер: он вырезает координаты.",
        "2. Положить в `data/field/<номер объекта>/`.",
        "3. Дописать запись в `ground_truth.json`: номер, вердикт",
        "   (свалка / не свалка / не понятно), кто был на месте, дата.",
        "4. `python scripts/attach_ground.py`, затем",
        "   `python scripts/publish_filter.py`.",
        "",
        "Выезд засчитывается, только если хотя бы один кадр снят не дальше",
        "150 м от контура объекта, не позже даты записи и не раньше, чем",
        "объект возник (`config/default.yaml`, раздел `field_check`).",
        "Запись без такого кадра отклоняется, и скрипт пишет, что с ней не",
        "так. Подтверждение, которое нечем проверить, — утверждение, и",
        "выдавать его за проверку нельзя.",
        "",
    ]
    out = Path(args.out)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"── {len(data)} объектов стоят поездки, записано {len(top)} в {out}")
    for row in top.itertuples():
        p = row.geometry.centroid
        area = f"{row.area_m2:7,.0f}".replace(",", " ")
        print(f"  {row.candidate_id}  {row.visual_check:10s} {area} м²  "
              f"{p.y:.5f}, {p.x:.5f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
