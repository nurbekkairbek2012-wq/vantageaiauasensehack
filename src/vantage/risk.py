"""Прогноз появления новых свалок.

Слой, который переводит проект из мониторинга в **превентивную экономику**.
Убрать свалку стоит миллионы; не дать ей появиться — стоит знака и
фотоловушки. Чтобы поставить их осмысленно, нужно знать, где именно.

Откуда берётся обучающая выборка
--------------------------------
Детектор изменений выдаёт не только факт свалки, но и **дату её
возникновения**. Значит, у нас автоматически есть история: где и когда
появлялись объекты с 2018 по 2026 год. Это и есть разметка — её не надо
собирать вручную, она получается побочным продуктом основного пайплайна.

Признаки
--------
Только пространственные и только те, что имеют содержательное объяснение:
расстояние до дороги, до жилья, до легального полигона, плотность уже
существующих свалок, тип землепользования, уклон рельефа, укрытость от
глаз. Каждый признак можно защитить фразой «водитель самосвала
рассуждает именно так».

Валидация — по времени, а не случайная
--------------------------------------
Ключевое методологическое решение. Случайное разбиение выборки здесь
даёт завышенную и **неправдивую** оценку: соседние ячейки сильно
скоррелированы, и модель, увидевшая одну ячейку свалки в обучении,
угадает соседнюю в валидации, ничего не выучив по существу.

Мы обучаемся на объектах, возникших до момента отсечки, и проверяемся
на возникших после. Это ровно та задача, которая стоит в реальности:
предсказать будущее по прошлому. Метрика получается ниже, чем при
случайном разбиении, — и она честная.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import geopandas as gpd
import numpy as np
from shapely.geometry import box

from .aoi import AOI
from .config import RiskCfg

log = logging.getLogger(__name__)

#: Признаки модели риска. Порядок фиксирован — от него зависит
#: интерпретация важностей в отчёте.
FEATURE_NAMES = (
    "dist_road_m",
    "dist_settlement_m",
    "dist_legal_site_m",
    "existing_density_3km",
    "existing_density_10km",
    "dist_nearest_existing_m",
    "screening_score",
)


@dataclass
class RiskModel:
    """Обученная модель риска вместе с оценкой качества."""

    booster: object
    feature_names: list[str]
    cutoff_date: str
    metrics: dict[str, float] = field(default_factory=dict)
    importances: dict[str, float] = field(default_factory=dict)

    def top_features(self, k: int = 5) -> list[tuple[str, float]]:
        return sorted(self.importances.items(), key=lambda kv: -kv[1])[:k]


# --------------------------------------------------------------------------- #
#  Сетка
# --------------------------------------------------------------------------- #


def build_grid(aoi: AOI, cell_m: float) -> gpd.GeoDataFrame:
    """Разбить область на регулярную сетку ячеек в метрической проекции.

    Размер ячейки — компромисс. Мелкая даёт точность, но большинство
    ячеек оказываются пустыми, и классы становятся катастрофически
    несбалансированными. 500 м — примерно масштаб решения водителя
    о том, где свернуть с дороги.
    """
    if cell_m <= 0:
        raise ValueError("размер ячейки должен быть положительным")

    geom = aoi.to_working()
    min_x, min_y, max_x, max_y = geom.bounds
    n_x = max(1, int(np.ceil((max_x - min_x) / cell_m)))
    n_y = max(1, int(np.ceil((max_y - min_y) / cell_m)))

    cells, ids = [], []
    for iy in range(n_y):
        for ix in range(n_x):
            x0 = min_x + ix * cell_m
            y0 = min_y + iy * cell_m
            cell = box(x0, y0, x0 + cell_m, y0 + cell_m)
            if cell.intersects(geom):
                cells.append(cell)
                ids.append(f"G{ix:04d}_{iy:04d}")

    grid = gpd.GeoDataFrame({"cell_id": ids, "geometry": cells}, crs=aoi.crs_working)
    log.info("Сетка риска: %d ячеек по %.0f м", len(grid), cell_m)
    return grid


# --------------------------------------------------------------------------- #
#  Признаки
# --------------------------------------------------------------------------- #


def _distance_to(cells: gpd.GeoDataFrame, layer: gpd.GeoDataFrame) -> np.ndarray:
    """Расстояние от центроида ячейки до ближайшего объекта слоя, метры."""
    if layer is None or len(layer) == 0:
        return np.full(len(cells), np.inf)
    centroids = gpd.GeoDataFrame(geometry=cells.geometry.centroid, crs=cells.crs)
    joined = gpd.sjoin_nearest(centroids, layer[["geometry"]], how="left", distance_col="_d")
    return joined.groupby(joined.index)["_d"].min().reindex(cells.index).to_numpy()


def _density_within(cells: gpd.GeoDataFrame, points: gpd.GeoDataFrame, radius_m: float) -> np.ndarray:
    """Сколько объектов слоя попадает в круг заданного радиуса вокруг ячейки."""
    if points is None or len(points) == 0:
        return np.zeros(len(cells))
    centroids = cells.geometry.centroid
    buffers = gpd.GeoDataFrame(geometry=centroids.buffer(radius_m), crs=cells.crs)
    joined = gpd.sjoin(buffers, points[["geometry"]], how="left", predicate="intersects")
    # sjoin с how="left" оставляет строку и при отсутствии совпадений,
    # поэтому считаем не размер группы, а число непустых index_right —
    # иначе ячейка без единой свалки получила бы плотность 1.
    matches = joined.groupby(joined.index)["index_right"].apply(lambda s: s.notna().sum())
    return matches.reindex(cells.index).fillna(0).to_numpy().astype(float)


def spatial_features(
    grid: gpd.GeoDataFrame,
    *,
    roads: gpd.GeoDataFrame | None = None,
    settlements: gpd.GeoDataFrame | None = None,
    legal_sites: gpd.GeoDataFrame | None = None,
    existing: gpd.GeoDataFrame | None = None,
) -> gpd.GeoDataFrame:
    """Посчитать признаки для каждой ячейки сетки.

    ``existing`` — уже известные свалки. Плотность существующих объектов
    рядом — самый сильный признак: свалки растут кустами. Где однажды
    свалили, свалят снова, потому что место уже «испорчено» и там
    привычно останавливаться.
    """
    out = grid.copy()
    out["dist_road_m"] = _distance_to(out, roads)
    out["dist_settlement_m"] = _distance_to(out, settlements)
    out["dist_legal_site_m"] = _distance_to(out, legal_sites)
    out["dist_nearest_existing_m"] = _distance_to(out, existing)
    out["existing_density_3km"] = _density_within(out, existing, 3_000)
    out["existing_density_10km"] = _density_within(out, existing, 10_000)

    # «Укрытость»: удобно там, где подъезд близко, а жильё далеко.
    # Прямое выражение логики нарушителя: доехать легко, увидеть некому.
    with np.errstate(divide="ignore", invalid="ignore"):
        out["screening_score"] = np.where(
            np.isfinite(out["dist_road_m"]) & (out["dist_road_m"] > 0),
            np.clip(out["dist_settlement_m"] / np.maximum(out["dist_road_m"], 1.0), 0, 100),
            0.0,
        )

    # Бесконечности заменяем на большое конечное число: деревья их не
    # обрабатывают, а смысл «очень далеко» сохраняется.
    for name in FEATURE_NAMES:
        out[name] = np.nan_to_num(out[name], posinf=1e6, neginf=0.0)
    return out


def temporal_labels(
    grid: gpd.GeoDataFrame,
    landfills: gpd.GeoDataFrame,
    *,
    date_column: str = "break_date",
    cutoff: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Метки «до отсечки» и «после отсечки» для каждой ячейки.

    Возвращает (y_train, y_future): появилась ли свалка в ячейке до
    момента отсечки и после него.
    """
    if date_column not in landfills.columns:
        raise KeyError(f"в таблице свалок нет колонки {date_column}")

    dates = landfills[date_column]
    cutoff_ts = np.datetime64(cutoff)
    valid = dates.notna()
    before = landfills[valid & (dates.values.astype("datetime64[D]") < cutoff_ts)]
    after = landfills[valid & (dates.values.astype("datetime64[D]") >= cutoff_ts)]

    def mark(subset: gpd.GeoDataFrame) -> np.ndarray:
        if len(subset) == 0:
            return np.zeros(len(grid), dtype=int)
        joined = gpd.sjoin(grid[["geometry"]], subset[["geometry"]], how="left", predicate="intersects")
        hit = joined.groupby(joined.index)["index_right"].apply(lambda s: s.notna().any())
        return hit.reindex(grid.index).fillna(False).to_numpy().astype(int)

    y_train, y_future = mark(before), mark(after)
    log.info(
        "Разметка риска: %d ячеек со свалками до %s, %d после",
        int(y_train.sum()), cutoff, int(y_future.sum()),
    )
    return y_train, y_future


# --------------------------------------------------------------------------- #
#  Обучение
# --------------------------------------------------------------------------- #


def train_risk_model(
    features: gpd.GeoDataFrame,
    y_train: np.ndarray,
    y_future: np.ndarray,
    cfg: RiskCfg,
    *,
    cutoff: str,
) -> RiskModel:
    """Обучить модель риска с валидацией по времени.

    Обучаемся на объектах до отсечки, проверяемся на появившихся после.
    Ячейки, где свалка уже была до отсечки, из валидации исключаются:
    предсказывать появление там, где уже есть, бессмысленно, и их
    присутствие завысило бы метрику.
    """
    import lightgbm as lgb

    from .model.train import pr_auc

    # Передаём DataFrame, а не numpy: LightGBM запоминает имена колонок и
    # при предсказании проверяет их. С голым массивом перепутанный порядок
    # признаков не вызвал бы ошибки — модель молча считала бы не то.
    x = features[list(FEATURE_NAMES)].astype(float)

    if y_train.sum() < 5:
        raise ValueError(
            f"слишком мало положительных примеров до отсечки ({int(y_train.sum())}). "
            "Сдвиньте cutoff позже или увеличьте область."
        )

    booster = lgb.LGBMClassifier(
        n_estimators=cfg.n_estimators,
        learning_rate=cfg.learning_rate,
        max_depth=cfg.max_depth,
        random_state=cfg.seed,
        # Классы крайне несбалансированы: свалки есть в единицах процентов ячеек
        class_weight="balanced",
        verbose=-1,
    )
    booster.fit(x, y_train)

    # Честная проверка: только ячейки, где до отсечки свалки НЕ было
    fresh = y_train == 0
    metrics: dict[str, float] = {}
    if fresh.sum() and y_future[fresh].sum():
        scores = booster.predict_proba(x.loc[fresh])[:, 1]
        metrics["pr_auc_future"] = pr_auc(y_future[fresh], scores)
        metrics["base_rate_future"] = float(y_future[fresh].mean())
        metrics["lift"] = (
            metrics["pr_auc_future"] / metrics["base_rate_future"]
            if metrics["base_rate_future"] > 0
            else 0.0
        )
        log.info(
            "Валидация по времени: PR-AUC=%.3f при базовой частоте %.4f (выигрыш x%.1f)",
            metrics["pr_auc_future"], metrics["base_rate_future"], metrics["lift"],
        )
    else:
        log.warning("После отсечки нет новых объектов — качество прогноза не измерено")

    importances = dict(
        zip(FEATURE_NAMES, booster.feature_importances_.astype(float), strict=True)
    )
    total = sum(importances.values()) or 1.0
    importances = {k: v / total for k, v in importances.items()}

    return RiskModel(
        booster=booster,
        feature_names=list(FEATURE_NAMES),
        cutoff_date=cutoff,
        metrics=metrics,
        importances=importances,
    )


def mask_implausible(
    risk_grid: gpd.GeoDataFrame, implausible: gpd.GeoDataFrame | None
) -> gpd.GeoDataFrame:
    """Обнулить риск там, где свалка не возникает.

    Модель обучена на признаках «далеко от жилья, близко к дороге, рядом уже
    свалили» и ничего не знает о том, кому принадлежит участок. Ботанический
    сад Назарбаев Университета удовлетворяет первым двум признакам идеально
    и на первом же прогоне получил высший класс риска. Ошибка не в весах:
    признака, отличающего охраняемую территорию от пустыря, в модели нет.

    Добавить такой признак и переобучить — заманчиво и неверно. Обучающих
    примеров «свалка в парке» ноль по определению, дерево не научится на
    отсутствии, и признак получит нулевой вес — ровно как расстояние до
    легального полигона. Запрет должен быть жёстким правилом, а не весом:
    это знание о мире, а не закономерность в данных.

    Обнуление, а не удаление строк: сетка остаётся полной, и по ней видно,
    что ячейка рассмотрена и отвергнута. Удалённая ячейка неотличима от
    непосчитанной.
    """
    out = risk_grid.copy()
    out["masked"] = False
    if implausible is None or implausible.empty or out.empty:
        return out

    layer = implausible.to_crs(out.crs) if implausible.crs != out.crs else implausible
    # Пересечение, а не «центр внутри»: ячейка 500 м, наполовину лежащая в
    # парке, — это половина парка, и предсказывать там нечего.
    hit = gpd.sjoin(
        out[["geometry"]], layer[["geometry"]], predicate="intersects", how="inner"
    )
    touched = out.index.isin(hit.index.unique())
    out.loc[touched, "masked"] = True
    if "risk" in out.columns:
        out.loc[touched, "risk"] = 0.0
        out["risk_rank"] = out["risk"].rank(ascending=False, method="min").astype(int)

    log.info(
        "Прогноз: обнулено %d ячеек из %d — охраняемые и застроенные земли",
        int(touched.sum()), len(out),
    )
    return out


def predict_risk(model: RiskModel, features: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Вероятность появления новой свалки в каждой ячейке.

    Признаки отбираются по именам, а не по позиции: таблица могла быть
    дополнена новыми колонками между обучением и применением.
    """
    missing = [name for name in model.feature_names if name not in features.columns]
    if missing:
        raise KeyError(f"в таблице признаков не хватает колонок: {missing}")

    x = features[model.feature_names].astype(float)
    out = features.copy()
    out["risk"] = model.booster.predict_proba(x)[:, 1]
    out["risk_rank"] = out["risk"].rank(ascending=False, method="min").astype(int)
    return out


# --------------------------------------------------------------------------- #
#  Публичный слой
# --------------------------------------------------------------------------- #


def aggregate_public(
    risk_grid: gpd.GeoDataFrame, cfg: RiskCfg, *, quantiles: int = 4
) -> gpd.GeoDataFrame:
    """Огрубить карту риска для публичной версии.

    Публичная карта показывает зоны укрупнённой сеткой и в виде класса
    риска, а не точной вероятности. Причина не техническая, а этическая:
    точная карта риска, привязанная к конкретным дворам, работает как
    неявное обвинение жителей в том, чего они ещё не сделали.

    Точные координаты и вероятности остаются в закрытой части — для
    акимата и экологической службы.
    """
    if cfg.public_grid_cell_m < cfg.grid_cell_m:
        raise ValueError("публичная сетка должна быть не мельче рабочей")

    factor = cfg.public_grid_cell_m
    centroids = risk_grid.geometry.centroid
    key_x = (centroids.x // factor).astype(int)
    key_y = (centroids.y // factor).astype(int)

    grouped = risk_grid.assign(_kx=key_x, _ky=key_y).groupby(["_kx", "_ky"])
    cells, values = [], []
    for (kx, ky), part in grouped:
        cells.append(box(kx * factor, ky * factor, (kx + 1) * factor, (ky + 1) * factor))
        values.append(float(part["risk"].max()))

    public = gpd.GeoDataFrame({"risk": values, "geometry": cells}, crs=risk_grid.crs)
    # Класс вместо числа: «высокий риск» понятнее и менее адресно, чем 0.734
    public["risk_class"] = (
        public["risk"].rank(pct=True).mul(quantiles).apply(np.ceil).clip(1, quantiles).astype(int)
    )
    return public[["risk_class", "geometry"]]


def dissolve_public(public: gpd.GeoDataFrame, *, drop_lowest: bool = True) -> gpd.GeoDataFrame:
    """Слить смежные ячейки одного класса в единые зоны.

    Две причины, и обе существенные.

    **Картографическая.** Зона риска — это область, а не мозаика из
    квадратов. Растворение убирает внутренние границы и даёт то, что
    человек и ожидает увидеть на карте риска.

    **Практическая.** Публичная карта должна открываться без сети на
    телефоне. Сетка из тысяч ячеек весит под мегабайт, а после
    растворения — десятки килобайт: общие границы соседних ячеек
    перестают храниться дважды.

    ``drop_lowest`` убирает класс минимального риска: он занимает
    большую часть площади, неотличим от фона и нужен только для
    раздувания файла.
    """
    if public.empty or "risk_class" not in public.columns:
        return public

    data = public[public["risk_class"] > 1] if drop_lowest else public
    if data.empty:
        return data

    dissolved = data.dissolve(by="risk_class").reset_index()
    return dissolved[["risk_class", "geometry"]]


def recommend_placements(risk_grid: gpd.GeoDataFrame, budget: int) -> gpd.GeoDataFrame:
    """Куда поставить знаки и фотоловушки при ограниченном бюджете.

    Это и есть та реплика на питче, ради которой строился весь слой:
    «убрать свалку стоит миллионы, не дать ей появиться — стоит знака,
    и вот координаты, куда его поставить».
    """
    if budget < 1:
        raise ValueError("бюджет должен быть положительным")
    top = risk_grid.nlargest(budget, "risk").copy()
    top["placement_rank"] = range(1, len(top) + 1)
    return top[["cell_id", "risk", "placement_rank", "geometry"]]


__all__ = [
    "FEATURE_NAMES",
    "RiskModel",
    "aggregate_public",
    "build_grid",
    "dissolve_public",
    "predict_risk",
    "recommend_placements",
    "spatial_features",
    "temporal_labels",
    "train_risk_model",
]
