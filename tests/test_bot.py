"""Тесты гражданского контура.

Ни одного обращения к Telegram: вся содержательная логика вынесена
в reports.py и проверяется без сети и токена. Тест, требующий живого
бота, на защите не запустится, а значит его не будут запускать вообще.

Проверяются четыре вещи, каждая из которых важна отдельно:
  * география считается по поверхности Земли, а не по градусам;
  * сообщение вне известных объектов создаёт нового кандидата —
    это закрывает главное ограничение системы, слепоту к мелким свалкам;
  * подтверждения не накручивают уверенность до единицы;
  * идентификатор отправителя необратим.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import geopandas as gpd
import pytest
from shapely.geometry import box

from vantage.bot.app import (
    BotContext,
    format_citizen_alert,
    format_new_candidate_alert,
    handle_location,
    subscribers_from_env,
)
from vantage.bot.reports import (
    CONFIRMATION_BOOST,
    DAILY_LIMIT_PER_SENDER,
    MIN_INTERVAL_S,
    CitizenReport,
    RateLimited,
    ReportStore,
    confidence_after_confirmation,
    hash_sender,
    haversine_m,
    match_report,
)

# Точка в пригороде Астаны
LAT, LON = 51.2050, 71.6025


def make_candidates() -> gpd.GeoDataFrame:
    """Два спутниковых кандидата: один вокруг LAT/LON, второй далеко."""
    return gpd.GeoDataFrame(
        {
            "candidate_id": ["C00000", "C00001"],
            "area_m2": [5_400.0, 2_100.0],
            "probability": [0.72, 0.65],
            "geometry": [
                box(71.600, 51.204, 71.605, 51.206),
                box(71.900, 51.300, 71.905, 51.302),
            ],
        },
        crs="EPSG:4326",
    )


def make_report(lat: float = LAT, lon: float = LON, **kwargs) -> CitizenReport:
    defaults = dict(report_id="r1", sender_hash="abc123", latitude=lat, longitude=lon)
    defaults.update(kwargs)
    return CitizenReport(**defaults)


# --------------------------------------------------------------------------- #
#  География
# --------------------------------------------------------------------------- #


class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine_m(LAT, LON, LAT, LON) == pytest.approx(0.0, abs=1e-6)

    def test_one_degree_of_latitude_is_about_111_km(self):
        assert haversine_m(51.0, 71.0, 52.0, 71.0) == pytest.approx(111_000, rel=0.01)

    def test_longitude_degree_is_shorter_at_high_latitude(self):
        """На широте Астаны градус долготы почти вдвое короче градуса широты.

        Наивное евклидово расстояние по градусам ошиблось бы здесь в разы
        именно в направлении восток-запад — и сопоставление сообщений
        со спутником поехало бы вбок.
        """
        by_lat = haversine_m(51.0, 71.0, 52.0, 71.0)
        by_lon = haversine_m(51.0, 71.0, 51.0, 72.0)
        assert by_lon < by_lat * 0.7

    def test_short_distance_is_accurate(self):
        # 0.001 градуса широты ≈ 111 м
        assert haversine_m(51.200, 71.600, 51.201, 71.600) == pytest.approx(111, rel=0.02)


# --------------------------------------------------------------------------- #
#  Сопоставление
# --------------------------------------------------------------------------- #


class TestMatching:
    def test_report_inside_known_object_matches(self):
        result = match_report(make_report(), make_candidates())
        assert result.matched
        assert result.candidate_id == "C00000"
        assert result.distance_m < 150

    def test_report_far_away_creates_new_object(self):
        """Главная ценность контура: житель находит то, чего спутник не видит."""
        result = match_report(make_report(lat=51.4000, lon=71.9500), make_candidates())
        assert not result.matched
        assert result.is_new_object

    def test_matching_radius_is_respected(self):
        # Точка примерно в 300 м севернее объекта — за радиусом 150 м
        result = match_report(make_report(lat=51.2077, lon=71.6025), make_candidates())
        assert not result.matched

    def test_empty_candidate_set_gives_new_object(self):
        result = match_report(make_report(), None)
        assert result.is_new_object
        assert result.distance_m is None

    def test_picks_the_nearest_candidate(self):
        result = match_report(make_report(), make_candidates())
        assert result.candidate_id == "C00000"

    def test_reply_text_differs_by_outcome(self):
        matched = match_report(make_report(), make_candidates()).to_user_text()
        new = match_report(make_report(lat=51.40, lon=71.95), make_candidates()).to_user_text()
        assert "уже есть" in matched
        assert "не было" in new
        assert matched != new


# --------------------------------------------------------------------------- #
#  Уверенность
# --------------------------------------------------------------------------- #


class TestConfidence:
    def test_no_confirmations_keeps_value(self):
        assert confidence_after_confirmation(0.72, 0) == pytest.approx(0.72)

    def test_first_confirmation_adds_full_boost(self):
        assert confidence_after_confirmation(0.60, 1) == pytest.approx(0.60 + CONFIRMATION_BOOST)

    def test_boost_diminishes(self):
        """Второе подтверждение весит меньше первого.

        Иначе десять сообщений из одного двора вывели бы объект
        в стопроцентную уверенность без единой проверки на месте.
        """
        first = confidence_after_confirmation(0.5, 1) - 0.5
        second = confidence_after_confirmation(0.5, 2) - confidence_after_confirmation(0.5, 1)
        assert second < first

    def test_never_reaches_certainty(self):
        """Полной уверенности без выезда не бывает."""
        assert confidence_after_confirmation(0.95, 100) <= 0.99

    def test_unknown_probability_starts_from_half(self):
        assert confidence_after_confirmation(None, 0) == 0.5


# --------------------------------------------------------------------------- #
#  Приватность
# --------------------------------------------------------------------------- #


class TestPrivacy:
    def test_hash_is_stable(self):
        assert hash_sender(123456, salt="s") == hash_sender(123456, salt="s")

    def test_different_senders_differ(self):
        assert hash_sender(1, salt="s") != hash_sender(2, salt="s")

    def test_hash_does_not_contain_raw_id(self):
        assert "987654321" not in hash_sender(987654321, salt="s")

    def test_salt_changes_the_hash(self):
        """Без соли пространство id Telegram перебирается за минуты."""
        assert hash_sender(123, salt="a") != hash_sender(123, salt="b")

    def test_report_does_not_store_raw_identity(self):
        report = make_report(sender_hash=hash_sender(999, salt="s"))
        assert "999" not in str(report.as_dict()["sender_hash"])


# --------------------------------------------------------------------------- #
#  Антиспам
# --------------------------------------------------------------------------- #


class TestRateLimiting:
    def test_accepts_first_report(self):
        store = ReportStore()
        assert store.add(make_report()) is not None

    def test_blocks_rapid_repeat(self):
        store = ReportStore()
        now = datetime.now()
        store.add(make_report(report_id="r1"), now=now)
        with pytest.raises(RateLimited, match="слишком часто"):
            store.add(make_report(report_id="r2"), now=now + timedelta(seconds=5))

    def test_allows_after_interval(self):
        store = ReportStore()
        now = datetime.now()
        store.add(make_report(report_id="r1"), now=now)
        later = now + timedelta(seconds=MIN_INTERVAL_S + 1)
        report = make_report(report_id="r2", created_at=later)
        assert store.add(report, now=later) is not None

    def test_enforces_daily_limit(self):
        store = ReportStore()
        base = datetime.now()
        for i in range(DAILY_LIMIT_PER_SENDER):
            moment = base + timedelta(minutes=i * 5)
            store.add(make_report(report_id=f"r{i}", created_at=moment), now=moment)
        moment = base + timedelta(hours=2)
        with pytest.raises(RateLimited, match="суточный лимит"):
            store.add(make_report(report_id="over", created_at=moment), now=moment)

    def test_limits_are_per_sender(self):
        store = ReportStore()
        now = datetime.now()
        store.add(make_report(report_id="r1", sender_hash="sender-a"), now=now)
        # Другой отправитель не должен пострадать от чужого лимита
        assert store.add(make_report(report_id="r2", sender_hash="sender-b"), now=now)


# --------------------------------------------------------------------------- #
#  Хранилище
# --------------------------------------------------------------------------- #


class TestReportStore:
    def test_writes_to_disk(self, tmp_path):
        path = tmp_path / "reports.jsonl"
        ReportStore(path).add(make_report())
        assert path.exists()
        assert "51.205" in path.read_text(encoding="utf-8")

    def test_tracks_confirmations(self):
        store = ReportStore()
        candidates = make_candidates()
        for i in range(3):
            moment = datetime.now() + timedelta(minutes=i * 5)
            report = make_report(report_id=f"r{i}", sender_hash=f"s{i}", created_at=moment)
            store.add(report, now=moment)
            store.apply_match(report, match_report(report, candidates))
        assert store.confirmations_for("C00000") == 3

    def test_unmatched_queue_holds_new_objects(self):
        store = ReportStore()
        candidates = make_candidates()
        far = make_report(report_id="far", lat=51.40, lon=71.95)
        store.add(far)
        store.apply_match(far, match_report(far, candidates))
        assert [r.report_id for r in store.unmatched()] == ["far"]

    def test_stats_are_consistent(self):
        store = ReportStore()
        candidates = make_candidates()
        now = datetime.now()
        near = make_report(report_id="near", sender_hash="a", created_at=now)
        far = make_report(report_id="far", sender_hash="b", lat=51.40, lon=71.95, created_at=now)
        for report in (near, far):
            store.add(report, now=now)
            store.apply_match(report, match_report(report, candidates))
        stats = store.stats()
        assert stats == {"total": 2, "matched": 1, "new_objects": 1, "unique_senders": 2}


# --------------------------------------------------------------------------- #
#  Валидация и обработчики
# --------------------------------------------------------------------------- #


class TestValidation:
    def test_rejects_impossible_latitude(self):
        with pytest.raises(ValueError, match="широта"):
            make_report(lat=120.0)

    def test_rejects_impossible_longitude(self):
        with pytest.raises(ValueError, match="долгота"):
            make_report(lon=200.0)


class TestHandlers:
    def _context(self) -> BotContext:
        return BotContext(store=ReportStore(), candidates=make_candidates())

    def test_matched_location_gets_confirmation_reply(self):
        reply, report = handle_location(
            self._context(), sender_id=42, latitude=LAT, longitude=LON
        )
        assert report is not None
        assert report.status == "matched"
        assert "уже есть" in reply

    def test_new_location_creates_candidate(self):
        reply, report = handle_location(
            self._context(), sender_id=42, latitude=51.40, longitude=71.95
        )
        assert report.status == "new"
        assert "не было" in reply

    def test_rate_limited_sender_gets_explanation_not_silence(self):
        context = self._context()
        handle_location(context, sender_id=42, latitude=LAT, longitude=LON)
        reply, report = handle_location(context, sender_id=42, latitude=LAT, longitude=LON)
        assert report is None
        assert "не принято" in reply

    def test_alert_for_service_contains_coordinates(self):
        report = make_report()
        text = format_citizen_alert(report, matched=True)
        assert "51.205" in text
        assert "Подтверждение" in text

    def test_alert_marks_object_invisible_to_satellite(self):
        text = format_citizen_alert(make_report(), matched=False)
        assert "спутник не видел" in text

    def test_new_candidate_alert_is_actionable(self):
        row = make_candidates().iloc[0]
        text = format_new_candidate_alert(row)
        assert "Координаты" in text
        assert "Площадь" in text
        assert "требует проверки" in text


class TestSubscribers:
    def test_parses_chat_ids(self, monkeypatch):
        monkeypatch.setenv("VANTAGE_BOT_SUBSCRIBERS", "123456, -100987654")
        assert subscribers_from_env() == (123456, -100987654)

    def test_empty_env_gives_empty_tuple(self, monkeypatch):
        monkeypatch.delenv("VANTAGE_BOT_SUBSCRIBERS", raising=False)
        assert subscribers_from_env() == ()

    def test_garbage_is_skipped_not_fatal(self, monkeypatch):
        monkeypatch.setenv("VANTAGE_BOT_SUBSCRIBERS", "123456,мусор,789")
        assert subscribers_from_env() == (123456, 789)


class TestBotContext:
    def test_reads_probability_from_candidates(self):
        context = BotContext(store=ReportStore(), candidates=make_candidates())
        assert context.candidate_probability("C00000") == pytest.approx(0.72)

    def test_unknown_candidate_gives_none(self):
        context = BotContext(store=ReportStore(), candidates=make_candidates())
        assert context.candidate_probability("нет такого") is None

    def test_no_candidates_gives_none(self):
        assert BotContext(store=ReportStore()).candidate_probability("C00000") is None
