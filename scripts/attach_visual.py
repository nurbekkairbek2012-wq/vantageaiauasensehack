"""Перенести разметку глазами на объекты карты.

── Зачем это отдельным шагом ───────────────────────────────────────────

Метки хранятся геометрией (``labels_manual.geojson``), а не по номеру
объекта. Номера живут ровно до следующего прогона: детектор пересчитает
область, нумерация сдвинется, и разметка, привязанная к ним, пропадёт
вместе с прогоном — а она стоит часов человеческого времени.

Здесь метки сопоставляются с объектами по месту. После любого пересчёта
достаточно запустить этот шаг заново.

── Что даёт визуальная проверка ────────────────────────────────────────

Больше, чем любая модель. Модель обучена на слабой разметке и уверенно
ошибается: складу под синей кровлей она ставит 0,97. Человек, посмотревший
на тот же склад в 0,6 м на пиксель, ошибиться не может.

Тридцать объектов проверяются за час. На защите это разница между «модель
считает» и «мы посмотрели каждый».

    python scripts/attach_visual.py
"""

import sys
from pathlib import Path

import geopandas as gpd

LABELS = Path("labels_manual.geojson")
TARGETS = [
    Path("web-next/public/data/candidates.geojson"),
    Path("outputs_real/candidates.geojson"),
]

#: Как вердикт называется в данных. Латиницей, потому что это значение
#: поля, а не текст для человека: текст живёт во фронтенде и меняется без
#: пересчёта данных.
CODES = {"свалка": "landfill", "не свалка": "not_landfill", "не понятно": "unclear"}


def main() -> int:
    if not LABELS.exists():
        print(f"нет файла разметки {LABELS}")
        return 1

    labels = gpd.read_file(LABELS)
    labels = labels[labels["verdict"].isin(CODES)]
    print(f"меток: {len(labels)}")

    for target in TARGETS:
        if not target.exists():
            print(f"пропущено, нет файла: {target}")
            continue

        objects = gpd.read_file(target)
        points = labels.to_crs(objects.crs)

        joined = gpd.sjoin(objects, points[["verdict", "geometry"]], predicate="contains", how="left")
        # Один объект может накрыть несколько меток — берём первую; такое
        # бывает только у крупных, и вердикт у них один и тот же.
        joined = joined[~joined.index.duplicated(keep="first")]

        objects = objects.drop(columns=["visual_check"], errors="ignore")
        objects["visual_check"] = (
            joined["verdict"].map(CODES).reindex(objects.index).astype(object)
        )
        marked = objects["visual_check"].notna().sum()

        objects.to_file(target, driver="GeoJSON")
        counts = objects["visual_check"].value_counts(dropna=False).to_dict()
        print(f"{target}: размечено {marked} из {len(objects)} — {counts}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
