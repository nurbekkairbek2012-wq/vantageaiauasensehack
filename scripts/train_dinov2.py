"""Классификатор снимков на признаках DINOv2 вместо ResNet18 с ImageNet.

── Зачем ───────────────────────────────────────────────────────────────

Классификатор «свалка / не свалка» по снимку 0,5 м обучен на итальянском
AerialWaste и переносится на Казахстан плохо: ROC-AUC 0,680 при интервале
0,517–0,841 (AI_RESULTS.md, 1ж). Признаки брались из ResNet18, обученного
на бытовых фотографиях ImageNet, и именно перенос между странами и
источниками съёмки — узкое место (раздел 1в: 0,62–0,69 между источниками).

DINOv2 обучена без разметки на 142 млн изображений и известна тем, что её
признаки переносятся между доменами лучше обученных с учителем. Модель
бесплатная и работает на процессоре: 0,05 с на снимок.

── Как проверяется ─────────────────────────────────────────────────────

Обучение — только на открытых наборах, ни одного нашего объекта:

  aw     — AerialWaste (Ломбардия), три архива;
  kz     — казахстанский набор из OSM: полигоны ТБО против карьеров,
           промплощадок и строек (data/kz_dataset);
  aw+kz  — оба.

Экзамен — data/eval/labeled.geojson, 158 объектов с вердиктом человека,
свежий снимок Esri Wayback 0,4 м (тот же кэш, что у Gemini). Рядом —
старая модель (highres_score) на тех же объектах. Решение по нижней
границе интервала и отдельно — внутри северного кольца.

    python scripts/train_dinov2.py
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

AW = ROOT / "data/aerialwaste"
KZ = ROOT / "data/kz_dataset"
EVAL = ROOT / "data/eval/labeled.geojson"
CACHE = ROOT / "data/dinov2_embeddings.npz"
OUT = ROOT / "data/eval/dinov2.json"
MODEL_OUT = ROOT / "models/dinov2_chip.joblib"

SIZE = 224
#: Центральное окно снимка экзамена, пикселей из 768: около 190 м —
#: ближе к охвату снимков AerialWaste, чем всё окно в 290 м.
CROP = 512


def load_model():
    import torch

    model = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14", trust_repo=True,
                           verbose=False)
    model.eval()
    return model


def to_tensor(picture):
    import numpy as np
    import torch

    picture = picture.convert("RGB").resize((SIZE, SIZE))
    arr = np.asarray(picture, dtype="float32") / 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype="float32")
    std = np.array([0.229, 0.224, 0.225], dtype="float32")
    arr = (arr - mean) / std
    return torch.from_numpy(arr.transpose(2, 0, 1))


def embed(model, pictures):
    """Признаки CLS для списка картинок, пачками по 32."""
    import numpy as np
    import torch

    out = []
    batch = []
    with torch.no_grad():
        for picture in pictures:
            batch.append(to_tensor(picture))
            if len(batch) == 32:
                out.append(model(torch.stack(batch)).numpy())
                batch = []
        if batch:
            out.append(model(torch.stack(batch)).numpy())
    return np.concatenate(out) if out else np.zeros((0, 384), dtype="float32")


def aerialwaste():
    """(картинки-генератор, метки) по трём скачанным архивам."""
    from PIL import Image

    labels = {}
    for name in ("training.json", "testing.json"):
        for item in json.loads((AW / name).read_text(encoding="utf-8"))["images"]:
            labels[item["file_name"]] = int(item["is_candidate_location"])
    names, marks = [], []
    for archive in sorted(AW.glob("images*.zip")):
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                base = Path(name).name
                if base in labels:
                    names.append((archive, name))
                    marks.append(labels[base])

    def pictures():
        opened = {}
        for archive, name in names:
            z = opened.setdefault(archive, zipfile.ZipFile(archive))
            yield Image.open(io.BytesIO(z.read(name)))
    return pictures(), marks


def kz():
    from PIL import Image

    index = json.loads((KZ / "index.json").read_text(encoding="utf-8"))
    rows = [r for r in index if (KZ / r["file"]).exists()]
    return (Image.open(KZ / r["file"]) for r in rows), [int(r["label"]) for r in rows]


def exam_pictures():
    """Свежие снимки объектов экзамена — из кэша Gemini (или сети)."""
    import geopandas as gpd
    from gemini_screen import WAYBACK, fetch, releases
    from PIL import Image

    frame = gpd.read_file(EVAL)
    release = releases()[-1][1]
    pictures, keep = [], []
    for i, row in enumerate(frame.itertuples()):
        point = row.geometry.representative_point()
        image, _ = fetch(point.y, point.x, WAYBACK.format(release=release, x="{x}", y="{y}",
                                                          z="{z}"), f"wb{release}")
        if image is None:
            continue
        h, w = image.shape[:2]
        top, left = (h - CROP) // 2, (w - CROP) // 2
        pictures.append(Image.fromarray(image[top:top + CROP, left:left + CROP]))
        keep.append(i)
    return frame.iloc[keep].reset_index(drop=True), pictures


def interval(y, score, seed=0, rounds=3000):
    import numpy as np
    from sklearn.metrics import roc_auc_score

    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(rounds):
        idx = rng.integers(0, len(y), len(y))
        if 0 < y[idx].sum() < len(idx):
            vals.append(roc_auc_score(y[idx], score[idx]))
    return float(np.percentile(vals, 5)), float(np.percentile(vals, 95))


def report(name, y, score, home):
    import numpy as np
    from sklearn.metrics import roc_auc_score

    auc = float(roc_auc_score(y, score))
    low, high = interval(y, score)
    inside = float(roc_auc_score(y[home], score[home])) if 0 < y[home].sum() < home.sum() else None
    in_low, in_high = (interval(y[home], score[home], seed=1)
                       if inside is not None else (None, None))
    # Сколько не-свалок ниже самой низкой свалки: их можно смотреть последними,
    # не рискуя ни одной из известных свалок.
    floor = float(score[y == 1].min())
    below = int(((score < floor) & (y == 0)).sum())
    print(f"   {name:22} ROC-AUC {auc:.3f} ({low:.2f}–{high:.2f}); внутри севера "
          f"{inside if inside is None else round(inside, 3)}"
          f"{'' if in_low is None else f' ({in_low:.2f}–{in_high:.2f})'}; "
          f"не-свалок ниже худшей свалки: {below} из {int((y == 0).sum())}")
    return {"roc_auc": round(auc, 3), "low": round(low, 3), "high": round(high, 3),
            "north": None if inside is None else round(inside, 3),
            "north_low": None if in_low is None else round(in_low, 3),
            "north_high": None if in_high is None else round(in_high, 3),
            "negatives_below_worst_dump": below, "negatives": int((y == 0).sum()),
            "_score": [round(float(s), 4) for s in np.asarray(score)]}


def main() -> int:
    import warnings

    import joblib
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    from vantage import env

    warnings.filterwarnings("ignore")
    env.configure()
    model = load_model()

    if CACHE.exists():
        cached = np.load(CACHE, allow_pickle=True)
        aw_x, aw_y = cached["aw_x"], cached["aw_y"]
        kz_x, kz_y = cached["kz_x"], cached["kz_y"]
        print(f"── признаки из кэша: AerialWaste {len(aw_y)}, Казахстан {len(kz_y)}")
    else:
        pics, aw_y = aerialwaste()
        print(f"── AerialWaste: {len(aw_y)} снимков, считаю признаки…")
        aw_x = embed(model, pics)
        pics, kz_y = kz()
        print(f"── Казахстан (OSM): {len(kz_y)} снимков…")
        kz_x = embed(model, pics)
        aw_y, kz_y = np.asarray(aw_y), np.asarray(kz_y)
        np.savez_compressed(CACHE, aw_x=aw_x, aw_y=aw_y, kz_x=kz_x, kz_y=kz_y)

    frame, pictures = exam_pictures()
    ex_x = embed(model, pictures)
    decided = frame["truth"].isin(["landfill", "not_landfill"]).to_numpy()
    y = (frame["truth"] == "landfill").to_numpy().astype(int)[decided]
    home = (frame["area"] == "outputs_real").to_numpy()[decided]
    x = ex_x[decided]
    print(f"── Экзамен: {len(y)} объектов, свалок {y.sum()}\n")

    def fit(X, Y):
        clf = make_pipeline(StandardScaler(), LogisticRegression(
            C=0.1, class_weight="balanced", max_iter=3000))
        return clf.fit(X, Y)

    results = {}
    for name, (X, Y) in {
        "aw": (aw_x, aw_y),
        "kz": (kz_x, kz_y),
        "aw+kz": (np.vstack([aw_x, kz_x]), np.concatenate([aw_y, kz_y])),
    }.items():
        clf = fit(X, Y)
        results[name] = report(f"DINOv2, учёба на {name}", y, clf.predict_proba(x)[:, 1], home)
        if name == "aw+kz":
            MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(clf, MODEL_OUT)

    # Старая модель (ResNet18 + бустинг, AerialWaste) на тех же объектах.
    old = frame["highres_score"].to_numpy(dtype="float64")[decided]
    have = np.isfinite(old)
    if have.sum() and 0 < y[have].sum() < have.sum():
        results["old_resnet18"] = report("старая, ResNet18", y[have], old[have], home[have])
        for name in ("aw", "aw+kz"):
            score = np.asarray(results[name]["_score"])[have]
            results[f"{name}_same_objects"] = report(f"DINOv2 {name}, те же", y[have],
                                                     score, home[have])

    # Оценки по каждому объекту — рядом, чтобы сравнивать с другими
    # проверяющими (Gemini, человек) на тех же объектах.
    import pandas as pd

    pd.DataFrame({
        "candidate_id": frame["candidate_id"].to_numpy()[decided],
        "truth": frame["truth"].to_numpy()[decided],
        "area": frame["area"].to_numpy()[decided],
        "dinov2_aw": results["aw"]["_score"],
        "dinov2_awkz": results["aw+kz"]["_score"],
    }).to_csv(OUT.with_name("dinov2_scores.csv"), index=False)

    for part in results.values():
        part.pop("_score", None)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n── записано в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
