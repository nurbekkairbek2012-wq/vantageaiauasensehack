"""Настоящий прогон по кольцу 20x20 км вокруг северной промзоны Астаны.

Область выбрана не наугад: в OSM здесь размечены шесть полигонов ТБО
(107, 79, 43, 42, 26 и 21 га). Стихийные свалки возникают на подъездах к
легальным площадкам, так что вероятность найти настоящие объекты здесь
выше, чем в случайном квадрате степи.

Чипы для сиамской сети режутся прямо в прогоне: загружать те же снимки
второй раз стоило бы столько же, сколько весь прогон.
"""
import logging
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("run_ring")

from vantage.aoi import AOI
from vantage.chips import build_chips
from vantage.config import load_settings
from vantage.pipeline import Pipeline

RING_BBOX = (71.37, 51.12, 71.66, 51.30)
TILE_M = 5_000

settings = load_settings()
ring = AOI.from_bbox(RING_BBOX, name="astana_north", crs_working=settings.project.crs_working)
log.info("Кольцо: %.0f км²", ring.area_km2)

pipeline = Pipeline(settings, outputs="outputs_real")
chip_dir = Path("data/chips")
chip_dir.mkdir(parents=True, exist_ok=True)


def cut_chips(tile, candidates, cube, grid, dates):
    """Нарезать пары «до / после» для кандидатов этой плитки."""
    if candidates.empty:
        return candidates
    try:
        dataset = build_chips(cube, candidates, grid, settings.chips)
    except Exception as exc:
        log.warning("%s: чипы не нарезаны (%s)", tile.name, exc)
        return candidates
    # Идентификаторы кандидатов внутри плитки не уникальны по прогону —
    # префиксуем именем плитки, иначе чипы разных плиток перезатрут друг друга.
    dataset.candidate_ids = [f"{tile.name}:{cid}" for cid in dataset.candidate_ids]
    dataset.save(chip_dir / f"{tile.name}.npz")
    return candidates


started = time.time()
merged = pipeline.run_tiles(
    tile_size_m=TILE_M,
    aoi=ring,
    on_tile=cut_chips,
    keep_bands=True,
)
log.info("Плиточный прогон завершён за %.0f мин, кандидатов %d",
         (time.time() - started) / 60, len(merged))

merged.to_file("outputs_real/candidates_raw.geojson", driver="GeoJSON")
log.info("Сырые кандидаты сохранены: outputs_real/candidates_raw.geojson")
