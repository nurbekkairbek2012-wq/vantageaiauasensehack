"""Классификатор «свалка / не свалка» по снимку, обученный на AerialWaste.

── Зачем чужой датасет ─────────────────────────────────────────────────

Своей разметки 71 объект, и свалок среди них пять. На пяти примерах
нельзя ни обучить модель, ни — что важнее — проверить её: доверительный
интервал по пяти наблюдениям шире самой метрики.

AerialWaste (Politecnico di Milano, CC BY, Scientific Data 2023) — 3 478
положительных и 6 956 отрицательных снимков, размеченных профессиональными
дешифровщиками. Модальность та же, что у наших чипов: RGB высокого
разрешения с воздуха и со спутника.

── Чего этот датасет не даёт ───────────────────────────────────────────

Он снят в Ломбардии. Другой ландшафт, другой состав отходов, другая
высота солнца, другой тип застройки. Модель, обученная там, на Казахстане
работает хуже — вопрос только насколько, и это здесь измеряется, а не
предполагается: отдельный шаг проверяет её на наших 71 метках, которых
она не видела.

Если перенос окажется плохим — это результат, а не провал. Плохой
перенос, названный вслух, стоит дороже хорошего, взятого на веру.

── Почему замороженный backbone, а не дообучение ───────────────────────

Машина без видеокарты. Дообучение ResNet на десяти тысячах снимков на
процессоре — это сутки. Замороженная сеть считает эмбеддинги один раз,
дальше градиентный бустинг обучается за минуты и позволяет честную
кросс-проверку, которая при дообучении была бы недоступна по времени.

Для переноса между доменами это к тому же безопаснее: замороженные
признаки ImageNet общие, а дообученные подстроились бы под Ломбардию.

    python scripts/train_aerialwaste.py [--limit N]
"""

import argparse
import io
import json
import logging
import sys
import zipfile
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("aerialwaste")

ROOT = Path("data/aerialwaste")
CACHE = ROOT / "embeddings.npz"
MODEL = Path("models/aerialwaste_chip.joblib")

#: Размер входа ResNet. Наши чипы 144×144, снимки AerialWaste около
#: 1050×1050 — оба приводятся сюда, и разница в охвате земли остаётся.
#: Это известное ограничение переноса, см. заголовок.
SIDE = 224

#: Батч подобран под память, а не под скорость: на процессоре разницы
#: между 32 и 64 почти нет, а 64 на слабой машине уходит в своп.
BATCH = 32


def backbone():
    """ResNet18 без последнего слоя: на выходе 512 чисел на снимок."""
    import torch
    import torchvision.models as models

    net = models.resnet18(weights=models.ResNet18_Weights.IMAGENET1K_V1)
    net.fc = torch.nn.Identity()
    net.eval()
    return net


def preprocess():
    from torchvision import transforms

    return transforms.Compose([
        transforms.Resize((SIDE, SIDE)),
        transforms.ToTensor(),
        # Нормировка ImageNet обязательна: веса обучены с ней, и без неё
        # признаки уезжают настолько, что модель работает как случайная.
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])


def labels() -> dict[str, int]:
    """Имя файла - метка. is_candidate_location: 1 — свалка, 0 — нет."""
    out: dict[str, int] = {}
    for name in ("training.json", "testing.json"):
        path = ROOT / name
        if not path.exists():
            raise SystemExit(f"нет {path} — сначала скачайте разметку AerialWaste")
        for image in json.loads(path.read_text(encoding="utf-8"))["images"]:
            out[image["file_name"]] = int(image["is_candidate_location"])
    return out


def archives() -> list[Path]:
    found = sorted(ROOT.glob("images*.zip"))
    if not found:
        raise SystemExit(f"нет архивов снимков в {ROOT}")
    return found


def embed(limit: int | None):
    """Пройти по архивам и посчитать эмбеддинги, не распаковывая их на диск."""
    import torch
    from PIL import Image

    known = labels()
    net, prep = backbone(), preprocess()

    vectors: list[np.ndarray] = []
    marks: list[int] = []
    batch: list = []

    def flush() -> None:
        if not batch:
            return
        with torch.no_grad():
            vectors.append(net(torch.stack(batch)).numpy())
        batch.clear()

    for archive in archives():
        with zipfile.ZipFile(archive) as zf:
            members = [m for m in zf.namelist() if m.lower().endswith((".png", ".jpg", ".jpeg"))]
            log.info("%s: снимков %d", archive.name, len(members))
            for member in members:
                if limit and len(marks) >= limit:
                    break
                stem = Path(member).name
                if stem not in known:
                    continue
                try:
                    with zf.open(member) as handle:
                        image = Image.open(io.BytesIO(handle.read())).convert("RGB")
                except Exception:
                    # Один битый файл не должен ронять час работы.
                    continue
                batch.append(prep(image))
                marks.append(known[stem])
                if len(batch) == BATCH:
                    flush()
                if len(marks) % 500 == 0:
                    log.info("   посчитано %d, свалок среди них %d", len(marks), sum(marks))
    flush()

    if not vectors:
        raise SystemExit("ни одного снимка не совпало с разметкой")
    return np.vstack(vectors), np.array(marks)


def fresh_model():
    import lightgbm as lgb

    return lgb.LGBMClassifier(
        n_estimators=400, learning_rate=0.05, num_leaves=31,
        subsample=0.8, colsample_bytree=0.6, random_state=0, verbose=-1,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="взять не больше N снимков")
    args = parser.parse_args()

    if CACHE.exists():
        log.info("эмбеддинги из кэша %s", CACHE)
        data = np.load(CACHE)
        features, marks = data["features"], data["marks"]
    else:
        features, marks = embed(args.limit or None)
        np.savez_compressed(CACHE, features=features, marks=marks)
        log.info("эмбеддинги сохранены: %s", CACHE)

    log.info("выборка: %d снимков, свалок %d (%.1f%%)",
             len(marks), int(marks.sum()), 100 * marks.mean())

    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.model_selection import StratifiedKFold

    # Кросс-проверка, а не одна отложенная выборка: качество на одном
    # разбиении при таком размере гуляет на несколько процентов, и по
    # одному числу нельзя понять, обучилась модель или повезло.
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
    out_of_fold = np.zeros(len(marks))
    for step, (train, test) in enumerate(folds.split(features, marks), 1):
        model = fresh_model()
        model.fit(features[train], marks[train])
        out_of_fold[test] = model.predict_proba(features[test])[:, 1]
        log.info("   фолд %d готов", step)

    base = float(marks.mean())
    pr = average_precision_score(marks, out_of_fold)
    log.info("")
    log.info("── Качество вне выборки, AerialWaste ──")
    log.info("ROC-AUC %.3f", roc_auc_score(marks, out_of_fold))
    log.info("PR-AUC  %.3f при базовой частоте %.3f — лучше случайного в %.1f раза",
             pr, base, pr / base)

    # Модель на всех данных — её и понесём на наши чипы.
    final = fresh_model()
    final.fit(features, marks)

    import joblib
    MODEL.parent.mkdir(exist_ok=True)
    joblib.dump(final, MODEL)
    log.info("модель сохранена: %s", MODEL)
    log.info("")
    log.info("Дальше: scripts/eval_on_ours.py — проверка на наших 71 метках.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
