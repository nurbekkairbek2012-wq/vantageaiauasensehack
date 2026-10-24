"""Пестрота снимка только внутри контура объекта.

── Зачем ───────────────────────────────────────────────────────────────

Текстурная оценка доверификации (verify_texture) считалась по всему кадру
в 290 м и на экзамене оказалась признаком МЕСТА, а не класса: она
отличала северное кольцо от других поясов (0,76) и не отличала свалку от
склада внутри одной области (0,43) — AI_RESULTS.md, 1и. Кадр почти
целиком занят окрестностями, и мерила она окрестности.

Здесь те же идеи — свалка пёстрая, крыша и площадка ровные — считаются
только по пикселям внутри контура, на свежем снимке Esri Wayback ~0,4 м:

  color_std     — разброс цвета (среднее СКО по каналам RGB);
  edge_density  — доля пикселей с резким перепадом яркости;
  entropy       — энтропия гистограммы яркости;
  bright_share  — доля очень светлых пикселей (белые вкрапления мусора).

    python scripts/contour_texture.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
OUT = ROOT / "data/eval/contour_texture.csv"


def contour_mask(geometry, lat, lon, zoom, shape):
    import numpy as np
    from gemini_screen import GRID, _tile_xy
    from PIL import Image, ImageDraw

    cx, cy = _tile_xy(lat, lon, zoom)
    left, top = (int(cx) - GRID // 2) * 256, (int(cy) - GRID // 2) * 256
    mask = Image.new("L", (shape[1], shape[0]), 0)
    draw = ImageDraw.Draw(mask)
    for poly in getattr(geometry, "geoms", [geometry]):
        pts = [(_tile_xy(y, x, zoom)[0] * 256 - left, _tile_xy(y, x, zoom)[1] * 256 - top)
               for x, y in poly.exterior.coords]
        draw.polygon(pts, fill=1)
    return np.asarray(mask, dtype=bool)


def texture(image, mask) -> dict:
    import numpy as np

    pix = image[mask].astype("float32")
    if len(pix) < 30:
        return {}
    gray = image.astype("float32").mean(axis=2)
    gy, gx = np.gradient(gray)
    grad = np.hypot(gx, gy)[mask]
    hist, _ = np.histogram(gray[mask], bins=32, range=(0, 255))
    p = hist / hist.sum()
    p = p[p > 0]
    return {
        "color_std": float(pix.std(axis=0).mean()),
        "edge_density": float(np.mean(grad > 20)),
        "entropy": float(-(p * np.log2(p)).sum()),
        "bright_share": float(np.mean(gray[mask] > 200)),
    }


def main() -> int:
    import geopandas as gpd
    import pandas as pd
    from gemini_screen import WAYBACK, fetch, releases

    frame = gpd.read_file(ROOT / "data/eval/labeled.geojson")
    release = releases()[-1][1]
    rows = []
    for row in frame.itertuples():
        p = row.geometry.representative_point()
        image, zoom = fetch(p.y, p.x, WAYBACK.format(release=release, x="{x}", y="{y}", z="{z}"),
                            f"wb{release}")
        if image is None:
            continue
        mask = contour_mask(row.geometry, p.y, p.x, zoom, image.shape)
        feats = texture(image, mask)
        if feats:
            rows.append({"candidate_id": row.candidate_id, "truth": row.truth, **feats})
    table = pd.DataFrame(rows)
    table["area"] = frame.set_index("candidate_id").loc[table["candidate_id"], "area"].values
    table.to_csv(OUT, index=False)
    print(f"── пестрота внутри контура: {len(table)} объектов → {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
