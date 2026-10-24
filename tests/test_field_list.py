"""Поездки в листе выезда: кучи точек — в одну поездку, объезд без петель."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("field_list", ROOT / "scripts/field_list.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_far_points_make_separate_trips():
    fl = _load()
    xy = [(0, 0), (500, 0), (1000, 0), (50_000, 0)]
    plan = fl.trips(xy, gap_m=2000, start=(0, 0))
    assert plan == [[0, 1, 2], [3]]


def test_chain_of_close_points_is_one_trip():
    """Концы цепочки дальше порога, но каждый шаг короче — одна поездка."""
    fl = _load()
    xy = [(0, 0), (1500, 0), (3000, 0), (4500, 0)]
    assert len(fl.trips(xy, gap_m=2000, start=(0, 0))) == 1


def test_route_starts_nearest_to_start_and_visits_all():
    fl = _load()
    xy = [(3000, 0), (0, 0), (1000, 0), (2000, 0)]
    (route,) = fl.trips(xy, gap_m=2000, start=(-100, 0))
    assert route == [1, 2, 3, 0]


def test_route_link_puts_last_point_as_destination():
    fl = _load()
    url = fl.route_link([(51.1, 71.4), (51.2, 71.5), (51.3, 71.6)])
    query = parse_qs(urlparse(url).query)
    assert query["destination"] == ["51.30000,71.60000"]
    assert query["waypoints"] == ["51.10000,71.40000|51.20000,71.50000"]
    assert query["api"] == ["1"]


def test_single_point_link_has_no_waypoints():
    fl = _load()
    assert "waypoints" not in fl.route_link([(51.1, 71.4)])
