"""Экзамен точности: каждый объект в нём один раз.

Объекты на стыке областей попадали в набор дважды — из двух прогонов, — и
свалка C00061 считалась за две. На семи свалках один дубль сдвигает любую
метрику на проценты, и сдвигает в сторону «модель лучше, чем есть».
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "data/eval/labeled.geojson"

pytestmark = pytest.mark.skipif(not EVAL.exists(), reason="экзамен не собран")


@pytest.fixture(scope="module")
def frame():
    gpd = pytest.importorskip("geopandas")
    return gpd.read_file(EVAL).to_crs(32642)


def test_no_object_counted_twice_across_areas(frame):
    import geopandas as gpd

    pairs = gpd.sjoin(frame[["candidate_id", "area", "geometry"]],
                      frame[["candidate_id", "area", "geometry"]], predicate="intersects")
    cross = pairs[pairs["area_left"] != pairs["area_right"]]
    assert cross.empty, f"один объект в двух областях: {cross[['candidate_id_left', 'candidate_id_right']].values[:5]}"


def test_keys_are_unique(frame):
    assert frame["candidate_id"].is_unique


def test_truth_values_are_known(frame):
    assert set(frame["truth"]) <= {"landfill", "not_landfill", "unclear"}


def test_published_landfills_are_all_in_the_exam(frame):
    """Каждая свалка с сайта — в экзамене: иначе он мерит не тот продукт."""
    site = ROOT / "web-next/public/data/candidates.geojson"
    if not site.exists():
        pytest.skip("выгрузки нет")
    published = {f["properties"]["candidate_id"] for f in
                 json.loads(site.read_text(encoding="utf-8"))["features"]
                 if f["properties"].get("visual_check") == "landfill"}
    in_exam = {c.split(":", 1)[1] for c in frame.loc[frame["truth"] == "landfill", "candidate_id"]
               if c.startswith("real:")}
    assert published <= in_exam
