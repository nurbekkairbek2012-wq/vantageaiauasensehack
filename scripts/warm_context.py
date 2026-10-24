"""Набрать контекстные слои области в кэш — по одному, не спеша.

── Зачем ───────────────────────────────────────────────────────────────

Overpass ограничивает частоту по адресу, и превышение продлевает
блокировку: повторы, которые кажутся настойчивостью, на деле держат
запрет открытым. Ночью 23 августа досчёт восточного пояса не прошёл шесть
кругов подряд именно поэтому — не потому, что сервис лежал, а потому что
мы его долбили.

Здесь четыре запроса делаются по одному, с минутными паузами между ними,
и результат ложится в тот же дисковый кэш, из которого читает прогон.
После этого досчёту не нужно ходить в сеть вовсе.

    python scripts/warm_context.py astana_east
"""

import sys
import time
from pathlib import Path

# Вывод в UTF-8: рамки и кириллица не влезают в консольную cp1251, и
# скрипт падал на последней строке отчёта — уже ПОСЛЕ того, как всю
# работу сделал.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    import yaml

    from vantage import env
    from vantage.aoi import AOI
    from vantage.config import load_settings
    from vantage.context import (
        OverpassClient,
        build_exclusion_query,
        build_implausible_query,
        build_roads_query,
        build_settlements_query,
    )

    if len(sys.argv) < 2:
        print("укажите область из config/cities.yaml")
        return 1

    env.configure()
    settings = load_settings()
    cities = yaml.safe_load(Path("config/cities.yaml").read_text(encoding="utf-8"))["cities"]
    found = next((c for c in cities if c["id"] == sys.argv[1]), None)
    if found is None:
        print(f"нет области {sys.argv[1]!r}")
        return 1

    aoi = AOI.from_bbox(tuple(found["bbox"]), name=found["id"],
                        crs_working=settings.project.crs_working)
    client = OverpassClient(settings.paths.resolve("data_cache"))

    queries = [
        ("исключения", build_exclusion_query(aoi, settings.context)),
        ("дороги", build_roads_query(aoi)),
        ("населённые пункты", build_settlements_query(aoi)),
        ("охраняемые земли", build_implausible_query(aoi)),
    ]

    ok = 0
    for i, (name, query) in enumerate(queries):
        for attempt in range(5):
            try:
                data = client.query(query, use_cache=True)
                print(f"{name:22s} {len(data.get('elements', [])):6d} элементов")
                ok += 1
                break
            except Exception as error:
                wait = 90 * (attempt + 1)
                print(f"{name:22s} попытка {attempt + 1}: {str(error)[:60]}")
                if attempt < 4:
                    print(f"{'':22s} жду {wait} с")
                    time.sleep(wait)
        # Пауза между разными запросами обязательна: Overpass считает
        # слоты, и два запроса подряд занимают оба.
        if i + 1 < len(queries):
            time.sleep(60)

    print()
    print(f"── набрано слоёв: {ok} из {len(queries)} ──")
    return 0 if ok == len(queries) else 1


if __name__ == "__main__":
    sys.exit(main())
