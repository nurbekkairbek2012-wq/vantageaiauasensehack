"""Тесты сервиса и ролевого доступа.

Это тесты не функциональности, а **границы**. Утечка точных координат
в публичный контур не сломает ни один сценарий использования — она просто
тихо произойдёт. Поэтому каждая граница закреплена отдельным тестом:

  * публичный запрос не получает ни координат, ни идентификаторов;
  * без токена закрытые ручки отвечают 404, а не 403;
  * оператор вывоза не может подтверждать акты;
  * каждое обращение к точным данным попадает в журнал.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import box

pytest.importorskip("fastapi", reason="нужен FastAPI (pip install -e .[service])")
pytest.importorskip("reportlab", reason="нужен reportlab (pip install -e .[service])")

from fastapi.testclient import TestClient

from vantage.api import create_app
from vantage.api.security import (
    AccessLog,
    TokenRegistry,
    blur_coordinate,
    has_at_least,
    sanitize_for_role,
)
from vantage.api.store import Store, candidate_to_record

OPERATOR_TOKEN = "operator-token-1234"
AKIMAT_TOKEN = "akimat-token-1234"


def make_candidates(n: int = 3) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "candidate_id": [f"C{i:05d}" for i in range(n)],
            "area_m2": np.linspace(2_000, 8_000, n),
            "probability": np.linspace(0.95, 0.60, n),
            "ndvi_drop": np.full(n, 0.28),
            "bsi_rise": np.full(n, 0.19),
            "verify_providers": np.full(n, 2),
            "break_date": [np.datetime64("2022-05-15")] * n,
            "geometry": [
                box(71.60 + i * 0.01, 51.20, 71.605 + i * 0.01, 51.205) for i in range(n)
            ],
        },
        crs="EPSG:4326",
    )


def make_risk_public() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "risk_class": [1, 3, 4],
            "geometry": [box(71.5 + i * 0.05, 51.1, 71.55 + i * 0.05, 51.15) for i in range(3)],
        },
        crs="EPSG:4326",
    )


@pytest.fixture
def client(tmp_path):
    tokens = TokenRegistry()
    tokens.add(OPERATOR_TOKEN, "operator")
    tokens.add(AKIMAT_TOKEN, "akimat")

    store = Store(candidates=make_candidates(), risk_public=make_risk_public())
    app = create_app(
        store=store,
        tokens=tokens,
        access_log=AccessLog(tmp_path / "access.log"),
    )
    return TestClient(app), app


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
#  Роли
# --------------------------------------------------------------------------- #


class TestRoleHierarchy:
    def test_hierarchy_is_ordered(self):
        assert has_at_least("admin", "public")
        assert has_at_least("akimat", "operator")
        assert not has_at_least("public", "operator")
        assert not has_at_least("operator", "akimat")

    def test_unknown_role_raises(self):
        with pytest.raises(ValueError):
            has_at_least("хакер", "public")  # type: ignore[arg-type]


class TestTokenRegistry:
    def test_resolves_registered_token(self):
        registry = TokenRegistry()
        registry.add(OPERATOR_TOKEN, "operator")
        assert registry.resolve(OPERATOR_TOKEN) == "operator"

    def test_unknown_token_is_public_not_error(self):
        """Неизвестный токен даёт публичный доступ, а не привилегии."""
        assert TokenRegistry().resolve("что-то-левое") == "public"

    def test_no_token_is_public(self):
        assert TokenRegistry().resolve(None) == "public"

    def test_tokens_are_stored_hashed(self):
        """Токен не должен восстанавливаться из дампа реестра."""
        registry = TokenRegistry()
        registry.add(OPERATOR_TOKEN, "operator")
        assert OPERATOR_TOKEN not in str(registry.__dict__)

    def test_rejects_short_token(self):
        with pytest.raises(ValueError):
            TokenRegistry().add("abc", "admin")

    def test_rejects_unknown_role(self):
        with pytest.raises(ValueError):
            TokenRegistry().add("длинный-токен", "суперюзер")  # type: ignore[arg-type]

    def test_empty_env_gives_empty_registry(self, monkeypatch):
        """Система без настройки должна быть закрытой, а не открытой."""
        monkeypatch.delenv("VANTAGE_API_TOKENS", raising=False)
        assert len(TokenRegistry.from_env()) == 0

    def test_parses_env_pairs(self, monkeypatch):
        monkeypatch.setenv(
            "VANTAGE_API_TOKENS", f"{OPERATOR_TOKEN}:operator,{AKIMAT_TOKEN}:akimat"
        )
        registry = TokenRegistry.from_env()
        assert registry.resolve(OPERATOR_TOKEN) == "operator"
        assert registry.resolve(AKIMAT_TOKEN) == "akimat"


class TestCoordinateBlurring:
    def test_two_digits_is_about_a_kilometre(self):
        assert blur_coordinate(51.208134, 2) == 51.21

    def test_rejects_negative_precision(self):
        with pytest.raises(ValueError):
            blur_coordinate(51.2, -1)

    def test_public_record_loses_identifiers_and_money(self):
        record = {
            "candidate_id": "C00001",
            "latitude": 51.208134,
            "longitude": 71.612455,
            "area_m2": 5400,
            "damage_p50": 13_268_247,
            "risk_class": 3,
        }
        public = sanitize_for_role(record, "public", precision_digits=2)
        assert "candidate_id" not in public
        assert "area_m2" not in public
        assert "damage_p50" not in public
        assert public["latitude"] == 51.21

    def test_operator_keeps_everything(self):
        record = {"candidate_id": "C1", "latitude": 51.208134, "damage_p50": 100}
        assert sanitize_for_role(record, "operator", precision_digits=2) == record


# --------------------------------------------------------------------------- #
#  Публичный контур
# --------------------------------------------------------------------------- #


class TestPublicEndpoints:
    def test_health_is_open(self, client):
        api, _ = client
        assert api.get("/health").status_code == 200

    def test_summary_has_no_addresses(self, client):
        api, _ = client
        body = api.get("/public/summary").json()
        assert body["objects"] == 3
        text = str(body)
        assert "latitude" not in text
        assert "candidate_id" not in text
        assert "C00000" not in text

    def test_summary_carries_disclaimer(self, client):
        """Публичная цифра без оговорки о вероятностном характере —
        это уже утверждение, за которое придётся отвечать."""
        api, _ = client
        assert "оценкой" in api.get("/public/summary").json()["disclaimer"]

    def test_public_risk_returns_only_class(self, client):
        api, _ = client
        body = api.get("/public/risk").json()
        assert body["type"] == "FeatureCollection"
        for feature in body["features"]:
            assert set(feature["properties"]) == {"risk_class"}
            assert "risk" not in feature["properties"]


# --------------------------------------------------------------------------- #
#  Закрытый контур
# --------------------------------------------------------------------------- #


class TestPrivateAccess:
    def test_anonymous_cannot_list_candidates(self, client):
        """404, а не 403: сам факт наличия объектов — уже информация."""
        api, _ = client
        assert api.get("/private/candidates").status_code == 404

    def test_operator_can_list(self, client):
        api, _ = client
        response = api.get("/private/candidates", headers=auth(OPERATOR_TOKEN))
        assert response.status_code == 200
        assert response.json()["count"] == 3

    def test_listing_contains_exact_coordinates(self, client):
        api, _ = client
        item = api.get("/private/candidates", headers=auth(OPERATOR_TOKEN)).json()["items"][0]
        assert item["latitude"] == pytest.approx(51.2025, abs=0.01)
        assert len(str(item["latitude"]).split(".")[1]) > 2

    def test_probability_filter_works(self, client):
        api, _ = client
        body = api.get(
            "/private/candidates?min_probability=0.9", headers=auth(OPERATOR_TOKEN)
        ).json()
        assert body["count"] == 1

    def test_candidate_card_includes_evidence(self, client):
        api, _ = client
        body = api.get("/private/candidates/C00000", headers=auth(OPERATOR_TOKEN)).json()
        assert "evidence" in body
        assert body["evidence"]["n_agreeing"] >= 1
        assert "признаков" in body["evidence"]["text"]

    def test_unknown_candidate_is_404(self, client):
        api, _ = client
        assert api.get("/private/candidates/НЕТ", headers=auth(OPERATOR_TOKEN)).status_code == 404

    def test_anonymous_cannot_read_card(self, client):
        api, _ = client
        assert api.get("/private/candidates/C00000").status_code == 404


class TestActWorkflow:
    def test_operator_creates_draft(self, client):
        api, _ = client
        body = api.post("/private/candidates/C00000/act", headers=auth(OPERATOR_TOKEN)).json()
        assert body["status"] == "draft"
        assert "черновик" in body["warning"]

    def test_operator_cannot_approve(self, client):
        """Ключевая граница: оператор вывоза имеет коммерческий интерес
        в объёме работ и не должен подтверждать собственный заказ."""
        api, _ = client
        api.post("/private/candidates/C00000/act", headers=auth(OPERATOR_TOKEN))
        response = api.post(
            "/private/acts/C00000/approve",
            params={"reviewer_name": "Оператор", "reviewer_position": "водитель"},
            headers=auth(OPERATOR_TOKEN),
        )
        assert response.status_code == 403
        assert "экологическая служба" in response.json()["detail"]

    def test_akimat_can_approve(self, client):
        api, _ = client
        api.post("/private/candidates/C00000/act", headers=auth(AKIMAT_TOKEN))
        response = api.post(
            "/private/acts/C00000/approve",
            params={
                "reviewer_name": "Абдрахманова Алима",
                "reviewer_position": "главный специалист",
            },
            headers=auth(AKIMAT_TOKEN),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "approved"

    def test_double_approval_is_rejected(self, client):
        api, _ = client
        api.post("/private/candidates/C00000/act", headers=auth(AKIMAT_TOKEN))
        params = {"reviewer_name": "Иванов", "reviewer_position": "специалист"}
        api.post("/private/acts/C00000/approve", params=params, headers=auth(AKIMAT_TOKEN))
        second = api.post("/private/acts/C00000/approve", params=params, headers=auth(AKIMAT_TOKEN))
        assert second.status_code == 409

    def test_approval_without_draft_is_404(self, client):
        api, _ = client
        response = api.post(
            "/private/acts/C00001/approve",
            params={"reviewer_name": "Иванов", "reviewer_position": "специалист"},
            headers=auth(AKIMAT_TOKEN),
        )
        assert response.status_code == 404

    def test_pdf_download_returns_a_pdf(self, client):
        api, _ = client
        api.post("/private/candidates/C00000/act", headers=auth(OPERATOR_TOKEN))
        response = api.get("/private/acts/C00000/pdf", headers=auth(OPERATOR_TOKEN))
        assert response.status_code == 200
        assert response.content.startswith(b"%PDF")

    def test_anonymous_cannot_download_act(self, client):
        api, _ = client
        api.post("/private/candidates/C00000/act", headers=auth(OPERATOR_TOKEN))
        assert api.get("/private/acts/C00000/pdf").status_code == 404


class TestAccessLog:
    def test_private_access_is_recorded(self, client):
        api, app = client
        api.get("/private/candidates/C00000", headers=auth(OPERATOR_TOKEN))
        entries = app.state.access_log.recent()
        assert any(e["action"] == "get_candidate" and e["target"] == "C00000" for e in entries)
        assert entries[-1]["role"] == "operator"

    def test_public_access_is_not_recorded(self, client):
        """Журнал нужен для контроля за адресными данными,
        а не для слежки за посетителями публичной карты."""
        api, app = client
        api.get("/public/summary")
        api.get("/public/risk")
        assert app.state.access_log.recent() == []

    def test_log_is_visible_only_to_service(self, client):
        api, _ = client
        api.get("/private/candidates", headers=auth(OPERATOR_TOKEN))
        assert api.get("/private/access-log", headers=auth(OPERATOR_TOKEN)).status_code == 404
        assert api.get("/private/access-log", headers=auth(AKIMAT_TOKEN)).status_code == 200

    def test_log_is_written_to_disk(self, tmp_path):
        """Журнал, исчезающий при перезапуске, журналом не является."""
        path = tmp_path / "access.log"
        journal = AccessLog(path)
        journal.record("akimat", "get_candidate", "C00001")
        assert path.exists()
        assert "C00001" in path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
#  Хранилище
# --------------------------------------------------------------------------- #


class TestStore:
    def test_empty_store_reports_zero(self):
        assert Store().summary() == {"objects": 0}
        assert Store().is_empty()

    def test_summary_aggregates(self):
        store = Store(candidates=make_candidates(3))
        summary = store.summary()
        assert summary["objects"] == 3
        assert summary["total_area_ha"] > 0

    def test_lookup_by_id(self):
        store = Store(candidates=make_candidates(3))
        assert store.get_candidate("C00001") is not None
        assert store.get_candidate("нет такого") is None

    def test_missing_files_do_not_crash_loading(self, tmp_path):
        store = Store.load(tmp_path)
        assert store.n_candidates == 0

    def test_record_drops_missing_values(self):
        row = make_candidates(1).iloc[0]
        record = candidate_to_record(row)
        assert "candidate_id" in record
        assert "damage_p50" not in record  # в таблице этой колонки нет
