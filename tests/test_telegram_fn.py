"""Тесты бессерверной функции бота — `api/telegram.py`.

Модуль до сих пор не был покрыт ничем: он живёт вне пакета `vantage`,
импортируется Vercel по пути и в тесты не попадал. Пока `/stats` печатал
два числа, цена этого была невелика. Теперь команда называет площадь,
ущерб и число выездов — то же самое, что стоит в шапке карты, — и
разойтись эти числа могут молча: сайт читает GeoJSON, бот читает
выжимку из него, и никто их между собой не сверяет.

Здесь сверяются. Проверяется не текст сообщения, а то, что числа в нём
взяты из данных: подпись можно переписать, ущерб — нет.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    """Импорт по пути: `api` — не пакет, обычным import его не достать."""
    spec = importlib.util.spec_from_file_location("vantage_api_telegram", ROOT / "api/telegram.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bot():
    return _load()


@pytest.fixture
def items():
    return json.loads((ROOT / "api/candidates.json").read_text(encoding="utf-8"))


class TestStats:
    def test_counts_come_from_the_export(self, bot, items):
        text = bot.stats_text()
        assert f"<b>{len(items)}</b>" in text
        confirmed = sum(1 for i in items if i.get("visual_check") == "landfill")
        assert f"<b>{confirmed}</b>" in text

    def test_damage_matches_the_sum_over_real_objects(self, bot, items):
        """Отвергнутые проверкой в сумму не входят — как и на сайте.

        Считать по всем объектам было бы проще и неверно: карта в шапке
        печатает сумму по тем, что остались свалками, и два разных ответа
        на «сколько всего» хуже, чем один.
        """
        real = [i for i in items if i.get("visual_check") != "not_landfill"]
        damage = sum(float(i.get("damage_p50") or 0) for i in real)
        assert bot.kzt(damage) in bot.stats_text()

    def test_area_matches_the_sum_over_real_objects(self, bot, items):
        real = [i for i in items if i.get("visual_check") != "not_landfill"]
        hectares = sum(float(i.get("area_m2") or 0) for i in real) / 10000
        assert f"{hectares:.1f}".replace(".", ",") + " га" in bot.stats_text()

    def test_names_the_most_expensive_object(self, bot, items):
        top = max(items, key=lambda i: float(i.get("damage_p50") or 0))
        assert top["id"] in bot.stats_text()

    def test_half_of_the_damage_is_a_number_of_trips(self, bot, items):
        """То же число, что в панели «С чего начать» на карте."""
        real = sorted(
            (i for i in items if i.get("visual_check") != "not_landfill"),
            key=lambda i: float(i.get("damage_p50") or 0),
            reverse=True,
        )
        total = sum(float(i.get("damage_p50") or 0) for i in real)
        running, trips = 0.0, 0
        for item in real:
            if running >= total / 2:
                break
            running += float(item.get("damage_p50") or 0)
            trips += 1
        assert f"Половину суммы закрывают <b>{trips}</b>" in bot.stats_text()

    def test_trips_are_declined(self, bot):
        """«1 выезда» на защите читают вслух, и это слышно."""
        assert bot.plural(1, "выезд", "выезда", "выездов") == "выезд"
        assert bot.plural(4, "выезд", "выезда", "выездов") == "выезда"
        assert bot.plural(5, "выезд", "выезда", "выездов") == "выездов"
        # Одиннадцать — не «одиннадцать один»: подростковые числа особые.
        assert bot.plural(11, "выезд", "выезда", "выездов") == "выездов"
        assert bot.plural(21, "выезд", "выезда", "выездов") == "выезд"

    def test_does_not_talk_about_the_hosting(self, bot):
        """Жителю не рассказывают, как устроен бэкенд.

        Раньше половину ответа занимало объяснение, почему не ведётся
        счётчик сообщений. Правдивое и не то, за чем приходят.
        """
        text = bot.stats_text().lower()
        for word in ("хранилищ", "счётчик", "бессервер"):
            assert word not in text

    def test_empty_export_does_not_crash(self, bot, monkeypatch):
        monkeypatch.setattr(bot, "candidates", lambda: [])
        assert "недоступна" in bot.stats_text()


class TestFormatting:
    def test_millions_use_a_comma(self, bot):
        assert bot.kzt(8_302_281).startswith("8,3")

    def test_billions_switch_units(self, bot):
        assert "млрд" in bot.kzt(2_400_000_000)

    def test_small_sums_stay_in_thousands(self, bot):
        assert "тыс" in bot.kzt(43_000)

    def test_month_is_in_the_prepositional_case(self, bot):
        """«в мае 2024», а не «в мая 2024» — русский не режется по пробелу."""
        assert bot.when("2024-05-01") == "в мае 2024"

    def test_missing_date_gives_nothing_rather_than_a_guess(self, bot):
        assert bot.when("") == ""
        assert bot.when("2024") == ""
        assert bot.when("2024-13-01") == ""
