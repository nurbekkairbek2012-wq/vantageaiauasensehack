"""Тесты денежного слоя и модели метана.

Проверяется не арифметика ради арифметики, а те свойства, на которых
разваливается защита:

  * масштабируемость — вдвое большая свалка стоит примерно вдвое дороже;
  * честный интервал — P10 < P50 < P90, и интервал не абсурдно широкий;
  * воспроизводимость — один seed даёт один результат;
  * методология штрафа — он зависит от категории нарушителя, а не от массы;
  * физика метана — эмиссия растёт с массой и насыщается со временем.
"""

from __future__ import annotations

import numpy as np
import pytest

from vantage import methane
from vantage.config import load_economics
from vantage.money import assess, sensitivity


@pytest.fixture(scope="module")
def econ():
    return load_economics()


# --------------------------------------------------------------------------- #
#  Модель метана
# --------------------------------------------------------------------------- #


class TestMethane:
    def test_zero_mass_gives_zero_emission(self):
        assert methane.cumulative_ch4(
            0.0, doc_fraction=0.15, doc_f=0.5, mcf=0.4, f_ch4=0.5, oxidation=0.1, k=0.06, years=20
        ) == 0.0

    def test_emission_scales_linearly_with_mass(self):
        kwargs = dict(doc_fraction=0.15, doc_f=0.5, mcf=0.4, f_ch4=0.5, oxidation=0.1, k=0.06, years=20)
        single = methane.cumulative_ch4(1000.0, **kwargs)
        double = methane.cumulative_ch4(2000.0, **kwargs)
        assert double == pytest.approx(2 * single)

    def test_emission_saturates_over_time(self):
        """За бесконечное время выделяется весь доступный углерод, не больше."""
        kwargs = dict(doc_fraction=0.15, doc_f=0.5, mcf=0.4, f_ch4=0.5, oxidation=0.1, k=0.06)
        at_20 = methane.cumulative_ch4(1000.0, years=20, **kwargs)
        at_100 = methane.cumulative_ch4(1000.0, years=100, **kwargs)
        ceiling = (
            methane.degradable_carbon(1000.0, 0.15, 0.5, 0.4)
            * 0.5
            * methane.CH4_PER_CARBON
            * 0.9
        )
        assert at_20 < at_100 <= ceiling * 1.0001
        assert at_100 == pytest.approx(ceiling, rel=0.01)

    def test_oxidation_reduces_emission(self):
        kwargs = dict(doc_fraction=0.15, doc_f=0.5, mcf=0.4, f_ch4=0.5, k=0.06, years=20)
        with_ox = methane.cumulative_ch4(1000.0, oxidation=0.10, **kwargs)
        without_ox = methane.cumulative_ch4(1000.0, oxidation=0.0, **kwargs)
        assert with_ox == pytest.approx(without_ox * 0.9)

    def test_annual_profile_sums_to_cumulative(self, econ):
        params = methane.from_config(econ)
        profile = methane.annual_ch4_profile(1000.0, params)
        total = methane.cumulative_ch4(
            1000.0,
            doc_fraction=params.doc_fraction,
            doc_f=params.doc_f,
            mcf=params.methane_correction_factor,
            f_ch4=params.f_ch4_in_gas,
            oxidation=params.oxidation_factor,
            k=params.k_rate_per_year,
            years=params.horizon_years,
        )
        assert profile.sum() == pytest.approx(total, rel=1e-9)

    def test_profile_is_monotonically_decreasing(self, econ):
        """Свалка «дымит» сильнее всего в первые годы — так работает FOD."""
        profile = methane.annual_ch4_profile(1000.0, methane.from_config(econ))
        assert np.all(np.diff(profile) < 0)

    def test_gwp_horizon_matters(self):
        ch4 = 10.0
        assert methane.to_co2e(ch4, 82.5) > methane.to_co2e(ch4, 29.8)

    def test_rejects_invalid_parameters(self, econ):
        params = methane.from_config(econ)
        with pytest.raises(ValueError):
            methane.MethaneParams(**{**params.__dict__, "doc_fraction": 1.5})
        with pytest.raises(ValueError):
            methane.MethaneParams(**{**params.__dict__, "k_rate_per_year": 0.0})


# --------------------------------------------------------------------------- #
#  Денежный слой
# --------------------------------------------------------------------------- #


class TestAssessment:
    def test_produces_ordered_percentiles(self, econ):
        a = assess(5_000, econ, iterations=5_000, seed=1)
        for band in (a.volume_m3, a.mass_t, a.removal_cost_kzt, a.net_damage_kzt):
            assert band.p10 < band.p50 < band.p90

    def test_interval_is_informative_not_absurd(self, econ):
        """P90/P10 должен быть в разумных пределах.

        Если бы диапазон считался перемножением крайних значений допущений,
        отношение было бы в десятки раз — такой «интервал» бесполезен.
        Монте-Карло даёт значительно более узкую и честную оценку.
        """
        a = assess(5_000, econ, iterations=20_000, seed=1)
        ratio = a.removal_cost_kzt.p90 / a.removal_cost_kzt.p10
        assert 1.5 < ratio < 12.0

    def test_scales_with_area(self, econ):
        small = assess(1_000, econ, iterations=5_000, seed=3)
        big = assess(2_000, econ, iterations=5_000, seed=3)
        assert big.mass_t.p50 == pytest.approx(2 * small.mass_t.p50, rel=0.02)
        assert big.removal_cost_kzt.p50 == pytest.approx(2 * small.removal_cost_kzt.p50, rel=0.02)

    def test_depth_class_changes_volume(self, econ):
        shallow = assess(5_000, econ, depth_class="shallow", iterations=5_000, seed=5)
        deep = assess(5_000, econ, depth_class="deep", iterations=5_000, seed=5)
        assert deep.volume_m3.p50 > shallow.volume_m3.p50 * 3

    def test_distance_increases_removal_cost(self, econ):
        near = assess(5_000, econ, distance_to_landfill_km=10, iterations=5_000, seed=7)
        far = assess(5_000, econ, distance_to_landfill_km=60, iterations=5_000, seed=7)
        assert far.removal_cost_kzt.p50 > near.removal_cost_kzt.p50

    def test_distance_below_base_radius_has_no_surcharge(self, econ):
        """Надбавка не должна быть отрицательной при близком полигоне."""
        at_base = assess(5_000, econ, distance_to_landfill_km=15, iterations=3_000, seed=9)
        closer = assess(5_000, econ, distance_to_landfill_km=2, iterations=3_000, seed=9)
        assert closer.removal_cost_kzt.p50 == pytest.approx(at_base.removal_cost_kzt.p50)

    def test_reproducible_with_same_seed(self, econ):
        first = assess(5_000, econ, iterations=3_000, seed=11)
        second = assess(5_000, econ, iterations=3_000, seed=11)
        assert first.net_damage_kzt.as_dict() == second.net_damage_kzt.as_dict()

    def test_recyclable_value_is_positive_and_bounded(self, econ):
        a = assess(5_000, econ, iterations=5_000, seed=13)
        assert a.recyclable_value_kzt.p50 > 0
        # Сырьё не может стоить дороже, чем вся масса по максимальной цене металла
        ceiling = a.mass_t.p90 * 1000.0 * 200.0
        assert a.recyclable_value_kzt.p90 < ceiling

    def test_climate_cost_is_material(self, econ):
        """Метан должен давать заметный, а не символический вклад."""
        a = assess(20_000, econ, depth_class="deep", iterations=5_000, seed=15)
        assert a.co2e_t.p50 > 0
        assert a.climate_cost_kzt.p50 > 0

    def test_rejects_non_positive_area(self, econ):
        with pytest.raises(ValueError):
            assess(0, econ)


class TestPenalty:
    def test_depends_on_violator_not_on_mass(self, econ):
        """Ключевая методологическая проверка.

        Штраф в РК назначается за факт правонарушения. Свалка вдесятеро
        большей площади не увеличивает штраф — увеличивается ущерб,
        но не санкция.
        """
        small = assess(500, econ, iterations=500, seed=17)
        huge = assess(50_000, econ, iterations=500, seed=17)
        assert small.penalty_kzt == huge.penalty_kzt

        company = assess(500, econ, violator="large", iterations=500, seed=17)
        assert company.penalty_mrp == 10 * small.penalty_mrp

    def test_uses_real_article_and_mrp(self, econ):
        a = assess(5_000, econ, violator="individual", iterations=500, seed=19)
        assert a.penalty_article == "ст. 344, ч. 2-1"
        assert a.penalty_mrp == 100
        assert a.penalty_kzt == 100 * 4325

    def test_alternative_article_can_be_selected(self, econ):
        a = assess(5_000, econ, article="landscaping_rules", violator="individual", iterations=500, seed=21)
        assert a.penalty_article == "ст. 505"
        assert a.penalty_mrp == 20

    def test_unknown_article_raises(self, econ):
        with pytest.raises(KeyError):
            assess(5_000, econ, article="несуществующая", iterations=100)

    def test_unknown_violator_class_raises(self, econ):
        # У ст. 344 ч.2 категории medium/large заданы в процентах выгоды,
        # а не в МРП — попытка их использовать должна падать явно.
        with pytest.raises(KeyError):
            assess(5_000, econ, article="storage_outside_designated", violator="large", iterations=100)


class TestSensitivity:
    def test_returns_correlation_for_each_assumption(self, econ):
        result = sensitivity(5_000, econ, iterations=3_000, seed=23)
        assert set(result) == {"depth", "density", "removal_cost", "carbon_price", "doc", "k"}
        assert all(-1.0 <= v <= 1.0 for v in result.values())

    def test_geometry_dominates_the_spread(self, econ):
        """Глубина и плотность влияют сильнее цены углерода.

        Это прямой ответ на вопрос «куда вам стоит потратить время»:
        уточнять надо объём выездным замером, а не курс углеродной единицы.
        """
        result = sensitivity(5_000, econ, iterations=5_000, seed=25)
        assert abs(result["depth"]) > abs(result["carbon_price"])
        assert abs(result["density"]) > abs(result["carbon_price"])


class TestAgeEntersTheMethaneModel:
    """Возраст объекта обязан входить в расчёт метана.

    Разложение по IPCC FOD идёт от момента захоронения. Без возраста
    свалка 2019 года получала ту же оценку, что свалка 2024-го, — а весь
    рассказ продукта держится на обратном: «каждый год ожидания — это
    выброс, которого уже не вернуть». Утверждение было верным по физике и
    никак не посчитанным.
    """

    def _economics(self):
        from vantage.config import load_economics

        return load_economics()

    def test_older_object_has_released_more(self):
        from vantage.money import assess

        e = self._economics()
        young = assess(3000.0, e, age_years=1.0)
        old = assess(3000.0, e, age_years=10.0)
        assert old.co2e_emitted_t.p50 > young.co2e_emitted_t.p50

    def test_emitted_plus_preventable_equals_total(self):
        """Уже ушедшее и предотвратимое в сумме дают полный горизонт.

        Проверяется по среднему, а не по медиане: перцентили не
        складываются. Медиана суммы не равна сумме медиан, если слагаемые
        не связаны строго монотонно, — а здесь предотвратимое считается
        как остаток и переупорядочивает выборку. Первая версия теста
        сравнивала медианы и падала на разнице в 0,1%, что было ошибкой
        теста, а не модели.
        """
        from vantage.money import assess

        a = assess(3000.0, self._economics(), age_years=6.0)
        total = a.co2e_emitted_t.mean + a.co2e_preventable_t.mean
        assert abs(total - a.co2e_t.mean) < 1e-6 * max(1.0, a.co2e_t.mean)

    def test_without_age_nothing_is_written_off(self):
        """Возраст не передан — значит «свежая свалка», и ушедшего нет.

        Молча приписать объекту возраст было бы хуже, чем не считать его:
        число выглядело бы посчитанным.
        """
        from vantage.money import assess

        a = assess(3000.0, self._economics())
        assert a.co2e_emitted_t.p50 == 0.0
        assert abs(a.co2e_preventable_t.mean - a.co2e_t.mean) < 1e-9

    def test_decay_is_front_loaded(self):
        """Первые годы дают больше, чем последние, — иначе это не FOD.

        Из этого следует вывод, ради которого всё и считается: убирать
        свалку поздно — не фигура речи.
        """
        from vantage.money import assess

        # Отрезки обязаны быть равными. Первая версия сравнивала первые
        # три года со следующими семью и падала: семь лет дают больше трёх
        # даже при убывающей скорости. Это была ошибка теста, а не модели.
        e = self._economics()
        at3 = assess(3000.0, e, age_years=3.0).co2e_emitted_t.mean
        at6 = assess(3000.0, e, age_years=6.0).co2e_emitted_t.mean
        at9 = assess(3000.0, e, age_years=9.0).co2e_emitted_t.mean
        first, second, third = at3, at6 - at3, at9 - at6
        assert first > second > third, (
            f"по трёхлетиям: {first:.0f}, {second:.0f}, {third:.0f} т — "
            "разложение должно замедляться, а не ускоряться"
        )

    def test_age_never_makes_preventable_negative(self):
        """Объект старше горизонта не должен давать отрицательный остаток."""
        from vantage.money import assess

        a = assess(3000.0, self._economics(), age_years=40.0)
        assert a.co2e_preventable_t.p50 >= 0.0

# --------------------------------------------------------------------------- #
#  Интервал по списку объектов
# --------------------------------------------------------------------------- #


class TestPortfolio:
    """Сумма интервалов и интервал суммы — разные величины.

    Это единственное место в проекте, где ошибка не видна глазом: сложить
    P10 всех объектов легко, выглядит правдоподобно и даёт заведомо
    неверный ответ. Поэтому свойства проверяются, а не подразумеваются.
    """

    @staticmethod
    def _economics():
        return load_economics()

    def test_matches_assess_on_single_object(self):
        """На одном объекте портфель обязан совпасть с обычной оценкой.

        Разойдись они — значит, розыгрыш общих допущений сдвинул
        последовательность генератора, и все опубликованные числа тихо
        поменялись бы при следующем прогоне.
        """
        from vantage.money import assess, portfolio

        e = self._economics()
        one = assess(3000.0, e, age_years=2.0)
        whole = portfolio([(3000.0, 2.0)], e)
        # Не побитовое равенство: общие допущения разыгрываются раньше
        # объектных, и порядок обращений к генератору другой. Совпадать
        # обязано распределение, а не конкретная выборка.
        assert whole["net_damage_kzt"].p50 == pytest.approx(one.net_damage_kzt.p50, rel=0.03)
        assert whole["mass_t"].p50 == pytest.approx(one.mass_t.p50, rel=0.03)

    def test_interval_narrower_than_sum_of_intervals(self):
        """Правильный интервал по пятнадцати объектам у́же наивной суммы."""
        from vantage.money import assess, portfolio

        e = self._economics()
        items = [(500.0 * i, 1.0 + i * 0.3) for i in range(1, 16)]
        whole = portfolio(items, e)
        each = [assess(area, e, age_years=age) for area, age in items]
        naive_low = sum(a.net_damage_kzt.p10 for a in each)
        naive_high = sum(a.net_damage_kzt.p90 for a in each)

        assert whole["net_damage_kzt"].p90 - whole["net_damage_kzt"].p10 < naive_high - naive_low

    def test_prices_stay_correlated_across_objects(self):
        """Общие допущения не дают интервалу схлопнуться до нуля.

        Если бы тариф на вывоз разыгрывался по каждому объекту заново,
        разброс по списку падал бы как корень из числа объектов, и на
        пятнадцати объектах интервал стал бы неправдоподобно узким. Он
        обязан остаться того же порядка, что относительный разброс по
        одному объекту.
        """
        from vantage.money import assess, portfolio

        e = self._economics()
        items = [(2000.0, 2.0)] * 15
        whole = portfolio(items, e)
        one = assess(2000.0, e, age_years=2.0)

        spread_one = (one.net_damage_kzt.p90 - one.net_damage_kzt.p10) / one.net_damage_kzt.p50
        spread_all = (
            whole["net_damage_kzt"].p90 - whole["net_damage_kzt"].p10
        ) / whole["net_damage_kzt"].p50
        # Половина относительного разброса одного объекта — граница с
        # запасом: независимые допущения по кучам разброс всё же сужают,
        # но не в четыре раза, как было бы при полной независимости.
        assert spread_all > spread_one * 0.5

    def test_empty_list_is_an_error(self):
        """Пустой список — не ноль ущерба, а вопрос к вызывающему коду."""
        from vantage.money import portfolio

        with pytest.raises(ValueError):
            portfolio([], self._economics())
