"""Экзамен машинного просмотра: считает то, что заявлено, и там, где надо.

Проверяются два места, где ошибка тихая:

  * контур объекта на снимке — если он уедет, модель будет судить о
    соседнем участке, и никакая метрика этого не покажет;
  * подсчёт экзамена — отказ по опознанной свалке обязан попасть в
    «свалок среди отказов», а не раствориться в общей доле.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
pytest.importorskip("PIL")
pytest.importorskip("geopandas")


@pytest.fixture(scope="module")
def gs():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("gemini_screen_ut",
                                                  ROOT / "scripts/gemini_screen.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_releases_are_sorted_by_date(gs):
    dates = [d for d, _ in gs.releases()]
    assert dates == sorted(dates) and len(dates) >= 2


def test_outline_is_drawn_around_the_centre(gs):
    """Объект в центре окна — и красный контур обязан быть в центре."""
    from shapely.geometry import box

    lat, lon, zoom = 51.1444, 71.5397, 18
    d = 0.0002
    image = np.zeros((768, 768, 3), dtype="uint8")
    out = gs.outline(image, box(lon - d, lat - d, lon + d, lat + d), lat, lon, zoom)
    red = np.argwhere((out[..., 0] > 200) & (out[..., 1] < 80))
    assert len(red) > 0
    cy, cx = red.mean(axis=0)
    # Центральный тайл сетки 3×3 — от 256 до 512 пикселей.
    assert 256 <= cx <= 512 and 256 <= cy <= 512


def test_exam_counts_a_rejected_dump(gs):
    import geopandas as gpd
    from shapely.geometry import Point

    frame = gpd.GeoDataFrame({
        "candidate_id": ["a", "b", "c", "d"],
        "area": ["outputs_real"] * 4,
        "truth": ["landfill", "landfill", "not_landfill", "unclear"],
    }, geometry=[Point(0, 0)] * 4, crs=4326)
    answers = {
        "a": {"verdict": "dump", "confidence": 0.9},
        "b": {"verdict": "not_dump", "confidence": 0.8},   # ошибка: отвергнута свалка
        "c": {"verdict": "not_dump", "confidence": 0.9},
        "d": {"verdict": "not_dump", "confidence": 0.6},
    }
    result = gs.examine(frame, answers, "test")["all"]
    assert result["rejected"] == 3
    assert result["rejected_landfill"] == 1
    assert result["rejected_unclear"] == 1
    assert result["dumps"] == 2 and result["dumps_called_dump"] == 1


def test_skipped_objects_are_not_counted(gs):
    import geopandas as gpd
    from shapely.geometry import Point

    frame = gpd.GeoDataFrame({"candidate_id": ["a"], "area": ["x"], "truth": ["landfill"]},
                             geometry=[Point(0, 0)], crs=4326)
    answers = {"a": {"verdict": "unclear", "confidence": 0.0, "skipped": True}}
    assert gs.examine(frame, answers, "test") == {}


def test_prompt_is_fixed_before_the_exam(gs):
    """Вопрос не должен знать ответов: в нём нет номеров объектов экзамена."""
    import re

    assert not re.search(r"C\d{5}", gs.CONTEXT)
