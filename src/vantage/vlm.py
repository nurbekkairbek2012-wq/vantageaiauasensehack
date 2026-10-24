"""Зрительная модель как второй слой доверификации.

Первый слой — текстурный анализ: свалка это структурный хаос, множество
мелких объектов разного цвета и яркости. Он работает всегда и без ключей,
но он груб: ровный строительный развал он тоже назовёт хаосом.

Второй слой смотрит на тайл глазами и отвечает словами, которые можно
прочитать в акте: «разбросанные мешки и покрышки, колеи техники» против
«правильные прямоугольные контуры котлована».

Почему вынесено отдельным модулем
---------------------------------
Пайплайн не должен зависеть ни от провайдера, ни от наличия ключа.
:mod:`vantage.verify` объявляет протокол ``VlmVerifier`` и принимает его
снаружи; здесь лежит одна конкретная реализация, которую можно не
подключать. Без ключа доверификация продолжает работать на текстуре.

Ответ модели структурирован схемой, а не выпрашивается словами «ответь
строго в JSON». Разница практическая: строка «Конечно! Вот JSON: ```json...»
ломает разбор, и ловить это регулярками — заведомо проигранная война.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)

#: Переменная окружения с ключом. Имя стандартное для SDK, чтобы ключ,
#: уже настроенный на машине, подхватился без дополнительных действий.
API_KEY_ENV = "ANTHROPIC_API_KEY"

#: Модель по умолчанию.
DEFAULT_MODEL = "claude-opus-5"

#: Схема ответа. Те же три поля, что ждёт verify.VerificationResult.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_landfill": {
            "type": "boolean",
            "description": "Похоже ли на несанкционированную свалку",
        },
        "confidence": {
            "type": "number",
            "description": "Уверенность от 0 до 1",
        },
        "reasoning": {
            "type": "string",
            "description": "Одно предложение: что именно видно на снимке",
        },
    },
    "required": ["is_landfill", "confidence", "reasoning"],
    "additionalProperties": False,
}


def _to_png(image: np.ndarray) -> bytes:
    """Превратить массив тайлов в PNG.

    Тайлы приходят как uint8 RGB; если пришло что-то другое, нормируем по
    фактическому диапазону, а не по 0..255 — иначе тёмный снимок уйдёт в
    модель чёрным квадратом.
    """
    from PIL import Image

    array = np.asarray(image)
    if array.dtype != np.uint8:
        finite = array[np.isfinite(array)]
        lo, hi = (float(finite.min()), float(finite.max())) if finite.size else (0.0, 1.0)
        span = hi - lo if hi > lo else 1.0
        array = np.clip((array - lo) / span, 0, 1)
        array = (array * 255).astype("uint8")

    if array.ndim == 2:
        picture = Image.fromarray(array, mode="L").convert("RGB")
    else:
        picture = Image.fromarray(array[..., :3], mode="RGB")

    buffer = io.BytesIO()
    picture.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


@dataclass
class ClaudeVlmVerifier:
    """Реализация ``verify.VlmVerifier`` поверх Claude Messages API.

    Подключается снаружи и только явно::

        from vantage.verify import verify_candidates
        from vantage.vlm import ClaudeVlmVerifier

        results = verify_candidates(candidates, settings.verify,
                                    vlm=ClaudeVlmVerifier())

    Ошибка одного тайла не роняет доверификацию: метод возвращает ответ с
    нулевой уверенностью и текстом ошибки. Пустая уверенность честнее
    выдуманного вердикта, а :meth:`VerificationResult.is_confirmed`
    принимает вердикт модели только при уверенности от 0.7.
    """

    model: str = DEFAULT_MODEL
    max_tokens: int = 1024
    api_key: str | None = None

    def __post_init__(self) -> None:
        self._client = None

    @property
    def available(self) -> bool:
        """Есть ли чем работать. Проверяется до прогона, а не в середине."""
        if self.api_key or os.environ.get(API_KEY_ENV):
            try:
                import anthropic  # noqa: F401
            except ImportError:
                return False
            return True
        return False

    def _ensure_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self.api_key or None)
        return self._client

    def verify(self, image: np.ndarray, prompt: str) -> dict:
        """Вернуть вердикт по одному тайлу."""
        try:
            client = self._ensure_client()
            payload = base64.standard_b64encode(_to_png(image)).decode("ascii")

            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": RESPONSE_SCHEMA,
                    }
                },
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": payload,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )

            if response.stop_reason == "refusal":
                log.warning("Зрительная модель отказалась отвечать по тайлу")
                return self._empty("модель отказалась отвечать")

            text = next((b.text for b in response.content if b.type == "text"), "")
            verdict = json.loads(text)
            return {
                "is_landfill": bool(verdict.get("is_landfill", False)),
                # Уверенность зажимается: значение вне 0..1 сломало бы
                # порог подтверждения, а не просто выглядело бы странно.
                "confidence": float(np.clip(float(verdict.get("confidence", 0.0)), 0.0, 1.0)),
                "reasoning": str(verdict.get("reasoning", "")).strip(),
            }

        except Exception as exc:
            log.warning("Зрительная модель не ответила: %s", type(exc).__name__)
            return self._empty(f"{type(exc).__name__}")

    @staticmethod
    def _empty(reason: str) -> dict:
        """Ответ, который не влияет на решение.

        Уверенность ноль означает, что порог 0.7 в
        ``VerificationResult.is_confirmed`` не сработает и вердикт вернётся
        к текстурному анализу. Это и нужно: недоступная модель не должна
        ни подтверждать, ни опровергать.
        """
        return {"is_landfill": False, "confidence": 0.0, "reasoning": f"нет ответа: {reason}"}


#: Ключ Gemini API. Бесплатный ключ выдаёт Google AI Studio; платный
#: подставляется в ту же переменную, код при этом не меняется.
GEMINI_KEY_ENV = "GEMINI_API_KEY"

#: Модель Gemini по умолчанию: быстрая и доступная на бесплатном тарифе.
GEMINI_MODEL = "gemini-3.8-flash"

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiQuotaError(RuntimeError):
    """Кончилась квота запросов. Не ошибка снимка — повторять сейчас бессмысленно."""


@dataclass
class GeminiVlmVerifier:
    """Реализация ``verify.VlmVerifier`` поверх Gemini API — через HTTP.

    Без SDK намеренно: запрос — один POST, а лишняя зависимость в проекте
    живёт дольше, чем эксперимент, ради которого её поставили.

    Кроме :meth:`verify` (одна картинка, ответ по схеме доверификации)
    есть :meth:`ask` — несколько картинок и своя схема ответа. Через него
    идёт машинный просмотр: снимок «до» и «после» в одном запросе.

    Ключ в запросе передаётся заголовком, а не в адресе: адреса попадают в
    журналы, заголовки — нет.
    """

    model: str = GEMINI_MODEL
    api_key: str | None = None
    timeout_s: float = 120.0
    #: Сколько раз повторять при временной ошибке сервера или лимите в минуту.
    retries: int = 4

    @property
    def available(self) -> bool:
        return bool(self.api_key or os.environ.get(GEMINI_KEY_ENV))

    def _key(self) -> str:
        key = self.api_key or os.environ.get(GEMINI_KEY_ENV)
        if not key:
            raise RuntimeError(f"нет ключа: переменная {GEMINI_KEY_ENV} пуста")
        return key

    def ask(self, images: list[np.ndarray], prompt: str, schema: dict) -> dict:
        """Отправить картинки и вопрос, вернуть разобранный JSON по схеме."""
        import time

        import httpx

        parts: list[dict] = [
            {"inline_data": {"mime_type": "image/png",
                             "data": base64.standard_b64encode(_to_png(img)).decode("ascii")}}
            for img in images
        ]
        parts.append({"text": prompt})
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        url = GEMINI_URL.format(model=self.model)
        delay = 10.0
        for attempt in range(self.retries + 1):
            response = httpx.post(url, headers={"x-goog-api-key": self._key()},
                                  json=body, timeout=self.timeout_s)
            if response.status_code == 200:
                payload = response.json()
                candidates = payload.get("candidates") or []
                if not candidates:
                    raise RuntimeError(f"пустой ответ: {str(payload)[:200]}")
                text = "".join(p.get("text", "") for p in
                               candidates[0].get("content", {}).get("parts", []))
                return json.loads(text)
            message = response.text[:300]
            # Дневная квота не восстановится повтором через минуту.
            if response.status_code == 429 and ("PerDay" in message
                                                or "per day" in message.lower()):
                raise GeminiQuotaError(message)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                time.sleep(delay)
                delay *= 2
                continue
            if response.status_code == 429:
                raise GeminiQuotaError(message)
            raise RuntimeError(f"HTTP {response.status_code}: {message}")
        raise RuntimeError("исчерпаны повторы")

    def verify(self, image: np.ndarray, prompt: str) -> dict:
        """Вердикт по одному тайлу — в схеме доверификации."""
        try:
            verdict = self.ask([image], prompt, _GEMINI_VERIFY_SCHEMA)
            return {
                "is_landfill": bool(verdict.get("is_landfill", False)),
                "confidence": float(np.clip(float(verdict.get("confidence", 0.0)), 0.0, 1.0)),
                "reasoning": str(verdict.get("reasoning", "")).strip(),
            }
        except Exception as exc:
            log.warning("Gemini не ответил: %s", type(exc).__name__)
            return ClaudeVlmVerifier._empty(type(exc).__name__)


#: Схема доверификации в диалекте Gemini: без additionalProperties.
_GEMINI_VERIFY_SCHEMA = {
    "type": "object",
    "properties": {k: v for k, v in RESPONSE_SCHEMA["properties"].items()},
    "required": list(RESPONSE_SCHEMA["required"]),
}


def build_verifier(model: str | None = None):
    """Собрать проверяющего, если он доступен, иначе честно вернуть None.

    Порядок: Claude, если есть его ключ, иначе Gemini. Вызывающий код
    решает, что делать дальше, и не обязан ловить ImportError или
    разбираться с переменными окружения.
    """
    verifier = ClaudeVlmVerifier(model=model or DEFAULT_MODEL)
    if verifier.available:
        return verifier
    gemini = GeminiVlmVerifier(model=model or GEMINI_MODEL)
    if gemini.available:
        return gemini
    log.info(
        "Зрительная модель не подключена: нет %s (или пакета anthropic) и нет %s. "
        "Доверификация продолжит работать на текстурном анализе.",
        API_KEY_ENV, GEMINI_KEY_ENV,
    )
    return None


__all__ = [
    "API_KEY_ENV",
    "DEFAULT_MODEL",
    "GEMINI_KEY_ENV",
    "GEMINI_MODEL",
    "RESPONSE_SCHEMA",
    "ClaudeVlmVerifier",
    "GeminiQuotaError",
    "GeminiVlmVerifier",
    "build_verifier",
]
