"""Тесты автоматической разметки по OpenStreetMap.

Смысл модуля — не сэкономить время, а собрать **правильные** примеры.
Трудные отрицательные (карьеры, стройки) ценнее случайной степи:
модель ошибается именно на них, и обучение на лёгких негативах даёт
отличную метрику при бесполезной модели.
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import box

from vantage.aoi import AOI
from vantage.labels import (
    HARD_NEGATIVE_TAGS,
    MIN_OVERLAP_FRACTION,
    POSITIVE_TAGS,
    LabelReport,
    auto_label,
    build_osm_query,
    class_balance,
    manual_queue,
    overlap_fraction,
)

UTM = "EPSG:32642"


def square(cx: float, cy: float, side: float):
    half = side / 2
    return box(cx - half, cy - half, cx + half, cy + half)


def candidates(specs: list[tuple[float, float, float]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "candidate_id": [f"C{i:05d}" for i in range(len(specs))],
            "area_m2": [side * side for _, _, side in specs],
            "geometry": [square(cx, cy, side) for cx, cy, side in specs],
        },
        crs=UTM,
    )


def reference(specs: list[tuple[float, float, float]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"geometry": [square(cx, cy, side) for cx, cy, side in specs]}, crs=UTM
    )


# --------------------------------------------------------------------------- #
#  Запросы
# --------------------------------------------------------------------------- #


class TestQueryBuilding:
    @pytest.fixture
    def aoi(self):
        return AOI.from_bbox((70.90, 50.88, 72.05, 51.42), name="astana", crs_working=UTM)

    def test_positive_query_covers_waste_tags(self, aoi):
        query = build_osm_query(aoi, POSITIVE_TAGS)
        assert "landfill" in query
        assert "waste_disposal" in query

    def test_negative_query_covers_lookalikes(self, aoi):
        """Карьер и стройка — то, с чем свалку путают чаще всего."""
        query = build_osm_query(aoi, HARD_NEGATIVE_TAGS)
        assert "quarry" in query
        assert "construction" in query

    def test_bbox_uses_overpass_order(self, aoi):
        assert "50.88,70.9,51.42,72.05" in build_osm_query(aoi, POSITIVE_TAGS)


# --------------------------------------------------------------------------- #
#  Перекрытие
# --------------------------------------------------------------------------- #


class TestOverlapFraction:
    def test_full_overlap_is_one(self):
        cand = candidates([(0, 0, 100)])
        ref = reference([(0, 0, 200)])
        assert overlap_fraction(cand, ref)[0] == pytest.approx(1.0)

    def test_no_overlap_is_zero(self):
        cand = candidates([(0, 0, 100)])
        ref = reference([(10_000, 10_000, 200)])
        assert overlap_fraction(cand, ref)[0] == 0.0

    def test_half_overlap(self):
        cand = candidates([(0, 0, 100)])
        ref = reference([(50, 0, 100)])
        assert overlap_fraction(cand, ref)[0] == pytest.approx(0.5, abs=0.02)

    def test_corner_touch_does_not_count_as_match(self):
        """Касание углом на один пиксель не должно объявлять совпадение —
        поэтому считается доля площади, а не факт пересечения."""
        cand = candidates([(0, 0, 100)])
        ref = reference([(99, 99, 100)])
        assert overlap_fraction(cand, ref)[0] < MIN_OVERLAP_FRACTION

    def test_empty_reference_gives_zeros(self):
        cand = candidates([(0, 0, 100)])
        empty = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=UTM)
        assert overlap_fraction(cand, empty).tolist() == [0.0]


# --------------------------------------------------------------------------- #
#  Разметка
# --------------------------------------------------------------------------- #


class TestLabelDtype:
    def test_dtype_is_stable_regardless_of_content(self):
        """Тип колонки не должен зависеть от того, что в неё попало.

        Без явного Int64 pandas выводит тип из данных: список из одних
        None остаётся object, а смесь None и чисел становится float64
        с NaN. Проверка на пропуск тогда работает на одной выборке
        и ломается на другой.
        """
        only_manual, _ = auto_label(candidates([(0, 0, 100)]))
        mixed, _ = auto_label(
            candidates([(0, 0, 100), (50_000, 0, 100)]), positives=reference([(0, 0, 200)])
        )
        assert str(only_manual["label"].dtype) == "Int64"
        assert str(mixed["label"].dtype) == "Int64"


class TestAutoLabel:
    def test_positive_from_osm_landfill(self):
        cand = candidates([(0, 0, 100)])
        labelled, report = auto_label(cand, positives=reference([(0, 0, 200)]))
        assert labelled["label"].iloc[0] == 1
        assert labelled["label_source"].iloc[0] == "osm_positive"
        assert report.positives == 1

    def test_hard_negative_from_osm_quarry(self):
        cand = candidates([(0, 0, 100)])
        labelled, report = auto_label(cand, hard_negatives=reference([(0, 0, 200)]))
        assert labelled["label"].iloc[0] == 0
        assert labelled["label_source"].iloc[0] == "osm_negative"
        assert report.hard_negatives == 1

    def test_unmatched_candidate_needs_manual_review(self):
        cand = candidates([(0, 0, 100)])
        labelled, report = auto_label(
            cand, positives=reference([(50_000, 0, 100)]), hard_negatives=reference([(60_000, 0, 100)])
        )
        assert labelled["label"].isna().iloc[0]
        assert labelled["label_source"].iloc[0] == "manual"
        assert report.unlabelled == 1

    def test_positive_wins_over_negative(self):
        """Полигон, рядом с которым идёт стройка, остаётся полигоном."""
        cand = candidates([(0, 0, 100)])
        labelled, _ = auto_label(
            cand, positives=reference([(0, 0, 200)]), hard_negatives=reference([(0, 0, 200)])
        )
        assert labelled["label"].iloc[0] == 1

    def test_mixed_batch_is_split_correctly(self):
        cand = candidates([(0, 0, 100), (5_000, 0, 100), (50_000, 0, 100)])
        labelled, report = auto_label(
            cand,
            positives=reference([(0, 0, 200)]),
            hard_negatives=reference([(5_000, 0, 200)]),
        )
        assert labelled["label"].tolist()[:2] == [1, 0]
        assert labelled["label"].isna().iloc[2]
        assert (report.positives, report.hard_negatives, report.unlabelled) == (1, 1, 1)

    def test_no_reference_layers_leaves_everything_manual(self):
        cand = candidates([(0, 0, 100), (5_000, 0, 100)])
        _, report = auto_label(cand)
        assert report.unlabelled == 2
        assert report.automatic_fraction == 0.0

    def test_overlap_columns_are_reported(self):
        """Доля перекрытия сохраняется: пограничные случаи надо уметь
        пересмотреть, не пересчитывая всё заново."""
        cand = candidates([(0, 0, 100)])
        labelled, _ = auto_label(cand, positives=reference([(50, 0, 100)]))
        assert 0.4 < labelled["osm_positive_overlap"].iloc[0] < 0.6


class TestLabelReport:
    def test_fraction_is_computed(self):
        report = LabelReport(positives=30, hard_negatives=50, unlabelled=20)
        assert report.total == 100
        assert report.automatic_fraction == pytest.approx(0.8)

    def test_text_is_actionable(self):
        text = LabelReport(positives=30, hard_negatives=50, unlabelled=20).to_text()
        assert "80%" in text
        assert "20" in text

    def test_empty_report_does_not_divide_by_zero(self):
        assert LabelReport(0, 0, 0).automatic_fraction == 0.0


class TestManualQueue:
    def test_returns_only_unlabelled(self):
        cand = candidates([(0, 0, 100), (50_000, 0, 100)])
        labelled, _ = auto_label(cand, positives=reference([(0, 0, 200)]))
        assert len(manual_queue(labelled)) == 1

    def test_sorted_by_model_confidence_when_available(self):
        cand = candidates([(0, 0, 100), (5_000, 0, 100)])
        cand["probability"] = [0.4, 0.9]
        labelled, _ = auto_label(cand)
        queue = manual_queue(labelled)
        assert queue["probability"].iloc[0] == 0.9

    def test_limit_is_respected(self):
        cand = candidates([(i * 5_000, 0, 100) for i in range(10)])
        labelled, _ = auto_label(cand)
        assert len(manual_queue(labelled, limit=3)) == 3


class TestClassBalance:
    def test_counts_each_class(self):
        cand = candidates([(0, 0, 100), (5_000, 0, 100), (50_000, 0, 100)])
        labelled, _ = auto_label(
            cand, positives=reference([(0, 0, 200)]), hard_negatives=reference([(5_000, 0, 200)])
        )
        assert class_balance(labelled) == {"positive": 1, "negative": 1, "unlabelled": 1}

    def test_single_class_is_visible_before_training(self):
        """Модель на одном классе не падает — она просто учится всегда
        отвечать одинаково. Проверять баланс надо до обучения."""
        cand = candidates([(0, 0, 100), (200, 0, 100)])
        labelled, _ = auto_label(cand, positives=reference([(100, 0, 600)]))
        balance = class_balance(labelled)
        assert balance["negative"] == 0
        assert balance["positive"] == 2
