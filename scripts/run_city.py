"""Прогон по любому городу из config/cities.yaml.

── Зачем отдельно от run_ring ──────────────────────────────────────────

run_ring.py прибит к Астане: область в константе, выгрузка в outputs_real.
Это было верно, пока город был один. Городов теперь три, переключатель на
карте их показывает, и у двух из них ноль объектов — не потому, что там
чисто, а потому, что прогон по ним не запускали.

── Почему в отдельную папку ────────────────────────────────────────────

Прогон нового города НЕ должен трогать outputs_real. Там лежит
единственный посчитанный результат, на который смотрит сайт, и потерять
его накануне сдачи из-за незамеченной ошибки в новом городе — цена, которую
незачем платить. Каждый город считается к себе, а слияние делается
отдельным осознанным шагом.

    python scripts/run_city.py almaty
    python scripts/run_city.py shymkent --tile 5000
"""

import argparse
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("run_city")

#: Сторона плитки. Пять километров — компромисс, найденный на Астане:
#: крупнее не помещается в память при восьми годах истории, мельче — и
#: накладные расходы на запрос каталога начинают преобладать над счётом.
TILE_M = 5_000


def main() -> int:
    import yaml

    from vantage.aoi import AOI
    from vantage.chips import build_chips
    from vantage.config import load_settings
    from vantage.pipeline import Pipeline

    parser = argparse.ArgumentParser()
    parser.add_argument("city", help="идентификатор из config/cities.yaml")
    parser.add_argument("--tile", type=int, default=TILE_M)
    parser.add_argument("--outputs", default=None, help="куда писать (по умолчанию outputs_<city>)")
    args = parser.parse_args()

    cities = yaml.safe_load(Path("config/cities.yaml").read_text(encoding="utf-8"))["cities"]
    found = next((c for c in cities if c["id"] == args.city), None)
    if found is None:
        log.error("нет города %r; есть: %s", args.city, ", ".join(c["id"] for c in cities))
        return 1

    settings = load_settings()
    aoi = AOI.from_bbox(tuple(found["bbox"]), name=found["id"],
                        crs_working=settings.project.crs_working)
    outputs = Path(args.outputs or f"outputs_{found['id']}")
    outputs.mkdir(parents=True, exist_ok=True)
    log.info("%s: %.0f км², плитка %d м, выгрузка в %s",
             found["name"], aoi.area_km2, args.tile, outputs)

    pipeline = Pipeline(settings, outputs=outputs)
    chip_dir = Path("data/chips")
    chip_dir.mkdir(parents=True, exist_ok=True)

    def cut_chips(tile, candidates, cube, grid, dates):
        """Нарезать пары «до / после» для кандидатов этой плитки."""
        if candidates.empty:
            return candidates
        try:
            dataset = build_chips(cube, candidates, grid, settings.chips)
        except Exception as error:
            log.warning("%s: чипы не нарезаны (%s)", tile.name, error)
            return candidates
        # Идентификаторы уникальны только внутри плитки: без префикса чипы
        # разных плиток — и тем более разных городов — затрут друг друга.
        dataset.candidate_ids = [f"{tile.name}:{cid}" for cid in dataset.candidate_ids]
        dataset.save(chip_dir / f"{tile.name}.npz")
        return candidates

    started = time.time()
    merged = pipeline.run_tiles(
        tile_size_m=args.tile,
        aoi=aoi,
        on_tile=cut_chips,
        keep_bands=True,
    )
    log.info("Прогон завершён за %.0f мин, сырых кандидатов %d",
             (time.time() - started) / 60, len(merged))

    target = outputs / "candidates_raw.geojson"
    if merged.empty:
        # Пустой результат — тоже результат, и его надо сохранить: иначе
        # следующий запуск не отличит «посчитали и ничего нет» от «не
        # считали».
        log.warning("кандидатов не найдено — сохраняю пустой файл")
    merged.to_file(target, driver="GeoJSON")
    log.info("Сохранено: %s", target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
