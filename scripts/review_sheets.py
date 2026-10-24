"""Собрать контактные листы кандидатов для проверки глазами.

── Зачем ───────────────────────────────────────────────────────────────

Проверка по снимку высокого разрешения — единственное, что отделяет
находку детектора от свалки. Открывать полсотни объектов по одному в
браузере никто не станет, и потому это не делается.

Здесь снимки складываются в листы по девять с подписью на каждом: номер,
площадь, дата, сколько признаков согласны. Лист просматривается за минуту,
и решение по каждому объекту принимается на одном экране.

── Почему снимок, а не чип ─────────────────────────────────────────────

Чипы для разметки собраны из Sentinel-2 — десять метров на пиксель. На
таком разрешении куча мусора и площадка склада выглядят одинаково серым
пятном, и разметчик честно отвечает «не понятно»: из 71 метки таких
двенадцать.

Здесь берутся тайлы поставщиков снимков высокого разрешения — около
полуметра на пиксель. Разница решающая: на полуметре видно колеи от
самосвалов, гребни ссыпанного грунта и белые вкрапления мусора.

── Про порядок ─────────────────────────────────────────────────────────

Объекты идут по убыванию площади. Крупные решают больше: в Алматы один
объект в 13,7 га давал 85% всей суммы ущерба, и ошибка в нём стоила
дороже, чем во всех остальных вместе.

    python scripts/review_sheets.py [--outputs outputs_real] [--per-sheet 9]
"""

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("review")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CACHE = Path("data/highres")

#: Сторона одной врезки в листе. 420 пикселей — предел, при котором на
#: листе три в ряд ещё различимы колеи самосвала.
TILE = 420

#: Зум тайлов. 18-й даёт около полуметра на пиксель и окно примерно
#: 450 м при сетке 3x3 — объект плюс контекст, по которому и опознают.
ZOOM = 18


def picture(lat: float, lon: float, name: str, cfg, refresh: bool = False):
    """Снимок высокого разрешения вокруг точки, из кэша или из сети."""
    from PIL import Image

    from vantage.verify import PROVIDERS, fetch_tile_grid

    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{name}.png"
    if path.exists() and not refresh:
        return Image.open(path).convert("RGB")

    for key in cfg.providers:
        provider = PROVIDERS.get(key)
        if provider is None:
            continue
        try:
            grid = fetch_tile_grid(provider, lat, lon, ZOOM, cfg.tile_grid, timeout=cfg.timeout_s)
        except Exception:
            continue
        image = Image.fromarray(grid.astype("uint8"))
        image.save(path)
        return image
    return None


def main() -> int:
    import geopandas as gpd
    from PIL import Image, ImageDraw

    from vantage import env
    from vantage.config import load_settings

    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", default="outputs_real")
    parser.add_argument("--per-sheet", type=int, default=9)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--out", default="review")
    args = parser.parse_args()

    env.configure()
    cfg = load_settings().verify

    source = Path(args.outputs) / "candidates.geojson"
    if not source.exists():
        log.error("нет %s", source)
        return 1

    data = gpd.read_file(source).to_crs(4326)
    data = data.sort_values("area_m2", ascending=False).reset_index(drop=True)
    log.info("объектов к проверке: %d", len(data))

    out = Path(args.out)
    out.mkdir(exist_ok=True)

    cells: list[tuple[str, "Image.Image"]] = []
    for row in data.itertuples():
        point = row.geometry.centroid
        image = picture(point.y, point.x, str(row.candidate_id), cfg, args.refresh)
        if image is None:
            log.warning("   %s: снимок не получен", row.candidate_id)
            continue

        frame = image.resize((TILE, TILE))
        draw = ImageDraw.Draw(frame)

        # Прицел по центру рисуется разрывом, а не сплошным крестом:
        # сплошной закрывает ровно то место, которое надо рассмотреть.
        c = TILE // 2
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            draw.line([(c + dx * 14, c + dy * 14), (c + dx * 30, c + dy * 30)],
                      fill=(255, 70, 70), width=3)

        # Подпись на тёмной плашке — поверх светлого снимка белый текст
        # не читается, а поверх тёмного не читается чёрный.
        label = (f"{row.candidate_id}  {row.area_m2:.0f} m2  "
                 f"{str(row.break_date)[:7]}  признаков {getattr(row, 'n_agreeing', '?')}")
        draw.rectangle([(0, TILE - 26), (TILE, TILE)], fill=(12, 9, 20))
        draw.text((8, TILE - 19), label, fill=(235, 230, 250))
        cells.append((str(row.candidate_id), frame))

    per = args.per_sheet
    side = int(per ** 0.5)
    made = 0
    for start in range(0, len(cells), per):
        chunk = cells[start:start + per]
        sheet = Image.new("RGB", (TILE * side, TILE * side), (12, 9, 20))
        for i, (_cid, frame) in enumerate(chunk):
            sheet.paste(frame, ((i % side) * TILE, (i // side) * TILE))
        name = out / f"sheet{made + 1:02d}.png"
        sheet.save(name)
        log.info("лист %s: %s", name.name, ", ".join(c for c, _ in chunk))
        made += 1

    log.info("")
    log.info("── Готово: %d листов в %s ──", made, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
