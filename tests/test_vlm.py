"""Тесты зрительной модели как второго слоя доверификации.

Главное свойство, которое здесь проверяется, — необязательность. Пайплайн
не должен зависеть ни от провайдера, ни от наличия ключа: без них
доверификация продолжает работать на текстурном анализе, и это штатный
режим, а не деградация.
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np
import pytest

from vantage.vlm import RESPONSE_SCHEMA, ClaudeVlmVerifier, build_verifier


class TestGracefulAbsence:
    def test_no_key_means_not_available(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert ClaudeVlmVerifier().available is False

    def test_builder_returns_none_instead_of_raising(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert build_verifier() is None

    def test_failure_gives_zero_confidence_not_a_verdict(self):
        """Недоступная модель не должна ни подтверждать, ни опровергать.

        Ноль уверенности означает, что порог 0.7 в is_confirmed не
        сработает и решение вернётся к текстуре. Выдуманный вердикт был бы
        хуже отсутствия ответа.
        """
        verifier = ClaudeVlmVerifier(api_key="ключа-нет")
        answer = verifier.verify(np.zeros((8, 8, 3), dtype="uint8"), "тест")
        assert answer["confidence"] == 0.0
        assert answer["is_landfill"] is False
        assert "нет ответа" in answer["reasoning"]

    def test_answer_shape_matches_protocol(self):
        verifier = ClaudeVlmVerifier(api_key="ключа-нет")
        answer = verifier.verify(np.zeros((4, 4, 3), dtype="uint8"), "тест")
        assert set(answer) == {"is_landfill", "confidence", "reasoning"}


class TestImageEncoding:
    def test_uint8_rgb_passes_through(self):
        from vantage.vlm import _to_png

        data = _to_png(np.full((16, 16, 3), 120, dtype="uint8"))
        assert data[:8] == b"\x89PNG\r\n\x1a\n"

    def test_float_image_is_normalised_by_its_own_range(self):
        """Тёмный снимок не должен уйти в модель чёрным квадратом.

        Нормировка по 0..255 превратила бы диапазон 0.02..0.09 в ноль;
        нормировка по фактическому диапазону сохраняет структуру, а
        структура — это и есть признак свалки.
        """
        from PIL import Image

        from vantage.vlm import _to_png

        dark = np.linspace(0.02, 0.09, 16 * 16 * 3, dtype="float32").reshape(16, 16, 3)
        picture = Image.open(__import__("io").BytesIO(_to_png(dark)))
        values = np.asarray(picture)
        assert values.max() > 200, "структура пропала при нормировке"
        assert values.min() < 40

    def test_grayscale_becomes_rgb(self):
        from PIL import Image

        from vantage.vlm import _to_png

        picture = Image.open(__import__("io").BytesIO(_to_png(np.zeros((8, 8), dtype="uint8"))))
        assert picture.mode == "RGB"


class TestSchema:
    def test_schema_matches_what_verify_expects(self):
        """Три поля, те же, что читает VerificationResult.is_confirmed."""
        assert set(RESPONSE_SCHEMA["required"]) == {"is_landfill", "confidence", "reasoning"}

    def test_schema_forbids_extra_fields(self):
        """Строгая схема — не педантизм.

        Ответ вида «Конечно! Вот JSON: ```json…» ломает разбор, и ловить
        это регулярками — заведомо проигранная война. Схема снимает
        вопрос на стороне API.
        """
        assert RESPONSE_SCHEMA["additionalProperties"] is False


@pytest.mark.parametrize("value,expected", [(-1.0, 0.0), (0.5, 0.5), (7.0, 1.0)])
def test_confidence_is_clamped(value, expected, monkeypatch):
    """Уверенность вне 0..1 сломала бы порог подтверждения, а не просто
    выглядела бы странно."""
    import json as json_module

    class FakeBlock:
        type = "text"
        text = json_module.dumps({"is_landfill": True, "confidence": value, "reasoning": "x"})

    class FakeResponse:
        stop_reason = "end_turn"
        content: ClassVar[list] = [FakeBlock()]

    class FakeMessages:
        def create(self, **_):
            return FakeResponse()

    class FakeClient:
        messages = FakeMessages()

    verifier = ClaudeVlmVerifier(api_key="есть")
    monkeypatch.setattr(verifier, "_ensure_client", lambda: FakeClient())
    answer = verifier.verify(np.zeros((4, 4, 3), dtype="uint8"), "тест")
    assert answer["confidence"] == expected
