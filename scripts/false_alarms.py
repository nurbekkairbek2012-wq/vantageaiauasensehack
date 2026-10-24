"""Ложные тревоги: сколько лишнего доходит до человека на 100 км².

── Зачем ───────────────────────────────────────────────────────────────

Точность — это две величины, а мерилась одна. Полноту (сколько свалок
найдено) проверяют экзамены. Вторая половина — сколько ЛИШНЕГО система
приносит человеку — считалась только как «три четверти отвергнуты» по
одному району. Для заказчика важнее другое число: сколько часов
просмотра стоит каждый квадратный километр наблюдения. Оно и считается
здесь, по всем посчитанным районам, на каждом шаге отсева:

  сырых находок → после контекстного отсева → свалок по вердикту человека

Район, где настоящих свалок ноль, — заведомо чистый: всё, что дошло там
до человека, — ложная тревога в чистом виде.

    python scripts/false_alarms.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
OUT = ROOT / "data/eval/false_alarms.json"

AREAS = ("outputs_real", "outputs_astana_east", "outputs_astana_industrial_west",
         "outputs_astana_south", "outputs_astana_southeast")


def main() -> int:
    import geopandas as gpd
    from build_eval_set import build

    exam = build()
    rows = []
    for area in AREAS:
        folder = ROOT / area
        raw_path = folder / "candidates_raw.geojson"
        if not raw_path.exists():
            continue
        raw = gpd.read_file(raw_path).to_crs(32642)
        # Площадь — по охвату сырых находок: это та территория, где
        # детектор действительно считал.
        minx, miny, maxx, maxy = raw.total_bounds
        km2 = (maxx - minx) * (maxy - miny) / 1e6
        reviewed_path = folder / "candidates.geojson"
        reviewed = len(gpd.read_file(reviewed_path)) if reviewed_path.exists() else 0
        mine = exam[exam["area"] == area]
        dumps = int((mine["truth"] == "landfill").sum())
        unclear = int((mine["truth"] == "unclear").sum())
        false = reviewed - dumps - unclear
        rows.append({
            "area": area.replace("outputs_", ""), "km2": round(km2),
            "raw": len(raw), "reviewed": reviewed, "dumps": dumps, "unclear": unclear,
            "false_to_human": false,
            "false_per_100km2": round(100 * false / km2, 1) if km2 else None,
            "raw_per_100km2": round(100 * len(raw) / km2, 1) if km2 else None,
        })

    print("── Ложные тревоги по районам")
    print(f"   {'район':22} {'км²':>5} {'сырых':>6} {'до чел.':>8} {'свалок':>7} "
          f"{'лишних/100 км²':>15}")
    for r in rows:
        print(f"   {r['area']:22} {r['km2']:>5} {r['raw']:>6} {r['reviewed']:>8} {r['dumps']:>7} "
              f"{r['false_per_100km2']:>15}")
    total_km2 = sum(r["km2"] for r in rows)
    total_false = sum(r["false_to_human"] for r in rows)
    summary = {"rows": rows, "total_km2": total_km2,
               "false_per_100km2": round(100 * total_false / total_km2, 1) if total_km2 else None}
    print(f"   всего: {total_km2} км², лишних до человека {total_false} — "
          f"{summary['false_per_100km2']} на 100 км²")
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"── записано в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
