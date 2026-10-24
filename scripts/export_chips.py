"""Выгрузить пары чипов «до / после» в картинки для ручной разметки.

Почему это нужно. Сеть не обучается, потому что положительных примеров из
OpenStreetMap не набирается в принципе: внутри существующего полигона ТБО
детектор изменений ничего не находит — там и в 2018 году была голая
поверхность. Остаётся один путь — посмотреть глазами. Инструмента для
этого не было, и очередь из шестисот объектов в QGIS никто бы не прошёл.

Что выгружается. Для каждого куска — два PNG, «до» и «после», собранных из
каналов B04/B03/B02 как обычный цветной снимок. Плюс индекс с площадью,
датой разрыва и автоматической меткой из OSM, если она есть: размечать
проще, когда видно, что система уже думает.

Растяжка контраста — по перцентилям и ОБЩАЯ для пары. Если растянуть
каждую картинку по своим минимуму и максимуму, разница между «до» и
«после» исчезнет: обе станут одинаково контрастными, а именно их различие
и надо оценить.

    python scripts/export_chips.py [сколько]
"""

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from vantage.chips import ChipDataset

CHIPS = Path("data/chips")
OUT = Path("web-next/public/chips")
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 200
# Апскейл делает CSS (image-rendering: pixelated), а не файл: 64 px
# картинка весит килобайт, а увеличенная вчетверо — двадцать восемь.
# Двести пар при апскейле в файле дали 5,5 МБ на публичном сайте.
UPSCALE = 1

OUT.mkdir(parents=True, exist_ok=True)
for stale in OUT.glob("*.png"):
    stale.unlink()

files = sorted(CHIPS.glob("*.npz"))
if not files:
    raise SystemExit("чипов нет — сначала пройдите область плитками")

datasets = [ChipDataset.load(f) for f in files]
channels = datasets[0].channels
rgb = [channels.index(band) for band in ("B04", "B03", "B02")]
print(f"файлов: {len(files)}, каналы: {channels}")


def to_png(before: np.ndarray, after: np.ndarray) -> tuple[Image.Image, Image.Image]:
    """Пара картинок с ОБЩЕЙ растяжкой контраста."""
    pair = np.stack([before[rgb], after[rgb]])  # (2, 3, H, W)
    finite = pair[np.isfinite(pair)]
    if finite.size == 0:
        finite = np.array([0.0, 1.0])
    lo, hi = np.percentile(finite, [2, 98])
    if hi - lo < 1e-6:
        hi = lo + 1e-6

    # Пиксели за краем растра заполнены нулём (chips.PAD_VALUE). В картинке
    # они дают чёрную полосу, которую размечающий примет за тёмную
    # поверхность. Красим их в цвет фона страницы: отсутствие кадра должно
    # читаться как отсутствие кадра.
    outside = np.all(pair == 0, axis=1)  # (2, H, W)
    SOOT = (13, 9, 24)

    out = []
    for k, image in enumerate(pair):
        scaled = np.clip((image - lo) / (hi - lo), 0, 1)
        scaled = np.nan_to_num(scaled, nan=0.0)
        arr = (scaled.transpose(1, 2, 0) * 255).astype("uint8")
        arr[outside[k]] = SOOT
        picture = Image.fromarray(arr, mode="RGB")
        if UPSCALE > 1:
            picture = picture.resize(
                (arr.shape[1] * UPSCALE, arr.shape[0] * UPSCALE), Image.NEAREST
            )
        out.append(picture)
    return out[0], out[1]


index = []
written = 0
for dataset in datasets:
    for i, cid in enumerate(dataset.candidate_ids):
        if written >= LIMIT:
            break
        slug = cid.replace(":", "__")
        before, after = to_png(dataset.before[i], dataset.after[i])
        before.save(OUT / f"{slug}-before.png", optimize=True)
        after.save(OUT / f"{slug}-after.png", optimize=True)
        index.append({"id": cid, "slug": slug})
        written += 1
    if written >= LIMIT:
        break

# Атрибуты кусков лежат в плиточных результатах — подтягиваем то, что
# помогает решать: площадь, дату, автоматическую метку.
import geopandas as gpd

attributes: dict[str, dict] = {}
for path in sorted(Path("outputs_real/tiles").glob("*.geojson")):
    layer = gpd.read_file(path)
    if layer.empty:
        continue
    for _, row in layer.iterrows():
        key = f"{path.stem}:{row['candidate_id']}"
        attributes[key] = {
            "area_m2": round(float(row.get("area_m2") or 0)),
            "break_date": str(row.get("break_date") or "")[:10],
            "ndvi_drop": round(float(row.get("ndvi_drop") or 0), 3),
            "bsi_rise": round(float(row.get("bsi_rise") or 0), 3),
        }

for item in index:
    item.update(attributes.get(item["id"], {}))

index.sort(key=lambda item: -(item.get("area_m2") or 0))
(Path("web-next/public/data") / "chips.json").write_text(
    json.dumps({"chips": index, "upscale": UPSCALE}, ensure_ascii=False, indent=1),
    encoding="utf-8",
)

total = sum(f.stat().st_size for f in OUT.glob("*.png"))
print(f"выгружено пар: {written}, вес: {total // 1024} КБ")
print("индекс: web-next/public/data/chips.json")
