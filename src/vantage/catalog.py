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
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, ClassVar

import pystac_client

from .aoi import AOI
from .config import Settings

log = logging.getLogger(__name__)

#: Пауза перед повтором запроса к STAC, множится на номер попытки.
RETRY_BACKOFF_S = 3.0

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

    def search_items(
        self,
        *,
        collection: str,
        aoi: AOI,
        start: str,
        end: str,
        query: dict[str, Any] | None = None,
        limit: int = 500,
        attempts: int = 3,
    ) -> list:
        """Найти сцены и вернуть **сырые** ``pystac.Item``.

        Отдельный метод от :meth:`search` нужен потому, что загрузчик
        растров (``odc.stac.load``) принимает именно ``pystac.Item`` —
        ему нужны ассеты с подписанными ссылками и проекционные
        метаданные, которых в компактном :class:`SceneRef` нет.
        Раньше в загрузчик передавались ``SceneRef``, и ветка растров
        не могла отработать в принципе.

        Ретраи здесь, а не у вызывающего: один сетевой таймаут не должен
        ронять шаг пайплайна, который идёт часами.
        """
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                search = self.client.search(
                    collections=[collection],
                    intersects=aoi.geo_interface,
                    datetime=f"{start}/{end}",
                    query=query or None,
                    limit=limit,
                )
                items = list(search.items())
                log.info(
                    "STAC %s: найдено %d сцен для %s (%s..%s)",
                    collection, len(items), aoi.name, start, end,
                )
                return items
            except Exception as exc:  # сетевые ошибки pystac-client не типизирует
                last_error = exc
                # Клиент мог остаться в нерабочем состоянии — пересоздаём.
                self._client = None
                if attempt < attempts:
                    delay = RETRY_BACKOFF_S * attempt
                    log.warning(
                        "STAC %s: попытка %d из %d не удалась (%s), повтор через %.0f с",
                        collection, attempt, attempts, exc, delay,
                    )
                    time.sleep(delay)
        raise RuntimeError(f"STAC {collection}: не удалось получить сцены") from last_error

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
        items = self.search_items(
            collection=collection, aoi=aoi, start=start, end=end, query=query, limit=limit
        )
        return [_to_scene_ref(item) for item in items]

    #: Элементы, у которых метаданные есть, а ассеты отдают 404. Проверка
    #: делается один раз за процесс: сцены повторяются от плитки к плитке,
    #: и перепроверять их двадцать пять раз незачем.
    #:
    #: Рядом — множество уже проверенных и годных. Без него запоминались
    #: только битые, а годные переспрашивались на каждой плитке: девятнадцать
    #: свежих сцен по семь каналов, сто тридцать открытий по сети, и так
    #: двадцать пять раз подряд.
    _broken_items: ClassVar[set[str]] = set()
    _checked_items: ClassVar[set[str]] = set()

    #: Насколько свежие сцены проверять. Ассеты не доезжают до хранилища
    #: только у недавно принятых элементов; у снимка годичной давности
    #: такого не бывает, и опрашивать восемьсот сцен ради двадцати
    #: подозрительных — впустую потраченные минуты.
    FRESH_DAYS: ClassVar[int] = 60

    def _drop_broken(self, items: list, settings: Settings) -> list:
        """Убрать элементы, чьи ассеты недоступны.

        ── Зачем ───────────────────────────────────────────────────────

        В каталоге встречаются свежепринятые элементы с полными
        метаданными и отсутствующими файлами. ``fail_on_error=False`` в
        odc-stac от них не спасает: он перехватывает ошибки rasterio, а
        здесь до rasterio дело не доходит. GDAL, не найдя файл, зовёт
        системный форматтер сообщений, тот отдаёт строку в кодировке
        Windows, и Python роняет UnicodeDecodeError при попытке прочитать
        её как UTF-8. Это не ошибка ввода-вывода ни для одного
        перехватчика, и она проходит насквозь до самого верха.

        Стоило это целого прогона: одна сцена от 12 июля 2026 года роняла
        КАЖДУЮ из двадцати пяти плиток, обе попытки подряд — повтор
        запрашивал ту же сцену.

        Проверка идёт запросом Range на первый байт: он дешевле HEAD у
        хранилищ, где HEAD не подписан, и точнее — отвечает ровно тот
        путь, который потом откроет GDAL.
        """
        import rasterio

        fresh_after = datetime.now(timezone.utc) - timedelta(days=self.FRESH_DAYS)
        suspect = [
            item
            for item in items
            if item.id not in self._broken_items
            and item.id not in self._checked_items
            and item.datetime is not None
            and item.datetime >= fresh_after
        ]
        if not suspect:
            return items

        wanted = [*settings.sentinel2.bands]
        # Проверка идёт СРЕДСТВАМИ GDAL, а не requests.
        #
        # Первая версия опрашивала ссылки через requests и не находила ничего
        # битого — а загрузчик на тех же файлах падал. Оказалось, requests их
        # скачивает, а GDAL получает «HTTP error code: 0», то есть соединение
        # не состоялось вовсе. Что бы ни было причиной — TLS, DNS или
        # недоступный узел хранилища, — единственный способ узнать, прочитает
        # ли файл загрузчик, это попробовать прочитать его так же.
        #
        # Здесь же перехватывается UnicodeDecodeError: в нашем коде он
        # ловится обычным except, в отличие от глубины стека odc-stac.
        with rasterio.Env(GDAL_HTTP_MAX_RETRY=0, GDAL_HTTP_TIMEOUT=15):
            for item in suspect:
                for name in wanted:
                    asset = item.assets.get(name)
                    if asset is None:
                        self._broken_items.add(item.id)
                        break
                    try:
                        with rasterio.open(asset.href) as src:
                            _ = src.width
                    except Exception:  # важен факт отказа, а не его вид
                        self._broken_items.add(item.id)
                        break
                else:
                    # Все каналы открылись — больше эту сцену не трогаем.
                    self._checked_items.add(item.id)

        if self._broken_items:
            kept = [item for item in items if item.id not in self._broken_items]
            if len(kept) != len(items):
                log.warning(
                    "Пропущено сцен с недоступными файлами: %d из %d",
                    len(items) - len(kept), len(items),
                )
            return kept
        return items

    def sentinel2_items(self, aoi: AOI, settings: Settings) -> list:
        """``pystac.Item`` Sentinel-2 с теми же фильтрами, что и :meth:`search_sentinel2`.

        Фильтр по месяцам применяется здесь же: зимние снимки бесполезны
        для спектральной ветки, а каждый лишний снимок в кубе — это
        десятки HTTP-запросов при загрузке.
        """
        items = self.search_items(
            collection=settings.sentinel2.collection,
            aoi=aoi,
            start=settings.time.start,
            end=settings.time.end,
            query={"eo:cloud_cover": {"lt": settings.sentinel2.max_scene_cloud_pct}},
        )
        allowed = set(settings.time.valid_months)
        items = [item for item in items if _item_month(item) in allowed]
        return self._drop_broken(items, settings)

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


def _item_month(item) -> int:
    """Месяц съёмки STAC-элемента."""
    stamp = item.properties.get("datetime") or item.properties.get("start_datetime") or ""
    return date.fromisoformat(stamp[:10]).month


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
    "EARTH_SEARCH_STAC",
    "PLANETARY_COMPUTER_STAC",
    "RETRY_BACKOFF_S",
    "SceneRef",
    "StacCatalog",
    "filter_by_month",
    "summarize",
]
