"""Найти, где вокруг Астаны карта достаточно подробна для прогона.

── Зачем ───────────────────────────────────────────────────────────────

Области для поиска до сих пор выбирались догадкой: «у сёл должно быть
больше свалок». Догадка проверялась прогоном по четыре часа и просмотром
по часу, и три раза из четырёх оказалась неверной — 104 находки в сельской
местности, ни одной настоящей свалки.

Замер полноты карты стоит один запрос `out count`. Это меняет порядок
действий: вместо «выбрать область и проверить прогоном» — **просмотреть
десятки областей замером и запускать только те, что прошли**.

── Что меряется ────────────────────────────────────────────────────────

Размеченные контуры OSM на квадратный километр. Обоснование замера — в
docs/BELTS.md; коротко: пять физических признаков находят необратимое
изменение поверхности, но свалку от склада внутри одной местности НЕ
отличают (ROC-AUC 0,500). Отсеивает лишнее контекстный фильтр по OSM, и
работает он ровно настолько, насколько подробна карта.

Порядок замеренных областей совпал с числом найденных свалок целиком:

    97,8  запад · промзона    считается
    94,7  север               3 подтверждённых + 5 опознанных
    32,5  запад · сёла        не запускался
    18,1  юго-восток          0 настоящих из 9
     6,8  восток              0 настоящих из 33

── Осторожность ────────────────────────────────────────────────────────

Замер — нижняя граница, а не обещание. Он говорит, что отсеву будет чем
работать; свалок может не оказаться и на подробной карте. Обратное же
утверждение сильное: где карты нет, отсев не работает, и до списка
доходит всё подряд.

Между запросами держится пауза: Overpass отвечает 429 на частые запросы,
и однажды я положил себе доступ собственным замером.

    python scripts/scan_density.py [--step 0.12] [--pause 20]
"""

import argparse
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("scan")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))

#: Что уже посчитано — эти клетки не предлагать заново.
DONE = {
    "север": (71.37, 51.12, 71.66, 51.30),
    "восток": (71.66, 51.10, 71.95, 51.28),
    "юго-восток": (71.60, 51.02, 71.88, 51.18),
    "запад · сёла": (71.08, 51.04, 71.36, 51.28),
    "запад · промзона": (71.18, 51.06, 71.42, 51.22),
}

#: Область просмотра: агломерация Астаны целиком.
FIELD = (70.95, 50.95, 72.05, 51.45)


def overlaps(a, b, share: float = 0.35) -> bool:
    """Перекрываются ли клетки настолько, что вторая не даст нового."""
    wide = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    high = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    own = (a[2] - a[0]) * (a[3] - a[1])
    return own > 0 and wide * high / own > share


def main() -> int:
    from probe_city import osm_density

    from vantage import env
    from vantage.config import load_settings

    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=float, default=0.12,
                        help="сторона клетки в градусах долготы (~8 км)")
    parser.add_argument("--pause", type=float, default=20.0)
    parser.add_argument("--top", type=int, default=8)
    args = parser.parse_args()

    env.configure()
    settings = load_settings()

    # Клетка берётся квадратной на местности, а не в градусах: на широте
    # 51° градус долготы вдвое короче градуса широты, и клетка «0,12 на
    # 0,12» была бы вытянутой вдвое.
    lat_step = args.step * 0.63

    boxes = []
    lon = FIELD[0]
    while lon + args.step <= FIELD[2] + 1e-9:
        lat = FIELD[1]
        while lat + lat_step <= FIELD[3] + 1e-9:
            boxes.append((round(lon, 3), round(lat, 3),
                          round(lon + args.step, 3), round(lat + lat_step, 3)))
            lat += lat_step
        lon += args.step

    log.info("клеток к просмотру: %d, сторона ~%.0f км", len(boxes), args.step * 70)
    log.info("")

    found = []
    for i, box in enumerate(boxes, 1):
        seen = next((n for n, b in DONE.items() if overlaps(box, b)), None)
        try:
            density = osm_density(box, settings)
        except Exception as error:
            log.warning("[%2d/%d] %s — замер не вышел: %s", i, len(boxes), box,
                        str(error)[:60])
            time.sleep(args.pause)
            continue

        mark = f"уже: {seen}" if seen else ""
        log.info("[%2d/%d] %7.1f контура/км²  %s  %s", i, len(boxes), density,
                 f"{box[0]:.2f},{box[1]:.2f}", mark)
        if not seen:
            found.append((density, box))
        time.sleep(args.pause)

    found.sort(reverse=True)
    log.info("")
    log.info("── Непросмотренные клетки, лучшие сверху ──")
    log.info("")
    for density, box in found[:args.top]:
        if density >= 50:
            answer = "запускать: карта подробна"
        elif density >= 20:
            answer = "неизвестно: середина, прогонов не было"
        else:
            answer = "не запускать: отсеивать будет нечем"
        log.info("  %6.1f  bbox: [%s]  %s", density,
                 ", ".join(f"{v:.2f}" for v in box), answer)

    good = [b for d, b in found if d >= 50]
    log.info("")
    if good:
        log.info("Годных клеток: %d. Соседние стоит слить в одну область —", len(good))
        log.info("прогон по области в 300 км² дешевле пяти прогонов по 60.")
    else:
        log.info("Годных клеток нет. Это ответ, а не неудача: за пределами")
        log.info("промзоны и города карта под Астаной пуста, и метод там не работает.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
