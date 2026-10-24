"""Вторая половина прогона для любого города из config/cities.yaml.

── Почему не одним прогоном на все города ──────────────────────────────

Соблазн слить сырых кандидатов трёх городов в один файл и досчитать разом
выглядит экономно и не работает. Контекстный отсев спрашивает
OpenStreetMap про область прогона: объединяющий прямоугольник Астаны,
Алматы и Шымкента накрывает половину Казахстана, и запрос по нему либо не
вернётся, либо вернёт сотни мегабайт дорог, из которых нужны единицы
процентов. Модель риска обучается по сетке той же области — сетка на
пол-страны при том же числе объектов означает базовую частоту, на которой
не обучится ничего.

Плюс идентификаторы кандидатов сквозные внутри прогона: C00000 есть и в
Астане, и в Алматы, и слияние сырых файлов молча склеило бы разные объекты.

Поэтому каждый город считается у себя целиком, а объединяется только то,
что показывает сайт, — отдельным шагом (scripts/merge_cities.py).

    python scripts/finish_city.py almaty [--no-signals] [--no-verify]
"""

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("finish_city")


def main() -> int:
    import geopandas as gpd
    import yaml

    from vantage.aoi import AOI
    from vantage.config import load_economics, load_settings
    from vantage.orchestrate import finish_run
    from vantage.pipeline import Pipeline

    parser = argparse.ArgumentParser()
    parser.add_argument("city")
    parser.add_argument("--no-signals", action="store_true")
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument("--outputs", default=None)
    args = parser.parse_args()

    cities = yaml.safe_load(Path("config/cities.yaml").read_text(encoding="utf-8"))["cities"]
    found = next((c for c in cities if c["id"] == args.city), None)
    if found is None:
        log.error("нет города %r", args.city)
        return 1

    outputs = Path(args.outputs or f"outputs_{found['id']}")
    raw = outputs / "candidates_raw.geojson"
    if not raw.exists():
        log.error("нет %s — сначала scripts/run_city.py %s", raw, args.city)
        return 1

    settings = load_settings()
    aoi = AOI.from_bbox(tuple(found["bbox"]), name=found["id"],
                        crs_working=settings.project.crs_working)
    pipeline = Pipeline(settings, load_economics(), outputs=outputs)

    merged = gpd.read_file(raw).to_crs(settings.project.crs_working)
    log.info("%s: сырых объектов %d", found["name"], len(merged))
    if merged.empty:
        log.warning("кандидатов нет — досчитывать нечего")
        return 0

    outcome = finish_run(
        pipeline,
        merged,
        aoi=aoi,
        # Префикс чипов совпадает с именем AOI: так их нарезал run_city.py.
        chips_prefix=f"{found['id']}_",
        with_model=True,
        with_signals=not args.no_signals,
        with_verify=not args.no_verify,
        with_risk=True,
    )

    log.info("ИТОГ: %s", outcome.to_text())
    log.info("Отсев: %s", outcome.rejection)
    (outputs / "funnel.json").write_text(
        json.dumps({"raw": len(merged), "rejected": outcome.rejection},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    for name, path in outcome.artifacts.items():
        log.info("  %s: %s", name, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
