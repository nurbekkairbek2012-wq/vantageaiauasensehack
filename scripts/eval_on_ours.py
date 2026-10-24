"""Проверить модель, обученную на AerialWaste, на наших казахстанских метках.

── Зачем отдельный скрипт ──────────────────────────────────────────────

Качество на AerialWaste говорит только о том, что модель научилась
отличать свалки Ломбардии от не-свалок Ломбардии. Нам нужно другое: как
она работает на Казахстане — другой ландшафт, другой состав отходов,
другая высота солнца, другая застройка.

Ответ на этот вопрос — единственное честное число, которое можно назвать
на защите. Всё остальное будет описанием чужого датасета.

── Почему выборка маленькая и что это значит ───────────────────────────

Наших меток 71, свалок среди них пять. Это мало настолько, что точечная
оценка бессмысленна: убери одну свалку — метрика прыгнет. Поэтому здесь
считается не только само число, но и границы, в которых оно гуляет
(бутстрэп). Если границы окажутся от «случайно» до «отлично» — так и надо
сказать, а не выбрать середину.

── Чего этот скрипт не делает ──────────────────────────────────────────

Он не дообучает модель на наших данных. Дообучение на пяти положительных
примерах даст модель, подогнанную под эти пять, и проверять её будет
нечем. Здесь измеряется чистый перенос.

    python scripts/eval_on_ours.py
"""

import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("eval")

LABELS = Path("labels_manual.geojson")
CHIPS = Path("web-next/public/chips")
MODEL = Path("models/aerialwaste_chip.joblib")

#: Как вердикт разметки превращается в метку. «Не понятно» выбрасывается:
#: объект, про который человек не смог решить, не годится ни в
#: положительные, ни в отрицательные — он бы измерял нашу неуверенность,
#: а не качество модели.
POSITIVE = "свалка"
NEGATIVE = "не свалка"


def wanted() -> dict[str, int]:
    """candidate_id -> 1 свалка / 0 не свалка."""
    features = json.loads(LABELS.read_text(encoding="utf-8"))["features"]
    out: dict[str, int] = {}
    skipped = 0
    for item in features:
        props = item["properties"]
        cid, verdict = props.get("candidate_id"), props.get("verdict")
        if not cid or verdict not in (POSITIVE, NEGATIVE):
            skipped += 1
            continue
        out[cid] = int(verdict == POSITIVE)
    log.info("меток пригодных %d, пропущено %d («не понятно» и без идентификатора)",
             len(out), skipped)
    return out


def chip_paths() -> dict[str, list[Path]]:
    """candidate_id -> файлы чипов «после».

    Идентификатор в имени файла идёт после двойного подчёркивания:
    astana_north_x000y000__C00034-after.png. Он уникален внутри плитки, но
    не между плитками, поэтому один и тот же C00034 может встретиться
    дважды — такие случаи считаются и исключаются, иначе метка одного
    объекта досталась бы снимку другого.
    """
    found: dict[str, list[Path]] = defaultdict(list)
    for path in sorted(CHIPS.glob("*-after.png")):
        match = re.search(r"__([A-Za-z]\d+)-after\.png$", path.name)
        if match:
            found[match.group(1)].append(path)
    return found


def main() -> int:
    if not MODEL.exists():
        log.error("нет модели %s — сначала scripts/train_aerialwaste.py", MODEL)
        return 1

    marks = wanted()
    chips = chip_paths()

    import joblib
    import torch
    from PIL import Image

    sys.path.insert(0, str(Path("scripts")))
    from train_aerialwaste import backbone, preprocess

    net, prep = backbone(), preprocess()
    model = joblib.load(MODEL)

    ready, truth, ambiguous, missing = [], [], 0, 0
    for cid, mark in marks.items():
        paths = chips.get(cid, [])
        if not paths:
            missing += 1
            continue
        if len(paths) > 1:
            ambiguous += 1
            continue
        ready.append(paths[0])
        truth.append(mark)

    log.info("сопоставлено %d объектов; без чипа %d, неоднозначных %d",
             len(ready), missing, ambiguous)
    if len(ready) < 10 or sum(truth) == 0:
        log.error("нечего измерять: объектов %d, свалок %d", len(ready), sum(truth))
        return 1

    vectors = []
    with torch.no_grad():
        for start in range(0, len(ready), 32):
            batch = [prep(Image.open(p).convert("RGB")) for p in ready[start:start + 32]]
            vectors.append(net(torch.stack(batch)).numpy())
    features = np.vstack(vectors)

    scores = model.predict_proba(features)[:, 1]
    truth = np.array(truth)

    from sklearn.metrics import average_precision_score, roc_auc_score

    base = truth.mean()
    log.info("")
    log.info("── Перенос на Казахстан ───────────────────────")
    log.info("объектов %d, свалок %d (%.0f%%)", len(truth), int(truth.sum()), 100 * base)

    try:
        roc = roc_auc_score(truth, scores)
        pr = average_precision_score(truth, scores)
    except ValueError as error:
        log.error("метрику не посчитать: %s", error)
        return 1

    log.info("ROC-AUC %.3f", roc)
    log.info("PR-AUC  %.3f при базовой частоте %.3f — лучше случайного в %.1f раза",
             pr, base, pr / base)

    # Границы, а не одно число: при пяти свалках точечная оценка ничего не
    # значит, и назвать её на защите без интервала — значит подставиться.
    rng = np.random.default_rng(0)
    draws = []
    for _ in range(2000):
        pick = rng.integers(0, len(truth), len(truth))
        if truth[pick].sum() in (0, len(pick)):
            continue
        draws.append(roc_auc_score(truth[pick], scores[pick]))
    if draws:
        low, high = np.percentile(draws, [5, 95])
        log.info("ROC-AUC, 90%% интервал по бутстрэпу: %.3f – %.3f", low, high)
        if low < 0.5:
            log.warning("нижняя граница ниже 0.5 — перенос НЕ доказан, так и говорить")
        elif low > 0.7:
            log.info("нижняя граница выше 0.7 — перенос состоялся")

    log.info("")
    log.info("── Как модель оценила наши объекты ────────────")
    for path, mark, score in sorted(zip(ready, truth, scores), key=lambda x: -x[2])[:12]:
        log.info("  %.3f  %-12s  %s", score, "СВАЛКА" if mark else "не свалка", path.name[:46])
    return 0


if __name__ == "__main__":
    sys.exit(main())
