"""Эмиссия метана свалкой — модель разложения первого порядка (IPCC FOD).

Почему метан вообще считается
-----------------------------
Стихийная свалка — это не только мусор на земле. Органическая фракция под
слоем отходов разлагается **анаэробно**, то есть без доступа кислорода, и
выделяет метан. На двадцатилетнем горизонте метан примерно в 82 раза
сильнее CO₂ как парниковый газ. Для средней свалки климатический ущерб
часто оказывается сопоставим со стоимостью её уборки — и это цифра,
которую больше никто на хакатоне не посчитает.

Модель
------
Методика IPCC 2006 (Vol.5 Ch.3), First Order Decay. Логика в трёх шагах:

1. Из массы отходов выделяется масса разлагаемого органического углерода::

       DDOCm = W · DOC · DOC_f · MCF

   где W — масса отходов, DOC — доля разлагаемого органического углерода,
   DOC_f — доля DOC, которая реально разлагается в анаэробных условиях,
   MCF — поправка на тип свалки (для неуправляемой мелкой — 0.4).

2. Разложение идёт по экспоненте с константой k. За время T разложится::

       DDOCm · (1 − e^{−kT})

3. Из разложившегося углерода образуется метан, часть которого окисляется
   в покрывающем слое::

       CH₄ = разложившийся_углерод · F · (16/12) · (1 − OX)

   где F — объёмная доля CH₄ в свалочном газе (обычно 0.5),
   16/12 — отношение молярных масс CH₄ и C,
   OX — доля, окисляющаяся при прохождении через грунт.

Все параметры берутся из ``config/economics_astana.yaml`` и имеют источник.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Отношение молярных масс CH₄ (16 г/моль) и углерода (12 г/моль).
#: Каждый грамм разложившегося углерода даёт 16/12 грамма метана.
CH4_PER_CARBON = 16.0 / 12.0


@dataclass(frozen=True)
class MethaneParams:
    """Параметры модели FOD (значения приходят из конфигурации)."""

    doc_fraction: float
    doc_f: float
    methane_correction_factor: float
    f_ch4_in_gas: float
    oxidation_factor: float
    k_rate_per_year: float
    horizon_years: int
    gwp: float

    def __post_init__(self) -> None:
        for name in ("doc_fraction", "doc_f", "methane_correction_factor", "f_ch4_in_gas"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} должен быть в диапазоне 0..1, получено {value}")
        if not 0.0 <= self.oxidation_factor < 1.0:
            raise ValueError("oxidation_factor должен быть в диапазоне 0..1")
        if self.k_rate_per_year <= 0:
            raise ValueError("k_rate_per_year должен быть положительным")
        if self.horizon_years <= 0:
            raise ValueError("horizon_years должен быть положительным")


def degradable_carbon(mass_t, doc_fraction, doc_f, mcf):
    """DDOCm — масса разлагаемого органического углерода, тонн.

    Это «топливо» всей модели: сколько углерода в принципе способно
    превратиться в метан.
    """
    return mass_t * doc_fraction * doc_f * mcf


def cumulative_ch4(
    mass_t,
    *,
    doc_fraction,
    doc_f,
    mcf,
    f_ch4,
    oxidation,
    k,
    years,
):
    """Суммарная эмиссия CH₄ за ``years`` лет, тонн.

    Замкнутая форма для разового захоронения массы ``mass_t`` в нулевой год::

        CH₄ = DDOCm · (1 − e^{−k·T}) · F · (16/12) · (1 − OX)

    Свалка растёт постепенно, а не появляется целиком за один день, но для
    стихийной свалки разброс дат формирования (месяцы) на порядок меньше
    горизонта расчёта (20 лет), поэтому разовое приближение оправдано.
    На защите это стоит сказать самим: приближение осознанное.
    """
    ddocm = degradable_carbon(mass_t, doc_fraction, doc_f, mcf)
    decomposed = ddocm * (1.0 - np.exp(-k * years))
    return decomposed * f_ch4 * CH4_PER_CARBON * (1.0 - oxidation)


def annual_ch4_profile(
    mass_t: float,
    params: MethaneParams,
) -> np.ndarray:
    """Погодовой профиль эмиссии CH₄, тонн в каждый год горизонта.

    Нужен для графика в интерфейсе: он наглядно показывает, что свалка
    «дымит» не мгновенно, а десятилетиями — и что чем позже её убрали,
    тем больше метана уже ушло безвозвратно.
    """
    ddocm = degradable_carbon(
        mass_t, params.doc_fraction, params.doc_f, params.methane_correction_factor
    )
    years = np.arange(1, params.horizon_years + 1, dtype=float)
    decomposed_by_year = ddocm * (np.exp(-params.k_rate_per_year * (years - 1)) - np.exp(-params.k_rate_per_year * years))
    return decomposed_by_year * params.f_ch4_in_gas * CH4_PER_CARBON * (1.0 - params.oxidation_factor)


def to_co2e(ch4_t, gwp: float):
    """Перевести тонны CH₄ в тонны CO₂-эквивалента.

    Выбор горизонта GWP — не техническая деталь, а позиция. На 20 лет
    метан весит 82.5, на 100 лет — 29.8. Для отходов корректнее
    двадцатилетний горизонт: метан живёт в атмосфере около 12 лет,
    и столетнее усреднение занижает его реальный эффект. Указывать
    горизонт обязательно — иначе цифра ни о чём не говорит.
    """
    return ch4_t * gwp


def from_config(economics, *, horizon: str = "20yr", sample=None) -> MethaneParams:
    """Собрать параметры модели из ``economics_astana.yaml``.

    Если передан ``sample`` (словарь уже разыгранных значений), используются
    они — так модель встраивается в Монте-Карло денежного слоя.
    """
    section = economics.section("methane")
    gwp_key = "gwp_ch4_20yr" if horizon == "20yr" else "gwp_ch4_100yr"
    sample = sample or {}
    return MethaneParams(
        doc_fraction=sample.get("doc_fraction", economics.triangular("methane", "doc_fraction").typical),
        doc_f=float(section["doc_f"]),
        methane_correction_factor=float(section["methane_correction_factor"]),
        f_ch4_in_gas=float(section["f_ch4_in_gas"]),
        oxidation_factor=float(section["oxidation_factor"]),
        k_rate_per_year=sample.get("k", economics.triangular("methane", "k_rate_per_year").typical),
        horizon_years=int(section["horizon_years"]),
        gwp=float(section[gwp_key]),
    )


__all__ = [
    "CH4_PER_CARBON",
    "MethaneParams",
    "annual_ch4_profile",
    "cumulative_ch4",
    "degradable_carbon",
    "from_config",
    "to_co2e",
]
