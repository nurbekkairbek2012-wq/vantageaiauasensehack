"""Вторая половина прогона по кольцу: от сырых кандидатов до файлов карты.

Первая половина (scripts/run_ring.py) прошла плитками по снимкам. Здесь всё,
что считается уже по найденному: разметка из OSM, обучение сети, отклик
полимеров из чипов, радар и тепло, контекстный отсев, доверификация, деньги,
риск и выгрузка.

Запускается отдельно и сколько угодно раз: снимки заново не тянутся, и на
проваленной ветке (недоступный Overpass, лимит тайлов) прогон не встаёт —
каждая ветка падает независимо и оставляет свою колонку пустой.
"""

import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

import geopandas as gpd

from vantage.aoi import AOI
from vantage.config import load_economics, load_settings
from vantage.orchestrate import finish_run
from vantage.pipeline import Pipeline

RING_BBOX = (71.37, 51.12, 71.66, 51.30)
OUTPUTS = Path("outputs_real")

settings = load_settings()
ring = AOI.from_bbox(RING_BBOX, name="astana_north", crs_working=settings.project.crs_working)
pipeline = Pipeline(settings, load_economics(), outputs=OUTPUTS)

merged = gpd.read_file(OUTPUTS / "candidates_raw.geojson").to_crs(settings.project.crs_working)
logging.info("Сырых объектов: %d", len(merged))

outcome = finish_run(
    pipeline,
    merged,
    aoi=ring,
    chips_prefix="astana_north_",
    with_model=True,
    with_signals="--no-signals" not in sys.argv,
    with_verify="--no-verify" not in sys.argv,
    with_risk=True,
)

logging.info("ИТОГ: %s", outcome.to_text())
logging.info("Отсев: %s", outcome.rejection)
logging.info("Метки: %s", outcome.labels)
for name, path in outcome.artifacts.items():
    logging.info("  %s: %s", name, path)
