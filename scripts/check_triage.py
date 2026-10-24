"""Какие признаки отличают свалку от ложной находки — класс или место.

── Зачем ───────────────────────────────────────────────────────────────

Модель по четырём признакам (текстура по снимкам, удалённость от дороги,
число кусков, падение NDVI) на всей разметке дала ROC-AUC 0,78 при
интервале 0,58–0,93 — вневыборочно, с нижней границей выше случайного.
Заголовок «ИИ отсеивает ложные находки с точностью 80%» просился сам.

Это неверно, и проверка заняла минуту. Все семь свалок лежат в одной
области — северном кольце, а большинство не-свалок — в других поясах. Признак, отличающий север от остальных, получает высокую оценку,
ничего не зная о свалках. Ровно эта ловушка описана в AI_RESULTS.md, 1а.

Поэтому каждый признак здесь проверяется двумя вопросами:

  * по классу — свалки против не-свалок ВНУТРИ одной области;
  * по месту — не-свалки этой области против не-свалок остальных.

Признак годится, только если он силён по классу и слаб по месту. Модель
оценивается так же — внутри области, а не на смеси.

── Что показала проверка (экзамен без дублей, 27 сентября) ─────────────

  признак                 по классу   по месту
  текстура по снимкам       0,43        0,76    ← про место
  падение NDVI              0,48        0,73    ← про место
  удалённость от дороги     0,72        0,48    ← про класс
  число кусков              0,70        0,56    ← про класс

  модель, внутри области: 0,695 (90%: 0,50–0,86) — не доказано

Вывод: фильтром это быть не может, а два признака — удалённость от
дороги и раздробленность пятна — стоит держать под наблюдением: они
отличают свалку от склада именно по смыслу (свалку ссыпают в стороне от
проезда, кучами; склад стоит у дороги одним пятном). Проверка решится,
когда свалок станет больше, — и первые же выезды её сдвинут.

    python scripts/check_triage.py
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]

HOME = "outputs_real"

#: Признак -> (колонка, преобразование). Логарифм у расстояния: разница
#: между 10 и 100 м важнее, чем между 1 000 и 1 090.
FEATURES = {
    "текстура по снимкам": ("verify_texture", None),
    "падение NDVI": ("ndvi_drop", None),
    "удалённость от дороги": ("dist_road_m", "log"),
    "число кусков": ("n_pieces", None),
}


def labelled():
    """Все размеченные объекты — из общего экзамена (scripts/build_eval_set.py).

    Раньше набор собирался здесь заново и считал объекты на стыке областей
    дважды: свалка C00061 северного кольца шла ещё и как C00056 южного
    пояса. Теперь набор один на все опыты, и дубли из него убраны.
    """
    import numpy as np
    import pandas as pd

    sys.path.insert(0, str(ROOT / "scripts"))
    from build_eval_set import build

    data = build()
    data = data[data["truth"].isin(["landfill", "not_landfill"])].reset_index(drop=True)
    X = pd.DataFrame(index=data.index)
    for name, (column, how) in FEATURES.items():
        values = pd.to_numeric(data[column], errors="coerce")
        X[name] = np.log1p(values) if how == "log" else values
    X = X.fillna(X.median())
    y = (data["truth"] == "landfill").astype(int).to_numpy()
    return X, y, data["area"].to_numpy()


def bootstrap_auc(y, score, *, seed: int = 0, rounds: int = 3000):
    import numpy as np
    from sklearn.metrics import roc_auc_score

    rng = np.random.default_rng(seed)
    out = []
    for _ in range(rounds):
        idx = rng.integers(0, len(y), len(y))
        if 0 < y[idx].sum() < len(idx):
            out.append(roc_auc_score(y[idx], score[idx]))
    return float(np.percentile(out, 5)), float(np.percentile(out, 95))


def main() -> int:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    warnings.filterwarnings("ignore")
    X, y, area = labelled()
    home = area == HOME
    print(f"── Размечено {len(y)} объектов, свалок {y.sum()}; "
          f"в области {HOME}: {home.sum()}, свалок {y[home].sum()}")
    if y[home].sum() < 2 or (y[home] == 0).sum() < 2:
        print("   в опорной области мало обеих групп — проверять нечем")
        return 1

    print(f"\n   {'признак':24} {'по классу':>10} {'по месту':>10}")
    for name in X.columns:
        values = X[name].to_numpy()
        by_class = roc_auc_score(y[home], values[home])
        by_place = roc_auc_score(home[y == 0], values[y == 0])
        # Признак «про место», если место он различает заметно сильнее класса.
        verdict = ("про место" if abs(by_place - 0.5) > abs(by_class - 0.5) + 0.05
                   else "про класс" if abs(by_class - 0.5) >= 0.15 else "")
        print(f"   {name:24} {by_class:>10.2f} {by_place:>10.2f}   {verdict}")

    # Модель: вневыборочно (исключить один), оценка — внутри опорной области.
    values = X.to_numpy()
    score = np.zeros(len(y))
    for i in range(len(y)):
        keep = np.ones(len(y), bool)
        keep[i] = False
        model = make_pipeline(StandardScaler(), LogisticRegression(
            C=0.3, class_weight="balanced", max_iter=1000))
        model.fit(values[keep], y[keep])
        score[i] = model.predict_proba(values[i:i + 1])[0, 1]

    mixed = roc_auc_score(y, score)
    low, high = bootstrap_auc(y, score)
    inside = roc_auc_score(y[home], score[home])
    in_low, in_high = bootstrap_auc(y[home], score[home], seed=1)
    print(f"\n   модель на смеси областей:  {mixed:.3f} (90%: {low:.2f}–{high:.2f})")
    print(f"   модель внутри {HOME}: {inside:.3f} (90%: {in_low:.2f}–{in_high:.2f})")
    if in_low <= 0.5:
        print("   → внутри области не доказано: интервал захватывает 0,5.")
        print("     Число на смеси областей мерит место, а не класс; фильтром это не ставится.")
    else:
        print("   → внутри области различает. Можно ставить подсказкой порядка просмотра,")
        print("     но не фильтром: цена пропущенной свалки несимметрична.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
