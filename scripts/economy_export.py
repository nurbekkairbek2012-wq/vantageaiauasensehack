"""Выгрузка денежного слоя для экрана «Экономика»: web-next/public/data/economy.json.

── Зачем отдельная выгрузка ────────────────────────────────────────────

В candidates.geojson лежит итог — чистый ущерб P10/P50/P90 и масса. Для
карты этого достаточно: там нужно решить, куда ехать. Для разговора о
деньгах — нет. Вопрос трека EcoFin звучит не «сколько это стоит», а
«сколько ресурса теряется и сколько из этого можно вернуть», и ответ на
него состоит из четырёх слагаемых, а не из одного:

    стоимость вывоза     — расход, который придётся понести
  − извлекаемое сырьё    — ресурс, который лежит в куче и стоит денег
  + климатический ущерб  — метан, уже ушедший и ещё предотвратимый
  = чистый ущерб

Слагаемые считает vantage.money, но в геоджейсон они не попадали. Здесь
они пересчитываются по опубликованному списку и кладутся рядом.

── Почему пересчёт, а не сохранение из прогона ─────────────────────────

Денежный слой не трогает снимки: ему нужны площадь и возраст, оба уже
лежат в выгрузке. Пересчёт занимает секунды, идёт с тем же зерном и на
том же конфиге, и потому даёт ровно те же числа, что на карте — сверено
тестом. Ждать полного прогона по спутнику ради двух новых колонок
незачем.

── Что здесь появляется сверх пайплайна ────────────────────────────────

1. Интервал по списку целиком (:func:`vantage.money.portfolio`), а не
   сумма интервалов по объектам. Сумма P10 — распространённая ошибка, и
   рядом показаны обе величины, чтобы разница была видна.
2. Цена ожидания: сколько CO₂-экв уйдёт за следующий год, если не
   трогать список. Считается разностью выброса на возрасте t+1 и t по
   той же модели FOD.
3. Приоритет: накопленная доля ущерба по объектам, отсортированным по
   убыванию. Отвечает на вопрос «сколько выездов закрывают большую часть
   суммы» — именно это делает рекомендация полезной, а не длинной.
4. Происхождение каждого допущения: source / derived / estimate прямо из
   economics.yaml. На экране это подписи «подтверждено», «выведено»,
   «инженерная оценка» — числа без такой пометки одинаково выглядят и
   одинаково не проверяются.

    python scripts/economy_export.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DATA = ROOT / "web-next/public/data"
OUT = DATA / "economy.json"

#: Допущения, попадающие в подпись на экране. Ключ — путь в economics.yaml,
#: значение — как назвать величину человеку.
TRACKED = {
    "removal_cost_kzt_per_t": "стоимость вывоза тонны",
    "transport_surcharge_per_km_kzt_per_t": "надбавка за плечо вывоза",
    "waste_density_t_per_m3": "плотность отходов",
    "depth_class_m": "глубина залегания",
    "morphology": "морфология свалки",
    "recyclable_price_kzt_per_kg": "прайс приёмки вторсырья",
    "recovery_rate": "извлекаемая доля фракции",
    "sorting_surcharge_share": "надбавка за разбор на месте",
    "carbon_price_kzt_per_t_co2e": "цена углеродной единицы",
    "methane": "модель разложения (IPCC FOD)",
    "mrp_kzt": "месячный расчётный показатель",
    "penalty": "штраф по КоАП РК",
}


def provenance(economics) -> dict[str, dict[str, str]]:
    """Чем подтверждено каждое допущение: source / derived / estimate.

    Читается из самого конфига, а не переписывается сюда руками: подпись
    «инженерная оценка», разошедшаяся с файлом, хуже отсутствия подписи.
    """
    result: dict[str, dict[str, str]] = {}
    for key, title in TRACKED.items():
        node = economics.raw.get(key)
        if not isinstance(node, dict):
            continue
        kind, text = "", ""
        for field in ("source", "derived", "estimate"):
            if isinstance(node.get(field), str):
                kind, text = field, node[field]
                break
        if not kind:
            # Вложенные разделы (methane, penalty) держат происхождение
            # на один уровень ниже — у самой секции.
            for sub in node.values():
                if isinstance(sub, dict):
                    for field in ("source", "derived", "estimate"):
                        if isinstance(sub.get(field), str):
                            kind, text = field, sub[field]
                            break
                if kind:
                    break
        if kind:
            result[key] = {"title": title, "kind": kind, "note": text}
    return result


def main() -> int:
    import geopandas as gpd
    import pandas as pd

    from vantage.config import load_economics
    from vantage.money import RECYCLABLE_FRACTIONS, assess, portfolio, sensitivity

    site = gpd.read_file(DATA / "candidates.geojson")
    funnel = json.loads((DATA / "funnel.json").read_text(encoding="utf-8"))
    economics = load_economics()

    # Объекты, отвергнутые просмотром, в деньги не идут: складывать ущерб
    # по складу под синей кровлей значит завышать итог, и первый же
    # вопрос «а что вот это» обесценит всю сумму.
    site = site[site.get("visual_check").ne("not_landfill")] if "visual_check" in site else site
    dates = pd.to_datetime(site["break_date"], errors="coerce")

    objects = []
    for i, (_, row) in enumerate(site.iterrows()):
        area = float(row.get("area_m2") or 0.0)
        if area <= 0:
            continue
        age = float(row.get("age_years") or 0.0)
        now = assess(area, economics, age_years=age)
        # Тот же объект на год старше — разница даёт цену ожидания.
        later = assess(area, economics, age_years=age + 1.0)
        when = dates.iat[i]
        objects.append({
            "id": str(row.get("candidate_id")),
            "break_date": None if pd.isna(when) else when.date().isoformat(),
            "age_years": round(age, 1),
            "area_m2": round(area),
            "mass_t": round(now.mass_t.p50, 1),
            "removal_kzt": round(now.removal_cost_kzt.p50),
            "recyclable_kzt": round(now.recyclable_value_kzt.p50),
            "climate_kzt": round(now.climate_cost_kzt.p50),
            "damage_p10": round(now.net_damage_kzt.p10),
            "damage_p50": round(now.net_damage_kzt.p50),
            "damage_p90": round(now.net_damage_kzt.p90),
            # Что выгоднее сделать: два решения и цена выбора между ними.
            "plain_kzt": round(now.plain_removal_kzt.p50),
            "sorted_kzt": round(now.sorted_removal_kzt.p50),
            # Экономия — разность показанных величин, а не медиана
            # разности. Величины эти отличаются (3,30 против 3,10 млн ₸
            # на крупнейшем объекте), и обе верны, но вычитает столбец
            # читатель. Число, которое не сходится с таблицей над ним,
            # обесценивает и таблицу, и объяснение.
            # Честный разброс экономии — рядом, из того же розыгрыша.
            "saving_kzt": round(now.plain_removal_kzt.p50 - now.sorted_removal_kzt.p50),
            "saving_p10": round(now.sorting_saving_kzt.p10),
            "saving_p90": round(now.sorting_saving_kzt.p90),
            "breakeven_share": round(now.breakeven_surcharge_share.p50, 3),
            "co2e_t": round(now.co2e_t.p50, 1),
            "co2e_emitted_t": round(now.co2e_emitted_t.p50, 1),
            "co2e_preventable_t": round(now.co2e_preventable_t.p50, 1),
            "co2e_next_year_t": round(
                max(0.0, later.co2e_emitted_t.p50 - now.co2e_emitted_t.p50), 1),
            "penalty_kzt": round(float(row.get("penalty_kzt") or 0.0)),
            "check_source": str(row.get("check_source") or ""),
            "visual_check": str(row.get("visual_check") or ""),
            "removal_status": str(row.get("removal_status") or ""),
        })

    items = [(float(o["area_m2"]), float(o["age_years"])) for o in objects]
    whole = portfolio(items, economics)
    later_whole = portfolio([(a, y + 1.0) for a, y in items], economics)

    carbon = economics.triangular("carbon_price_kzt_per_t_co2e")
    waiting_t = max(0.0, later_whole["co2e_emitted_t"].p50 - whole["co2e_emitted_t"].p50)

    # Приоритет: накопленная доля суммы по объектам от дорогого к дешёвому.
    # Это и есть рекомендация — не «вот пятнадцать точек», а «вот три, и
    # они держат половину денег».
    order = sorted(objects, key=lambda o: o["damage_p50"], reverse=True)
    total_damage = sum(o["damage_p50"] for o in order) or 1
    running, priority = 0, []
    for n, obj in enumerate(order, start=1):
        running += obj["damage_p50"]
        priority.append({"n": n, "id": obj["id"], "share": round(running / total_damage, 4)})

    # Чувствительность считается на медианном по площади объекте: она
    # зависит от площади слабо, а называть, на чём считали, надо.
    median_area = float(pd.Series([o["area_m2"] for o in objects]).median())
    drivers = sensitivity(median_area, economics)

    rejected = funnel.get("rejected", {})
    reviewed = rejected.get("ПРОШЁЛ ОТСЕВ", 0)
    payload = {
        "generated": date.today().isoformat(),
        "iterations": int(economics.section("monte_carlo")["iterations"]),
        "queue": {
            "raw": funnel.get("raw", 0),
            "auto_rejected": sum(v for k, v in rejected.items() if k != "ПРОШЁЛ ОТСЕВ"),
            "reviewed": reviewed,
            "published": len(objects),
            "ground": sum(1 for o in objects if o["check_source"] == "ground"),
        },
        "objects": objects,
        "priority": priority,
        "totals": {
            "objects": len(objects),
            "area_m2": sum(o["area_m2"] for o in objects),
            # Интервал по списку целиком: складываются итерации, а не
            # процентили. Рядом лежит наивная сумма — чтобы разница между
            # правильным и привычным способом была видна, а не заявлена.
            "mass_t": {k: round(v, 1) for k, v in _pct(whole["mass_t"]).items()},
            "removal_kzt": _pct(whole["removal_cost_kzt"]),
            "recyclable_kzt": _pct(whole["recyclable_value_kzt"]),
            "climate_kzt": _pct(whole["climate_cost_kzt"]),
            "damage_kzt": _pct(whole["net_damage_kzt"]),
            # Решения по списку целиком. Интервал экономии считается по
            # портфелю: надбавка за разбор у подрядчика одна на все
            # объекты, и разыгрывать её по каждому заново значило бы
            # обещать усреднение, которого не будет.
            "plain_kzt": _pct(whole["plain_removal_kzt"]),
            "sorted_kzt": _pct(whole["sorted_removal_kzt"]),
            "saving_kzt": _pct(whole["sorting_saving_kzt"]),
            "breakeven_share": round(whole["breakeven_surcharge_share"].p50, 3),
            "naive_damage_kzt": {
                "p10": sum(o["damage_p10"] for o in objects),
                "p50": sum(o["damage_p50"] for o in objects),
                "p90": sum(o["damage_p90"] for o in objects),
            },
            # Сумма медиан по объектам — то, что получится, если сложить
            # столбец таблицы на экране. Она обязана быть на странице
            # именно потому, что её сложат: медиана суммы (45,7 млн)
            # больше суммы медиан (43,0 млн) на несимметричности
            # распределения, и человек с калькулятором найдёт эту разницу
            # раньше, чем дослушает объяснение. Поэтому крупным на
            # странице стоит складываемое число, а портфельный расчёт
            # даёт интервал и назван отдельно.
            "sum_of_medians": {
                "mass_t": round(sum(o["mass_t"] for o in objects), 1),
                "removal_kzt": sum(o["removal_kzt"] for o in objects),
                "recyclable_kzt": sum(o["recyclable_kzt"] for o in objects),
                "climate_kzt": sum(o["climate_kzt"] for o in objects),
                "damage_kzt": sum(o["damage_p50"] for o in objects),
                # Метан тоже складывается по объектам, а не берётся из
                # портфельного розыгрыша: в candidates.geojson лежат
                # медианы по объектам, и дека с картой обязаны показывать
                # одно и то же число.
                "co2e_t": round(sum(o["co2e_t"] for o in objects), 1),
                "co2e_emitted_t": round(sum(o["co2e_emitted_t"] for o in objects), 1),
                "co2e_preventable_t": round(sum(o["co2e_preventable_t"] for o in objects), 1),
                "co2e_next_year_t": round(sum(o["co2e_next_year_t"] for o in objects), 1),
                "plain_kzt": sum(o["plain_kzt"] for o in objects),
                "sorted_kzt": sum(o["sorted_kzt"] for o in objects),
                "saving_kzt": sum(o["plain_kzt"] - o["sorted_kzt"] for o in objects),
            },
            "co2e_t": round(whole["co2e_t"].p50, 1),
            "co2e_emitted_t": round(whole["co2e_emitted_t"].p50, 1),
            "co2e_preventable_t": round(whole["co2e_preventable_t"].p50, 1),
            "penalty_kzt": sum(o["penalty_kzt"] for o in objects),
            # Цена ожидания: что уйдёт в атмосферу за следующие двенадцать
            # месяцев, если список останется нетронутым.
            "waiting_year_co2e_t": round(waiting_t, 1),
            "waiting_year_kzt": round(waiting_t * carbon.typical),
        },
        "sensitivity": {k: round(v, 3) for k, v in
                        sorted(drivers.items(), key=lambda kv: -abs(kv[1]))},
        "sensitivity_area_m2": round(median_area),
        "fractions": list(RECYCLABLE_FRACTIONS),
        "provenance": provenance(economics),
    }

    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    t = payload["totals"]
    print(f"── Экономика выгружена: {OUT.relative_to(ROOT)}")
    print(f"   объектов {t['objects']}, масса {t['mass_t']['p50']:,.0f} т".replace(",", " "))
    print(f"   вывоз {t['removal_kzt']['p50'] / 1e6:.1f} млн ₸, "
          f"вторсырьё {t['recyclable_kzt']['p50'] / 1e6:.1f} млн ₸, "
          f"климат {t['climate_kzt']['p50'] / 1e6:.1f} млн ₸")
    print(f"   ущерб по списку {t['damage_kzt']['p10'] / 1e6:.1f}–"
          f"{t['damage_kzt']['p90'] / 1e6:.1f} млн ₸ "
          f"(медиана {t['damage_kzt']['p50'] / 1e6:.1f})")
    print(f"   наивная сумма процентилей дала бы "
          f"{t['naive_damage_kzt']['p10'] / 1e6:.1f}–"
          f"{t['naive_damage_kzt']['p90'] / 1e6:.1f} млн ₸")
    print(f"   год ожидания: {t['waiting_year_co2e_t']:,.0f} т CO₂-экв".replace(",", " "))
    som = t["sum_of_medians"]
    print(f"   решение: обычный вывоз {som['plain_kzt'] / 1e6:.1f} млн ₸ против "
          f"{som['sorted_kzt'] / 1e6:.1f} млн ₸ с разбором, "
          f"экономия {som['saving_kzt'] / 1e6:.1f} млн ₸")
    print(f"   разбор окупается, пока он дешевле "
          f"{t['breakeven_share']:.0%} стоимости вывоза")
    return 0


def _pct(p) -> dict[str, float]:
    return {"p10": round(p.p10), "p50": round(p.p50), "p90": round(p.p90)}


if __name__ == "__main__":
    sys.exit(main())
