"""Машинный просмотр снимков моделью Gemini — и его экзамен.

── Зачем ───────────────────────────────────────────────────────────────

Машинный просмотр снимков 0,5 м — лучшее звено системы: на 49 объектах
он снял 71% ручной работы и не отверг ни одной опознанной свалки. Но
делался он не программой, а вручную, моделью в диалоге, по контактным
листам (labels_ai_screen.json). На новой территории его нечем повторить.

Здесь тот же просмотр делает Gemini через API — на каждом объекте, без
человека. Два варианта:

  single — один свежий снимок высокого разрешения;
  pair   — снимок «до» из архива Esri Wayback (последний релиз раньше
           даты разрыва) и свежий «после». Свалка из года в год растёт,
           склад стоит неизменным, — это видно только по паре.

На обоих снимках красным обведён контур объекта: окно 290 м, а объект
бывает 20 × 25 м, и модель должна знать, куда смотреть.

── Вопрос составлен до экзамена ────────────────────────────────────────

Текст запроса написан по описанию задачи, а не подобран под ответы
разметки. Подбор формулировки под 8 свалок дал бы красивое число и
модель, выученную на экзамене. Меняется вопрос — экзамен повторяется
целиком и записывается как новый вариант.

── Экзамен ─────────────────────────────────────────────────────────────

data/eval/labeled.geojson (scripts/build_eval_set.py): 158 объектов с
вердиктом человека. Правило то же, что у машинного просмотра:

  * сколько работы снимает — доля «не свалка» среди ответов;
  * чего это стоит — сколько опознанных свалок среди отказов (цель 0);
  * отдельно — северное кольцо, чтобы число не мерило место.

Ответы кэшируются в data/gemini/<вариант>/: повторный запуск квоту не
тратит, а экзамен можно пересчитать без сети.

    python scripts/gemini_screen.py --variant pair            # экзамен
    python scripts/gemini_screen.py --variant pair --target site   # на сайт
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

EVAL = ROOT / "data/eval/labeled.geojson"
SITE = ROOT / "web-next/public/data/candidates.geojson"
IMAGES = ROOT / "data/gemini/images"
ANSWERS = ROOT / "data/gemini"
RESULTS = ROOT / "data/eval"

ZOOMS = (18, 17)
GRID = 3
WAYBACK = ("https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/"
           "WMTS/1.0.0/default028mm/MapServer/tile/{release}/{z}/{y}/{x}")
#: Пауза между запросами: бесплатный тариф ограничивает запросы в минуту.
PAUSE_S = 4.5

CATEGORIES = [
    "dump", "building_or_warehouse", "construction_site", "quarry_or_earthworks",
    "storage_yard", "road_or_parking", "field_or_natural", "water_or_wetland", "other",
]

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["dump", "not_dump", "unclear"]},
        "confidence": {"type": "number"},
        "category": {"type": "string", "enum": CATEGORIES},
        "reasoning": {"type": "string"},
    },
    "required": ["verdict", "confidence", "category", "reasoning"],
}

CONTEXT = """You are assisting an environmental inspector near Astana, Kazakhstan \
(dry steppe, industrial outskirts of a large city).

{images}

The RED OUTLINE marks a patch where Sentinel-2 detected that vegetation disappeared \
and did not come back. The question is what this patch is.

Is the outlined patch an unauthorized waste dump?

Signs of a dump: heaps and piles tipped from trucks, irregular mounds, fan-shaped \
tipping ridges, scattered mixed debris of different colours (plastic, household or \
construction waste), tyre tracks leading to the piles, dark stains, a patch that \
grows over the years.

Similar-looking things that are NOT dumps: buildings and warehouses (roofs, regular \
shapes), construction sites (foundations, regular earthworks, formwork), quarries and \
borrow pits (excavation, stepped walls), orderly storage yards (stacked materials, \
containers, pipes in rows), parking lots, roads, agricultural fields, dried ponds and \
wetlands, burned areas.

Judge only the outlined patch. If the imagery is not sharp enough to decide, answer \
"unclear" — do not guess. Give confidence from 0 to 1 and a one-sentence reason in \
Russian."""

SINGLE = ("Image: high-resolution satellite imagery of the place today, about 0.4 m "
          "per pixel, about 290 m across.")
PAIR = ("Image 1: the same place BEFORE the change (imagery release of {before}). "
        "Image 2: the same place TODAY (release of {after}). Both are high-resolution "
        "satellite imagery, about 0.4 m per pixel, about 290 m across.")


def releases() -> list[tuple[str, str]]:
    """Релизы Wayback: (дата, номер), по возрастанию даты."""
    data = json.loads((ROOT / "wayback_releases.json").read_text(encoding="utf-8"))
    return sorted((v["date"], str(v["release"])) for v in data.values())


def _tile_xy(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """Координаты точки в тайлах (дробные), Web Mercator."""
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def fetch(lat: float, lon: float, template: str, key: str):
    """Сетка 3×3 тайлов вокруг точки; из кэша, если уже скачана.

    Возвращает (картинка, зум) или (None, None), если снимка нет.
    """
    import numpy as np
    from PIL import Image
    from review_sheets import looks_like_placeholder

    from vantage.verify import TileProvider, fetch_tile_grid

    IMAGES.mkdir(parents=True, exist_ok=True)
    for zoom in ZOOMS:
        path = IMAGES / f"{lat:.5f}_{lon:.5f}_{key}_z{zoom}.png"
        if path.exists():
            return np.asarray(Image.open(path).convert("RGB")), zoom
    for zoom in ZOOMS:
        provider = TileProvider(name=key, url_template=template, attribution="",
                                source="esri", max_zoom=19)
        try:
            grid = fetch_tile_grid(provider, lat, lon, zoom, GRID, timeout=30)
        except Exception:
            continue
        if looks_like_placeholder(grid):
            continue
        Image.fromarray(grid.astype("uint8")).save(
            IMAGES / f"{lat:.5f}_{lon:.5f}_{key}_z{zoom}.png")
        return grid, zoom
    return None, None


def outline(image, geometry, lat: float, lon: float, zoom: int):
    """Обвести контур объекта красным на копии снимка."""
    import numpy as np
    from PIL import Image, ImageDraw

    cx, cy = _tile_xy(lat, lon, zoom)
    left, top = (int(cx) - GRID // 2) * 256, (int(cy) - GRID // 2) * 256
    picture = Image.fromarray(np.asarray(image, dtype="uint8"))
    draw = ImageDraw.Draw(picture)
    polygons = getattr(geometry, "geoms", [geometry])
    for poly in polygons:
        points = []
        for x, y in poly.exterior.coords:
            tx, ty = _tile_xy(y, x, zoom)
            points.append((tx * 256 - left, ty * 256 - top))
        draw.line(points + points[:1], fill=(255, 40, 40), width=3)
    return np.asarray(picture)


def object_crop(image, lat: float, lon: float, zoom: int, size: int):
    """Квадрат size × size пикселей с центром на объекте.

    Сетка тайлов строится вокруг ТАЙЛА, в котором лежит точка, и объект
    бывает смещён от центра картинки на полтайла. Центральная обрезка
    давала двум соседним объектам одну и ту же картинку — и одну оценку.
    """
    cx, cy = _tile_xy(lat, lon, zoom)
    px = (cx - (int(cx) - GRID // 2)) * 256
    py = (cy - (int(cy) - GRID // 2)) * 256
    h, w = image.shape[:2]
    half = size // 2
    left = int(min(max(px - half, 0), w - size))
    top = int(min(max(py - half, 0), h - size))
    return image[top:top + size, left:left + size]


def prompt_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def screen(frame, variant: str, verifier, *, limit: int | None = None) -> dict[str, dict]:
    """Прогнать объекты через Gemini, вернуть {ключ: ответ}."""
    from vantage.vlm import GeminiQuotaError

    history = releases()
    latest_date, latest_release = history[-1]
    answers: dict[str, dict] = {}
    asked = 0
    for row in frame.itertuples():
        key = str(row.candidate_id)
        point = row.geometry.representative_point()
        lat, lon = point.y, point.x
        now, zoom = fetch(lat, lon, WAYBACK.format(release=latest_release, x="{x}", y="{y}",
                                                    z="{z}"), f"wb{latest_release}")
        if now is None:
            answers[key] = {"verdict": "unclear", "confidence": 0.0, "category": "other",
                            "reasoning": "нет снимка высокого разрешения", "skipped": True}
            continue
        images = [outline(now, row.geometry, lat, lon, zoom)]
        text = CONTEXT.format(images=SINGLE)

        if variant == "pair":
            broke = str(getattr(row, "break_date", "") or "")[:10]
            earlier = [(d, r) for d, r in history if broke and d < broke]
            if earlier:
                before_date, before_release = earlier[-1]
                before, bzoom = fetch(lat, lon, WAYBACK.format(
                    release=before_release, x="{x}", y="{y}", z="{z}"), f"wb{before_release}")
                if before is not None:
                    images = [outline(before, row.geometry, lat, lon, bzoom), images[0]]
                    text = CONTEXT.format(images=PAIR.format(before=before_date[:7],
                                                             after=latest_date[:7]))

        cache = (ANSWERS / variant / verifier.model
                 / f"{key.replace(':', '_')}_{prompt_hash(text)}.json")
        if cache.exists():
            answers[key] = json.loads(cache.read_text(encoding="utf-8"))
            continue
        if limit is not None and asked >= limit:
            break
        try:
            answer = verifier.ask(images, text, SCHEMA)
        except GeminiQuotaError as error:
            print(f"   квота исчерпана на {key}: {str(error)[:120]}")
            break
        except Exception as error:
            print(f"   {key}: {type(error).__name__} {str(error)[:120]}")
            continue
        answer["n_images"] = len(images)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(answer, ensure_ascii=False, indent=1), encoding="utf-8")
        answers[key] = answer
        asked += 1
        print(f"   {key:32} {answer['verdict']:9} {answer.get('confidence', 0):.2f} "
              f"{answer.get('category', ''):22} {str(answer.get('reasoning', ''))[:70]}")
        time.sleep(PAUSE_S)
    return answers


PROTOCOL = RESULTS / "gemini_protocol.json"


def strict_reject(answer: dict, rule: dict) -> bool:
    """Строгий отказ: модель уверена и называет явный предмет.

    «Пустое поле» сюда не входит намеренно: редкий мусор на траве модель
    по снимку пропускает, и именно так облегчённая модель отвергла одну
    свалку в первых 25 объектах. Здание, стройка, дорога и вода видны
    однозначно. Правило записано в data/eval/gemini_protocol.json до
    ответов по остальным объектам.
    """
    return (answer.get("verdict") == rule["verdict"]
            and float(answer.get("confidence", 0)) >= rule["min_confidence"]
            and answer.get("category") in rule["categories"])


def examine_strict(frame, answers: dict[str, dict]) -> dict | None:
    """Строгое правило на части экзамена, которой не было при его выборе."""
    if not PROTOCOL.exists():
        return None
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    dev = set(protocol["dev_ids"])
    rule = protocol["strict_reject"]
    rows = [(row.truth, answers[str(row.candidate_id)]) for row in frame.itertuples()
            if str(row.candidate_id) not in dev and str(row.candidate_id) in answers
            and not answers[str(row.candidate_id)].get("skipped")]
    out = {"objects": len(rows)}
    for name, test in (("any_not_dump", lambda a: a.get("verdict") == "not_dump"),
                       ("strict", lambda a: strict_reject(a, rule))):
        rejected = [(t, a) for t, a in rows if test(a)]
        out[name] = {
            "rejected": len(rejected),
            "workload_removed": round(len(rejected) / len(rows), 3) if rows else 0.0,
            "rejected_landfill": sum(1 for t, _ in rejected if t == "landfill"),
            "rejected_unclear": sum(1 for t, _ in rejected if t == "unclear"),
            "landfills": sum(1 for t, _ in rows if t == "landfill"),
        }
    print()
    print(f"── Часть экзамена вслепую (без {len(dev)} черновых): {len(rows)} объектов")
    for name, title in (("any_not_dump", "любой отказ"), ("strict", "строгий отказ")):
        r = out[name]
        print(f"   {title:14} снимает {r['workload_removed']:.0%}; свалок среди отказов "
              f"{r['rejected_landfill']} из {r['landfills']}, «не разобрать» {r['rejected_unclear']}")
    return out


def examine(frame, answers: dict[str, dict], variant: str) -> dict:
    """Сравнить ответы с человеком по правилу экзамена."""
    from sklearn.metrics import roc_auc_score

    rows = []
    for row in frame.itertuples():
        a = answers.get(str(row.candidate_id))
        if a is None or a.get("skipped"):
            continue
        rows.append((row.area, row.truth, a["verdict"], float(a.get("confidence", 0.0))))
    if not rows:
        return {}

    def summary(subset, name):
        truth = [t for _, t, _, _ in subset]
        verdict = [v for _, _, v, _ in subset]
        matrix = {m: {h: sum(1 for t, v in zip(truth, verdict, strict=True)
                             if t == h and v == m)
                      for h in ("landfill", "not_landfill", "unclear")}
                  for m in ("dump", "unclear", "not_dump")}
        n = len(subset)
        rejected = sum(matrix["not_dump"].values())
        dumps = sum(1 for t in truth if t == "landfill")
        decided = [(t, v, c) for _, t, v, c in subset if t in ("landfill", "not_landfill")]
        score = [c if v == "dump" else (1 - c if v == "not_dump" else 0.5) for _, v, c in decided]
        labels = [1 if t == "landfill" else 0 for t, _, _ in decided]
        auc = (float(roc_auc_score(labels, score))
               if 0 < sum(labels) < len(labels) else None)
        return {
            "name": name, "objects": n, "matrix": matrix,
            "workload_removed": round(rejected / n, 3) if n else 0.0,
            "rejected": rejected,
            "rejected_landfill": matrix["not_dump"]["landfill"],
            "rejected_unclear": matrix["not_dump"]["unclear"],
            "dumps": dumps, "dumps_called_dump": matrix["dump"]["landfill"],
            "negatives_called_dump": matrix["dump"]["not_landfill"],
            "roc_auc": round(auc, 3) if auc is not None else None,
        }

    result = {
        "generated": date.today().isoformat(),
        "variant": variant,
        "model": getattr(frame, "attrs", {}).get("model"),
        "all": summary(rows, "все области"),
        "north": summary([r for r in rows if r[0] == "outputs_real"], "северное кольцо"),
    }
    for part in (result["all"], result["north"]):
        m = part["matrix"]
        print(f"\n── {variant}, {part['name']}: {part['objects']} объектов")
        print(f"   {'Gemini ↓ / человек →':24} {'свалка':>8} {'не свалка':>10} {'не разобрать':>13}")
        for k, title in (("dump", "свалка"), ("unclear", "не разобрать"), ("not_dump", "не свалка")):
            print(f"   {title:24} {m[k]['landfill']:>8} {m[k]['not_landfill']:>10} "
                  f"{m[k]['unclear']:>13}")
        print(f"   снимает работы {part['workload_removed']:.0%}; свалок среди отказов "
              f"{part['rejected_landfill']} из {part['dumps']}; ROC-AUC {part['roc_auc']}")
    return result


def main() -> int:
    import geopandas as gpd
    from dotenv import load_dotenv

    from vantage.vlm import GeminiVlmVerifier

    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=("single", "pair"), default="pair")
    parser.add_argument("--target", choices=("eval", "site"), default="eval")
    parser.add_argument("--limit", type=int, default=None,
                        help="не больше стольких новых запросов (для пробы)")
    # Бесплатный тариф считает квоту по каждой модели отдельно: у основной
    # flash — 20 запросов в день, у облегчённых больше. Экзамен всегда
    # идёт одной моделью: смесь ответов разных моделей ничего не мерит.
    parser.add_argument("--model", default=None)
    # Только эти объекты (через запятую): при 20 запросах в день на
    # основную модель квоту тратят на самые важные объекты, а не по порядку.
    parser.add_argument("--only", default=None)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    verifier = GeminiVlmVerifier(model=args.model) if args.model else GeminiVlmVerifier()
    if not verifier.available:
        print("нет GEMINI_API_KEY в окружении или .env")
        return 1

    if args.target == "eval":
        frame = gpd.read_file(EVAL)
    else:
        frame = gpd.read_file(SITE).to_crs(4326)
        frame["candidate_id"] = "site:" + frame["candidate_id"].astype(str)
    if args.only:
        wanted = set(args.only.split(","))
        frame = frame[frame["candidate_id"].astype(str).isin(wanted)]
    print(f"── Gemini ({verifier.model}), вариант {args.variant}: {len(frame)} объектов")
    answers = screen(frame, args.variant, verifier, limit=args.limit)

    if args.target == "eval":
        frame.attrs["model"] = verifier.model
        result = examine(frame, answers, args.variant)
        result["blind"] = examine_strict(frame, answers)
        RESULTS.mkdir(parents=True, exist_ok=True)
        out = RESULTS / f"gemini_{args.variant}_{verifier.model}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n── записано в {out.relative_to(ROOT)}")
    else:
        out = ANSWERS / f"site_{args.variant}_{verifier.model}.json"
        out.write_text(json.dumps(answers, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n── ответы по сайту: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
