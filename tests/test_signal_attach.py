"""Тесты подключения радара, тепла и полимеров к найденным объектам.

Модули ``sar`` и ``thermal`` умели считать свои признаки с самого начала,
но в решение они не попадали: панель доказательств на карте читала поля
``sar_incoherence``, ``pmli_response`` и ``thermal_anomaly``, которых у
настоящих кандидатов не было ни одного. То есть на синтетике панель была
полной, а на честном прогоне — пустой.

Здесь проверяется то, что делается без сети: сведение растра к объектам,
разбиение на блоки под память и извлечение отклика полимеров из уже
нарезанных чипов.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
import rasterio.transform
from shapely.geometry import box

from vantage.chips import ChipDataset
from vantage.config import load_settings
from vantage.signals import (
    SAR_BLOCK_M,
    SignalReport,
    _spatial_blocks,
    bounding_aoi,
    pmli_response_from_chips,
    zonal_median,
)

CRS = "EPSG:32642"


@pytest.fixture(scope="module")
def settings():
    return load_settings()


def grid_transform(origin_x=0.0, origin_y=100.0, pixel=10.0):
    return rasterio.transform.from_origin(origin_x, origin_y, pixel, pixel)


class TestZonalMedian:
    def test_takes_median_inside_the_polygon(self):
        values = np.zeros((10, 10), dtype="float32")
        values[0:3, 0:3] = 5.0
        candidates = gpd.GeoDataFrame({"geometry": [box(0, 70, 30, 100)]}, crs=CRS)

        result = zonal_median(candidates, values, grid_transform(), crs=CRS)
        assert result[0] == pytest.approx(5.0)

    def test_ignores_values_outside(self):
        values = np.zeros((10, 10), dtype="float32")
        values[5:, 5:] = 99.0
        candidates = gpd.GeoDataFrame({"geometry": [box(0, 70, 30, 100)]}, crs=CRS)

        assert zonal_median(candidates, values, grid_transform(), crs=CRS)[0] == pytest.approx(0.0)

    def test_median_is_robust_to_one_edge_pixel(self):
        """Край объекта всегда смешанный — один пиксель фона не должен решать."""
        values = np.full((10, 10), 2.0, dtype="float32")
        values[0, 0] = 1000.0
        candidates = gpd.GeoDataFrame({"geometry": [box(0, 70, 30, 100)]}, crs=CRS)

        assert zonal_median(candidates, values, grid_transform(), crs=CRS)[0] == pytest.approx(2.0)

    def test_all_nan_area_gives_nan_not_zero(self):
        """Пустая ячейка честнее нуля: ноль читается как измеренное значение."""
        values = np.full((10, 10), np.nan, dtype="float32")
        candidates = gpd.GeoDataFrame({"geometry": [box(0, 70, 30, 100)]}, crs=CRS)

        assert np.isnan(zonal_median(candidates, values, grid_transform(), crs=CRS)[0])

    def test_object_outside_the_raster_gives_nan(self):
        values = np.ones((10, 10), dtype="float32")
        candidates = gpd.GeoDataFrame({"geometry": [box(5000, 5000, 5030, 5030)]}, crs=CRS)

        assert np.isnan(zonal_median(candidates, values, grid_transform(), crs=CRS)[0])


class TestSpatialBlocks:
    def test_neighbours_land_in_one_block(self):
        candidates = gpd.GeoDataFrame(
            {"geometry": [box(0, 0, 30, 30), box(100, 100, 130, 130)]}, crs=CRS
        )
        assert len(_spatial_blocks(candidates, SAR_BLOCK_M)) == 1

    def test_distant_objects_are_split(self):
        """Разбиение нужно ровно затем, чтобы куб влезал в память.

        Sentinel-1 за восемь лет — около пятисот проходов; куб 20x20 км
        в двух поляризациях весит порядка четырёх гигабайт, блок 5x5 км —
        около двухсот пятидесяти мегабайт.
        """
        candidates = gpd.GeoDataFrame(
            {"geometry": [box(0, 0, 30, 30), box(40_000, 40_000, 40_030, 40_030)]}, crs=CRS
        )
        blocks = _spatial_blocks(candidates, SAR_BLOCK_M)
        assert len(blocks) == 2
        assert sorted(len(b) for b in blocks) == [1, 1]

    def test_every_object_appears_exactly_once(self):
        rng = np.random.default_rng(0)
        xs = rng.uniform(0, 30_000, 40)
        ys = rng.uniform(0, 30_000, 40)
        candidates = gpd.GeoDataFrame(
            {"geometry": [box(x, y, x + 20, y + 20) for x, y in zip(xs, ys, strict=True)]},
            crs=CRS,
        )
        positions = [i for block in _spatial_blocks(candidates, SAR_BLOCK_M) for i in block]
        assert sorted(positions) == list(range(40))


class TestBoundingAoi:
    def test_covers_all_objects_with_margin(self, settings):
        candidates = gpd.GeoDataFrame(
            {"geometry": [box(400_000, 5_660_000, 400_100, 5_660_100)]},
            crs=settings.project.crs_working,
        )
        area = bounding_aoi(candidates, settings, margin_m=1000.0)
        assert area.area_km2 > 4.0  # 2.1 x 2.1 км за вычетом искажений проекции

    def test_empty_input_is_an_error(self, settings):
        empty = gpd.GeoDataFrame(
            {"geometry": []}, geometry="geometry", crs=settings.project.crs_working
        )
        with pytest.raises(ValueError, match="нет кандидатов"):
            bounding_aoi(empty, settings, margin_m=1000.0)


def chips_with_pmli(before_value: float, after_value: float, size: int = 64) -> ChipDataset:
    channels = ["B02", "B03", "B04", "B08", "B11", "B12", "ndvi", "bsi", "pmli"]
    shape = (1, len(channels), size, size)
    before = np.zeros(shape, dtype="float32")
    after = np.zeros(shape, dtype="float32")
    before[0, channels.index("pmli")] = before_value
    after[0, channels.index("pmli")] = after_value
    return ChipDataset(
        before=before, after=after, candidate_ids=["tile:C00000"], channels=channels
    )


class TestPmliFromChips:
    def test_growth_of_polymer_response_is_positive(self):
        result = pmli_response_from_chips(chips_with_pmli(0.02, 0.11))
        assert result["tile:C00000"] == pytest.approx(0.09, abs=1e-5)

    def test_no_change_gives_zero(self):
        result = pmli_response_from_chips(chips_with_pmli(0.05, 0.05))
        assert result["tile:C00000"] == pytest.approx(0.0, abs=1e-6)

    def test_edge_of_the_window_does_not_dilute_the_signal(self):
        """Признак считается по центру чипа, а не по всему окну.

        Окно 64 пикселя — это 640 метров; у объекта площадью 1500 м²
        внутрь попадает в основном фон, и усреднение по всему окну
        систематически занижало бы признак у мелких объектов.
        """
        chips = chips_with_pmli(0.0, 0.0)
        channel = chips.channels.index("pmli")
        chips.after[0, channel] = 0.0
        chips.after[0, channel, 24:40, 24:40] = 0.20  # отклик только в центре

        result = pmli_response_from_chips(chips)
        assert result["tile:C00000"] == pytest.approx(0.20, abs=1e-5)

    def test_missing_channel_is_an_explicit_error(self):
        chips = chips_with_pmli(0.0, 0.1)
        chips.channels = [c if c != "pmli" else "nbr" for c in chips.channels]
        with pytest.raises(KeyError, match="pmli"):
            pmli_response_from_chips(chips)


class TestSignalReport:
    def test_text_mentions_every_branch(self):
        report = SignalReport(sar_covered=3, thermal_covered=7, pmli_covered=9, total=10)
        text = report.to_text()
        assert "10" in text and "радар 3" in text and "тепло 7" in text

    def test_failures_are_visible_not_swallowed(self):
        report = SignalReport(total=4, notes=["радар: таймаут"])
        assert "таймаут" in report.to_text()
