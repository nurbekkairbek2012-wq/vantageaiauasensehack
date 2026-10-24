"""Обучить сеть с перекрёстной проверкой и получить честные вероятности.

── Что было не так с обычным обучением ─────────────────────────────────

Первый запуск обучения прошёл успешно и выдал бесполезный результат:
медианная вероятность на тридцати опубликованных объектах — 0,999. Модель
была уверена почти во всём, и уверена справедливо: эти тридцать объектов
и составляли её положительную часть обучающей выборки. Она их запомнила.

Показать такое число на карте значит выдать запоминание за предсказание.
Числу 99,9% верят, а проверить его на защите нечем — вопрос «а откуда
уверенность» получил бы ответ «модель видела этот объект на обучении».

── Как это чинится ─────────────────────────────────────────────────────

Перекрёстной проверкой. Выборка делится на пять частей; пять раз модель
учится на четырёх и предсказывает пятую, которой не видела. Вероятность
каждого объекта приходит от модели, для которой он был новым.

Деление — ПО ОБЪЕКТАМ, а не по кускам. Объект, разрезанный границей
плитки, даёт два куска одного и того же места; окажись они в разных
частях, модель увидела бы на обучении ровно то, что предсказывает.
Это самая частая и самая незаметная утечка в геоданных.

Метрика тоже считается по вневыборочным предсказаниям. Она ниже той, что
показывает обычное обучение, и это правильно: та завышена.

В конце обучается ещё одна модель — на всей выборке целиком. Она идёт в
следующий прогон, где будет встречать действительно новые объекты. Её
собственная метрика не публикуется: проверять её не на чем.

    python scripts/train_crossval.py [файл-разметки]
"""

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

import geopandas as gpd
import numpy as np
import pandas as pd

from vantage.config import load_settings
from vantage.orchestrate import load_chips

FOLDS = 5
MIN_PER_CLASS = 5
OUTPUTS = Path("outputs_real")
LABELS = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("labels_evidence.json")

settings = load_settings()
crs = settings.project.crs_working

payload = json.loads(LABELS.read_text(encoding="utf-8"))
raw = payload.get("labels", payload)
verdicts = {"landfill": 1, "not": 0}
labels = {cid: verdicts[v] for cid, v in raw.items() if v in verdicts}
logging.info("Разметка: %d кусков (свалка %d)", len(labels), sum(labels.values()))

chips = load_chips(Path("data/chips"))
if chips is None:
    raise SystemExit("чипов нет — сначала пройдите область плитками")

index = np.array([i for i, cid in enumerate(chips.candidate_ids) if cid in labels])
if index.size == 0:
    raise SystemExit("метки и чипы не сопоставились: идентификаторы из разных прогонов")

dataset = chips.subset(index)
dataset.labels = np.array([labels[chips.candidate_ids[i]] for i in index], dtype="int64")
keys = [chips.candidate_ids[i] for i in index]

# ── Группы: куски одного объекта не должны разъезжаться по частям ───────
pieces: list[gpd.GeoDataFrame] = []
for path in sorted((OUTPUTS / "tiles").glob("*.geojson")):
    layer = gpd.read_file(path)
    if layer.empty:
        continue
    layer = layer.to_crs(crs)
    layer["chip_key"] = path.stem + ":" + layer["candidate_id"].astype(str)
    pieces.append(layer[["chip_key", "geometry"]])

all_pieces = gpd.GeoDataFrame(pd.concat(pieces, ignore_index=True), crs=crs)
merged = gpd.read_file(OUTPUTS / "candidates_raw.geojson").to_crs(crs)
link = gpd.sjoin(
    all_pieces, merged[["candidate_id", "geometry"]], predicate="intersects", how="left"
)
# Кусок, не попавший ни в один склеенный объект, — сам себе группа.
group_of = {
    row.chip_key: (row.candidate_id if isinstance(row.candidate_id, str) else row.chip_key)
    for row in link.drop_duplicates("chip_key").itertuples()
}
groups = np.array([group_of.get(k, k) for k in keys])
logging.info("Групп (склеенных объектов): %d на %d кусков", len(set(groups)), len(keys))

# ── Перекрёстная проверка по группам ────────────────────────────────────
from vantage.model.infer import predict
from vantage.model.train import train

unique_groups = np.array(sorted(set(groups)))
rng = np.random.default_rng(settings.model.seed)
rng.shuffle(unique_groups)
fold_of_group = {g: i % FOLDS for i, g in enumerate(unique_groups)}
fold = np.array([fold_of_group[g] for g in groups])

oof = np.full(len(keys), np.nan, dtype="float64")

for k in range(FOLDS):
    train_mask = fold != k
    test_mask = ~train_mask
    y_train = dataset.labels[train_mask]
    if len(np.unique(y_train)) < 2 or y_train.sum() < MIN_PER_CLASS:
        logging.warning("Часть %d пропущена: в обучающей половине не хватает класса", k + 1)
        continue

    part = dataset.subset(np.flatnonzero(train_mask))
    part.labels = y_train
    logging.info(
        "Часть %d/%d: обучение на %d, проверка на %d",
        k + 1, FOLDS, int(train_mask.sum()), int(test_mask.sum()),
    )
    model = train(part, settings.model, progress=False)

    held = dataset.subset(np.flatnonzero(test_mask))
    held.candidate_ids = [keys[i] for i in np.flatnonzero(test_mask)]
    result = predict(model, held)
    scores = {r["candidate_id"]: r["probability"] for r in result.as_records()}
    for position in np.flatnonzero(test_mask):
        oof[position] = scores.get(keys[position], np.nan)

covered = np.isfinite(oof)
logging.info("Вневыборочных предсказаний: %d из %d", int(covered.sum()), len(keys))

# ── Честная метрика ─────────────────────────────────────────────────────
from sklearn.metrics import average_precision_score, roc_auc_score

y_true = dataset.labels[covered]
y_score = oof[covered]
base_rate = float(y_true.mean())
pr_auc = float(average_precision_score(y_true, y_score))
roc_auc = float(roc_auc_score(y_true, y_score))

logging.info("── Вневыборочно ──")
logging.info("PR-AUC   %.3f  (базовая частота %.3f, выигрыш ×%.1f)", pr_auc, base_rate, pr_auc / base_rate)
logging.info("ROC-AUC  %.3f", roc_auc)

# ── Итоговая модель на всей выборке: она пойдёт в следующий прогон ──────
logging.info("Обучение итоговой модели на всей выборке")
final = train(dataset, settings.model, progress=False)
models = settings.paths.resolve("models")
models.mkdir(parents=True, exist_ok=True)
final.save(models / "siamese.pt")
logging.info("Модель сохранена: %s", models / "siamese.pt")

# ── Перенос вневыборочных вероятностей на объекты карты ─────────────────
frame = pd.DataFrame({"chip_key": keys, "probability": oof, "group": groups})
frame = frame.dropna(subset=["probability"])
# У объекта несколько кусков — берём максимум: свалка на одной половине
# остаётся свалкой, даже если вторая половина попала на чистое поле.
by_object = frame.groupby("group", as_index=False)["probability"].max()

web_path = Path("web-next/public/data/candidates.geojson")
published = gpd.read_file(web_path)
published = published.drop(columns=["probability"], errors="ignore").merge(
    by_object.rename(columns={"group": "candidate_id"}), on="candidate_id", how="left"
)
filled = int(published["probability"].notna().sum())
logging.info("Вероятность проставлена %d объектам из %d", filled, len(published))
if filled:
    values = published["probability"].dropna()
    logging.info(
        "Разброс: %.2f — %.2f, медиана %.2f",
        values.min(), values.max(), values.median(),
    )
published.to_file(web_path, driver="GeoJSON")

metrics_path = Path("web-next/public/data/model.json")
metrics_path.write_text(
    json.dumps(
        {
            "kind": "siamese",
            "supervision": "weak",
            "folds": FOLDS,
            "n_pieces": int(covered.sum()),
            "n_positive": int(y_true.sum()),
            "base_rate": round(base_rate, 4),
            "pr_auc_oof": round(pr_auc, 4),
            "roc_auc_oof": round(roc_auc, 4),
            "lift": round(pr_auc / base_rate, 2),
            "positives_from": payload.get("positives_from", ""),
            "negatives_from": payload.get("negatives_from", ""),
        },
        ensure_ascii=False,
        indent=1,
    ),
    encoding="utf-8",
)
logging.info("Метрики модели: %s", metrics_path)
