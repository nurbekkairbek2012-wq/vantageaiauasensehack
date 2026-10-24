"""Разметка верхних окон сканирования — местные примеры для модели окна.

── Зачем ───────────────────────────────────────────────────────────────

Модель окна учится на дроновых кадрах, Пекине и полигонах OSM — и они не
похожи на свалки Астаны (AI_RESULTS.md, 1х). Global Plastic Watch решал
то же самое кругами: модель ранжирует, проверяющий просматривает верх
списка, просмотренное идёт обратно в обучение (AI_RESULTS.md, 1ц).

  sheets --area A — верхние N окон участка раскладываются по 9 на лист с
           номерами W001…. Север ранжирует среднее трёх исходных моделей,
           остальные участки — модель, обученная на всех уже
           просмотренных участках;
  train  — метки всех просмотренных участков идут в обучение: свалки —
           положительные, остальное просмотренное — трудные
           отрицательные, непросмотренные окна — фон. Проверка — на
           каждом участке, который НЕ просматривался, по свалкам
           госмониторинга, которых ни модель, ни проверяющий не видели;
  sites --area A — окна-«свалки» участка, собранные в места, и их
           расстояние до находок детектора и до карты госмониторинга.

Просматривает ИИ-проверяющий (Claude); метки помечены reviewer и в
разметку человека не попадают.

    python scripts/scan_review.py sheets --area north [--top 153]
    python scripts/scan_review.py train
    python scripts/scan_review.py sites --area north
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
SCAN = ROOT / "data/scan"
OUT = ROOT / "data/eval/scan_review.json"
MODEL = ROOT / "models/scan_window.joblib"
CELL = 340
#: Вес просмотренных окон: свалок — десятки против тысяч фона.
POS_WEIGHT, NEG_WEIGHT = 10.0, 5.0
#: Непросмотренные окна идут в обучение как фон («не свалка») только с
#: участков, где среди просмотренных окон свалок меньше этой доли. На
#: северо-востоке их 64% — вокруг городского полигона, — и его фон
#: учил модель считать мусор «не свалкой»: круг 2 с ним был хуже круга 1
#: на обоих участках проверки (AI_RESULTS.md, 1ц).
CLEAN_BACKGROUND_SHARE = 0.5


def key_path(area: str) -> Path:
    return SCAN / f"review_{area}_key.json"


def answers_path(area: str) -> Path:
    return SCAN / f"review_{area}.json"


def rank(v):
    import numpy as np

    return np.argsort(np.argsort(v)) / (len(v) - 1)


def area_cells(area: str):
    from gemini_screen import releases
    from scan_tiles import tile_range, windows
    from scan_train import AREAS

    bbox = tuple(float(v) for v in AREAS[area].split(","))
    xs, ys = tile_range(bbox)
    return list(windows(xs, ys, releases()[-1][1]))


_PATCH = None
_DINO = None


def features(area: str):
    """(CLS-признаки окон, широты, долготы) участка."""
    import numpy as np

    global _PATCH, _DINO
    if _PATCH is None:
        _PATCH = dict(np.load(SCAN / "patch_features.npz"))
    if f"{area}_cls" in _PATCH:
        return _PATCH[f"{area}_cls"], _PATCH[f"{area}_lat"], _PATCH[f"{area}_lon"]
    from scan_train import area_windows
    from train_dinov2 import load_model

    if _DINO is None:
        _DINO = load_model()
    return area_windows(area, _DINO)


def reviewed_areas() -> list[str]:
    from scan_train import AREAS

    return [a for a in AREAS if answers_path(a).exists() and key_path(a).exists()]


def fit(areas: list[str]):
    """Модель окна: общие наборы + фон и метки просмотренных участков."""
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    z = _PATCH if _PATCH is not None else dict(np.load(SCAN / "patch_features.npz"))
    names = ["drone", "kz", "cwld", "own"]
    X = [z[f"{n}_cls"] for n in names]
    Y = [z[f"{n}_y"] for n in names]
    W = [np.full(len(z[f"{n}_y"]), 10.0 if n == "own" else 1.0) for n in names]
    for area in areas:
        cls, _, _ = features(area)
        key = json.loads(key_path(area).read_text(encoding="utf-8"))
        ans = json.loads(answers_path(area).read_text(encoding="utf-8"))
        dump = [key[c]["index"] for c in ans["dump"]]
        unclear = [key[c]["index"] for c in ans.get("unclear", [])]
        seen = [v["index"] for v in key.values()]
        neg = [i for i in seen if i not in dump]
        rest = np.setdiff1d(np.arange(len(cls)), seen)
        if len(dump) >= CLEAN_BACKGROUND_SHARE * len(seen):
            rest = rest[:0]
        X += [cls[rest], cls[neg], cls[dump]]
        Y += [np.zeros(len(rest)), np.zeros(len(neg)), np.ones(len(dump))]
        W += [np.ones(len(rest)), np.full(len(neg), NEG_WEIGHT), np.full(len(dump), POS_WEIGHT)]
        print(f"   {area}: свалка {len(dump)}, не разобрать {len(unclear)}, "
              f"не свалка {len(neg) - len(unclear)}, фон {len(rest)}")
    clf = make_pipeline(StandardScaler(), LogisticRegression(
        C=0.1, class_weight="balanced", max_iter=5000))
    return clf.fit(np.vstack(X), np.concatenate(Y), logisticregression__sample_weight=np.concatenate(W))


def sheets(area: str, top: int) -> int:
    import numpy as np
    from PIL import Image, ImageDraw

    cells = area_cells(area)
    if area == "north":
        scores = np.load(SCAN / "scores_north.npz")
        score = np.mean([rank(scores[k]) for k in ("dinov2", "cwld", "skyclip")], axis=0)
    else:
        clf = fit(reviewed_areas())
        cls, _, _ = features(area)
        score = rank(clf.decision_function(cls))
        # С SkyCLIP в смеси проверяющему достаётся больше свалок: на двух
        # участках проверки смесь 0,7/0,3 поднимала их в верхних 10% окон.
        sky_path = SCAN / f"scores_{area}.npz"
        if sky_path.exists():
            sky = np.load(sky_path)
            if "skyclip" in sky and len(sky["skyclip"]) == len(score):
                score = 0.7 * score + 0.3 * rank(sky["skyclip"])
    assert len(cells) == len(score), "окна не совпали с оценками"
    order = np.argsort(-score)[:top]
    folder = SCAN / f"review_{area}"
    folder.mkdir(parents=True, exist_ok=True)
    key = {}
    for s in range(0, len(order), 9):
        canvas = Image.new("RGB", (CELL * 3, CELL * 3), (0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        for j, idx in enumerate(order[s:s + 9]):
            code = f"W{s + j + 1:03d}"
            lat, lon, picture = cells[int(idx)]
            key[code] = {"index": int(idx), "lat": float(lat), "lon": float(lon),
                         "score": round(float(score[idx]), 4)}
            x, y = (j % 3) * CELL, (j // 3) * CELL
            canvas.paste(picture.resize((CELL - 4, CELL - 4)), (x + 2, y + 2))
            draw.rectangle((x + 2, y + 2, x + 62, y + 20), fill=(0, 0, 0))
            draw.text((x + 6, y + 5), code, fill=(255, 255, 0))
        canvas.save(folder / f"sheet_{s // 9 + 1:02d}.png")
    key_path(area).write_text(json.dumps(key, indent=1), encoding="utf-8")
    print(f"── листов: {(len(order) + 8) // 9}, окон {len(order)} → {folder.relative_to(ROOT)}")
    return 0


def train() -> int:
    import warnings

    import geopandas as gpd
    import joblib
    import numpy as np
    from scan_train import AREAS, labels
    from sklearn.metrics import roc_auc_score

    warnings.filterwarnings("ignore")
    done = reviewed_areas()
    tests = [a for a in AREAS if a not in done]
    gov = gpd.read_file(ROOT / "data/gov_waste/astana.geojson").geometry.representative_point()
    print(f"── просмотрены: {', '.join(done)}; проверка: {', '.join(tests)}")

    def evaluate(s, label, groups):
        order = np.empty(len(s), dtype=int)
        order[np.argsort(-s)] = np.arange(len(s))
        row = {"roc_auc": round(float(roc_auc_score(label, s)), 3)}
        for share in (0.05, 0.10, 0.20):
            top = order < int(share * len(s))
            row[f"top{int(share * 100)}"] = int(sum(bool(top[g].any()) for g in groups))
        return row

    variants = {"без просмотра": fit([]), f"просмотр: {', '.join(done)}": fit(done)}
    results = {"reviewed": done, "tests": {}}
    for area in tests:
        cls, lat, lon = features(area)
        label, groups = labels(lat, lon, gov, tuple(float(v) for v in AREAS[area].split(",")))
        if not groups:
            continue
        results["tests"][area] = {"gov_dumps": len(groups), "models": {}}
        print(f"\n── проверка: {area}, окон {len(cls)}, свалок госмониторинга {len(groups)}")
        for name, clf in variants.items():
            r = evaluate(clf.decision_function(cls), label, groups)
            results["tests"][area]["models"][name] = r
            print(f"   {name:36} AUC {r['roc_auc']:.3f}; в верхних 5/10/20% — "
                  f"{r['top5']}/{r['top10']}/{r['top20']} из {len(groups)}", flush=True)
    joblib.dump(variants[f"просмотр: {', '.join(done)}"], MODEL)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"── модель → {MODEL.relative_to(ROOT)}; итоги → {OUT.relative_to(ROOT)}")
    return 0


def sites(area: str) -> int:
    """Места, найденные просмотром: окна-«свалки» ближе 200 м — одно место.

    Для каждого места — расстояние до находок детектора и до карты
    госмониторинга. Место, которого нет ни там, ни там, — новая находка
    сканирования. Это кандидаты ИИ-проверяющего: в разметку и в деньги
    они не входят, их смотрит человек.
    """
    import geopandas as gpd
    from shapely.geometry import Point

    key = json.loads(key_path(area).read_text(encoding="utf-8"))
    answers = json.loads(answers_path(area).read_text(encoding="utf-8"))
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

    raw_frames = [gpd.read_file(p).to_crs(32642)
                  for p in ROOT.glob("outputs_*/candidates_raw.geojson")]
    raw = gpd.GeoDataFrame(geometry=[g for f in raw_frames for g in f.geometry], crs=32642)
    gov = gpd.read_file(ROOT / "data/gov_waste/astana.geojson").to_crs(32642)
    rows = []
    for members in groups.values():
        # Представитель места — окно с наибольшей оценкой, а не центр
        # цепочки окон: у вытянутой свалки центр цепочки лежит мимо неё.
        best = max(members, key=lambda i: key[codes[i]]["score"])
        point = pts.geometry.iloc[best]
        rows.append({"area": area, "codes": ",".join(codes[i] for i in members),
                     "windows": len(members),
                     "detector_m": round(float(raw.distance(point).min())),
                     "gov_m": round(float(gov.distance(point).min())),
                     "reviewer": answers.get("reviewer", "claude-vision"), "geometry": point})
    frame = gpd.GeoDataFrame(rows, crs=32642).to_crs(4326)
    frame["new"] = (frame["detector_m"] > 150) & (frame["gov_m"] > 150)
    out = SCAN / f"new_sites_{area}.geojson"
    frame.to_file(out, driver="GeoJSON")
    for r in frame.sort_values("windows", ascending=False).itertuples():
        print(f"   окон {r.windows:2}; до находки детектора {r.detector_m} м, "
              f"до госмониторинга {r.gov_m} м{' — НОВОЕ' if r.new else ''}")
    print(f"── мест {len(frame)}, новых {int(frame['new'].sum())} → {out.relative_to(ROOT)}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    mode = args[0] if args else "sheets"
    area = args[args.index("--area") + 1] if "--area" in args else "north"
    if mode == "sheets":
        return sheets(area, int(args[args.index("--top") + 1]) if "--top" in args else 153)
    if mode == "sites":
        return sites(area)
    return train()


if __name__ == "__main__":
    sys.exit(main())
