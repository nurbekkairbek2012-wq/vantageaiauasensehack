"""Запись выезда: с кадром у объекта — записывается, без него — нет.

Реальные ground_truth.json и data/field в тесте не трогаются: пути модуля
подменяются временной папкой.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytest.importorskip("PIL")
pytest.importorskip("geopandas")

LAT, LON = 51.144466, 71.539746


@pytest.fixture()
def tool(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("add_field_visit_ut",
                                                  ROOT / "scripts/add_field_visit.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "SOURCE", tmp_path / "ground_truth.json")
    monkeypatch.setattr(module, "PHOTOS", tmp_path / "field")

    import geopandas as gpd
    from shapely.geometry import box

    d = 0.0003
    site = gpd.GeoDataFrame({"candidate_id": ["C00061"], "break_date": ["2024-05-01"]},
                            geometry=[box(LON - d, LAT - d, LON + d, LAT + d)], crs=4326)
    site_path = tmp_path / "site.geojson"
    site.to_file(site_path, driver="GeoJSON")
    return module, tmp_path, site_path


def _photo(path: Path, lat, lon, taken="2026:09:20 12:00:00"):
    from PIL import Image
    from PIL.TiffImagePlugin import IFDRational

    def dms(v):
        v = abs(v)
        d = int(v)
        m = int((v - d) * 60)
        s = (v - d - m / 60) * 3600
        return (IFDRational(d), IFDRational(m), IFDRational(round(s * 1000), 1000))

    exif = Image.Exif()
    if lat is not None:
        gps = exif.get_ifd(0x8825)
        gps[1], gps[2], gps[3], gps[4] = "N", dms(lat), "E", dms(lon)
    exif.get_ifd(0x8769)[36867] = taken
    Image.new("RGB", (8, 8)).save(path, exif=exif)
    return path


def test_visit_with_a_located_photo_is_recorded(tool):
    module, tmp, site = tool
    photo = _photo(tmp / "a.jpg", LAT, LON)
    code = module.main(["C00061", "--verdict", "свалка", "--by", "Инспектор",
                        "--photos", str(photo), "--date", "2026-09-21", "--site", str(site)])
    assert code == 0
    records = json.loads((tmp / "ground_truth.json").read_text(encoding="utf-8"))
    assert records[0]["candidate_id"] == "C00061"
    assert "координатами: 1" in records[0]["evidence"]
    assert (tmp / "field" / "C00061" / "a.jpg").exists()


def test_visit_without_coordinates_is_not_recorded(tool):
    module, tmp, site = tool
    photo = _photo(tmp / "b.jpg", None, None)
    code = module.main(["C00061", "--verdict", "свалка", "--by", "Инспектор",
                        "--photos", str(photo), "--date", "2026-09-21", "--site", str(site)])
    assert code == 2
    assert not (tmp / "ground_truth.json").exists()
    assert not (tmp / "field" / "C00061").exists()


def test_photo_from_elsewhere_is_not_recorded(tool):
    module, tmp, site = tool
    photo = _photo(tmp / "c.jpg", LAT + 0.02, LON)
    code = module.main(["C00061", "--verdict", "свалка", "--by", "Инспектор",
                        "--photos", str(photo), "--date", "2026-09-21", "--site", str(site)])
    assert code == 2
    assert not (tmp / "ground_truth.json").exists()
