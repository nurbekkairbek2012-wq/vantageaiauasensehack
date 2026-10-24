"""Контекст, которого не видит OpenStreetMap: здания, застройка, форма пятна.

── Зачем ───────────────────────────────────────────────────────────────

Из 59 находок северного кольца, дошедших до человека, 44 оказались не
свалками, и почти все они — склады, стройки и новая застройка. Отсеять их
должен был контекстный фильтр по OpenStreetMap, но новая застройка вокруг
Астаны в OSM не нанесена. Здесь три источника, которые от OSM не зависят:

  bldg_share    — доля контура под зданиями из глобальной карты Microsoft
                  (Global ML Building Footprints, выпуск 2026 года);
  lulc_built    — доля контура, которую карта земного покрова Impact
                  Observatory (10 м, по годам) в последний год называет
                  застройкой, и была ли она застройкой до разрыва;
  rectangularity — насколько пятно похоже на прямоугольник: площадь,
                  делённая на площадь описанного повёрнутого прямоугольника.
                  Здание и площадка — около 1, ссыпка — рваная.

Каждый признак проверяется на экзамене (data/eval/labeled.geojson) тем же
правилом, что и всё остальное: сколько не-свалок снимает, не потеряв ни
одной опознанной свалки. Пороги выбраны до экзамена, по физике, а не
подобраны под восемь свалок.

    python scripts/context_extra.py [--target eval|site]
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

EVAL = ROOT / "data/eval/labeled.geojson"
SITE = ROOT / "web-next/public/data/candidates.geojson"
CACHE = ROOT / "data/cache/buildings"
OUT = ROOT / "data/eval"

LINKS = "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
METRIC = 32642
#: Класс «застройка» в карте Impact Observatory (io-lulc-annual-v02).
LULC_BUILT = 7


def quadkey(lat: float, lon: float, level: int) -> str:
    import math

    lat = max(min(lat, 85.05112878), -85.05112878)
    n = 2 ** level
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    key = []
    for i in range(level, 0, -1):
        digit, mask = 0, 1 << (i - 1)
        if x & mask:
            digit += 1
        if y & mask:
            digit += 2
        key.append(str(digit))
    return "".join(key)


def buildings_for(frame):
    """Здания Microsoft, накрывающие объекты, — из кэша или из сети."""
    import geopandas as gpd
    import httpx
    import pandas as pd
    from shapely.geometry import shape

    CACHE.mkdir(parents=True, exist_ok=True)
    index = CACHE / "dataset-links.csv"
    if not index.exists():
        index.write_bytes(httpx.get(LINKS, timeout=120).content)
    links = pd.read_csv(index)
    links = links[links["Location"] == "Kazakhstan"]
    level = len(str(links["QuadKey"].iloc[0]))

    keys = {quadkey(p.y, p.x, level) for p in frame.geometry.representative_point()}
    rows = []
    for key in sorted(keys):
        part = links[links["QuadKey"].astype(str) == key]
        if part.empty:
            continue
        for url in part["Url"]:
            path = CACHE / f"{key}_{Path(url).name}"
            if not path.exists():
                path.write_bytes(httpx.get(url, timeout=300).content)
            with gzip.open(io.BytesIO(path.read_bytes()), "rt", encoding="utf-8") as f:
                for line in f:
                    rows.append(shape(json.loads(line)["geometry"]))
    return gpd.GeoDataFrame(geometry=rows, crs=4326)


def building_share(frame, buildings):
    """Доля площади каждого контура, занятая зданиями."""
    import geopandas as gpd

    shapes = frame.to_crs(METRIC)
    blds = buildings.to_crs(METRIC)
    share = []
    sindex = blds.sindex
    for geom in shapes.geometry:
        hits = blds.iloc[list(sindex.query(geom, predicate="intersects"))]
        covered = gpd.GeoSeries(hits.geometry.intersection(geom)).union_all() if len(hits) else None
        share.append(float(covered.area / geom.area) if covered is not None and geom.area else 0.0)
    return share


def rectangularity(frame):
    shapes = frame.to_crs(METRIC).geometry
    out = []
    for geom in shapes:
        box = geom.minimum_rotated_rectangle
        out.append(float(geom.area / box.area) if box.area else 0.0)
    return out


def lulc(frame):
    """Доля «застройки» в контуре: в последний год и до года разрыва.

    Карта Impact Observatory (Planetary Computer, io-lulc-annual-v02), 10 м.
    """
    import numpy as np
    import planetary_computer
    import pystac_client
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.warp import transform_geom
    from rasterio.windows import from_bounds

    catalog = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace)
    bbox = list(frame.total_bounds)
    items = list(catalog.search(collections=["io-lulc-annual-v02"], bbox=bbox).items())
    by_year: dict[int, list] = {}
    for item in items:
        year = int(str(item.properties.get("start_datetime", item.datetime))[:4])
        by_year.setdefault(year, []).append(item)
    years = sorted(by_year)
    if not years:
        return [None] * len(frame), [None] * len(frame), None

    def share(item_list, geom4326):
        for item in item_list:
            with rasterio.open(item.assets["data"].href) as src:
                g = transform_geom("EPSG:4326", src.crs, geom4326.__geo_interface__)
                from shapely.geometry import shape as _shape
                gs = _shape(g)
                b = gs.bounds
                sb = src.bounds
                if b[0] < sb.left or b[2] > sb.right or b[1] < sb.bottom or b[3] > sb.top:
                    continue
                win = from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
                win = win.__class__(win.col_off, win.row_off,
                                    max(1, win.width + 1), max(1, win.height + 1))
                data = src.read(1, window=win)
                mask = geometry_mask([g], out_shape=data.shape,
                                     transform=src.window_transform(win),
                                     invert=True, all_touched=True)
                if not mask.any():
                    return 0.0
                return float(np.mean(data[mask] == LULC_BUILT))
        return None

    last, before = [], []
    for row in frame.itertuples():
        geom = row.geometry
        last.append(share(by_year[years[-1]], geom))
        broke = str(getattr(row, "break_date", "") or "")[:4]
        prior = [y for y in years if broke and y < int(broke)]
        before.append(share(by_year[prior[-1]], geom) if prior else None)
    return last, before, years[-1]


def main() -> int:
    import geopandas as gpd

    from vantage import env

    # Путь к сертификатам с кириллицей в имени пользователя GDAL читает как
    # байты не в той кодировке; env.configure раскладывает всё по-своему.
    env.configure()

    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=("eval", "site"), default="eval")
    args = parser.parse_args()

    frame = gpd.read_file(EVAL if args.target == "eval" else SITE).to_crs(4326)
    print(f"── Контур и контекст: {len(frame)} объектов")

    buildings = buildings_for(frame)
    print(f"   зданий Microsoft в районе: {len(buildings)}")
    frame["bldg_share"] = building_share(frame, buildings)
    frame["rectangularity"] = rectangularity(frame)
    last, before, year = lulc(frame)
    frame["lulc_built"] = last
    frame["lulc_built_before"] = before
    print(f"   карта земного покрова: последний год {year}")

    cols = ["candidate_id", "bldg_share", "rectangularity", "lulc_built", "lulc_built_before"]
    if "truth" in frame:
        cols.insert(1, "truth")
        cols.insert(2, "area")
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"context_{args.target}.csv"
    frame[cols].to_csv(out, index=False)
    print(f"── записано в {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
