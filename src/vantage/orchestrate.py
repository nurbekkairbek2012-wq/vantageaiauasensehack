"""Сквозной настоящий прогон: от снимков до артефактов карты.

Модуль собирает вместе то, что раньше существовало только как отдельные
шаги и вызывалось из тестов. До него ``vantage run`` доходил до поиска
сцен и останавливался, а карта показывала синтетику.

Прогон разделён на две половины, и разделение не косметическое.

**Первая половина — плитки.** Идёт по снимкам, стоит часы, требует сети,
падает от таймаута. Каждая плитка пишет свой результат на диск и при
повторе берётся из кеша.

**Вторая половина — по найденному.** Разметка, обучение, радар, тепло,
деньги, риск, выгрузка. Считается минуты и переигрывается сколько угодно
раз без единого обращения к снимкам.

Один порядок здесь важен и неочевиден: разметка и обучение идут по
**сырым** кандидатам, до контекстного отсева. Отсев вычитает из списка
известные полигоны ТБО — то есть ровно те объекты, которые служат
положительными примерами. Обучать после него означало бы обучать на
выборке, где нет ни одного положительного класса.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from .aoi import AOI
from .chips import ChipDataset
from .config import Economics, Settings, load_economics, load_settings
from .pipeline import Pipeline

log = logging.getLogger(__name__)

#: Сторона плитки по умолчанию. Пять километров — компромисс между двумя
#: расходами: мелкие плитки платят сетевые накладные много раз, крупные не
#: помещаются в память вместе с каналами для чипов (девять каналов на
#: шестьдесят месяцев — это гигабайты).
DEFAULT_TILE_M = 5_000.0

#: Минимум размеченных примеров каждого класса, при котором вообще имеет
#: смысл обучать сеть. Меньше — и «качество» будет измеряться на трёх
#: объектах, то есть не будет измеряться никак.
MIN_LABELS_PER_CLASS = 5


@dataclass
class RunOutcome:
    """Чем закончился прогон. Всё, о чём придётся отвечать на защите."""

    raw_candidates: int = 0
    merged_candidates: int = 0
    kept_candidates: int = 0
    rejection: dict[str, int] = field(default_factory=dict)
    labels: dict[str, int] = field(default_factory=dict)
    model_note: str | None = None
    signals: str | None = None
    verified: int = 0
    confirmed: int = 0
    artifacts: dict[str, str] = field(default_factory=dict)

    def to_text(self) -> str:
        lines = [
            f"кандидатов найдено: {self.merged_candidates}",
            f"после контекстного отсева: {self.kept_candidates}",
        ]
        if self.model_note:
            lines.append(f"модель: {self.model_note}")
        if self.signals:
            lines.append(f"признаки: {self.signals}")
        if self.verified:
            lines.append(f"доверифицировано: {self.confirmed} из {self.verified}")
        return "; ".join(lines)


# --------------------------------------------------------------------------- #
#  Первая половина: плитки
# --------------------------------------------------------------------------- #


def scan_tiles(
    pipeline: Pipeline,
    *,
    aoi: AOI | None = None,
    tile_size_m: float = DEFAULT_TILE_M,
    chips_dir: Path | None = None,
    limit: int | None = None,
) -> gpd.GeoDataFrame:
    """Пройти область плитками и склеить кандидатов.

    Попутно режет пары чипов «до / после»: снимки для них уже загружены,
    и второй проход за теми же данными стоил бы столько же, сколько весь
    прогон.
    """
    from .chips import build_chips

    area = aoi or pipeline.aoi
    target = Path(chips_dir) if chips_dir else pipeline.settings.paths.resolve("chips")
    target.mkdir(parents=True, exist_ok=True)

    def cut_chips(tile, candidates, cube, grid, dates):
        if candidates.empty:
            return None
        try:
            dataset = build_chips(cube, candidates, grid, pipeline.settings.chips)
        except Exception as exc:
            log.warning("%s: чипы не нарезаны (%s)", tile.name, exc)
            return None
        # Идентификаторы кандидатов уникальны только внутри плитки —
        # без префикса чипы соседних плиток затрут друг друга.
        dataset.candidate_ids = [f"{tile.name}:{cid}" for cid in dataset.candidate_ids]
        dataset.save(target / f"{tile.name}.npz")
        return None

    merged = pipeline.run_tiles(
        tile_size_m=tile_size_m,
        aoi=area,
        on_tile=cut_chips,
        limit=limit,
        keep_bands=True,
    )
    if not merged.empty:
        path = pipeline.path("candidates_raw.geojson")
        merged.to_crs(pipeline.settings.project.crs_output).to_file(path, driver="GeoJSON")
        pipeline.report.artifacts["candidates_raw"] = str(path)
    return merged


# --------------------------------------------------------------------------- #
#  Вторая половина: по найденному
# --------------------------------------------------------------------------- #


def load_tile_pieces(pipeline: Pipeline) -> gpd.GeoDataFrame:
    """Собрать покусочные результаты плиток с ключом на чипы.

    Куски нужны отдельно от склеенного слоя: чипы нарезаны по кускам, а
    после склейки идентификаторы новые. Связь между ними восстанавливается
    пространственно — см. :func:`transfer_to_merged`.
    """
    crs = pipeline.settings.project.crs_working
    parts = []
    for path in sorted((pipeline.outputs / "tiles").glob("*.geojson")):
        layer = gpd.read_file(path)
        if layer.empty:
            continue
        layer["tile"] = path.stem
        layer["chip_key"] = layer["tile"] + ":" + layer["candidate_id"]
        parts.append(layer.to_crs(crs))
    if not parts:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)
    return gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=crs)


def load_chips(chips_dir: Path, prefix: str = "") -> ChipDataset | None:
    """Склеить все сохранённые наборы чипов в один."""
    files = sorted(Path(chips_dir).glob(f"{prefix}*.npz"))
    datasets = [ChipDataset.load(path) for path in files]
    if not datasets:
        return None
    combined = ChipDataset(
        before=np.concatenate([d.before for d in datasets]),
        after=np.concatenate([d.after for d in datasets]),
        candidate_ids=[cid for d in datasets for cid in d.candidate_ids],
        channels=datasets[0].channels,
    )
    log.info("Чипов загружено: %d пар из %d файлов", len(combined), len(files))
    return combined


def classify(
    chips: ChipDataset,
    pieces: gpd.GeoDataFrame,
    settings: Settings,
    *,
    model_path: Path | None = None,
) -> tuple[dict[str, float], str | None]:
    """Обучить сеть на автоматической разметке и применить ко всем кускам.

    Возвращает ``(вероятность по ключу чипа, замечание)``. Замечание не
    пустое, когда обучение не состоялось — и тогда карта честно покажет
    прочерк вместо уверенности, а не выдуманное число.
    """
    order = {key: position for position, key in enumerate(pieces["chip_key"])}
    present = [i for i, cid in enumerate(chips.candidate_ids) if cid in order]
    if not present:
        return {}, "чипы и кандидаты не сопоставились"

    raw = pieces["label"].to_numpy()
    values = [raw[order[chips.candidate_ids[i]]] for i in present]
    known = np.array([v is not None and not pd.isna(v) for v in values])
    positives = int(sum(1 for v, ok in zip(values, known, strict=True) if ok and int(v) == 1))
    negatives = int(known.sum()) - positives
    log.info(
        "Размеченных чипов: %d (положительных %d, трудных отрицательных %d)",
        int(known.sum()), positives, negatives,
    )

    if positives < MIN_LABELS_PER_CLASS or negatives < MIN_LABELS_PER_CLASS:
        return {}, (
            f"меток мало ({positives} положительных, {negatives} отрицательных) — "
            "нужна ручная разметка"
        )

    from .model.infer import predict
    from .model.train import train

    index = np.array([present[i] for i in np.nonzero(known)[0]])
    training = chips.subset(index)
    training.labels = np.array(
        [int(v) for v, ok in zip(values, known, strict=True) if ok], dtype="int64"
    )

    trained = train(training, settings.model, progress=False)
    if model_path is not None:
        trained.save(model_path)

    prediction = predict(trained, chips)
    log.info(
        "Модель применена к %d кандидатам, выше порога %d",
        len(prediction), prediction.n_positive,
    )
    return dict(zip(prediction.candidate_ids, prediction.probability.tolist(), strict=True)), None


def transfer_to_merged(
    merged: gpd.GeoDataFrame,
    pieces: gpd.GeoDataFrame,
    columns: tuple[str, ...] = ("probability", "pmli_response"),
) -> gpd.GeoDataFrame:
    """Перенести атрибуты кусков на склеенные объекты.

    Связь пространственная: после склейки идентификаторы новые, и
    сопоставлять по имени нечего. Вероятность берётся максимальная по
    кускам — объект, у которого хотя бы одна часть уверенно опознана,
    уверенно опознан целиком; остальное усредняется медианой.
    """
    available = [c for c in columns if c in pieces.columns]
    if merged.empty or pieces.empty or not available:
        return merged

    probes = pieces[[*available, "geometry"]].copy()
    probes["geometry"] = probes.geometry.representative_point()
    link = gpd.sjoin(
        probes, merged[["candidate_id", "geometry"]], how="left", predicate="within"
    )

    aggregations = {}
    for column in available:
        aggregations[column] = (column, "max" if column == "probability" else "median")
    summary = link.groupby("candidate_id").agg(**aggregations)
    return merged.merge(summary, on="candidate_id", how="left")


def finish_run(
    pipeline: Pipeline,
    merged: gpd.GeoDataFrame,
    *,
    aoi: AOI | None = None,
    chips_dir: Path | None = None,
    chips_prefix: str = "",
    with_model: bool = True,
    with_signals: bool = True,
    with_verify: bool = True,
    with_risk: bool = True,
) -> RunOutcome:
    """Вторая половина прогона: от сырых кандидатов до файлов карты."""
    from .labels import class_balance, harvest_labels
    from .signals import attach_signals, pmli_response_from_chips

    settings = pipeline.settings
    area = aoi or pipeline.aoi
    outcome = RunOutcome(merged_candidates=len(merged))

    pieces = load_tile_pieces(pipeline)
    outcome.raw_candidates = len(pieces)
    if pieces.empty:
        raise RuntimeError("плиточных результатов нет — сначала пройдите область плитками")

    labelled, report = harvest_labels(area, settings, pieces)
    outcome.labels = class_balance(labelled)
    log.info("Разметка: %s", report.to_text())

    chips = load_chips(
        Path(chips_dir) if chips_dir else settings.paths.resolve("chips"), chips_prefix
    )

    if chips is not None and with_model:
        models_dir = settings.paths.resolve("models")
        models_dir.mkdir(parents=True, exist_ok=True)
        probabilities, outcome.model_note = classify(
            chips, labelled, settings, model_path=models_dir / f"siamese_{area.name}.pt"
        )
        labelled["probability"] = labelled["chip_key"].map(probabilities)
    elif with_model:
        outcome.model_note = "чипов нет — сеть не обучалась"

    if chips is not None:
        try:
            labelled["pmli_response"] = labelled["chip_key"].map(
                pmli_response_from_chips(chips)
            )
        except Exception as exc:
            log.warning("Отклик полимеров из чипов не получен: %s", exc)

    merged = transfer_to_merged(merged, labelled)

    filtered, outcome.rejection, layers = pipeline.step_context(merged)
    kept = filtered[filtered["passes_context"]].reset_index(drop=True)
    outcome.kept_candidates = len(kept)

    # Отклонённые сохраняются целиком: на вопрос «а почему вы выкинули
    # вот это» отвечает файл с причиной по каждому объекту, а не память.
    rejected_path = pipeline.path("rejected.geojson")
    filtered.to_crs(settings.project.crs_output).to_file(rejected_path, driver="GeoJSON")
    pipeline.path("rejection_report.json").write_text(
        json.dumps(outcome.rejection, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if kept.empty:
        log.warning("После контекстного отсева не осталось ни одного объекта")
        outcome.artifacts = {"rejected": str(rejected_path)}
        return outcome

    if with_signals:
        kept, signal_report = attach_signals(kept, settings)
        outcome.signals = signal_report.to_text()

    # Согласие признаков считается всегда и последним из признаков:
    # к этому моменту известны и радар, и тепло, и полимеры.
    from .explain import attach_evidence

    kept = attach_evidence(kept)

    if with_verify:
        # Доверификация идёт после отсева и после модели: тайлы высокого
        # разрешения тянутся для лучших кандидатов, а не для всех подряд,
        # и порядок «лучших» задаёт как раз вероятность модели.
        from .verify import attach_verification, verify_candidates
        from .vlm import build_verifier

        try:
            # Зрительная модель подключается, только если есть ключ и пакет.
            # Без неё доверификация работает на текстурном анализе — это
            # штатный режим, а не деградация.
            results = verify_candidates(kept, settings.verify, vlm=build_verifier())
            kept = attach_verification(kept, results, settings.verify)
            outcome.verified = int(sum(1 for r in results if r.n_providers))
            outcome.confirmed = int(
                sum(1 for r in results if r.is_confirmed(settings.verify))
            )
        except Exception as exc:
            log.warning("Доверификация не отработала: %s", exc)

    kept = pipeline.step_money(kept)

    risk_model = private = public = None
    if with_risk:
        try:
            risk_model, private, public = pipeline.step_risk(kept, layers)
        except Exception as exc:
            log.warning("Модель риска не построена: %s", exc)

    registry = pipeline.step_registry()
    outcome.artifacts = pipeline.step_export(
        kept, private, public, registry=registry, risk_model=risk_model, is_demo=False
    )
    pipeline.finish()
    return outcome


def run_full(
    *,
    settings: Settings | None = None,
    economics: Economics | None = None,
    aoi: AOI | None = None,
    outputs: Path | str | None = None,
    tile_size_m: float = DEFAULT_TILE_M,
    limit: int | None = None,
    force: bool = False,
    with_model: bool = True,
    with_signals: bool = True,
    with_verify: bool = True,
    with_risk: bool = True,
) -> RunOutcome:
    """Полный прогон одной командой."""
    settings = settings or load_settings()
    economics = economics or load_economics()
    pipeline = Pipeline(settings, economics, outputs=outputs, force=force)
    area = aoi or pipeline.aoi

    merged = scan_tiles(pipeline, aoi=area, tile_size_m=tile_size_m, limit=limit)
    return finish_run(
        pipeline,
        merged,
        aoi=area,
        chips_prefix=f"{area.name}_",
        with_model=with_model,
        with_signals=with_signals,
        with_verify=with_verify,
        with_risk=with_risk,
    )


__all__ = [
    "DEFAULT_TILE_M",
    "MIN_LABELS_PER_CLASS",
    "RunOutcome",
    "classify",
    "finish_run",
    "load_chips",
    "load_tile_pieces",
    "run_full",
    "scan_tiles",
    "transfer_to_merged",
]
