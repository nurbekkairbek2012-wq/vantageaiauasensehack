"""Тесты векторизации кандидатов.

Проверяется главным образом геометрическая корректность: площадь в метрах,
правильная привязка, устойчивость к шуму. Ошибка здесь не падает с
исключением — она тихо выдаёт полигоны не в том месте или не того размера,
и обнаруживается только когда кто-то приедет на координату и ничего там
не найдёт.
"""

from __future__ import annotations

import numpy as np
import pytest
import rasterio.transform

from vantage.candidates import RasterGrid, build_candidates, clean_mask, polygonize, to_geojson
from vantage.change import BreakpointResult
from vantage.config import CandidatesCfg, load_settings

UTM = "EPSG:32642"
PIXEL_M = 10.0

CFG = CandidatesCfg(opening_iterations=1, closing_iterations=2, simplify_tolerance_m=5.0)


def make_grid(ny: int = 60, nx: int = 60) -> RasterGrid:
    """Растр 10 м/пиксель с началом в удобной точке UTM 42N."""
    transform = rasterio.transform.from_origin(
        west=350_000.0, north=5_670_000.0, xsize=PIXEL_M, ysize=PIXEL_M
    )
    return RasterGrid(transform=tuple(transform)[:6], crs=UTM, shape=(ny, nx))


def make_result(mask: np.ndarray, *, break_index: int = 28) -> BreakpointResult:
    """Собрать BreakpointResult из готовой маски."""
    flat = mask.ravel()
    n = flat.size
    rng = np.random.default_rng(0)
    return BreakpointResult(
        has_break=flat,
        break_index=np.where(flat, break_index, -1).astype("int32"),
        zscore=np.where(flat, rng.uniform(3.0, 8.0, n), rng.uniform(0.0, 2.0, n)).astype("float32"),
        ndvi_before=np.full(n, 0.35, dtype="float32"),
        ndvi_after=np.where(flat, 0.08, 0.33).astype("float32"),
        ndvi_drop=np.where(flat, 0.27, 0.02).astype("float32"),
        bsi_rise=np.where(flat, 0.18, 0.01).astype("float32"),
        recovered=np.zeros(n, dtype=bool),
        n_valid=np.full(n, 56, dtype="int32"),
    )


def dates_axis(n: int = 56) -> np.ndarray:
    return np.array(
        [f"{y}-{m:02d}-15" for y in range(2018, 2026) for m in (4, 5, 6, 7, 8, 9, 10)],
        dtype="datetime64[D]",
    )[:n]


# --------------------------------------------------------------------------- #


class TestRasterGrid:
    def test_pixel_area(self):
        assert make_grid().pixel_area_m2 == pytest.approx(PIXEL_M**2)


class TestCleanMask:
    def test_removes_isolated_pixels(self):
        """Одиночное срабатывание — статистический шум, а не свалка."""
        mask = np.zeros((40, 40), dtype=bool)
        mask[5, 5] = True
        assert not clean_mask(mask, CFG).any()

    def test_keeps_solid_blob(self):
        mask = np.zeros((40, 40), dtype=bool)
        mask[10:20, 10:20] = True
        assert clean_mask(mask, CFG).sum() > 50

    def test_fills_holes_from_cloud_gaps(self):
        """Дырка внутри пятна — пиксель, который не прошёл порог из-за облаков."""
        mask = np.zeros((40, 40), dtype=bool)
        mask[10:22, 10:22] = True
        mask[15, 15] = False
        assert clean_mask(mask, CFG)[15, 15]


class TestPolygonize:
    def test_area_matches_pixel_count(self):
        grid = make_grid()
        mask = np.zeros(grid.shape, dtype=bool)
        mask[10:20, 10:20] = True  # 100 пикселей по 100 м²
        gdf = polygonize(mask, grid)
        assert len(gdf) == 1
        assert gdf["area_m2"].iat[0] == pytest.approx(10_000, rel=0.01)

    def test_separate_blobs_become_separate_polygons(self):
        grid = make_grid()
        mask = np.zeros(grid.shape, dtype=bool)
        mask[5:12, 5:12] = True
        mask[40:50, 40:50] = True
        assert len(polygonize(mask, grid)) == 2

    def test_geometry_is_georeferenced(self):
        """Полигон должен лежать там, где реально находится объект."""
        grid = make_grid()
        mask = np.zeros(grid.shape, dtype=bool)
        mask[0:10, 0:10] = True  # северо-западный угол
        gdf = polygonize(mask, grid)
        min_x, _, _, max_y = gdf.total_bounds
        assert min_x == pytest.approx(350_000.0)
        assert max_y == pytest.approx(5_670_000.0)

    def test_empty_mask_gives_empty_frame(self):
        grid = make_grid()
        gdf = polygonize(np.zeros(grid.shape, dtype=bool), grid)
        assert gdf.empty
        assert gdf.crs.to_string() == UTM

    def test_simplify_preserves_validity(self):
        grid = make_grid()
        mask = np.zeros(grid.shape, dtype=bool)
        mask[10:30, 10:12] = True  # тонкий вытянутый объект
        gdf = polygonize(mask, grid, simplify_tolerance_m=5.0)
        assert all(g.is_valid for g in gdf.geometry)


class TestBuildCandidates:
    @pytest.fixture
    def settings(self):
        return load_settings()

    def test_builds_expected_number_of_candidates(self, settings):
        grid = make_grid()
        mask = np.zeros(grid.shape, dtype=bool)
        mask[10:20, 10:20] = True
        mask[35:48, 35:48] = True
        gdf = build_candidates(make_result(mask), grid, settings)
        assert len(gdf) == 2
        assert gdf["candidate_id"].tolist() == ["C00000", "C00001"]

    def test_attributes_are_aggregated(self, settings):
        grid = make_grid()
        mask = np.zeros(grid.shape, dtype=bool)
        mask[10:25, 10:25] = True
        gdf = build_candidates(make_result(mask), grid, settings)
        row = gdf.iloc[0]
        assert row["n_pixels"] > 100
        assert row["ndvi_drop"] == pytest.approx(0.27, abs=0.01)
        assert row["bsi_rise"] == pytest.approx(0.18, abs=0.01)
        assert row["zscore_max"] >= row["zscore_median"]

    def test_break_date_is_reported(self, settings):
        grid = make_grid()
        mask = np.zeros(grid.shape, dtype=bool)
        mask[10:25, 10:25] = True
        dates = dates_axis()
        gdf = build_candidates(make_result(mask, break_index=28), grid, settings, dates=dates)
        # Индекс 28 при семи наблюдениях в году приходится на 2022 год —
        # это и есть фраза «объект появился в 2022 году» на защите
        assert str(gdf["break_date"].iat[0]).startswith("2022")

    def test_noise_alone_produces_nothing(self, settings):
        """Разрозненный шум не должен порождать кандидатов."""
        grid = make_grid()
        rng = np.random.default_rng(1)
        mask = rng.random(grid.shape) < 0.02
        gdf = build_candidates(make_result(mask), grid, settings)
        assert len(gdf) <= 1

    def test_empty_detection_gives_empty_frame(self, settings):
        grid = make_grid()
        gdf = build_candidates(make_result(np.zeros(grid.shape, dtype=bool)), grid, settings)
        assert gdf.empty

    def test_polygons_are_matched_to_correct_labels(self, settings):
        """Два объекта с разной силой сигнала не должны перепутаться.

        rasterio.features.shapes и scipy.ndimage.label обходят области в
        разном порядке, поэтому сопоставление идёт по координате, а не по
        индексу. Тест фиксирует, что это работает.
        """
        grid = make_grid()
        mask = np.zeros(grid.shape, dtype=bool)
        mask[5:15, 5:15] = True
        mask[40:50, 40:50] = True

        result = make_result(mask)
        zscore = result.zscore.reshape(grid.shape).copy()
        zscore[5:15, 5:15] = 9.0   # первый объект — очень уверенный
        zscore[40:50, 40:50] = 3.5  # второй — пограничный
        result = BreakpointResult(**{**result.__dict__, "zscore": zscore.ravel()})

        gdf = build_candidates(result, grid, settings)
        by_position = gdf.sort_values("geometry", key=lambda s: s.apply(lambda g: -g.centroid.y))
        assert by_position["zscore_median"].iloc[0] == pytest.approx(9.0)
        assert by_position["zscore_median"].iloc[1] == pytest.approx(3.5)


class TestExport:
    def test_geojson_roundtrip(self, tmp_path):
        import geopandas as gpd

        settings = load_settings()
        grid = make_grid()
        mask = np.zeros(grid.shape, dtype=bool)
        mask[10:25, 10:25] = True
        gdf = build_candidates(make_result(mask), grid, settings, dates=dates_axis())

        path = tmp_path / "candidates.geojson"
        to_geojson(gdf, path)
        assert path.exists()

        reloaded = gpd.read_file(path)
        assert len(reloaded) == len(gdf)
        assert reloaded.crs.to_string() == "EPSG:4326"
        # Дата должна остаться читаемой строкой, а не числом наносекунд
        assert str(reloaded["break_date"].iat[0]).startswith("2022")
