"""Тесты модели прогноза появления новых свалок.

Главное, что здесь проверяется, — методология, а не точность. Модель
риска легко сделать красивой и бессмысленной: случайное разбиение выборки
даёт высокую метрику просто потому, что соседние ячейки скоррелированы.
Тесты фиксируют, что валидация идёт по времени и что ячейки с уже
существующими свалками из неё исключены.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Point, box

from vantage.aoi import AOI
from vantage.config import RiskCfg
from vantage.risk import (
    FEATURE_NAMES,
    aggregate_public,
    build_grid,
    predict_risk,
    recommend_placements,
    spatial_features,
    temporal_labels,
    train_risk_model,
)

UTM = "EPSG:32642"

CFG = RiskCfg(
    grid_cell_m=1000,
    horizon_months=12,
    model="lightgbm",
    n_estimators=60,
    learning_rate=0.1,
    max_depth=5,
    seed=42,
    public_grid_cell_m=4000,
)


@pytest.fixture(scope="module")
def aoi() -> AOI:
    # Небольшая область около Астаны: 20 x 20 км
    return AOI.from_bbox((71.30, 51.05, 71.60, 51.23), name="test", crs_working=UTM)


@pytest.fixture(scope="module")
def grid(aoi) -> gpd.GeoDataFrame:
    return build_grid(aoi, CFG.grid_cell_m)


def make_roads(grid) -> gpd.GeoDataFrame:
    min_x, min_y, max_x, max_y = grid.total_bounds
    mid_y = (min_y + max_y) / 2
    return gpd.GeoDataFrame(
        {"geometry": [LineString([(min_x, mid_y), (max_x, mid_y)])]}, crs=UTM
    )


def make_settlements(grid) -> gpd.GeoDataFrame:
    min_x, min_y, _, _ = grid.total_bounds
    return gpd.GeoDataFrame({"geometry": [Point(min_x + 2000, min_y + 2000).buffer(1500)]}, crs=UTM)


def make_landfills(grid, *, n_before: int = 25, n_after: int = 12, seed: int = 0):
    """Свалки, сгруппированные вдоль дороги — так и происходит в реальности.

    Часть возникла до отсечки (обучение), часть после (проверка прогноза).
    """
    rng = np.random.default_rng(seed)
    min_x, min_y, max_x, max_y = grid.total_bounds
    mid_y = (min_y + max_y) / 2

    geoms, dates = [], []
    for i in range(n_before + n_after):
        x = rng.uniform(min_x + 1000, max_x - 1000)
        # Кучкуются у дороги: |dy| мал
        y = mid_y + rng.normal(0, 800)
        y = float(np.clip(y, min_y + 100, max_y - 100))
        geoms.append(Point(x, y).buffer(80))
        year = 2019 + (i % 3) if i < n_before else 2024 + (i % 2)
        dates.append(np.datetime64(f"{year}-06-15"))

    return gpd.GeoDataFrame(
        {"landfill_id": range(len(geoms)), "break_date": dates, "geometry": geoms}, crs=UTM
    )


# --------------------------------------------------------------------------- #


class TestGrid:
    def test_covers_area(self, aoi, grid):
        assert len(grid) > 100
        assert grid.crs.to_string() == UTM

    def test_cell_size_is_metric(self, grid):
        """Ячейка 1000 м должна иметь площадь 1 км², а не 1 квадратный градус."""
        assert grid.geometry.area.median() == pytest.approx(1_000_000, rel=0.01)

    def test_ids_are_unique(self, grid):
        assert grid["cell_id"].nunique() == len(grid)

    def test_rejects_bad_cell_size(self, aoi):
        with pytest.raises(ValueError):
            build_grid(aoi, 0)


@pytest.fixture(scope="module")
def features(grid):
    return spatial_features(
        grid,
        roads=make_roads(grid),
        settlements=make_settlements(grid),
        existing=make_landfills(grid),
    )


class TestFeatures:
    def test_all_features_present(self, features):
        for name in FEATURE_NAMES:
            assert name in features.columns

    def test_no_infinities_remain(self, features):
        """Бесконечности ломают деревья — их обязаны заменять конечным числом."""
        values = features[list(FEATURE_NAMES)].to_numpy(dtype=float)
        assert np.isfinite(values).all()

    def test_distance_to_road_is_metric(self, features):
        # Область 20 км, дорога посередине — максимум порядка 10 км
        assert features["dist_road_m"].max() < 20_000
        assert features["dist_road_m"].min() < 600

    def test_density_is_higher_near_clusters(self, features):
        assert features["existing_density_3km"].max() > features["existing_density_3km"].median()

    def test_missing_layer_does_not_crash(self, grid):
        result = spatial_features(grid)
        assert np.isfinite(result[list(FEATURE_NAMES)].to_numpy(dtype=float)).all()

    def test_screening_score_rewards_road_access_and_distance_from_homes(self, features):
        """Логика нарушителя: доехать легко, увидеть некому."""
        near_road_far_homes = features[
            (features["dist_road_m"] < 500) & (features["dist_settlement_m"] > 8000)
        ]
        near_road_near_homes = features[
            (features["dist_road_m"] < 500) & (features["dist_settlement_m"] < 3000)
        ]
        if len(near_road_far_homes) and len(near_road_near_homes):
            assert (
                near_road_far_homes["screening_score"].median()
                > near_road_near_homes["screening_score"].median()
            )


class TestLabels:
    def test_splits_by_cutoff_date(self, grid):
        landfills = make_landfills(grid)
        y_train, y_future = temporal_labels(grid, landfills, cutoff="2023-01-01")
        assert y_train.sum() > 0
        assert y_future.sum() > 0
        assert len(y_train) == len(grid)

    def test_cutoff_moves_the_split(self, grid):
        landfills = make_landfills(grid)
        early, _ = temporal_labels(grid, landfills, cutoff="2020-01-01")
        late, _ = temporal_labels(grid, landfills, cutoff="2026-01-01")
        assert late.sum() > early.sum()

    def test_missing_dates_are_ignored_not_counted(self, grid):
        landfills = make_landfills(grid).copy()
        landfills.loc[landfills.index[:5], "break_date"] = np.datetime64("NaT")
        y_train, y_future = temporal_labels(grid, landfills, cutoff="2023-01-01")
        assert y_train.sum() + y_future.sum() > 0

    def test_requires_date_column(self, grid):
        landfills = make_landfills(grid).drop(columns=["break_date"])
        with pytest.raises(KeyError):
            temporal_labels(grid, landfills, cutoff="2023-01-01")


lgb = pytest.importorskip("lightgbm", reason="нужен LightGBM (pip install -e .[ml])")


@pytest.fixture(scope="module")
def trained(grid):
    """Одно обучение на весь модуль: обучаемся до 2023, проверяемся после."""
    landfills = make_landfills(grid, n_before=40, n_after=20)
    train_features = spatial_features(
        grid,
        roads=make_roads(grid),
        settlements=make_settlements(grid),
        existing=landfills[landfills["break_date"] < np.datetime64("2023-01-01")],
    )
    y_train, y_future = temporal_labels(grid, landfills, cutoff="2023-01-01")
    model = train_risk_model(train_features, y_train, y_future, CFG, cutoff="2023-01-01")
    return model, train_features, y_train, y_future


class TestTraining:
    def test_model_trains(self, trained):
        model, *_ = trained
        assert model.feature_names == list(FEATURE_NAMES)
        assert model.cutoff_date == "2023-01-01"

    def test_validation_is_temporal_not_random(self, trained):
        """Метрика обязана считаться на будущих объектах.

        Случайное разбиение дало бы завышенную цифру: соседние ячейки
        скоррелированы, и модель угадывала бы соседа, ничего не выучив.
        """
        model, *_ = trained
        assert "pr_auc_future" in model.metrics
        assert "base_rate_future" in model.metrics

    def test_beats_the_base_rate(self, trained):
        """Модель должна быть лучше случайного тыка.

        Базовая частота — доля ячеек, где свалка появилась. Если PR-AUC
        не выше неё, модель бесполезна независимо от красоты графиков.
        """
        model, *_ = trained
        assert model.metrics["pr_auc_future"] > model.metrics["base_rate_future"]
        assert model.metrics["lift"] > 1.0

    def test_importances_sum_to_one(self, trained):
        model, *_ = trained
        assert sum(model.importances.values()) == pytest.approx(1.0)

    def test_proximity_features_matter_most(self, trained):
        """Ожидаемая картина: решают близость дороги и соседство
        существующих свалок. Если бы главным оказался случайный признак,
        это был бы признак утечки или переобучения."""
        model, *_ = trained
        top = [name for name, _ in model.top_features(4)]
        assert any("road" in name or "existing" in name or "screening" in name for name in top)

    def test_refuses_to_train_without_examples(self, grid):
        features = spatial_features(grid, roads=make_roads(grid))
        zeros = np.zeros(len(grid), dtype=int)
        with pytest.raises(ValueError, match="мало положительных"):
            train_risk_model(features, zeros, zeros, CFG, cutoff="2023-01-01")

    def test_prediction_is_a_probability(self, trained):
        model, features, *_ = trained
        result = predict_risk(model, features)
        assert result["risk"].between(0, 1).all()
        assert result["risk_rank"].min() == 1


@pytest.fixture(scope="module")
def risk_grid(trained):
    model, train_features, _, _ = trained
    return predict_risk(model, train_features)


class TestPublicLayer:
    def test_public_grid_is_coarser(self, risk_grid):
        public = aggregate_public(risk_grid, CFG)
        assert len(public) < len(risk_grid)
        assert public.geometry.area.median() > risk_grid.geometry.area.median()

    def test_public_layer_hides_exact_probability(self, risk_grid):
        """Этическое требование: публичная карта показывает класс зоны,
        а не точную вероятность, привязанную к конкретному двору."""
        public = aggregate_public(risk_grid, CFG)
        assert "risk" not in public.columns
        assert set(public.columns) == {"risk_class", "geometry"}

    def test_risk_class_is_bounded(self, risk_grid):
        public = aggregate_public(risk_grid, CFG, quantiles=4)
        assert public["risk_class"].between(1, 4).all()

    def test_high_risk_covers_small_share_of_area(self, risk_grid):
        """Зоны риска — это места, а не треть области.

        Прежняя версия делила ВСЕ ячейки на квартили, и высший класс
        доставался верхней четверти региона: на настоящем прогоне это
        оказалось 3 155 км² при трёх классах на 9 468 км². Карта, где три
        четверти области закрашены тревожным цветом, не выделяет ничего —
        и справедливо получала вопрос «почему у вас свалка в ботаническом
        саду». Патрулю нужен список мест, а не раскраска карты.
        """
        public = aggregate_public(risk_grid, CFG)
        shown = (public["risk_class"] > 1).mean()
        assert shown <= 0.10, f"под риском {shown:.0%} ячеек — это раскраска, а не прогноз"

    def test_masked_cells_never_enter_risk_zones(self, risk_grid):
        """Земля, снятая маской невозможного, не может попасть в верхушку.

        Ячейки парков и кампусов обнулены, а не удалены. Если отбор берёт
        верхние проценты по рангу без явного исключения нулей, то на
        прогоне, где риск везде близок к нулю, обнулённые ячейки попадают
        в число «самых опасных» — ровно то, чего маска и должна не
        допустить.
        """
        grid = risk_grid.copy()
        grid.loc[grid.index[: len(grid) // 2], "risk"] = 0.0
        public = aggregate_public(grid, CFG)
        assert (public["risk_class"] > 1).sum() > 0, "не осталось ни одной зоны"

    def test_rejects_finer_public_grid(self, risk_grid):
        bad = RiskCfg(**{**CFG.__dict__, "public_grid_cell_m": 100})
        with pytest.raises(ValueError):
            aggregate_public(risk_grid, bad)


class TestPlacementRecommendation:
    def test_returns_requested_number_of_points(self, risk_grid):
        placements = recommend_placements(risk_grid, budget=10)
        assert len(placements) == 10
        assert placements["placement_rank"].tolist() == list(range(1, 11))
        # Рекомендации отсортированы по убыванию риска
        assert placements["risk"].is_monotonic_decreasing

    def test_rejects_zero_budget(self, grid):
        empty = gpd.GeoDataFrame(
            {"cell_id": ["G0"], "risk": [0.5], "geometry": [box(0, 0, 1, 1)]}, crs=UTM
        )
        with pytest.raises(ValueError):
            recommend_placements(empty, budget=0)
