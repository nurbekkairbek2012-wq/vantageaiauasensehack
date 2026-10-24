"""Сверить ответы ИИ-проверяющего с разметкой — после того, как все ответы записаны.

Ответы — data/vision/answers_eval.json (scripts/vision_answer.py), ключ —
data/vision/key_eval.json (scripts/vision_sheets.py), разметка — экзамен
data/eval/labeled.geojson. Считается:

  * сколько свалок проверяющий пропустил, назвав «не свалка», — главное
    число: такой отсев выбрасывает настоящую свалку;
  * сколько не-свалок он снял бы с очереди верно — это экономия работы;
  * согласие с человеком и таблица расхождений: каждое расхождение — это
    объект, где кто-то из двоих ошибся, и именно их стоит смотреть первыми.

Порядок подсчёта: «не разобрать» у проверяющего — не отказ, объект
остаётся в очереди. Отдельно — северное кольцо, где лежат все свалки.

    python scripts/vision_exam.py
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
OUT = ROOT / "data/eval/vision_exam.json"
NAMES = {"landfill": "свалка", "not_landfill": "не свалка", "unclear": "не разобрать"}


def main() -> int:
    from build_eval_set import build
    from screen_metrics import clopper_pearson
    from sklearn.metrics import roc_auc_score

    answers = json.loads((ROOT / "data/vision/answers_eval.json").read_text(encoding="utf-8"))
    key = json.loads((ROOT / "data/vision/key_eval.json").read_text(encoding="utf-8"))
    frame = build().set_index("candidate_id")

    rows = []
    for code, cid in key.items():
        if code in answers and cid in frame.index:
            rows.append((cid, frame.loc[cid, "area"], frame.loc[cid, "truth"],
                         answers[code]["verdict"], float(answers[code]["confidence"]),
                         answers[code].get("note", "")))

    def block(name, sub):
        dumps = [r for r in sub if r[2] == "landfill"]
        nots = [r for r in sub if r[2] == "not_landfill"]
        lost = [r for r in dumps if r[3] == "not_landfill"]
        found = [r for r in dumps if r[3] == "landfill"]
        cut = [r for r in nots if r[3] == "not_landfill"]
        false_dump = [r for r in nots if r[3] == "landfill"]
        decided = [r for r in sub if r[2] in ("landfill", "not_landfill")]
        agree = sum(r[2] == r[3] for r in decided)
        # Оценка для ранжирования: «свалка» по уверенности, «не разобрать» —
        # середина, «не свалка» — 1 − уверенность.
        score = [r[4] if r[3] == "landfill" else 0.5 if r[3] == "unclear" else 1 - r[4]
                 for r in decided]
        truth = [int(r[2] == "landfill") for r in decided]
        auc = roc_auc_score(truth, score) if 0 < sum(truth) < len(truth) else None
        low = clopper_pearson(len(dumps) - len(lost), len(dumps))[0] if dumps else None
        result = {
            "objects": len(sub), "dumps": len(dumps), "not": len(nots),
            "dumps_lost": len(lost), "dumps_named": len(found),
            "not_cut": len(cut), "not_named_dump": len(false_dump),
            "agreement_decided": round(agree / len(decided), 3) if decided else None,
            "roc_auc": None if auc is None else round(float(auc), 3),
            "kept_recall_low": None if low is None else round(low, 3),
            "workload_removed": round(len(cut) / len(nots), 3) if nots else None,
        }
        print(f"\n── {name}: {len(sub)} объектов, свалок {len(dumps)}, не-свалок {len(nots)}")
        print(f"   свалок потеряно (назвал «не свалка»): {len(lost)} из {len(dumps)}"
              + (f"  → полнота не ниже {low:.0%} (90%)" if low is not None else ""))
        print(f"   свалок назвал свалкой:                {len(found)} из {len(dumps)}")
        print(f"   не-свалок снял бы верно:              {len(cut)} из {len(nots)}"
              + (f" ({len(cut) / len(nots):.0%} работы)" if nots else ""))
        print(f"   не-свалок назвал свалкой:             {len(false_dump)}")
        if auc is not None:
            print(f"   ROC-AUC по уверенности:               {auc:.3f}")
        return result

    results = {"all": block("Весь экзамен", rows),
               "north": block("Северное кольцо", [r for r in rows if r[1] == "outputs_real"])}

    print("\n── Расхождения с человеком (кто-то из двоих ошибся)")
    disputes = []
    for cid, area, truth, verdict, conf, note in sorted(rows):
        if truth != verdict and not (truth == "unclear" and verdict == "unclear"):
            disputes.append({"candidate_id": cid, "area": area, "human": truth,
                             "ai": verdict, "confidence": conf, "note": note})
            print(f"   {cid:40} человек: {NAMES[truth]:13} ИИ: {NAMES[verdict]:13} "
                  f"({conf:.2f}) {note[:70]}")
    results["disputes"] = disputes
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n── записано в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
