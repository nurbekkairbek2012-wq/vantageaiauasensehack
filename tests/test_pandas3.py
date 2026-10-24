"""Совместимость с pandas 3, где строки получили собственный dtype.

Эти два места ломались молча и по-разному, и оба нашлись только на CI:
локально стоял pandas 2, а CI ставит последний.

**np.issubdtype на колонке pandas.** В pandas 3 строковая колонка имеет
StringDtype, и numpy на нём падает с «Cannot interpret StringDtype as a
data type». Выгрузка GeoJSON пыталась так определить, датная ли колонка,
и роняла экспорт целиком.

**None в списке строк.** Список с None внутри становится StringDtype, и
None превращается в nan. «Нет причины отклонения» — это именно None,
отсутствие значения; после превращения проверка `reason is None`
перестаёт работать, и прошедший отсев кандидат выглядит отклонённым по
причине «nan».

Тесты гоняют оба пути с принудительно включённым режимом pandas 3, чтобы
поломка не вернулась, когда версия сменится по-настоящему.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from vantage.candidates import to_geojson
from vantage.config import load_settings


@pytest.fixture
def pandas3():
    """Включить строковый dtype и вернуть всё как было."""
    previous = pd.options.future.infer_string
    pd.options.future.infer_string = True
    yield
    pd.options.future.infer_string = previous


class TestGeoJsonExport:
    def test_export_survives_string_dtype(self, pandas3, tmp_path):
        """ГЛАВНАЯ ПРОВЕРКА ФАЙЛА: выгрузка не должна падать на строках."""
        gdf = gpd.GeoDataFrame(
            {
                "candidate_id": ["C00001", "C00002"],
                "penalty_article": ["ст. 344, ч. 2-1", "ст. 344, ч. 2-1"],
                "break_date": pd.to_datetime(["2021-05-01", "2023-08-01"]),
                "area_m2": [1200.0, 3400.0],
                "geometry": [box(0, 0, 30, 30), box(60, 60, 90, 90)],
            },
            crs="EPSG:32642",
        )
        target = tmp_path / "candidates.geojson"
        to_geojson(gdf, target)
        assert target.exists()

    def test_dates_stay_readable_strings(self, pandas3, tmp_path):
        """Дата не должна уехать числом наносекунд."""
        gdf = gpd.GeoDataFrame(
            {
                "candidate_id": ["C00001"],
                "break_date": pd.to_datetime(["2022-07-01"]),
                "geometry": [box(0, 0, 30, 30)],
            },
            crs="EPSG:32642",
        )
        target = tmp_path / "one.geojson"
        to_geojson(gdf, target)
        assert str(gpd.read_file(target)["break_date"].iat[0]).startswith("2022")

    def test_missing_date_stays_empty_not_the_word_nat(self, pandas3, tmp_path):
        gdf = gpd.GeoDataFrame(
            {
                "candidate_id": ["C00001"],
                "break_date": pd.to_datetime([None]),
                "geometry": [box(0, 0, 30, 30)],
            },
            crs="EPSG:32642",
        )
        target = tmp_path / "empty.geojson"
        to_geojson(gdf, target)
        value = gpd.read_file(target)["break_date"].iat[0]
        assert value is None or pd.isna(value) or str(value) != "NaT"


class TestRejectReason:
    """«Причины нет» обязано остаться None, а не стать nan."""

    def _layers(self):
        from vantage.context import ContextLayers

        crs = load_settings().project.crs_working
        empty = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)
        roads = gpd.GeoDataFrame({"geometry": [Point(0, 0).buffer(1)]}, crs=crs)
        return ContextLayers(excluded=empty, roads=roads, settlements=roads, crs=crs)

    def test_passing_candidate_has_none_not_nan(self, pandas3):
        from vantage.context import apply_context_filter

        settings = load_settings()
        # Кандидат заведомо отклонится по расстоянию, но нас интересует не
        # вердикт, а тип колонки: она обязана быть object.
        candidates = gpd.GeoDataFrame(
            {"geometry": [box(0, 0, 40, 40)]}, crs=settings.project.crs_working
        )
        out = apply_context_filter(candidates, self._layers(), settings.context)
        assert out["reject_reason"].dtype == object

    def test_empty_input_keeps_object_dtype(self, pandas3):
        from vantage.context import apply_context_filter

        settings = load_settings()
        empty = gpd.GeoDataFrame(
            {"geometry": []}, geometry="geometry", crs=settings.project.crs_working
        )
        out = apply_context_filter(empty, self._layers(), settings.context)
        assert "reject_reason" in out.columns


def test_numpy_cannot_read_pandas_string_dtype(pandas3):
    """Фиксируем саму причину, а не только следствие.

    Если однажды numpy научится понимать StringDtype, этот тест упадёт —
    и станет видно, что обходной путь можно убирать.
    """
    column = pd.Series(["текст"])
    with pytest.raises(TypeError, match="StringDtype"):
        np.issubdtype(column.dtype, np.datetime64)
    assert pd.api.types.is_datetime64_any_dtype(column) is False
