"""Сквозной прогон: от STAC до артефактов на диске.

Каждый шаг пишет свой результат в ``outputs/`` и умеет пропускаться,
если результат уже есть. Это не оптимизация, а требование к работе
за неделю: полный прогон по области идёт часами, и падение на шаге 7
не должно означать повтор шагов 1-6.

Артефакты на выходе — то, что читает сервис и фронтенд:

    candidates.geojson    объекты с атрибутами, деньгами и доказательствами
    risk_private.geojson  точная сетка риска
    risk_public.geojson   агрегированные зоны для публичной карты
    story.json            сценарий демонстрации
    run.json              метаданные прогона: что, когда, сколько

Порядок шагов и их зависимости зафиксированы в PIPELINE_STEPS.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .aoi import AOI
from .config import Economics, Settings, load_economics, load_settings

log = logging.getLogger(__name__)

#: Шаги пайплайна в порядке выполнения. Имя шага = имя артефакта.
PIPELINE_STEPS = (
    "scenes",
    "features",
    "change",
    "candidates",
    "context",
    "money",
    "risk",
    "export",
)


@dataclass
class RunReport:
    """Что произошло за прогон — идёт в run.json и в вывод CLI."""

    started_at: str
    aoi_name: str
    aoi_area_km2: float
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    finished_at: str | None = None

    def record(self, step: str, *, seconds: float, **details: Any) -> None:
        self.steps[step] = {"seconds": round(seconds, 1), **details}
        log.info("Шаг %s завершён за %.1f с: %s", step, seconds, details)

    def as_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "aoi": {"name": self.aoi_name, "area_km2": round(self.aoi_area_km2, 1)},
            "steps": self.steps,
            "artifacts": self.artifacts,
        }


class Pipeline:
    """Оркестратор прогона.

    Хранит настройки и каталог артефактов; каждый шаг — отдельный метод,
    который можно вызвать независимо. Это важно и для отладки, и для
    защиты: на вопрос «покажите промежуточный результат» должен быть
    файл, а не пересказ.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        economics: Economics | None = None,
        *,
        outputs: Path | None = None,
        force: bool = False,
    ) -> None:
        self.settings = settings or load_settings()
        self.economics = economics or load_economics()
        self.outputs = Path(outputs) if outputs else self.settings.paths.resolve("outputs")
        self.outputs.mkdir(parents=True, exist_ok=True)
        self.force = force
        self.aoi = AOI.from_settings(self.settings)
        self.report = RunReport(
            started_at=datetime.now().isoformat(timespec="seconds"),
            aoi_name=self.aoi.name,
            aoi_area_km2=self.aoi.area_km2,
        )

    # ------------------------------------------------------------------ #

    def path(self, name: str) -> Path:
        return self.outputs / name

    def exists(self, name: str) -> bool:
        return not self.force and self.path(name).exists()

    # ------------------------------------------------------------------ #
    #  Шаги
    # ------------------------------------------------------------------ #

    def step_scenes(self) -> dict:
        """Найти доступные сцены. Первый шаг: если сцен мало, дальше нет смысла."""
        from .catalog import StacCatalog, summarize

        target = self.path("scenes.json")
        if self.exists("scenes.json"):
            return json.loads(target.read_text(encoding="utf-8"))

        catalog = StacCatalog()
        s2 = catalog.search_sentinel2(self.aoi, self.settings)
        stats = {"sentinel2": summarize(s2)}
        target.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        self.report.artifacts["scenes"] = str(target)
        return stats

    def step_change(self, ndvi: np.ndarray, bsi: np.ndarray, dates: np.ndarray):
        """Детекция необратимых изменений по временным рядам."""
        from .change import detect

        return detect(ndvi, bsi, dates, self.settings.change)

    def step_candidates(self, result, grid, dates: np.ndarray):
        """Векторизация в полигоны с атрибутами."""
        from .candidates import build_candidates

        return build_candidates(result, grid, self.settings, dates=dates)

    def step_context(self, candidates, *, use_cache: bool = True):
        """Контекстный отсев по OpenStreetMap."""
        from .context import apply_context_filter, fetch_context, rejection_report

        layers = fetch_context(self.aoi, self.settings, use_cache=use_cache)
        filtered = apply_context_filter(candidates, layers, self.settings.context)
        return filtered, rejection_report(filtered), layers

    def step_money(self, candidates):
        """Денежный слой по каждому объекту.

        Монте-Карло на объект стоит десятые доли секунды, поэтому по
        десяткам объектов это мгновенно. Если объектов окажутся тысячи,
        число итераций надо снижать — но тогда и интервал станет шире,
        и об этом надо будет сказать честно.
        """
        from .money import assess

        if candidates.empty:
            return candidates

        result = candidates.copy()
        rows = []
        for _, row in result.iterrows():
            area = float(row.get("area_m2", 0.0))
            if area <= 0:
                rows.append({})
                continue
            assessment = assess(area, self.economics)
            rows.append(
                {
                    "damage_p10": assessment.net_damage_kzt.p10,
                    "damage_p50": assessment.net_damage_kzt.p50,
                    "damage_p90": assessment.net_damage_kzt.p90,
                    "mass_t": assessment.mass_t.p50,
                    "co2e_t": assessment.co2e_t.p50,
                    "penalty_kzt": assessment.penalty_kzt,
                    "penalty_article": assessment.penalty_article,
                }
            )
        for key in ("damage_p10", "damage_p50", "damage_p90", "mass_t", "co2e_t",
                    "penalty_kzt", "penalty_article"):
            result[key] = [row.get(key) for row in rows]
        return result

    def step_risk(self, candidates, layers=None):
        """Модель риска и две сетки: точная и публичная."""
        from .risk import (
            aggregate_public,
            build_grid,
            predict_risk,
            spatial_features,
            temporal_labels,
            train_risk_model,
        )

        grid = build_grid(self.aoi, self.settings.risk.grid_cell_m)
        features = spatial_features(
            grid,
            roads=None if layers is None else layers.roads,
            settlements=None if layers is None else layers.settlements,
            existing=candidates,
        )

        # Отсечка: две трети периода на обучение, треть на проверку прогноза
        cutoff = _two_thirds_date(self.settings.time.start, self.settings.time.end)
        y_train, y_future = temporal_labels(grid, candidates, cutoff=cutoff)

        model = train_risk_model(features, y_train, y_future, self.settings.risk, cutoff=cutoff)
        private = predict_risk(model, features)
        public = aggregate_public(private, self.settings.risk)
        return model, private, public

    def step_export(self, candidates, risk_private=None, risk_public=None) -> dict[str, str]:
        """Записать артефакты в WGS84 — их читают карта и сервис."""
        from .candidates import to_geojson

        written: dict[str, str] = {}

        if candidates is not None and not candidates.empty:
            target = self.path("candidates.geojson")
            to_geojson(candidates, target, crs_output=self.settings.project.crs_output)
            written["candidates"] = str(target)

        for name, layer in (("risk_private", risk_private), ("risk_public", risk_public)):
            if layer is None or layer.empty:
                continue
            target = self.path(f"{name}.geojson")
            layer.to_crs(self.settings.project.crs_output).to_file(target, driver="GeoJSON")
            written[name] = str(target)

        self.report.artifacts.update(written)
        return written

    # ------------------------------------------------------------------ #

    def finish(self) -> Path:
        """Записать отчёт о прогоне."""
        self.report.finished_at = datetime.now().isoformat(timespec="seconds")
        target = self.path("run.json")
        target.write_text(
            json.dumps(self.report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return target


def _two_thirds_date(start: str, end: str) -> str:
    """Дата, отсекающая две трети периода.

    Выбор не произвольный: модели риска нужно достаточно истории для
    обучения и достаточно будущего для честной проверки. Две трети на
    восьмилетнем периоде дают почти три года на валидацию.
    """
    from datetime import date

    d0, d1 = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
    return (d0 + (d1 - d0) * 2 // 3).isoformat()


def timed(func, *args, **kwargs) -> tuple[Any, float]:
    """Выполнить и вернуть (результат, секунды)."""
    started = time.perf_counter()
    result = func(*args, **kwargs)
    return result, time.perf_counter() - started


__all__ = ["PIPELINE_STEPS", "Pipeline", "RunReport", "timed"]
