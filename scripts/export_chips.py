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

── Почему картинки были нечитаемы ─────────────────────────────────────

Первая версия отдавала окно целиком: 64 пикселя при десяти метрах — это
640 метров поля, внутри которого объект в 1800 м² занимает четыре пикселя
из четырёх тысяч. Разметчик видел пейзаж, а не предмет вопроса, и честно
отвечал «не понятно».

Исправлено тремя вещами, и ни одна не добавляет разрешения — его взять
неоткуда, десять метров это физика прибора:

1. Обрезка к центру. Из 640 метров остаётся CROP_PX·10 метров вокруг
   объекта. Контекст сокращается до того, что нужно для суждения, а сам
   объект занимает заметную долю кадра.

2. Растяжка по центральной части, а не по всему окну. Перцентили,
   посчитанные по 640 метрам поля, задавались фоном; локальный контраст
   объекта прижимался к середине шкалы.

3. Увеличение по Ланцошу при записи файла вместо ступенчатого в CSS.
   Ступенчатое честнее — оно показывает ровно пиксели, — но человек не
   умеет читать шахматную доску. Интерполяция не добавляет сведений, она
   делает имеющиеся различимыми.

Разрешение остаётся десятиметровым, и страница разметки говорит об этом
прямо, показывая рядом живой снимок в 0,75 м на пиксель.

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
# Сто двадцать пар — это примерно час работы разметчика. Выгружать
# больше значит возить на сайт мегабайты, которые никто не откроет.
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 120
# Сколько пикселей исходного окна оставить вокруг объекта. 28 — это
# 360 метров: объект в 40 метров занимает девятую часть кадра, и вокруг
# остаётся достаточно, чтобы отличить свалку от распаханного поля.
CROP_PX = 36

# Увеличение при записи. Вчетверо даёт 112 px: на экране картинка чёткая,
# а двести пар при шестикратном весили 11,8 МБ — столько статике возить
# незачем. Остаток увеличения доделывает браузер сглаживанием.
UPSCALE = 4

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


def crop_center(pair: np.ndarray, size: int) -> np.ndarray:
    """Вырезать центральный квадрат: объект лежит ровно в середине окна."""
    height = pair.shape[-2]
    width = pair.shape[-1]
    if size >= min(height, width):
        return pair
    top = (height - size) // 2
    left = (width - size) // 2
    return pair[..., top : top + size, left : left + size]


def to_png(before: np.ndarray, after: np.ndarray) -> tuple[Image.Image, Image.Image]:
    """Пара картинок с общей растяжкой контраста, поканальной.

    Растяжка ОБЩАЯ для пары: растянув каждую картинку по своим минимуму и
    максимуму, мы уравняли бы их контраст и стёрли разницу, которую и надо
    оценить.

    Растяжка ПОКАНАЛЬНАЯ. Одна шкала на три канала кажется честнее, но
    честна она только для датчика, а не для глаза: у Sentinel-2 красный
    канал над голой землёй систематически ярче синего, и общая шкала
    вгоняет всю картинку в оранжевый. Это не свойство местности, это
    отсутствие баланса белого.

    Пиксели за краем растра (chips.PAD_VALUE = 0) в расчёт перцентилей не
    берутся вовсе. Их там оказывалось достаточно, чтобы нижний перцентиль
    садился ровно на ноль, и вся сцена уезжала в пересвет.
    """
    pair = crop_center(np.stack([before[rgb], after[rgb]]), CROP_PX)  # (2, 3, H, W)

    outside = np.all(pair == 0, axis=1)  # (2, H, W) — кадра здесь нет
    SOOT = (13, 9, 24)

    frames = []
    for k in range(2):
        channels_out = []
        for c in range(3):
            band = pair[:, c]
            valid = band[np.isfinite(band) & ~outside]
            if valid.size < 16:
                lo, hi = 0.0, 1.0
            else:
                lo, hi = np.percentile(valid, [2, 98])
            if hi - lo < 1e-6:
                hi = lo + 1e-6
            scaled = np.clip((pair[k, c] - lo) / (hi - lo), 0, 1)
            # Мягкая гамма: линейное отражение отражательной способности
            # оставляет сцену тёмной, тени сливаются в один тон.
            channels_out.append(np.nan_to_num(scaled, nan=0.0) ** 0.75)

        arr = (np.stack(channels_out, axis=-1) * 255).astype("uint8")
        arr[outside[k]] = SOOT
        picture = Image.fromarray(arr, mode="RGB")
        if UPSCALE > 1:
            picture = picture.resize(
                (arr.shape[1] * UPSCALE, arr.shape[0] * UPSCALE), Image.LANCZOS
            )
        frames.append(picture)
    return frames[0], frames[1]


# Атрибуты кусков лежат в плиточных результатах. Читаются ДО отбора: по
# ним и отбирается, что выгружать.
import geopandas as gpd

attributes: dict[str, dict] = {}
for path in sorted(Path("outputs_real/tiles").glob("*.geojson")):
    layer = gpd.read_file(path)
    if layer.empty:
        continue
    # Координаты нужны странице разметки: рядом с парой десятиметровых
    # чипов она показывает то же место на живом снимке в 0,75 м/пиксель.
    # Без высокого разрешения решение «свалка или карьер» принимается
    # наугад, а наугад поставленная метка хуже отсутствующей.
    centers = layer.geometry.to_crs(4326).representative_point()
    for (_, row), point in zip(layer.iterrows(), centers, strict=True):
        key = f"{path.stem}:{row['candidate_id']}"
        attributes[key] = {
            "area_m2": round(float(row.get("area_m2") or 0)),
            "break_date": str(row.get("break_date") or "")[:10],
            "ndvi_drop": round(float(row.get("ndvi_drop") or 0), 3),
            "bsi_rise": round(float(row.get("bsi_rise") or 0), 3),
            "lat": round(float(point.y), 6),
            "lon": round(float(point.x), 6),
        }

# Отбор: САМЫЕ КРУПНЫЕ, а не первые попавшиеся.
#
# Первая версия брала первые LIMIT кусков в порядке файлов и сортировала
# уже выгруженное. Порядок файлов — это порядок плиток, то есть география,
# а не важность: выгружался северо-западный угол области целиком, включая
# куски по 900 м², и ни один из подтверждённых объектов в набор не попадал.
#
# Площадь здесь не про значимость, а про различимость. При десяти метрах на
# пиксель объект в 900 м² — это девять пикселей, по которым человек не
# скажет ничего. Размечать имеет смысл то, что вообще видно.
order = []
for d, dataset in enumerate(datasets):
    for i, cid in enumerate(dataset.candidate_ids):
        order.append((attributes.get(cid, {}).get("area_m2", 0), d, i, cid))
order.sort(key=lambda item: -item[0])

index = []
written = 0
for area, d, i, cid in order[:LIMIT]:
    dataset = datasets[d]
    slug = cid.replace(":", "__")
    before, after = to_png(dataset.before[i], dataset.after[i])
    before.save(OUT / f"{slug}-before.png", optimize=True)
    after.save(OUT / f"{slug}-after.png", optimize=True)
    index.append({"id": cid, "slug": slug})
    written += 1

for item in index:
    item.update(attributes.get(item["id"], {}))

index.sort(key=lambda item: -(item.get("area_m2") or 0))
(Path("web-next/public/data") / "chips.json").write_text(
    json.dumps(
        {
            "chips": index,
            "upscale": UPSCALE,
            "crop_px": CROP_PX,
            # Сторона кадра в метрах: страница обязана назвать масштаб,
            # иначе разметчик не знает, на что смотрит.
            "span_m": CROP_PX * 10,
        },
        ensure_ascii=False,
        indent=1,
    ),
    encoding="utf-8",
)

total = sum(f.stat().st_size for f in OUT.glob("*.png"))
print(f"выгружено пар: {written}, вес: {total // 1024} КБ")
print("индекс: web-next/public/data/chips.json")
