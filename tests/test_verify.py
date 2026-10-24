"""Тесты доверификации.

Сеть в тестах не используется: провайдеры подменяются заглушками.
Тест, зависящий от доступности внешнего тайлового сервера, начнёт падать
в самый неудобный момент и приучит команду игнорировать красный прогон.

Тайловая арифметика проверяется по известным опорным точкам: ошибка в
формуле не падает, она просто показывает не то место на Земле.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import box

from vantage.config import VerifyCfg
from vantage.verify import (
    PROVIDERS,
    TileProvider,
    VerificationResult,
    attach_verification,
    deg2tile,
    ground_resolution_m,
    quadkey,
    texture_score,
    tile2deg,
    verify_candidates,
)

ASTANA_LAT, ASTANA_LON = 51.1694, 71.4491

CFG = VerifyCfg(
    providers=["esri_current", "bing"],
    zoom=17,
    tile_grid=3,
    timeout_s=5,
    max_candidates=10,
    min_agreeing_providers=2,
)


# --------------------------------------------------------------------------- #
#  Тайловая арифметика
# --------------------------------------------------------------------------- #


class TestTileMath:
    def test_known_reference_point(self):
        """Нулевой тайл на нулевом зуме покрывает всю Землю."""
        assert deg2tile(0.0, 0.0, 0) == (0, 0)

    def test_greenwich_equator_at_zoom_one(self):
        """На зуме 1 мир делится на 4 тайла; (0,0) — северо-западный."""
        assert deg2tile(45.0, -90.0, 1) == (0, 0)
        assert deg2tile(-45.0, 90.0, 1) == (1, 1)

    def test_astana_tile_is_stable(self):
        x, y = deg2tile(ASTANA_LAT, ASTANA_LON, 12)
        # Обратное преобразование должно вернуть точку рядом с исходной
        lat, lon = tile2deg(x, y, 12)
        assert abs(lat - ASTANA_LAT) < 0.1
        assert abs(lon - ASTANA_LON) < 0.1

    def test_roundtrip_stays_within_tile(self):
        """tile2deg возвращает СЕВЕРО-ЗАПАДНЫЙ угол тайла.

        Значит, чтобы остаться внутри тайла, надо сдвинуться на юг
        (широта уменьшается) и на восток (долгота растёт). Сдвиг на
        север выведет в соседний тайл — и это правильное поведение.
        """
        for zoom in (10, 14, 17):
            x, y = deg2tile(ASTANA_LAT, ASTANA_LON, zoom)
            lat, lon = tile2deg(x, y, zoom)
            assert deg2tile(lat - 1e-6, lon + 1e-6, zoom) == (x, y)

    def test_north_of_corner_lands_in_previous_tile(self):
        x, y = deg2tile(ASTANA_LAT, ASTANA_LON, 14)
        lat, lon = tile2deg(x, y, 14)
        assert deg2tile(lat + 1e-6, lon + 1e-6, 14) == (x, y - 1)

    def test_poles_are_clamped_not_infinite(self):
        """Web Mercator не определён у полюсов — координата обязана
        ограничиваться, а не улетать в бесконечность."""
        _, y = deg2tile(89.9, 0.0, 10)
        assert 0 <= y < 2**10

    def test_rejects_invalid_longitude(self):
        with pytest.raises(ValueError):
            deg2tile(51.0, 200.0, 10)

    def test_tile_index_never_exceeds_grid(self):
        for zoom in range(1, 19):
            x, y = deg2tile(85.0, 179.99, zoom)
            assert x < 2**zoom and y < 2**zoom


class TestQuadkey:
    def test_known_values(self):
        # Опорные значения из документации Bing Maps
        assert quadkey(0, 0, 1) == "0"
        assert quadkey(1, 1, 1) == "3"
        assert quadkey(3, 5, 3) == "213"

    def test_length_equals_zoom(self):
        assert len(quadkey(1000, 2000, 15)) == 15

    def test_uses_only_digits_0_to_3(self):
        assert set(quadkey(12345, 23456, 17)) <= {"0", "1", "2", "3"}


class TestResolution:
    def test_astana_resolution_at_zoom_17(self):
        """На зуме 17 в Астане пиксель порядка 0.7 м — это и есть
        честный ответ на вопрос о разрешении доверификации."""
        res = ground_resolution_m(ASTANA_LAT, 17)
        assert 0.5 < res < 1.0

    def test_resolution_improves_with_latitude(self):
        assert ground_resolution_m(60.0, 17) < ground_resolution_m(0.0, 17)

    def test_resolution_halves_per_zoom_level(self):
        assert ground_resolution_m(51.0, 18) == pytest.approx(
            ground_resolution_m(51.0, 17) / 2
        )


# --------------------------------------------------------------------------- #
#  Текстурный анализ
# --------------------------------------------------------------------------- #


def smooth_image(size: int = 128) -> np.ndarray:
    """Ровное поле: плавный градиент без структуры."""
    gradient = np.linspace(60, 90, size, dtype="float32")
    base = np.tile(gradient, (size, 1))
    return np.stack([base, base * 1.05, base * 0.9], axis=2).astype("uint8")


def chaotic_image(size: int = 128, seed: int = 0) -> np.ndarray:
    """Свалка: множество мелких объектов разного цвета и яркости."""
    rng = np.random.default_rng(seed)
    image = np.full((size, size, 3), 80, dtype="uint8")
    for _ in range(400):
        y, x = rng.integers(0, size - 6, 2)
        h, w = rng.integers(2, 7, 2)
        image[y : y + h, x : x + w] = rng.integers(0, 255, 3)
    return image


class TestTextureScore:
    def test_chaos_scores_higher_than_smooth(self):
        assert texture_score(chaotic_image())["texture_score"] > texture_score(smooth_image())["texture_score"]

    def test_smooth_surface_scores_low(self):
        assert texture_score(smooth_image())["texture_score"] < 0.2

    def test_chaotic_surface_scores_high(self):
        assert texture_score(chaotic_image())["texture_score"] > 0.5

    def test_score_is_bounded(self):
        rng = np.random.default_rng(0)
        noise = rng.integers(0, 255, (128, 128, 3), dtype=np.uint8)
        score = texture_score(noise)["texture_score"]
        assert 0.0 <= score <= 1.0

    def test_all_components_are_reported(self):
        result = texture_score(chaotic_image())
        assert set(result) == {"edge_density", "local_variance", "color_spread", "texture_score"}

    def test_uniform_image_has_no_edges(self):
        flat = np.full((64, 64, 3), 120, dtype="uint8")
        assert texture_score(flat)["edge_density"] == 0.0

    def test_rejects_non_rgb_input(self):
        with pytest.raises(ValueError):
            texture_score(np.zeros((64, 64), dtype="uint8"))


# --------------------------------------------------------------------------- #
#  Проход доверификации с подменёнными провайдерами
# --------------------------------------------------------------------------- #


def make_candidates(n: int = 3):
    return gpd.GeoDataFrame(
        {
            "candidate_id": [f"C{i:05d}" for i in range(n)],
            "probability": np.linspace(0.9, 0.5, n),
            "geometry": [box(350_000 + i * 500, 5_670_000, 350_060 + i * 500, 5_670_060) for i in range(n)],
        },
        crs="EPSG:32642",
    )


@pytest.fixture
def fake_providers(monkeypatch):
    """Подменяем сетевую загрузку: первый провайдер отдаёт хаос,
    второй — гладкую картинку, третий всегда падает."""
    import vantage.verify as verify_module

    def fake_fetch(provider, lat, lon, zoom, grid=3, *, timeout=20):
        if provider.name == "broken":
            raise verify_module.TileFetchError("недоступен")
        if provider.name == "chaotic":
            return chaotic_image()
        return smooth_image()

    monkeypatch.setattr(verify_module, "fetch_tile_grid", fake_fetch)
    return {
        "chaotic": TileProvider("chaotic", "http://x/{z}/{y}/{x}", "test"),
        "smooth": TileProvider("smooth", "http://x/{z}/{y}/{x}", "test"),
        "broken": TileProvider("broken", "http://x/{z}/{y}/{x}", "test"),
    }


class TestVerifyCandidates:
    def test_records_working_and_failing_providers(self, fake_providers):
        cfg = VerifyCfg(
            providers=["chaotic", "smooth", "broken"], zoom=17, tile_grid=3,
            timeout_s=5, max_candidates=10, min_agreeing_providers=2,
        )
        results = verify_candidates(make_candidates(2), cfg, providers=fake_providers)
        assert results[0].providers_ok == ["chaotic", "smooth"]
        assert results[0].providers_failed == ["broken"]

    def test_single_provider_failure_does_not_stop_the_run(self, fake_providers):
        """Ради этого и вводилась мультипровайдерность."""
        cfg = VerifyCfg(
            providers=["broken", "chaotic"], zoom=17, tile_grid=3,
            timeout_s=5, max_candidates=10, min_agreeing_providers=1,
        )
        results = verify_candidates(make_candidates(1), cfg, providers=fake_providers)
        assert results[0].n_providers == 1
        assert results[0].is_confirmed(cfg)

    def test_respects_max_candidates_limit(self, fake_providers):
        cfg = VerifyCfg(
            providers=["chaotic"], zoom=17, tile_grid=3,
            timeout_s=5, max_candidates=2, min_agreeing_providers=1,
        )
        assert len(verify_candidates(make_candidates(10), cfg, providers=fake_providers)) == 2

    def test_processes_most_probable_first(self, fake_providers):
        cfg = VerifyCfg(
            providers=["chaotic"], zoom=17, tile_grid=3,
            timeout_s=5, max_candidates=2, min_agreeing_providers=1,
        )
        results = verify_candidates(make_candidates(5), cfg, providers=fake_providers)
        assert [r.candidate_id for r in results] == ["C00000", "C00001"]

    def test_unknown_provider_is_skipped_with_warning(self, fake_providers):
        cfg = VerifyCfg(
            providers=["не_существует", "chaotic"], zoom=17, tile_grid=3,
            timeout_s=5, max_candidates=5, min_agreeing_providers=1,
        )
        results = verify_candidates(make_candidates(1), cfg, providers=fake_providers)
        assert results[0].providers_ok == ["chaotic"]

    def test_vlm_verdict_overrides_texture_when_confident(self, fake_providers):
        class StubVlm:
            def verify(self, image, prompt):
                return {"is_landfill": False, "confidence": 0.95, "reasoning": "это карьер"}

        cfg = VerifyCfg(
            providers=["chaotic"], zoom=17, tile_grid=3,
            timeout_s=5, max_candidates=1, min_agreeing_providers=1,
        )
        results = verify_candidates(make_candidates(1), cfg, vlm=StubVlm(), providers=fake_providers)
        # Текстура высокая, но зрительная модель уверенно говорит «нет»
        assert results[0].scores["chaotic"] > 0.5
        assert not results[0].is_confirmed(cfg)

    def test_low_confidence_vlm_does_not_override(self, fake_providers):
        class UnsureVlm:
            def verify(self, image, prompt):
                return {"is_landfill": False, "confidence": 0.3, "reasoning": "не уверен"}

        cfg = VerifyCfg(
            providers=["chaotic"], zoom=17, tile_grid=3,
            timeout_s=5, max_candidates=1, min_agreeing_providers=1,
        )
        results = verify_candidates(make_candidates(1), cfg, vlm=UnsureVlm(), providers=fake_providers)
        assert results[0].is_confirmed(cfg)


class TestConfirmation:
    def test_requires_minimum_providers(self):
        result = VerificationResult("C1", providers_ok=["a"], scores={"a": 0.9})
        assert not result.is_confirmed(CFG)  # нужно два, есть один

    def test_confirms_with_enough_providers_and_texture(self):
        result = VerificationResult("C1", providers_ok=["a", "b"], scores={"a": 0.8, "b": 0.7})
        assert result.is_confirmed(CFG)

    def test_low_texture_is_not_confirmed(self):
        result = VerificationResult("C1", providers_ok=["a", "b"], scores={"a": 0.05, "b": 0.02})
        assert not result.is_confirmed(CFG)


class TestAttach:
    def test_adds_columns_for_all_candidates(self, fake_providers):
        cfg = VerifyCfg(
            providers=["chaotic"], zoom=17, tile_grid=3,
            timeout_s=5, max_candidates=2, min_agreeing_providers=1,
        )
        candidates = make_candidates(4)
        results = verify_candidates(candidates, cfg, providers=fake_providers)
        merged = attach_verification(candidates, results, cfg)
        assert len(merged) == 4
        # Непроверенные кандидаты помечаются, а не выбрасываются
        assert merged["verify_confirmed"].sum() == 2
        assert merged["verify_providers"].tolist() == [1, 1, 0, 0]


class TestProviderRegistry:
    def test_all_providers_have_attribution(self):
        """Атрибуция обязательна по условиям использования базовых карт."""
        for provider in PROVIDERS.values():
            assert provider.attribution

    def test_bing_uses_quadkey_scheme(self):
        assert PROVIDERS["bing"].scheme == "quadkey"
        assert "{q}" in PROVIDERS["bing"].url_template

    def test_xyz_providers_have_all_placeholders(self):
        for provider in PROVIDERS.values():
            if provider.scheme == "xyz":
                for token in ("{x}", "{y}", "{z}"):
                    assert token in provider.url_template
