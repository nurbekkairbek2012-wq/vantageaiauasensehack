"""Выезд засчитывается только с фотографией, снятой у объекта.

── Что здесь проверяется ───────────────────────────────────────────────

Запись «был на месте» с именем и датой, но без кадра, раньше принималась
и уходила на сайт зелёной меткой «подтверждено». Проверить её было
нечем. Теперь scripts/attach_ground.py принимает выезд, только если в
data/field/<номер>/ лежит фотография с координатами в EXIF рядом с
контуром объекта и с датой, не противоречащей записи.

Каждый тест — один способ выдать непроверенное за проверенное: запись
без кадра, кадр без координат, кадр из другого места, кадр, снятый
после даты записи или до появления объекта.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

PIL = pytest.importorskip("PIL")
pytest.importorskip("pyproj")
pytest.importorskip("shapely")


@pytest.fixture(scope="module")
def ground():
    spec = importlib.util.spec_from_file_location(
        "attach_ground_under_test", ROOT / "scripts/attach_ground.py")
    module = importlib.util.module_from_spec(spec)
    # dataclass ищет свой модуль в sys.modules; загруженный по пути модуль
    # туда сам не попадает.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Объект — квадрат 40 × 40 м под Астаной, в UTM 42N.
LAT, LON = 51.144466, 71.539746


@pytest.fixture(scope="module")
def shape():
    from pyproj import Transformer
    from shapely.geometry import box

    x, y = Transformer.from_crs(4326, 32642, always_xy=True).transform(LON, LAT)
    return box(x - 20, y - 20, x + 20, y + 20)


def _dms(value: float):
    from PIL.TiffImagePlugin import IFDRational

    value = abs(value)
    d = int(value)
    m = int((value - d) * 60)
    s = (value - d - m / 60) * 3600
    return (IFDRational(d), IFDRational(m), IFDRational(round(s * 1000), 1000))


def _photo(folder: Path, name: str, lat: float | None, lon: float | None,
           taken: str | None = "2026:09:20 12:30:00") -> Path:
    from PIL import Image

    folder.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    if lat is not None and lon is not None:
        gps = exif.get_ifd(0x8825)
        gps[1] = "N" if lat >= 0 else "S"
        gps[2] = _dms(lat)
        gps[3] = "E" if lon >= 0 else "W"
        gps[4] = _dms(lon)
    if taken:
        exif.get_ifd(0x8769)[36867] = taken
    path = folder / name
    Image.new("RGB", (16, 16), "gray").save(path, exif=exif)
    return path


RECORD = {"candidate_id": "C00061", "verdict": "свалка",
          "by": "Инспектор", "date": "2026-09-21"}


def _check(ground, shape, root, record=RECORD, **kw):
    return ground.check_evidence(record, shape, max_distance_m=150, root=root,
                                 break_date=kw.get("break_date", date(2024, 5, 1)))


def test_exif_coordinates_are_read_back(ground, tmp_path):
    path = _photo(tmp_path / "C00061", "a.jpg", LAT, LON)
    facts = ground.read_photo(path)
    assert facts.lat == pytest.approx(LAT, abs=1e-5)
    assert facts.lon == pytest.approx(LON, abs=1e-5)
    assert facts.taken == date(2026, 9, 20)


def test_photo_at_the_object_is_accepted(ground, shape, tmp_path):
    _photo(tmp_path / "C00061", "a.jpg", LAT, LON)
    ev = _check(ground, shape, tmp_path)
    assert ev.photos == 1 and ev.located == 1
    assert ev.nearest_m == pytest.approx(0.0, abs=1.0)


def test_no_photo_means_no_confirmation(ground, shape, tmp_path):
    """Имя и дата без кадра — утверждение, а не проверка."""
    ev = _check(ground, shape, tmp_path)
    assert ev.photos == 0 and ev.located == 0


def test_photo_without_coordinates_is_not_counted(ground, shape, tmp_path):
    """Мессенджер вырезал EXIF — кадр есть, привязки к месту нет."""
    _photo(tmp_path / "C00061", "a.jpg", None, None)
    ev = _check(ground, shape, tmp_path)
    assert ev.photos == 1 and ev.located == 0
    assert "нет координат" in ev.problems[0]


def test_photo_from_elsewhere_is_rejected(ground, shape, tmp_path):
    """Кадр в километре от объекта доказывает только, что где-то есть куча."""
    _photo(tmp_path / "C00061", "a.jpg", LAT + 0.01, LON)
    ev = _check(ground, shape, tmp_path)
    assert ev.located == 0
    assert ev.nearest_m > 1000
    assert "от объекта" in ev.problems[0]


def test_photo_taken_after_the_record_is_rejected(ground, shape, tmp_path):
    """Нельзя подтвердить 21 сентября кадром, снятым в октябре."""
    _photo(tmp_path / "C00061", "a.jpg", LAT, LON, taken="2026:10:05 10:00:00")
    ev = _check(ground, shape, tmp_path)
    assert ev.located == 0
    assert "позже даты записи" in ev.problems[0]


def test_photo_older_than_the_object_is_rejected(ground, shape, tmp_path):
    """Кадр 2020 года не показывает свалку, возникшую в 2024-м."""
    _photo(tmp_path / "C00061", "a.jpg", LAT, LON, taken="2020:06:01 10:00:00")
    ev = _check(ground, shape, tmp_path)
    assert ev.located == 0
    assert "раньше, чем возник" in ev.problems[0]


def test_one_good_photo_is_enough_among_bad_ones(ground, shape, tmp_path):
    folder = tmp_path / "C00061"
    _photo(folder, "a.jpg", None, None)
    _photo(folder, "b.jpg", LAT + 0.01, LON)
    _photo(folder, "c.jpg", LAT, LON)
    ev = _check(ground, shape, tmp_path)
    assert ev.photos == 3 and ev.located == 1


def test_default_rule_requires_a_located_photo():
    """Правило живёт в конфиге, и по умолчанию без кадра выезда нет."""
    sys.path.insert(0, str(ROOT / "src"))
    from vantage.config import load_settings

    rules = load_settings().field_check
    assert rules.min_located_photos >= 1
    assert 0 < rules.max_photo_distance_m <= 500


def test_published_ground_marks_have_photo_evidence():
    """Ни один объект на сайте не помечен выездом без засчитанного кадра."""
    path = ROOT / "web-next/public/data/candidates.geojson"
    if not path.exists():
        pytest.skip("выгрузки нет")
    for feature in json.loads(path.read_text(encoding="utf-8"))["features"]:
        p = feature.get("properties") or {}
        if p.get("check_source") == "ground" or p.get("ground_check"):
            assert (p.get("ground_photos_located") or 0) >= 1, (
                f"{p.get('candidate_id')}: помечен выездом без фотографии с координатами"
            )
