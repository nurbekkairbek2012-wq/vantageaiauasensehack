"""Доверификация кандидатов снимками высокого разрешения.

Зачем нужен отдельный шаг
-------------------------
Sentinel-2 даёт 10 метров на пиксель. Этого достаточно, чтобы **найти**
изменение поверхности, но недостаточно, чтобы **увидеть** мусор. На 10
метрах свалка и грунтовая площадка выглядят одинаково — серым пятном.
Поэтому лучшие кандидаты проверяются повторно тайлами базовых карт
с разрешением порядка 0.3–0.6 м, где уже различимы отдельные объекты:
покрышки, мешки, колеи техники.

Почему несколько провайдеров, а не один
---------------------------------------
Ставка на один источник — это единая точка отказа. Лимит, недоступность,
отсутствие свежей съёмки именно над нужным квадратом — и вся ветка
доверификации не работает в день сдачи. Провайдеры опрашиваются по
очереди, и результат считается подтверждённым при согласии минимум двух.

Два уровня проверки
-------------------
**Текстурный анализ** (``texture_score``) работает всегда и без ключей.
Свалка — это структурный хаос: множество мелких объектов разного цвета
и яркости, дающих высокую локальную дисперсию и плотность краёв.
Ровное поле, асфальт и водная гладь дают низкую. Это грубый, но честный
и полностью объяснимый признак.

**Зрительная модель** (``VlmVerifier``) — опциональный слой поверх.
Интерфейс намеренно вынесен наружу: ключ и провайдер задаёт команда,
а пайплайн от них не зависит и работает без них.

Правовая сторона
----------------
Тайлы базовых карт используются для визуальной верификации ограниченного
числа объектов, с соблюдением требований атрибуции провайдера и с
ограничением частоты запросов. Массовое скачивание тайлов условиями
использования не предусмотрено — отсюда лимит ``max_candidates``.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import requests

from .config import VerifyCfg

log = logging.getLogger(__name__)

#: Пауза между запросами тайлов, секунды. Провайдеры базовых карт
#: рассчитаны на интерактивную работу человека, а не на цикл по списку.
REQUEST_DELAY_S = 0.2

TILE_SIZE_PX = 256


# --------------------------------------------------------------------------- #
#  Провайдеры
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TileProvider:
    """Описание источника тайлов."""

    name: str
    url_template: str
    attribution: str
    max_zoom: int = 19
    scheme: str = "xyz"  # xyz | quadkey

    def tile_url(self, x: int, y: int, z: int) -> str:
        if self.scheme == "quadkey":
            return self.url_template.format(q=quadkey(x, y, z))
        return self.url_template.format(x=x, y=y, z=z)


PROVIDERS: dict[str, TileProvider] = {
    "esri_current": TileProvider(
        name="esri_current",
        url_template=(
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        attribution="Esri, Maxar, Earthstar Geographics",
        max_zoom=19,
    ),
    "esri_wayback": TileProvider(
        name="esri_wayback",
        url_template=(
            "https://wayback.maptiles.arcgis.com/arcgis/rest/services/"
            "World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile/{z}/{y}/{x}"
        ),
        attribution="Esri World Imagery Wayback",
        max_zoom=19,
    ),
    "bing": TileProvider(
        name="bing",
        url_template="https://ecn.t3.tiles.virtualearth.net/tiles/a{q}.jpeg?g=1",
        attribution="Microsoft Bing Maps",
        max_zoom=19,
        scheme="quadkey",
    ),
}


# --------------------------------------------------------------------------- #
#  Тайловая арифметика
# --------------------------------------------------------------------------- #


def deg2tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """Координаты тайла по широте/долготе (схема Web Mercator, XYZ).

    Проекция Web Mercator не определена у полюсов, поэтому широта
    ограничивается ±85.05°: за этой границей формула даёт бесконечность.
    """
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"долгота вне диапазона: {lon}")
    lat = max(min(lat, 85.05112878), -85.05112878)
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return min(x, n - 1), min(y, n - 1)


def tile2deg(x: int, y: int, zoom: int) -> tuple[float, float]:
    """Северо-западный угол тайла в градусах."""
    n = 2**zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def quadkey(x: int, y: int, zoom: int) -> str:
    """Quadkey для Bing Maps: тайл кодируется строкой цифр 0..3."""
    key = []
    for i in range(zoom, 0, -1):
        digit = 0
        mask = 1 << (i - 1)
        if x & mask:
            digit += 1
        if y & mask:
            digit += 2
        key.append(str(digit))
    return "".join(key)


def ground_resolution_m(lat: float, zoom: int) -> float:
    """Размер пикселя тайла в метрах на данной широте.

    Нужен, чтобы честно говорить на защите, какое у нас разрешение:
    на широте Астаны оно заметно лучше, чем на экваторе.
    """
    return 156543.03392 * math.cos(math.radians(lat)) / (2**zoom)


# --------------------------------------------------------------------------- #
#  Загрузка
# --------------------------------------------------------------------------- #


class TileFetchError(RuntimeError):
    """Провайдер не отдал тайл."""


def fetch_tile(provider: TileProvider, x: int, y: int, z: int, *, timeout: int = 20) -> np.ndarray:
    """Загрузить один тайл и вернуть массив (H, W, 3) в диапазоне 0..255."""
    from io import BytesIO

    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise ImportError("для доверификации нужен Pillow: pip install pillow") from exc

    url = provider.tile_url(x, y, z)
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "VANTAGE/0.1 (hackathon research)"})
    if response.status_code != 200:
        raise TileFetchError(f"{provider.name}: HTTP {response.status_code}")
    image = Image.open(BytesIO(response.content)).convert("RGB")
    return np.asarray(image, dtype="uint8")


def fetch_tile_grid(
    provider: TileProvider,
    lat: float,
    lon: float,
    zoom: int,
    grid: int = 3,
    *,
    timeout: int = 20,
) -> np.ndarray:
    """Загрузить сетку grid x grid тайлов вокруг точки и склеить в одно изображение.

    Один тайл 256x256 на зуме 17 покрывает примерно 200 м — этого может не
    хватить на крупный объект, поэтому берётся окно из нескольких тайлов.
    """
    if grid < 1 or grid % 2 == 0:
        raise ValueError("grid должен быть нечётным положительным числом")
    zoom = min(zoom, provider.max_zoom)

    cx, cy = deg2tile(lat, lon, zoom)
    half = grid // 2
    rows = []
    for dy in range(-half, half + 1):
        row = []
        for dx in range(-half, half + 1):
            row.append(fetch_tile(provider, cx + dx, cy + dy, zoom, timeout=timeout))
            time.sleep(REQUEST_DELAY_S)
        rows.append(np.concatenate(row, axis=1))
    return np.concatenate(rows, axis=0)


# --------------------------------------------------------------------------- #
#  Текстурный анализ
# --------------------------------------------------------------------------- #


def texture_score(image: np.ndarray) -> dict[str, float]:
    """Признаки визуального хаоса — косвенный индикатор свалки.

    Свалка структурно неоднородна: множество мелких объектов разного
    цвета, размера и яркости. Это даёт:

        edge_density   — высокую плотность градиентов;
        local_variance — высокую локальную дисперсию яркости;
        color_spread   — широкий разброс цвета.

    Поле, асфальт, вода и ровный грунт дают низкие значения по всем трём.
    Признак грубый и не является доказательством — он ранжирует кандидатов
    для человека, а не выносит вердикт.
    """
    if image.ndim != 3 or image.shape[2] < 3:
        raise ValueError("ожидается изображение (H, W, 3)")

    rgb = image[:, :, :3].astype("float32") / 255.0
    gray = rgb.mean(axis=2)

    # Градиенты по обеим осям — плотность краёв
    gy, gx = np.gradient(gray)
    gradient = np.hypot(gx, gy)
    edge_density = float(np.mean(gradient > 0.06))

    # Локальная дисперсия в окне 8x8 через быстрый приём:
    # E[x^2] - (E[x])^2 на блоках
    block = 8
    h = gray.shape[0] // block * block
    w = gray.shape[1] // block * block
    tiles = gray[:h, :w].reshape(h // block, block, w // block, block)
    local_var = float(np.mean(tiles.var(axis=(1, 3))))

    color_spread = float(np.mean(rgb.std(axis=2)))

    # Свёртка в одну оценку. Веса подобраны по физике признака,
    # а не по данным: коэффициенты, подогнанные на десятке примеров,
    # были бы переобучением, которое нечем защитить.
    combined = float(np.clip(2.0 * edge_density + 12.0 * local_var + 3.0 * color_spread, 0.0, 1.0))

    return {
        "edge_density": edge_density,
        "local_variance": local_var,
        "color_spread": color_spread,
        "texture_score": combined,
    }


# --------------------------------------------------------------------------- #
#  Зрительная модель (опциональный слой)
# --------------------------------------------------------------------------- #


class VlmVerifier(Protocol):
    """Интерфейс зрительной модели.

    Реализация задаётся командой и внедряется снаружи: пайплайн не должен
    зависеть от конкретного провайдера и от наличия ключа.
    """

    def verify(self, image: np.ndarray, prompt: str) -> dict:
        """Вернуть структурированный вердикт: {is_landfill, confidence, reasoning}."""
        ...


VLM_PROMPT = (
    "На снимке участок земли рядом с Астаной (Казахстан), разрешение около 0.5 м/пиксель. "
    "Ответь строго в JSON. Признаки несанкционированной свалки: разбросанные предметы разного "
    "размера и цвета, мешки, покрышки, строительный мусор, следы и колеи техники, отсутствие "
    "правильной геометрии. Признаки НЕ свалки: правильные прямоугольные контуры карьера или "
    "котлована, ровная однотонная поверхность, техника в организованном порядке, штабеля "
    "стройматериалов, снегосвалка (однородная белая или серая масса). "
    'Формат: {"is_landfill": true/false, "confidence": 0..1, "reasoning": "одно предложение"}'
)


# --------------------------------------------------------------------------- #
#  Основной проход
# --------------------------------------------------------------------------- #


@dataclass
class VerificationResult:
    """Итог доверификации одного кандидата."""

    candidate_id: str
    providers_ok: list[str] = field(default_factory=list)
    providers_failed: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    vlm: dict | None = None
    ground_resolution_m: float = 0.0

    @property
    def n_providers(self) -> int:
        return len(self.providers_ok)

    def is_confirmed(self, cfg: VerifyCfg, *, min_texture: float = 0.35) -> bool:
        """Подтверждён ли кандидат.

        Требуется и достаточное число ответивших провайдеров, и признак
        визуального хаоса. Если подключена зрительная модель, её вердикт
        имеет приоритет — но только при высокой уверенности.
        """
        if self.n_providers < cfg.min_agreeing_providers:
            return False
        if self.vlm is not None and float(self.vlm.get("confidence", 0.0)) >= 0.7:
            return bool(self.vlm.get("is_landfill", False))
        return float(np.mean(list(self.scores.values()) or [0.0])) >= min_texture


def verify_candidates(
    candidates,
    cfg: VerifyCfg,
    *,
    vlm: VlmVerifier | None = None,
    providers: dict[str, TileProvider] | None = None,
) -> list[VerificationResult]:
    """Доверифицировать топ кандидатов тайлами высокого разрешения.

    Кандидаты берутся в порядке убывания вероятности модели — лимит
    ``cfg.max_candidates`` существует и по условиям использования тайлов,
    и по здравому смыслу: инспектор всё равно не объедет двести точек.
    """
    registry = providers or PROVIDERS
    ranked = candidates
    if "probability" in candidates.columns:
        ranked = candidates.sort_values("probability", ascending=False)
    ranked = ranked.head(cfg.max_candidates)

    # Тайлы адресуются широтой и долготой, поэтому переходим в WGS84
    wgs = ranked.to_crs("EPSG:4326")
    results: list[VerificationResult] = []

    for (_, row), (_, geom_row) in zip(ranked.iterrows(), wgs.iterrows(), strict=True):
        point = geom_row.geometry.representative_point()
        lat, lon = point.y, point.x
        result = VerificationResult(
            candidate_id=str(row.get("candidate_id", "?")),
            ground_resolution_m=ground_resolution_m(lat, cfg.zoom),
        )

        for name in cfg.providers:
            provider = registry.get(name)
            if provider is None:
                log.warning("Неизвестный провайдер тайлов: %s", name)
                continue
            try:
                image = fetch_tile_grid(
                    provider, lat, lon, cfg.zoom, cfg.tile_grid, timeout=cfg.timeout_s
                )
                result.scores[name] = texture_score(image)["texture_score"]
                result.providers_ok.append(name)
                if vlm is not None and result.vlm is None:
                    result.vlm = vlm.verify(image, VLM_PROMPT)
            except Exception as exc:
                log.warning("Провайдер %s не отдал тайлы для %s: %s", name, result.candidate_id, exc)
                result.providers_failed.append(name)

        results.append(result)

    confirmed = sum(1 for r in results if r.is_confirmed(cfg))
    log.info("Доверификация: %d из %d кандидатов подтверждены", confirmed, len(results))
    return results


def attach_verification(candidates, results: list[VerificationResult], cfg: VerifyCfg):
    """Добавить итоги доверификации в таблицу кандидатов."""
    by_id = {r.candidate_id: r for r in results}
    out = candidates.copy()
    out["verify_providers"] = out["candidate_id"].map(
        lambda cid: by_id[cid].n_providers if cid in by_id else 0
    )
    out["verify_texture"] = out["candidate_id"].map(
        lambda cid: float(np.mean(list(by_id[cid].scores.values()) or [0.0])) if cid in by_id else np.nan
    )
    out["verify_confirmed"] = out["candidate_id"].map(
        lambda cid: by_id[cid].is_confirmed(cfg) if cid in by_id else False
    )
    return out


__all__ = [
    "PROVIDERS",
    "REQUEST_DELAY_S",
    "TILE_SIZE_PX",
    "VLM_PROMPT",
    "TileFetchError",
    "TileProvider",
    "VerificationResult",
    "VlmVerifier",
    "attach_verification",
    "deg2tile",
    "fetch_tile",
    "fetch_tile_grid",
    "ground_resolution_m",
    "quadkey",
    "texture_score",
    "tile2deg",
    "verify_candidates",
]
