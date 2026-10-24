"""VANTAGE — обнаружение несанкционированных свалок по спутниковым данным.

Пайплайн (каждый шаг — отдельный модуль, отдельная CLI-команда, отдельный
артефакт на диске; любой шаг можно перезапустить, не трогая остальные):

    catalog  → поиск сцен в STAC
    raster   → загрузка окон, маска облаков, месячные композиты
    indices  → спектральные признаки (NDVI, BSI, PMLI, NDWI, NDMI, NBR)
    sar      → стабильность поверхности по Sentinel-1
    thermal  → тепловая аномалия по Landsat
    change   → поиск необратимых разрывов во временном ряду
    context  → отсев по OSM, дорогам и расстоянию до жилья
    candidates → векторизация в полигоны-кандидаты
    model    → сиамская классификация пар «до / после»
    explain  → вклад каждого признака в решение
    verify   → доверификация тайлами высокого разрешения
    money    → ущерб и извлекаемая ценность (Монте-Карло по диапазонам)
    risk     → прогноз появления новых свалок
    removal  → мультисигнальное подтверждение устранения
    act      → черновик акта, подтверждаемый человеком
"""

from __future__ import annotations

__version__ = "0.1.0"

# Настройка окружения GDAL идёт до любых гео-импортов: pyogrio при импорте
# выставляет путь к сертификатам через setdefault, и переопределить его
# позже уже нельзя. Подробности и причина — в vantage/env.py.
from .env import configure as _configure_env

_configure_env()

from .aoi import AOI  # noqa: E402
from .config import Economics, Settings, load_economics, load_settings  # noqa: E402

__all__ = ["AOI", "Economics", "Settings", "__version__", "load_economics", "load_settings"]
