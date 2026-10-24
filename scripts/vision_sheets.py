"""Листы для машинного просмотра — по одному на объект, без подсказок.

── Зачем ───────────────────────────────────────────────────────────────

Проверяющий по снимку — не обязательно человек. Здесь готовится материал
для ИИ-проверяющего (мультимодальной модели), и готовится так, чтобы его
можно было честно проверить экзаменом:

  * у листа нет номера объекта, района и вердикта — только случайный код.
    Номера опознанных свалок встречаются в документах проекта, и по ним
    ответ можно было бы узнать, не глядя на снимок;
  * порядок листов перемешан, чтобы соседние по номеру объекты одного
    района не шли подряд;
  * ключ «код → объект» пишется в отдельный файл, который проверяющий не
    открывает до конца экзамена (scripts/vision_exam.py сверяет сам).

── Что на листе ────────────────────────────────────────────────────────

Три панели, контур объекта красным:

  1. СЕЙЧАС — свежий выпуск Esri Wayback, около 290 м в поперечнике;
  2. ДО — последний выпуск раньше даты разрыва (если такой есть);
  3. ОКРЕСТНОСТИ — тот же свежий выпуск мельче, около 1,2 км: видно,
     промзона вокруг, стройка, дачи или пустырь.

    python scripts/vision_sheets.py [--target eval|site]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

OUT = ROOT / "data/vision"
PANEL = 512
CONTEXT_ZOOM = 16


def context_grid(lat: float, lon: float, release: str):
    """Сетка 3×3 тайлов мелкого масштаба вокруг точки (кэшируется)."""
    import numpy as np
    from gemini_screen import IMAGES, WAYBACK
    from PIL import Image

    from vantage.verify import TileProvider, fetch_tile_grid

    path = IMAGES / f"{lat:.5f}_{lon:.5f}_wb{release}_ctx{CONTEXT_ZOOM}.png"
    if path.exists():
        return np.asarray(Image.open(path).convert("RGB"))
    provider = TileProvider(name=f"wb{release}", url_template=WAYBACK.format(
        release=release, x="{x}", y="{y}", z="{z}"), attribution="", source="esri",
        max_zoom=19)
    try:
        grid = fetch_tile_grid(provider, lat, lon, CONTEXT_ZOOM, 3, timeout=30)
    except Exception:
        return None
    IMAGES.mkdir(parents=True, exist_ok=True)
    Image.fromarray(grid.astype("uint8")).save(path)
    return grid


def sheet(row, history):
    """Собрать лист объекта: три панели в ряд с подписями."""
    import numpy as np
    from gemini_screen import WAYBACK, fetch, object_crop, outline
    from PIL import Image, ImageDraw

    point = row.geometry.representative_point()
    lat, lon = point.y, point.x
    latest_date, latest = history[-1]
    now, zoom = fetch(lat, lon, WAYBACK.format(release=latest, x="{x}", y="{y}", z="{z}"),
                      f"wb{latest}")
    if now is None:
        return None
    panels = [(object_crop(outline(now, row.geometry, lat, lon, zoom), lat, lon, zoom, 704),
               f"СЕЙЧАС · {latest_date[:7]}")]

    broke = str(getattr(row, "break_date", "") or "")[:10]
    earlier = [(d, r) for d, r in history if broke and d < broke]
    if earlier:
        before_date, before = earlier[-1]
        old, bzoom = fetch(lat, lon, WAYBACK.format(release=before, x="{x}", y="{y}", z="{z}"),
                           f"wb{before}")
        if old is not None:
            panels.append((object_crop(outline(old, row.geometry, lat, lon, bzoom),
                                       lat, lon, bzoom, 704), f"ДО · {before_date[:7]}"))
    if len(panels) == 1:
        panels.append((np.zeros((704, 704, 3), dtype="uint8"), "ДО · снимка нет"))

    ctx = context_grid(lat, lon, latest)
    if ctx is not None:
        panels.append((outline(ctx, row.geometry, lat, lon, CONTEXT_ZOOM),
                       "ОКРЕСТНОСТИ · ~1,2 км"))

    canvas = Image.new("RGB", (PANEL * len(panels), PANEL + 28), (12, 12, 16))
    draw = ImageDraw.Draw(canvas)
    for i, (picture, label) in enumerate(panels):
        tile = Image.fromarray(np.asarray(picture, dtype="uint8")).resize((PANEL, PANEL))
        canvas.paste(tile, (i * PANEL, 28))
        draw.text((i * PANEL + 8, 7), label, fill=(235, 235, 235))
    return canvas


def main() -> int:
    import geopandas as gpd
    from build_eval_set import build
    from gemini_screen import releases

    from vantage import env

    env.configure()
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=("eval", "site"), default="eval")
    parser.add_argument("--seed", type=int, default=20260927)
    args = parser.parse_args()

    if args.target == "eval":
        frame = build().to_crs(4326)
    else:
        frame = gpd.read_file(ROOT / "web-next/public/data/candidates.geojson").to_crs(4326)
        frame["candidate_id"] = "site:" + frame["candidate_id"].astype(str)
    folder = OUT / args.target
    folder.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)
    order = list(range(len(frame)))
    rng.shuffle(order)
    codes = rng.sample(range(1000, 10000), len(frame))
    history = releases()

    key = {}
    for n, (i, code) in enumerate(zip(order, codes, strict=True), 1):
        row = next(frame.iloc[[i]].itertuples())
        picture = sheet(row, history)
        name = f"V{code}"
        if picture is None:
            print(f"   {n:3}/{len(frame)} {name}: снимка нет")
            continue
        picture.save(folder / f"{n:03d}_{name}.png")
        key[name] = str(row.candidate_id)
        print(f"   {n:3}/{len(frame)} {name}", flush=True)

    # Ключ — отдельно от листов, и проверяющий его не открывает.
    (OUT / f"key_{args.target}.json").write_text(
        json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"── листов: {len(key)} → {folder.relative_to(ROOT)}; ключ — data/vision/key_{args.target}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
