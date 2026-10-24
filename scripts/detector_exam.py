"""Экзамен для самого поиска: находит ли он то, что видит независимая карта.

── Зачем ───────────────────────────────────────────────────────────────

Все прежние проверки мерили то, что стоит ПОСЛЕ поиска: отсев, просмотр,
модели по снимкам. Сам поиск — детектор необратимого исчезновения
растительности — не проверял никто: неизвестно, сколько таких мест он
пропускает. Экзамен на легальных полигонах ТБО не сложился: ни один из
четырёх полигонов в посчитанных областях за 2018–2026 годы не рос — два
старых зарастают, действующая карта была голой всё время.

Здесь эталон — независимая карта земного покрова Impact Observatory
(io-lulc-annual-v02, 10 м, по годам, своя модель и своя разметка). Место
считается «необратимо потерявшим растительность», если:

  * в 2017 и 2018 годах там была растительность (деревья, кустарник и
    степь, пашня, заболоченная растительность);
  * в год t из 2019–2022 оно стало голым грунтом или застройкой;
  * и оставалось таким во все следующие годы карты (до 2023-го).

2023 год карты — последний; разрывы позже 2022 года детектор и не обязан
видеть: он подтверждает необратимость только через 18 месяцев.

Меряется:

  * полнота поиска — доля таких мест от 500 м², которых касается хотя бы
    одна сырая находка детектора (до контекстного отсева);
  * отдельно без мест, бывших пашней: переход «пашня → голый грунт» часто
    залежь, и детектор намеренно её не ловит;
  * совпадение года: год разрыва у находки против года смены по карте.

Карта — не истина: у неё свои ошибки, и «застройкой» она называет в том
числе свалки (AI_RESULTS.md, 1к). Поэтому это оценка порядка величины,
а не точная полнота.

    python scripts/detector_exam.py [--area outputs_real]
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
OUT = ROOT / "data/eval"

CRS = "EPSG:32642"
RES = 10.0
VEGETATION = {2, 4, 5, 11}
CROPS = 5
LOST = {7, 8}
YEARS = range(2017, 2024)
MIN_PIXELS = 5  # 500 м² — порог площади самого детектора


def lulc_stack(bounds):
    """Карта по годам на общей сетке 10 м в UTM 42N: {год: массив}."""
    import numpy as np
    import planetary_computer
    import pystac_client
    import rasterio
    from pyproj import Transformer
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.vrt import WarpedVRT

    minx, miny, maxx, maxy = bounds
    width, height = int((maxx - minx) / RES), int((maxy - miny) / RES)
    transform = from_origin(minx, maxy, RES, RES)
    to_wgs = Transformer.from_crs(CRS, 4326, always_xy=True)
    lon0, lat0 = to_wgs.transform(minx, miny)
    lon1, lat1 = to_wgs.transform(maxx, maxy)

    catalog = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1",
                                        modifier=planetary_computer.sign_inplace)
    items = list(catalog.search(collections=["io-lulc-annual-v02"],
                                bbox=[lon0, lat0, lon1, lat1]).items())
    stack = {}
    for year in YEARS:
        chosen = [i for i in items if str(i.properties.get("start_datetime", ""))[:4] == str(year)]
        canvas = np.zeros((height, width), dtype="uint8")
        for item in chosen:
            with rasterio.open(item.assets["data"].href) as src, WarpedVRT(
                    src, crs=CRS, transform=transform, width=width, height=height,
                    resampling=Resampling.nearest) as vrt:
                data = vrt.read(1)
                canvas = np.where(canvas == 0, data, canvas)
        if chosen:
            stack[year] = canvas
    return stack, transform, (height, width)


def changed_mask(stack):
    """Год необратимой смены растительности на грунт/застройку, иначе 0."""
    import numpy as np

    first = min(stack)
    base = np.isin(stack[first], list(VEGETATION)) & np.isin(stack[first + 1], list(VEGETATION))
    was_crop = (stack[first] == CROPS) | (stack[first + 1] == CROPS)
    year_of = np.zeros(base.shape, dtype="int16")
    last = max(stack)
    for t in range(first + 2, last):
        stays = np.ones(base.shape, dtype=bool)
        for later in range(t, last + 1):
            stays &= np.isin(stack[later], list(LOST))
        before_ok = np.isin(stack[t - 1], list(VEGETATION))
        hit = base & before_ok & stays & (year_of == 0)
        year_of[hit] = t
    return year_of, was_crop


def main() -> int:
    import geopandas as gpd
    import numpy as np
    import pandas as pd
    from rasterio.features import rasterize
    from scipy import ndimage

    from vantage import env

    env.configure()
    parser = argparse.ArgumentParser()
    parser.add_argument("--area", default="outputs_real")
    args = parser.parse_args()

    raw = gpd.read_file(ROOT / args.area / "candidates_raw.geojson").to_crs(CRS)
    minx, miny, maxx, maxy = raw.total_bounds
    bounds = (minx - 200, miny - 200, maxx + 200, maxy + 200)
    print(f"── Экзамен поиска: {args.area}, {len(raw)} сырых находок, "
          f"{(bounds[2] - bounds[0]) / 1e3:.1f} × {(bounds[3] - bounds[1]) / 1e3:.1f} км")

    stack, transform, shape = lulc_stack(bounds)
    print(f"   карта земного покрова: годы {min(stack)}–{max(stack)}")
    year_of, was_crop = changed_mask(stack)

    found = rasterize(((g, 1) for g in raw.geometry), out_shape=shape, transform=transform,
                      all_touched=True, fill=0, dtype="uint8").astype(bool)
    found = ndimage.binary_dilation(found, iterations=1)
    # У части сырых находок даты нет (NaT) — они участвуют в полноте, но
    # не в сверке года.
    dated = [(g, int(str(d)[:4])) for g, d in zip(raw.geometry, raw["break_date"], strict=True)
             if str(d)[:4].isdigit()]
    break_year = rasterize(dated, out_shape=shape, transform=transform, all_touched=True,
                           fill=0, dtype="int16")

    labels, _ = ndimage.label(year_of > 0)
    rows = []
    for idx, obj in enumerate(ndimage.find_objects(labels), start=1):
        region = labels[obj] == idx
        size = int(region.sum())
        if size < MIN_PIXELS:
            continue
        years = year_of[obj][region]
        hit = bool(found[obj][region].any())
        by = break_year[obj][region]
        by = by[by > 0]
        cy, cx = [float(np.mean(a)) for a in np.nonzero(region)]
        x_utm, y_utm = transform * (obj[1].start + cx + 0.5, obj[0].start + cy + 0.5)
        rows.append({
            "x": x_utm, "y": y_utm,
            "pixels": size, "year": int(np.bincount(years).argmax()),
            "crop_origin": bool(was_crop[obj][region].mean() > 0.5),
            "found": hit,
            "detector_year": int(np.bincount(by).argmax()) if by.size else None,
        })
    table = pd.DataFrame(rows)
    if table.empty:
        print("   карта не показывает необратимых смен — экзаменовать нечем")
        return 1

    def summary(part, name):
        recall = part["found"].mean() if len(part) else float("nan")
        big = part[part["pixels"] >= 20]
        big_recall = big["found"].mean() if len(big) else float("nan")
        same = part.dropna(subset=["detector_year"])
        agree = (abs(same["detector_year"] - same["year"]) <= 1).mean() if len(same) else float("nan")
        print(f"   {name:34} мест {len(part):4}; найдено {recall:.0%}; "
              f"из них от 2 000 м² — {big_recall:.0%}; год ±1 совпал у {agree:.0%}")
        return {"places": len(part), "recall": round(float(recall), 3),
                "recall_2000m2": round(float(big_recall), 3),
                "year_agreement": round(float(agree), 3) if agree == agree else None}

    result = {
        "area": args.area,
        "all": summary(table, "все места необратимой смены"),
        "not_crop": summary(table[~table["crop_origin"]], "без бывшей пашни"),
        "by_year": {int(y): summary(table[table["year"] == y], f"  сменились в {y}")
                    for y in sorted(table["year"].unique())},
    }
    # Обратная сторона: сколько сырых находок карта считает местом смены.
    hits = rasterize(((g, i + 1) for i, g in enumerate(raw.geometry)), out_shape=shape,
                     transform=transform, all_touched=True, fill=0, dtype="int32")
    confirmed = np.unique(hits[(year_of > 0) & (hits > 0)])
    result["raw_confirmed_by_map"] = round(len(confirmed) / len(raw), 3)
    print(f"   сырых находок, где карта тоже видит смену: {result['raw_confirmed_by_map']:.0%}")

    OUT.mkdir(parents=True, exist_ok=True)
    # Места смены — отдельным файлом: по ним смотрят, что именно пропущено.
    from pyproj import Transformer

    to_wgs = Transformer.from_crs(CRS, 4326, always_xy=True)
    table["lon"], table["lat"] = to_wgs.transform(table["x"].to_numpy(), table["y"].to_numpy())
    table.drop(columns=["x", "y"]).to_csv(OUT / f"detector_exam_{args.area}.csv", index=False)
    out = OUT / f"detector_exam_{args.area}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"── записано в {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
