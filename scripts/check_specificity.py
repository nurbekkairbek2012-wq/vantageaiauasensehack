"""Проверить модель на объектах, про которые точно известно: свалок нет.

── Зачем отдельная проверка ────────────────────────────────────────────

Сравнение моделей на нашей размеченной выборке не работает: там семнадцать
объектов и три положительных. При трёх положительных ранжировать можно и
не различая — модель, ставящая «свалка» всему подряд, получает приличный
ROC-AUC, потому что низкая специфичность ничем не наказывается.

Так и вышло 23 августа: модель, обученная на казахстанских полигонах,
дала 0,786 против 0,643 у прежней, была поставлена в продукт и снята
через час. На тридцати объектах восточного пояса, просмотренных глазами и
не содержащих ни одной свалки, она назвала свалкой 63% против 10%.

── Почему восточный пояс годится как эталон ────────────────────────────

Это единственная область, где просмотрены ВСЕ находки и все оказались
ложными: пашня, залежь, луговая степь, лесополосы, пруды. Тридцать три
заведомо отрицательных объекта в одном месте — редкая роскошь.

Хорошая модель должна ставить им низкие оценки. Модель, которая этого не
делает, на карте будет называть свалкой каждый второй склад.

    python scripts/check_specificity.py [models/a.joblib models/b.joblib]
"""

import sys
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Область, все находки которой просмотрены и оказались ложными.
NEGATIVES = Path("outputs_astana_east/candidates.geojson")

#: Сколько объектов брать. Тридцати хватает, чтобы отличить 10% от 63%.
LIMIT = 30


def main() -> int:
    import geopandas as gpd
    import joblib
    import torch

    from vantage import env
    from vantage.config import load_settings

    if not NEGATIVES.exists():
        print(f"нет {NEGATIVES} — эталон отрицательных не посчитан")
        return 1

    env.configure()
    cfg = load_settings().verify
    sys.path.insert(0, str(Path("scripts")))
    from review_sheets import picture
    from train_aerialwaste import backbone, preprocess

    models = [Path(a) for a in sys.argv[1:] if a.endswith(".joblib")]
    if not models:
        models = sorted(Path("models").glob("*chip.joblib"))
    if not models:
        print("не указано ни одной модели")
        return 1

    net, prep = backbone(), preprocess()
    data = gpd.read_file(NEGATIVES).to_crs(4326)

    images = []
    for row in data.itertuples():
        point = row.geometry.centroid
        image = picture(point.y, point.x, str(row.candidate_id), cfg)
        if image is not None:
            images.append(prep(image))
        if len(images) >= LIMIT:
            break

    if len(images) < 10:
        print(f"снимков хватило только на {len(images)} объектов")
        return 1

    with torch.no_grad():
        features = net(torch.stack(images)).numpy()

    print(f"эталон: {len(images)} объектов, свалок среди них нет ни одной")
    print()
    worst = None
    for path in models:
        scores = joblib.load(path).predict_proba(features)[:, 1]
        false_yes = float((scores > 0.5).mean())
        print(f"  {path.stem:22s} медиана {np.median(scores):.3f}   "
              f"ложных «свалка» {false_yes:.0%}")
        if worst is None or false_yes > worst[1]:
            worst = (path.stem, false_yes)

    print()
    if worst and worst[1] > 0.3:
        print(f"ВНИМАНИЕ: {worst[0]} называет свалкой {worst[1]:.0%} заведомо чистых")
        print("объектов. На карте это будет каждый второй склад.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
