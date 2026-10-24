"""Генератор демонстрационных артефактов.

Зачем это нужно
---------------
Фронтенд, сервис и бот надо разрабатывать и репетировать до того, как
полный прогон по области закончится. Полный прогон идёт часами и требует
сети; репетиция выступления — не требует ничего.

Честность
---------
Каждый сгенерированный артефакт помечен полем ``is_demo: true``, а карта
показывает предупреждающую полосу, когда видит эту метку. Демонстрация
синтетических данных без явной пометки — это то, за что дисквалифицируют,
и никакой пользы от неё нет: жюри всё равно спросит, откуда цифры.

Использовать эти данные можно только для отладки интерфейса и прогона
сценария. На защите показываются результаты настоящего прогона.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from .aoi import AOI
from .config import Economics, Settings, load_economics, load_settings

log = logging.getLogger(__name__)

DEMO_MARKER = {"is_demo": True, "warning": "СИНТЕТИЧЕСКИЕ ДАННЫЕ ДЛЯ ОТЛАДКИ ИНТЕРФЕЙСА"}


def generate_candidates(
    aoi: AOI,
    economics: Economics,
    *,
    n: int = 24,
    seed: int = 42,
):
    """Синтетические объекты, правдоподобные по структуре, но выдуманные."""
    import geopandas as gpd
    from shapely.geometry import Polygon

    from .money import assess

    rng = np.random.default_rng(seed)
    geom = aoi.to_working()
    min_x, min_y, max_x, max_y = geom.bounds

    records = []
    for i in range(n):
        # Объекты кучкуются вдоль условной дороги — как в реальности
        cx = rng.uniform(min_x + 2000, max_x - 2000)
        cy = rng.uniform(min_y + 2000, max_y - 2000)
        side = float(rng.uniform(40, 220))

        # Неправильный многоугольник: свалка не бывает прямоугольной
        angles = np.sort(rng.uniform(0, 2 * np.pi, 7))
        radii = side / 2 * rng.uniform(0.6, 1.4, 7)
        points = [(cx + r * np.cos(a), cy + r * np.sin(a)) for a, r in zip(angles, radii, strict=True)]
        polygon = Polygon(points).buffer(0)

        appeared = date(2019, 1, 1) + timedelta(days=int(rng.integers(0, 2200)))
        probability = float(np.clip(rng.beta(5, 2), 0.35, 0.98))
        area = float(polygon.area)
        assessment = assess(area, economics, iterations=4000, seed=seed + i)

        records.append(
            {
                "candidate_id": f"D{i:05d}",
                "geometry": polygon,
                "area_m2": area,
                "break_date": np.datetime64(appeared.isoformat()),
                "probability": probability,
                "ndvi_drop": float(np.clip(rng.normal(0.27, 0.06), 0.12, 0.55)),
                "bsi_rise": float(np.clip(rng.normal(0.17, 0.05), 0.06, 0.40)),
                "pmli_response": float(np.clip(rng.normal(0.08, 0.04), 0.0, 0.20)),
                "sar_incoherence": float(np.clip(rng.normal(0.35, 0.15), 0.0, 0.90)),
                "thermal_anomaly": float(np.clip(rng.normal(1.8, 1.0), -1.0, 4.5)),
                "verify_providers": int(rng.integers(0, 4)),
                "verify_texture": float(np.clip(rng.normal(0.55, 0.18), 0.0, 1.0)),
                "damage_p10": assessment.net_damage_kzt.p10,
                "damage_p50": assessment.net_damage_kzt.p50,
                "damage_p90": assessment.net_damage_kzt.p90,
                "mass_t": assessment.mass_t.p50,
                "co2e_t": assessment.co2e_t.p50,
                "penalty_kzt": assessment.penalty_kzt,
                "penalty_article": assessment.penalty_article,
                "is_demo": True,
            }
        )

    gdf = gpd.GeoDataFrame(records, geometry="geometry", crs=aoi.crs_working)
    return gdf.sort_values("probability", ascending=False).reset_index(drop=True)


def generate_risk(aoi: AOI, settings: Settings, candidates, *, seed: int = 42):
    """Синтетические сетки риска: точная и публичная."""
    import geopandas as gpd

    from .risk import aggregate_public, build_grid

    rng = np.random.default_rng(seed)
    grid = build_grid(aoi, settings.risk.grid_cell_m)

    # Риск выше рядом с существующими объектами — так и должно быть
    centroids = candidates.geometry.centroid
    cell_centres = grid.geometry.centroid
    risk = np.zeros(len(grid))
    for point in centroids:
        distance = np.hypot(cell_centres.x - point.x, cell_centres.y - point.y)
        risk += np.exp(-((distance / 4000.0) ** 2))
    risk = risk / max(risk.max(), 1e-9)
    risk = np.clip(risk * 0.8 + rng.uniform(0, 0.2, len(grid)), 0, 1)

    private = grid.copy()
    private["risk"] = risk
    private["risk_rank"] = private["risk"].rank(ascending=False, method="min").astype(int)
    private["is_demo"] = True

    public = aggregate_public(private, settings.risk)
    public["is_demo"] = True
    return private, gpd.GeoDataFrame(public, crs=private.crs)


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


def build_story(candidates, *, top_n: int = 3, registry_count: int = 0) -> dict:
    """Сценарий демонстрации: последовательность сцен вместо свободной карты.

    Улучшение 10 из версии 3.0. Смысл в том, что под стрессом на сцене
    выступающий забывает, куда кликать, и демонстрация разваливается.
    Сценарий фиксирует порядок и реплики заранее.
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

    return {
        **DEMO_MARKER,
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


def generate_all(
    outputs: str | Path | None = None,
    *,
    settings: Settings | None = None,
    economics: Economics | None = None,
    n: int = 24,
    seed: int = 42,
) -> dict[str, Path]:
    """Сгенерировать полный набор демонстрационных артефактов."""
    settings = settings or load_settings()
    economics = economics or load_economics()
    aoi = AOI.from_settings(settings)
    target = Path(outputs) if outputs else settings.paths.resolve("outputs")
    target.mkdir(parents=True, exist_ok=True)

    candidates = generate_candidates(aoi, economics, n=n, seed=seed)
    private, public = generate_risk(aoi, settings, candidates, seed=seed)
    registry = fetch_official_registry(aoi, settings)
    story = build_story(candidates, registry_count=len(registry))

    from .candidates import simplify_for_web, to_geojson
    from .risk import dissolve_public

    written: dict[str, Path] = {}

    to_geojson(candidates, target / "candidates.geojson", crs_output=settings.project.crs_output)
    written["candidates"] = target / "candidates.geojson"

    for name, layer in (("risk_private", private), ("risk_public", public)):
        path = target / f"{name}.geojson"
        export = layer.to_crs(settings.project.crs_output)
        if name == "risk_public":
            # Растворяем смежные ячейки одного класса: зона риска — это
            # область, а не мозаика квадратов, и файл при этом падает
            # с сотен килобайт до десятков.
            export = dissolve_public(export)
        export = simplify_for_web(export)
        export.to_file(path, driver="GeoJSON")
        written[name] = path

    if not registry.empty:
        registry_path = target / "registry.geojson"
        simplify_for_web(registry.to_crs(settings.project.crs_output)).to_file(
            registry_path, driver="GeoJSON"
        )
        written["registry"] = registry_path

    story_path = target / "story.json"
    story_path.write_text(json.dumps(story, ensure_ascii=False, indent=2), encoding="utf-8")
    written["story"] = story_path

    log.warning(
        "Сгенерированы СИНТЕТИЧЕСКИЕ данные для отладки интерфейса (%d объектов). "
        "На защите показывайте результат настоящего прогона.",
        len(candidates),
    )
    return written


__all__ = ["DEMO_MARKER", "build_story", "generate_all", "generate_candidates", "generate_risk"]
