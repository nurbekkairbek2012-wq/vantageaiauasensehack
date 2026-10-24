"""Сценарий демонстрации и публичный реестр известных объектов.

Модуль отделён от :mod:`vantage.demo` намеренно: там лежит генератор
синтетики, и всё, что оттуда импортируется, обоснованно вызывает подозрение.
Сценарий же строится и по настоящему прогону — разница ровно в одном поле
``is_demo``, от которого зависит предупреждающая полоса на карте.
"""

from __future__ import annotations

import logging

from .aoi import AOI
from .config import Settings
from .explain import SIGNAL_FULL_SCALE

log = logging.getLogger(__name__)

#: Метка синтетических артефактов. Её видит карта и команда ``vantage publish``:
#: опубликовать помеченные данные можно только с явным флагом.
DEMO_MARKER = {"is_demo": True, "warning": "СИНТЕТИЧЕСКИЕ ДАННЫЕ ДЛЯ ОТЛАДКИ ИНТЕРФЕЙСА"}


def fetch_official_registry(aoi: AOI, settings: Settings, *, use_cache: bool = True):
    """Настоящий официальный реестр: полигоны ТБО из OpenStreetMap.

    Первая сцена демонстрации строится на противопоставлении «что знает
    государство» и «что есть на самом деле». Если слева показывать не
    настоящий реестр, а несколько собственных находок, перекрашенных
    в синий, то на вопрос «откуда данные реестра» ответить нечем —
    и вся сцена рассыпается.

    OSM — не государственный реестр, и это надо говорить прямо. Но это
    открытые данные о том, какие объекты обращения с отходами известны
    публично, и как нижняя оценка «что знают» они работают честно.
    """
    import geopandas as gpd

    from .labels import POSITIVE_TAGS, fetch_reference_objects

    try:
        registry = fetch_reference_objects(aoi, settings, POSITIVE_TAGS, use_cache=use_cache)
    except Exception as exc:
        log.warning("Не удалось загрузить реестр из OSM: %s", exc)
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=aoi.crs_working)

    if registry.empty:
        log.warning("В OSM не нашлось объектов обращения с отходами по этой области")
        return registry

    keep = [c for c in ("name", "landuse", "amenity", "man_made", "geometry") if c in registry.columns]
    registry = registry[keep].copy()
    registry["source"] = "OpenStreetMap"
    registry["area_m2"] = registry.geometry.area
    log.info("Официальный реестр из OSM: %d объектов", len(registry))
    return registry


def build_story(
    candidates,
    *,
    top_n: int = 3,
    registry_count: int = 0,
    is_demo: bool = False,
) -> dict:
    """Сценарий демонстрации: последовательность сцен вместо свободной карты.

    Смысл в том, что под стрессом на сцене выступающий забывает, куда
    кликать, и демонстрация разваливается. Сценарий фиксирует порядок и
    реплики заранее.

    ``is_demo`` определяет, покажет ли карта красную полосу
    «синтетические данные». Раньше метка проставлялась безусловно, и
    результат настоящего прогона всё равно выходил помеченным как
    отладочный — то есть показать его было нельзя.
    """
    top = candidates.head(top_n)
    focus = []
    for _, row in top.to_crs("EPSG:4326").iterrows():
        point = row.geometry.representative_point()
        focus.append(
            {
                "candidate_id": row["candidate_id"],
                "center": [float(point.x), float(point.y)],
                "zoom": 14,
            }
        )

    total_damage = float(candidates["damage_p50"].sum()) if "damage_p50" in candidates else 0.0
    total_low = float(candidates["damage_p10"].sum()) if "damage_p10" in candidates else 0.0
    total_high = float(candidates["damage_p90"].sum()) if "damage_p90" in candidates else 0.0

    marker = DEMO_MARKER if is_demo else {"is_demo": False}

    return {
        **marker,
        # Шкалы нормировки признаков едут вместе с данными. Иначе карта
        # хранит их своей копией, и рассинхрон замечают только на защите:
        # ровно так шкала радара разошлась с sar.py в шесть раз.
        "signal_scales": dict(SIGNAL_FULL_SCALE),
        "scenes": [
            {
                "id": "registry",
                "title": "Что известно публично",
                "line": (
                    f"Объектов обращения с отходами в открытых данных: {registry_count}."
                    if registry_count
                    else "Открытые данные знают о единицах объектов."
                ),
                "layers": ["registry"],
            },
            {
                "id": "found",
                "title": "Что есть на самом деле",
                "line": f"Мы нашли {len(candidates)} объектов.",
                "layers": ["registry", "candidates"],
            },
            {
                "id": "evidence",
                "title": "Доказательная цепочка",
                "line": "Она появилась не вчера. Вот пять независимых признаков.",
                "layers": ["candidates"],
                "focus": focus[0] if focus else None,
                "panel": "evidence",
            },
            {
                "id": "money",
                "title": "Оценка ущерба",
                "line": (
                    f"{len(candidates)} объектов = "
                    f"{total_low / 1e6:.0f}–{total_high / 1e6:.0f} млн ₸ ущерба."
                ),
                "layers": ["candidates"],
                "panel": "money",
            },
            {
                "id": "act",
                "title": "Акт",
                "line": "Автомат предлагает, человек подтверждает.",
                "layers": ["candidates"],
                "panel": "act",
            },
            {
                "id": "risk",
                "title": "Прогноз",
                "line": "А здесь свалки ещё нет. Она появится здесь.",
                "layers": ["risk"],
            },
            {
                "id": "mistake",
                "title": "Где мы ошиблись",
                "line": "Мы сами знаем границы своей модели.",
                "layers": ["candidates", "rejected"],
                "panel": "mistakes",
            },
        ],
        "totals": {
            "registry_known": int(registry_count),
            "objects": len(candidates),
            "damage_p10": total_low,
            "damage_p50": total_damage,
            "damage_p90": total_high,
            "area_ha": float(candidates["area_m2"].sum()) / 10_000 if "area_m2" in candidates else 0.0,
            "co2e_t": float(candidates["co2e_t"].sum()) if "co2e_t" in candidates else 0.0,
        },
    }


__all__ = ["DEMO_MARKER", "build_story", "fetch_official_registry"]
