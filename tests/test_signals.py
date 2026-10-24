"""Тесты двух признаков, ортогональных оптике: радара и тепла.

Ради них вся конструкция и держится на пяти признаках, а не на трёх.
Оптика не отличает свалку от карьера и от снегосвалки — эти два
признака отличают, причём в разные стороны:

    карьер       радарно СТАБИЛЕН   (стенки не меняются неделями)
    снегосвалка  ХОЛОДНЕЕ фона      (а свалка теплее)

Именно про снегосвалки в Астане спрашивают на защите обязательно.
"""

from __future__ import annotations

import numpy as np
import pytest

from vantage.sar import (
    FULL_SCALE_INCOHERENCE_DB,
    incoherence_change,
    incoherence_strength,
    std_uncertainty,
    temporal_incoherence,
    to_db,
)
from vantage.thermal import (
    LANDSAT_ST_OFFSET,
    LANDSAT_ST_SCALE,
    anomaly_strength,
    is_snow_dump,
    local_background,
    radius_in_pixels,
    thermal_anomaly,
    to_celsius,
    to_kelvin,
)

# --------------------------------------------------------------------------- #
#  Радар
# --------------------------------------------------------------------------- #


class TestBackscatterConversion:
    def test_db_conversion_is_correct(self):
        # 10*log10(0.1) = -10 дБ
        assert to_db(np.array([0.1]))[0] == pytest.approx(-10.0)

    def test_zero_does_not_become_infinite(self):
        """Нули встречаются на воде и в тени рельефа.

        Без отсечки log10(0) даёт -inf, который отравляет всю
        последующую статистику по временному ряду.
        """
        assert np.isfinite(to_db(np.array([0.0]))[0])

    def test_negative_values_are_clipped(self):
        assert np.isfinite(to_db(np.array([-0.5]))[0])


class TestTemporalIncoherence:
    def test_stable_surface_has_low_incoherence(self):
        """Степь: обратное рассеяние почти не меняется от прохода к проходу."""
        rng = np.random.default_rng(0)
        stable = rng.normal(-12.0, 0.2, (12, 5)).astype("float32")
        assert temporal_incoherence(stable).max() < 0.5

    def test_changing_surface_has_high_incoherence(self):
        rng = np.random.default_rng(0)
        unstable = rng.normal(-12.0, 3.0, (12, 5)).astype("float32")
        assert temporal_incoherence(unstable).min() > 1.5

    def test_insufficient_data_gives_nan_not_zero(self):
        """«Мало данных» и «поверхность стабильна» — разные утверждения.

        Склеить их значит соврать о наличии доказательства.
        """
        sparse = np.full((12, 3), np.nan, dtype="float32")
        sparse[:2, 0] = -12.0
        assert np.isnan(temporal_incoherence(sparse)[0])

    def test_rejects_wrong_shape(self):
        with pytest.raises(ValueError):
            temporal_incoherence(np.zeros(10, dtype="float32"))


class TestIncoherenceChange:
    """Sentinel-1 проходит над точкой каждые 6-12 суток.

    За 8 лет это сотни наблюдений, поэтому в тестах берутся реалистичные
    60 проходов на сегмент, а не десяток: при малой выборке оценка СКО
    сама шумит сильнее искомого эффекта — см. std_uncertainty().
    """

    def test_detects_transition_to_instability(self):
        """Была стабильной, стала нестабильной — вот это и есть признак."""
        rng = np.random.default_rng(1)
        series = np.concatenate(
            [
                rng.normal(-12.0, 0.2, (60, 1)),
                rng.normal(-12.0, 3.0, (60, 1)),
            ]
        ).astype("float32")
        change = incoherence_change(series, np.array([60]))
        assert change[0] > 2.0

    def test_always_unstable_surface_shows_no_change(self):
        """Город и поля нестабильны всегда — абсолютная величина бесполезна,
        смысл имеет только ИЗМЕНЕНИЕ."""
        rng = np.random.default_rng(2)
        series = rng.normal(-10.0, 3.0, (120, 1)).astype("float32")
        change = incoherence_change(series, np.array([60]))
        assert abs(change[0]) < 0.7

    def test_small_sample_is_rejected_not_guessed(self):
        """При десятке наблюдений сегмент честнее пометить как NaN,
        чем выдать случайное число за признак."""
        rng = np.random.default_rng(4)
        series = rng.normal(-12.0, 1.0, (16, 1)).astype("float32")
        assert np.isnan(incoherence_change(series, np.array([8]))[0])

    def test_no_break_gives_nan(self):
        rng = np.random.default_rng(3)
        series = rng.normal(-12.0, 0.5, (120, 1)).astype("float32")
        assert np.isnan(incoherence_change(series, np.array([-1]))[0])

    def test_rejects_length_mismatch(self):
        with pytest.raises(ValueError):
            incoherence_change(np.zeros((10, 3), dtype="float32"), np.array([5, 5]))


class TestSampleSizeSensitivity:
    """Свойство метода, которое надо знать и уметь объяснить."""

    def test_uncertainty_falls_with_sample_size(self):
        assert std_uncertainty(3.0, 10) > std_uncertainty(3.0, 40)

    def test_ten_samples_are_too_noisy_for_the_effect(self):
        """При n=10 погрешность оценки сопоставима с искомым эффектом."""
        assert std_uncertainty(3.0, 10) > 0.6

    def test_forty_samples_are_enough(self):
        assert std_uncertainty(3.0, 40) < 0.4

    def test_degenerate_sample_is_infinite_uncertainty(self):
        assert std_uncertainty(3.0, 1) == float("inf")


class TestIncoherenceStrength:
    def test_full_scale_gives_one(self):
        assert incoherence_strength(FULL_SCALE_INCOHERENCE_DB) == pytest.approx(1.0)

    def test_saturates(self):
        """Поверхность не может стать нестабильной дважды."""
        assert incoherence_strength(30.0) == 1.0

    def test_negative_change_gives_zero(self):
        assert incoherence_strength(-2.0) == 0.0


# --------------------------------------------------------------------------- #
#  Тепло
# --------------------------------------------------------------------------- #


class TestTemperatureConversion:
    def test_landsat_scaling(self):
        dn = 30_000
        assert to_kelvin(np.array([dn]))[0] == pytest.approx(dn * LANDSAT_ST_SCALE + LANDSAT_ST_OFFSET)

    def test_scaled_value_is_physically_plausible(self):
        """Без масштабирования значения выглядят как десятки тысяч,
        и сравнение с порогом в кельвинах даёт бессмыслицу, не падая."""
        kelvin = to_kelvin(np.array([30_000]))[0]
        assert 200 < kelvin < 340

    def test_celsius_conversion(self):
        assert to_celsius(np.array([273.15]))[0] == pytest.approx(0.0, abs=1e-4)


class TestBackgroundAndAnomaly:
    def _scene(self, hot_spot: float = 0.0, size: int = 81) -> np.ndarray:
        """Ровный фон 265 K с пятном в центре."""
        scene = np.full((size, size), 265.0, dtype="float32")
        if hot_spot:
            c = size // 2
            scene[c - 3 : c + 4, c - 3 : c + 4] += hot_spot
        return scene

    def test_uniform_scene_has_no_anomaly(self):
        anomaly = thermal_anomaly(self._scene(), radius_px=10)
        assert abs(anomaly).max() < 0.01

    def test_warm_object_gives_positive_anomaly(self):
        """Тело свалки греется само: разложение органики экзотермично."""
        scene = self._scene(hot_spot=+4.0)
        anomaly = thermal_anomaly(scene, radius_px=10)
        centre = anomaly.shape[0] // 2
        assert anomaly[centre, centre] > 3.0

    def test_snow_dump_gives_negative_anomaly(self):
        """Ключевое различение: снегосвалка ХОЛОДНЕЕ фона.

        Спектрально она почти неотличима от свалки, и в Астане про неё
        спрашивают обязательно. Тепловой канал разделяет их однозначно.
        """
        scene = self._scene(hot_spot=-5.0)
        anomaly = thermal_anomaly(scene, radius_px=10)
        centre = anomaly.shape[0] // 2
        assert anomaly[centre, centre] < -3.0
        assert is_snow_dump(anomaly[centre, centre])

    def test_landfill_is_not_flagged_as_snow_dump(self):
        scene = self._scene(hot_spot=+4.0)
        anomaly = thermal_anomaly(scene, radius_px=10)
        centre = anomaly.shape[0] // 2
        assert not is_snow_dump(anomaly[centre, centre])

    def test_background_is_local_not_global(self):
        """Фон считается по окружению, иначе весь городской остров
        тепла попадёт в аномалии."""
        scene = np.full((81, 81), 265.0, dtype="float32")
        scene[:, 40:] += 6.0  # правая половина сцены теплее
        background = local_background(scene, radius_px=6)
        # Фон справа должен быть выше, чем слева
        assert background[40, 60] > background[40, 20]

    def test_gradient_does_not_create_false_anomaly(self):
        """Плавный градиент температуры не должен давать аномалию:
        иначе склон, обращённый к солнцу, станет «свалкой»."""
        gradient = np.tile(np.linspace(260, 270, 81, dtype="float32"), (81, 1))
        anomaly = thermal_anomaly(gradient, radius_px=6)
        assert abs(anomaly[40, 40]) < 1.0

    def test_rejects_zero_radius(self):
        with pytest.raises(ValueError):
            local_background(self._scene(), radius_px=0)


class TestAnomalyStrength:
    def test_full_scale_gives_one(self):
        assert anomaly_strength(3.0) == pytest.approx(1.0)

    def test_cold_object_gives_zero_not_negative(self):
        """Холодный объект — не слабое доказательство свалки,
        а отсутствие доказательства вообще."""
        assert anomaly_strength(-5.0) == 0.0

    def test_saturates(self):
        assert anomaly_strength(50.0) == 1.0


class TestRadiusConversion:
    def test_metres_to_pixels(self):
        # Landsat: 30 м на пиксель, радиус фона 1000 м
        assert radius_in_pixels(1000, 30) == 33

    def test_never_below_one_pixel(self):
        assert radius_in_pixels(5, 30) == 1

    def test_rejects_bad_resolution(self):
        with pytest.raises(ValueError):
            radius_in_pixels(1000, 0)


# --------------------------------------------------------------------------- #
#  Совместная работа признаков
# --------------------------------------------------------------------------- #


class TestSignalSeparation:
    """Пять признаков вместе разделяют то, что три оптических не разделяют."""

    def test_quarry_profile_differs_from_landfill(self):
        from vantage.explain import physical_evidence

        # Карьер: оптика как у свалки, но радарно стабилен и холодный
        quarry = physical_evidence(
            "Q1", ndvi_drop=0.30, bsi_rise=0.22, pmli_response=0.01,
            sar_incoherence=0.02, thermal_anomaly=0.0,
        )
        # Свалка: те же оптические признаки плюс нестабильность и тепло
        landfill = physical_evidence(
            "L1", ndvi_drop=0.30, bsi_rise=0.22, pmli_response=0.10,
            sar_incoherence=0.45, thermal_anomaly=2.5,
        )
        assert landfill.combined_score > quarry.combined_score * 1.5
        assert landfill.n_agreeing > quarry.n_agreeing

    def test_optical_signals_alone_cannot_separate_them(self):
        """Доказательство необходимости радара и тепла.

        Если убрать эти два признака, карьер и свалка становятся
        неразличимы — что и происходит у решений, работающих
        только на оптике.
        """
        from vantage.explain import physical_evidence

        quarry = physical_evidence("Q1", ndvi_drop=0.30, bsi_rise=0.22)
        landfill = physical_evidence("L1", ndvi_drop=0.30, bsi_rise=0.22)
        assert quarry.combined_score == pytest.approx(landfill.combined_score)
