"""Модель окна, которая смотрит на кусочки, а не на окно целиком.

── Зачем ───────────────────────────────────────────────────────────────

Свалка госмониторинга — в среднем 1 500 м², то есть около 4% окна
190 × 190 м. Признак всего окна (CLS DINOv2) такую свалку размывает:
модель видит «пустырь со складом», а не кучу в углу. DINOv2 даёт признак
и для каждого кусочка 14 × 14 пикселей входа — около 12 м на местности,
16 × 16 кусочков на окно. Здесь:

  cls        — как было: линейная модель на признаке всего окна;
  mean       — линейная модель на среднем признаке кусочков;
  top4       — та же модель применяется к КАЖДОМУ кусочку, окно получает
               среднее четырёх самых подозрительных — мелкая свалка не
               тонет в фоне;

и отдельно — добавить в обучение наши опознанные свалки (7, северное
кольцо): каждая вырезается со сдвигами окна и по нескольким выпускам
снимков после появления, так из семи свалок выходит сотня примеров.

Фон — окна северного участка (без окон у наших свалок). Проверка — на
западном участке по 30 свалкам госмониторинга, которых модель не видела;
данные госмониторинга в обучение не идут.

    python scripts/scan_patch.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
OUT = ROOT / "data/eval/scan_patch.json"
CACHE = ROOT / "data/scan/patch_features.npz"
SHIFT_M = 48
OUR_WEIGHT = 5.0


def tokens(model, pictures, batch: int = 32):
    """(CLS, среднее кусочков, все кусочки float16) для списка картинок."""
    import numpy as np
    import torch
    from train_dinov2 import to_tensor

    cls, mean, patches = [], [], []
    with torch.no_grad():
        for i in range(0, len(pictures), batch):
            x = torch.stack([to_tensor(p) for p in pictures[i:i + batch]])
            out = model.forward_features(x)
            c = out["x_norm_clstoken"].numpy()
            p = out["x_norm_patchtokens"].numpy()
            cls.append(c)
            mean.append(p.mean(axis=1))
            patches.append(p.astype("float16"))
    return np.concatenate(cls), np.concatenate(mean), np.concatenate(patches)


def our_dump_pictures():
    """Наши опознанные свалки: сдвиги окна × выпуски снимков после появления."""
    import geopandas as gpd
    import pandas as pd
    from gemini_screen import WAYBACK, fetch, object_crop, releases
    from PIL import Image

    frame = gpd.read_file(ROOT / "data/eval/labeled.geojson").to_crs(4326)
    dumps = frame[frame["truth"] == "landfill"]
    history = releases()
    pictures = []
    for row in dumps.itertuples():
        p = row.geometry.representative_point()
        broke = pd.to_datetime(str(getattr(row, "break_date", "") or "")[:10], errors="coerce")
        after = [r for d, r in history if pd.isna(broke) or pd.Timestamp(d) > broke + pd.DateOffset(months=3)]
        for release in after[-3:]:
            grid, zoom = fetch(p.y, p.x, WAYBACK.format(release=release, x="{x}", y="{y}", z="{z}"),
                               f"wb{release}")
            if grid is None:
                continue
            dlat = SHIFT_M / 111_320
            dlon = SHIFT_M / (111_320 * math.cos(math.radians(p.y)))
            for sy in (-1, 0, 1):
                for sx in (-1, 0, 1):
                    crop = object_crop(grid, p.y + sy * dlat, p.x + sx * dlon, zoom, 512)
                    pictures.append(Image.fromarray(crop))
    return pictures, dumps


def main() -> int:
    import warnings

    import geopandas as gpd
    import numpy as np
    from scan_train import AREAS, labels
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from train_cwld import chips
    from train_dinov2 import OWN_WEIGHT, drone, exam_pictures, kz, load_model

    from vantage import env

    warnings.filterwarnings("ignore")
    env.configure()
    model = load_model()

    if CACHE.exists():
        z = dict(np.load(CACHE))
    else:
        from gemini_screen import releases
        from scan_tiles import tile_range, windows

        z = {}
        sets = {}
        pics, y = drone()
        sets["drone"] = (list(pics), np.asarray(y))
        pics, y = kz()
        sets["kz"] = (list(pics), np.asarray(y))
        pics, y = chips()
        sets["cwld"] = (pics, np.asarray(y))
        frame, pictures = exam_pictures()
        own = ((frame["truth"] == "not_landfill") & (frame["area"] != "outputs_real")).to_numpy()
        sets["own"] = ([p for p, o in zip(pictures, own, strict=True) if o], np.zeros(int(own.sum())))
        ours, _ = our_dump_pictures()
        sets["ours"] = (ours, np.ones(len(ours)))
        for name, (pics, y) in sets.items():
            c, m, _ = tokens(model, pics)
            z[f"{name}_cls"], z[f"{name}_mean"], z[f"{name}_y"] = c, m, y
            print(f"── {name}: {len(y)} картинок", flush=True)
        release = releases()[-1][1]
        for area, box in AREAS.items():
            bbox = tuple(float(v) for v in box.split(","))
            xs, ys = tile_range(bbox)
            cells = list(windows(xs, ys, release))
            c, m, p = tokens(model, [cell[2] for cell in cells])
            z[f"{area}_cls"], z[f"{area}_mean"], z[f"{area}_patch"] = c, m, p
            z[f"{area}_lat"] = np.array([cell[0] for cell in cells])
            z[f"{area}_lon"] = np.array([cell[1] for cell in cells])
            print(f"── окна {area}: {len(cells)}", flush=True)
        np.savez_compressed(CACHE, **z)

    gov = gpd.read_file(ROOT / "data/gov_waste/astana.geojson").geometry.representative_point()
    ours_frame = gpd.read_file(ROOT / "data/eval/labeled.geojson").to_crs(4326)
    ours = ours_frame[ours_frame["truth"] == "landfill"].geometry.representative_point()

    # Фон — окна севера, кроме окон у наших свалок (они там положительные).
    n_lat, n_lon = z["north_lat"], z["north_lon"]
    near = np.zeros(len(n_lat), dtype=bool)
    for p in ours:
        near |= (np.abs(n_lat - p.y) < 150 / 111_320) & (np.abs(n_lon - p.x) < 250 / 111_320)
    background = ~near

    def fit(kind: str, with_ours: bool):
        names = ["drone", "kz", "cwld", "own"] + (["ours"] if with_ours else [])
        X = [z[f"{n}_{kind}"] for n in names] + [z[f"north_{kind}"][background]]
        Y = [z[f"{n}_y"] for n in names] + [np.zeros(int(background.sum()))]
        W = [np.full(len(z[f"{n}_y"]), OWN_WEIGHT if n == "own" else OUR_WEIGHT if n == "ours"
                     else 1.0) for n in names] + [np.ones(int(background.sum()))]
        clf = make_pipeline(StandardScaler(), LogisticRegression(
            C=0.1, class_weight="balanced", max_iter=5000))
        return clf.fit(np.vstack(X), np.concatenate(Y), logisticregression__sample_weight=np.concatenate(W))

    def top_k(clf, patches, k=4):
        n, p, d = patches.shape
        s = clf.decision_function(patches.reshape(-1, d).astype("float32")).reshape(n, p)
        return np.sort(s, axis=1)[:, -k:].mean(axis=1)

    w_lat, w_lon = z["west_lat"], z["west_lon"]
    bbox = tuple(float(v) for v in AREAS["west"].split(","))
    label, groups = labels(w_lat, w_lon, gov, bbox)

    def evaluate(scores):
        order = np.empty(len(scores), dtype=int)
        order[np.argsort(-scores)] = np.arange(len(scores))
        row = {"roc_auc": round(float(roc_auc_score(label, scores)), 3)}
        for share in (0.05, 0.10, 0.20):
            top = order < int(share * len(scores))
            row[f"top{int(share * 100)}"] = int(sum(bool(top[g].any()) for g in groups))
        return row

    sky = None
    sky_path = ROOT / "data/scan/scores_area.npz"
    if sky_path.exists():
        s = np.load(sky_path)
        if "skyclip" in s and len(s["skyclip"]) == len(w_lat):
            sky = s["skyclip"]

    def rank(v):
        return np.argsort(np.argsort(v)) / (len(v) - 1)

    results = {}
    print(f"\n── проверка: запад, окон {len(w_lat)}, свалок госмониторинга {len(groups)}")
    for with_ours in (False, True):
        tag = " + наши свалки" if with_ours else ""
        clf_cls = fit("cls", with_ours)
        clf_mean = fit("mean", with_ours)
        variants = {
            f"cls{tag}": clf_cls.decision_function(z["west_cls"]),
            f"mean{tag}": clf_mean.decision_function(z["west_mean"]),
            f"top4{tag}": top_k(clf_mean, z["west_patch"]),
        }
        if sky is not None:
            variants[f"top4{tag} + SkyCLIP"] = (rank(variants[f"top4{tag}"]) + rank(sky)) / 2
        for name, s in variants.items():
            results[name] = evaluate(s)
            r = results[name]
            print(f"   {name:34} AUC {r['roc_auc']:.3f}; в верхних 5/10/20% окон — "
                  f"{r['top5']}/{r['top10']}/{r['top20']} из {len(groups)}", flush=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"── записано в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
