"""Почему детектор не нашёл свалки госмониторинга — по пикселям каждой.

scripts/gov_waste.py показал: из свалок открытого госмониторинга (снимок
KazEOSat-1 от 19 апреля 2026) в наших областях детектор нашёл около 10%.
Здесь для каждой такой свалки детектор запускается заново на окне вокруг
неё (как scripts/detector_diagnose.py), и по пикселям ВНУТРИ полигона
считается:

  veg_before      — доля пикселей, где до 2020 года была растительность
                    (медиана NDVI лета ≥ 0,3). Метод ищет её исчезновение:
                    свалка на голом грунте для него невидима;
  has_break       — доля пикселей с подтверждённым разрывом;
  break_late      — доля пикселей, где разрыв найден, но позже 2024-го:
                    его ещё нельзя подтвердить (окно восстановления);
  ndvi_drop_low, bsi_rise_low, zscore_low, recovered — какое условие
                    отсекло разрыв.

    python scripts/gov_diagnose.py [--n 40] [--shard 0/3]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
GOV = ROOT / "data/gov_waste/astana.geojson"
REPORT = ROOT / "data/eval/gov_waste.json"
CACHE = ROOT / "data/eval/gov_diag"
HALF_M = 200
VEG = 0.3


def diagnose(geometry, settings, catalog) -> dict:
    import geopandas as gpd
    import numpy as np
    import pandas as pd
    from rasterio.features import geometry_mask
    from rasterio.transform import from_bounds
    from shapely.geometry import box

    from vantage.aoi import AOI
    from vantage.change import detect
    from vantage.raster import build_feature_cube, series_to_matrix

    crs = settings.project.crs_working
    c = geometry.representative_point()
    window = box(c.x - HALF_M, c.y - HALF_M, c.x + HALF_M, c.y + HALF_M)
    aoi = AOI.from_bbox(tuple(gpd.GeoSeries([window], crs=crs).to_crs(4326).total_bounds),
                        name="gov", crs_working=crs)
    cube = build_feature_cube(aoi, settings, catalog.sentinel2_items(aoi, settings),
                              variables=["ndvi", "bsi"])
    ndvi, dates, shape = series_to_matrix(cube, "ndvi")
    bsi, _, _ = series_to_matrix(cube, "bsi")
    ny, nx = shape
    xs, ys = cube["x"].values, cube["y"].values
    res_x, res_y = abs(xs[1] - xs[0]), abs(ys[1] - ys[0])
    transform = from_bounds(xs.min() - res_x / 2, ys.min() - res_y / 2,
                            xs.max() + res_x / 2, ys.max() + res_y / 2, nx, ny)
    inside = ~geometry_mask([geometry.buffer(5)], out_shape=(ny, nx), transform=transform)
    if ys[0] < ys[-1]:
        inside = inside[::-1]
    sel = inside.ravel()
    if sel.sum() == 0:
        sel = np.zeros(ny * nx, dtype=bool)
        sel[(ny // 2) * nx + nx // 2] = True

    result = detect(ndvi, bsi, dates, settings.change)
    cfg = settings.change
    when = pd.DatetimeIndex(dates)
    early = (when.year <= 2019) & when.month.isin([6, 7, 8])
    # Матрица — (время, пиксели): series_to_matrix.
    n_pix = ndvi.shape[1]
    before = (np.nanmedian(ndvi[early, :], axis=0) if early.any()
              else np.full(n_pix, np.nan))
    late = (when.year >= 2025) & when.month.isin([6, 7, 8])
    after = np.nanmedian(ndvi[late, :], axis=0) if late.any() else np.full(n_pix, np.nan)
    found = result.zscore > 0
    idx = np.asarray(result.break_index)
    years = np.array([when[i].year if 0 <= i < len(when) else 0 for i in idx])
    return {
        "pixels": int(sel.sum()),
        "veg_before": round(float((before[sel] >= VEG).mean()), 3),
        "ndvi_before": round(float(np.nanmedian(before[sel])), 3),
        "ndvi_after": round(float(np.nanmedian(after[sel])), 3),
        "has_break": round(float(result.has_break[sel].mean()), 3),
        "break_found": round(float(found[sel].mean()), 3),
        "break_late": round(float((found[sel] & (years[sel] >= 2025)).mean()), 3),
        "zscore_low": round(float((result.zscore[sel] < cfg.breakpoint_zscore).mean()), 3),
        "ndvi_drop_low": round(float((result.ndvi_drop[sel] < cfg.min_ndvi_drop).mean()), 3),
        "bsi_rise_low": round(float((result.bsi_rise[sel] < cfg.min_bsi_rise).mean()), 3),
        "recovered": round(float(result.recovered[sel].mean()), 3),
    }


def main() -> int:
    import geopandas as gpd
    import pandas as pd

    from vantage import env
    from vantage.catalog import StacCatalog
    from vantage.config import load_settings

    env.configure()
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=40)
    parser.add_argument("--shard", default="0/1")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    shard, shards = (int(x) for x in args.shard.split("/"))
    CACHE.mkdir(parents=True, exist_ok=True)

    report = json.loads(REPORT.read_text(encoding="utf-8"))
    table = pd.DataFrame(report["rows"]).drop_duplicates("gov_id")
    # Выборка: все найденные (для сравнения) и пропущенные — равномерно по площади.
    missed = table[~table["found"]].sort_values("gov_area_m2")
    step = max(1, len(missed) // args.n)
    picks = pd.concat([table[table["found"]], missed.iloc[::step].head(args.n)])

    if not args.summary:
        settings = load_settings()
        catalog = StacCatalog()
        gov = gpd.read_file(GOV).to_crs(settings.project.crs_working).set_index("OBJECTID")
        for i, row in enumerate(picks.itertuples()):
            path = CACHE / f"{row.gov_id}.json"
            if i % shards != shard or path.exists():
                continue
            try:
                r = diagnose(gov.loc[row.gov_id].geometry, settings, catalog)
            except Exception as error:
                print(f"   {row.gov_id}: {type(error).__name__} {str(error)[:80]}", flush=True)
                continue
            r.update({"gov_id": int(row.gov_id), "found": bool(row.found),
                      "gov_area_m2": int(row.gov_area_m2), "area": row.area})
            path.write_text(json.dumps(r), encoding="utf-8")
            print(f"   {row.gov_id} {'найдена ' if row.found else 'пропущена'} "
                  f"{row.gov_area_m2:7.0f} м²: растительность до {r['veg_before']:.0%}, "
                  f"разрыв {r['has_break']:.0%}, поздний {r['break_late']:.0%}", flush=True)

    rows = [json.loads(p.read_text(encoding="utf-8")) for p in CACHE.glob("*.json")]
    if not rows:
        return 1
    d = pd.DataFrame(rows)
    print(f"\n── разобрано {len(d)} свалок (найдено детектором {int(d['found'].sum())})")
    cols = ["veg_before", "ndvi_before", "ndvi_after", "has_break", "break_found",
            "break_late", "ndvi_drop_low", "bsi_rise_low", "zscore_low", "recovered"]
    print(d.groupby("found")[cols].median().round(2).T.to_string())
    missed_d = d[~d["found"]]
    bare = (missed_d["veg_before"] < 0.5).mean()
    late = (missed_d["break_late"] >= 0.3).mean()
    print(f"\n   среди пропущенных: на голом месте с 2019 года {bare:.0%}, "
          f"с поздним разрывом (2025+) {late:.0%}")
    (ROOT / "data/eval/gov_diagnose.json").write_text(
        json.dumps({"n": len(d), "missed_bare_share": round(float(bare), 3),
                    "missed_late_share": round(float(late), 3),
                    "median_by_found": d.groupby("found")[cols].median().round(3).to_dict()},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
