"""Тесты бессерверной функции `api/summary.py`.

Лендинг показывает её ответ рядом с числами из выгрузки, поэтому здесь
сверяется, что агрегаты посчитаны из тех же данных и не несут координат.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    """Импорт по пути: `api` — не пакет, обычным import его не достать."""
    spec = importlib.util.spec_from_file_location("vantage_api_summary", ROOT / "api/summary.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def summary():
    server = HTTPServer(("127.0.0.1", 0), _load().handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/summary"
        with urllib.request.urlopen(url, timeout=5) as r:
            yield r.headers.get("Content-Type"), json.loads(r.read())
    finally:
        server.shutdown()


@pytest.fixture
def items():
    return json.loads((ROOT / "api/candidates.json").read_text(encoding="utf-8"))


def test_aggregates_come_from_the_export(summary, items):
    content_type, body = summary
    dumps = [c for c in items if c.get("visual_check") == "landfill"]
    assert content_type == "application/json"
    assert body["objects"] == len(items)
    assert body["confirmed"] == len(dumps)
    assert body["damage_p50_kzt"] == sum(c.get("damage_p50", 0) for c in dumps)


def test_no_coordinates_leak(summary):
    _, body = summary
    assert set(body) == {"objects", "confirmed", "damage_p50_kzt"}
