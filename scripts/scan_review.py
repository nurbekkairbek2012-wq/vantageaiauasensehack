"""Разметка верхних окон сканирования — местные примеры для модели окна.

── Зачем ───────────────────────────────────────────────────────────────

Модель окна учится на дроновых кадрах, Пекине и полигонах OSM — и они не
похожи на свалки Астаны (AI_RESULTS.md, 1х). Global Plastic Watch решал
то же самое кругами: модель ранжирует, человек просматривает верх
списка, просмотренное идёт обратно в обучение. Здесь первый круг:

  sheets — верхние N окон северного участка (среднее рангов трёх моделей)
           раскладываются по 9 на лист с номерами W001…;
  train  — метки просмотра (data/scan/review_north.json: номера окон со
           свалкой и «не разобрать») идут в обучение: свалки —
           положительные, остальное просмотренное — трудные
           отрицательные; проверка — на ЗАПАДНОМ участке по свалкам
           госмониторинга, которых ни модель, ни проверяющий не видели.

Просматривает ИИ-проверяющий (Claude); метки помечены reviewer и в
разметку человека не попадают.

    python scripts/scan_review.py sheets [--top 153]
    python scripts/scan_review.py train
    python scripts/scan_review.py sites
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
SHEETS = ROOT / "data/scan/review_north"
KEY = ROOT / "data/scan/review_north_key.json"
ANSWERS = ROOT / "data/scan/review_north.json"
OUT = ROOT / "data/eval/scan_review.json"
CELL = 340


def rank(v):
    import numpy as np

    return np.argsort(np.argsort(v)) / (len(v) - 1)


def north_windows():
    from gemini_screen import releases
    from scan_tiles import tile_range, windows
    from scan_train import AREAS

    bbox = tuple(float(v) for v in AREAS["north"].split(","))
    xs, ys = tile_range(bbox)
    return list(windows(xs, ys, releases()[-1][1]))


def sheets(top: int) -> int:
    import numpy as np
    from PIL import Image, ImageDraw

    scores = np.load(ROOT / "data/scan/scores_north.npz")
    mean = np.mean([rank(scores[k]) for k in ("dinov2", "cwld", "skyclip")], axis=0)
    order = np.argsort(-mean)[:top]
    cells = north_windows()
    assert len(cells) == len(mean), "окна не совпали с оценками"
    SHEETS.mkdir(parents=True, exist_ok=True)
    key = {}
    for s in range(0, len(order), 9):
        canvas = Image.new("RGB", (CELL * 3, CELL * 3), (0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        for j, idx in enumerate(order[s:s + 9]):
            code = f"W{s + j + 1:03d}"
            lat, lon, picture = cells[int(idx)]
            key[code] = {"index": int(idx), "lat": float(lat), "lon": float(lon),
                         "score": round(float(mean[idx]), 4)}
            x, y = (j % 3) * CELL, (j // 3) * CELL
            canvas.paste(picture.resize((CELL - 4, CELL - 4)), (x + 2, y + 2))
            draw.rectangle((x + 2, y + 2, x + 62, y + 20), fill=(0, 0, 0))
            draw.text((x + 6, y + 5), code, fill=(255, 255, 0))
        canvas.save(SHEETS / f"sheet_{s // 9 + 1:02d}.png")
    KEY.write_text(json.dumps(key, indent=1), encoding="utf-8")
    print(f"── листов: {(len(order) + 8) // 9}, окон {len(order)} → {SHEETS.relative_to(ROOT)}")
    return 0


def train() -> int:
    import warnings

    import geopandas as gpd
    import numpy as np
    from scan_train import AREAS, labels
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    warnings.filterwarnings("ignore")
    z = dict(np.load(ROOT / "data/scan/patch_features.npz"))
    key = json.loads(KEY.read_text(encoding="utf-8"))
    answers = json.loads(ANSWERS.read_text(encoding="utf-8"))
    dump_idx = [key[c]["index"] for c in answers["dump"]]
    unclear_idx = [key[c]["index"] for c in answers.get("unclear", [])]
    reviewed = [v["index"] for v in key.values()]
    hard_neg = [i for i in reviewed if i not in dump_idx and i not in unclear_idx]
    print(f"── просмотрено {len(reviewed)} окон: свалка {len(dump_idx)}, "
          f"не разобрать {len(unclear_idx)}, не свалка {len(hard_neg)}")

    n_cls = z["north_cls"]
    rest = np.setdiff1d(np.arange(len(n_cls)), reviewed)

    def fit(extra_pos, extra_neg, pos_w, neg_w):
        names = ["drone", "kz", "cwld", "own"]
        X = [z[f"{n}_cls"] for n in names] + [n_cls[rest], n_cls[extra_neg], n_cls[extra_pos]]
        Y = ([z[f"{n}_y"] for n in names] + [np.zeros(len(rest)), np.zeros(len(extra_neg)),
                                            np.ones(len(extra_pos))])
        W = ([np.full(len(z[f"{n}_y"]), 10.0 if n == "own" else 1.0) for n in names]
             + [np.ones(len(rest)), np.full(len(extra_neg), neg_w), np.full(len(extra_pos), pos_w)])
        clf = make_pipeline(StandardScaler(), LogisticRegression(
            C=0.1, class_weight="balanced", max_iter=5000))
        return clf.fit(np.vstack(X), np.concatenate(Y), logisticregression__sample_weight=np.concatenate(W))

    gov = gpd.read_file(ROOT / "data/gov_waste/astana.geojson").geometry.representative_point()
    w_lat, w_lon = z["west_lat"], z["west_lon"]
    label, groups = labels(w_lat, w_lon, gov, tuple(float(v) for v in AREAS["west"].split(",")))
    sky = np.load(ROOT / "data/scan/scores_area.npz")["skyclip"]

    def evaluate(s):
        order = np.empty(len(s), dtype=int)
        order[np.argsort(-s)] = np.arange(len(s))
        row = {"roc_auc": round(float(roc_auc_score(label, s)), 3)}
        for share in (0.05, 0.10, 0.20):
            top = order < int(share * len(s))
            row[f"top{int(share * 100)}"] = int(sum(bool(top[g].any()) for g in groups))
        return row

    variants = {
        "фон (без просмотра)": fit([], [], 1.0, 1.0),
        "+ просмотренные не-свалки": fit([], hard_neg + unclear_idx, 1.0, 5.0),
        "+ просмотренные свалки и не-свалки": fit(dump_idx, hard_neg + unclear_idx, 10.0, 5.0),
    }
    results = {"reviewed": len(reviewed), "dumps": len(dump_idx), "models": {}}
    print(f"\n── проверка: запад, {len(groups)} свалок госмониторинга")
    for name, clf in variants.items():
        s = clf.decision_function(z["west_cls"])
        for tag, score in ((name, s), (f"{name} + SkyCLIP", (rank(s) + rank(sky)) / 2)):
            r = evaluate(score)
            results["models"][tag] = r
            print(f"   {tag:48} AUC {r['roc_auc']:.3f}; в верхних 5/10/20% — "
                  f"{r['top5']}/{r['top10']}/{r['top20']} из {len(groups)}", flush=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"── записано в {OUT.relative_to(ROOT)}")
    return 0


def sites() -> int:
    """Места, найденные просмотром: окна-«свалки» ближе 200 м — одно место.

    Для каждого места — расстояние до находок детектора и до карты
    госмониторинга. Место, которого нет ни там, ни там, — новая находка
    сканирования. Это кандидаты ИИ-проверяющего: в разметку и в деньги
    они не входят, их смотрит человек (data/scan/new_sites.geojson).
    """
    import geopandas as gpd
    from shapely.geometry import Point

    key = json.loads(KEY.read_text(encoding="utf-8"))
    answers = json.loads(ANSWERS.read_text(encoding="utf-8"))
    codes = answers["dump"]
    pts = gpd.GeoDataFrame({"code": codes},
                           geometry=[Point(key[c]["lon"], key[c]["lat"]) for c in codes],
                           crs=4326).to_crs(32642)
    parent = list(range(len(pts)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            if pts.geometry.iloc[i].distance(pts.geometry.iloc[j]) < 200:
                parent[root(i)] = root(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(pts)):
        groups.setdefault(root(i), []).append(i)

    raw = gpd.read_file(ROOT / "outputs_real/candidates_raw.geojson").to_crs(32642)
    gov = gpd.read_file(ROOT / "data/gov_waste/astana.geojson").to_crs(32642)
    rows = []
    for members in groups.values():
        # Представитель места — окно с наибольшей оценкой, а не центр
        # цепочки окон: у вытянутой свалки центр цепочки лежит мимо неё.
        best = max(members, key=lambda i: key[codes[i]]["score"])
        point = pts.geometry.iloc[best]
        rows.append({"codes": ",".join(codes[i] for i in members), "windows": len(members),
                     "detector_m": round(float(raw.distance(point).min())),
                     "gov_m": round(float(gov.distance(point).min())),
                     "reviewer": answers.get("reviewer", "claude-vision"), "geometry": point})
    frame = gpd.GeoDataFrame(rows, crs=32642).to_crs(4326)
    frame["new"] = (frame["detector_m"] > 150) & (frame["gov_m"] > 150)
    out = ROOT / "data/scan/new_sites.geojson"
    frame.to_file(out, driver="GeoJSON")
    for r in frame.sort_values("windows", ascending=False).itertuples():
        print(f"   {r.codes:60} окон {r.windows:2}; до находки детектора {r.detector_m} м, "
              f"до госмониторинга {r.gov_m} м{' — НОВОЕ' if r.new else ''}")
    print(f"── мест {len(frame)}, новых {int(frame['new'].sum())} → {out.relative_to(ROOT)}")
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "sheets"
    if mode == "sites":
        return sites()
    if mode == "sheets":
        top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 153
        return sheets(top)
    return train()


if __name__ == "__main__":
    sys.exit(main())
