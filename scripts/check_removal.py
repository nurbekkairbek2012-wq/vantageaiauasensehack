"""Контроль устранения по найденным объектам.

Отвечает на вопрос, ради которого весь модуль и писался: убрали свалку или
засыпали грунтом. Растительность возвращается в обоих случаях, открытый
грунт нормализуется в обоих — различает только тепло, потому что органика
под насыпью продолжает разлагаться и греть.

Результат дописывается в candidates.geojson колонками removal_status и
removal_confidence и отдельно кладётся в removal.json со сводкой.

    python scripts/check_removal.py [--no-thermal]
"""

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

import geopandas as gpd

from vantage.config import load_settings
from vantage.posthistory import assess_all, build_post_histories
from vantage.removal import needs_field_check, summarize

OUTPUTS = Path("outputs_real")

settings = load_settings()
candidates = gpd.read_file(OUTPUTS / "candidates.geojson").to_crs(settings.project.crs_working)
logging.info("Объектов: %d", len(candidates))

histories = build_post_histories(
    candidates, settings, with_thermal="--no-thermal" not in sys.argv
)
assessments = assess_all(histories, settings)

by_id = {a.candidate_id: a for a in assessments}
candidates["removal_status"] = candidates["candidate_id"].map(
    lambda cid: by_id[cid].status if cid in by_id else "insufficient_data"
)
candidates["removal_confidence"] = candidates["candidate_id"].map(
    lambda cid: round(by_id[cid].confidence, 3) if cid in by_id else None
)
candidates["removal_note"] = candidates["candidate_id"].map(
    lambda cid: by_id[cid].to_text() if cid in by_id else ""
)

candidates.to_crs(settings.project.crs_output).to_file(
    OUTPUTS / "candidates.geojson", driver="GeoJSON"
)

counts = summarize(assessments)
field_check = needs_field_check(assessments)
payload = {
    "counts": counts,
    # Подозрение на присыпку идёт первым не из вежливости: по такому
    # объекту может быть закрыт акт и оплачена работа, а отходы на месте.
    "needs_field_check": field_check,
    "objects": [
        {
            "candidate_id": a.candidate_id,
            "status": a.status,
            "confidence": round(a.confidence, 3),
            "n_agreeing": a.n_agreeing,
            "n_observations": a.n_observations,
            "consecutive_passes": a.consecutive_passes,
            "signals": a.signals,
            "note": a.to_text(),
            "warnings": a.warnings,
        }
        for a in assessments
    ],
}
(OUTPUTS / "removal.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
)

logging.info("Сводка: %s", counts)
if field_check:
    logging.warning("ВЫЕЗД В ПЕРВУЮ ОЧЕРЕДЬ (подозрение на присыпку): %s", ", ".join(field_check))
logging.info("Записано: %s", OUTPUTS / "removal.json")
