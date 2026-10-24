"""Собрать обучающую выборку из независимых свидетельств.

── Зачем это вообще понадобилось ───────────────────────────────────────

Сеть не обучалась ни разу, и причина не в коде обучения — он готов и с
проверкой, и с ранней остановкой. Причина в том, что положительных
примеров неоткуда было взять. Попытка размечать по OpenStreetMap провалилась
по построению: внутри существующего полигона ТБО детектор изменений ничего
не находит, там и в 2018 году была голая поверхность. Нет разрыва — нет
кандидата — нечему ставить метку «свалка». На кольце автоматически
разметилось 16 объектов из 429, и все шестнадцать отрицательные.

Ручная разметка остаётся лучшим источником и никуда не девается: страница
`label.html` для этого и написана. Но она требует человека и часа времени,
а прогон уже накопил два независимых свидетельства, которые до сих пор
лежали без дела.

── Откуда берутся метки ────────────────────────────────────────────────

ОТРИЦАТЕЛЬНЫЕ — из OpenStreetMap. Контекстный отсев отбросил 124 объекта
за совпадение с карьером, стройкой, промзоной или дорожным полотном. Это
не «ничего не найдено»: там произошло настоящее изменение поверхности,
просто законное. Именно такие примеры и нужны — не пустое поле, а трудный
отрицательный, который похож на свалку всем, кроме сути.

ПОЛОЖИТЕЛЬНЫЕ — из доверификации. Объект берётся, если он прошёл
контекстный отсев И подтверждён двумя независимыми источниками снимков в
0,75 м на пиксель. Подтверждение считает не детектор: это текстурный
анализ чужого снимка от другого поставщика.

── Чем это не является ─────────────────────────────────────────────────

Это слабая разметка, и называть её ручной нельзя. Положительная часть
частично наследует решение детектора: объект попал в проверку потому, что
детектор его нашёл. Независима здесь только проверка, а не отбор.

Значит, сеть учится не «находить свалки с нуля» — этому её такая выборка
научить не может. Она учится отличать подтверждённое изменение от
законного: свалку от карьера и стройки. Это более узкая задача, чем
кажется по слову «модель», и на карте она подписана именно так.

Файл помечен `source: "evidence"`. Ручная разметка со страницы приходит с
`source: "manual"` и при слиянии имеет приоритет: человек, посмотревший
на снимок, всегда сильнее правила.

    python scripts/labels_from_evidence.py [выходной-файл]
"""

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

import geopandas as gpd
import pandas as pd

from vantage.config import load_settings

OUTPUTS = Path("outputs_real")
TARGET = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("labels_evidence.json")

#: Причина отсева, означающая законное изменение поверхности. Совпадение
#: ищется по подстроке: формулировка живёт в context.py и может уточняться.
TECHNICAL = "технически"
OSM_MATCH = "OSM"

#: Минимум независимых источников снимков для положительной метки. Один
#: источник — это один взгляд; ошибиться одним взглядом легко.
MIN_SOURCES = 2

settings = load_settings()
crs = settings.project.crs_working


def load(name: str) -> gpd.GeoDataFrame:
    path = OUTPUTS / f"{name}.geojson"
    if not path.exists():
        raise SystemExit(f"нет файла {path} — сначала пройдите область прогоном")
    return gpd.read_file(path).to_crs(crs)


rejected = load("rejected")
kept = load("candidates")

# Подтверждение доезжает до объектов на этапе выгрузки сайта, а не прогона,
# поэтому источник здесь — то, что реально лежит на карте.
web = Path("web-next/public/data/candidates.geojson")
if web.exists():
    published = gpd.read_file(web).to_crs(crs)
    columns = [c for c in ("candidate_id", "verify_confirmed", "n_agreeing") if c in published.columns]
    kept = kept.merge(published[columns], on="candidate_id", how="left", suffixes=("", "_web"))

# ── Отрицательные ───────────────────────────────────────────────────────
reasons = rejected.get("reject_reason", pd.Series(dtype=object)).fillna("")
is_technical = reasons.str.contains(OSM_MATCH, case=False, na=False) | reasons.str.contains(
    TECHNICAL, case=False, na=False
)
negatives = rejected[is_technical]
logging.info("Отрицательных из OSM: %d", len(negatives))

# ── Положительные ───────────────────────────────────────────────────────
if "verify_confirmed" in kept.columns:
    confirmed = kept["verify_confirmed"].fillna(False).astype(bool)
else:
    confirmed = pd.Series(False, index=kept.index)
    logging.warning("Поля verify_confirmed нет: доверификация в прогоне не выполнялась")

positives = kept[confirmed]
logging.info("Положительных, подтверждённых двумя источниками: %d", len(positives))

if positives.empty:
    raise SystemExit(
        "положительных нет. Доверификация либо не запускалась, либо ничего не "
        "подтвердила — обучать не на чем. Размечайте руками на label.html."
    )

# ── Перенос на куски ────────────────────────────────────────────────────
# Метки нужны кускам, а не склеенным объектам: чипы нарезаны до склейки, и
# ключ у них плиточный. Объект, разрезанный границей плитки, отдаёт метку
# обеим своим половинам — обе показывают одно и то же место.
pieces: list[gpd.GeoDataFrame] = []
for path in sorted((OUTPUTS / "tiles").glob("*.geojson")):
    layer = gpd.read_file(path)
    if layer.empty:
        continue
    layer = layer.to_crs(crs)
    layer["chip_key"] = path.stem + ":" + layer["candidate_id"].astype(str)
    pieces.append(layer[["chip_key", "geometry"]])

if not pieces:
    raise SystemExit("плиточных результатов нет — обучать не на чем")

all_pieces = gpd.GeoDataFrame(pd.concat(pieces, ignore_index=True), crs=crs)
logging.info("Кусков всего: %d", len(all_pieces))


def keys_touching(target: gpd.GeoDataFrame) -> set[str]:
    """Куски, пересекающиеся с размеченными объектами."""
    if target.empty:
        return set()
    joined = gpd.sjoin(all_pieces, target[["geometry"]], predicate="intersects", how="inner")
    return set(joined["chip_key"])


positive_keys = keys_touching(positives)
negative_keys = keys_touching(negatives)

# Кусок, попавший в оба множества, не годится ни в одно: он лежит на
# границе законного объекта и подтверждённого, и любая метка на нём — ложь.
both = positive_keys & negative_keys
if both:
    logging.info("Спорных кусков отброшено: %d", len(both))
positive_keys -= both
negative_keys -= both

labels = {key: "landfill" for key in sorted(positive_keys)}
labels.update({key: "not" for key in sorted(negative_keys)})

logging.info(
    "Итого кусков: свалка %d, не свалка %d",
    len(positive_keys), len(negative_keys),
)

if len(positive_keys) < 5 or len(negative_keys) < 5:
    logging.warning(
        "Меньше пяти примеров в классе — обучение откажется идти, и правильно "
        "сделает: на трёх объектах получается красивая метрика и бесполезная модель."
    )

TARGET.write_text(
    json.dumps(
        {
            "labelled_at": pd.Timestamp.now().isoformat(timespec="seconds"),
            "source": "evidence",
            "positives_from": f"доверификация, ≥{MIN_SOURCES} независимых источника 0,75 м/пиксель",
            "negatives_from": "OpenStreetMap: карьеры, стройки, промзоны, дорожное полотно",
            "labels": labels,
        },
        ensure_ascii=False,
        indent=1,
    ),
    encoding="utf-8",
)
logging.info("Записано: %s", TARGET)
logging.info("Дальше: python scripts/train_from_labels.py %s", TARGET)
