"""Тесты извлечения чипов.

Основной класс проверяемых ошибок — тихие: чип вырезан не в том месте,
эпохи перепутаны местами, статистика нормировки посчитана по всему набору
вместо обучающей части. Ни одна из них не падает с исключением, но каждая
делает измеренное качество модели неправдой.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
import rasterio.transform
import xarray as xr
from shapely.geometry import box

from vantage.candidates import RasterGrid
from vantage.chips import (
    PAD_VALUE,
    ChannelStats,
    ChipDataset,
    build_chips,
    extract_window,
    train_val_split,
    window_bounds,
)
from vantage.config import ChipsCfg

UTM = "EPSG:32642"
PIXEL_M = 10.0
NY = NX = 80
N_TIME = 40

CFG = ChipsCfg(
    size_px=16,
    bands=["B02", "B04", "B08"],
    derived=["ndvi", "bsi"],
)


def make_grid() -> RasterGrid:
    transform = rasterio.transform.from_origin(350_000.0, 5_670_000.0, PIXEL_M, PIXEL_M)
    return RasterGrid(transform=tuple(transform)[:6], crs=UTM, shape=(NY, NX))


def make_cube(*, break_at: int = 20) -> xr.Dataset:
    """Синтетический куб: в правом нижнем углу после break_at всё меняется."""
    grid = make_grid()
    x = 350_000.0 + PIXEL_M * (np.arange(NX) + 0.5)
    y = 5_670_000.0 - PIXEL_M * (np.arange(NY) + 0.5)
    time = np.array(
        [f"{2018 + i // 7}-{(i % 7) + 4:02d}-15" for i in range(N_TIME)], dtype="datetime64[D]"
    )

    data = {}
    rng = np.random.default_rng(0)
    for name, base, after in [
        ("B02", 0.05, 0.12),
        ("B04", 0.10, 0.22),
        ("B08", 0.35, 0.15),
        ("ndvi", 0.40, 0.05),
        ("bsi", 0.05, 0.30),
    ]:
        arr = np.full((N_TIME, NY, NX), base, dtype="float32")
        arr[break_at:, 50:70, 50:70] = after
        arr += rng.normal(0, 0.005, arr.shape).astype("float32")
        data[name] = (("time", "y", "x"), arr)

    return xr.Dataset(data, coords={"time": time, "y": y, "x": x}), grid


def make_candidates(centers_rowcol: list[tuple[int, int]], *, break_index: int | None = 20):
    """Кандидаты, заданные центрами в координатах пикселей."""
    geoms = []
    for row, col in centers_rowcol:
        cx = 350_000.0 + PIXEL_M * (col + 0.5)
        cy = 5_670_000.0 - PIXEL_M * (row + 0.5)
        geoms.append(box(cx - 30, cy - 30, cx + 30, cy + 30))
    gdf = gpd.GeoDataFrame(
        {"candidate_id": [f"C{i:05d}" for i in range(len(geoms))], "geometry": geoms}, crs=UTM
    )
    if break_index is not None:
        gdf["break_index"] = break_index
    return gdf


# --------------------------------------------------------------------------- #


class TestWindowBounds:
    def test_centred_window_is_full(self):
        r0, r1, c0, c1, dr0, dr1, dc0, dc1 = window_bounds(40, 40, 16, (80, 80))
        assert (r1 - r0, c1 - c0) == (16, 16)
        assert (dr0, dr1, dc0, dc1) == (0, 16, 0, 16)

    def test_window_at_top_left_corner_is_clipped(self):
        r0, r1, c0, _c1, dr0, dr1, dc0, _dc1 = window_bounds(2, 2, 16, (80, 80))
        assert r0 == 0 and c0 == 0
        # Недостающие строки должны попасть в начало чипа, а не в конец
        assert dr0 == 6 and dc0 == 6
        assert (r1 - r0) == (dr1 - dr0)

    def test_window_at_bottom_right_corner_is_clipped(self):
        _r0, r1, _c0, c1, dr0, _dr1, dc0, _dc1 = window_bounds(78, 78, 16, (80, 80))
        assert r1 == 80 and c1 == 80
        assert dr0 == 0 and dc0 == 0


class TestExtractWindow:
    def test_reads_correct_location(self):
        stack = np.zeros((2, 80, 80), dtype="float32")
        stack[:, 30:40, 30:40] = 7.0
        chip = extract_window(stack, row=35, col=35, size_px=16)
        # Центр чипа должен попасть в помеченную область
        assert chip[0, 8, 8] == 7.0

    def test_pads_outside_raster(self):
        stack = np.ones((2, 80, 80), dtype="float32")
        chip = extract_window(stack, row=0, col=0, size_px=16)
        assert chip.shape == (2, 16, 16)
        assert chip[0, 0, 0] == PAD_VALUE   # за краем
        assert chip[0, 15, 15] == 1.0       # внутри растра


class TestBuildChips:
    def test_shapes_and_channels(self):
        cube, grid = make_cube()
        candidates = make_candidates([(60, 60), (20, 20)])
        ds = build_chips(cube, candidates, grid, CFG, epoch_months=10)
        assert ds.before.shape == (2, 5, 16, 16)
        assert ds.after.shape == ds.before.shape
        assert ds.channels == ["B02", "B04", "B08", "ndvi", "bsi"]
        assert ds.candidate_ids == ["C00000", "C00001"]

    def test_epochs_are_not_swapped(self):
        """До разрыва NDVI высокий, после — низкий. Перепутанные эпохи
        обучили бы сеть ровно наоборот, и она бы «работала» на валидации,
        но давала бы противоположный ответ на новых данных."""
        cube, grid = make_cube(break_at=20)
        candidates = make_candidates([(60, 60)], break_index=20)
        ds = build_chips(cube, candidates, grid, CFG, epoch_months=10)
        ndvi_channel = ds.channels.index("ndvi")
        assert ds.before[0, ndvi_channel].mean() > ds.after[0, ndvi_channel].mean()

    def test_bsi_rises_after_break(self):
        cube, grid = make_cube(break_at=20)
        ds = build_chips(cube, make_candidates([(60, 60)]), grid, CFG, epoch_months=10)
        bsi = ds.channels.index("bsi")
        assert ds.after[0, bsi].mean() > ds.before[0, bsi].mean()

    def test_unchanged_area_shows_no_difference(self):
        cube, grid = make_cube(break_at=20)
        ds = build_chips(cube, make_candidates([(10, 10)]), grid, CFG, epoch_months=10)
        ndvi = ds.channels.index("ndvi")
        diff = abs(ds.after[0, ndvi].mean() - ds.before[0, ndvi].mean())
        assert diff < 0.02

    def test_break_near_series_edge_is_shifted_inward(self):
        """Эпохи должны остаться одинакового объёма даже у края ряда."""
        cube, grid = make_cube()
        candidates = make_candidates([(60, 60)], break_index=2)
        ds = build_chips(cube, candidates, grid, CFG, epoch_months=10)
        assert np.isfinite(ds.before).all()
        assert np.isfinite(ds.after).all()

    def test_rejects_missing_channels(self):
        cube, grid = make_cube()
        bad_cfg = ChipsCfg(size_px=16, bands=["B02", "B99"], derived=[])
        with pytest.raises(KeyError, match="B99"):
            build_chips(cube, make_candidates([(60, 60)]), grid, bad_cfg)

    def test_rejects_too_short_series(self):
        cube, grid = make_cube()
        with pytest.raises(ValueError, match="мало для двух эпох"):
            build_chips(cube, make_candidates([(60, 60)]), grid, CFG, epoch_months=30)

    def test_rejects_empty_candidates(self):
        cube, grid = make_cube()
        empty = gpd.GeoDataFrame({"candidate_id": [], "geometry": []}, crs=UTM)
        with pytest.raises(ValueError):
            build_chips(cube, empty, grid, CFG)


class TestNormalization:
    def _dataset(self) -> ChipDataset:
        rng = np.random.default_rng(0)
        # Каналы намеренно разного масштаба: SWIR и синий отличаются на порядок
        before = np.stack(
            [
                rng.normal(0.05, 0.01, (20, 8, 8)),
                rng.normal(0.50, 0.10, (20, 8, 8)),
            ],
            axis=1,
        ).astype("float32")
        after = before + 0.02
        return ChipDataset(
            before=before,
            after=after,
            candidate_ids=[f"C{i:05d}" for i in range(20)],
            channels=["blue", "swir"],
        )

    def test_equalizes_channel_scales(self):
        """Без поканальной нормировки градиенты определял бы самый громкий канал."""
        ds = self._dataset()
        stats = ChannelStats.fit(ds)
        normalized = stats.transform(ds)
        per_channel_std = normalized.before.std(axis=(0, 2, 3))
        assert per_channel_std.max() / per_channel_std.min() < 2.0

    def test_statistics_use_only_training_subset(self):
        """Утечка данных: статистика по всему набору завышает качество."""
        ds = self._dataset()
        train_idx, _ = train_val_split(len(ds), 0.5, seed=0)
        train_only = ChannelStats.fit(ds, train_idx)
        everything = ChannelStats.fit(ds)
        assert not np.allclose(train_only.mean, everything.mean)

    def test_nan_becomes_neutral_value(self):
        ds = self._dataset()
        ds.before[0, 0, 0, 0] = np.nan
        stats = ChannelStats.fit(ds)
        assert stats.transform(ds).before[0, 0, 0, 0] == PAD_VALUE

    def test_zero_variance_channel_does_not_divide_by_zero(self):
        ds = self._dataset()
        ds.before[:, 0] = 0.3
        ds.after[:, 0] = 0.3
        normalized = ChannelStats.fit(ds).transform(ds)
        assert np.isfinite(normalized.before).all()

    def test_stats_roundtrip(self, tmp_path):
        stats = ChannelStats.fit(self._dataset())
        path = tmp_path / "stats.npz"
        stats.save(path)
        loaded = ChannelStats.load(path)
        assert np.allclose(stats.mean, loaded.mean)
        assert np.allclose(stats.std, loaded.std)


class TestDatasetIO:
    def test_roundtrip(self, tmp_path):
        cube, grid = make_cube()
        ds = build_chips(cube, make_candidates([(60, 60), (20, 20)]), grid, CFG, epoch_months=10)
        ds.labels = np.array([1, 0])
        path = tmp_path / "chips.npz"
        ds.save(path)
        loaded = ChipDataset.load(path)
        assert len(loaded) == 2
        assert loaded.channels == ds.channels
        assert np.array_equal(loaded.labels, ds.labels)
        assert np.allclose(loaded.before, ds.before)

    def test_shape_mismatch_is_rejected(self):
        with pytest.raises(ValueError):
            ChipDataset(
                before=np.zeros((2, 3, 8, 8), dtype="float32"),
                after=np.zeros((3, 3, 8, 8), dtype="float32"),
                candidate_ids=["a", "b"],
                channels=["x", "y", "z"],
            )

    def test_channel_count_mismatch_is_rejected(self):
        with pytest.raises(ValueError, match="каналов"):
            ChipDataset(
                before=np.zeros((2, 3, 8, 8), dtype="float32"),
                after=np.zeros((2, 3, 8, 8), dtype="float32"),
                candidate_ids=["a", "b"],
                channels=["x"],
            )


class TestSplit:
    def test_split_is_disjoint_and_complete(self):
        train, val = train_val_split(100, 0.2, seed=0)
        assert len(train) + len(val) == 100
        assert set(train).isdisjoint(set(val))
        assert len(val) == 20

    def test_split_is_reproducible(self):
        assert np.array_equal(train_val_split(50, 0.2, 7)[1], train_val_split(50, 0.2, 7)[1])

    def test_tiny_dataset_still_gets_validation(self):
        _, val = train_val_split(3, 0.2, seed=0)
        assert len(val) >= 1

    def test_rejects_invalid_fraction(self):
        with pytest.raises(ValueError):
            train_val_split(10, 0.0, seed=0)
