"""Gemini как зрительная модель: протокол, ошибки, квота — без сети.

Сеть в тестах не используется: httpx.post подменяется. Тест, зависящий
от живого API, упал бы ночью на квоте бесплатного ключа и приучил бы
команду не смотреть на красный прогон.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from vantage import vlm
from vantage.vlm import GeminiQuotaError, GeminiVlmVerifier, build_verifier


class _Response:
    def __init__(self, status: int, payload: dict | None = None, text: str = ""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload


def _answer(obj: dict) -> _Response:
    return _Response(200, {"candidates": [{"content": {"parts": [{"text": json.dumps(obj)}]}}]})


@pytest.fixture()
def image():
    return np.zeros((16, 16, 3), dtype="uint8")


def test_key_goes_in_header_not_url(monkeypatch, image):
    """Адреса попадают в журналы, заголовки — нет."""
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"], seen["headers"], seen["body"] = url, headers, json
        return _answer({"is_landfill": True, "confidence": 0.9, "reasoning": "кучи"})

    monkeypatch.setattr("httpx.post", fake_post)
    out = GeminiVlmVerifier(api_key="секрет").verify(image, "вопрос")
    assert out == {"is_landfill": True, "confidence": 0.9, "reasoning": "кучи"}
    assert "секрет" not in seen["url"]
    assert seen["headers"]["x-goog-api-key"] == "секрет"
    parts = seen["body"]["contents"][0]["parts"]
    assert parts[0]["inline_data"]["mime_type"] == "image/png"
    assert parts[-1]["text"] == "вопрос"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"


def test_confidence_is_clamped(monkeypatch, image):
    monkeypatch.setattr("httpx.post", lambda *a, **k: _answer(
        {"is_landfill": True, "confidence": 7, "reasoning": ""}))
    assert GeminiVlmVerifier(api_key="k").verify(image, "q")["confidence"] == 1.0


def test_failure_gives_zero_confidence_not_a_verdict(monkeypatch, image):
    monkeypatch.setattr("httpx.post", lambda *a, **k: _Response(400, text="bad request"))
    out = GeminiVlmVerifier(api_key="k", retries=0).verify(image, "q")
    assert out["confidence"] == 0.0 and out["is_landfill"] is False


def test_daily_quota_is_not_retried(monkeypatch, image):
    """Дневная квота не восстановится через минуту — повторять нельзя."""
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return _Response(429, text='{"error": "Quota exceeded: GenerateRequestsPerDay"}')

    monkeypatch.setattr("httpx.post", fake_post)
    with pytest.raises(GeminiQuotaError):
        GeminiVlmVerifier(api_key="k", retries=3).ask([image], "q", {"type": "object"})
    assert len(calls) == 1


def test_transient_error_is_retried(monkeypatch, image):
    answers = [_Response(503, text="overloaded"), _answer({"ok": True})]
    monkeypatch.setattr("httpx.post", lambda *a, **k: answers.pop(0))
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert GeminiVlmVerifier(api_key="k").ask([image], "q", {"type": "object"}) == {"ok": True}


def test_builder_prefers_claude_then_gemini(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert isinstance(build_verifier(), GeminiVlmVerifier)


def test_no_gemini_key_means_not_available(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert GeminiVlmVerifier().available is False


def test_schema_has_no_fields_gemini_rejects():
    """Gemini не принимает additionalProperties в схеме ответа."""
    assert "additionalProperties" not in vlm._GEMINI_VERIFY_SCHEMA
