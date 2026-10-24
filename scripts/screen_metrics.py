"""Машинный просмотр против человека — считается, а не переписывается.

── Зачем ───────────────────────────────────────────────────────────────

Числа «полнота 7 из 8, отбраковка 34 из 35» были посчитаны один раз, в
ночь на 23 августа, и вписаны в README, деку, экран «Экономика» и текст
питча. После этого разметка менялась: два объекта оказались авторазборкой,
часть «не разобрать» пересмотрена. Числа в текстах остались прежними и
перестали совпадать с данными — ровно тот тихий дрейф, от которого проект
уже защищает остальные числа.

Теперь матрица ошибок считается здесь, из labels_ai_screen.json (вердикт
машины) и visual_check в выгрузке прогона (вердикт человека), и кладётся в
web-next/public/data/screen.json. Сайт и docs/NUMBERS.md читают её оттуда.

── Как читать ──────────────────────────────────────────────────────────

Машина — отбраковщик, а не детектор. Поэтому главные числа — про отказы:

  * сколько работы снимает — доля объектов, которым машина сказала «нет»;
  * чего это стоит — сколько опознанных человеком свалок среди отказов;
  * полнота — доля опознанных свалок, которых машина НЕ отвергла.

Интервалы — точные (Клоппер — Пирсон), 90%: при единицах положительных
середина без границ ничего не значит, и называть надо нижнюю границу.

    python scripts/screen_metrics.py [--outputs outputs_real]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
SCREEN = ROOT / "labels_ai_screen.json"
OUT = ROOT / "web-next/public/data/screen.json"

MACHINE = ("dump", "maybe", "no")
HUMAN = ("landfill", "not_landfill", "unclear")


def clopper_pearson(k: int, n: int, level: float = 0.90) -> tuple[float, float]:
    """Точный интервал для доли k из n."""
    if n == 0:
        return 0.0, 1.0
    from scipy.stats import beta

    alpha = 1.0 - level
    low = 0.0 if k == 0 else float(beta.ppf(alpha / 2, k, n - k + 1))
    high = 1.0 if k == n else float(beta.ppf(1 - alpha / 2, k + 1, n - k))
    return low, high


def measure(human: dict[str, str], machine: dict[str, str]) -> dict:
    """Матрица и производные числа по объектам, у которых есть оба вердикта."""
    both = sorted(set(human) & set(machine))
    matrix = {m: {h: 0 for h in HUMAN} for m in MACHINE}
    for cid in both:
        m, h = machine[cid], human[cid]
        if m in matrix and h in matrix[m]:
            matrix[m][h] += 1

    n = sum(sum(row.values()) for row in matrix.values())
    rejected = sum(matrix["no"].values())
    dumps = sum(matrix[m]["landfill"] for m in MACHINE)
    kept_dumps = dumps - matrix["no"]["landfill"]
    said_dump = sum(matrix["dump"].values())

    recall_low, recall_high = clopper_pearson(kept_dumps, dumps)
    clean_low, clean_high = clopper_pearson(matrix["no"]["not_landfill"], rejected)
    return {
        "generated": date.today().isoformat(),
        "objects": n,
        "matrix": matrix,
        # Сколько ручной работы снимает: объект с «нет» человек может
        # смотреть последним.
        "workload_removed": round(rejected / n, 3) if n else 0.0,
        "rejected": rejected,
        "rejected_not_landfill": matrix["no"]["not_landfill"],
        "rejected_unclear": matrix["no"]["unclear"],
        "rejected_landfill": matrix["no"]["landfill"],
        "rejected_clean_low": round(clean_low, 3),
        "rejected_clean_high": round(clean_high, 3),
        "dumps": dumps,
        "dumps_kept": kept_dumps,
        "recall_low": round(recall_low, 3),
        "recall_high": round(recall_high, 3),
        "said_dump": said_dump,
        "said_dump_agreed": matrix["dump"]["landfill"],
    }


def main() -> int:
    import geopandas as gpd

    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", default="outputs_real")
    args = parser.parse_args()

    source = ROOT / args.outputs / "candidates.geojson"
    if not source.exists() or not SCREEN.exists():
        print(f"нет {source} или {SCREEN}")
        return 1

    data = gpd.read_file(source)
    human = {
        str(c): str(v) for c, v in zip(data["candidate_id"], data["visual_check"], strict=True)
        if isinstance(v, str) and v
    }
    screen = json.loads(SCREEN.read_text(encoding="utf-8")).get("screen") or {}
    machine = {str(k): v["verdict"] for k, v in screen.items() if v.get("verdict")}

    result = measure(human, machine)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    m = result["matrix"]
    print(f"── Машинный просмотр против человека: {result['objects']} объектов")
    print(f"   {'машина ↓ / человек →':24} " + "  ".join(f"{h:>12}" for h in HUMAN))
    for row in MACHINE:
        print(f"   {row:24} " + "  ".join(f"{m[row][h]:>12}" for h in HUMAN))
    print(f"   снимает работы: {result['workload_removed']:.0%} "
          f"({result['rejected']} отказов из {result['objects']})")
    print(f"   среди отказов: не свалка {result['rejected_not_landfill']}, "
          f"не разобрать {result['rejected_unclear']}, свалка {result['rejected_landfill']}")
    print(f"   полнота по опознанным свалкам: {result['dumps_kept']} из {result['dumps']} "
          f"(90%: {result['recall_low']:.2f}–{result['recall_high']:.2f})")
    print(f"── записано в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
