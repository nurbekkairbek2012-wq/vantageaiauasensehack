"""Перенести подтверждения выездом на объекты карты.

── Зачем это отдельно от разметки по снимку ────────────────────────────

Просмотр по снимку 0,4–0,8 м и выезд на место — разные вещи, и разница
между ними решающая. Снимок показывает пятно нужной текстуры; человек на
месте видит, что это, откуда возят и лежит ли оно до сих пор.

Поэтому подтверждение выездом хранится ОТДЕЛЬНО и остаётся отдельным
полем в выгрузке. Слить их в один вердикт значило бы потерять ровно ту
разницу, ради которой стоит ехать.

── Приоритет ───────────────────────────────────────────────────────────

Выезд перебивает просмотр по снимку. Объекты с вердиктом «не разобрать»
получили его именно потому, что по снимку там не решить, — а человек,
стоявший рядом, решить может. Данные, полученные ближе к предмету,
сильнее.

── Выезд без фотографии не засчитывается ───────────────────────────────

Первая редакция принимала запись с именем и датой без единого кадра, и
записи «осмотр на месте», которые нечем было проверить, ушли на сайт
зелёной меткой «подтверждено». Они отозваны. Ошибка здесь в устройстве:
система, которая принимает «я был там» на слово, передаёт заказчику
чужое слово под своей печатью.

Теперь выезд принимается, только если в ``data/field/<номер объекта>/``
лежит хотя бы одна фотография, у которой в EXIF есть координаты и они не
дальше ``field_check.max_photo_distance_m`` от контура объекта. Если в
кадре есть и дата съёмки, она обязана быть не позже даты записи и не
раньше возникновения объекта. Запись без такой фотографии отклоняется с
объяснением, что именно не так.

Координаты в EXIF можно подделать, и это не криптография. Но это
превращает «поверьте на слово» в утверждение, которое проверяется
открытием файла, — и подделка перестаёт быть случайной небрежностью.

── Что нужно записать ──────────────────────────────────────────────────

Файл ``ground_truth.json`` в корне, список записей:

    [
      {
        "candidate_id": "C00082",
        "verdict": "свалка",              свалка | не свалка | не понятно
        "by": "Имя Фамилия, должность",   кто был на месте
        "date": "2026-10-02",             когда
        "evidence": "фотографии, 4 кадра",
        "note": "бытовой мусор, свежие колеи"
      }
    ]

**Поля ``by`` и ``date`` обязательны**, фотографии — тоже. Подтверждение
без имени, даты и кадра — это не подтверждение, а утверждение.

Геометка и дата в камере телефона включаются до выезда. Мессенджеры
вырезают EXIF при пересылке — фотографии копируются с телефона файлом.

    python scripts/attach_ground.py [--outputs outputs_real]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("ground")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SOURCE = Path("ground_truth.json")
WEB = Path("web-next/public/data/candidates.geojson")
PHOTOS = Path("data/field")

#: Вердикт выезда -> значение в данных. Латиницей, как и у разметки по
#: снимку: это значение поля, а не текст для человека.
CODES = {"свалка": "landfill", "не свалка": "not_landfill", "не понятно": "unclear"}

#: Расширения, которые считаем фотографией. HEIC считается, но координаты
#: из него без дополнительной библиотеки не читаются — такой кадр не
#: засчитывается как привязанный к месту, и скрипт говорит об этом.
PHOTO_SUFFIX = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff"}

#: Метрическая проекция для расстояний: UTM 42N, как во всём проекте.
METRIC_CRS = 32642

_GPS_IFD = 0x8825
_EXIF_IFD = 0x8769
_DATETIME_ORIGINAL = 36867
_DATETIME = 306


@dataclass
class PhotoFacts:
    """Что известно о кадре из его EXIF."""

    path: Path
    lat: float | None = None
    lon: float | None = None
    taken: date | None = None


@dataclass
class Evidence:
    """Итог проверки фотографий по одному объекту."""

    photos: int = 0
    located: int = 0
    nearest_m: float | None = None
    problems: list[str] = field(default_factory=list)


def _dms(value, ref) -> float | None:
    """Градусы, минуты, секунды из EXIF -> десятичные градусы."""
    try:
        d, m, s = (float(x) for x in value)
    except (TypeError, ValueError):
        return None
    deg = d + m / 60.0 + s / 3600.0
    if str(ref).upper() in {"S", "W"}:
        deg = -deg
    return deg


def _exif_date(raw) -> date | None:
    """«2026:09:20 12:30:00» -> дата. Время отбрасывается: сверяются дни."""
    if not raw:
        return None
    text = str(raw).strip().replace("\x00", "")[:10].replace("-", ":")
    try:
        return datetime.strptime(text, "%Y:%m:%d").date()
    except ValueError:
        return None


def read_photo(path: Path) -> PhotoFacts:
    """Координаты и дата съёмки из EXIF. Нечитаемый кадр — пустые поля."""
    facts = PhotoFacts(path=path)
    try:
        from PIL import Image

        with Image.open(path) as img:
            exif = img.getexif()
            gps = exif.get_ifd(_GPS_IFD)
            if gps:
                facts.lat = _dms(gps.get(2), gps.get(1))
                facts.lon = _dms(gps.get(4), gps.get(3))
            sub = exif.get_ifd(_EXIF_IFD)
            facts.taken = _exif_date(sub.get(_DATETIME_ORIGINAL) or exif.get(_DATETIME))
    except Exception as error:  # битый файл не должен ронять весь перенос
        log.debug("не прочитан EXIF %s: %s", path, error)
    return facts


def photos_of(candidate_id: str, root: Path = PHOTOS) -> list[Path]:
    folder = root / str(candidate_id)
    if not folder.exists():
        return []
    return sorted(f for f in folder.iterdir() if f.suffix.lower() in PHOTO_SUFFIX)


def photos_for(candidate_id: str, root: Path = PHOTOS) -> int:
    return len(photos_of(candidate_id, root))


def check_evidence(record: dict, shape_metric, *, max_distance_m: float,
                   break_date: date | None = None, root: Path = PHOTOS) -> Evidence:
    """Проверить фотографии выезда против контура объекта.

    ``shape_metric`` — геометрия объекта в метрической проекции. Кадр
    засчитывается, если его координаты не дальше ``max_distance_m`` от
    контура и дата съёмки (если есть) не противоречит записи.
    """
    from pyproj import Transformer
    from shapely.geometry import Point

    to_metric = Transformer.from_crs(4326, METRIC_CRS, always_xy=True)
    try:
        record_day = date.fromisoformat(str(record.get("date"))[:10])
    except ValueError:
        record_day = None

    result = Evidence()
    for path in photos_of(str(record["candidate_id"]), root):
        result.photos += 1
        facts = read_photo(path)
        if facts.lat is None or facts.lon is None:
            result.problems.append(f"{path.name}: нет координат в EXIF")
            continue
        x, y = to_metric.transform(facts.lon, facts.lat)
        distance = float(shape_metric.distance(Point(x, y)))
        if result.nearest_m is None or distance < result.nearest_m:
            result.nearest_m = distance
        if distance > max_distance_m:
            result.problems.append(f"{path.name}: снят в {distance:,.0f} м от объекта".replace(",", " "))
            continue
        if facts.taken and record_day and facts.taken > record_day:
            result.problems.append(f"{path.name}: снят {facts.taken}, позже даты записи {record_day}")
            continue
        if facts.taken and break_date and facts.taken < break_date:
            result.problems.append(f"{path.name}: снят {facts.taken}, раньше, чем возник объект")
            continue
        result.located += 1
    return result


def load() -> list[dict]:
    """Прочитать записи и отбросить неполные — с объяснением, что не так."""
    if not SOURCE.exists():
        return []
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    records = data if isinstance(data, list) else data.get("records", [])
    good = []
    for i, record in enumerate(records, 1):
        missing = [k for k in ("candidate_id", "verdict", "by", "date") if not record.get(k)]
        if missing:
            log.error("запись %d: не хватает полей %s — пропущена", i, ", ".join(missing))
            continue
        if record["verdict"] not in CODES:
            log.error("запись %d: вердикт %r не из списка %s",
                      i, record["verdict"], list(CODES))
            continue
        good.append(record)
    return good


def _break_day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def main() -> int:
    import geopandas as gpd

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from vantage.config import load_settings

    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", default="outputs_real")
    args = parser.parse_args()

    rules = load_settings().field_check
    records = load()
    # Пустой файл — не повод выйти, ничего не тронув. Раньше скрипт так и
    # делал, и запись, удалённая из ground_truth.json, продолжала жить в
    # выгрузке: колонки ground_* оставались от прошлого запуска, и на
    # карте стояло «подтверждено выездом» по выезду, которого больше нет в
    # источнике. Отозванное подтверждение обязано исчезать так же
    # надёжно, как появляется, поэтому колонки переписываются всегда.
    if not records:
        log.info("нет записей о выездах — %s пуст или отсутствует", SOURCE)
        log.info("формат описан в заголовке этого файла")

    by_id = {str(r["candidate_id"]): r for r in records}
    log.info("записей о выездах: %d", len(by_id))

    targets = [Path(args.outputs) / "candidates.geojson", WEB]
    seen: set[str] = set()
    accepted: dict[str, Evidence] = {}
    rejected: dict[str, Evidence] = {}
    for target in targets:
        if not target.exists():
            log.warning("нет %s — пропущено", target)
            continue

        data = gpd.read_file(target)
        ids = data["candidate_id"].astype(str)
        seen |= set(ids)
        metric = data.to_crs(METRIC_CRS).geometry

        evidence: dict[str, Evidence] = {}
        for i, cid in enumerate(ids):
            if cid not in by_id:
                continue
            breaks = data["break_date"].iat[i] if "break_date" in data else None
            evidence[cid] = check_evidence(
                by_id[cid], metric.iat[i],
                max_distance_m=rules.max_photo_distance_m,
                break_date=_break_day(breaks),
            )
        good = {c for c, e in evidence.items() if e.located >= rules.min_located_photos}
        accepted.update({c: evidence[c] for c in good})
        rejected.update({c: e for c, e in evidence.items() if c not in good})

        # Всё, что зависит от набора текущего файла, передаётся явно:
        # замыкание на переменную цикла при втором файле молча работало
        # бы с набором первого — так уже ломалось здесь однажды.
        def pick(keys, accepted_ids, field_name: str, default=None):
            return keys.map(
                lambda key: by_id[key].get(field_name, default) if key in accepted_ids else None)

        data["ground_check"] = ids.map(
            lambda key, ok=good: CODES[by_id[key]["verdict"]] if key in ok else None)
        data["ground_by"] = pick(ids, good, "by")
        data["ground_date"] = pick(ids, good, "date")
        data["ground_note"] = pick(ids, good, "note", "")
        data["ground_evidence"] = pick(ids, good, "evidence", "фотографии с координатами")
        data["ground_photos"] = ids.map(
            lambda key, ev=evidence: ev[key].photos if key in ev else 0)
        data["ground_photos_located"] = ids.map(
            lambda key, ok=good, ev=evidence: ev[key].located if key in ok else 0)

        matched = int(data["ground_check"].notna().sum())
        data.to_file(target, driver="GeoJSON")
        log.info("%s: подтверждено выездом %d из %d", target, matched, len(data))

    unknown = sorted(set(by_id) - seen)
    if unknown:
        log.warning("в выгрузке нет объектов: %s", ", ".join(unknown))
        log.warning("номера живут до следующего прогона — проверьте, тот ли это прогон")

    for cid, ev in sorted(rejected.items()):
        reason = "; ".join(ev.problems[:3]) if ev.problems else "в data/field/ нет ни одного кадра"
        log.error("%s: выезд НЕ засчитан — %s", cid, reason)

    log.info("")
    log.info("── Итог ──")
    if not records:
        log.info("  выездов нет — на сайте не будет ни одного «подтверждено выездом»")
        return 0
    log.info("  засчитано выездов   %d", len(accepted))
    log.info("  отклонено           %d  (нет фотографии с координатами рядом с объектом)",
             len(rejected))
    counts: dict[str, int] = {}
    for cid in accepted:
        verdict = by_id[cid]["verdict"]
        counts[verdict] = counts.get(verdict, 0) + 1
    for verdict, number in sorted(counts.items()):
        log.info("    %-12s %d", verdict, number)
    return 0


if __name__ == "__main__":
    sys.exit(main())
