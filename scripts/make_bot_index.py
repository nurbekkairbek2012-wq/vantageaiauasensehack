"""Собрать лёгкий указатель объектов для бота на Vercel.

Функция бота живёт в бессерверном окружении, где нет ни geopandas, ни
shapely и где холодный старт считается миллисекундами. Читать там
GeoJSON с полигонами незачем: боту нужна одна операция — найти ближайшую
точку к присланной координате.

Поэтому здесь geometry сводится к центру, а из полей остаётся то, что
бот реально показывает жителю. Файл получается на два порядка легче
исходного и грузится без единой зависимости.

    python scripts/make_bot_index.py
"""

import json
import sys
from pathlib import Path

SOURCE = Path("web-next/public/data/candidates.geojson")
TARGET = Path("api/candidates.json")


def main() -> int:
    if not SOURCE.exists():
        print(f"нет файла {SOURCE}")
        return 1

    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    items = []

    for feature in data.get("features", []):
        geometry = feature.get("geometry") or {}
        rings = (
            geometry.get("coordinates", [[]])[0]
            if geometry.get("type") == "Polygon"
            else geometry.get("coordinates", [[[]]])[0][0]
            if geometry.get("type") == "MultiPolygon"
            else []
        )
        if not rings:
            continue

        lon = sum(point[0] for point in rings) / len(rings)
        lat = sum(point[1] for point in rings) / len(rings)
        properties = feature.get("properties") or {}

        items.append(
            {
                "id": str(properties.get("candidate_id", "")),
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "area_m2": int(properties.get("area_m2") or 0),
                "break_date": str(properties.get("break_date") or "")[:10],
                "damage_p50": int(properties.get("damage_p50") or 0),
                # Проверка глазами важнее модели: жителю честнее сказать
                # «объект подтверждён», чем «модель считает 0,9».
                "visual_check": properties.get("visual_check"),
            }
        )

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    size = TARGET.stat().st_size
    print(f"объектов: {len(items)}, файл: {TARGET} ({size // 1024 or 1} КБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
