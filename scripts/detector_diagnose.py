"""Почему детектор пропускает места необратимой смены — по условию на пиксель.

Экзамен поиска (scripts/detector_exam.py) показал, что сырые находки
касаются лишь 11–20% мест, где независимая карта видит необратимую смену
растительности на грунт или застройку, и пропущенные места — настоящие:
новые кварталы, склады, резервуарный парк. Здесь по каждому месту
детектор запускается заново на окне вокруг него, и для пикселей места
считается, какое из условий has_break не выполнено:

  found       — поиск точки разрыва вообще её нашёл;
  zscore      — разрыв значим (breakpoint_zscore);
  ndvi_drop   — падение NDVI не меньше min_ndvi_drop;
  bsi_rise    — рост открытого грунта не меньше min_bsi_rise;
  recovered   — «растительность вернулась» в окне восстановления;
  observable  — после разрыва хватает наблюдений;
  n_valid     — хватает валидных композитов.

    python scripts/detector_diagnose.py [--n 6]
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
EXAM = ROOT / "data/eval/detector_exam_outputs_real.csv"
OUT = ROOT / "data/eval/detector_diagnose.json"
HALF_M = 150  # окно 300 × 300 м вокруг центра места


def cube_for(lat: float, lon: float, settings, catalog):
    import geopandas as gpd
    from shapely.geometry import Point, box

    from vantage.aoi import AOI
    from vantage.raster import build_feature_cube, series_to_matrix

    crs = settings.project.crs_working
    p = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(crs).iloc[0]
    window = box(p.x - HALF_M, p.y - HALF_M, p.x + HALF_M, p.y + HALF_M)
    wgs = gpd.GeoSeries([window], crs=crs).to_crs(4326)
    aoi = AOI.from_bbox(tuple(wgs.total_bounds), name="diag", crs_working=crs)
    items = catalog.sentinel2_items(aoi, settings)
    cube = build_feature_cube(aoi, settings, items, variables=["ndvi", "bsi"])
    ndvi, dates, shape = series_to_matrix(cube, "ndvi")
    bsi, _, _ = series_to_matrix(cube, "bsi")
    return ndvi, bsi, dates, shape


def reasons(result, cfg, centre):
    """Доля пикселей центра, не прошедших каждое условие."""
    import numpy as np

    from vantage.change import _MIN_VALID_OBS

    sel = centre
    found = result.zscore > 0
    out = {
        "pixels": int(sel.sum()),
        "has_break": float(result.has_break[sel].mean()),
        "no_break_found": float((~found[sel]).mean()),
        "zscore_low": float((result.zscore[sel] < cfg.breakpoint_zscore).mean()),
        "ndvi_drop_low": float((result.ndvi_drop[sel] < cfg.min_ndvi_drop).mean()),
        "bsi_rise_low": float((result.bsi_rise[sel] < cfg.min_bsi_rise).mean()),
        "recovered": float(result.recovered[sel].mean()),
        "not_observable": float((~result.observable[sel]).mean())
        if result.observable is not None else 0.0,
        "few_valid": float((result.n_valid[sel] < _MIN_VALID_OBS).mean()),
        "median_ndvi_drop": float(np.nanmedian(result.ndvi_drop[sel])),
        "median_bsi_rise": float(np.nanmedian(result.bsi_rise[sel])),
        "median_zscore": float(np.nanmedian(result.zscore[sel])),
    }
    return {k: round(v, 3) if isinstance(v, float) else v for k, v in out.items()}


def main() -> int:
    import numpy as np
    import pandas as pd

    from vantage import env
    from vantage.catalog import StacCatalog
    from vantage.change import detect
    from vantage.config import load_settings

    env.configure()
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=6)
    args = parser.parse_args()

    settings = load_settings()
    catalog = StacCatalog()
    table = pd.read_csv(EXAM)
    early = table[table["year"] <= 2021]
    picks = pd.concat([
        early[~early["found"]].sort_values("pixels", ascending=False).head(args.n).assign(kind="пропущено"),
        early[early["found"]].sort_values("pixels", ascending=False).head(args.n // 2).assign(kind="найдено"),
    ])

    report = []
    for _, row in picks.iterrows():
        try:
            ndvi, bsi, dates, shape = cube_for(row.lat, row.lon, settings, catalog)
        except Exception as error:
            print(f"   {row.lat:.4f},{row.lon:.4f}: {type(error).__name__} {str(error)[:80]}")
            continue
        result = detect(ndvi, bsi, dates, settings.change)
        ny, nx = shape
        yy, xx = np.mgrid[0:ny, 0:nx]
        centre = ((abs(yy - ny // 2) <= 2) & (abs(xx - nx // 2) <= 2)).ravel()
        r = reasons(result, settings.change, centre)
        r.update({"kind": row.kind, "lat": round(row.lat, 5), "lon": round(row.lon, 5),
                  "map_year": int(row.year), "hectares": round(row.pixels / 100, 1)})
        report.append(r)
        print(f"   {row.kind:10} {row.pixels / 100:5.1f} га {row.year}: найдено {r['has_break']:.0%}; "
              f"отсекло: восстановилось {r['recovered']:.0%}, z мал {r['zscore_low']:.0%}, "
              f"NDVI мал {r['ndvi_drop_low']:.0%}, BSI мал {r['bsi_rise_low']:.0%}, "
              f"не наблюдаемо {r['not_observable']:.0%}")
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"── записано в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
