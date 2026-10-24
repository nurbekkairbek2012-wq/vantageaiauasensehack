"""Перенести подтверждения с земли на объекты карты.

── Зачем это отдельно от разметки по снимку ────────────────────────────

Просмотр по снимку 0,4–0,8 м и выезд на место — разные вещи, и разница
между ними решающая. Снимок показывает пятно нужной текстуры; человек на
месте видит, что это, откуда возят и лежит ли оно до сих пор.

Поэтому подтверждение с земли хранится ОТДЕЛЬНО и остаётся отдельным
полем в выгрузке. Слить их в один вердикт значило бы потерять ровно ту
разницу, ради которой стоит ехать.

── Приоритет ───────────────────────────────────────────────────────────

Выезд перебивает просмотр по снимку. Так и должно быть: я поставил восьми
объектам «не разобрать» именно потому, что по снимку там не решить, — а
человек, стоявший рядом, решить может. Данные, полученные ближе к предмету,
сильнее.

── Что нужно записать ──────────────────────────────────────────────────

Файл ``ground_truth.json`` в корне, список записей:

    [
      {
        "candidate_id": "C00082",
        "verdict": "свалка",              свалка | не свалка | не понятно
        "by": "Имя Фамилия",              кто был на месте
        "date": "2026-08-23",             когда
        "evidence": "осмотр на месте",    чем подтверждено
        "note": "бытовой мусор, свежие колеи"
      }
    ]

**Поля ``by`` и ``date`` обязательны.** Не из бюрократии: подтверждение
без имени и даты — это не подтверждение, а утверждение. На вопрос «кто
проверял и когда» должен отвечать файл, а не память выступающего.

Фотографии, если они есть, кладутся в ``data/field/<номер объекта>/`` и
считаются автоматически. Их отсутствие не мешает записать выезд — тогда в
``evidence`` честно пишется, чем именно подтверждено.

    python scripts/attach_ground.py [--outputs outputs_real]
"""

import argparse
import json
import logging
import sys
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

#: Расширения, которые считаем фотографией.
PHOTO_SUFFIX = {".jpg", ".jpeg", ".png", ".heic", ".webp"}


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


def photos_for(candidate_id: str) -> int:
    folder = PHOTOS / str(candidate_id)
    if not folder.exists():
        return 0
    return sum(1 for f in folder.iterdir() if f.suffix.lower() in PHOTO_SUFFIX)


def main() -> int:
    import geopandas as gpd

    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", default="outputs_real")
    args = parser.parse_args()

    records = load()
    if not records:
        log.info("нет подтверждений с земли — %s пуст или отсутствует", SOURCE)
        log.info("формат описан в заголовке этого файла")
        return 0

    by_id = {str(r["candidate_id"]): r for r in records}
    log.info("подтверждений с земли: %d", len(by_id))

    targets = [Path(args.outputs) / "candidates.geojson", WEB]
    seen: set[str] = set()
    for target in targets:
        if not target.exists():
            log.warning("нет %s — пропущено", target)
            continue

        data = gpd.read_file(target)
        ids = data["candidate_id"].astype(str)
        seen |= set(ids)

        def pick(field: str, default=None, ids=ids):
            # ids связывается умолчанием: иначе замыкание смотрит на
            # переменную цикла, и при втором файле pick молча работал бы
            # с номерами первого.
            return ids.map(lambda c: by_id[c].get(field, default) if c in by_id else None)

        data["ground_check"] = ids.map(
            lambda c: CODES[by_id[c]["verdict"]] if c in by_id else None)
        data["ground_by"] = pick("by")
        data["ground_date"] = pick("date")
        data["ground_note"] = pick("note", "")
        data["ground_evidence"] = pick("evidence", "осмотр на месте")
        data["ground_photos"] = ids.map(photos_for)

        matched = int(data["ground_check"].notna().sum())
        data.to_file(target, driver="GeoJSON")
        log.info("%s: подтверждено с земли %d из %d", target, matched, len(data))

    unknown = sorted(set(by_id) - seen)
    if unknown:
        log.warning("в выгрузке нет объектов: %s", ", ".join(unknown))
        log.warning("номера живут до следующего прогона — проверьте, тот ли это прогон")

    log.info("")
    log.info("── Итог ──")
    counts: dict[str, int] = {}
    for record in records:
        counts[record["verdict"]] = counts.get(record["verdict"], 0) + 1
    for verdict, number in sorted(counts.items()):
        log.info("  %-12s %d", verdict, number)

    total_photos = sum(photos_for(c) for c in by_id)
    log.info("  фотографий  %d", total_photos)
    if not total_photos:
        log.info("")
        log.info("Фотографий нет. Выезд записан, и на защите это надо называть")
        log.info("именно так: «осмотр на месте», а не «фотофиксация».")
    return 0


if __name__ == "__main__":
    sys.exit(main())
