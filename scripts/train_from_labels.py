"""Обучить сиамскую сеть на ручной разметке.

Замыкает цикл, который до сих пор был разорван: страница `label.html`
собирает метки глазами, этот скрипт превращает их в модель, а прогон
подставляет вероятность вместо согласия признаков.

Почему без ручной разметки не обойтись. Положительные примеры пытались
брать из OpenStreetMap автоматически, но внутри существующего полигона ТБО
детектор изменений ничего не находит: там и в 2018 году была голая
поверхность. На кольце 20×20 км автоматически разметилось 17 объектов из
607, и все семнадцать — отрицательные.

    python scripts/train_from_labels.py labels.json
"""

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

import numpy as np

from vantage.config import load_settings
from vantage.orchestrate import load_chips

MIN_PER_CLASS = 5

if len(sys.argv) < 2:
    raise SystemExit("укажите файл разметки: python scripts/train_from_labels.py labels.json")

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
raw = payload.get("labels", payload)

# «Не понятно» — это отказ от ответа, а не третий класс. Такие примеры в
# обучение не идут: учить сеть на том, чего человек не смог различить,
# значит учить её шуму.
verdicts = {"landfill": 1, "not": 0}
labels = {cid: verdicts[v] for cid, v in raw.items() if v in verdicts}
skipped = len(raw) - len(labels)

positives = sum(labels.values())
negatives = len(labels) - positives
logging.info(
    "Разметка: %d годных (свалка %d, не свалка %d), «не понятно» пропущено %d",
    len(labels), positives, negatives, skipped,
)

if positives < MIN_PER_CLASS or negatives < MIN_PER_CLASS:
    raise SystemExit(
        f"мало примеров: нужно хотя бы по {MIN_PER_CLASS} каждого класса. "
        "Разметьте ещё на странице label.html — обучение на трёх объектах "
        "даст красивую метрику и бесполезную модель."
    )

settings = load_settings()
chips = load_chips(Path("data/chips"))
if chips is None:
    raise SystemExit("чипов нет — сначала пройдите область плитками")

index = [i for i, cid in enumerate(chips.candidate_ids) if cid in labels]
if not index:
    raise SystemExit("метки и чипы не сопоставились: идентификаторы из разных прогонов")

training = chips.subset(np.array(index))
training.labels = np.array([labels[chips.candidate_ids[i]] for i in index], dtype="int64")
logging.info("Обучающих пар: %d", len(training))

from vantage.model.infer import attach_to_candidates, predict
from vantage.model.train import train

trained = train(training, settings.model, progress=True)

models = settings.paths.resolve("models")
models.mkdir(parents=True, exist_ok=True)
target = models / "siamese_manual.pt"
trained.save(target)
logging.info("Модель сохранена: %s", target)

history = trained.history.as_dict()
logging.info("Качество: %s", {k: v for k, v in history.items() if not isinstance(v, list)})

# Применяем ко всем кускам и переносим на объекты карты.
prediction = predict(trained, chips)
logging.info(
    "Применено к %d кускам, выше порога %.2f: %d",
    len(prediction), trained.threshold, prediction.n_positive,
)

import geopandas as gpd

from vantage.orchestrate import load_tile_pieces, transfer_to_merged
from vantage.pipeline import Pipeline

pipeline = Pipeline(settings, outputs="outputs_real")
pieces = load_tile_pieces(pipeline)
pieces = attach_to_candidates(pieces.assign(candidate_id=pieces["chip_key"]), prediction)

merged = gpd.read_file("outputs_real/candidates.geojson").to_crs(settings.project.crs_working)
merged = merged.drop(columns=[c for c in ("probability",) if c in merged.columns])
merged = transfer_to_merged(merged, pieces, columns=("probability",))
merged.to_crs(settings.project.crs_output).to_file(
    "outputs_real/candidates.geojson", driver="GeoJSON"
)
logging.info(
    "Вероятность проставлена %d объектам из %d",
    int(merged["probability"].notna().sum()), len(merged),
)
logging.info("Дальше: vantage web — карта покажет уверенность модели вместо согласия признаков")
