"""Модель окна для сканирования снимка: учим её фону Астаны.

── Зачем ───────────────────────────────────────────────────────────────

Сканирование окнами (scripts/scan_tiles.py, AI_RESULTS.md 1х) находит в
верхних 10% окон 10–14 из 31 свалки госмониторинга. Модель окна при этом
ни разу не видела обычного пейзажа Астаны: её отрицательные примеры —
дроновые кадры, Пекин и полигоны OSM. Отсюда большинство ложных тревог:
стройки, склады, пустыри, которых в обучении не было.

Здесь модели дают фон: случайные окна ОДНОГО участка идут в обучение как
«не свалка» (свалок среди окон около 1%, шум мал), а проверка — на
ДРУГОМ участке, чтобы модель не запоминала сами окна. Свалки
госмониторинга и наши опознанные — только для проверки, в обучение не
идут.

Сравнение, на одних и тех же окнах участка проверки:

  base         — дрон + Казахстан OSM + CWLD + отказы человека (как в 1у);
  base + фон   — то же + окна другого участка как отрицательные;
  + SkyCLIP    — среднее рангов с SkyCLIP без обучения.

    python scripts/scan_train.py
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
OUT = ROOT / "data/eval/scan_train.json"
AREAS = {
    "west": "71.31667,51.12529,71.39038,51.16878",
    "north": "71.5,51.13,71.575,51.175",
    # Второй участок проверки: 24 свалки госмониторинга, с западным не пересекается.
    "east": "71.53672,51.09824,71.61059,51.14158",
    # Участок второго круга просмотра (AI_RESULTS.md, 1ц): 22 свалки
    # госмониторинга, до просмотра — ещё одна проверка модели первого круга.
    "ne": "71.49283,51.21161,71.56684,51.25498",
}
#: Вес окна фона: их тысячи против сотен положительных примеров.
BACKGROUND_WEIGHT = 1.0


def area_windows(name: str, model):
    """Признаки DINOv2 окон участка (кэш) и их центры."""
    import numpy as np
    from gemini_screen import releases
    from scan_tiles import tile_range, windows
    from train_dinov2 import embed

    cache = ROOT / f"data/scan/embed_{name}.npz"
    if cache.exists():
        z = np.load(cache)
        return z["x"], z["lat"], z["lon"]
    bbox = tuple(float(v) for v in AREAS[name].split(","))
    xs, ys = tile_range(bbox)
    cells = list(windows(xs, ys, releases()[-1][1]))
    x = embed(model, [c[2] for c in cells])
    lat = np.array([c[0] for c in cells])
    lon = np.array([c[1] for c in cells])
    np.savez_compressed(cache, x=x, lat=lat, lon=lon)
    return x, lat, lon


def labels(lat, lon, points, bbox):
    """Окно — «со свалкой», если центр свалки в его центральной половине."""
    import numpy as np

    w, s, e, n = bbox
    half_lat = 48 / 111_320
    half_lon = 48 / (111_320 * math.cos(math.radians((s + n) / 2)))
    label = np.zeros(len(lat), dtype=int)
    groups = []
    for p in points:
        if not (w <= p.x <= e and s <= p.y <= n):
            continue
        hit = (np.abs(lat - p.y) <= half_lat) & (np.abs(lon - p.x) <= half_lon)
        label[hit] = 1
        if hit.any():
            groups.append(np.flatnonzero(hit))
    return label, groups


def main() -> int:
    import warnings

    import geopandas as gpd
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from train_cwld import CACHE as CWLD_CACHE
    from train_dinov2 import CACHE as BASE_CACHE
    from train_dinov2 import OWN_WEIGHT, embed, exam_pictures, load_model

    from vantage import env

    warnings.filterwarnings("ignore")
    env.configure()
    model = load_model()

    base = np.load(BASE_CACHE, allow_pickle=True)
    drone = np.load(BASE_CACHE.with_name(BASE_CACHE.stem + "_drone.npz"))
    cw = np.load(CWLD_CACHE)
    frame, pictures = exam_pictures()
    own = ((frame["truth"] == "not_landfill") & (frame["area"] != "outputs_real")).to_numpy()
    ex_x = embed(model, [p for p, o in zip(pictures, own, strict=True) if o])
    parts_x = [drone["x"], base["kz_x"], cw["x"], ex_x]
    parts_y = [drone["y"], base["kz_y"], cw["y"], np.zeros(len(ex_x))]
    parts_w = [np.ones(len(drone["y"])), np.ones(len(base["kz_y"])), np.ones(len(cw["y"])),
               np.full(len(ex_x), OWN_WEIGHT)]

    gov = gpd.read_file(ROOT / "data/gov_waste/astana.geojson").geometry.representative_point()
    ours_frame = gpd.read_file(ROOT / "data/eval/labeled.geojson").to_crs(4326)
    ours = ours_frame[ours_frame["truth"] == "landfill"].geometry.representative_point()

    windows_ = {name: area_windows(name, model) for name in AREAS}
    skyclip = {}
    for name in AREAS:
        path = ROOT / f"data/scan/scores_{'area' if name == 'west' else name}.npz"
        if path.exists():
            z = np.load(path)
            if "skyclip" in z and len(z["skyclip"]) == len(windows_[name][0]):
                skyclip[name] = z["skyclip"]

    def fit(extra_x=None):
        X = np.vstack(parts_x + ([extra_x] if extra_x is not None else []))
        Y = np.concatenate(parts_y + ([np.zeros(len(extra_x))] if extra_x is not None else []))
        W = np.concatenate(parts_w + ([np.full(len(extra_x), BACKGROUND_WEIGHT)]
                                      if extra_x is not None else []))
        clf = make_pipeline(StandardScaler(), LogisticRegression(
            C=0.1, class_weight="balanced", max_iter=5000))
        return clf.fit(X, Y, logisticregression__sample_weight=W)

    def rank(v):
        return np.argsort(np.argsort(v)) / (len(v) - 1)

    def evaluate(scores, label, groups):
        order = np.empty(len(scores), dtype=int)
        order[np.argsort(-scores)] = np.arange(len(scores))
        row = {"roc_auc": round(float(roc_auc_score(label, scores)), 3)
               if 0 < label.sum() < len(label) else None}
        for share in (0.05, 0.10, 0.20):
            top = order < int(share * len(scores))
            row[f"top{int(share * 100)}"] = int(sum(bool(top[g].any()) for g in groups))
        return row

    base_clf = fit()
    results = {}
    for target, source in (("north", "west"), ("west", "north")):
        x, lat, lon = windows_[target]
        bbox = tuple(float(v) for v in AREAS[target].split(","))
        gov_label, gov_groups = labels(lat, lon, gov, bbox)
        our_label, our_groups = labels(lat, lon, ours, bbox)
        local = fit(windows_[source][0])
        variants = {
            "base": base_clf.predict_proba(x)[:, 1],
            "base + фон": local.predict_proba(x)[:, 1],
        }
        if target in skyclip:
            variants["base + фон + SkyCLIP"] = (rank(variants["base + фон"])
                                               + rank(skyclip[target])) / 2
        results[target] = {"windows": len(x), "gov_dumps": len(gov_groups),
                           "our_dumps": len(our_groups), "models": {}}
        print(f"\n── участок {target}: окон {len(x)}, госсвалок {len(gov_groups)}, "
              f"наших свалок {len(our_groups)}; фон взят с участка {source}")
        for name, s in variants.items():
            g = evaluate(s, gov_label, gov_groups)
            o = evaluate(s, our_label, our_groups) if our_groups else {}
            results[target]["models"][name] = {"gov": g, "ours": o}
            print(f"   {name:24} госсвалки: AUC {g['roc_auc']}, в верхних 5/10/20% — "
                  f"{g['top5']}/{g['top10']}/{g['top20']} из {len(gov_groups)}"
                  + (f"; наши: {o['top5']}/{o['top10']}/{o['top20']} из {len(our_groups)}"
                     if o else ""), flush=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n── записано в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
