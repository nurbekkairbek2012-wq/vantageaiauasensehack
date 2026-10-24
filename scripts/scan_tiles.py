"""Сканирование снимка высокого разрешения окнами — второй способ поиска.

── Зачем ───────────────────────────────────────────────────────────────

Основной поиск ищет по Sentinel-2 место, где растительность исчезла
необратимо. Проверка по госмониторингу (AI_RESULTS.md, 1ф) показала, что
так находится около 10% свалок: остальные лежат там, где растительности
не было и раньше, или появились слишком недавно. Похожие проекты
(Politecnico di Milano, 2025) ищут иначе: проходят снимок высокого
разрешения окнами и оценивают каждое окно классификатором.

Здесь это проверяется на одном участке, до того как строить на этом
продукт. Окно — 2×2 тайла Esri Wayback z18 (512 px, ~190 м), шаг — один
тайл (~96 м). Каждое окно оценивают:

  dinov2 — модель снимка из продукта (models/dinov2_chip_open.joblib);
  cwld   — та же, дообученная на CWLD (models/dinov2_chip_open_cwld.joblib);
  skyclip — SkyCLIP без обучения (scripts/rs_clip_exam.py).

Проверка — по свалкам открытого госмониторинга (только проверка, не
обучение): окно «со свалкой», если центр полигона лежит в его центральной
половине. Меряется, сколько свалок попадает в верхние 5% и 10% окон.

    python scripts/scan_tiles.py [--bbox 71.31667,51.12529,71.39038,51.16878]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
TILES = ROOT / "data/scan/tiles"
OUT = ROOT / "data/eval/scan_tiles.json"
Z = 18
DEFAULT_BBOX = "71.31667,51.12529,71.39038,51.16878"


def tile_range(bbox):
    w, s, e, n = bbox
    def xy(lat, lon):
        k = 2 ** Z
        x = (lon + 180) / 360 * k
        y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * k
        return int(x), int(y)
    x0, y0 = xy(n, w)
    x1, y1 = xy(s, e)
    return range(x0, x1 + 1), range(y0, y1 + 1)


def tile_center(x: float, y: float):
    k = 2 ** Z
    lon = x / k * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / k))))
    return lat, lon


def fetch_all(xs, ys, release: str) -> None:
    """Скачать тайлы в кэш; повторный запуск докачивает недостающие."""
    import httpx
    from gemini_screen import WAYBACK

    folder = TILES / release
    folder.mkdir(parents=True, exist_ok=True)
    todo = [(x, y) for y in ys for x in xs if not (folder / f"{x}_{y}.jpg").exists()]
    print(f"── тайлов всего {len(xs) * len(ys)}, скачать {len(todo)}", flush=True)
    # Wayback отвечает на адрес тайла редиректом 301 — без follow_redirects
    # не сохранялось ни одного тайла.
    with httpx.Client(timeout=30, follow_redirects=True,
                      headers={"User-Agent": "VANTAGE/0.1 (research)"}) as client:
        for i, (x, y) in enumerate(todo, 1):
            url = WAYBACK.format(release=release, z=Z, y=y, x=x)
            for attempt in range(3):
                try:
                    r = client.get(url)
                    if r.status_code == 200:
                        (folder / f"{x}_{y}.jpg").write_bytes(r.content)
                    break
                except httpx.HTTPError:
                    time.sleep(2 * (attempt + 1))
            if i % 200 == 0:
                print(f"   {i}/{len(todo)}", flush=True)


def windows(xs, ys, release: str):
    """Окна 2×2 тайла с шагом в тайл: (центр lat, lon, картинка)."""
    import numpy as np
    from PIL import Image

    folder = TILES / release
    for y in list(ys)[:-1]:
        for x in list(xs)[:-1]:
            parts = [folder / f"{x + dx}_{y + dy}.jpg" for dy in (0, 1) for dx in (0, 1)]
            if not all(p.exists() for p in parts):
                continue
            canvas = np.zeros((512, 512, 3), dtype="uint8")
            for p, (dy, dx) in zip(parts, [(0, 0), (0, 1), (1, 0), (1, 1)], strict=True):
                canvas[dy * 256:(dy + 1) * 256, dx * 256:(dx + 1) * 256] = np.asarray(
                    Image.open(p).convert("RGB").resize((256, 256)))
            if canvas.std() < 4:                     # заглушка «снимка нет»
                continue
            lat, lon = tile_center(x + 1, y + 1)
            yield lat, lon, Image.fromarray(canvas)


def main() -> int:
    import warnings

    import geopandas as gpd
    import joblib
    import numpy as np
    from gemini_screen import releases
    from sklearn.metrics import roc_auc_score
    from train_dinov2 import embed, load_model

    from vantage import env

    warnings.filterwarnings("ignore")
    env.configure()
    parser = argparse.ArgumentParser()
    parser.add_argument("--bbox", default=DEFAULT_BBOX)
    parser.add_argument("--name", default="area")
    args = parser.parse_args()
    bbox = tuple(float(v) for v in args.bbox.split(","))
    release = releases()[-1][1]
    xs, ys = tile_range(bbox)
    fetch_all(xs, ys, release)

    cells = list(windows(xs, ys, release))
    print(f"── окон: {len(cells)}", flush=True)
    lats = np.array([c[0] for c in cells])
    lons = np.array([c[1] for c in cells])
    pictures = [c[2] for c in cells]

    scores = {}
    dino = load_model()
    feats = embed(dino, pictures)
    for name, path in (("dinov2", "models/dinov2_chip_open.joblib"),
                       ("cwld", "models/dinov2_chip_open_cwld.joblib")):
        if (ROOT / path).exists():
            scores[name] = joblib.load(ROOT / path).predict_proba(feats)[:, 1]
    try:
        from rs_clip_exam import encode_images, load, zero_shot
        model, preprocess, tokenizer = load("skyclip-l14")
        scores["skyclip"] = zero_shot(model, tokenizer, encode_images(model, preprocess, pictures))
    except Exception as error:
        print(f"   SkyCLIP не посчитан: {type(error).__name__} {error}")
    ranks = [np.argsort(np.argsort(s)) / (len(s) - 1) for s in scores.values()]
    scores["среднее"] = np.mean(ranks, axis=0)

    # Разметка: окно — «со свалкой», если центр полигона в центральной
    # половине окна (±48 м от центра — полтайла z18). Два набора: свалки
    # госмониторинга и опознанные человеком свалки нашего экзамена.
    w, s, e, n = bbox
    half_lat = 48 / 111_320
    half_lon = 48 / (111_320 * math.cos(math.radians((s + n) / 2)))

    def labels_for(points):
        label = np.zeros(len(cells), dtype=int)
        nearest = {}
        for i, p in enumerate(points):
            hit = (np.abs(lats - p.y) <= half_lat) & (np.abs(lons - p.x) <= half_lon)
            label[hit] = 1
            nearest[i] = np.flatnonzero(hit)
        return label, nearest

    def within(points):
        return points[(points.x >= w) & (points.x <= e) & (points.y >= s) & (points.y <= n)]

    gov = gpd.read_file(ROOT / "data/gov_waste/astana.geojson")
    inside = within(gov.geometry.representative_point())
    label, nearest = labels_for(inside)
    print(f"── госсвалок на участке: {len(inside)}; окон со свалкой: {int(label.sum())}")
    ours = gpd.read_file(ROOT / "data/eval/labeled.geojson").to_crs(4326)
    ours_in = within(ours[ours["truth"] == "landfill"].geometry.representative_point())
    _, our_nearest = labels_for(ours_in)
    print(f"── наших опознанных свалок на участке: {len(ours_in)}")

    results = {"bbox": bbox, "windows": len(cells), "gov_dumps": len(inside),
               "positive_windows": int(label.sum()), "our_dumps": len(ours_in),
               "models": {}}
    for name, s in scores.items():
        order = np.argsort(-s)
        rank = np.empty(len(s), dtype=int)
        rank[order] = np.arange(len(s))
        row = {"roc_auc": round(float(roc_auc_score(label, s)), 3) if 0 < label.sum() < len(label)
               else None}
        for share in (0.05, 0.10, 0.20):
            top = rank < int(share * len(s))
            covered = sum(bool(top[idx].any()) for idx in nearest.values() if len(idx))
            row[f"dumps_in_top_{int(share * 100)}pct"] = int(covered)
            row[f"ours_in_top_{int(share * 100)}pct"] = int(
                sum(bool(top[idx].any()) for idx in our_nearest.values() if len(idx)))
        results["models"][name] = row
        print(f"   {name:8} ROC-AUC {row['roc_auc']}; госсвалок в верхних 5% окон: "
              f"{row['dumps_in_top_5pct']}, 10%: {row['dumps_in_top_10pct']}, "
              f"20%: {row['dumps_in_top_20pct']} из {len(inside)}; наших: "
              f"{row['ours_in_top_5pct']}/{row['ours_in_top_10pct']}/{row['ours_in_top_20pct']}"
              f" из {len(ours_in)}")
    print(f"   случайный порядок: в 10% окон ≈ {0.10 * len(inside):.0f} свалок")
    out = OUT if args.bbox == DEFAULT_BBOX else OUT.with_name(f"scan_tiles_{args.name}.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    np.savez_compressed(ROOT / f"data/scan/scores_{args.name}.npz", lat=lats, lon=lons, label=label,
                        **{k: v for k, v in scores.items() if k.isascii()})
    print(f"── записано в {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
