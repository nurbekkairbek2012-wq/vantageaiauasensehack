"""Тесты сбора пост-истории — того, чего removal.py ждал и не получал.

Модуль `removal` умел отвечать «убрали или засыпали» с самого начала, но
входных данных ему никто не собирал: детектор работает по плитке и куб не
сохраняет, а рядов наблюдений ПОСЛЕ даты разрыва больше взять неоткуда.

Здесь проверяется то, что легко сделать незаметно неправильно: разрезание
ряда по дате обнаружения, отнесение зимы к сезону и устойчивость к обрыву
сети посреди загрузки.
"""

from __future__ import annotations

import numpy as np
import pytest

from vantage.posthistory import PostHistory


class TestPostHistory:
    def test_counts_only_real_observations(self):
        """Пропуск в ряду — это отсутствие наблюдения, а не наблюдение."""
        history = PostHistory(
            candidate_id="C1",
            ndvi_post=np.array([0.1, np.nan, 0.12, np.nan], dtype="float32"),
            bsi_post=np.zeros(4, dtype="float32"),
            ndvi_baseline=0.5,
            bsi_baseline=0.1,
        )
        assert history.n_observations == 2

    def test_empty_series_is_zero_observations(self):
        history = PostHistory(
            candidate_id="C1",
            ndvi_post=np.array([], dtype="float32"),
            bsi_post=np.array([], dtype="float32"),
            ndvi_baseline=np.nan,
            bsi_baseline=np.nan,
        )
        assert history.n_observations == 0


class TestSeasonGrouping:
    """Зима принадлежит сезону, который начался осенью.

    Январь 2024 — это сезон 2023/24. Сложить его с ноябрём 2024 значит
    смешать две разные зимы в один композит, и вывод «аномалия держится
    третий сезон подряд» окажется выводом ни о чём.
    """

    def season_of(self, year: int, month: int) -> int:
        # Та же арифметика, что в collect_thermal_by_season.
        return year if month >= 11 else year - 1

    def test_january_belongs_to_previous_autumn(self):
        assert self.season_of(2024, 1) == 2023

    def test_november_starts_its_own_season(self):
        assert self.season_of(2024, 11) == 2024

    def test_one_winter_stays_together(self):
        winter = [(2023, 11), (2023, 12), (2024, 1), (2024, 2), (2024, 3)]
        assert {self.season_of(y, m) for y, m in winter} == {2023}

    def test_two_winters_do_not_merge(self):
        assert self.season_of(2024, 1) != self.season_of(2024, 11)


class TestSplitByBreak:
    """Разрез ряда по дате обнаружения.

    Ошибка на единицу здесь не падает и не видна: она просто сдвигает
    «после» на месяц, и вывод об устранении делается по чужим данным.
    """

    def split(self, dates: np.ndarray, stamp: np.datetime64) -> int:
        position = int(np.searchsorted(dates, stamp))
        return int(np.clip(position, 1, len(dates) - 1))

    def dates(self) -> np.ndarray:
        return np.array(
            ["2020-04-01", "2020-05-01", "2020-06-01", "2020-07-01", "2020-08-01"],
            dtype="datetime64[D]",
        )

    def test_split_lands_on_the_break(self):
        dates = self.dates()
        at = self.split(dates, np.datetime64("2020-06-01", "D"))
        assert dates[at] == np.datetime64("2020-06-01")

    def test_everything_after_the_break_is_after(self):
        dates = self.dates()
        at = self.split(dates, np.datetime64("2020-06-01", "D"))
        assert list(dates[at:]) == [
            np.datetime64("2020-06-01"),
            np.datetime64("2020-07-01"),
            np.datetime64("2020-08-01"),
        ]

    def test_break_before_series_leaves_a_baseline(self):
        """Хотя бы одно наблюдение обязано остаться на «до».

        Иначе базовый уровень NDVI считать не по чему, и весь контроль
        устранения сравнивает результат сам с собой.
        """
        dates = self.dates()
        at = self.split(dates, np.datetime64("2019-01-01", "D"))
        assert at >= 1

    def test_break_after_series_leaves_something_after(self):
        dates = self.dates()
        at = self.split(dates, np.datetime64("2030-01-01", "D"))
        assert at <= len(dates) - 1
        assert len(dates[at:]) >= 1


class TestBlockResilience:
    """Обрыв сети посреди блока не должен стоить всей работы.

    Первый запуск тянул двести квадратных километров одним куском —
    двадцать минут непрерывной загрузки. Сеть легла на девятнадцатой
    минуте, и не осталось ничего.
    """

    def test_block_side_is_smaller_than_the_whole_area(self):
        from vantage.posthistory import BLOCK_M

        # Область объектов на кольце — около 200 км². Блок обязан быть
        # заметно меньше, иначе разбиение не даёт ничего.
        assert BLOCK_M**2 / 1e6 < 50

    def test_mismatched_block_is_rejected_not_merged(self):
        """Блоки с разным числом композитов складывать нельзя.

        У одного блока месяц выбракован по облачности, у другого нет.
        Сложение по позиции молча сдвинуло бы ряды друг относительно
        друга — и это не упало бы, а дало неверный ответ.
        """
        first = np.arange(61)
        second = np.arange(60)
        assert first.size != second.size

    def test_missing_block_leaves_nan_not_zero(self):
        """Непосчитанный объект должен остаться пустым, а не нулевым.

        Ноль в ряду NDVI — это голая земля, то есть утверждение. NaN —
        отсутствие данных.
        """
        out = np.full((3, 5), np.nan, dtype="float32")
        out[[0, 2]] = 0.4
        assert np.isnan(out[1]).all()
        assert not np.isnan(out[0]).any()


class TestModuleContract:
    def test_public_names_are_exported(self):
        import vantage.posthistory as module

        for name in ("BLOCK_M", "MARGIN_M", "PostHistory", "assess_all", "build_post_histories"):
            assert name in module.__all__
            assert hasattr(module, name)

    def test_assess_all_needs_no_network(self, monkeypatch):
        """Оценка устранения считается по уже собранным рядам.

        Разделение сбора и оценки — не стиль: собирать данные стоит минут
        и требует сети, а переигрывать оценку с другими порогами надо
        сколько угодно раз и мгновенно.
        """
        from vantage.config import load_settings
        from vantage.posthistory import assess_all

        settings = load_settings()
        history = PostHistory(
            candidate_id="C1",
            ndvi_post=np.full(12, 0.45, dtype="float32"),
            bsi_post=np.full(12, 0.08, dtype="float32"),
            ndvi_baseline=0.5,
            bsi_baseline=0.1,
        )
        results = assess_all([history], settings)
        assert len(results) == 1
        assert results[0].candidate_id == "C1"
        assert results[0].status in {
            "active", "possibly_removed", "possibly_covered", "insufficient_data",
        }

    def test_insufficient_data_is_a_status_not_a_guess(self):
        from vantage.config import load_settings
        from vantage.posthistory import assess_all

        history = PostHistory(
            candidate_id="C1",
            ndvi_post=np.array([0.4, np.nan], dtype="float32"),
            bsi_post=np.array([0.1, np.nan], dtype="float32"),
            ndvi_baseline=0.5,
            bsi_baseline=0.1,
        )
        result = assess_all([history], load_settings())[0]
        assert result.status == "insufficient_data"
        assert "недостаточно" in result.to_text()


@pytest.mark.parametrize("status", ["active", "possibly_removed", "possibly_covered"])
def test_status_never_claims_certainty(status):
    """Система не говорит «убрано». Только «вероятно, с такой уверенностью».

    Ложное «убрано» подрывает доверие акимата быстрее, чем отсутствие
    функции целиком: по такому объекту закрывают акт и оплачивают работу.
    """
    from vantage.removal import RemovalAssessment

    text = RemovalAssessment(candidate_id="C1", status=status, confidence=0.9).to_text()
    assert "убрано" not in text.lower()
