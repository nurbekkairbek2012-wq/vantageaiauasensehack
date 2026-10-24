"""Тесты спектральных индексов.

Индексы — фундамент всей детекции, поэтому проверяются не «что функция
не падает», а физический смысл: знак, диапазон, поведение на вырожденных
входах и известные ловушки Sentinel-2.
"""

from __future__ import annotations

import numpy as np
import pytest

from vantage.indices import (
    S2_BASELINE_04_OFFSET,
    baseline_offset_for,
    bsi,
    ndvi,
    ndwi,
    nbr,
    pmli,
    scale_reflectance,
)


class TestNDVI:
    def test_vegetation_is_positive(self):
        # Растительность: высокое NIR, низкое красное
        assert ndvi(np.array([0.45]), np.array([0.05]))[0] > 0.5

    def test_bare_soil_is_near_zero(self):
        # Голый грунт: NIR и красное близки
        value = ndvi(np.array([0.25]), np.array([0.22]))[0]
        assert -0.2 < value < 0.2

    def test_range_is_bounded(self):
        rng = np.random.default_rng(0)
        nir = rng.uniform(0.01, 1.0, 1000)
        red = rng.uniform(0.01, 1.0, 1000)
        values = ndvi(nir, red)
        assert np.all(values >= -1.0) and np.all(values <= 1.0)

    def test_zero_sum_gives_nan_not_inf(self):
        # Деление на ноль должно давать NaN (пропуск), а не бесконечность,
        # иначе одно битое значение отравит всю статистику временного ряда.
        value = ndvi(np.array([0.0]), np.array([0.0]))[0]
        assert np.isnan(value)


class TestBSI:
    def test_bare_soil_higher_than_vegetation(self):
        # грунт: SWIR и красное высокие, NIR умеренное
        soil = bsi(np.array([0.35]), np.array([0.28]), np.array([0.30]), np.array([0.15]))[0]
        # растительность: NIR высокое, SWIR низкое
        veg = bsi(np.array([0.15]), np.array([0.05]), np.array([0.45]), np.array([0.04]))[0]
        assert soil > veg


class TestPMLI:
    def test_responds_to_swir_excess(self):
        high = pmli(np.array([0.40]), np.array([0.15]))[0]
        low = pmli(np.array([0.15]), np.array([0.15]))[0]
        assert high > low
        assert low == pytest.approx(0.0, abs=1e-9)


class TestNDWI:
    def test_water_is_positive(self):
        # Вода: зелёное заметно выше NIR
        assert ndwi(np.array([0.12]), np.array([0.02]))[0] > 0.5

    def test_land_is_negative(self):
        assert ndwi(np.array([0.08]), np.array([0.30]))[0] < 0


class TestNBR:
    def test_burned_surface_lower_than_healthy(self):
        healthy = nbr(np.array([0.45]), np.array([0.10]))[0]
        burned = nbr(np.array([0.12]), np.array([0.25]))[0]
        assert burned < healthy


class TestReflectanceScaling:
    def test_dn_to_reflectance(self):
        assert scale_reflectance(np.array([2500.0]))[0] == pytest.approx(0.25)

    def test_baseline_offset_applied_after_2022_01_25(self):
        """Ловушка Sentinel-2: с Processing Baseline 04.00 добавлено смещение -1000.

        Если его не учесть, все индексы скачком меняются на границе января
        2022 года, и детектор изменений находит «разрыв» одновременно во всей
        области. Это самая частая причина ложных срабатываний по всему AOI.
        """
        dates = np.array(["2021-06-01", "2022-06-01"], dtype="datetime64[D]")
        offsets = baseline_offset_for(dates)
        assert offsets[0] == 0.0
        assert offsets[1] == S2_BASELINE_04_OFFSET

    def test_offset_removes_artificial_jump(self):
        # Один и тот же физический объект, снятый до и после смены baseline
        dn_before = np.array([2500.0])
        dn_after = np.array([3500.0])  # тот же объект, но DN сдвинут на +1000
        r_before = scale_reflectance(dn_before, offset=0.0)
        r_after = scale_reflectance(dn_after, offset=S2_BASELINE_04_OFFSET)
        assert r_before[0] == pytest.approx(r_after[0])
