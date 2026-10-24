"""Тесты потайлового прогона: склейка объектов на стыках и перенос атрибутов.

Область Астаны — тысячи квадратных километров, и загрузить её одним
массивом нельзя ни по памяти, ни по таймаутам. Значит, объект на границе
двух плиток находится дважды, двумя половинами.

Просто сложить таблицы нельзя, и это не эстетика. Свалка площадью 1200 м²,
разрезанная пополам, даёт два объекта по 600 м²; оба проходят фильтр
минимальной площади в 900 м² — то есть исчезают оба. А если и проходят,
ущерб считается по площади, и вместо одной оценки получаются две вдвое
меньшие, которые нельзя просто сложить: масса зависит от площади нелинейно
через класс глубины.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import box

from vantage.candidates import merge_across_tiles

CRS = "EPSG:32642"


def piece(x0, y0, x1, y1, **attributes) -> gpd.GeoDataFrame:
    row = {"geometry": [box(x0, y0, x1, y1)], **{k: [v] for k, v in attributes.items()}}
    return gpd.GeoDataFrame(row, crs=CRS)


class TestMergeAcrossTiles:
    def test_empty_input_gives_empty_layer(self):
        merged = merge_across_tiles([], crs=CRS)
        assert merged.empty
        assert merged.crs == CRS

    def test_separate_objects_stay_separate(self):
        left = piece(0, 0, 30, 30, area_m2=900.0, n_pixels=9, zscore_max=4.0)
        right = piece(500, 500, 530, 530, area_m2=900.0, n_pixels=9, zscore_max=4.0)

        merged = merge_across_tiles([left, right], crs=CRS)
        assert len(merged) == 2
        assert set(merged["n_pieces"]) == {1}

    def test_object_cut_by_tile_border_becomes_one(self):
        """ГЛАВНАЯ ПРОВЕРКА ФАЙЛА.

        Две половины одного объекта по разные стороны стыка обязаны стать
        одним объектом полной площади, иначе фильтр минимальной площади
        съест обе.
        """
        left = piece(0, 0, 30, 60, area_m2=1800.0, n_pixels=18, zscore_max=4.2)
        right = piece(30, 0, 60, 60, area_m2=1800.0, n_pixels=18, zscore_max=5.1)

        merged = merge_across_tiles([left, right], crs=CRS)
        assert len(merged) == 1
        assert merged["n_pieces"].iat[0] == 2
        assert merged["area_m2"].iat[0] == pytest.approx(3600.0)
        assert merged["n_pixels"].iat[0] == 36

    def test_confidence_is_taken_from_the_best_piece(self):
        """Разрез по границе не должен занижать уверенность объекта."""
        left = piece(0, 0, 30, 60, area_m2=1800.0, zscore_max=4.2)
        right = piece(30, 0, 60, 60, area_m2=1800.0, zscore_max=6.4)

        merged = merge_across_tiles([left, right], crs=CRS)
        assert merged["zscore_max"].iat[0] == pytest.approx(6.4)

    def test_measurements_are_weighted_by_area(self):
        """Маленький кусок не должен тянуть оценку объекта на себя."""
        big = piece(0, 0, 90, 60, area_m2=5400.0, ndvi_drop=0.30)
        small = piece(90, 0, 100, 60, area_m2=600.0, ndvi_drop=0.10)

        merged = merge_across_tiles([big, small], crs=CRS)
        expected = (0.30 * 5400 + 0.10 * 600) / 6000
        assert merged["ndvi_drop"].iat[0] == pytest.approx(expected, abs=1e-6)

    def test_earliest_break_date_wins(self):
        """Объект возник тогда, когда его увидела первая половина.

        Позже — это уже про то, когда он дорос до второй плитки.
        """
        left = piece(0, 0, 30, 60, area_m2=1800.0, break_date="2021-05-01", break_index=12)
        right = piece(30, 0, 60, 60, area_m2=1800.0, break_date="2023-08-01", break_index=27)

        merged = merge_across_tiles([left, right], crs=CRS)
        assert str(merged["break_date"].iat[0])[:10] == "2021-05-01"
        assert merged["break_index"].iat[0] == 12

    def test_identifiers_are_reissued_and_unique(self):
        """Идентификаторы плиток не уникальны по прогону: у каждой свой C00000."""
        left = piece(0, 0, 30, 30, area_m2=900.0, candidate_id="C00000")
        right = piece(500, 500, 530, 530, area_m2=900.0, candidate_id="C00000")

        merged = merge_across_tiles([left, right], crs=CRS)
        assert merged["candidate_id"].nunique() == len(merged)

    def test_largest_object_comes_first(self):
        small = piece(0, 0, 30, 30, area_m2=900.0)
        large = piece(500, 500, 590, 590, area_m2=8100.0)

        merged = merge_across_tiles([small, large], crs=CRS)
        assert merged["area_m2"].iat[0] > merged["area_m2"].iat[-1]

    def test_empty_frames_are_ignored(self):
        empty = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=CRS)
        one = piece(0, 0, 30, 30, area_m2=900.0)

        merged = merge_across_tiles([empty, one, None], crs=CRS)
        assert len(merged) == 1


class TestTransferToMerged:
    """Перенос атрибутов с кусков на склеенный объект.

    Чипы для сети режутся по кускам — до склейки. После неё идентификаторы
    новые, и связать предсказание с объектом по имени уже нельзя.
    """

    def test_probability_comes_from_the_most_confident_piece(self):
        from vantage.orchestrate import transfer_to_merged

        merged = gpd.GeoDataFrame(
            {"candidate_id": ["C00000"], "geometry": [box(0, 0, 60, 60)]}, crs=CRS
        )
        pieces = gpd.GeoDataFrame(
            {
                "probability": [0.42, 0.91],
                "pmli_response": [0.05, 0.09],
                "geometry": [box(0, 0, 30, 60), box(30, 0, 60, 60)],
            },
            crs=CRS,
        )

        result = transfer_to_merged(merged, pieces)
        assert result["probability"].iat[0] == pytest.approx(0.91)
        assert result["pmli_response"].iat[0] == pytest.approx(0.07)

    def test_object_without_pieces_gets_no_value(self):
        """Пустая ячейка честнее нуля: ноль читается как «модель уверена, что нет»."""
        from vantage.orchestrate import transfer_to_merged

        merged = gpd.GeoDataFrame(
            {"candidate_id": ["C00000"], "geometry": [box(1000, 1000, 1060, 1060)]}, crs=CRS
        )
        pieces = gpd.GeoDataFrame(
            {"probability": [0.9], "geometry": [box(0, 0, 30, 30)]}, crs=CRS
        )

        result = transfer_to_merged(merged, pieces)
        assert np.isnan(result["probability"].iat[0])

    def test_missing_columns_are_tolerated(self):
        from vantage.orchestrate import transfer_to_merged

        merged = gpd.GeoDataFrame(
            {"candidate_id": ["C00000"], "geometry": [box(0, 0, 60, 60)]}, crs=CRS
        )
        pieces = gpd.GeoDataFrame({"geometry": [box(0, 0, 30, 60)]}, crs=CRS)

        result = transfer_to_merged(merged, pieces)
        assert list(result["candidate_id"]) == ["C00000"]


class TestTileRetry:
    """Повтор плитки при сетевом сбое.

    Не перестраховка: на прогоне по кольцу две плитки из одиннадцати
    упали с «Chunk and warp failed» — это оборванное соединение при
    чтении COG, а не свойство данных. Плитка стоит несколько минут и
    состоит из сотен range-запросов; терять её из-за одного разорванного
    запроса дорого.
    """

    def _pipeline(self, tmp_path):
        from vantage.config import load_settings
        from vantage.pipeline import Pipeline

        return Pipeline(load_settings(), outputs=tmp_path)

    def test_second_attempt_succeeds(self, tmp_path, monkeypatch):
        from vantage import pipeline as pipeline_module

        pipeline = self._pipeline(tmp_path)
        monkeypatch.setattr(pipeline_module.time, "sleep", lambda _: None)

        calls = {"n": 0}

        def flaky(tile, *, keep_bands=False):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("Chunk and warp failed")
            return ("ok", None, None, None)

        monkeypatch.setattr(pipeline, "process_tile", flaky)
        result = pipeline._process_tile_with_retry(
            pipeline.aoi, keep_bands=False, attempts=2
        )
        assert result[0] == "ok"
        assert calls["n"] == 2

    def test_error_survives_all_attempts(self, tmp_path, monkeypatch):
        from vantage import pipeline as pipeline_module

        pipeline = self._pipeline(tmp_path)
        monkeypatch.setattr(pipeline_module.time, "sleep", lambda _: None)

        def always_fails(tile, *, keep_bands=False):
            raise RuntimeError("Chunk and warp failed")

        monkeypatch.setattr(pipeline, "process_tile", always_fails)
        with pytest.raises(RuntimeError, match="Chunk and warp"):
            pipeline._process_tile_with_retry(pipeline.aoi, keep_bands=False, attempts=2)

    def test_success_does_not_retry(self, tmp_path, monkeypatch):
        pipeline = self._pipeline(tmp_path)
        calls = {"n": 0}

        def once(tile, *, keep_bands=False):
            calls["n"] += 1
            return ("ok", None, None, None)

        monkeypatch.setattr(pipeline, "process_tile", once)
        pipeline._process_tile_with_retry(pipeline.aoi, keep_bands=False, attempts=3)
        assert calls["n"] == 1
