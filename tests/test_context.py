"""Тесты контекстного отсева.

Тесты работают без сети: слои OSM подменяются синтетическими. Проверяется
логика решения, а не доступность Overpass — иначе набор тестов начнёт
падать в самый неподходящий момент, за час до сдачи.

Отдельный акцент — на объяснимости: недостаточно отсеять кандидата, нужно
знать причину. Без причины на вопрос жюри «а почему вы это выбросили»
ответить нечем.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Point, Polygon, box

from vantage.aoi import AOI
from vantage.config import ContextCfg
from vantage.context import (
    ContextLayers,
    apply_context_filter,
    build_exclusion_query,
    build_roads_query,
    build_settlements_query,
    distance_to_layer,
    overpass_to_gdf,
    rejection_report,
)

UTM = "EPSG:32642"

CFG = ContextCfg(
    max_distance_to_road_m=300,
    min_distance_to_settlement_m=1500,
    max_distance_to_settlement_m=15000,
    exclude_landuse=["landfill", "quarry", "construction"],
    exclude_natural=["water"],
    min_area_m2=900,
    max_area_m2=500_000,
)


def square(cx: float, cy: float, side: float) -> Polygon:
    """Квадрат заданной площади с центром в (cx, cy) — метры, UTM."""
    half = side / 2
    return box(cx - half, cy - half, cx + half, cy + half)


@pytest.fixture
def layers() -> ContextLayers:
    """Синтетический контекст.

    Дорога идёт по линии y = 0. Населённый пункт — квадрат вокруг (0, 0)
    со стороной 2 км. Исключаемый объект (карьер) — квадрат у (10000, 200).
    """
    roads = gpd.GeoDataFrame(
        {"highway": ["secondary"], "geometry": [LineString([(-50_000, 0), (50_000, 0)])]},
        crs=UTM,
    )
    settlements = gpd.GeoDataFrame(
        {"place": ["city"], "geometry": [square(0, 0, 2_000)]}, crs=UTM
    )
    excluded = gpd.GeoDataFrame(
        {"landuse": ["quarry"], "geometry": [square(10_000, 200, 400)]}, crs=UTM
    )
    return ContextLayers(excluded=excluded, roads=roads, settlements=settlements, crs=UTM)


def candidates_from(geoms: list[Polygon]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"geometry": geoms}, crs=UTM)


# --------------------------------------------------------------------------- #
#  Построение запросов
# --------------------------------------------------------------------------- #


class TestQueryBuilding:
    @pytest.fixture
    def aoi(self) -> AOI:
        return AOI.from_bbox((70.90, 50.88, 72.05, 51.42), name="astana", crs_working=UTM)

    def test_bbox_order_is_overpass_convention(self, aoi):
        """Overpass ждёт (юг, запад, север, восток), а не bbox GeoJSON.

        Перепутанный порядок — классическая ошибка: запрос отработает
        без ошибки, но вернёт пустоту или чужой регион.
        """
        query = build_roads_query(aoi)
        assert "50.88,70.9,51.42,72.05" in query

    def test_exclusion_query_contains_configured_classes(self, aoi):
        query = build_exclusion_query(aoi, CFG)
        for landuse in CFG.exclude_landuse:
            assert landuse in query
        assert "water" in query

    def test_roads_query_excludes_footpaths(self, aoi):
        """По тропинке отходы не вывозят — она не должна попасть в фильтр."""
        query = build_roads_query(aoi)
        assert "track" in query
        assert "footway" not in query
        assert "cycleway" not in query

    def test_settlements_query_covers_all_place_types(self, aoi):
        query = build_settlements_query(aoi)
        for place in ("city", "town", "village", "hamlet"):
            assert place in query


class TestOverpassParsing:
    def test_parses_node_way_and_closed_way(self):
        payload = {
            "elements": [
                {"type": "node", "id": 1, "lat": 51.1, "lon": 71.4, "tags": {"place": "village"}},
                {
                    "type": "way",
                    "id": 2,
                    "geometry": [{"lat": 51.0, "lon": 71.0}, {"lat": 51.1, "lon": 71.1}],
                    "tags": {"highway": "track"},
                },
                {
                    "type": "way",
                    "id": 3,
                    "geometry": [
                        {"lat": 51.0, "lon": 71.0},
                        {"lat": 51.0, "lon": 71.01},
                        {"lat": 51.01, "lon": 71.01},
                        {"lat": 51.0, "lon": 71.0},
                    ],
                    "tags": {"landuse": "quarry"},
                },
            ]
        }
        gdf = overpass_to_gdf(payload, target_crs=UTM)
        assert len(gdf) == 3
        assert gdf.crs.to_string() == UTM
        kinds = sorted(g.geom_type for g in gdf.geometry)
        assert kinds == ["LineString", "Point", "Polygon"]

    def test_empty_payload_gives_empty_frame_with_crs(self):
        gdf = overpass_to_gdf({"elements": []}, target_crs=UTM)
        assert gdf.empty
        assert gdf.crs.to_string() == UTM

    def test_degenerate_geometry_is_skipped(self):
        payload = {"elements": [{"type": "way", "id": 9, "geometry": [{"lat": 51.0, "lon": 71.0}]}]}
        assert overpass_to_gdf(payload, target_crs=UTM).empty


# --------------------------------------------------------------------------- #
#  Расстояния
# --------------------------------------------------------------------------- #


class TestDistances:
    def test_distance_is_in_metres(self, layers):
        # Кандидат в 500 м севернее дороги (дорога по y = 0)
        cand = candidates_from([square(20_000, 500, 60)])
        dist = distance_to_layer(cand, layers.roads)
        # От края квадрата (30 м) до дороги: 500 - 30 = 470 м
        assert dist[0] == pytest.approx(470, abs=1)

    def test_empty_layer_gives_infinity_not_zero(self, layers):
        """Отсутствие данных не должно молча означать «расстояние ноль».

        Иначе пустой слой дорог пропустит вообще всех кандидатов.
        """
        empty = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=UTM)
        cand = candidates_from([square(20_000, 500, 60)])
        assert np.isinf(distance_to_layer(cand, empty)[0])

    def test_mismatched_crs_raises(self, layers):
        cand = gpd.GeoDataFrame({"geometry": [Point(71.4, 51.1).buffer(0.001)]}, crs="EPSG:4326")
        with pytest.raises(ValueError, match="проекции"):
            distance_to_layer(cand, layers.roads)


# --------------------------------------------------------------------------- #
#  Основной фильтр
# --------------------------------------------------------------------------- #


class TestContextFilter:
    def test_good_candidate_passes(self, layers):
        # 5 км от центра города (значит вне жилья, в кольце), 100 м от дороги,
        # площадь 3600 м², не пересекает карьер
        cand = candidates_from([square(5_000, 100, 60)])
        out = apply_context_filter(cand, layers, CFG)
        assert out["passes_context"].iat[0], out["reject_reason"].iat[0]
        assert out["reject_reason"].iat[0] is None

    def test_too_small_is_rejected(self, layers):
        cand = candidates_from([square(5_000, 100, 20)])  # 400 м² < 900
        out = apply_context_filter(cand, layers, CFG)
        assert not out["passes_context"].iat[0]
        assert "площадь ниже порога" in out["reject_reason"].iat[0]

    def test_too_large_is_rejected(self, layers):
        cand = candidates_from([square(5_000, 100, 1_000)])  # 1 км² > 500 000 м²
        out = apply_context_filter(cand, layers, CFG)
        assert "это полигон" in out["reject_reason"].iat[0]

    def test_overlapping_known_object_is_rejected(self, layers):
        """Карьер не должен попасть в результаты — про это спросят первым делом."""
        cand = candidates_from([square(10_000, 200, 100)])
        out = apply_context_filter(cand, layers, CFG)
        assert not out["passes_context"].iat[0]
        assert "OSM" in out["reject_reason"].iat[0]

    def test_far_from_road_is_rejected(self, layers):
        # 5 км севернее дороги — самосвал туда не доедет
        cand = candidates_from([square(5_000, 5_000, 60)])
        out = apply_context_filter(cand, layers, CFG)
        assert "нет подъезда" in out["reject_reason"].iat[0]

    def test_too_close_to_settlement_is_rejected(self, layers):
        # Прямо у границы города: 1 км от центра, город — квадрат 2x2 км
        cand = candidates_from([square(1_200, 100, 60)])
        out = apply_context_filter(cand, layers, CFG)
        assert "близко к жилью" in out["reject_reason"].iat[0]

    def test_too_far_from_settlement_is_rejected(self, layers):
        cand = candidates_from([square(40_000, 100, 60)])
        out = apply_context_filter(cand, layers, CFG)
        assert "далеко от жилья" in out["reject_reason"].iat[0]

    def test_every_candidate_gets_a_reason_or_passes(self, layers):
        """Инвариант: не бывает отсеянного кандидата без объяснения."""
        cand = candidates_from(
            [
                square(5_000, 100, 60),      # проходит
                square(5_000, 100, 20),      # мал
                square(10_000, 200, 100),    # карьер
                square(5_000, 5_000, 60),    # нет подъезда
                square(1_200, 100, 60),      # близко к жилью
                square(40_000, 100, 60),     # далеко от жилья
            ]
        )
        out = apply_context_filter(cand, layers, CFG)
        for i in range(len(out)):
            passed = out["passes_context"].iat[i]
            reason = out["reject_reason"].iat[i]
            assert passed == (reason is None)

    def test_reduces_candidate_count(self, layers):
        rng = np.random.default_rng(0)
        geoms = [
            square(float(x), float(y), 60)
            for x, y in rng.uniform(-30_000, 30_000, size=(200, 2))
        ]
        out = apply_context_filter(candidates_from(geoms), layers, CFG)
        passed = int(out["passes_context"].sum())
        # Отсев должен быть жёстким, но не тотальным
        assert 0 < passed < len(out) * 0.5

    def test_reprojects_candidates_if_needed(self, layers):
        cand = gpd.GeoDataFrame({"geometry": [square(5_000, 100, 60)]}, crs=UTM).to_crs("EPSG:4326")
        out = apply_context_filter(cand, layers, CFG)
        assert out.crs.to_string() == UTM
        assert out["passes_context"].iat[0]

    def test_empty_input_is_handled(self, layers):
        empty = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=UTM)
        out = apply_context_filter(empty, layers, CFG)
        assert out.empty


class TestRejectionReport:
    def test_counts_each_reason(self, layers):
        cand = candidates_from(
            [square(5_000, 100, 60), square(5_000, 100, 20), square(10_000, 200, 100)]
        )
        report = rejection_report(apply_context_filter(cand, layers, CFG))
        assert report["ПРОШЁЛ ОТСЕВ"] == 1
        assert sum(report.values()) == 3

    def test_requires_filter_first(self):
        empty = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=UTM)
        with pytest.raises(KeyError):
            rejection_report(empty)


class TestEmptyLayersAreLoud:
    """Пустой слой дорог обязан ронять прогон, а не отсеивать всё молча.

    Ночью 22 августа Overpass не ответил на запросы по Алматы и Шымкенту.
    Слой дорог пришёл пустым, расстояние до дороги стало бесконечным у
    всех кандидатов, и отсев забраковал 499 объектов из 499 с причиной
    «нет подъезда». Отчёт при этом выглядел осмысленно — правдоподобные
    числа по правдоподобным причинам, — и понять, что данных не было
    вовсе, можно было только по отсутствию причины «совпал с объектом
    OSM».

    Молчаливый отказ, дающий правдоподобный результат, опаснее падения:
    падение видно сразу, а такой результат уезжает на сайт.
    """

    def test_empty_roads_raise(self, monkeypatch, tmp_path):
        import geopandas as gpd

        from vantage import context as ctx

        empty = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:32642")
        monkeypatch.setattr(ctx.OverpassClient, "query", lambda *a, **k: {"elements": []})
        monkeypatch.setattr(ctx, "overpass_to_gdf", lambda *a, **k: empty)

        from vantage.config import load_settings

        settings = load_settings()
        aoi = AOI.from_bbox((71.4, 51.1, 71.5, 51.2), name="t",
                            crs_working=settings.project.crs_working)
        with pytest.raises(RuntimeError, match="дорог"):
            ctx.fetch_context(aoi, settings, cache_dir=tmp_path)
