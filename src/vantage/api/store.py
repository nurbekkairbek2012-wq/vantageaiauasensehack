"""Хранилище результатов пайплайна для сервиса.

Сервис ничего не считает. Он отдаёт то, что уже посчитано и сохранено
на диск. Это принципиально: на защите нельзя допустить, чтобы нажатие
кнопки запускало обучение модели или обращение к спутниковому каталогу.
Всё предрассчитано, API только читает.

Хранилище держит данные в памяти и умеет перечитывать их с диска.
База данных здесь была бы честным оверинжинирингом: объектов десятки,
а не миллионы, и запись идёт раз в сутки пакетом, а не построчно.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Store:
    """Результаты пайплайна, доступные сервису."""

    candidates: Any = None       # GeoDataFrame подтверждённых объектов
    risk_public: Any = None      # агрегированная сетка риска (публичная)
    risk_private: Any = None     # точная сетка риска (для службы)
    acts: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #

    @property
    def n_candidates(self) -> int:
        return 0 if self.candidates is None else len(self.candidates)

    def is_empty(self) -> bool:
        return self.n_candidates == 0

    def get_candidate(self, candidate_id: str):
        """Найти кандидата по идентификатору или вернуть None."""
        if self.candidates is None or "candidate_id" not in self.candidates.columns:
            return None
        match = self.candidates[self.candidates["candidate_id"] == candidate_id]
        return None if match.empty else match.iloc[0]

    def summary(self) -> dict[str, Any]:
        """Сводка без адресных данных — годится для публичной выдачи."""
        if self.candidates is None or self.candidates.empty:
            return {"objects": 0}

        columns = self.candidates.columns
        result: dict[str, Any] = {"objects": len(self.candidates)}
        if "area_m2" in columns:
            result["total_area_ha"] = round(float(self.candidates["area_m2"].sum()) / 10_000, 1)
        if "damage_p50" in columns:
            result["total_damage_p50_kzt"] = float(self.candidates["damage_p50"].sum())
        if "co2e_t" in columns:
            result["total_co2e_t"] = round(float(self.candidates["co2e_t"].sum()), 1)
        if "break_date" in columns:
            dates = self.candidates["break_date"].dropna()
            if len(dates):
                result["first_appeared"] = str(dates.min())[:10]
                result["last_appeared"] = str(dates.max())[:10]
        return result

    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls, directory: str | Path) -> Store:
        """Прочитать артефакты пайплайна из каталога.

        Отсутствующий файл — не ошибка: слои считаются независимо, и
        сервис должен подниматься даже когда готова только часть.
        """
        import geopandas as gpd

        directory = Path(directory)
        store = cls()

        def read(name: str):
            path = directory / name
            if not path.exists():
                log.info("Артефакт не найден, пропускаем: %s", path.name)
                return None
            try:
                return gpd.read_file(path)
            except Exception as exc:
                log.error("Не удалось прочитать %s: %s", path.name, exc)
                return None

        store.candidates = read("candidates.geojson")
        store.risk_public = read("risk_public.geojson")
        store.risk_private = read("risk_private.geojson")
        store.meta = {"source": str(directory), "loaded": store.n_candidates}
        log.info("Хранилище загружено из %s: %d объектов", directory, store.n_candidates)
        return store


def candidate_to_record(row, *, include_geometry: bool = False) -> dict[str, Any]:
    """Превратить строку GeoDataFrame в словарь для ответа API.

    Координаты берутся из геометрии — в таблице их может не быть, а
    рабочая проекция метрическая и человеку бесполезна.
    """
    import math

    def clean(value):
        if value is None:
            return None
        if isinstance(value, float) and math.isnan(value):
            return None
        return value

    point = row.geometry.representative_point()
    record: dict[str, Any] = {
        "candidate_id": clean(row.get("candidate_id")),
        "latitude": float(point.y),
        "longitude": float(point.x),
        "area_m2": clean(_float(row.get("area_m2"))),
        "break_date": _date_str(row.get("break_date")),
        "probability": clean(_float(row.get("probability"))),
        "ndvi_drop": clean(_float(row.get("ndvi_drop"))),
        "bsi_rise": clean(_float(row.get("bsi_rise"))),
        "verify_providers": int(row.get("verify_providers") or 0),
        "damage_p10": clean(_float(row.get("damage_p10"))),
        "damage_p50": clean(_float(row.get("damage_p50"))),
        "damage_p90": clean(_float(row.get("damage_p90"))),
    }
    if include_geometry:
        record["geometry"] = row.geometry.__geo_interface__
    return {k: v for k, v in record.items() if v is not None}


def _float(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _date_str(value) -> str | None:
    if value is None:
        return None
    text = str(value)
    return None if text in {"NaT", "None", "nan"} else text[:10]


__all__ = ["Store", "candidate_to_record"]
