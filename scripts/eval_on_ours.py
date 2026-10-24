"""Проверить модель AerialWaste на казахстанских объектах — честно.

── Почему не по чипам Sentinel ─────────────────────────────────────────

Первая версия этого скрипта сравнивала предсказания с нашими чипами для
разметки. Это была ошибка измерения, а не кода: чипы собраны из Sentinel-2
и несут десять метров на пиксель, а AerialWaste снят с воздуха и с
WorldView-3 — от двадцати до пятидесяти сантиметров. Разница в двадцать
раз по разрешению означает, что модель смотрела бы на другой предмет.
Провал такого переноса ничего не сказал бы о модели.

Здесь берутся тайлы высокого разрешения — те самые, что тянет
доверификация, около полуметра на пиксель. Это та же модальность, в
которой модель обучалась, и результат интерпретируем.

── Чего ждать от трёх положительных примеров ───────────────────────────

Подтверждённых свалок три, отвергнутых четырнадцать. Точечная оценка на
таком размере не значит ничего: убери одну свалку — метрика прыгнет на
десятые. Поэтому считается интервал по бутстрэпу, и решение принимается по
его нижней границе, а не по середине.

Если нижняя граница окажется ниже 0,5 — перенос НЕ доказан, и говорить
надо именно так. Названная слабость стоит дороже необоснованной цифры:
на техническом Q&A вторую разберут за минуту.

── Кэш ─────────────────────────────────────────────────────────────────

Тайлы тянутся с чужих серверов с паузой между запросами. Скачанное
складывается в data/highres и переиспользуется: результат по объекту не
меняется от того, что рядом пересчитали деньги.

    python scripts/eval_on_ours.py [--refresh]
"""

import logging
import sys
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("eval")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CANDIDATES = Path("outputs_real/candidates.geojson")
CACHE = Path("data/highres")
MODEL = Path("models/aerialwaste_chip.joblib")

# Модель выбирается ключом --model: моделей стало несколько, и сравнивать
# их надо одной и той же проверкой, а не разными скриптами.
for _i, _a in enumerate(sys.argv):
    if _a == "--model" and _i + 1 < len(sys.argv):
        MODEL = Path(sys.argv[_i + 1])

#: Вердикт разметки -> метка. «Не понятно» выброшено: объект, про который
#: человек не смог решить, измерял бы нашу неуверенность, а не модель.
VERDICT = {"landfill": 1, "not_landfill": 0}


def picture(lat: float, lon: float, name: str, cfg, refresh: bool):
    """Снимок высокого разрешения вокруг точки, из кэша или из сети."""
    from PIL import Image

    from vantage.verify import PROVIDERS, fetch_tile_grid

    CACHE.mkdir(parents=True, exist_ok=True)
    # Зум в имени файла обязателен. Два скрипта делят этот кэш и качают
    # на разном зуме: доверификация на 17-м, листы просмотра на 18-м. При
    # ключе из одного идентификатора тот, кто записал первым, определял,
    # что увидит второй — и одно и то же измерение давало то 0,643, то
    # 0,333 в зависимости от порядка запуска.
    path = CACHE / f"{name}_z{cfg.zoom}.png"
    legacy = CACHE / f"{name}.png"
    if path.exists() and not refresh:
        return Image.open(path).convert("RGB")
    if legacy.exists() and not refresh:
        # Старый файл неизвестного зума не используем: лучше скачать
        # заново, чем сравнивать модели на разных снимках.
        legacy.unlink()

    # Первый доступный поставщик: расхождения между ними здесь не важны —
    # доверификация уже сравнила их между собой, а нам нужен снимок.
    for key in cfg.providers:
        provider = PROVIDERS.get(key)
        if provider is None:
            continue
        try:
            grid = fetch_tile_grid(provider, lat, lon, cfg.zoom, cfg.tile_grid,
                                   timeout=cfg.timeout_s)
        except Exception as error:
            log.debug("   %s: %s", key, error)
            continue
        image = Image.fromarray(grid.astype("uint8"))
        image.save(path)
        return image
    return None


def main() -> int:
    if not MODEL.exists():
        log.error("нет модели %s — сначала scripts/train_aerialwaste.py", MODEL)
        return 1

    import geopandas as gpd
    import joblib
    import torch

    from vantage import env
    from vantage.config import load_settings

    env.configure()
    cfg = load_settings().verify

    sys.path.insert(0, str(Path("scripts")))
    from train_aerialwaste import backbone, preprocess

    kept = gpd.read_file(CANDIDATES).to_crs(4326)
    kept["mark"] = kept["visual_check"].map(VERDICT)
    usable = kept[kept["mark"].notna()].copy()
    log.info("объектов с вердиктом: %d из %d (свалок %d)",
             len(usable), len(kept), int(usable["mark"].sum()))
    if len(usable) < 8 or usable["mark"].sum() < 2:
        log.error("нечего измерять")
        return 1

    refresh = "--refresh" in sys.argv
    net, prep = backbone(), preprocess()
    model = joblib.load(MODEL)

    images, truth, names = [], [], []
    for row in usable.itertuples():
        point = row.geometry.centroid
        image = picture(point.y, point.x, str(row.candidate_id), cfg, refresh)
        if image is None:
            log.warning("   %s: снимок не получен", row.candidate_id)
            continue
        images.append(prep(image))
        truth.append(int(row.mark))
        names.append(str(row.candidate_id))

    if len(images) < 8 or sum(truth) < 2:
        log.error("снимков хватило только на %d объектов, свалок %d", len(images), sum(truth))
        return 1

    with torch.no_grad():
        features = net(torch.stack(images)).numpy()
    scores = model.predict_proba(features)[:, 1]
    truth = np.array(truth)

    from sklearn.metrics import average_precision_score, roc_auc_score

    base = float(truth.mean())
    log.info("")
    log.info("── Перенос AerialWaste на Казахстан, снимки ~0,5 м/пиксель ──")
    log.info("объектов %d, свалок %d (%.0f%%)", len(truth), int(truth.sum()), 100 * base)

    roc = roc_auc_score(truth, scores)
    pr = average_precision_score(truth, scores)
    log.info("ROC-AUC %.3f", roc)
    log.info("PR-AUC  %.3f при базовой частоте %.3f — лучше случайного в %.1f раза",
             pr, base, pr / base)

    rng = np.random.default_rng(0)
    draws = []
    for _ in range(4000):
        pick = rng.integers(0, len(truth), len(truth))
        if 0 < truth[pick].sum() < len(pick):
            draws.append(roc_auc_score(truth[pick], scores[pick]))
    if draws:
        low, high = np.percentile(draws, [5, 95])
        log.info("ROC-AUC, 90%% интервал по бутстрэпу: %.3f – %.3f", low, high)
        log.info("")
        if low < 0.5:
            log.warning("ВЫВОД: перенос НЕ доказан — нижняя граница ниже случайного.")
            log.warning("На защите говорить именно так, а не называть середину.")
        elif low > 0.7:
            log.info("ВЫВОД: перенос состоялся, нижняя граница выше 0,7.")
        else:
            log.info("ВЫВОД: перенос вероятен, но выборка мала — интервал широкий.")

    log.info("")
    log.info("── Как модель оценила каждый ──")
    for name, mark, score in sorted(zip(names, truth, scores), key=lambda x: -x[2]):
        log.info("  %.3f  %-11s  %s", score, "СВАЛКА" if mark else "не свалка", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
