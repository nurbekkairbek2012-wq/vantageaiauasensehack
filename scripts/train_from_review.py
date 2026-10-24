"""Обучить отбраковщик на нашей собственной разметке глазами.

── Чем это отличается от прежних попыток ───────────────────────────────

Первая попытка (`train_aerialwaste.py`) училась на итальянском наборе
AerialWaste — 5 220 снимков, но чужая местность.

Вторая (`build_kz_dataset.py`) училась на полигонах ТБО из OpenStreetMap.
Она дала 0,786 против 0,643 у прежней, была поставлена в продукт и снята
через час: на 33 заведомо ложных находках восточного пояса она назвала
свалкой 63% против 10% у прежней. Модель выучила «рукотворное против
природного», а не «отходы».

Здесь материал другой — **наша разметка глазами**. К утру 23 августа
накопилось 179 меток по снимкам 0,5 м из пяти областей вокруг Астаны:
северное кольцо, восточный и юго-восточный пояса, западная промзона, юг.

Это принципиально: положительные и отрицательные примеры размечены **одним
человеком по одному правилу** и происходят из **разных мест**. Прежние
попытки нарушали и то, и другое — и обе споткнулись именно об это.

── Чем проверяется ─────────────────────────────────────────────────────

Тремя вещами, и все три обязательны:

1. **Перекрёстная проверка с делением по МЕСТУ, а не по объекту.** Объекты
   из одной промзоны похожи друг на друга; случайное деление посадило бы
   соседей и в обучение, и в проверку, и метрика выросла бы на пустом
   месте. Делим по клеткам сетки 2 км.

2. **Интервал по бутстрэпу.** Свалок одиннадцать. Середина на такой
   выборке сдвигается от одного объекта, и решение принимается по нижней
   границе.

3. **Специфичность на восточном поясе** — 33 объекта, просмотренных
   глазами и не содержащих ни одной свалки. Ровно та проверка, на которой
   вторая попытка провалилась. Модель, которая её не проходит, не
   ставится, каким бы ни был ROC-AUC.

── Результат: отрицательный ────────────────────────────────────────────

Обучено 23 августа на 101 объекте (свалок 9, клеток 53).

    AerialWaste, чужой итальянский набор   ROC-AUC 0,680  (0,517 – 0,841)
    на нашей разметке                      ROC-AUC 0,326  (0,202 – 0,450)

Интервал целиком ниже случайного: модель антикоррелирована с истиной. При
делении по месту в каждой части остаётся два-три положительных примера
против девяноста отрицательных — учить нечему.

В продукт НЕ поставлено. Скрипт оставлен, чтобы результат можно было
повторить, когда объектов станет в разы больше. Разбор в AI_RESULTS.md,
раздел 1з.

    python scripts/train_from_review.py
"""

import logging
import sys
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("train")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))

AREAS = ("outputs_real", "outputs_astana_east", "outputs_astana_southeast",
         "outputs_astana_industrial_west", "outputs_astana_south")
OUT = Path("models/review_chip.joblib")

#: Сторона клетки для деления при проверке, в градусах (~2 км по широте).
#: Меньше — и соседние объекты попадут в разные части, что и есть утечка.
CELL = 0.018


def collect():
    """Все размеченные объекты пяти областей с их вердиктами."""
    import geopandas as gpd
    import pandas as pd

    labels = gpd.read_file("labels_manual.geojson")
    codes = {"свалка": 1, "не свалка": 0}
    labels = labels[labels["verdict"].isin(codes)]

    parts = []
    for area in AREAS:
        path = Path(area) / "candidates.geojson"
        if not path.exists():
            continue
        objects = gpd.read_file(path).to_crs(labels.crs)
        joined = gpd.sjoin(objects, labels[["verdict", "geometry"]],
                           predicate="contains", how="inner")
        joined = joined[~joined.index.duplicated(keep="first")]
        joined["mark"] = joined["verdict"].map(codes)
        joined["area"] = area
        parts.append(joined[["candidate_id", "mark", "area", "geometry"]])

    everything = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=labels.crs)
    # Один объект найден в двух перекрывающихся областях — берём один раз.
    # Ключ для дедупликации считается в градусах, и это здесь допустимо:
    # нужен не размер, а совпадение точки. Четыре знака — около десяти
    # метров, то есть один объект, найденный в двух перекрывающихся
    # областях, схлопнется, а соседние — нет.
    point = everything.geometry.representative_point()
    everything["key"] = (point.y.round(4).astype(str) + "_" + point.x.round(4).astype(str))
    return everything.drop_duplicates("key").reset_index(drop=True)


def main() -> int:
    import geopandas as gpd  # noqa: F401
    import joblib
    import torch
    from lightgbm import LGBMClassifier
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import GroupKFold

    from vantage import env
    from vantage.config import load_settings

    env.configure()
    cfg = load_settings().verify

    from eval_on_ours import picture

    data = collect().to_crs(4326)
    log.info("размеченных объектов: %d (свалок %d)", len(data), int(data["mark"].sum()))
    if data["mark"].sum() < 6:
        log.error("свалок слишком мало для обучения")
        return 1

    from train_aerialwaste import backbone, preprocess

    net, prep = backbone(), preprocess()
    images, marks, groups, names = [], [], [], []
    for row in data.itertuples():
        point = row.geometry.centroid
        image = picture(point.y, point.x, str(row.candidate_id), cfg, False)
        if image is None:
            continue
        images.append(prep(image))
        marks.append(int(row.mark))
        # Группа — клетка сетки: соседние объекты не должны расходиться
        # между обучением и проверкой.
        groups.append(f"{int(point.y / CELL)}_{int(point.x / CELL)}")
        names.append(str(row.candidate_id))

    log.info("снимков получено: %d (свалок %d), клеток %d",
             len(images), sum(marks), len(set(groups)))
    if sum(marks) < 6:
        log.error("свалок со снимками слишком мало")
        return 1

    with torch.no_grad():
        features = net(torch.stack(images)).numpy()
    marks = np.array(marks)
    groups = np.array(groups)

    # Перекрёстная проверка по клеткам.
    folds = min(5, len(set(groups)))
    scores = np.zeros(len(marks), dtype=float)
    for train_idx, test_idx in GroupKFold(n_splits=folds).split(features, marks, groups):
        if marks[train_idx].sum() == 0:
            continue
        model = LGBMClassifier(n_estimators=200, learning_rate=0.05, max_depth=4,
                               class_weight="balanced", random_state=0, verbose=-1)
        model.fit(features[train_idx], marks[train_idx])
        scores[test_idx] = model.predict_proba(features[test_idx])[:, 1]

    roc = roc_auc_score(marks, scores)
    rng = np.random.default_rng(0)
    draws = []
    for _ in range(4000):
        pick = rng.integers(0, len(marks), len(marks))
        if 0 < marks[pick].sum() < len(pick):
            draws.append(roc_auc_score(marks[pick], scores[pick]))
    low, high = np.percentile(draws, [5, 95])

    log.info("")
    log.info("── Перекрёстная проверка с делением по месту ──")
    log.info("ROC-AUC %.3f, 90%% интервал %.3f – %.3f", roc, low, high)
    log.info("для сравнения, AerialWaste на тех же объектах: 0,680 (0,517 – 0,841)")
    log.info("")

    if low <= 0.517:
        log.warning("Нижняя граница не выше прежней модели — ставить нечего.")
        log.warning("Это отрицательный результат, и записать его надо именно так.")
    else:
        log.info("Нижняя граница выше прежней. Дальше — проверка специфичности:")
        log.info("без неё модель ставить нельзя, вторая попытка сорвалась именно там.")

    OUT.parent.mkdir(exist_ok=True)
    final = LGBMClassifier(n_estimators=200, learning_rate=0.05, max_depth=4,
                           class_weight="balanced", random_state=0, verbose=-1)
    final.fit(features, marks)
    joblib.dump(final, OUT)
    log.info("модель сохранена: %s (в продукт НЕ ставится до проверки)", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
