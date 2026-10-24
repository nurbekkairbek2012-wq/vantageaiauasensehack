"""Тесты сквозного прогона, выгрузки и демонстрационных данных.

Отдельный акцент — на честности демонстрационного режима. Показ
синтетических данных без явной пометки это то, за что дисквалифицируют,
и польза от такого показа нулевая: жюри всё равно спросит, откуда цифры.
Поэтому метка проверяется тестом, а не соглашением.

Второй акцент — на размере выгрузки. Офлайн-карта должна открываться
на телефоне без сети; мегабайтный GeoJSON это ломает молча, без ошибок.
"""

from __future__ import annotations

import json

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import box

from vantage.candidates import GEOJSON_PRECISION, simplify_for_web, to_geojson
from vantage.config import RiskCfg, load_economics, load_settings
from vantage.demo import DEMO_MARKER, build_story, generate_all, generate_candidates
from vantage.pipeline import PIPELINE_STEPS, Pipeline, RunReport, _two_thirds_date, timed
from vantage.risk import dissolve_public

UTM = "EPSG:32642"


# --------------------------------------------------------------------------- #
#  Выгрузка
# --------------------------------------------------------------------------- #


class TestGeoJsonExport:
    def _layer(self) -> gpd.GeoDataFrame:
        rng = np.random.default_rng(0)
        geoms = [
            box(71.6 + rng.random() / 1000, 51.2 + rng.random() / 1000,
                71.601 + rng.random() / 1000, 51.201 + rng.random() / 1000)
            for _ in range(30)
        ]
        return gpd.GeoDataFrame(
            {"candidate_id": [f"C{i:05d}" for i in range(30)], "geometry": geoms}, crs="EPSG:4326"
        )

    def test_precision_reduces_file_size(self, tmp_path):
        """Лишние знаки координат — это нули, которые ничего не означают,
        но замедляют загрузку офлайн-карты."""
        layer = self._layer()
        full = tmp_path / "full.geojson"
        trimmed = tmp_path / "trimmed.geojson"
        layer.to_file(full, driver="GeoJSON")
        to_geojson(layer, trimmed, precision=GEOJSON_PRECISION)
        assert trimmed.stat().st_size < full.stat().st_size

    def test_precision_keeps_positions_accurate(self):
        """Шесть знаков — это около 10 см, на порядки точнее 10 м Sentinel-2."""
        layer = self._layer()
        rounded = simplify_for_web(layer, precision=6)
        before = layer.geometry.iloc[0].centroid
        after = rounded.geometry.iloc[0].centroid
        assert abs(before.x - after.x) < 1e-5
        assert abs(before.y - after.y) < 1e-5

    def test_export_writes_valid_geojson(self, tmp_path):
        path = tmp_path / "out.geojson"
        to_geojson(self._layer(), path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["type"] == "FeatureCollection"
        assert len(payload["features"]) == 30


class TestDissolvePublic:
    def _grid(self, n: int = 40) -> gpd.GeoDataFrame:
        """Связные зоны риска — так выглядит настоящая карта.

        Риск пространственно скоррелирован: свалки растут кустами, и
        зоны получаются сплошными, а не рассыпанными в шахматном порядке.
        """
        cells, classes = [], []
        centre = n / 2
        for i in range(n):
            for j in range(n):
                cells.append(box(i * 1000, j * 1000, (i + 1) * 1000, (j + 1) * 1000))
                distance = np.hypot(i - centre, j - centre) / centre
                classes.append(int(np.clip(4 - round(distance * 3), 1, 4)))
        return gpd.GeoDataFrame({"risk_class": classes, "geometry": cells}, crs=UTM)

    def _checkerboard(self, n: int = 40) -> gpd.GeoDataFrame:
        cells, classes = [], []
        for i in range(n):
            for j in range(n):
                cells.append(box(i * 1000, j * 1000, (i + 1) * 1000, (j + 1) * 1000))
                classes.append(1 + (i + j) % 4)
        return gpd.GeoDataFrame({"risk_class": classes, "geometry": cells}, crs=UTM)

    def test_merges_into_one_feature_per_class(self):
        dissolved = dissolve_public(self._grid())
        assert len(dissolved) == 3  # классы 2, 3, 4; первый отброшен
        assert set(dissolved["risk_class"]) == {2, 3, 4}

    def test_drops_lowest_class(self):
        """Класс минимального риска занимает большую часть площади,
        неотличим от фона и нужен только для раздувания файла."""
        assert 1 not in set(dissolve_public(self._grid())["risk_class"])

    def test_can_keep_lowest_class(self):
        assert 1 in set(dissolve_public(self._grid(), drop_lowest=False)["risk_class"])

    def test_reduces_output_size(self, tmp_path):
        """На связных зонах растворение даёт кратное сокращение файла:
        общие границы соседних ячеек перестают храниться дважды."""
        grid = self._grid()
        raw = tmp_path / "raw.geojson"
        merged = tmp_path / "merged.geojson"
        grid.to_file(raw, driver="GeoJSON")
        dissolve_public(grid).to_file(merged, driver="GeoJSON")
        assert merged.stat().st_size < raw.stat().st_size / 10

    def test_worst_case_still_helps(self, tmp_path):
        """Шахматный узор — худший случай: сливать нечего, у каждой
        ячейки все соседи другого класса. Даже здесь выигрыш есть,
        но небольшой. В реальности зоны риска связные."""
        grid = self._checkerboard()
        raw = tmp_path / "raw.geojson"
        merged = tmp_path / "merged.geojson"
        grid.to_file(raw, driver="GeoJSON")
        dissolve_public(grid).to_file(merged, driver="GeoJSON")
        assert merged.stat().st_size < raw.stat().st_size

    def test_empty_input_is_handled(self):
        empty = gpd.GeoDataFrame({"risk_class": [], "geometry": []}, crs=UTM)
        assert dissolve_public(empty).empty


# --------------------------------------------------------------------------- #
#  Демонстрационные данные
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def demo_candidates():
    from vantage.aoi import AOI

    settings = load_settings()
    return generate_candidates(AOI.from_settings(settings), load_economics(), n=8, seed=7)


class TestDemoData:
    def test_every_object_is_marked(self, demo_candidates):
        """ГЛАВНАЯ ПРОВЕРКА МОДУЛЯ.

        Синтетические данные без явной пометки — прямой путь к
        дисквалификации, и польза от них нулевая.
        """
        assert demo_candidates["is_demo"].all()

    def test_story_is_marked(self, demo_candidates):
        story = build_story(demo_candidates, is_demo=True)
        assert story["is_demo"] is True
        assert "СИНТЕТИЧЕСКИЕ" in story["warning"]

    def test_story_of_real_run_is_not_marked(self, demo_candidates):
        """Обратная сторона той же проверки.

        Пометка обязана исчезать, когда данные настоящие: пока
        ``build_story`` ставила её безусловно, результат честного прогона
        выходил помеченным как отладочный, и показать его было нельзя.
        """
        story = build_story(demo_candidates, is_demo=False)
        assert story["is_demo"] is False
        assert "warning" not in story

    def test_marker_shouts(self):
        assert DEMO_MARKER["is_demo"] is True
        assert DEMO_MARKER["warning"].isupper() or "СИНТЕТИЧ" in DEMO_MARKER["warning"]

    def test_objects_have_all_five_signals(self, demo_candidates):
        for column in ("ndvi_drop", "bsi_rise", "pmli_response", "sar_incoherence", "thermal_anomaly"):
            assert column in demo_candidates.columns

    def test_money_is_ordered(self, demo_candidates):
        assert (demo_candidates["damage_p10"] <= demo_candidates["damage_p50"]).all()
        assert (demo_candidates["damage_p50"] <= demo_candidates["damage_p90"]).all()

    def test_polygons_are_irregular(self, demo_candidates):
        """Свалка не бывает прямоугольной — прямоугольник на карте
        сразу выдал бы синтетику даже без пометки."""
        for geom in demo_candidates.geometry:
            assert len(geom.exterior.coords) > 5

    def test_generation_is_reproducible(self):
        from vantage.aoi import AOI

        settings = load_settings()
        aoi = AOI.from_settings(settings)
        econ = load_economics()
        first = generate_candidates(aoi, econ, n=5, seed=3)
        second = generate_candidates(aoi, econ, n=5, seed=3)
        assert list(first["candidate_id"]) == list(second["candidate_id"])
        assert first["area_m2"].tolist() == pytest.approx(second["area_m2"].tolist())


class TestStory:
    def test_scene_sequence_matches_the_pitch(self, demo_candidates):
        """Сценарий фиксирует порядок сцен заранее: под стрессом на сцене
        выступающий забывает, куда кликать."""
        ids = [scene["id"] for scene in build_story(demo_candidates)["scenes"]]
        assert ids == ["registry", "found", "evidence", "money", "act", "risk", "mistake"]

    def test_every_scene_has_a_line(self, demo_candidates):
        for scene in build_story(demo_candidates)["scenes"]:
            assert scene["line"].strip()
            assert scene["title"].strip()

    def test_totals_are_computed(self, demo_candidates):
        totals = build_story(demo_candidates)["totals"]
        assert totals["objects"] == len(demo_candidates)
        assert totals["damage_p10"] < totals["damage_p90"]

    def test_focus_points_are_in_wgs84(self, demo_candidates):
        focus = build_story(demo_candidates)["scenes"][2]["focus"]
        lon, lat = focus["center"]
        assert 70 < lon < 73
        assert 50 < lat < 52


class TestGenerateAll:
    def test_writes_all_artifacts(self, tmp_path):
        written = generate_all(tmp_path, n=6, seed=11)
        for name in ("candidates", "risk_private", "risk_public", "story"):
            assert name in written
            assert written[name].exists()

    def test_public_layer_is_small_enough_for_offline(self, tmp_path):
        """Офлайн-карта должна открываться на телефоне без сети.
        Мегабайтный GeoJSON ломает это молча, без ошибок."""
        written = generate_all(tmp_path, n=6, seed=11)
        assert written["risk_public"].stat().st_size < 600_000

    def test_public_layer_has_no_exact_risk(self, tmp_path):
        written = generate_all(tmp_path, n=6, seed=11)
        payload = json.loads(written["risk_public"].read_text(encoding="utf-8"))
        for feature in payload["features"]:
            assert "risk" not in feature["properties"]
            assert "risk_class" in feature["properties"]


# --------------------------------------------------------------------------- #
#  Оркестратор
# --------------------------------------------------------------------------- #


class TestPipeline:
    def test_steps_are_declared_in_order(self):
        assert PIPELINE_STEPS[0] == "scenes"
        assert PIPELINE_STEPS[-1] == "export"

    def test_creates_output_directory(self, tmp_path):
        pipeline = Pipeline(outputs=tmp_path / "out")
        assert pipeline.outputs.exists()

    def test_skips_existing_artifacts_unless_forced(self, tmp_path):
        pipeline = Pipeline(outputs=tmp_path)
        (tmp_path / "scenes.json").write_text("{}", encoding="utf-8")
        assert pipeline.exists("scenes.json")

        forced = Pipeline(outputs=tmp_path, force=True)
        assert not forced.exists("scenes.json")

    def test_report_records_steps(self, tmp_path):
        pipeline = Pipeline(outputs=tmp_path)
        pipeline.report.record("scenes", seconds=1.234, found=42)
        assert pipeline.report.steps["scenes"]["found"] == 42
        assert pipeline.report.steps["scenes"]["seconds"] == 1.2

    def test_finish_writes_run_report(self, tmp_path):
        pipeline = Pipeline(outputs=tmp_path)
        path = pipeline.finish()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["finished_at"]
        assert payload["aoi"]["area_km2"] > 0

    def test_money_step_adds_columns(self, tmp_path, demo_candidates):
        pipeline = Pipeline(outputs=tmp_path)
        priced = pipeline.step_money(demo_candidates[["candidate_id", "area_m2", "geometry"]].copy())
        for column in ("damage_p10", "damage_p50", "damage_p90", "penalty_kzt"):
            assert column in priced.columns

    def test_export_writes_geojson(self, tmp_path, demo_candidates):
        pipeline = Pipeline(outputs=tmp_path)
        written = pipeline.step_export(demo_candidates)
        assert "candidates" in written
        assert (tmp_path / "candidates.geojson").exists()

    def test_export_handles_empty_input(self, tmp_path):
        pipeline = Pipeline(outputs=tmp_path)
        empty = gpd.GeoDataFrame({"geometry": []}, crs=UTM)
        assert pipeline.step_export(empty) == {}


class TestCutoffDate:
    def test_splits_two_thirds(self):
        """Две трети на обучение, треть на честную проверку прогноза."""
        cutoff = _two_thirds_date("2018-01-01", "2027-01-01")
        assert cutoff.startswith("2024")

    def test_cutoff_is_inside_the_period(self):
        cutoff = _two_thirds_date("2020-01-01", "2026-01-01")
        assert "2020-01-01" < cutoff < "2026-01-01"


class TestTimed:
    def test_returns_result_and_duration(self):
        value, seconds = timed(lambda x: x * 2, 21)
        assert value == 42
        assert seconds >= 0


class TestRunReport:
    def test_serialises(self):
        report = RunReport(started_at="2026-08-17T10:00:00", aoi_name="astana", aoi_area_km2=4834.2)
        report.record("scenes", seconds=3.0, count=1574)
        payload = report.as_dict()
        assert payload["aoi"]["name"] == "astana"
        assert payload["steps"]["scenes"]["count"] == 1574


class TestRiskConfigGuard:
    def test_public_grid_must_not_be_finer(self):
        settings = load_settings()
        bad = RiskCfg(**{**settings.risk.__dict__, "public_grid_cell_m": 10})
        grid = gpd.GeoDataFrame(
            {"risk": [0.5], "geometry": [box(0, 0, 500, 500)]}, crs=UTM
        )
        from vantage.risk import aggregate_public

        with pytest.raises(ValueError):
            aggregate_public(grid, bad)
