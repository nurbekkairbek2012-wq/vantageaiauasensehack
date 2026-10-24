"""Тесты области интереса и проекций.

Главный класс ошибок, который здесь ловится, — смешение градусов и метров.
Один буфер, посчитанный в градусах, тихо ломает весь контекстный отсев:
0.003 «метра» вместо 300 превращает фильтр по дорогам в фильтр «нигде».
"""

from __future__ import annotations

import json

import pytest
from shapely.geometry import box

from vantage.aoi import AOI, reproject_geometry, utm_crs_for

ASTANA_BBOX = (70.90, 50.88, 72.05, 51.42)
UTM_42N = "EPSG:32642"


@pytest.fixture
def astana() -> AOI:
    return AOI.from_bbox(ASTANA_BBOX, name="astana", crs_working=UTM_42N)


class TestArea:
    def test_area_is_in_square_kilometres(self, astana: AOI):
        # bbox ~1.15° по долготе и ~0.54° по широте на широте 51°
        # даёт порядка 5 тысяч км². Проверяем именно порядок величины:
        # если бы площадь считалась в градусах, вышло бы ~0.6.
        assert 3_000 < astana.area_km2 < 8_000

    def test_area_not_computed_in_degrees(self, astana: AOI):
        degrees_area = astana.geometry.area  # площадь в квадратных градусах
        assert astana.area_km2 > degrees_area * 1000


class TestBuffer:
    def test_buffer_grows_area(self, astana: AOI):
        buffered = astana.buffer_m(1000)
        assert buffered.area_km2 > astana.area_km2

    def test_buffer_distance_is_metric(self, astana: AOI):
        """Буфер 5 км должен добавлять примерно 5 км по краю, а не 5 градусов."""
        buffered = astana.buffer_m(5000)
        min_lon_before = astana.bbox[0]
        min_lon_after = buffered.bbox[0]
        delta_deg = min_lon_before - min_lon_after
        # 5 км по долготе на широте ~51° — это примерно 0.07°
        assert 0.03 < delta_deg < 0.12


class TestTiles:
    def test_tiles_cover_aoi(self, astana: AOI):
        tiles = astana.tiles(tile_size_m=20_000)
        assert len(tiles) > 1
        total = sum(t.area_km2 for t in tiles)
        # Сумма площадей плиток без перекрытия ≈ площадь области
        assert total == pytest.approx(astana.area_km2, rel=0.02)

    def test_overlap_increases_total_area(self, astana: AOI):
        plain = sum(t.area_km2 for t in astana.tiles(20_000))
        overlapped = sum(t.area_km2 for t in astana.tiles(20_000, overlap_m=2_000))
        assert overlapped > plain

    def test_rejects_invalid_overlap(self, astana: AOI):
        with pytest.raises(ValueError):
            astana.tiles(tile_size_m=1000, overlap_m=1000)


class TestGeoJSON:
    def test_reads_feature_collection(self, tmp_path):
        path = tmp_path / "aoi.geojson"
        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": json.loads(json.dumps(box(*ASTANA_BBOX).__geo_interface__)),
                }
            ],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        aoi = AOI.from_geojson(path, name="from_file", crs_working=UTM_42N)
        assert aoi.bbox == pytest.approx(ASTANA_BBOX)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            AOI.from_geojson(tmp_path / "нет.geojson", name="x", crs_working=UTM_42N)


class TestProjections:
    def test_roundtrip_is_stable(self, astana: AOI):
        metric = reproject_geometry(astana.geometry, "EPSG:4326", UTM_42N)
        back = reproject_geometry(metric, UTM_42N, "EPSG:4326")
        assert back.bounds == pytest.approx(astana.geometry.bounds, abs=1e-6)

    def test_utm_zone_for_astana(self):
        assert utm_crs_for(71.45, 51.17) == UTM_42N

    def test_utm_zone_for_almaty_differs(self):
        # Алматы — зона 43N. Жёстко зашитая 42N исказила бы площади.
        assert utm_crs_for(76.9, 43.2) == "EPSG:32643"

    def test_southern_hemisphere(self):
        assert utm_crs_for(71.45, -51.17) == "EPSG:32742"
