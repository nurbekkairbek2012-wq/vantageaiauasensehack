"""Экзамен: все объекты с вердиктом человека, одной таблицей.

── Зачем ───────────────────────────────────────────────────────────────

Каждое улучшение точности — фильтр, модель, новый источник снимков —
надо проверять на одном и том же наборе и одним и тем же правилом. Иначе
каждый опыт мерится по-своему, и «стало лучше» нельзя отличить от
«померили на другом».

Здесь набор собирается один раз из того, что уже есть:

  * прогоны пяти областей вокруг Астаны (outputs_*/candidates.geojson);
  * вердикты человека по снимку 0,4–0,8 м (labels_manual.geojson);
  * восточный пояс: все 33 находки просмотрены и все ложные
    (docs/BELTS.md), но в labels_manual не внесены — они идут как
    «не свалка» с пометкой источника.

── Почему не полигоны ТБО из OpenStreetMap ─────────────────────────────

Казалось естественным взять 745 известных полигонов из OSM как экзамен
детектора: «система обязана их находить». Для детектора это нечестная
проверка: почти все легальные полигоны существовали до 2018 года, а
детектор ищет разрыв ВНУТРИ архива 2018–2026. Полигон, которого не было
видно появившимся, детектор не обязан находить, и промах там ничего не
говорит о качестве. Для проверки зрения по снимку эти полигоны годятся и
используются отдельно.

── Правило экзамена ────────────────────────────────────────────────────

Улучшение засчитывается, если оно:

  1. не теряет НИ ОДНОЙ свалки из опознанных человеком;
  2. снимает как можно больше не-свалок;
  3. держится внутри одной области (северное кольцо), а не только на
     смеси областей — см. scripts/check_triage.py.

Объекты «не разобрать» в счёт не идут — правды о них никто не знает, —
но число снятых среди них печатается: снять неясный объект значит,
возможно, снять свалку.

    python scripts/build_eval_set.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/eval/labeled.geojson"

AREAS = ("outputs_real", "outputs_astana_east", "outputs_astana_industrial_west",
         "outputs_astana_south", "outputs_astana_southeast")
#: Области, просмотренные целиком и целиком ложные, но без записей в разметке.
ALL_NEGATIVE = {"outputs_astana_east"}
CODES = {"свалка": "landfill", "не свалка": "not_landfill", "не понятно": "unclear"}

#: Колонки, которые уходят в набор. Остальное — внутреннее состояние прогона.
KEEP = ("candidate_id", "area", "truth", "truth_source", "area_m2", "age_years",
        "break_date", "n_pieces", "dist_road_m", "dist_settlement_m", "verify_texture",
        "ndvi_drop", "bsi_rise", "pmli_response", "sar_incoherence", "thermal_anomaly",
        "highres_score", "evidence_score", "geometry")


def build():
    import geopandas as gpd
    import pandas as pd

    labels = gpd.read_file(ROOT / "labels_manual.geojson")
    frames = []
    for area in AREAS:
        path = ROOT / area / "candidates.geojson"
        if not path.exists():
            continue
        g = gpd.read_file(path)
        g["area"] = area
        frames.append(g)
    data = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)

    joined = gpd.sjoin(data, labels[["verdict", "geometry"]].to_crs(data.crs),
                       predicate="intersects", how="left")
    joined = joined[~joined.index.duplicated(keep="first")].drop(columns="index_right")
    joined["truth"] = joined["verdict"].map(CODES)
    joined["truth_source"] = joined["truth"].map(lambda v: "labels_manual" if v else None)
    blank = joined["area"].isin(ALL_NEGATIVE) & joined["truth"].isna()
    joined.loc[blank, "truth"] = "not_landfill"
    joined.loc[blank, "truth_source"] = "BELTS.md: пояс просмотрен целиком"
    joined = joined[joined["truth"].notna()].copy()

    # Области перекрываются по краям, и один объект попадает в экзамен
    # дважды — из двух прогонов. Так свалка C00061 северного кольца жила
    # в наборе ещё и как C00056 южного пояса: «восемь свалок» были семью,
    # а одинаковые оценки двух строк честно выдавали дубль. Объект
    # остаётся в первой области по порядку AREAS (северное кольцо первое).
    # Пересекающиеся объекты ВНУТРИ одного прогона — разные находки, и они
    # остаются: на сайте это тоже два объекта.
    metric = joined.to_crs(32642)
    kept_idx, taken = [], []
    for area in AREAS:
        part = metric[metric["area"] == area]
        for idx, geom in zip(part.index, part.geometry, strict=True):
            if any(geom.intersects(other) for other in taken):
                continue
            kept_idx.append(idx)
        taken.extend(part.loc[[i for i in part.index if i in set(kept_idx)]].geometry)
    joined = joined.loc[kept_idx]
    # Номера объектов сквозные только внутри области: ключ — область + номер.
    joined["candidate_id"] = joined["area"].str.replace("outputs_", "") + ":" + \
        joined["candidate_id"].astype(str)
    cols = [c for c in KEEP if c in joined.columns]
    return gpd.GeoDataFrame(joined[cols], crs=data.crs).to_crs(4326)


def main() -> int:
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data.to_file(OUT, driver="GeoJSON")
    print(f"── Экзамен: {len(data)} объектов → {OUT.relative_to(ROOT)}")
    print(data.groupby(["area", "truth"]).size().unstack(fill_value=0).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
