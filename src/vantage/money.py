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

    # --- 1. Геометрия: объём и масса ------------------------------------- #
    depth = economics.triangular("depth_class_m", depth_class).sample(rng, n)
    density = economics.triangular("waste_density_t_per_m3").sample(rng, n)
    volume = area_m2 * depth
    mass = volume * density

    # --- 2. Стоимость ликвидации ----------------------------------------- #
    base_cost = economics.triangular("removal_cost_kzt_per_t").sample(rng, n)
    surcharge = economics.triangular("transport_surcharge_per_km_kzt_per_t").sample(rng, n)
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
        price_per_kg = economics.triangular("recyclable_price_kzt_per_kg", fraction).sample(rng, n)
        recovery = economics.triangular("recovery_rate", fraction).sample(rng, n)
        # масса фракции в тоннах -> килограммы -> тенге
        recyclable_value += mass * share * recovery * 1000.0 * price_per_kg

    # --- 4. Климатический ущерб ------------------------------------------ #
    methane_cfg = economics.section("methane")
    doc = economics.triangular("methane", "doc_fraction").sample(rng, n)
    k = economics.triangular("methane", "k_rate_per_year").sample(rng, n)
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
    carbon_price = economics.triangular("carbon_price_kzt_per_t_co2e").sample(rng, n)
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

    # --- 6. Штраф (считается отдельно, не входит в ущерб) ----------------- #
    penalty_mrp, penalty_article = _penalty_mrp(economics, violator, article)
    mrp_value = economics.scalar("mrp_kzt", "value")

    return DamageAssessment(
        area_m2=area_m2,
        depth_class=depth_class,
        distance_to_landfill_km=distance_to_landfill_km,
        violator=violator,
        volume_m3=Percentiles.of(volume, percentiles),
        mass_t=Percentiles.of(mass, percentiles),
        removal_cost_kzt=Percentiles.of(removal_cost, percentiles),
        recyclable_value_kzt=Percentiles.of(recyclable_value, percentiles),
        ch4_t=Percentiles.of(ch4_t, percentiles),
        co2e_t=Percentiles.of(co2e_t, percentiles),
        co2e_emitted_t=Percentiles.of(co2e_emitted_t, percentiles),
        co2e_preventable_t=Percentiles.of(co2e_preventable_t, percentiles),
        age_years=float(age_years),
        climate_cost_kzt=Percentiles.of(climate_cost, percentiles),
        net_damage_kzt=Percentiles.of(net_damage, percentiles),
        penalty_mrp=penalty_mrp,
        penalty_kzt=penalty_mrp * mrp_value,
        penalty_article=penalty_article,
        iterations=n,
    )


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
    "sensitivity",
]
