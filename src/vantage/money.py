"""Денежный слой: во сколько обходится одна свалка.

Это то, что превращает карту находок в финансовый реестр — и то, что делает
проект треком Eco**Fin**, а не просто экологией.

Почему диапазон, а не одна цифра
--------------------------------
Сказать «ущерб 12,4 млн тенге» — значит гарантированно получить вопрос
«откуда именно 12,4» и не иметь на него честного ответа: в расчёте участвуют
восемь величин, каждая из которых известна лишь примерно. Поэтому каждое
допущение задаётся треугольным распределением (min / typical / max), а
результат разыгрывается методом Монте-Карло и публикуется как P10 / P50 / P90.

Перемножать крайние значения нельзя: это предполагает, что все восемь
допущений одновременно приняли минимум (или максимум) — событие с
исчезающе малой вероятностью. Монте-Карло даёт честный интервал.

Состав ущерба
-------------
    стоимость ликвидации   — сколько будет стоить убрать (расход бюджета)
  − извлекаемая ценность   — сколько стоит сырьё внутри (частично компенсирует)
  + климатический ущерб    — метан за 20 лет в CO₂-эквиваленте по цене углерода
  = чистый ущерб

Штраф считается отдельно и НЕ входит в ущерб: это санкция нарушителю, а не
затрата бюджета. Смешивать их — методологическая ошибка.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from . import methane as ch4_model
from .config import Economics

DepthClass = Literal["shallow", "medium", "deep"]
ViolatorClass = Literal["individual", "official_or_small", "medium", "large"]

#: Фракции, у которых есть рыночная цена приёма.
RECYCLABLE_FRACTIONS = ("plastic", "paper", "metal", "glass")


@dataclass(frozen=True)
class Percentiles:
    """Результат Монте-Карло по одной величине."""

    p10: float
    p50: float
    p90: float
    mean: float

    @classmethod
    def of(cls, samples: np.ndarray, percentiles: tuple[int, int, int] = (10, 50, 90)) -> Percentiles:
        p10, p50, p90 = np.percentile(samples, percentiles)
        return cls(p10=float(p10), p50=float(p50), p90=float(p90), mean=float(samples.mean()))

    def as_dict(self) -> dict[str, float]:
        return {"p10": self.p10, "p50": self.p50, "p90": self.p90, "mean": self.mean}

    def format_kzt(self) -> str:
        """Человекочитаемый диапазон в тенге — то, что идёт на слайд."""
        return f"{_kzt(self.p10)} … {_kzt(self.p90)} ₸ (медиана {_kzt(self.p50)} ₸)"


@dataclass(frozen=True)
class DamageAssessment:
    """Полная оценка одного объекта."""

    area_m2: float
    depth_class: DepthClass
    distance_to_landfill_km: float
    violator: ViolatorClass

    volume_m3: Percentiles
    mass_t: Percentiles
    removal_cost_kzt: Percentiles
    recyclable_value_kzt: Percentiles
    ch4_t: Percentiles
    co2e_t: Percentiles
    #: Сколько CO₂-экв уже ушло за годы, что объект лежит. Ноль, если
    #: возраст не передан: тогда оценка отвечает на вопрос «сколько выбросит
    #: свежая свалка такой массы», и это надо называть вслух.
    co2e_emitted_t: Percentiles
    #: Что ещё можно предотвратить уборкой сейчас.
    co2e_preventable_t: Percentiles
    climate_cost_kzt: Percentiles
    net_damage_kzt: Percentiles

    penalty_mrp: int
    penalty_kzt: float
    penalty_article: str

    iterations: int = field(default=0)
    #: Возраст объекта в годах на момент расчёта. Ноль означает «возраст
    #: не передан», и тогда co2e_emitted_t тоже ноль — оценка отвечает на
    #: вопрос «сколько выбросит свежая свалка такой массы».
    age_years: float = 0.0


    def summary_lines(self) -> list[str]:
        """Готовые строки для панели интерфейса и для слайда."""
        return [
            f"Площадь: {self.area_m2:,.0f} м²".replace(",", " "),
            f"Объём: {self.volume_m3.p10:,.0f} … {self.volume_m3.p90:,.0f} м³".replace(",", " "),
            f"Масса: {self.mass_t.p10:,.0f} … {self.mass_t.p90:,.0f} т".replace(",", " "),
            f"Ликвидация: {self.removal_cost_kzt.format_kzt()}",
            f"Извлекаемое сырьё: {self.recyclable_value_kzt.format_kzt()}",
            f"Метан за 20 лет: {self.co2e_t.p10:,.0f} … {self.co2e_t.p90:,.0f} т CO₂-экв.".replace(",", " "),
            f"Климатический ущерб: {self.climate_cost_kzt.format_kzt()}",
            f"ЧИСТЫЙ УЩЕРБ: {self.net_damage_kzt.format_kzt()}",
            f"Штраф ({self.penalty_article}, {self.violator}): {self.penalty_mrp} МРП = {_kzt(self.penalty_kzt)} ₸",
        ]


def _kzt(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def assess(
    area_m2: float,
    economics: Economics,
    *,
    depth_class: DepthClass = "medium",
    distance_to_landfill_km: float = 15.0,
    violator: ViolatorClass = "individual",
    article: str | None = None,
    age_years: float = 0.0,
    iterations: int | None = None,
    seed: int | None = None,
) -> DamageAssessment:
    """Оценить ущерб от одного объекта методом Монте-Карло.

    ``area_m2`` — площадь полигона свалки, посчитанная в метрической
    проекции. ``depth_class`` присваивается моделью по текстуре и тени.
    """
    if area_m2 <= 0:
        raise ValueError("площадь должна быть положительной")

    mc = economics.section("monte_carlo")
    n = int(iterations or mc["iterations"])
    rng = np.random.default_rng(seed if seed is not None else mc["seed"])
    percentiles = tuple(mc.get("report_percentiles", (10, 50, 90)))  # type: ignore[assignment]

    draw = _draw(
        area_m2,
        economics,
        rng,
        n,
        depth_class=depth_class,
        distance_to_landfill_km=distance_to_landfill_km,
        age_years=age_years,
    )

    # --- 6. Штраф (считается отдельно, не входит в ущерб) ----------------- #
    penalty_mrp, penalty_article = _penalty_mrp(economics, violator, article)
    mrp_value = economics.scalar("mrp_kzt", "value")

    return DamageAssessment(
        area_m2=area_m2,
        depth_class=depth_class,
        distance_to_landfill_km=distance_to_landfill_km,
        violator=violator,
        volume_m3=Percentiles.of(draw["volume_m3"], percentiles),
        mass_t=Percentiles.of(draw["mass_t"], percentiles),
        removal_cost_kzt=Percentiles.of(draw["removal_cost_kzt"], percentiles),
        recyclable_value_kzt=Percentiles.of(draw["recyclable_value_kzt"], percentiles),
        ch4_t=Percentiles.of(draw["ch4_t"], percentiles),
        co2e_t=Percentiles.of(draw["co2e_t"], percentiles),
        co2e_emitted_t=Percentiles.of(draw["co2e_emitted_t"], percentiles),
        co2e_preventable_t=Percentiles.of(draw["co2e_preventable_t"], percentiles),
        age_years=float(age_years),
        climate_cost_kzt=Percentiles.of(draw["climate_cost_kzt"], percentiles),
        net_damage_kzt=Percentiles.of(draw["net_damage_kzt"], percentiles),
        penalty_mrp=penalty_mrp,
        penalty_kzt=penalty_mrp * mrp_value,
        penalty_article=penalty_article,
        iterations=n,
    )


#: Допущения, общие для всех объектов одного города.
#:
#: Разделение принципиальное, и без него сумма по списку считается
#: неправильно. Тариф на вывоз тонны, прайс приёмки вторсырья и цена
#: углеродной единицы — величины рынка: если вывоз стоит дорого, он стоит
#: дорого сразу для всех пятнадцати объектов. Разыгрывать их заново на
#: каждый объект значит предполагать, что дорогой вывоз одной свалки
#: компенсируется дешёвым вывозом соседней. Такая «диверсификация»
#: существует только в арифметике: она сужает интервал по списку тем
#: сильнее, чем больше объектов, и на пятнадцати даёт видимость точности,
#: которой нет.
#:
#: Остальные допущения — глубина, плотность, доля органики, извлекаемость
#: фракции — свойства конкретной кучи и разыгрываются по каждой отдельно.
SHARED_ASSUMPTIONS: tuple[str, ...] = (
    "base_cost", "surcharge", "carbon_price", "k",
    *(f"price_{fraction}" for fraction in RECYCLABLE_FRACTIONS),
)


def _draw(
    area_m2: float,
    economics: Economics,
    rng: np.random.Generator,
    n: int,
    *,
    depth_class: DepthClass = "medium",
    distance_to_landfill_km: float = 15.0,
    age_years: float = 0.0,
    shared: dict[str, np.ndarray] | None = None,
) -> dict[str, np.ndarray]:
    """Один розыгрыш Монте-Карло по одному объекту: массивы длины ``n``.

    Вынесено из :func:`assess` не ради красоты, а потому что тот же
    розыгрыш нужен :func:`portfolio` — там нельзя складывать готовые
    процентили, там нужно складывать сами итерации.

    ``shared`` — уже разыгранные общерыночные допущения (см.
    :data:`SHARED_ASSUMPTIONS`). Если его нет, всё разыгрывается здесь, и
    порядок обращений к ``rng`` в точности тот же, что был до выделения
    функции: иначе при том же зерне поменялись бы все опубликованные числа.
    """

    def take(key: str, *path: str) -> np.ndarray:
        if shared is not None and key in shared:
            return shared[key]
        return economics.triangular(*path).sample(rng, n)

    # --- 1. Геометрия: объём и масса ------------------------------------- #
    depth = take("depth", "depth_class_m", depth_class)
    density = take("density", "waste_density_t_per_m3")
    volume = area_m2 * depth
    mass = volume * density

    # --- 2. Стоимость ликвидации ----------------------------------------- #
    base_cost = take("base_cost", "removal_cost_kzt_per_t")
    surcharge = take("surcharge", "transport_surcharge_per_km_kzt_per_t")
    base_radius = economics.scalar("transport_surcharge_per_km_kzt_per_t", "base_radius_km")
    extra_km = max(0.0, distance_to_landfill_km - base_radius)
    removal_cost = mass * (base_cost + surcharge * extra_km)

    # --- 3. Извлекаемая ценность ----------------------------------------- #
    morphology = economics.section("morphology")
    recyclable_value = np.zeros(n, dtype=float)
    for fraction in RECYCLABLE_FRACTIONS:
        share = float(morphology.get(fraction, 0.0))
        if share <= 0:
            continue
        price_per_kg = take(f"price_{fraction}", "recyclable_price_kzt_per_kg", fraction)
        recovery = take(f"recovery_{fraction}", "recovery_rate", fraction)
        # масса фракции в тоннах -> килограммы -> тенге
        recyclable_value += mass * share * recovery * 1000.0 * price_per_kg

    # --- 4. Климатический ущерб ------------------------------------------ #
    methane_cfg = economics.section("methane")
    doc = take("doc", "methane", "doc_fraction")
    k = take("k", "methane", "k_rate_per_year")
    organic_share = float(morphology.get("organic", 0.0))

    ch4_t = ch4_model.cumulative_ch4(
        mass * organic_share,
        doc_fraction=doc,
        doc_f=float(methane_cfg["doc_f"]),
        mcf=float(methane_cfg["methane_correction_factor"]),
        f_ch4=float(methane_cfg["f_ch4_in_gas"]),
        oxidation=float(methane_cfg["oxidation_factor"]),
        k=k,
        years=int(methane_cfg["horizon_years"]),
    )
    co2e_t = ch4_model.to_co2e(ch4_t, float(methane_cfg["gwp_ch4_20yr"]))
    carbon_price = take("carbon_price", "carbon_price_kzt_per_t_co2e")
    # Климатический ущерб считается по ПОЛНОМУ горизонту, а не по остатку.
    #
    # Выбор осознанный, и на защите его спросят. Ущерб — это вред, который
    # объект наносит, а не только тот, который ещё можно предотвратить:
    # метан, ушедший за годы лежания, никуда не делся из атмосферы.
    # Считать по остатку значило бы, что чем дольше свалка лежит, тем
    # меньше с неё спрос, — а это ровно наоборот.
    #
    # Разделение на ушедшее и предотвратимое считается отдельно (ниже) и
    # показывается рядом: оно отвечает на другой вопрос — «что даст уборка
    # сейчас».
    climate_cost = co2e_t * carbon_price

    # Сколько метана уже ушло за годы, что объект лежит.
    #
    # Модель FOD считает выброс от момента захоронения, и без возраста
    # свалка 2019 года и свалка 2024-го получали одинаковую оценку. При
    # этом весь рассказ на сайте держится на обратном: «каждый год
    # ожидания — это выброс, которого уже не вернуть». Утверждение было
    # верным по физике и никак не посчитанным.
    #
    # Разложение экспоненциальное, и это важно для вывода: первые годы
    # дают больше всего. Свалка, пролежавшая пять лет, уже отдала заметную
    # долю того, что могла, — и убирать её поздно не в переносном смысле.
    emitted_ch4 = ch4_model.cumulative_ch4(
        mass * organic_share,
        doc_fraction=doc,
        doc_f=float(methane_cfg["doc_f"]),
        mcf=float(methane_cfg["methane_correction_factor"]),
        f_ch4=float(methane_cfg["f_ch4_in_gas"]),
        oxidation=float(methane_cfg["oxidation_factor"]),
        k=k,
        years=max(0.0, float(age_years)),
    ) if age_years and age_years > 0 else ch4_t * 0.0
    co2e_emitted_t = ch4_model.to_co2e(emitted_ch4, float(methane_cfg["gwp_ch4_20yr"]))
    # Предотвратимое — то, что ещё не ушло: остаток от полного горизонта.
    co2e_preventable_t = np.maximum(co2e_t - co2e_emitted_t, 0.0)

    # --- 5. Чистый ущерб -------------------------------------------------- #
    # Извлекаемое сырьё вычитается: оно частично компенсирует уборку.
    # Ущерб может оказаться отрицательным — это не ошибка, а важный вывод:
    # такую свалку выгодно разобрать, а не просто вывезти на полигон.
    net_damage = removal_cost - recyclable_value + climate_cost

    return {
        "volume_m3": volume,
        "mass_t": mass,
        "removal_cost_kzt": removal_cost,
        "recyclable_value_kzt": recyclable_value,
        "ch4_t": ch4_t,
        "co2e_t": co2e_t,
        "co2e_emitted_t": co2e_emitted_t,
        "co2e_preventable_t": co2e_preventable_t,
        "climate_cost_kzt": climate_cost,
        "net_damage_kzt": net_damage,
    }


def portfolio(
    items: Sequence[tuple[float, float]],
    economics: Economics,
    *,
    depth_class: DepthClass = "medium",
    distance_to_landfill_km: float = 15.0,
    iterations: int | None = None,
    seed: int | None = None,
) -> dict[str, Percentiles]:
    """Интервал по СПИСКУ объектов, а не сумма интервалов по каждому.

    ``items`` — пары «площадь в м², возраст в годах».

    Зачем отдельно от :func:`assess`
    --------------------------------
    Сложить P10 всех объектов и назвать это нижней границей по списку —
    ошибка, и на техническом Q&A её находят первой. Сумма десятых
    процентилей отвечает на вопрос «сколько выйдет, если КАЖДЫЙ из
    пятнадцати объектов одновременно окажется дешевле, чем в девяти
    случаях из десяти» — событие куда менее вероятное, чем один шанс из
    десяти, который обещает подпись «P10».

    Здесь складываются итерации: на каждой из двадцати тысяч разыгрывается
    весь список сразу, и процентили берутся уже от суммы. Общерыночные
    допущения (:data:`SHARED_ASSUMPTIONS`) при этом разыгрываются один раз
    на итерацию и применяются ко всем объектам — тариф на вывоз в городе
    один.

    Интервал выходит у́же наивной суммы, и это не подгонка: разброс сужают
    независимые физические допущения по каждой куче, а зависимые — цены —
    остаются общими и продолжают двигать итог целиком.
    """
    if not items:
        raise ValueError("список объектов пуст")

    mc = economics.section("monte_carlo")
    n = int(iterations or mc["iterations"])
    rng = np.random.default_rng(seed if seed is not None else mc["seed"])
    percentiles = tuple(mc.get("report_percentiles", (10, 50, 90)))  # type: ignore[assignment]

    shared = {
        "base_cost": economics.triangular("removal_cost_kzt_per_t").sample(rng, n),
        "surcharge": economics.triangular("transport_surcharge_per_km_kzt_per_t").sample(rng, n),
        "carbon_price": economics.triangular("carbon_price_kzt_per_t_co2e").sample(rng, n),
        "k": economics.triangular("methane", "k_rate_per_year").sample(rng, n),
    }
    for fraction in RECYCLABLE_FRACTIONS:
        shared[f"price_{fraction}"] = economics.triangular(
            "recyclable_price_kzt_per_kg", fraction).sample(rng, n)

    total: dict[str, np.ndarray] = {}
    for area_m2, age_years in items:
        if area_m2 <= 0:
            continue
        draw = _draw(
            area_m2, economics, rng, n,
            depth_class=depth_class,
            distance_to_landfill_km=distance_to_landfill_km,
            age_years=age_years,
            shared=shared,
        )
        for key, values in draw.items():
            total[key] = total[key] + values if key in total else values.copy()

    return {key: Percentiles.of(values, percentiles) for key, values in total.items()}


def _penalty_mrp(
    economics: Economics, violator: ViolatorClass, article: str | None
) -> tuple[int, str]:
    """Размер штрафа в МРП по статье КоАП и категории нарушителя."""
    penalty = economics.section("penalty")
    key = article or penalty["default_article"]
    articles = penalty["articles"]
    if key not in articles:
        raise KeyError(f"нет статьи «{key}» в economics.penalty.articles")
    entry = articles[key]
    mrp_table = entry["mrp"]
    if violator not in mrp_table:
        raise KeyError(
            f"для статьи «{key}» не задан размер штрафа для категории «{violator}»; "
            f"доступны: {sorted(mrp_table)}"
        )
    return int(mrp_table[violator]), str(entry["article"])


def sensitivity(
    area_m2: float,
    economics: Economics,
    *,
    depth_class: DepthClass = "medium",
    iterations: int = 4000,
    seed: int = 0,
) -> dict[str, float]:
    """Вклад каждого допущения в разброс итогового ущерба.

    Считается ранговая корреляция Спирмена между разыгранным значением
    допущения и итоговым ущербом. Это прямой ответ на вопрос жюри
    «какая из ваших оценок больше всего влияет на результат» — и он же
    показывает, куда команде имеет смысл потратить время на уточнение
    данных, а куда не имеет.
    """
    rng = np.random.default_rng(seed)
    n = iterations

    samples = {
        "depth": economics.triangular("depth_class_m", depth_class).sample(rng, n),
        "density": economics.triangular("waste_density_t_per_m3").sample(rng, n),
        "removal_cost": economics.triangular("removal_cost_kzt_per_t").sample(rng, n),
        "carbon_price": economics.triangular("carbon_price_kzt_per_t_co2e").sample(rng, n),
        "doc": economics.triangular("methane", "doc_fraction").sample(rng, n),
        "k": economics.triangular("methane", "k_rate_per_year").sample(rng, n),
    }

    morphology = economics.section("morphology")
    methane_cfg = economics.section("methane")

    volume = area_m2 * samples["depth"]
    mass = volume * samples["density"]
    removal = mass * samples["removal_cost"]

    recyclable = np.zeros(n)
    for fraction in RECYCLABLE_FRACTIONS:
        share = float(morphology.get(fraction, 0.0))
        if share <= 0:
            continue
        price = economics.triangular("recyclable_price_kzt_per_kg", fraction).sample(rng, n)
        recovery = economics.triangular("recovery_rate", fraction).sample(rng, n)
        recyclable += mass * share * recovery * 1000.0 * price

    ch4 = ch4_model.cumulative_ch4(
        mass * float(morphology.get("organic", 0.0)),
        doc_fraction=samples["doc"],
        doc_f=float(methane_cfg["doc_f"]),
        mcf=float(methane_cfg["methane_correction_factor"]),
        f_ch4=float(methane_cfg["f_ch4_in_gas"]),
        oxidation=float(methane_cfg["oxidation_factor"]),
        k=samples["k"],
        years=int(methane_cfg["horizon_years"]),
    )
    net = removal - recyclable + ch4 * float(methane_cfg["gwp_ch4_20yr"]) * samples["carbon_price"]

    return {name: _spearman(values, net) for name, values in samples.items()}


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Ранговая корреляция Спирмена без зависимости от scipy."""
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    rx -= rx.mean()
    ry -= ry.mean()
    denom = np.sqrt((rx**2).sum() * (ry**2).sum())
    return float((rx * ry).sum() / denom) if denom else 0.0


__all__ = [
    "RECYCLABLE_FRACTIONS",
    "DamageAssessment",
    "Percentiles",
    "assess",
    "portfolio",
    "sensitivity",
]
