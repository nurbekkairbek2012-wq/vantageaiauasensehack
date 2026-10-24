"""Признаки поведения пятна во времени — по каждому объекту экзамена.

── Зачем ───────────────────────────────────────────────────────────────

Три четверти ложных находок — стройки и склады. Спектр их от свалки не
отличает (AI_RESULTS.md, 1а), а вот ПОВЕДЕНИЕ во времени должно:

  break_spread_months — разброс дат разрыва по пикселям пятна (межквартиль-
                        ный размах, в месяцах). Свалку ссыпают постепенно,
                        и её части появляются в разные месяцы; стройплощадку
                        расчищают за раз.
  growth_share        — доля пикселей пятна, сломавшихся позже чем через
                        12 месяцев после первых. Свалка расползается.
  after_amplitude     — сезонный размах NDVI после разрыва. Крыша и бетон
                        ровные круглый год; на свалке и вокруг неё по краям
                        прорастает трава, зимой лежит снег по-разному.
  ndbi_after, ndbi_rise — индекс застройки (SWIR1 − NIR)/(SWIR1 + NIR) после
                        разрыва и его рост. Крыши и бетон дают высокий NDBI.

Детектор запускается заново на окне вокруг объекта — теми же функциями и
с тем же конфигом, что в прогоне, — чтобы получить разрыв по каждому
пикселю: в выгрузке прогона хранится только итог по пятну.

Считается долго (минута-две на объект, снимки по сети), поэтому каждый
объект кэшируется в data/eval/objects_ts/, и повторный запуск продолжает
с места остановки.

    python scripts/object_timeseries.py [--target eval|site] [--limit N]
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
EVAL = ROOT / "data/eval/labeled.geojson"
SITE = ROOT / "web-next/public/data/candidates.geojson"
CACHE = ROOT / "data/eval/objects_ts"
PAD_M = 60


def features_for(geometry_metric, settings, catalog) -> dict:
    import geopandas as gpd
    import numpy as np
    from rasterio.features import geometry_mask
    from rasterio.transform import from_origin
    from shapely.geometry import box

    from vantage.aoi import AOI
    from vantage.change import detect
    from vantage.raster import build_feature_cube, series_to_matrix

    crs = settings.project.crs_working
    minx, miny, maxx, maxy = geometry_metric.bounds
    window = box(minx - PAD_M, miny - PAD_M, maxx + PAD_M, maxy + PAD_M)
    wgs = gpd.GeoSeries([window], crs=crs).to_crs(4326)
    aoi = AOI.from_bbox(tuple(wgs.total_bounds), name="obj", crs_working=crs)
    items = catalog.sentinel2_items(aoi, settings)
    cube = build_feature_cube(aoi, settings, items, variables=["ndvi", "bsi", "ndmi"])
    ndvi, dates, (ny, nx) = series_to_matrix(cube, "ndvi")
    bsi, _, _ = series_to_matrix(cube, "bsi")
    ndmi, _, _ = series_to_matrix(cube, "ndmi")

    xs, ys = np.asarray(cube["x"].values), np.asarray(cube["y"].values)
    res_x, res_y = abs(float(xs[1] - xs[0])), abs(float(ys[1] - ys[0]))
    transform = from_origin(float(xs.min()) - res_x / 2, float(ys.max()) + res_y / 2, res_x, res_y)
    flip = ys[0] < ys[-1]
    inside = geometry_mask([geometry_metric], out_shape=(ny, nx), transform=transform,
                           invert=True, all_touched=True)
    if flip:
        inside = inside[::-1]
    inside = inside.ravel()

    result = detect(ndvi, bsi, dates, settings.change)
    months = (dates.astype("datetime64[M]").astype(int))
    broke = result.has_break & inside
    out = {"pixels": int(inside.sum()), "broken_pixels": int(broke.sum())}
    if broke.sum() >= 2:
        idx = result.break_index[broke]
        m = months[idx]
        out["break_spread_months"] = float(np.percentile(m, 75) - np.percentile(m, 25))
        out["growth_share"] = float(np.mean(m > m.min() + 12))
    else:
        out["break_spread_months"] = 0.0
        out["growth_share"] = 0.0

    # После разрыва — по пикселям пятна; разрыв пятна — медиана по пикселям,
    # а если детектор здесь ничего не нашёл — середина ряда.
    ref = int(np.median(result.break_index[broke])) if broke.any() else len(dates) // 2
    sel = inside
    after_ndvi = np.nanmean(ndvi[ref:, sel], axis=1)
    years = dates[ref:].astype("datetime64[Y]").astype(int)
    amps = []
    for y in np.unique(years):
        v = after_ndvi[years == y]
        v = v[np.isfinite(v)]
        if len(v) >= 3:
            amps.append(float(v.max() - v.min()))
    out["after_amplitude"] = float(np.median(amps)) if amps else None
    ndbi = -ndmi[:, sel]
    before = np.nanmean(ndbi[:ref]) if ref > 0 else np.nan
    after = np.nanmean(ndbi[ref:])
    out["ndbi_after"] = float(after) if np.isfinite(after) else None
    out["ndbi_rise"] = float(after - before) if np.isfinite(after) and np.isfinite(before) else None
    return out


def main() -> int:
    import geopandas as gpd

    from vantage import env
    from vantage.catalog import StacCatalog
    from vantage.config import load_settings

    env.configure()
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=("eval", "site"), default="eval")
    parser.add_argument("--limit", type=int, default=None)
    # Параллельные запуски делят объекты по остатку от деления номера
    # строки: --shard 0/3, 1/3, 2/3. Снимки качаются по сети, и три потока
    # втрое быстрее одного.
    parser.add_argument("--shard", default="0/1")
    args = parser.parse_args()
    shard, shards = (int(x) for x in args.shard.split("/"))

    settings = load_settings()
    catalog = StacCatalog()
    frame = gpd.read_file(EVAL if args.target == "eval" else SITE)
    if args.target == "site":
        frame["candidate_id"] = "site:" + frame["candidate_id"].astype(str)
    frame = frame.to_crs(settings.project.crs_working)
    CACHE.mkdir(parents=True, exist_ok=True)

    done = 0
    for i, row in enumerate(frame.itertuples()):
        if i % shards != shard:
            continue
        path = CACHE / f"{str(row.candidate_id).replace(':', '_')}.json"
        if path.exists():
            continue
        if args.limit is not None and done >= args.limit:
            break
        try:
            feats = features_for(row.geometry, settings, catalog)
        except Exception as error:
            print(f"   {row.candidate_id}: {type(error).__name__} {str(error)[:90]}", flush=True)
            continue
        path.write_text(json.dumps(feats, ensure_ascii=False), encoding="utf-8")
        done += 1
        print(f"   {row.candidate_id:34} разброс {feats['break_spread_months']:5.1f} мес, "
              f"рост {feats['growth_share']:.2f}, пикселей {feats['broken_pixels']}/"
              f"{feats['pixels']}", flush=True)
    total = len(list(CACHE.glob("*.json")))
    print(f"── готово объектов: {total} (новых {done}) → {CACHE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
