"""Опыт: отличает ли сезонность ДО разрыва пашню от свалки.

── Откуда вопрос ───────────────────────────────────────────────────────

Прогон по Алматы дал восемь объектов, и все восемь оказались ложными:
орошаемая пашня, пруды, заболоченные русла. Пять физических признаков их
не разделили — сила признаков у пашни 0,575 против 0,505 у подтверждённых
свалок Астаны.

Причина в самом методе. Детектор ищет место, где растительность исчезла
НАВСЕГДА, и поле, выведенное из оборота, ведёт себя ровно так же.

── Гипотеза ────────────────────────────────────────────────────────────

Разница должна быть не ПОСЛЕ разрыва, а ДО него.

У пашни до вывода из оборота — правильный годовой цикл: посев, рост,
уборка, голая земля. Амплитуда годовой гармоники большая.

У пустыря, на котором потом возникла свалка, до разрыва растёт бурьян:
сезонность есть, но слабее и без резкой уборки.

Если гипотеза верна, амплитуда сезонности до разрыва — недостающий
шестой признак, и он снимает ровно тот класс ошибок, на котором метод
провалился в Алматы.

── Как проверяется ─────────────────────────────────────────────────────

По каждому объекту берётся ряд NDVI за восемь лет в его собственных
пикселях, обрезается по дате разрыва, и на этом отрезке подгоняется
годовая гармоника. Амплитуда = sqrt(a² + b²) при sin и cos.

Сравниваются три группы: подтверждённые свалки Астаны, отвергнутые
объекты Астаны и алматинская пашня. Если группы не разделяются —
гипотеза неверна, и это тоже результат.

    python scripts/exp_seasonality.py
"""

import logging
import sys
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("seasonality")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Полуразмер окна вокруг объекта, м. Сто метров — объект плюс немного
#: контекста; больше значит разбавить его сигнал соседним полем.
HALF_M = 100

#: Сколько наблюдений минимум нужно до разрыва, чтобы годовая гармоника
#: имела смысл. Двенадцать месячных композитов — один полный цикл; меньше
#: подгоняется под шум.
MIN_BEFORE = 12


def annual_amplitude(values: np.ndarray, dates: np.ndarray) -> float:
    """Амплитуда годовой гармоники, подогнанной обычным МНК.

    Возвращает NaN, если наблюдений слишком мало: пустой ответ честнее
    числа, полученного из четырёх точек.
    """
    good = np.isfinite(values)
    if good.sum() < MIN_BEFORE:
        return float("nan")

    t = dates[good].astype("datetime64[D]").astype("float64") / 365.25
    y = values[good].astype("float64")
    design = np.column_stack([
        np.ones_like(t),
        t,                              # тренд — иначе он утечёт в гармонику
        np.sin(2 * np.pi * t),
        np.cos(2 * np.pi * t),
    ])
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    return float(np.hypot(beta[2], beta[3]))


def amplitude_for(geometry, break_date, catalog, settings) -> float:
    """Сезонность в пикселях объекта до даты разрыва."""
    from shapely.geometry import box

    from vantage.aoi import AOI
    from vantage.raster import build_feature_cube, series_to_matrix

    point = geometry.centroid
    window = box(point.x - HALF_M, point.y - HALF_M, point.x + HALF_M, point.y + HALF_M)
    import geopandas as gpd

    wgs = gpd.GeoSeries([window], crs=settings.project.crs_working).to_crs(4326)
    aoi = AOI.from_bbox(tuple(wgs.total_bounds), name="probe",
                        crs_working=settings.project.crs_working)

    items = catalog.sentinel2_items(aoi, settings)
    if not items:
        return float("nan")
    cube = build_feature_cube(aoi, settings, items, variables=["ndvi"])
    matrix, dates, _ = series_to_matrix(cube, "ndvi")

    # Среднее по окну: отдельный пиксель шумит сильнее, чем различаются
    # группы, и на нём опыт не сойдётся ни при какой гипотезе.
    series = np.nanmean(matrix, axis=1)

    cut = np.datetime64(str(break_date)[:10])
    before = dates < cut
    if before.sum() < MIN_BEFORE:
        return float("nan")
    return annual_amplitude(series[before], dates[before])


def main() -> int:
    import geopandas as gpd

    from vantage import env
    from vantage.catalog import StacCatalog
    from vantage.config import load_settings

    env.configure()
    settings = load_settings()
    catalog = StacCatalog()

    groups: dict[str, list[float]] = {
        "Астана · подтверждённые свалки": [],
        "Астана · отвергнутые": [],
        "Алматы · пашня (все ложные)": [],
    }

    sources = [
        ("outputs_real/candidates.geojson", "astana"),
        ("outputs_almaty/candidates.geojson", "almaty"),
    ]
    for path, city in sources:
        if not Path(path).exists():
            log.warning("нет %s — пропускаю", path)
            continue
        # В рабочую проекцию обязательно: GeoJSON всегда в градусах, а
        # окно вокруг объекта строится в метрах. Без этого ±100 «метров»
        # прибавляются к долготе, и окно уезжает за тысячи километров —
        # первый запуск искал снимки в Гвинейском заливе.
        data = gpd.read_file(path).to_crs(settings.project.crs_working)
        for row in data.itertuples():
            if city == "astana":
                verdict = getattr(row, "visual_check", None)
                if verdict == "landfill":
                    key = "Астана · подтверждённые свалки"
                elif verdict == "not_landfill":
                    key = "Астана · отвергнутые"
                else:
                    continue
            else:
                key = "Алматы · пашня (все ложные)"

            try:
                value = amplitude_for(row.geometry, row.break_date, catalog, settings)
            except Exception as error:
                log.warning("   %s: %s", row.candidate_id, str(error)[:70])
                continue
            if np.isfinite(value):
                groups[key].append(value)
                log.info("   %-8s %-30s амплитуда %.4f", row.candidate_id, key[:30], value)

    log.info("")
    log.info("── Амплитуда годовой гармоники NDVI до разрыва ──")
    for name, values in groups.items():
        if not values:
            log.info("%-32s нет данных", name)
            continue
        arr = np.array(values)
        log.info("%-32s n=%2d  медиана %.4f  разброс %.4f–%.4f",
                 name, len(arr), float(np.median(arr)), float(arr.min()), float(arr.max()))

    dumps = np.array(groups["Астана · подтверждённые свалки"])
    fields = np.array(groups["Алматы · пашня (все ложные)"])
    log.info("")
    if len(dumps) >= 2 and len(fields) >= 2:
        overlap = (dumps.max() >= fields.min()) and (fields.max() >= dumps.min())
        if overlap:
            log.info("ВЫВОД: диапазоны перекрываются — признак НЕ разделяет группы.")
            log.info("Гипотеза не подтвердилась. Это результат, а не неудача:")
            log.info("шестой признак искать надо в другом месте.")
        else:
            log.info("ВЫВОД: диапазоны НЕ перекрываются — признак разделяет группы.")
            log.info("При трёх свалках это ещё не доказательство, но проверять стоит.")
    else:
        log.info("ВЫВОД: данных мало для сравнения.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
