"""Загрузка и валидация конфигурации.

Принцип: в коде нет магических чисел. Любой порог, окно, тариф или список
классов живёт в YAML и попадает сюда через типизированную модель. Это даёт
две вещи: (1) на Q&A видно, что параметры осознанные и имеют источник,
(2) эксперимент = правка YAML, а не правка кода.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# Корень репозитория: src/vantage/config.py -> src/vantage -> src -> <repo>
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


# --------------------------------------------------------------------------- #
#  Секции default.yaml
# --------------------------------------------------------------------------- #


class ProjectCfg(BaseModel):
    name: str
    crs_working: str
    crs_output: str


class AoiCfg(BaseModel):
    name: str
    bbox: tuple[float, float, float, float]
    geojson: str | None = None

    @field_validator("bbox")
    @classmethod
    def _check_bbox(cls, v: tuple[float, float, float, float]):
        min_lon, min_lat, max_lon, max_lat = v
        if not (min_lon < max_lon and min_lat < max_lat):
            raise ValueError(f"bbox должен быть [min_lon, min_lat, max_lon, max_lat], получено {v}")
        if not (-180 <= min_lon <= 180 and -90 <= min_lat <= 90):
            raise ValueError(f"bbox вне допустимых координат: {v}")
        return v


class TimeCfg(BaseModel):
    start: str
    end: str
    composite_freq: str
    valid_months: list[int]

    @field_validator("valid_months")
    @classmethod
    def _check_months(cls, v: list[int]):
        if not v or any(m < 1 or m > 12 for m in v):
            raise ValueError("valid_months должен содержать числа 1..12")
        return v

    @model_validator(mode="after")
    def _check_order(self):
        if self.start >= self.end:
            raise ValueError(f"time.start ({self.start}) должен быть раньше time.end ({self.end})")
        return self


class Sentinel2Cfg(BaseModel):
    collection: str
    bands: list[str]
    resolution_m: int
    max_scene_cloud_pct: float
    scl_mask_classes: list[int]
    min_valid_fraction: float = Field(ge=0.0, le=1.0)


class Sentinel1Cfg(BaseModel):
    collection: str
    polarizations: list[str]
    resolution_m: int
    coherence_window_months: int


class LandsatCfg(BaseModel):
    collection: str
    thermal_asset: str
    resolution_m: int
    background_radius_m: int


class ChangeCfg(BaseModel):
    min_segment_months: int
    min_ndvi_drop: float
    min_bsi_rise: float
    recovery_tolerance: float
    recovery_window_months: int
    breakpoint_zscore: float


class ContextCfg(BaseModel):
    max_distance_to_road_m: float
    min_distance_to_settlement_m: float
    max_distance_to_settlement_m: float
    exclude_landuse: list[str]
    exclude_natural: list[str]
    min_area_m2: float
    max_area_m2: float

    @model_validator(mode="after")
    def _check_ring(self):
        if self.min_distance_to_settlement_m >= self.max_distance_to_settlement_m:
            raise ValueError("кольцо расстояний до населённого пункта задано некорректно")
        if self.min_area_m2 >= self.max_area_m2:
            raise ValueError("min_area_m2 должен быть меньше max_area_m2")
        return self


class CandidatesCfg(BaseModel):
    opening_iterations: int
    closing_iterations: int
    simplify_tolerance_m: float


class ChipsCfg(BaseModel):
    size_px: int
    bands: list[str]
    derived: list[str]

    @property
    def n_channels(self) -> int:
        """Число каналов чипа для одной эпохи (до / после)."""
        return len(self.bands) + len(self.derived)


class ModelCfg(BaseModel):
    backbone: str
    pretrained_source: str
    embedding_dim: int
    dropout: float
    batch_size: int
    epochs: int
    lr: float
    weight_decay: float
    early_stopping_patience: int
    decision_threshold: float = Field(ge=0.0, le=1.0)
    val_fraction: float = Field(gt=0.0, lt=1.0)
    seed: int


class VerifyCfg(BaseModel):
    providers: list[str]
    zoom: int
    tile_grid: int
    timeout_s: int
    max_candidates: int
    min_agreeing_providers: int


class RemovalCfg(BaseModel):
    min_agreeing_signals: int
    consecutive_clear_passes: int
    ndvi_recovery_threshold: float
    bsi_still_high_threshold: float


class RiskCfg(BaseModel):
    grid_cell_m: float
    horizon_months: int
    model: str
    n_estimators: int
    learning_rate: float
    max_depth: int
    seed: int
    public_grid_cell_m: float


class ApiCfg(BaseModel):
    host: str
    port: int
    roles: list[str]
    public_precision_digits: int


class PathsCfg(BaseModel):
    data_raw: str
    data_interim: str
    data_cache: str
    chips: str
    models: str
    outputs: str

    def resolve(self, key: str) -> Path:
        """Абсолютный путь к каталогу; каталог создаётся при первом обращении."""
        rel = getattr(self, key)
        path = REPO_ROOT / rel
        path.mkdir(parents=True, exist_ok=True)
        return path


class Settings(BaseModel):
    """Полная конфигурация пайплайна (config/default.yaml)."""

    project: ProjectCfg
    aoi: AoiCfg
    time: TimeCfg
    sentinel2: Sentinel2Cfg
    sentinel1: Sentinel1Cfg
    landsat: LandsatCfg
    change: ChangeCfg
    context: ContextCfg
    candidates: CandidatesCfg
    chips: ChipsCfg
    model: ModelCfg
    verify: VerifyCfg
    removal: RemovalCfg
    risk: RiskCfg
    api: ApiCfg
    paths: PathsCfg


# --------------------------------------------------------------------------- #
#  Экономика: треугольные распределения вместо точечных цифр
# --------------------------------------------------------------------------- #


class Triangular(BaseModel):
    """Треугольное распределение допущения: min / typical / max.

    Денежный слой работает диапазонами, а не одной цифрой (улучшение 7).
    Треугольное распределение выбрано потому, что оно требует ровно тех трёх
    чисел, которые эксперт способен назвать: минимум, наиболее вероятное,
    максимум — и не требует придумывать дисперсию.
    """

    min: float
    typical: float
    max: float

    @model_validator(mode="after")
    def _check_order(self):
        if not (self.min <= self.typical <= self.max):
            raise ValueError(f"нарушен порядок min<=typical<=max: {self.min}/{self.typical}/{self.max}")
        return self

    def sample(self, rng, size: int):
        """Выборка из треугольного распределения (numpy Generator)."""
        # numpy требует left < right; при вырожденном допущении возвращаем константу
        if self.min == self.max:
            import numpy as np

            return np.full(size, self.typical, dtype=float)
        return rng.triangular(self.min, self.typical, self.max, size)


class Economics(BaseModel):
    """config/economics_astana.yaml — допущения денежного слоя.

    Хранится как «сырой» словарь + типизированные аксессоры: структура файла
    намеренно гибкая (морфология и цены пополняются по мере сбора реальных
    прайсов), а жёсткая схема тут мешала бы больше, чем помогала.
    """

    currency: str
    raw: dict[str, Any]

    def triangular(self, *keys: str) -> Triangular:
        """Достать треугольное допущение по пути ключей.

        Пример: ``econ.triangular("recyclable_price_kzt_per_kg", "metal")``
        """
        node: Any = self.raw
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                raise KeyError(f"нет параметра {'.'.join(keys)} в economics.yaml")
            node = node[k]
        if not isinstance(node, dict):
            raise TypeError(f"{'.'.join(keys)} не является распределением min/typical/max")
        return Triangular(**{k: node[k] for k in ("min", "typical", "max")})

    def scalar(self, *keys: str) -> float:
        node: Any = self.raw
        for k in keys:
            node = node[k]
        return float(node)

    def section(self, key: str) -> dict[str, Any]:
        return dict(self.raw[key])

    def unresolved_sources(self) -> list[str]:
        """Пути к параметрам, у которых source всё ещё TODO.

        Используется в CLI-команде ``vantage doctor``: перед сдачей ни один
        денежный параметр не должен остаться без ссылки на источник.
        """
        todos: list[str] = []

        def walk(node: Any, path: list[str]) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    if k == "source" and isinstance(v, str) and v.strip().startswith("TODO"):
                        todos.append(".".join(path) or "<root>")
                    else:
                        walk(v, [*path, k])

        walk(self.raw, [])
        return todos


# --------------------------------------------------------------------------- #
#  Загрузка
# --------------------------------------------------------------------------- #


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"не найден файл конфигурации: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} должен содержать YAML-словарь верхнего уровня")
    return data


@lru_cache(maxsize=8)
def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    """Загрузить основную конфигурацию.

    Путь можно переопределить аргументом или переменной окружения VANTAGE_CONFIG.
    """
    cfg_path = Path(path or os.environ.get("VANTAGE_CONFIG") or CONFIG_DIR / "default.yaml")
    return Settings(**_read_yaml(cfg_path))


@lru_cache(maxsize=8)
def load_economics(path: str | os.PathLike[str] | None = None) -> Economics:
    """Загрузить экономические допущения."""
    econ_path = Path(
        path or os.environ.get("VANTAGE_ECONOMICS") or CONFIG_DIR / "economics_astana.yaml"
    )
    raw = _read_yaml(econ_path)
    return Economics(currency=raw.get("currency", "KZT"), raw=raw)


Role = Literal["public", "operator", "akimat", "admin"]

__all__ = [
    "REPO_ROOT",
    "CONFIG_DIR",
    "Settings",
    "Economics",
    "Triangular",
    "Role",
    "load_settings",
    "load_economics",
]
