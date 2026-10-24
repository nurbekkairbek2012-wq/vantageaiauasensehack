"""Экран «Экономика» обязан считать то же, что лежит на карте.

── Что здесь проверяется ───────────────────────────────────────────────

Экономика выгружается отдельным скриптом и отдельным файлом. Это
четвёртый случай схемы «связанные файлы пишутся разными шагами», на
которой проект уже трижды получал расходящиеся числа: слияние городов в
обход фильтра, указатель бота с 49 объектами против 16 на сайте и
сценарий таймлапса, наводящийся на снятый объект.

Поэтому здесь не «работает ли функция», а ровно два свойства, поломка
которых заметна только на защите:

  * ущерб по объекту в economy.json совпадает с candidates.geojson —
    иначе на карте одна сумма, на экране экономики другая, и вопрос
    «какой из них верить» закрывает разговор;
  * интервал по списку у́же наивной суммы процентилей и содержит её
    медиану — иначе портфельный расчёт не имеет смысла.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web-next/public/data"

pytestmark = pytest.mark.skipif(
    not ((DATA / "economy.json").exists() and (DATA / "candidates.geojson").exists()),
    reason="нет выгрузки",
)


@pytest.fixture(scope="module")
def pair():
    import geopandas as gpd

    economy = json.loads((DATA / "economy.json").read_text(encoding="utf-8"))
    site = gpd.read_file(DATA / "candidates.geojson")
    return economy, site


def test_damage_matches_map(pair):
    """Ущерб по каждому объекту — тот же, что на карте.

    Денежный слой один, зерно одно, конфиг один; расхождение здесь
    означало бы, что экран экономики пересчитал по-другому.
    """
    economy, site = pair
    on_map = {
        str(row["candidate_id"]): float(row["damage_p50"])
        for _, row in site.iterrows()
        if row.get("visual_check") != "not_landfill"
    }
    assert on_map, "на карте нет опубликованных объектов"

    for obj in economy["objects"]:
        assert obj["id"] in on_map, f"объекта {obj['id']} нет на карте"
        # Округление до тенге при выгрузке — единственная допустимая разница.
        assert abs(obj["damage_p50"] - on_map[obj["id"]]) <= 1.0


def test_no_object_lost(pair):
    """Списки совпадают по составу, а не только по числам."""
    economy, site = pair
    published = {
        str(row["candidate_id"])
        for _, row in site.iterrows()
        if row.get("visual_check") != "not_landfill"
    }
    assert {o["id"] for o in economy["objects"]} == published


def test_portfolio_interval_is_narrower(pair):
    """Интервал по списку у́же суммы интервалов — и это не совпадение.

    Сумма P10 по объектам отвечает на вопрос «а если ВСЕ пятнадцать
    одновременно окажутся дешевле, чем в девяти случаях из десяти».
    Такое событие менее вероятно, чем обещает подпись «P10», и потому
    наивная сумма всегда шире.
    """
    economy, _ = pair
    whole = economy["totals"]["damage_kzt"]
    naive = economy["totals"]["naive_damage_kzt"]

    assert whole["p10"] < whole["p50"] < whole["p90"]
    assert whole["p90"] - whole["p10"] < naive["p90"] - naive["p10"]
    # Сумма медиан обязана лежать внутри интервала по списку: иначе это
    # уже не разные вопросы к одному распределению, а разные данные.
    assert whole["p10"] <= naive["p50"] <= whole["p90"]


def test_priority_is_sorted_and_complete(pair):
    """Приоритет — накопленная доля, а не просто порядок.

    Проверяется монотонность и то, что последний объект закрывает 100%:
    иначе «четыре выезда закрывают половину суммы» — фраза без основания.
    """
    economy, _ = pair
    shares = [p["share"] for p in economy["priority"]]
    assert shares == sorted(shares)
    assert len(shares) == len(economy["objects"])
    assert shares[-1] == pytest.approx(1.0, abs=1e-3)


def test_every_assumption_declares_origin(pair):
    """У каждого допущения на экране есть происхождение.

    Число без пометки «подтверждено / выведено / инженерная оценка»
    невозможно проверить, а на защите непроверяемое число хуже
    отсутствующего.
    """
    economy, _ = pair
    assert economy["provenance"], "происхождение допущений не выгружено"
    for key, entry in economy["provenance"].items():
        assert entry["kind"] in {"source", "derived", "estimate"}, key
        assert entry["note"].strip(), key


def test_recyclable_never_exceeds_removal(pair):
    """Сырьё не может стоить дороже вывоза — это была бы не свалка, а прииск.

    Проверка защищает от подмены знака в формуле: ущерб считается как
    вывоз минус сырьё плюс климат, и перепутанный знак дал бы
    отрицательный итог, который легко не заметить в миллионах.
    """
    economy, _ = pair
    totals = economy["totals"]
    assert 0 < totals["recyclable_kzt"]["p50"] < totals["removal_kzt"]["p50"]
    assert totals["damage_kzt"]["p50"] > 0
