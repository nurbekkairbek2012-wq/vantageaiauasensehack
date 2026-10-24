"""Проставить каждому объекту оценку модели по снимку высокого разрешения.

── Зачем на сайте ──────────────────────────────────────────────────────

Модель существует и измерена, но живёт в логах. За «Использование ИИ»
ставят балл за то, что видно и проверяемо, а не за упоминание в тексте.

── Какая модель и почему именно она ────────────────────────────────────

Обучена на AerialWaste (Politecnico di Milano, CC BY-NC-ND 4.0 — только некоммерческое использование): 5 220 снимков,
ROC-AUC 0,858 при кросс-проверке на их данных, 0,643 на наших семнадцати
при интервале 0,333–0,923 — перенос не доказан.

Казахстанская модель на тех же семнадцати давала 0,786 при интервале
0,571–0,943 и час простояла в продукте. Снята после проверки на выборке
втрое большей и куда более показательной.

── Проверка, которой не хватало ────────────────────────────────────────

Тридцать объектов восточного пояса Астаны, про которые ТОЧНО известно, что
свалок среди них нет: все просмотрены глазами.

    казахстанская   медиана 0,814   ложных «свалка» 63%
    AerialWaste     медиана 0,008   ложных «свалка» 10%

Казахстанская модель говорит «свалка» почти всему. Её высокий результат на
семнадцати объектах при трёх положительных объясняется тем, что ранжировать
можно и не различая: три положительных не наказывают за низкую
специфичность.

Урок общий: сравнение моделей на выборке с тремя положительными не
отличает «умеет различать» от «говорит да». Тридцать известных
отрицательных отличили за одну минуту.

Соблазн был написать «ниже 0,35 модель надёжно отбраковывает»: пять таких
объектов действительно оказались не свалками, и ни одна свалка туда не
попала. Но при четырнадцати не-свалках из семнадцати такая пятёрка
выпадает случайно примерно в трети случаев. Совпадение подходящего
размера — не доказательство, и выдавать его за доказательство нельзя.

Поэтому оценка идёт в данные как справка, а словесная пометка осторожна на
обоих концах шкалы. Закроет вопрос не порог и не переобучение, а
подтверждённые объекты: выезд с фотоаппаратом стоит здесь больше, чем
любая правка кода.

── Смена модели 27 сентября ────────────────────────────────────────────

По умолчанию теперь модель без AerialWaste: DINOv2 ViT-S/14 + логистическая
регрессия на дроновом наборе незаконных свалок (CC-BY-4.0), казахстанском
наборе OSM и отказах человека из других поясов
(models/dinov2_chip_open.joblib, scripts/train_dinov2.py). Причины две:

  * AerialWaste распространяется под CC BY-NC-ND 4.0 — только
    некоммерческое использование; для платного пилота модель на нём не
    годится (docs/LICENSES.md);
  * новая модель точнее на нашем экзамене: внутри северного кольца
    ROC-AUC 0,79 (0,68–0,90) против 0,63 (0,43–0,83) у старой; медиана
    оценки у свалок 0,40, у не-свалок 0,14.

Эта же оценка задаёт подсказку порядка просмотра (vantage.triage).

Старая модель осталась доступна ключом --model models/aerialwaste_chip.joblib.

    python scripts/attach_chipmodel.py [--refresh]
"""

import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("chipmodel")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CANDIDATES = Path("outputs_real/candidates.geojson")
WEB = Path("web-next/public/data/candidates.geojson")
MODEL = Path("models/dinov2_chip_open.joblib")
#: Точность модели на объектах с вердиктом человека — для страницы чисел
#: (scripts/key_numbers.py). Считается при каждой оценке, а не вписывается.
QUALITY = Path("web-next/public/data/image_model.json")

# Папка прогона и модель выбираются ключами: областей стало четыре, а
# моделей несколько, и оценивать их прибитыми путями значит держать по
# копии скрипта на каждую.
for _i, _a in enumerate(sys.argv):
    if _a == "--outputs" and _i + 1 < len(sys.argv):
        CANDIDATES = Path(sys.argv[_i + 1]) / "candidates.geojson"
        WEB = Path("nowhere")          # чужую область на сайт не выгружаем
    if _a == "--model" and _i + 1 < len(sys.argv):
        MODEL = Path(sys.argv[_i + 1])

#: Черта, ниже которой оценка помечается как низкая. Это порог показа, а
#: не доказанное свойство модели — см. заголовок о том, почему пять
#: верных отбраковок подряд ничего не доказывают при такой выборке.
REJECT_BELOW = 0.35


def verdict(score: float) -> str:
    """Словесная пометка — намеренно осторожная на обоих концах.

    Соблазн писать «надёжно не свалка» ниже порога был: пять таких
    объектов действительно оказались не свалками. Но при четырнадцати
    не-свалках из семнадцати такая пятёрка выпадает случайно примерно в
    трети случаев — это не доказательство, а совпадение подходящего
    размера.
    """
    if score < REJECT_BELOW:
        return "низкая оценка"
    return "модель не различает"


def main() -> int:
    if not MODEL.exists():
        log.error("нет модели %s — сначала scripts/train_dinov2.py", MODEL)
        return 1
    if "dinov2" in MODEL.name:
        return run_dinov2()

    import geopandas as gpd
    import joblib
    import torch

    from vantage import env
    from vantage.config import load_settings

    env.configure()
    cfg = load_settings().verify

    sys.path.insert(0, str(Path("scripts")))
    from eval_on_ours import picture
    from train_aerialwaste import backbone, preprocess

    kept = gpd.read_file(CANDIDATES).to_crs(4326)
    net, prep = backbone(), preprocess()
    model = joblib.load(MODEL)
    refresh = "--refresh" in sys.argv

    scores: dict[str, float] = {}
    batch, ids = [], []
    for row in kept.itertuples():
        point = row.geometry.centroid
        image = picture(point.y, point.x, str(row.candidate_id), cfg, refresh)
        if image is None:
            log.warning("   %s: снимок не получен", row.candidate_id)
            continue
        batch.append(prep(image))
        ids.append(str(row.candidate_id))

    if not batch:
        log.error("ни одного снимка")
        return 1

    with torch.no_grad():
        features = net(torch.stack(batch)).numpy()
    for cid, value in zip(ids, model.predict_proba(features)[:, 1], strict=False):
        scores[cid] = float(value)

    working = kept.to_crs(kept.crs)
    working["highres_score"] = working["candidate_id"].map(
        lambda c: round(scores[c], 3) if c in scores else None
    )
    working["highres_verdict"] = working["candidate_id"].map(
        lambda c: verdict(scores[c]) if c in scores else None
    )

    rejected = int(sum(1 for v in scores.values() if v < REJECT_BELOW))
    log.info("оценено %d объектов; уверенно отбраковано %d (ниже %.2f)",
             len(scores), rejected, REJECT_BELOW)

    # Сверка с разметкой глазами — чтобы расхождение было видно сразу, а не
    # обнаружилось на защите.
    if "visual_check" in working.columns:
        marked = working[working["visual_check"].isin(["landfill", "not_landfill"])]
        low = marked[marked["highres_score"] < REJECT_BELOW]
        wrong = int((low["visual_check"] == "landfill").sum())
        log.info("среди уверенно отбракованных настоящих свалок: %d из %d",
                 wrong, len(low))
        if wrong:
            log.warning("модель отбраковывает НАСТОЯЩИЕ свалки — порог занижать нельзя")

    working.to_file(CANDIDATES, driver="GeoJSON")
    if WEB.parent.exists() and WEB.name != "nowhere":
        working.to_file(WEB, driver="GeoJSON")
    log.info("записано в %s и %s", CANDIDATES, WEB)
    return 0


def run_dinov2() -> int:
    """Оценка моделью DINOv2: тот же снимок, что на экзамене (Wayback, 190 м)."""
    import geopandas as gpd
    import joblib
    from PIL import Image

    from vantage import env

    env.configure()
    sys.path.insert(0, str(Path("scripts")))
    from gemini_screen import WAYBACK, fetch, object_crop, releases
    from train_dinov2 import CROP, load_model, predict_tta

    kept = gpd.read_file(CANDIDATES).to_crs(4326)
    release = releases()[-1][1]
    pictures, ids = [], []
    for row in kept.itertuples():
        p = row.geometry.representative_point()
        image, zoom = fetch(p.y, p.x, WAYBACK.format(release=release, x="{x}", y="{y}", z="{z}"),
                         f"wb{release}")
        if image is None:
            log.warning("   %s: снимок не получен", row.candidate_id)
            continue
        # Окно по центру объекта, а не сетки тайлов: иначе соседние объекты
        # получают одну картинку и одну оценку.
        pictures.append(Image.fromarray(object_crop(image, p.y, p.x, zoom, CROP)))
        ids.append(str(row.candidate_id))
    if not pictures:
        log.error("ни одного снимка")
        return 1
    values = predict_tta(load_model(), joblib.load(MODEL), pictures)
    scores = {c: float(v) for c, v in zip(ids, values, strict=True)}

    working = kept
    working["highres_score"] = working["candidate_id"].astype(str).map(
        lambda c: round(scores[c], 3) if c in scores else None)
    working["highres_verdict"] = working["candidate_id"].astype(str).map(
        lambda c: verdict(scores[c]) if c in scores else None)
    log.info("оценено %d объектов моделью %s", len(scores), MODEL.name)
    working.to_file(CANDIDATES, driver="GeoJSON")
    if WEB.name != "nowhere":          # точность — только по области сайта
        write_quality(working)
    if WEB.parent.exists() and WEB.name != "nowhere":
        # На сайт — только опубликованные объекты: переносим оценку по номеру.
        site = gpd.read_file(WEB)
        site["highres_score"] = site["candidate_id"].astype(str).map(
            lambda c: round(scores[c], 3) if c in scores else None)
        site["highres_verdict"] = site["candidate_id"].astype(str).map(
            lambda c: verdict(scores[c]) if c in scores else None)
        site.to_file(WEB, driver="GeoJSON")
    log.info("записано в %s и %s", CANDIDATES, WEB)
    return 0


def write_quality(frame) -> None:
    """ROC-AUC оценки на объектах, где человек сказал «свалка» или «не свалка»."""
    import json

    import numpy as np
    from sklearn.metrics import roc_auc_score
    from train_dinov2 import interval

    d = frame[frame["visual_check"].isin(["landfill", "not_landfill"])
              & frame["highres_score"].notna()]
    y = (d["visual_check"] == "landfill").to_numpy().astype(int)
    if not 0 < y.sum() < len(y):
        return
    score = d["highres_score"].to_numpy(dtype="float64")
    low, high = interval(y, score)
    payload = {
        "model": MODEL.name,
        "roc_auc": round(float(roc_auc_score(y, score)), 3),
        "low": round(low, 3), "high": round(high, 3),
        "objects": len(d), "dumps": int(y.sum()),
        "dumps_below_035": int((score[y == 1] < 0.35).sum()),
        "median_dump": round(float(np.median(score[y == 1])), 3),
        "median_not": round(float(np.median(score[y == 0])), 3),
    }
    # Подсказка порядка просмотра (vantage.triage) на тех же объектах:
    # насколько раньше при ней находятся все свалки.
    from vantage.triage import review_priority

    priority = review_priority(d).to_numpy(dtype="float64")
    t_low, t_high = interval(y, priority)
    rank = np.argsort(np.argsort(-priority, kind="stable"), kind="stable") + 1
    payload["triage"] = {
        "roc_auc": round(float(roc_auc_score(y, priority)), 3),
        "low": round(t_low, 3), "high": round(t_high, 3),
        "last_dump": int(rank[y == 1].max()), "objects": len(d),
    }
    QUALITY.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    log.info("ROC-AUC %.3f (%.2f–%.2f) на %d объектах → %s",
             payload["roc_auc"], low, high, len(d), QUALITY)


if __name__ == "__main__":
    sys.exit(main())
