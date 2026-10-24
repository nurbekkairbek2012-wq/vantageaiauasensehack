"""Тесты конфигурации.

Конфигурация — это контракт между кодом и защитой на Q&A. Если в YAML можно
записать бессмысленный порог и код это проглотит, то на сцене выяснится,
что пайплайн считал не то, что написано на слайде.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vantage.config import (
    AoiCfg,
    ContextCfg,
    Economics,
    TimeCfg,
    Triangular,
    load_economics,
    load_settings,
)


class TestRealConfigLoads:
    def test_default_yaml_is_valid(self):
        settings = load_settings()
        assert settings.project.name == "VANTAGE"
        assert settings.sentinel2.resolution_m == 10
        assert "SCL" in settings.sentinel2.bands

    def test_economics_yaml_is_valid(self):
        econ = load_economics()
        assert econ.currency == "KZT"
        density = econ.triangular("waste_density_t_per_m3")
        assert density.min < density.typical < density.max

    def test_chips_channel_count_matches_model_input(self):
        settings = load_settings()
        # 6 спектральных каналов + 3 производных индекса
        assert settings.chips.n_channels == 9

    def test_scl_mask_includes_clouds_and_snow(self):
        settings = load_settings()
        # 8,9 — облака средней и высокой вероятности; 11 — снег.
        # Без снега зимние композиты дадут ложный «голый грунт» по всей степи.
        for cls in (8, 9, 11):
            assert cls in settings.sentinel2.scl_mask_classes


class TestValidation:
    def test_rejects_inverted_bbox(self):
        with pytest.raises(ValidationError):
            AoiCfg(name="bad", bbox=(72.0, 51.0, 70.0, 50.0))

    def test_rejects_out_of_range_bbox(self):
        with pytest.raises(ValidationError):
            AoiCfg(name="bad", bbox=(-200.0, 50.0, 72.0, 51.0))

    def test_rejects_inverted_time_range(self):
        with pytest.raises(ValidationError):
            TimeCfg(start="2026-01-01", end="2018-01-01", composite_freq="MS", valid_months=[6])

    def test_rejects_bad_month(self):
        with pytest.raises(ValidationError):
            TimeCfg(start="2018-01-01", end="2026-01-01", composite_freq="MS", valid_months=[0, 13])

    def test_rejects_inverted_settlement_ring(self):
        with pytest.raises(ValidationError):
            ContextCfg(
                max_distance_to_road_m=300,
                min_distance_to_settlement_m=15000,
                max_distance_to_settlement_m=1500,
                exclude_landuse=[],
                exclude_natural=[],
                min_area_m2=900,
                max_area_m2=500000,
            )


class TestTriangular:
    def test_rejects_wrong_order(self):
        with pytest.raises(ValidationError):
            Triangular(min=10, typical=5, max=20)

    def test_sample_respects_bounds(self):
        import numpy as np

        tri = Triangular(min=1.0, typical=2.0, max=5.0)
        values = tri.sample(np.random.default_rng(0), 10_000)
        assert values.min() >= 1.0
        assert values.max() <= 5.0
        # Мода треугольного распределения смещает среднее к typical
        assert 2.0 < values.mean() < 3.0

    def test_degenerate_distribution_is_constant(self):
        import numpy as np

        tri = Triangular(min=3.0, typical=3.0, max=3.0)
        values = tri.sample(np.random.default_rng(0), 100)
        assert (values == 3.0).all()


class TestSourceAudit:
    """Аудит происхождения экономических величин.

    У каждого числа в денежном слое должно быть одно из трёх объяснений:
    ссылка на документ (source), формула вывода (derived) или честно
    названная инженерная оценка (estimate). Число без объяснения —
    это то, на чём разваливается защита.
    """

    def test_detects_todo_provenance(self):
        econ = Economics(
            currency="KZT",
            raw={
                "a": {"min": 1, "typical": 2, "max": 3, "source": "TODO: найти прайс"},
                "b": {"min": 1, "typical": 2, "max": 3, "source": "Отчёт оператора, 2025"},
                "c": {"min": 1, "typical": 2, "max": 3, "estimate": "оценка по аналогии"},
                "d": {"min": 1, "typical": 2, "max": 3, "derived": "a / b"},
            },
        )
        assert econ.unresolved_sources() == ["a"]
        assert econ.estimated_parameters() == ["c"]
        assert sorted(econ.documented_parameters()) == ["b", "d"]

    def test_real_economics_has_no_todos(self):
        """Все параметры реального файла объяснены."""
        econ = load_economics()
        assert econ.unresolved_sources() == []

    def test_real_economics_declares_its_estimates(self):
        """Оценочные величины названы поимённо, а не спрятаны среди источников."""
        estimated = load_economics().estimated_parameters()
        assert "morphology" in estimated
        assert "recovery_rate" in estimated

    def test_key_documented_values_are_present(self):
        econ = load_economics()
        assert econ.scalar("mrp_kzt", "value") == 4325
        # Плотность ТБО из методики расчёта тарифа
        assert econ.triangular("waste_density_t_per_m3").typical == 0.25
        # Цены приёмок Астаны
        assert econ.triangular("recyclable_price_kzt_per_kg", "plastic").typical == 70

    def test_penalty_is_per_violation_not_per_ton(self):
        """Штраф в РК назначается за факт нарушения, а не за тонну.

        Умножение МРП на массу отходов — методологическая ошибка, которую
        на Q&A заметят немедленно. Тест фиксирует правильную структуру.
        """
        penalty = load_economics().section("penalty")
        article = penalty["articles"]["dumping_with_vehicle"]
        assert article["article"] == "ст. 344, ч. 2-1"
        assert article["mrp"]["individual"] == 100
        assert article["mrp"]["large"] == 1000
        assert "per_ton" not in str(penalty)
