"""Добавить в модель снимка открытый набор CWLD и проверить экзаменом.

── Что за набор ────────────────────────────────────────────────────────

CWLD — «Construction Waste Landfill Dataset», два района Пекина, снимки
Google Earth и GF-2, 685 сцен 500×500 с попиксельной разметкой: отходы
(красный), сооружения (синий), площадка под ссыпку (белый), фон (чёрный).
Zenodo 8333888, лицензия CC BY 4.0 — коммерческое использование можно, с
указанием авторства. Наши свалки северного кольца — в основном строительный
мусор и грунт, то есть ровно этот тип.

── Как нарезается ──────────────────────────────────────────────────────

Из каждой сцены — не больше одной вырезки каждого класса, CROP_PX пикселей:

  * свалка — квадрат с центром в центре масс красной области, если отходы
    занимают в нём не меньше MIN_WASTE;
  * не свалка — квадрат из фона без отходов, сооружений и площадки: это
    окрестности полигонов — пустыри, поля, дороги, стройки. Трудные
    отрицательные, как у нас.

Проверка — та же, что у модели в продукте (scripts/train_dinov2.py):
51 объект северного кольца, 7 свалок, ROC-AUC с 90% интервалом.

    python scripts/train_cwld.py
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
ARCHIVE = ROOT / "data/cwld/CWLD.zip"
CACHE = ROOT / "data/dinov2_embeddings_cwld.npz"
OUT = ROOT / "data/eval/cwld.json"
MODEL = ROOT / "models/dinov2_chip_open_cwld.joblib"
CROP_PX = 240
MIN_WASTE = 0.15


def masks(label):
    """Маски классов по цвету: отходы, сооружения, площадка."""
    import numpy as np

    a = np.asarray(label.convert("RGB")).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    waste = (r > 200) & (g < 60) & (b < 60)
    facility = (b > 200) & (r < 60) & (g < 60)
    vacant = (r > 200) & (g > 200) & (b > 200)
    return waste, facility, vacant


def window(mask, cy, cx, size):
    h, w = mask.shape
    top = int(min(max(cy - size // 2, 0), h - size))
    left = int(min(max(cx - size // 2, 0), w - size))
    return top, left


def chips():
    """(картинки, метки) из исходных сцен CWLD."""
    import numpy as np
    from PIL import Image

    pictures, labels = [], []
    rng = np.random.default_rng(0)
    with zipfile.ZipFile(ARCHIVE) as z:
        names = [n for n in z.namelist()
                 if n.startswith("Original Dataset/") and "/label/" in n and n.endswith(".png")]
        for name in sorted(names):
            image_name = name.replace("/label/", "/images/").rsplit(".", 1)[0] + ".tif"
            try:
                image = Image.open(io.BytesIO(z.read(image_name))).convert("RGB")
                waste, facility, vacant = masks(Image.open(io.BytesIO(z.read(name))))
            except KeyError:
                continue
            if image.size[0] < CROP_PX or image.size[1] < CROP_PX:
                continue
            if waste.sum() > 0:
                ys, xs = np.nonzero(waste)
                top, left = window(waste, int(ys.mean()), int(xs.mean()), CROP_PX)
                if waste[top:top + CROP_PX, left:left + CROP_PX].mean() >= MIN_WASTE:
                    pictures.append(image.crop((left, top, left + CROP_PX, top + CROP_PX)))
                    labels.append(1)
            busy = waste | facility | vacant
            for _ in range(20):
                top = int(rng.integers(0, busy.shape[0] - CROP_PX + 1))
                left = int(rng.integers(0, busy.shape[1] - CROP_PX + 1))
                if not busy[top:top + CROP_PX, left:left + CROP_PX].any():
                    pictures.append(image.crop((left, top, left + CROP_PX, top + CROP_PX)))
                    labels.append(0)
                    break
    return pictures, labels


def main() -> int:
    import warnings

    import joblib
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from train_dinov2 import (
        CACHE as BASE_CACHE,
    )
    from train_dinov2 import (
        OWN_WEIGHT,
        embed,
        exam_pictures,
        load_model,
        predict_tta,
        report,
    )

    from vantage import env

    warnings.filterwarnings("ignore")
    env.configure()
    model = load_model()

    if CACHE.exists():
        z = np.load(CACHE)
        cw_x, cw_y = z["x"], z["y"]
    else:
        pics, cw_y = chips()
        print(f"── CWLD: {len(cw_y)} вырезок, свалок {sum(cw_y)}; считаю признаки…", flush=True)
        cw_x, cw_y = embed(model, pics), np.asarray(cw_y)
        np.savez_compressed(CACHE, x=cw_x, y=cw_y)
    print(f"── CWLD: {len(cw_y)} вырезок, свалок {int(cw_y.sum())}")

    base = np.load(BASE_CACHE, allow_pickle=True)
    kz_x, kz_y = base["kz_x"], base["kz_y"]
    drone = np.load(BASE_CACHE.with_name(BASE_CACHE.stem + "_drone.npz"))
    dr_x, dr_y = drone["x"], drone["y"]

    frame, pictures = exam_pictures()
    ex_x = embed(model, pictures)
    own = ((frame["truth"] == "not_landfill") & (frame["area"] != "outputs_real")).to_numpy()
    north = ((frame["area"] == "outputs_real")
             & frame["truth"].isin(["landfill", "not_landfill"])).to_numpy()
    y = (frame["truth"] == "landfill").to_numpy().astype(int)[north]
    ones = np.ones(int(north.sum()), dtype=bool)

    def fit(parts):
        X = np.vstack([p[0] for p in parts] + [ex_x[own]])
        Y = np.concatenate([p[1] for p in parts] + [np.zeros(int(own.sum()))])
        w = np.ones(len(Y))
        w[-int(own.sum()):] = OWN_WEIGHT
        clf = make_pipeline(StandardScaler(), LogisticRegression(
            C=0.1, class_weight="balanced", max_iter=3000))
        return clf.fit(X, Y, logisticregression__sample_weight=w)

    results = {}
    variants = {
        "drone+kz+own (продукт)": [(dr_x, dr_y), (kz_x, kz_y)],
        "cwld+own": [(cw_x, cw_y)],
        "drone+kz+cwld+own": [(dr_x, dr_y), (kz_x, kz_y), (cw_x, cw_y)],
    }
    fitted = {}
    for name, parts in variants.items():
        clf = fit(parts)
        fitted[name] = clf
        results[name] = report(name, y, clf.predict_proba(ex_x[north])[:, 1], ones)
    # Лучший вариант — ещё и с усреднением по поворотам, как в продукте.
    best = max(results, key=lambda k: results[k]["roc_auc"])
    north_pics = [p for p, keep in zip(pictures, north, strict=True) if keep]
    results[f"{best} + TTA"] = report(f"{best} + TTA", y,
                                      predict_tta(model, fitted[best], north_pics), ones)
    joblib.dump(fitted["drone+kz+cwld+own"], MODEL)
    for part in results.values():
        part.pop("_score", None)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"── записано в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
