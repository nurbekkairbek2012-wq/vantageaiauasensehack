"""Выгрузка реестра в форматы, которые открывают без нашего сайта.

── Зачем ───────────────────────────────────────────────────────────────

Сайт — наш интерфейс, а у заказчика свои: ГИС отдела, Excel у
специалиста, навигатор в телефоне инспектора. Если объект нельзя унести в
привычный инструмент, его перерисуют руками, и первая же опечатка в
координатах отправит машину не туда.

  vantage_objects.kml      Google Earth, 2ГИС, QGIS — контуры и карточки
  vantage_objects.gpx      навигатор в телефоне: точки для выезда
  vantage_objects.csv      Excel: одна строка — один объект
  vantage_objects.geojson  ГИС: то же, что на карте, без служебных полей

Порядок везде — очередь по деньгам, та же, что в панели «С чего начать».

    python scripts/export_register.py
"""

from __future__ import annotations

import csv
import json
import sys
from html import escape
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web-next/public/data"
OUT = DATA / "export"
LIVE = "https://hakathon-lyart.vercel.app"

STATUS = {
    "landfill": "опознан по снимку как свалка",
    "unclear": "по снимку не разобрать — нужен выезд",
    "not_landfill": "не свалка",
}

#: Поля, которые уходят наружу. Внутренние (ранги, служебные оценки) — нет.
FIELDS = ("n", "candidate_id", "status", "lat", "lon", "area_m2", "break_date",
          "mass_t", "removal_kzt", "recyclable_kzt", "damage_p50_kzt", "penalty_kzt",
          "field_check", "map_link", "google_maps")


def rows():
    import geopandas as gpd

    site = gpd.read_file(DATA / "candidates.geojson").to_crs(4326)
    economy = json.loads((DATA / "economy.json").read_text(encoding="utf-8"))
    econ = {o["id"]: o for o in economy["objects"]}
    order = {p["id"]: p["n"] for p in economy["priority"]}
    out = []
    for _, row in site.iterrows():
        cid = str(row["candidate_id"])
        e = econ.get(cid, {})
        point = row.geometry.representative_point()
        out.append({
            "n": order.get(cid, 999),
            "candidate_id": cid,
            "status": ("подтверждён выездом" if row.get("check_source") == "ground"
                       else STATUS.get(str(row.get("visual_check")), "не проверен")),
            "lat": round(point.y, 6),
            "lon": round(point.x, 6),
            "area_m2": round(float(row.get("area_m2") or 0)),
            "break_date": str(row.get("break_date") or "")[:7],
            "mass_t": round(float(e.get("mass_t", 0))),
            "removal_kzt": round(float(e.get("removal_kzt", 0))),
            "recyclable_kzt": round(float(e.get("recyclable_kzt", 0))),
            "damage_p50_kzt": round(float(e.get("damage_p50", row.get("damage_p50") or 0))),
            "penalty_kzt": round(float(e.get("penalty_kzt", row.get("penalty_kzt") or 0))),
            "field_check": "да" if row.get("check_source") == "ground" else "нет",
            "map_link": f"{LIVE}/map.html?object={cid}",
            "google_maps": f"https://www.google.com/maps?q={point.y:.6f},{point.x:.6f}",
            "_geometry": row.geometry,
        })
    return sorted(out, key=lambda r: r["n"])


def to_csv(items, path: Path) -> None:
    # utf-8-sig: без метки порядка байтов Excel открывает кириллицу кракозябрами.
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, delimiter=";")
        writer.writeheader()
        for item in items:
            writer.writerow({k: item[k] for k in FIELDS})


def _ring(coords) -> str:
    return " ".join(f"{x:.6f},{y:.6f},0" for x, y in coords)


def to_kml(items, path: Path) -> None:
    style = {"опознан по снимку как свалка": "dump", "подтверждён выездом": "ground"}
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
        "<name>Vantage AI — очередь выезда</name>",
        '<Style id="dump"><LineStyle><color>ff3a1d7c</color><width>3</width></LineStyle>'
        "<PolyStyle><color>553a1d7c</color></PolyStyle></Style>",
        '<Style id="pending"><LineStyle><color>ff0b8ae3</color><width>3</width></LineStyle>'
        "<PolyStyle><color>550b8ae3</color></PolyStyle></Style>",
        '<Style id="ground"><LineStyle><color>ff3ab91f</color><width>3</width></LineStyle>'
        "<PolyStyle><color>553ab91f</color></PolyStyle></Style>",
    ]
    for it in items:
        geom = it["_geometry"]
        polys = getattr(geom, "geoms", [geom])
        shapes = "".join(
            "<Polygon><outerBoundaryIs><LinearRing><coordinates>"
            f"{_ring(p.exterior.coords)}</coordinates></LinearRing></outerBoundaryIs></Polygon>"
            for p in polys)
        body = (f"{escape(it['status'])}<br/>Площадь {it['area_m2']} м², возник {it['break_date']}"
                f"<br/>Ущерб ~{it['damage_p50_kzt'] / 1e6:.1f} млн ₸".replace(".", ",")
                + f"<br/><a href=\"{it['map_link']}\">карточка на карте</a>")
        parts.append(
            f"<Placemark><name>{it['n']}. {escape(it['candidate_id'])}</name>"
            f"<styleUrl>#{style.get(it['status'], 'pending')}</styleUrl>"
            f"<description><![CDATA[{body}]]></description>"
            f"<MultiGeometry><Point><coordinates>{it['lon']:.6f},{it['lat']:.6f},0</coordinates>"
            f"</Point>{shapes}</MultiGeometry></Placemark>")
    parts.append("</Document></kml>")
    path.write_text("\n".join(parts), encoding="utf-8")


def to_gpx(items, path: Path) -> None:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="Vantage AI" xmlns="http://www.topografix.com/GPX/1/1">',
    ]
    for it in items:
        parts.append(
            f'<wpt lat="{it["lat"]:.6f}" lon="{it["lon"]:.6f}">'
            f"<name>{it['n']}. {escape(it['candidate_id'])}</name>"
            f"<desc>{escape(it['status'])}, {it['area_m2']} м²</desc></wpt>")
    parts.append("</gpx>")
    path.write_text("\n".join(parts), encoding="utf-8")


def to_geojson(items, path: Path) -> None:
    from shapely.geometry import mapping

    features = [{
        "type": "Feature",
        "geometry": mapping(it["_geometry"]),
        "properties": {k: it[k] for k in FIELDS},
    } for it in items]
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                               ensure_ascii=False), encoding="utf-8")


def main() -> int:
    items = rows()
    OUT.mkdir(parents=True, exist_ok=True)
    to_csv(items, OUT / "vantage_objects.csv")
    to_kml(items, OUT / "vantage_objects.kml")
    to_gpx(items, OUT / "vantage_objects.gpx")
    to_geojson(items, OUT / "vantage_objects.geojson")
    print(f"── Реестр: {len(items)} объектов → {OUT.relative_to(ROOT)} (csv, kml, gpx, geojson)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
