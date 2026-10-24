"""Записать ответ ИИ-проверяющего по листу: код, вердикт, уверенность, причина.

Ответы пишутся в data/vision/answers_<target>.json по коду листа — до того,
как проверяющий увидит ключ (scripts/vision_exam.py сверяет потом).

    python scripts/vision_answer.py V8477 not 0.85 "канал с насыпью вдоль реки"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERDICTS = {"dump": "landfill", "not": "not_landfill", "unclear": "unclear"}


def main() -> int:
    target = "eval"
    args = sys.argv[1:]
    if args and args[0].startswith("--target="):
        target = args.pop(0).split("=", 1)[1]
    code, verdict, confidence, *note = args
    path = ROOT / f"data/vision/answers_{target}.json"
    answers = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    answers[code] = {"verdict": VERDICTS[verdict], "confidence": float(confidence),
                     "note": " ".join(note), "reviewer": "claude-vision"}
    path.write_text(json.dumps(answers, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{code}: {answers[code]['verdict']} ({len(answers)} ответов)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
