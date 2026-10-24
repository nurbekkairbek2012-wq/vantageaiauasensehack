"""Различают ли признаки поведения во времени свалку и не-свалку.

Признаки считает scripts/object_timeseries.py (кэш data/eval/objects_ts/).
Проверка та же, что у всех признаков (scripts/check_triage.py):

  * по классу — свалки против не-свалок ВНУТРИ северного кольца;
  * по месту — не-свалки севера против не-свалок других поясов.

Признак годится, только если он силён по классу, а по месту близок к 0,5.
Направление не задаётся заранее: AUC ниже 0,5 значит «у свалок меньше»,
и тогда рядом печатается 1 − AUC.

    python scripts/check_object_ts.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
CACHE = ROOT / "data/eval/objects_ts"
OUT = ROOT / "data/eval/object_ts_check.json"
HOME = "outputs_real"
FEATURES = ("break_spread_months", "growth_share", "after_amplitude", "ndbi_after", "ndbi_rise")


def main() -> int:
    import pandas as pd
    from build_eval_set import build
    from sklearn.metrics import roc_auc_score
    from train_dinov2 import interval

    frame = build()
    rows = []
    for row in frame.itertuples():
        path = CACHE / f"{str(row.candidate_id).replace(':', '_')}.json"
        if path.exists():
            rows.append({"candidate_id": row.candidate_id, "truth": row.truth, "area": row.area,
                         **json.loads(path.read_text(encoding="utf-8"))})
    d = pd.DataFrame(rows)
    d = d[d["truth"].isin(["landfill", "not_landfill"])]
    home = d["area"] == HOME
    north = d[home]
    y = (north["truth"] == "landfill").to_numpy().astype(int)
    print(f"── объектов с рядами: {len(d)}; север: {len(north)} (свалок {int(y.sum())})")
    if y.sum() == 0 or y.sum() == len(y):
        print("   на севере нет обоих классов — рано")
        return 1

    nots = d[d["truth"] == "not_landfill"]
    place_y = (nots["area"] == HOME).to_numpy().astype(int)
    results = {}
    print(f"\n   {'признак':22} {'по классу':>10} {'90% интервал':>14} {'по месту':>9}")
    for column in FEATURES:
        x = pd.to_numeric(north[column], errors="coerce").fillna(0).to_numpy()
        auc = roc_auc_score(y, x)
        sign = 1 if auc >= 0.5 else -1
        low, high = interval(y, sign * x)
        place = None
        if 0 < place_y.sum() < len(place_y):
            px = pd.to_numeric(nots[column], errors="coerce").fillna(0).to_numpy()
            place = roc_auc_score(place_y, sign * px)
        flipped = "" if sign > 0 else " (у свалок меньше)"
        place_text = f"{place:9.2f}" if place is not None else f"{'—':>9}"
        print(f"   {column:22} {max(auc, 1 - auc):10.2f} {low:6.2f} – {high:5.2f} {place_text}{flipped}")
        results[column] = {"class_auc": round(float(max(auc, 1 - auc)), 3),
                           "direction": "higher" if sign > 0 else "lower",
                           "low": round(float(low), 3), "high": round(float(high), 3),
                           "place_auc": None if place is None else round(float(place), 3)}
    results["_objects"] = {"north": len(north), "dumps": int(y.sum()), "all": len(d)}
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"── записано в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
