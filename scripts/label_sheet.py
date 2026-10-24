"""Собрать листы для разметки: объекты на снимке 0,75 м/пиксель.

── Зачем ещё один инструмент разметки ──────────────────────────────────

Страница `label.html` показывает пару чипов Sentinel-2 и живой снимок и
рассчитана на человека, который размечает мышью. Здесь другое: контактные
листы по двенадцать объектов, которые можно просмотреть подряд и быстро.

Разница не в удобстве, а в разрешении вопроса. Пара чипов отвечает «что
изменилось», и десяти метров на пиксель для этого хватает. Вопрос
разметки другой — «что это за объект», — и на десяти метрах он не
решается: свалка, карьер и стройка выглядят одинаково. Нужен снимок
высокого разрешения, а на нём изменение как раз не видно, потому что
снимок один и сегодняшний.

Поэтому лист показывает то, на что вопрос отвечается: сегодняшнее
состояние места в 0,75 м/пиксель с обведённым контуром объекта.

── Почему метки хранятся геометрией, а не по идентификатору ────────────

Идентификаторы вида ``astana_north_x001y002:C00004`` живут ровно до
следующего прогона: детектор пересчитает область, и нумерация сдвинется.
Разметка, привязанная к ним, пропадёт вместе с прогоном — а она стоит
часов человеческого времени.

Метка ставится месту. Файл ``labels_manual.geojson`` — это точки с
вердиктом, и он переживает любые пересчёты: новые кандидаты
сопоставляются с метками пространственно.

    python scripts/label_sheet.py [сколько-на-листе]
"""

import json
import math
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

OUT = Path("data/label_sheets")
SOURCE = Path("outputs_real")
PER_SHEET = int(sys.argv[1]) if len(sys.argv) > 1 else 12

#: Зум тайлов Esri. 18 — примерно 0,6 м на пиксель на этой широте: объект
#: в сорок метров занимает шестьдесят пикселей, и по нему уже видно, куча
#: это, котлован или стройплощадка.
ZOOM = 18

#: Сторона клетки на листе. Меньше 300 — не разглядеть, больше 400 — лист
#: перестаёт помещаться в один взгляд.
CELL = 340

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  body { margin:0; background:#0d0918; font-family: system-ui, sans-serif; }
  .grid { display:grid; grid-template-columns: repeat(%(cols)d, %(cell)dpx); gap:6px; padding:6px; }
  .cell { position:relative; width:%(cell)dpx; height:%(cell)dpx; overflow:hidden; background:#150f26; }
  .map { position:absolute; inset:0; }
  .tag {
    position:absolute; left:0; top:0; z-index:500;
    background:#0d0918; color:#ede9fe; font-size:15px; font-weight:700;
    padding:3px 9px; font-variant-numeric: tabular-nums;
  }
  .meta {
    position:absolute; left:0; bottom:0; right:0; z-index:500;
    background:rgba(13,9,24,.82); color:#b3a5d9; font-size:11px; padding:3px 6px;
  }
</style></head><body>
<div class="grid">%(cells)s</div>
<script>
var items = %(items)s;
items.forEach(function (item) {
  var m = L.map('m' + item.n, {
    zoomControl:false, attributionControl:false, dragging:false,
    scrollWheelZoom:false, doubleClickZoom:false, boxZoom:false, keyboard:false
  }).setView([item.lat, item.lon], %(zoom)d);
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {maxZoom:19}).addTo(m);
  L.geoJSON(item.geom, {style:{color:'#ffffff', weight:2, fill:false, dashArray:'4 3'}}).addTo(m);
});
</script></body></html>"""


def already_labelled() -> gpd.GeoDataFrame | None:
    """Уже размеченные места.

    Разметка стоит времени глаз, и переспрашивать про то же место после
    каждого прогона — самый дешёвый способ её обесценить. Метки хранятся
    геометрией, поэтому переживают пересчёт: новый кандидат в том же месте
    сопоставляется со старой меткой пространственно.
    """
    path = Path("labels_manual.geojson")
    if not path.exists():
        return None
    return gpd.read_file(path)


def drop_labelled(selection: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Убрать из показа то, про что уже спрашивали."""
    known = already_labelled()
    if known is None or known.empty or selection.empty:
        return selection
    joined = gpd.sjoin(
        selection, known.to_crs(selection.crs)[["geometry"]], predicate="contains", how="left"
    )
    fresh = selection[joined["index_right"].isna().reindex(selection.index, fill_value=True)]
    skipped = len(selection) - len(fresh)
    if skipped:
        print(f"пропущено уже размеченных: {skipped}")
    return fresh


def build_selection() -> gpd.GeoDataFrame:
    """Что показывать: смесь прошедших отсев и отклонённых.

    Учить только на прошедших нельзя — модель не увидит ни одного
    отрицательного примера. Учить только на отклонённых тем более. Берём
    и тех и других, причём отклонённых по причине «совпал с объектом OSM»
    берём отдельно: это карьеры и стройки, то есть самые трудные
    отрицательные, ради которых всё и затевается.
    """
    kept = gpd.read_file(SOURCE / "candidates.geojson")
    kept["источник"] = "прошёл отсев"

    rejected = gpd.read_file(SOURCE / "rejected.geojson")
    reasons = rejected.get("reject_reason", pd.Series(dtype=object)).fillna("")

    technical = rejected[reasons.str.contains("OSM", case=False, na=False)].copy()
    technical["источник"] = "совпал с OSM"

    other = rejected[
        reasons.str.contains("жилью", case=False, na=False)
        | reasons.str.contains("подъезд", case=False, na=False)
    ].copy()
    other["источник"] = "рядом с жильём"

    # Мелкие объекты не показываем: на снимке в 0,6 м они есть, но сказать
    # по ним что-то определённое нельзя — контур в тридцать пикселей это
    # не предмет, а пятно.
    parts = []
    for frame, limit in ((kept, None), (technical, 60), (other, 30)):
        if frame.empty:
            continue
        frame = frame[frame["area_m2"] >= 1500].sort_values("area_m2", ascending=False)
        parts.append(frame if limit is None else frame.head(limit))

    selection = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=kept.crs)
    return drop_labelled(selection).to_crs(4326)


def main() -> int:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob("*.png"):
        stale.unlink()

    selection = build_selection()
    print(f"объектов к просмотру: {len(selection)}")
    print(selection["источник"].value_counts().to_string())

    cols = 4
    rows = math.ceil(PER_SHEET / cols)
    index = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(
            viewport={"width": cols * (CELL + 6) + 6, "height": rows * (CELL + 6) + 6}
        )

        for sheet_no, start in enumerate(range(0, len(selection), PER_SHEET), start=1):
            chunk = selection.iloc[start : start + PER_SHEET]
            cells, items = [], []
            for offset, (_, row) in enumerate(chunk.iterrows()):
                number = start + offset + 1
                point = row.geometry.representative_point()
                cells.append(
                    f'<div class="cell"><div class="map" id="m{number}"></div>'
                    f'<div class="tag">{number}</div>'
                    f'<div class="meta">{int(row["area_m2"])} м² · {row["источник"]}</div></div>'
                )
                items.append(
                    {
                        "n": number,
                        "lat": point.y,
                        "lon": point.x,
                        "geom": json.loads(gpd.GeoSeries([row.geometry]).to_json())["features"][0],
                    }
                )
                index.append(
                    {
                        "n": number,
                        "candidate_id": str(row.get("candidate_id", "")),
                        "area_m2": int(row["area_m2"]),
                        "источник": row["источник"],
                        "lat": round(point.y, 6),
                        "lon": round(point.x, 6),
                    }
                )

            page.set_content(
                PAGE
                % {
                    "cols": cols,
                    "cell": CELL,
                    "cells": "".join(cells),
                    "items": json.dumps(items),
                    "zoom": ZOOM,
                }
            )
            # Тайлы грузятся асинхронно; без ожидания лист выходит наполовину
            # серым, и разметка по нему невозможна.
            page.wait_for_timeout(9000)
            target = OUT / f"sheet{sheet_no:02d}.png"
            page.screenshot(path=str(target))
            print(f"{target.name}: объекты {start + 1}–{start + len(chunk)}")

        browser.close()

    (OUT / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"указатель: {OUT / 'index.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
