"""Ряд вегетации для первого экрана — по тому объекту, о котором говорит страница.

── Зачем ───────────────────────────────────────────────────────────────

График на первом экране лендинга был снят 20 августа с объекта C00001
первого прогона. После пересчёта нумерация сменилась, а сам объект до
просмотра человеком не дошёл: подпись «Объект C00001» указывала на то,
чего нет на карте, а текст рядом говорил о самой крупной опознанной
свалке — другом объекте. Первый же вопрос «покажите C00001» оставался
без ответа.

Здесь ряд строится по герою страницы — самой крупной свалке, опознанной
человеком по снимку, — тем же путём, каким его видит детектор: месячные
композиты Sentinel-2 с маской облаков, NDVI, среднее по пикселям внутри
контура объекта.

Нужна сеть: снимки читаются из Planetary Computer.

    python scripts/make_hero_series.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DATA = ROOT / "web-next/public/data"
OUT = DATA / "hero-series.json"

#: Поле вокруг контура, м. Окно запроса снимков берётся с запасом, а
#: среднее считается только по пикселям внутри контура.
PAD_M = 60


def main() -> int:
    import geopandas as gpd
    import numpy as np
    from rasterio.features import geometry_mask
    from shapely.geometry import box

    from vantage import env
    from vantage.aoi import AOI
    from vantage.catalog import StacCatalog
    from vantage.config import load_settings
    from vantage.raster import build_feature_cube

    env.configure()
    settings = load_settings()
    crs = settings.project.crs_working

    site = gpd.read_file(DATA / "candidates.geojson").to_crs(crs)
    sure = site[site["visual_check"] == "landfill"]
    pool = sure if not sure.empty else site
    # Тот же выбор, что делает лендинг: самый крупный из опознанных.
    hero = pool.sort_values("area_m2", ascending=False).iloc[0]
    shape = hero.geometry
    print(f"── Герой страницы: {hero['candidate_id']}, {hero['area_m2']:.0f} м², "
          f"разрыв {str(hero['break_date'])[:7]}")

    minx, miny, maxx, maxy = shape.bounds
    window = box(minx - PAD_M, miny - PAD_M, maxx + PAD_M, maxy + PAD_M)
    wgs = gpd.GeoSeries([window], crs=crs).to_crs(4326)
    aoi = AOI.from_bbox(tuple(wgs.total_bounds), name="hero", crs_working=crs)

    items = StacCatalog().sentinel2_items(aoi, settings)
    if not items:
        print("   снимков нет — файл не тронут")
        return 1
    cube = build_feature_cube(aoi, settings, items, variables=["ndvi"])
    da = cube["ndvi"].transpose("time", "y", "x")

    # Геопривязка — из координат куба: они в рабочей проекции и указывают
    # на центры пикселей.
    from rasterio.transform import from_origin

    xs, ys = np.asarray(da["x"].values), np.asarray(da["y"].values)
    res_x, res_y = abs(float(xs[1] - xs[0])), abs(float(ys[1] - ys[0]))
    transform = from_origin(float(xs.min()) - res_x / 2, float(ys.max()) + res_y / 2,
                            res_x, res_y)
    if ys[0] < ys[-1]:
        da = da.isel(y=slice(None, None, -1))
    inside = geometry_mask([shape], out_shape=(da.sizes["y"], da.sizes["x"]),
                           transform=transform, invert=True, all_touched=True)
    if not inside.any():
        print("   контур меньше пикселя — файл не тронут")
        return 1
    values = np.asarray(da.values, dtype="float64")[:, inside]
    series = np.nanmean(values, axis=1)
    dates = [str(t)[:10] for t in da["time"].values]

    cut = str(hero["break_date"])[:7]
    index = next((i for i, d in enumerate(dates) if d[:7] >= cut), len(dates) - 1)
    before = series[:index]
    after = series[index:]
    # До разрыва — пик сезона (медиана летних максимумов по годам): именно
    # его теряет свалка. После — медиана всего, что было потом.
    years = {}
    for d, v in zip(dates[:index], before, strict=True):
        if np.isfinite(v):
            years[d[:4]] = max(years.get(d[:4], -1.0), float(v))
    ndvi_before = float(np.median(list(years.values()))) if years else float("nan")
    ndvi_after = float(np.nanmedian(after)) if len(after) else float("nan")

    payload = {
        "candidate_id": str(hero["candidate_id"]),
        "area_m2": round(float(hero["area_m2"])),
        "break_date": str(hero["break_date"])[:10],
        "break_index": int(index),
        "ndvi_before": round(ndvi_before, 3),
        "ndvi_after": round(ndvi_after, 3),
        "dates": dates,
        # Пропуск — это пропуск: график разрывает линию, а не рисует
        # через месяц без наблюдений.
        "ndvi": [None if not np.isfinite(v) else round(float(v), 4) for v in series],
        "source": "Sentinel-2 L2A, месячные медианные композиты, "
                  f"апрель–октябрь {dates[0][:4]}–{dates[-1][:4]}, среднее по контуру объекта",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"   {len(dates)} месяцев, NDVI {payload['ndvi_before']} → {payload['ndvi_after']}")
    print(f"── записано в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
