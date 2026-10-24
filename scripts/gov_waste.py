"""Свалки из открытого госмониторинга — независимая проверка нашего поиска.

── Откуда ──────────────────────────────────────────────────────────────

Министерство экологии с 2018 года ведёт космический мониторинг мест
размещения отходов вместе с АО «НК «Қазақстан Ғарыш Сапары»: снимки
KazEOSat-1 (1 м), дешифрирование, затем проверка департаментами экологии.
Результаты открыто показываются на геосервисе https://wasteopen.gharysh.kz/;
его слой «Карта местоположения отходов» — ArcGIS MapServer с разрешённым
запросом (KM_Waste/2022_Othodi_open/MapServer/0).

Берётся ТОЛЬКО этот открытый слой. На том же сервере есть и другие
сервисы отходов, но открытыми они не помечены (закрытый портал —
waste.gharysh.kz), и их мы не трогаем.

Условия использования слоя на сайте не указаны: для проверки и
исследования — да; для обучения коммерческой модели — **спросить** у
Ғарыш Сапары (docs/LICENSES.md).

── Что считается ───────────────────────────────────────────────────────

Для каждой свалки госмониторинга в пределах наших посчитанных областей:

  * нашёл ли её наш детектор (кандидат не дальше MATCH_M, до любого
    отсева) — полнота поиска на НЕЗАВИСИМОЙ разметке;
  * прошла ли она наш отсев и что о ней сказал человек.

    python scripts/gov_waste.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
LAYER = ("https://arcgis.gharysh.kz/server/rest/services/KM_Waste/"
         "2022_Othodi_open/MapServer/0/query")
BBOX = (71.0, 50.95, 72.0, 51.35)
OUT = ROOT / "data/gov_waste/astana.geojson"
REPORT = ROOT / "data/eval/gov_waste.json"
MATCH_M = 100.0

#: Наши посчитанные области: папка → рамка (как в scripts/key_numbers.py).
AREAS = {
    "outputs_real": (71.37, 51.12, 71.66, 51.30),
    "outputs_astana_east": (71.66, 51.10, 71.95, 51.28),
    "outputs_astana_southeast": (71.60, 51.02, 71.88, 51.18),
    "outputs_astana_industrial_west": (71.18, 51.06, 71.42, 51.22),
    "outputs_astana_south": (71.38, 50.99, 71.58, 51.15),
}


def download() -> dict:
    import httpx

    params = {"where": "1=1", "geometry": ",".join(map(str, BBOX)),
              "geometryType": "esriGeometryEnvelope", "inSR": "4326",
              "spatialRel": "esriSpatialRelIntersects", "outFields": "*",
              "outSR": "4326", "f": "geojson", "resultRecordCount": 1000}
    data = httpx.get(LAYER, params=params, timeout=120).json()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def main() -> int:
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import box

    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else download()
    gov = gpd.GeoDataFrame.from_features(data["features"], crs=4326)
    print(f"── госмониторинг: {len(gov)} объектов в рамке Астаны")
    for column in ("shooting_date", "Tip_othoda2", "Город"):
        if column in gov:
            values = gov[column]
            if column == "shooting_date":
                values = pd.to_datetime(values, unit="ms", errors="coerce").dt.year
            print(f"   {column}: {values.value_counts().head(8).to_dict()}")

    gov = gov.to_crs(32642)
    gov["centroid"] = gov.geometry.representative_point()
    rows = []
    for folder, bbox in AREAS.items():
        raw_path = ROOT / folder / "candidates_raw.geojson"
        if not raw_path.exists():
            continue
        frame = gpd.GeoDataFrame(geometry=[box(*bbox)], crs=4326).to_crs(32642)
        inside = gov[gov["centroid"].within(frame.geometry.iloc[0])]
        raw = gpd.read_file(raw_path).to_crs(32642)
        kept_path = ROOT / folder / "candidates.geojson"
        kept = gpd.read_file(kept_path).to_crs(32642) if kept_path.exists() else raw.iloc[:0]
        for _, g in inside.iterrows():
            d_raw = raw.distance(g.geometry).min() if len(raw) else float("inf")
            near = kept[kept.distance(g.geometry) <= MATCH_M] if len(kept) else kept
            verdict = (near["visual_check"].dropna().astype(str).tolist()
                       if "visual_check" in near else [])
            rows.append({"area": folder, "gov_id": int(g.get("OBJECTID", -1)),
                         "year": pd.to_datetime(g.get("shooting_date"), unit="ms",
                                                errors="coerce").year,
                         "type": g.get("Tip_othoda2"), "gov_area_m2": round(g.geometry.area),
                         "found": bool(d_raw <= MATCH_M), "distance_m": round(float(d_raw)),
                         "passed_filter": bool(len(near)),
                         "human": verdict[0] if verdict else None})
    table = pd.DataFrame(rows)
    if table.empty:
        print("   ни одной свалки госмониторинга в наших областях")
        return 1
    print(f"\n── в наших посчитанных областях: {len(table)}")
    print(table.groupby("area").agg(всего=("found", "size"), нашёл=("found", "sum"),
                                    прошли_отсев=("passed_filter", "sum")).to_string())
    total, found = len(table), int(table["found"].sum())
    print(f"\n   детектор нашёл {found} из {total} ({found / total:.0%})")
    print("   по году снимка госмониторинга:")
    print(table.groupby("year")["found"].agg(["size", "sum"]).to_string())
    print("   по площади (м²):")
    table["size_band"] = pd.cut(table["gov_area_m2"], [0, 500, 2000, 10_000, 1e9],
                                labels=["<500", "500–2000", "2000–10000", ">10000"])
    print(table.groupby("size_band", observed=False)["found"].agg(["size", "sum"]).to_string())
    REPORT.write_text(json.dumps({"total": total, "found": found, "match_m": MATCH_M,
                                  "rows": table.drop(columns="size_band").to_dict("records")},
                                 ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"── записано в {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
