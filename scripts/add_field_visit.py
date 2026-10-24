"""Записать выезд одной командой — с проверкой фотографий сразу, на месте.

── Зачем ───────────────────────────────────────────────────────────────

Выезд засчитывается только с фотографией, снятой у объекта
(scripts/attach_ground.py). Но узнавать, что кадр не годится, на
следующий день у компьютера — поздно: объект уже не рядом. Здесь запись
делается одной командой, и фотографии проверяются в момент записи:

  * координаты в EXIF есть и лежат не дальше 150 м от контура;
  * дата съёмки не позже даты выезда и не раньше возникновения объекта.

Если ни один кадр не прошёл, запись НЕ создаётся, а скрипт пишет, что не
так с каждым кадром. Ручная правка ground_truth.json больше не нужна.

    python scripts/add_field_visit.py C00082 --verdict свалка ^
        --by "Иванов И. И., инспектор" --photos D:/DCIM/100/*.jpg ^
        --note "бытовой мусор, свежие колеи"

Дата выезда по умолчанию — сегодня; другая задаётся --date 2026-10-02.
"""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

SOURCE = ROOT / "ground_truth.json"
PHOTOS = ROOT / "data/field"
SITE = ROOT / "web-next/public/data/candidates.geojson"


def _load_ground():
    """Модуль attach_ground: в нём живут правила проверки кадра."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("attach_ground_rules",
                                                  ROOT / "scripts/attach_ground.py")
    mod = importlib.util.module_from_spec(spec)
    # dataclass ищет свой модуль в sys.modules.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    import geopandas as gpd

    from vantage.config import load_settings

    parser = argparse.ArgumentParser(description="Записать выезд с проверкой фотографий")
    parser.add_argument("candidate_id")
    parser.add_argument("--verdict", required=True, choices=("свалка", "не свалка", "не понятно"))
    parser.add_argument("--by", required=True, help="кто был на месте: ФИО, должность")
    parser.add_argument("--photos", required=True, nargs="+", help="файлы или шаблон *.jpg")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--note", default="")
    parser.add_argument("--site", default=str(SITE), help="выгрузка с контурами объектов")
    args = parser.parse_args(argv)

    ground = _load_ground()
    rules = load_settings().field_check

    files = [Path(p) for pattern in args.photos for p in (glob.glob(pattern) or [pattern])]
    files = [f for f in files if f.exists() and f.suffix.lower() in ground.PHOTO_SUFFIX]
    if not files:
        print("нет ни одной фотографии по указанным путям")
        return 1

    site = gpd.read_file(args.site)
    row = site[site["candidate_id"].astype(str) == args.candidate_id]
    if row.empty:
        print(f"объекта {args.candidate_id} нет в {args.site}")
        return 1
    shape = row.to_crs(ground.METRIC_CRS).geometry.iloc[0]
    broke = ground._break_day(row["break_date"].iloc[0]) if "break_date" in row else None

    # Кадры кладутся во временную папку и проверяются тем же кодом, что
    # потом проверит перенос на карту: два разных правила разошлись бы.
    staging = PHOTOS / f"_проверка_{args.candidate_id}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for f in files:
        shutil.copy2(f, staging / f.name)
    record = {"candidate_id": staging.name, "verdict": args.verdict,
              "by": args.by, "date": args.date}
    evidence = ground.check_evidence(record, shape, max_distance_m=rules.max_photo_distance_m,
                                     break_date=broke, root=PHOTOS)

    print(f"── {args.candidate_id}: кадров {evidence.photos}, засчитано {evidence.located}")
    for problem in evidence.problems:
        print(f"   не засчитан: {problem}")
    if evidence.located < rules.min_located_photos:
        shutil.rmtree(staging, ignore_errors=True)
        print("── Выезд НЕ записан: нет ни одного кадра с координатами у объекта.")
        print("   Проверьте, что геометка в камере включена и кадры скопированы файлом,")
        print("   а не пересланы через мессенджер: он вырезает координаты.")
        return 2

    target = PHOTOS / args.candidate_id
    target.mkdir(parents=True, exist_ok=True)
    for f in staging.iterdir():
        shutil.move(str(f), target / f.name)
    shutil.rmtree(staging, ignore_errors=True)

    records = json.loads(SOURCE.read_text(encoding="utf-8")) if SOURCE.exists() else []
    records = [r for r in records if str(r.get("candidate_id")) != args.candidate_id]
    records.append({
        "candidate_id": args.candidate_id, "verdict": args.verdict, "by": args.by,
        "date": args.date, "evidence": f"фотографии с координатами: {evidence.located}",
        "note": args.note,
    })
    SOURCE.write_text(json.dumps(records, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"── Выезд записан в {SOURCE.name}, кадры — в {target}.")
    print("   Дальше: python scripts/attach_ground.py && python scripts/publish_filter.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
