"""Область интереса (Area of Interest) и работа с геометрией.

Единственное место, где живёт знание о том, «где мы ищем». Всё остальное
получает AOI как объект и не занимается разбором bbox/GeoJSON.

Отдельно решается вопрос проекций: STAC и GeoJSON работают в EPSG:4326
(градусы), а любые расстояния, площади и буферы считаются только в метрической
проекции (UTM 42N для Астаны). Смешение этих двух систем — источник самых
неприятных и незаметных ошибок в геоаналитике.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from pyproj import CRS, Transformer
from shapely.geometry import box, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

from .config import REPO_ROOT, Settings

WGS84 = "EPSG:4326"


@dataclass(frozen=True)
class AOI:
    """Область интереса.

    Хранит геометрию в WGS84 (это «истина» для STAC и выгрузки) и умеет
    отдавать её в рабочей метрической проекции для расчётов.
    """

    name: str
    geometry: BaseGeometry  # всегда в WGS84
    crs_working: str

    # ------------------------------------------------------------------ #
    #  Конструкторы
    # ------------------------------------------------------------------ #

    @classmethod
    def from_settings(cls, settings: Settings) -> AOI:
        """Собрать AOI из конфигурации: geojson имеет приоритет над bbox."""
        if settings.aoi.geojson:
            path = Path(settings.aoi.geojson)
            if not path.is_absolute():
                path = REPO_ROOT / path
            return cls.from_geojson(path, name=settings.aoi.name, crs_working=settings.project.crs_working)
        return cls(
            name=settings.aoi.name,
            geometry=box(*settings.aoi.bbox),
            crs_working=settings.project.crs_working,
        )

    @classmethod
    def from_geojson(cls, path: str | Path, *, name: str, crs_working: str) -> AOI:
        """Прочитать AOI из GeoJSON (Feature, FeatureCollection или Geometry)."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"AOI GeoJSON не найден: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))

        if data.get("type") == "FeatureCollection":
            geoms = [shape(f["geometry"]) for f in data["features"] if f.get("geometry")]
            if not geoms:
                raise ValueError(f"{path}: FeatureCollection без геометрий")
            geometry: BaseGeometry = geoms[0] if len(geoms) == 1 else _union_all(geoms)
        elif data.get("type") == "Feature":
            geometry = shape(data["geometry"])
        else:
            geometry = shape(data)

        if not geometry.is_valid:
            geometry = geometry.buffer(0)  # стандартный приём починки самопересечений
        return cls(name=name, geometry=geometry, crs_working=crs_working)

    @classmethod
    def from_bbox(
        cls,
        bbox: tuple[float, float, float, float],
        *,
        name: str = "custom",
        crs_working: str = "EPSG:32642",
    ) -> AOI:
        return cls(name=name, geometry=box(*bbox), crs_working=crs_working)

    # ------------------------------------------------------------------ #
    #  Представления
    # ------------------------------------------------------------------ #

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """bbox в WGS84: (min_lon, min_lat, max_lon, max_lat)."""
        return tuple(self.geometry.bounds)  # type: ignore[return-value]

    @property
    def geo_interface(self) -> dict:
        """GeoJSON-геометрия — то, что принимает STAC-поиск (`intersects`)."""
        return mapping(self.geometry)

    @property
    def centroid(self) -> tuple[float, float]:
        c = self.geometry.centroid
        return (c.x, c.y)

    def to_working(self) -> BaseGeometry:
        """Геометрия в рабочей метрической проекции."""
        return reproject_geometry(self.geometry, WGS84, self.crs_working)

    @property
    def area_km2(self) -> float:
        """Площадь в км², посчитанная в метрической проекции (не в градусах!)."""
        return self.to_working().area / 1e6

    def buffer_m(self, distance_m: float) -> AOI:
        """Расширить AOI на N метров (буфер считается в метрах, не в градусах)."""
        buffered = self.to_working().buffer(distance_m)
        return AOI(
            name=f"{self.name}+{int(distance_m)}m",
            geometry=reproject_geometry(buffered, self.crs_working, WGS84),
            crs_working=self.crs_working,
        )

    def tiles(self, tile_size_m: float, overlap_m: float = 0.0) -> list[AOI]:
        """Нарезать AOI на квадратные плитки для потайловой обработки.

        Область Астаны — это порядка тысяч км²; загружать её одним массивом
        нельзя ни по памяти, ни по таймаутам. Пайплайн всегда идёт плитками,
        а перекрытие нужно, чтобы объект на границе не разрезался пополам.
        """
        if tile_size_m <= 0:
            raise ValueError("tile_size_m должен быть положительным")
        if overlap_m < 0 or overlap_m >= tile_size_m:
            raise ValueError("overlap_m должен быть в диапазоне [0, tile_size_m)")

        geom_m = self.to_working()
        min_x, min_y, max_x, max_y = geom_m.bounds
        step = tile_size_m - overlap_m
        n_x = max(1, math.ceil((max_x - min_x) / step))
        n_y = max(1, math.ceil((max_y - min_y) / step))

        out: list[AOI] = []
        for iy in range(n_y):
            for ix in range(n_x):
                x0 = min_x + ix * step
                y0 = min_y + iy * step
                tile_m = box(x0, y0, x0 + tile_size_m, y0 + tile_size_m)
                clipped = tile_m.intersection(geom_m)
                if clipped.is_empty:
                    continue
                out.append(
                    AOI(
                        name=f"{self.name}_x{ix:03d}y{iy:03d}",
                        geometry=reproject_geometry(clipped, self.crs_working, WGS84),
                        crs_working=self.crs_working,
                    )
                )
        return out

    def __repr__(self) -> str:  # pragma: no cover - вспомогательное
        min_lon, min_lat, max_lon, max_lat = self.bbox
        return (
            f"AOI(name={self.name!r}, bbox=({min_lon:.4f}, {min_lat:.4f}, "
            f"{max_lon:.4f}, {max_lat:.4f}), area={self.area_km2:.1f} км²)"
        )


# --------------------------------------------------------------------------- #
#  Утилиты проекций
# --------------------------------------------------------------------------- #


def reproject_geometry(geometry: BaseGeometry, src_crs: str, dst_crs: str) -> BaseGeometry:
    """Перепроецировать геометрию между двумя CRS."""
    if CRS.from_user_input(src_crs) == CRS.from_user_input(dst_crs):
        return geometry
    transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    return shapely_transform(transformer.transform, geometry)


def utm_crs_for(lon: float, lat: float) -> str:
    """Подобрать UTM-зону по координате.

    Нужно, если AOI переносят на другой регион: жёстко зашитая зона 42N
    даст сильные искажения площадей уже в соседней области.
    """
    zone = int((lon + 180.0) / 6.0) + 1
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    return f"EPSG:{epsg}"


def _union_all(geoms: list[BaseGeometry]) -> BaseGeometry:
    from shapely.ops import unary_union

    return unary_union(geoms)


__all__ = ["AOI", "WGS84", "reproject_geometry", "utm_crs_for"]
