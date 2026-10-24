"""Извлечение чипов «до / после» для сиамской сети.

Чип — квадратный фрагмент растра вокруг кандидата. Сеть получает **пару**
чипов: состояние до разрыва и после. Она учится не тому, «как выглядит
свалка», а тому, «как выглядит превращение в свалку» — это принципиально
другая и заметно более простая задача при малой обучающей выборке.

Три решения, каждое из которых придётся объяснять
-------------------------------------------------
**Почему эпохи усредняются, а не берётся один снимок.** Один снимок несёт
случайность конкретного дня: облачная тень, влажность после дождя, угол
солнца. Медиана по нескольким композитам до разрыва и после снимает это.

**Почему нормировка по каналам, а не глобальная.** Отражение в SWIR и в
синем канале различаются по величине на порядок. Без поканальной нормировки
градиенты будут определяться самым «громким» каналом.

**Почему статистика нормировки считается только на обучающей выборке.**
Иначе информация о валидационных объектах протекает в обучение, и
измеренное качество окажется завышенным — классическая утечка данных.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .candidates import RasterGrid
from .config import ChipsCfg

log = logging.getLogger(__name__)

#: Значение-заполнитель для пикселей за краем растра.
#: Ноль после нормировки означает «среднее по каналу», то есть отсутствие
#: сигнала, а не экстремальное значение.
PAD_VALUE = 0.0


@dataclass
class ChipDataset:
    """Пары чипов «до / после» с метаданными.

    ``before`` и ``after`` имеют форму (N, C, H, W).
    """

    before: np.ndarray
    after: np.ndarray
    candidate_ids: list[str]
    channels: list[str]
    labels: np.ndarray | None = None
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return int(self.before.shape[0])

    def __post_init__(self) -> None:
        if self.before.shape != self.after.shape:
            raise ValueError(
                f"формы before {self.before.shape} и after {self.after.shape} должны совпадать"
            )
        if len(self.candidate_ids) != len(self):
            raise ValueError("число идентификаторов не совпадает с числом чипов")
        if self.before.shape[1] != len(self.channels):
            raise ValueError(
                f"каналов в данных {self.before.shape[1]}, а имён каналов {len(self.channels)}"
            )
        if self.labels is not None and self.labels.shape[0] != len(self):
            raise ValueError("число меток не совпадает с числом чипов")

    @property
    def n_channels(self) -> int:
        return int(self.before.shape[1])

    def subset(self, index: np.ndarray) -> ChipDataset:
        return ChipDataset(
            before=self.before[index],
            after=self.after[index],
            candidate_ids=[self.candidate_ids[i] for i in np.atleast_1d(index)],
            channels=list(self.channels),
            labels=None if self.labels is None else self.labels[index],
            meta=dict(self.meta),
        )

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            before=self.before,
            after=self.after,
            candidate_ids=np.array(self.candidate_ids, dtype=object),
            channels=np.array(self.channels, dtype=object),
            labels=np.array([]) if self.labels is None else self.labels,
        )
        log.info("Сохранено %d пар чипов в %s", len(self), path)

    @classmethod
    def load(cls, path: str | Path) -> ChipDataset:
        data = np.load(Path(path), allow_pickle=True)
        labels = data["labels"]
        return cls(
            before=data["before"],
            after=data["after"],
            candidate_ids=list(data["candidate_ids"]),
            channels=list(data["channels"]),
            labels=None if labels.size == 0 else labels,
        )


# --------------------------------------------------------------------------- #
#  Вырезание
# --------------------------------------------------------------------------- #


def window_bounds(
    row: int, col: int, size_px: int, shape: tuple[int, int]
) -> tuple[int, int, int, int, int, int, int, int]:
    """Границы окна в растре и соответствующие границы внутри чипа.

    Возвращает (r0, r1, c0, c1, dr0, dr1, dc0, dc1): первые четыре — что
    читать из растра, вторые четыре — куда это класть в чипе. Разделение
    нужно для объектов у края растра: окно обрезается, но чип остаётся
    фиксированного размера, а недостающее заполняется PAD_VALUE.
    """
    ny, nx = shape
    half = size_px // 2
    r0, r1 = row - half, row - half + size_px
    c0, c1 = col - half, col - half + size_px

    dr0 = max(0, -r0)
    dc0 = max(0, -c0)
    r0c, c0c = max(0, r0), max(0, c0)
    r1c, c1c = min(ny, r1), min(nx, c1)
    dr1 = dr0 + (r1c - r0c)
    dc1 = dc0 + (c1c - c0c)
    return r0c, r1c, c0c, c1c, dr0, dr1, dc0, dc1


def extract_window(stack: np.ndarray, row: int, col: int, size_px: int) -> np.ndarray:
    """Вырезать окно (C, size, size) с дополнением за краем растра."""
    n_channels, ny, nx = stack.shape
    chip = np.full((n_channels, size_px, size_px), PAD_VALUE, dtype="float32")
    r0, r1, c0, c1, dr0, dr1, dc0, dc1 = window_bounds(row, col, size_px, (ny, nx))
    if r1 > r0 and c1 > c0:
        chip[:, dr0:dr1, dc0:dc1] = stack[:, r0:r1, c0:c1]
    return chip


def epoch_composite(
    cube,
    channels: list[str],
    time_slice: slice,
) -> np.ndarray:
    """Медианный композит куба по срезу времени -> (C, ny, nx).

    Медиана по нескольким месяцам, а не единичный снимок: она снимает
    случайность конкретного дня съёмки и устойчива к пропущенному облаку.
    """
    layers = []
    for name in channels:
        values = cube[name].isel(time=time_slice)
        layers.append(np.asarray(values.median(dim="time", skipna=True).values, dtype="float32"))
    return np.stack(layers, axis=0)


def build_chips(
    cube,
    candidates,
    grid: RasterGrid,
    cfg: ChipsCfg,
    *,
    epoch_months: int = 12,
    labels: np.ndarray | None = None,
) -> ChipDataset:
    """Собрать пары чипов для всех кандидатов.

    Для каждого кандидата берётся окно ``cfg.size_px`` вокруг его
    центроида. Эпоха «до» — ``epoch_months`` наблюдений перед разрывом,
    эпоха «после» — столько же после. Если разрыв слишком близко к краю
    ряда, окно сдвигается внутрь, а не обрезается: сеть должна получать
    сопоставимые по объёму данных эпохи, иначе она выучит не изменение
    поверхности, а разницу в уровне шума.
    """
    from rasterio.transform import Affine, rowcol

    if len(candidates) == 0:
        raise ValueError("нет кандидатов для извлечения чипов")

    channels = list(cfg.bands) + list(cfg.derived)
    missing = [c for c in channels if c not in cube]
    if missing:
        raise KeyError(f"в кубе нет каналов: {missing}")

    n_time = cube.sizes["time"]
    if n_time < 2 * epoch_months:
        raise ValueError(
            f"в ряду {n_time} наблюдений — мало для двух эпох по {epoch_months}. "
            "Уменьшите epoch_months или расширьте период."
        )

    transform = Affine(*grid.transform)
    points = candidates.geometry.representative_point()
    rows, cols = rowcol(transform, [p.x for p in points], [p.y for p in points])
    rows = np.atleast_1d(np.asarray(rows))
    cols = np.atleast_1d(np.asarray(cols))

    # Индекс разрыва по каждому кандидату: если его нет, делим ряд пополам
    if "break_index" in candidates.columns:
        break_idx = candidates["break_index"].to_numpy()
    else:
        break_idx = np.full(len(candidates), n_time // 2)

    before_list: list[np.ndarray] = []
    after_list: list[np.ndarray] = []

    # Композиты кешируются: у разных кандидатов часто совпадает эпоха,
    # а медиана по кубу — самая дорогая операция в этом модуле.
    cache: dict[tuple[int, int], np.ndarray] = {}

    def composite(start: int, stop: int) -> np.ndarray:
        key = (start, stop)
        if key not in cache:
            cache[key] = epoch_composite(cube, channels, slice(start, stop))
        return cache[key]

    for i in range(len(candidates)):
        k = int(np.clip(break_idx[i], epoch_months, n_time - epoch_months))
        before_stack = composite(k - epoch_months, k)
        after_stack = composite(k, k + epoch_months)
        before_list.append(extract_window(before_stack, int(rows[i]), int(cols[i]), cfg.size_px))
        after_list.append(extract_window(after_stack, int(rows[i]), int(cols[i]), cfg.size_px))

    dataset = ChipDataset(
        before=np.stack(before_list),
        after=np.stack(after_list),
        candidate_ids=list(candidates["candidate_id"])
        if "candidate_id" in candidates.columns
        else [f"C{i:05d}" for i in range(len(candidates))],
        channels=channels,
        labels=labels,
        meta={"epoch_months": epoch_months, "size_px": cfg.size_px},
    )
    log.info("Извлечено %d пар чипов, %d каналов", len(dataset), dataset.n_channels)
    return dataset


# --------------------------------------------------------------------------- #
#  Нормировка
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ChannelStats:
    """Поканальные среднее и стандартное отклонение."""

    mean: np.ndarray
    std: np.ndarray

    @classmethod
    def fit(cls, dataset: ChipDataset, index: np.ndarray | None = None) -> ChannelStats:
        """Посчитать статистику ТОЛЬКО по обучающей выборке.

        Если считать по всему набору, информация о валидационных объектах
        протечёт в обучение и измеренное качество окажется завышенным.
        Это самая незаметная и самая частая ошибка в ML-пайплайнах.
        """
        subset = dataset if index is None else dataset.subset(index)
        both = np.concatenate([subset.before, subset.after], axis=0)
        # Усредняем по объектам и пространству, оставляя ось каналов
        mean = np.nanmean(both, axis=(0, 2, 3))
        std = np.nanstd(both, axis=(0, 2, 3))
        std = np.where(np.isfinite(std) & (std > 1e-6), std, 1.0)
        return cls(mean=np.nan_to_num(mean).astype("float32"), std=std.astype("float32"))

    def apply(self, chips: np.ndarray) -> np.ndarray:
        """Нормировать (N, C, H, W); NaN заменяются нулём — «нет сигнала»."""
        mean = self.mean[None, :, None, None]
        std = self.std[None, :, None, None]
        return np.nan_to_num((chips - mean) / std, nan=PAD_VALUE).astype("float32")

    def transform(self, dataset: ChipDataset) -> ChipDataset:
        return ChipDataset(
            before=self.apply(dataset.before),
            after=self.apply(dataset.after),
            candidate_ids=list(dataset.candidate_ids),
            channels=list(dataset.channels),
            labels=dataset.labels,
            meta={**dataset.meta, "normalized": True},
        )

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, mean=self.mean, std=self.std)

    @classmethod
    def load(cls, path: str | Path) -> ChannelStats:
        data = np.load(Path(path))
        return cls(mean=data["mean"], std=data["std"])


def train_val_split(
    n: int, val_fraction: float, seed: int
) -> tuple[np.ndarray, np.ndarray]:
    """Разделить индексы на обучение и валидацию."""
    if not 0.0 < val_fraction < 1.0:
        raise ValueError("val_fraction должен быть в интервале (0, 1)")
    rng = np.random.default_rng(seed)
    order = rng.permutation(n)
    n_val = max(1, round(n * val_fraction))
    return order[n_val:], order[:n_val]


__all__ = [
    "PAD_VALUE",
    "ChannelStats",
    "ChipDataset",
    "build_chips",
    "epoch_composite",
    "extract_window",
    "train_val_split",
    "window_bounds",
]
