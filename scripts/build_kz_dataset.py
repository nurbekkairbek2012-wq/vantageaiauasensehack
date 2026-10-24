"""Собрать обучающий набор по Казахстану из полигонов свалок OpenStreetMap.

── Зачем, если есть AerialWaste ────────────────────────────────────────

AerialWaste снят в Ломбардии. Классификатор на нём даёт ROC-AUC 0,858 на
их данных и 0,643 на наших при интервале 0,333–0,923 — то есть перенос на
Казахстан не доказан. Виновата не модель: у неё другой ландшафт, другое
солнце, другой состав отходов и другой тип застройки вокруг.

Лечится это не подбором порога, а данными из нужного места. Они есть и
они открытые: в OpenStreetMap по Казахстану размечено 776 полигонов
свалок и площадок обращения с отходами.

── Чем этот набор хуже идеального ──────────────────────────────────────

Полигоны OSM — это в основном ЗАКОННЫЕ объекты: городские полигоны ТБО,
крупные и старые. Стихийная свалка мельче, свежее и лежит на обочине.

Значит, модель научится узнавать «место, где лежат отходы», а не
«незаконность». Для нашей задачи этого достаточно: незаконность
определяется не снимком, а отсутствием объекта в реестре, и это уже
делает контекстный отсев.

Второе ограничение честнее назвать прямо: OSM неполон и местами неточен.
Полигон может быть обведён по устаревшему контуру или включать
подъездные дороги. Поэтому положительные примеры берутся из центра
полигона, а не из всей его площади.

── Отрицательные примеры ───────────────────────────────────────────────

Случайная точка Казахстана — это степь, и отличать свалку от степи модель
научится мгновенно и бесполезно. Нужны ТРУДНЫЕ отрицательные: то, что
детектор путает со свалкой на самом деле — промплощадки, карьеры,
стройки, пашня, вода.

Поэтому отрицательные берутся не случайно, а из тех же тегов OSM, по
которым идёт контекстный отсев. Набор получается сбалансированным по
сложности, а не по расстоянию.

    python scripts/build_kz_dataset.py [--limit 400] [--refresh]
"""

import argparse
import hashlib
import json
import logging
import random
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("kz-dataset")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT = Path("data/kz_dataset")
INDEX = OUT / "index.json"

#: Зеркала Overpass. Список тот же, что в vantage.context: публичные
#: инстансы регулярно уходят в перегрузку, и все сразу.
MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)

#: Что считается свалкой. amenity=waste_disposal и man_made=spoil_heap
#: добавлены к landuse=landfill: в Казахстане размечают всеми тремя.
POSITIVE_TAGS = (
    ("landuse", "landfill"),
    ("amenity", "waste_disposal"),
    ("man_made", "spoil_heap"),
)

#: Трудные отрицательные — ровно то, что детектор путает со свалкой.
#: Жильё и лес сюда не входят намеренно: их модель отличит и без обучения,
#: а место в наборе они занимают.
NEGATIVE_TAGS = (
    ("landuse", "quarry"),
    ("landuse", "industrial"),
    ("landuse", "construction"),
    ("landuse", "farmyard"),
    ("landuse", "farmland"),
    ("man_made", "works"),
    ("natural", "water"),
)


def overpass(query: str) -> list[dict]:
    """Запрос к Overpass с обходом зеркал."""
    body = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for host in MIRRORS:
        try:
            request = urllib.request.Request(
                host, data=body, headers={"User-Agent": "vantage-ai/1.0 (hackathon)"}
            )
            with urllib.request.urlopen(request, timeout=300) as response:
                return json.load(response).get("elements", [])
        except Exception as error:  # зеркало недоступно — идём к следующему
            last = error
            log.warning("   %s: %s", host.split("/")[2], type(error).__name__)
            time.sleep(5)
    raise RuntimeError(f"все зеркала Overpass недоступны: {last}")


def overpass_patient(query: str, rounds: int = 4) -> list[dict]:
    """То же, но с растущей паузой между кругами по всем зеркалам.

    Один проход по зеркалам занимает секунды, и если перегружены все —
    а они перегружаются одновременно, — повтор через пять секунд попадает
    в ту же перегрузку. Ночью 23 августа так и не собрался главный
    положительный класс: landuse=landfill отвалился с 500 и 502 на всех
    трёх зеркалах, и набор остался без самой нужной части.
    """
    for attempt in range(rounds):
        try:
            return overpass(query)
        except Exception:
            if attempt + 1 >= rounds:
                raise
            pause = 15 * (2 ** attempt)
            log.info("   все зеркала молчат, жду %d с (круг %d из %d)",
                     pause, attempt + 2, rounds)
            time.sleep(pause)
    return []


#: Казахстан по квадрантам. Один запрос на всю страну по частому тегу
#: Overpass не выдерживает: landuse=landfill валил все три зеркала подряд
#: с 500 и 502, а это как раз главный положительный класс.
#:
#: Поиск по area["ISO3166-1"] к тому же дороже поиска по bbox: сервер
#: сначала строит геометрию страны, потом режет по ней. Прямоугольник
#: захватывает лишнее по краям, но лишнее отсеется тегом.
QUADRANTS = (
    (40.5, 46.5, 48.0, 62.0),
    (40.5, 62.0, 48.0, 76.0),
    (40.5, 76.0, 48.0, 88.0),
    (48.0, 46.5, 56.0, 62.0),
    (48.0, 62.0, 56.0, 76.0),
    (48.0, 76.0, 56.0, 88.0),
)


def fetch_places(tags, limit_per_tag: int) -> list[tuple[float, float, str]]:
    """Центры полигонов по заданным тегам. Возвращает (lat, lon, тег)."""
    found: list[tuple[float, float, str]] = []
    for key, value in tags:
        picked = 0
        seen = 0
        for south, west, north, east in QUADRANTS:
            if picked >= limit_per_tag:
                break
            query = (
                '[out:json][timeout:180];'
                f'(way["{key}"="{value}"]({south},{west},{north},{east});'
                f'relation["{key}"="{value}"]({south},{west},{north},{east}););'
                'out center;'
            )
            try:
                elements = overpass_patient(query)
            except Exception as error:
                log.warning("   %s=%s, квадрант %.0f/%.0f: %s",
                            key, value, south, west, str(error)[:50])
                continue

            seen += len(elements)
            for element in elements:
                centre = element.get("center") or {}
                lat, lon = centre.get("lat"), centre.get("lon")
                if lat is None or lon is None:
                    continue
                found.append((float(lat), float(lon), f"{key}={value}"))
                picked += 1
                if picked >= limit_per_tag:
                    break
            time.sleep(3)

        log.info("%-28s найдено %5d, взято %d", f"{key}={value}", seen, picked)
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=400,
                        help="сколько объектов брать на каждый тег")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    from PIL import Image

    from vantage import env
    from vantage.config import load_settings
    from vantage.verify import PROVIDERS, fetch_tile_grid

    env.configure()
    cfg = load_settings().verify
    OUT.mkdir(parents=True, exist_ok=True)

    if INDEX.exists() and not args.refresh:
        index = json.loads(INDEX.read_text(encoding="utf-8"))
        log.info("список мест взят из кэша: %d", len(index))
    else:
        log.info("── Положительные: свалки OSM по Казахстану ──")
        positives = fetch_places(POSITIVE_TAGS, args.limit)
        log.info("── Отрицательные: то, что детектор путает со свалкой ──")
        negatives = fetch_places(NEGATIVE_TAGS, max(60, args.limit // len(NEGATIVE_TAGS)))

        index = (
            [{"lat": la, "lon": lo, "tag": tg, "label": 1} for la, lo, tg in positives]
            + [{"lat": la, "lon": lo, "tag": tg, "label": 0} for la, lo, tg in negatives]
        )
        random.Random(0).shuffle(index)
        INDEX.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        log.info("список сохранён: %d мест", len(index))

    positives = sum(1 for item in index if item["label"] == 1)
    log.info("всего мест: %d (свалок %d, прочего %d)",
             len(index), positives, len(index) - positives)

    log.info("── Снимки ──")
    done = failed = 0
    for i, item in enumerate(index):
        # Имя файла по координате, а не по позиции в списке. По позиции
        # получался тихий подлог: при новом списке те же имена доставались
        # ДРУГИМ местам, кэш подставлял старые снимки, и набор молча
        # перемешивался — метка от одного объекта, картинка от другого.
        key = hashlib.sha1(f"{item['lat']:.5f},{item['lon']:.5f}".encode()).hexdigest()[:12]
        name = f"{item['label']}_{key}.png"
        path = OUT / name
        item["file"] = name
        if path.exists():
            done += 1
            continue

        got = False
        for key in cfg.providers:
            provider = PROVIDERS.get(key)
            if provider is None:
                continue
            try:
                grid = fetch_tile_grid(provider, item["lat"], item["lon"], 17, 3,
                                       timeout=cfg.timeout_s)
            except Exception:
                continue
            Image.fromarray(grid.astype("uint8")).resize((256, 256)).save(path)
            got = True
            break

        done += got
        failed += not got
        if (i + 1) % 50 == 0:
            log.info("   %4d из %d, скачано %d, не вышло %d",
                     i + 1, len(index), done, failed)

    INDEX.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    log.info("")
    log.info("── Готово: %d снимков в %s, не вышло %d ──", done, OUT, failed)
    log.info("Дальше: scripts/train_kz.py")
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
