"""Вырезать обучающие примеры из дронового датасета незаконных свалок.

── Что за датасет ──────────────────────────────────────────────────────

INS-IntelligentNetworkSolutions/Waste-Dumpsites-DroneImagery, лицензия
CC-BY-4.0: 2 115 снимков с дрона, на каждом рамки вокруг мусора. Это
именно НЕЗАКОННЫЕ свалки, а не официальные полигоны ТБО — в отличие от
OpenStreetMap, где размечены в основном законные объекты.

── Почему отрицательные берутся из тех же кадров ───────────────────────

Обычная ошибка при сборе таких наборов — брать отрицательные откуда
попало. Тогда модель выучивает не мусор, а различие условий съёмки:
другое солнце, другая камера, другой сезон. На своих данных она покажет
прекрасную метрику и рассыплется на чужих.

Здесь отрицательный кусок вырезается из ТОГО ЖЕ снимка, подальше от всех
рамок. Камера, высота, освещение, грунт и время — одинаковые. Разница
ровно одна: мусор есть или мусора нет. Это самая честная пара, какую
можно получить.

── Почему картинки уменьшаются ─────────────────────────────────────────

Дрон снимает примерно с десяти сантиметров на пиксель, наши проверочные
тайлы — с полуметра. Разница в пять раз: модель, обученная на дроновой
детализации, на спутнике увидит другой предмет — ровно та ошибка, из-за
которой первая проверка переноса AerialWaste считалась по чипам
Sentinel-2 и ничего не значила.

Поэтому каждый кусок сначала уменьшается до масштаба спутникового тайла,
и лишь потом приводится к входу сети. Детали, которых на спутнике не
будет, теряются намеренно.

    python scripts/extract_drone.py [--out data/drone_crops]
"""

import argparse
import io
import logging
import random
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("drone")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SHARDS = Path("data/drone")

#: Во сколько раз кусок шире рамки. Мусор без окружения не опознаётся:
#: важно, что рядом колея, обочина или край поля, а не только сама куча.
CONTEXT = 3.0

#: Во сколько раз уменьшить, чтобы приблизиться к спутниковому масштабу.
#: Дрон даёт около 0,1 м на пиксель, наши тайлы — около 0,5 м.
DOWNSCALE = 5

#: Сторона сохраняемого куска. 256 — как в наборе по Казахстану, чтобы
#: обучать на них вместе без пересчёта.
SIDE = 256

#: Минимальная сторона рамки в пикселях. Совсем мелкие после уменьшения
#: в пять раз превращаются в три пикселя и учат шуму.
MIN_BOX_PX = 40


def crop_square(image, cx: float, cy: float, size: float):
    """Квадрат вокруг точки, прижатый к границам кадра."""
    half = size / 2
    left = max(0, min(image.width - size, cx - half))
    top = max(0, min(image.height - size, cy - half))
    return image.crop((int(left), int(top), int(left + size), int(top + size)))


def to_satellite_scale(patch):
    """Уменьшить до спутниковой детализации и вернуть к входу сети."""
    small = max(16, int(patch.width / DOWNSCALE))
    return patch.resize((small, small)).resize((SIDE, SIDE))


def main() -> int:
    import pyarrow.parquet as pq
    from PIL import Image

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/drone_crops")
    args = parser.parse_args()

    shards = sorted(SHARDS.glob("*.parquet"))
    if not shards:
        log.error("нет файлов в %s", SHARDS)
        return 1

    out = Path(args.out)
    (out / "waste").mkdir(parents=True, exist_ok=True)
    (out / "clean").mkdir(parents=True, exist_ok=True)

    rng = random.Random(0)
    positives = negatives = skipped = 0

    for shard in shards:
        table = pq.read_table(shard)
        log.info("%s: строк %d", shard.name, table.num_rows)
        rows = table.to_pylist()

        # Рамки группируются по снимку: у одного кадра их бывает несколько,
        # и отрицательный кусок должен обходить ВСЕ, а не только текущую.
        by_image: dict[int, list] = {}
        for row in rows:
            by_image.setdefault(row["image_id"], []).append(row)

        for image_id, group in by_image.items():
            blob = group[0]["image"]
            data = blob["bytes"] if isinstance(blob, dict) else blob
            try:
                image = Image.open(io.BytesIO(data)).convert("RGB")
            except Exception:
                skipped += 1
                continue

            boxes = []
            for row in group:
                box = row.get("bounding_box") or []
                if len(box) != 4:
                    continue
                x, y, w, h = (float(v) for v in box)
                if min(w, h) < MIN_BOX_PX:
                    continue
                boxes.append((x, y, w, h))

            if not boxes:
                skipped += 1
                continue

            # Не больше трёх положительных с кадра: на плотных снимках их
            # бывает по десятку, и один кадр начинает весить в наборе как
            # десять разных мест. Модель тогда учит этот конкретный пейзаж.
            for i, (x, y, w, h) in enumerate(boxes[:3]):
                size = max(w, h) * CONTEXT
                patch = crop_square(image, x + w / 2, y + h / 2, size)
                to_satellite_scale(patch).save(out / "waste" / f"{shard.stem}_{image_id}_{i}.png")
                positives += 1

            # Чистый кусок из того же кадра: тот же размер, подальше от
            # всех рамок. Двадцать попыток — дальше кадр считается слишком
            # плотно занятым, и отрицательного из него не выйдет.
            size = max(max(w, h) for _x, _y, w, h in boxes) * CONTEXT
            want = min(3, len(boxes[:3]))   # столько же, сколько взяли мусора
            got = 0
            for attempt in range(60):
                if got >= want:
                    break
                cx = rng.uniform(size / 2, max(size / 2 + 1, image.width - size / 2))
                cy = rng.uniform(size / 2, max(size / 2 + 1, image.height - size / 2))
                far = all(
                    abs(cx - (x + w / 2)) > size or abs(cy - (y + h / 2)) > size
                    for x, y, w, h in boxes
                )
                if not far:
                    continue
                patch = crop_square(image, cx, cy, size)
                to_satellite_scale(patch).save(
                    out / "clean" / f"{shard.stem}_{image_id}_{got}.png")
                negatives += 1
                got += 1

    log.info("")
    log.info("── Готово ──")
    log.info("мусор:  %d кусков", positives)
    log.info("чисто:  %d кусков (из тех же кадров)", negatives)
    log.info("пропущено кадров: %d", skipped)
    log.info("папка: %s", out)
    return 0 if positives else 1


if __name__ == "__main__":
    sys.exit(main())
