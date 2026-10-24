"""Поиск сцен в STAC-каталогах.

Данные не скачиваются целиком. Снимки Sentinel-2/1 и Landsat лежат в облаке
как Cloud-Optimized GeoTIFF, и мы читаем только нужные окна по HTTP-range.
Этот модуль отвечает только за одно: найти, какие сцены вообще существуют
для заданной области и периода. Загрузка пикселей — в :mod:`vantage.raster`.

Основной источник — Microsoft Planetary Computer: у него открытый STAC API
и подписывание URL по запросу, без регистрации и ключей.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

import pystac_client

from .aoi import AOI
from .config import Settings

log = logging.getLogger(__name__)

PLANETARY_COMPUTER_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
EARTH_SEARCH_STAC = "https://earth-search.aws.element84.com/v1"


@dataclass(frozen=True)
class SceneRef:
    """Ссылка на одну сцену: минимум, нужный для последующей загрузки."""

    id: str
    collection: str
    datetime: str
    cloud_cover: float | None
    bbox: tuple[float, float, float, float]
    assets: dict[str, str]

    @property
    def day(self) -> date:
        return date.fromisoformat(self.datetime[:10])


class StacCatalog:
    """Обёртка над pystac-client с подписыванием ссылок Planetary Computer.

    Почему обёртка, а не прямой вызов: подписывание URL, ретраи и разбор
    ответов нужны в нескольких местах пайплайна, а различия между провайдерами
    (Planetary Computer / Earth Search) не должны протекать в бизнес-логику.
    """

    def __init__(self, url: str = PLANETARY_COMPUTER_STAC, *, sign: bool | None = None) -> None:
        self.url = url
        # Подписывание нужно только для Planetary Computer
        self._sign = (url == PLANETARY_COMPUTER_STAC) if sign is None else sign
        self._client: pystac_client.Client | None = None

    @property
    def client(self) -> pystac_client.Client:
        """Ленивое подключение: объект каталога можно создать без сети."""
        if self._client is None:
            modifier = None
            if self._sign:
                try:
                    import planetary_computer

                    modifier = planetary_computer.sign_inplace
                except ImportError:  # pragma: no cover
                    log.warning(
                        "planetary-computer не установлен — ссылки не будут подписаны, "
                        "часть ассетов окажется недоступна"
                    )
            self._client = pystac_client.Client.open(self.url, modifier=modifier)
        return self._client

    # ------------------------------------------------------------------ #
    #  Поиск
    # ------------------------------------------------------------------ #

    def search(
        self,
        *,
        collection: str,
        aoi: AOI,
        start: str,
        end: str,
        query: dict[str, Any] | None = None,
        limit: int = 500,
    ) -> list[SceneRef]:
        """Найти сцены коллекции, пересекающие AOI в заданном интервале."""
        search = self.client.search(
            collections=[collection],
            intersects=aoi.geo_interface,
            datetime=f"{start}/{end}",
            query=query or None,
            limit=limit,
        )
        items = list(search.items())
        log.info("STAC %s: найдено %d сцен для %s (%s..%s)", collection, len(items), aoi.name, start, end)
        return [_to_scene_ref(item) for item in items]

    def search_sentinel2(self, aoi: AOI, settings: Settings) -> list[SceneRef]:
        """Sentinel-2 L2A с фильтром по облачности сцены.

        Фильтр по облачности сцены — грубый: он отсекает бесполезные снимки
        целиком. Попиксельная маска облаков (SCL) применяется позже, в
        :mod:`vantage.raster` — сцена с 50% облаков может быть полностью
        чистой над нашей плиткой.
        """
        scenes = self.search(
            collection=settings.sentinel2.collection,
            aoi=aoi,
            start=settings.time.start,
            end=settings.time.end,
            query={"eo:cloud_cover": {"lt": settings.sentinel2.max_scene_cloud_pct}},
        )
        return list(filter_by_month(scenes, settings.time.valid_months))

    def search_sentinel1(self, aoi: AOI, settings: Settings) -> list[SceneRef]:
        """Sentinel-1 RTC — радар, нужен для признака стабильности поверхности."""
        return self.search(
            collection=settings.sentinel1.collection,
            aoi=aoi,
            start=settings.time.start,
            end=settings.time.end,
        )

    def search_landsat(self, aoi: AOI, settings: Settings) -> list[SceneRef]:
        """Landsat Collection 2 L2 — нужен тепловой канал."""
        return self.search(
            collection=settings.landsat.collection,
            aoi=aoi,
            start=settings.time.start,
            end=settings.time.end,
            query={"eo:cloud_cover": {"lt": settings.sentinel2.max_scene_cloud_pct}},
        )


# --------------------------------------------------------------------------- #
#  Вспомогательное
# --------------------------------------------------------------------------- #


def _to_scene_ref(item) -> SceneRef:
    props = item.properties
    return SceneRef(
        id=item.id,
        collection=item.collection_id,
        datetime=props.get("datetime") or props.get("start_datetime") or "",
        cloud_cover=props.get("eo:cloud_cover"),
        bbox=tuple(item.bbox),  # type: ignore[arg-type]
        assets={k: a.href for k, a in item.assets.items()},
    )


def filter_by_month(scenes: Iterable[SceneRef], valid_months: list[int]) -> Iterable[SceneRef]:
    """Оставить только съёмку в пригодные месяцы.

    Зимой степь под снегом: NDVI, BSI и PMLI теряют смысл, а тепловая
    аномалия, наоборот, видна лучше всего. Поэтому спектральная ветка
    пайплайна работает по тёплому сезону, а тепловая — по холодному.
    """
    allowed = set(valid_months)
    for scene in scenes:
        if scene.day.month in allowed:
            yield scene


def summarize(scenes: list[SceneRef]) -> dict[str, Any]:
    """Краткая сводка по выборке сцен — для CLI и логов."""
    if not scenes:
        return {"count": 0}
    days = sorted(s.day for s in scenes)
    clouds = [s.cloud_cover for s in scenes if s.cloud_cover is not None]
    by_year: dict[int, int] = {}
    for d in days:
        by_year[d.year] = by_year.get(d.year, 0) + 1
    return {
        "count": len(scenes),
        "first": days[0].isoformat(),
        "last": days[-1].isoformat(),
        "mean_cloud_pct": round(sum(clouds) / len(clouds), 1) if clouds else None,
        "per_year": dict(sorted(by_year.items())),
    }


__all__ = [
    "PLANETARY_COMPUTER_STAC",
    "EARTH_SEARCH_STAC",
    "SceneRef",
    "StacCatalog",
    "filter_by_month",
    "summarize",
]
