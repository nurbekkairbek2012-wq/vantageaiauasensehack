"""Образцы акта для заказчика: черновик и подтверждённый — на настоящем объекте.

── Зачем ───────────────────────────────────────────────────────────────

В docs/examples лежали два акта, собранные 17 августа на синтетических
данных: объект C00042, которого нет в прогоне, вымышленная подпись и
примечание «объект подтверждён выездом». Показать такой лист акимату
значит показать выдуманный документ, который выглядит как настоящий.

Теперь образцы собираются из верхнего объекта очереди — того же, с которого
начинается демонстрация, — и оба помечены «ОБРАЗЕЦ». В подтверждённом
вместо имени стоит место для подписи: подтверждает акт уполномоченное
лицо по итогам выездной проверки, а не команда проекта.

    python scripts/make_act_examples.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DATA = ROOT / "web-next/public/data"
OUT = ROOT / "docs/examples"


def main() -> int:
    import json

    import geopandas as gpd

    from vantage.act import ActDraft, render_pdf
    from vantage.config import load_economics
    from vantage.explain import physical_evidence
    from vantage.money import assess

    site = gpd.read_file(DATA / "candidates.geojson")
    economy = json.loads((DATA / "economy.json").read_text(encoding="utf-8"))
    # Верхний объект очереди, но только из опознанных по снимку: акт на
    # объект «не разобрать» до выезда составлять не на чем.
    sure = set(site.loc[site["visual_check"] == "landfill", "candidate_id"].astype(str))
    top_id = next((p["id"] for p in economy["priority"] if p["id"] in sure), None)
    if top_id is None:
        print("нет опознанных объектов — образец не из чего собрать")
        return 1
    row = site[site["candidate_id"].astype(str) == top_id].iloc[0]

    economics = load_economics()
    assessment = assess(float(row["area_m2"]), economics,
                        age_years=float(row.get("age_years") or 0.0))

    def value(name: str) -> float:
        v = row.get(name)
        return float(v) if v is not None and v == v else float("nan")

    evidence = physical_evidence(
        top_id,
        ndvi_drop=value("ndvi_drop"), bsi_rise=value("bsi_rise"),
        pmli_response=value("pmli_response"), sar_incoherence=value("sar_incoherence"),
        thermal_anomaly=value("thermal_anomaly"),
    )

    def build() -> ActDraft:
        act = ActDraft.from_pipeline(row, assessment, evidence, economics)
        # Оценка модели по снимку высокого разрешения — то же число, что
        # в карточке на карте, а не пустое поле demo-генератора.
        score = row.get("highres_score")
        if score is not None and score == score:
            act.model_probability = float(score)
        return act

    OUT.mkdir(parents=True, exist_ok=True)
    render_pdf(build(), OUT / "act_draft.pdf", sample=True)

    approved = build().approve(
        "________________ (Ф. И. О.)",
        "уполномоченное лицо отдела экологии",
        note=("образец заполнения. Акт подтверждает уполномоченное лицо по итогам "
              "выездной проверки; к акту прикладываются фотографии с координатами."),
    )
    render_pdf(approved, OUT / "act_approved.pdf", sample=True)

    print(f"── Образцы акта на объекте {top_id}: {OUT / 'act_draft.pdf'}, "
          f"{OUT / 'act_approved.pdf'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
