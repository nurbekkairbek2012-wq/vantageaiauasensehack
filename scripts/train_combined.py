"""Обучить классификатор свалок на трёх источниках сразу.

── Почему три, а не один ───────────────────────────────────────────────

У каждого источника своя дыра, и они не совпадают.

**AerialWaste** (Ломбардия, 5 220 снимков) — размечен профессиональными
дешифровщиками, но это Италия: другой ландшафт, другое солнце, другой
состав отходов. Модель на нём одном даёт 0,858 у себя и 0,643 у нас при
интервале 0,333–0,923, то есть перенос не доказан.

**Дрон** (2 115 снимков, CC-BY-4.0) — настоящие НЕЗАКОННЫЕ свалки, и
отрицательные вырезаны из тех же кадров: та же камера, то же солнце, тот
же грунт. Пара честная до предела. Но отрицательные там лёгкие — трава и
дорожки, потому что снимали сельскую местность.

**Казахстан из OSM** — наш ландшафт и трудные отрицательные: карьеры,
промплощадки, стройки, пашня, вода, то есть ровно то, что детектор путает
со свалкой. Но положительные там в основном ЗАКОННЫЕ полигоны ТБО:
крупные, старые, не похожие на обочину с двумя самосвалами.

Вместе они закрывают дыры друг друга: у дрона — незаконность и честная
пара, у Казахстана — ландшафт и трудные отрицательные, у AerialWaste —
объём и качество разметки.

── Как проверяется ─────────────────────────────────────────────────────

Кросс-проверка внутри смеси сказала бы только, что модель выучила смесь.
Поэтому качество меряется ОТДЕЛЬНО на каждом источнике при обучении на
двух других: так видно, переносится ли она между доменами вообще.

И отдельно — на наших казахстанских объектах с разметкой, которых в
обучении нет ни в каком виде. Это единственное число, которое можно
называть на защите.

    python scripts/train_combined.py [--eval-only]
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("combined")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CACHE = Path("data/combined_embeddings.npz")
MODEL = Path("models/combined_chip.joblib")

#: Источники: имя -> (папка положительных, папка отрицательных).
#: AerialWaste подключается отдельно — он лежит в архивах, а не папками.
FOLDERS = {
    "дрон": (Path("data/drone_crops/waste"), Path("data/drone_crops/clean")),
}

KZ = Path("data/kz_dataset")

BATCH = 32


def backbone_and_prep():
    sys.path.insert(0, str(Path("scripts")))
    from train_aerialwaste import backbone, preprocess

    return backbone(), preprocess()


def embed_paths(paths, net, prep) -> np.ndarray:
    """Эмбеддинги пачками. Битый файл пропускается, а не роняет час работы."""
    import torch
    from PIL import Image

    out = []
    batch = []
    for path in paths:
        try:
            batch.append(prep(Image.open(path).convert("RGB")))
        except Exception:
            continue
        if len(batch) == BATCH:
            with torch.no_grad():
                out.append(net(torch.stack(batch)).numpy())
            batch = []
    if batch:
        with torch.no_grad():
            out.append(net(torch.stack(batch)).numpy())
    return np.vstack(out) if out else np.zeros((0, 512), dtype="float32")


def collect() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Признаки, метки и номер источника по всем наборам."""
    net, prep = backbone_and_prep()
    features, marks, source = [], [], []

    for index, (name, (pos_dir, neg_dir)) in enumerate(FOLDERS.items()):
        for folder, label in ((pos_dir, 1), (neg_dir, 0)):
            paths = sorted(folder.glob("*.png"))
            if not paths:
                log.warning("%s: пусто в %s", name, folder)
                continue
            vectors = embed_paths(paths, net, prep)
            features.append(vectors)
            marks.append(np.full(len(vectors), label))
            source.append(np.full(len(vectors), index))
            log.info("%-10s %-6s %5d снимков", name, "мусор" if label else "чисто", len(vectors))

    # Казахстан: метка зашита в имя файла, 1_ или 0_
    if KZ.exists():
        for label in (1, 0):
            paths = sorted(KZ.glob(f"{label}_*.png"))
            if not paths:
                continue
            vectors = embed_paths(paths, net, prep)
            features.append(vectors)
            marks.append(np.full(len(vectors), label))
            source.append(np.full(len(vectors), len(FOLDERS)))
            log.info("%-10s %-6s %5d снимков", "Казахстан",
                     "мусор" if label else "чисто", len(vectors))

    # AerialWaste — из кэша эмбеддингов, если он посчитан
    aerial = Path("data/aerialwaste/embeddings.npz")
    if aerial.exists():
        data = np.load(aerial)
        features.append(data["features"])
        marks.append(data["marks"])
        source.append(np.full(len(data["marks"]), len(FOLDERS) + 1))
        log.info("%-10s        %5d снимков (из кэша)", "AerialWaste", len(data["marks"]))

    if not features:
        raise SystemExit("ни одного источника — сначала соберите наборы")
    return np.vstack(features), np.concatenate(marks), np.concatenate(source)


def fresh_model():
    import lightgbm as lgb

    return lgb.LGBMClassifier(
        n_estimators=500, learning_rate=0.05, num_leaves=31,
        subsample=0.8, colsample_bytree=0.6, random_state=0, verbose=-1,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-only", action="store_true")
    args = parser.parse_args()

    if CACHE.exists() and args.eval_only:
        data = np.load(CACHE)
        features, marks, source = data["features"], data["marks"], data["source"]
        log.info("эмбеддинги из кэша: %d", len(marks))
    else:
        features, marks, source = collect()
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(CACHE, features=features, marks=marks, source=source)
        log.info("эмбеддинги сохранены: %s", CACHE)

    names = list(FOLDERS) + ["Казахстан", "AerialWaste"]
    log.info("")
    log.info("── Смесь ──")
    for index in np.unique(source):
        part = marks[source == index]
        log.info("%-12s %5d снимков, свалок %4d (%.0f%%)",
                 names[int(index)] if int(index) < len(names) else f"источник {index}",
                 len(part), int(part.sum()), 100 * part.mean())

    from sklearn.metrics import roc_auc_score

    # Проверка между доменами: учим на двух источниках, меряем на третьем.
    # Кросс-проверка внутри смеси сказала бы только, что модель выучила
    # смесь, а нам нужно знать, переносится ли она вообще.
    log.info("")
    log.info("── Перенос между источниками ──")
    for index in np.unique(source):
        train = source != index
        test = source == index
        if marks[test].sum() in (0, test.sum()) or marks[train].sum() == 0:
            continue
        model = fresh_model()
        model.fit(features[train], marks[train])
        score = roc_auc_score(marks[test], model.predict_proba(features[test])[:, 1])
        label = names[int(index)] if int(index) < len(names) else str(index)
        log.info("обучено без «%s» → ROC-AUC на нём %.3f", label, score)

    final = fresh_model()
    final.fit(features, marks)

    import joblib
    MODEL.parent.mkdir(exist_ok=True)
    joblib.dump(final, MODEL)
    log.info("")
    log.info("модель сохранена: %s", MODEL)
    log.info("Дальше: scripts/eval_on_ours.py --model %s", MODEL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
