"""Оставить в выгрузке только объекты, похожие на свалки.

── Зачем ───────────────────────────────────────────────────────────────

На сайт уезжали все находки детектора, а отвергнутые проверкой были
помечены надписью «не свалка». Заказчик решил их убирать: пометка не
приносит пользы посетителю, а список, где две трети объектов —
склады, читается как список складов.

Возражение, высказанное и отклонённое, записываю здесь, чтобы оно не
потерялось: удалённая ошибка неотличима от её отсутствия, и раздел «мы
отвергли N собственных находок» работал на доверие. Взамен на лендинге
остаётся воронка — она показывает тот же отсев числами, не засоряя
рабочий список.

── Откуда берутся вердикты ─────────────────────────────────────────────

Из двух источников, и приоритет у человека:

    visual_check     — разметка человеком через label.html;
    labels_ai_screen — просмотр моделью и мной по снимкам 0,5 м/пиксель.

Человеческий вердикт всегда сильнее машинного: если человек сказал
«свалка», объект остаётся, даже когда просмотр говорит обратное.
Обратное тоже верно.

Объект публикуется, если ХОТЯ БЫ ОДИН источник считает его свалкой или
сомневается. Убирается, только когда оба согласны, что это не свалка,
либо когда единственный имеющийся источник говорит «нет».

── Что НЕ выбрасывается ────────────────────────────────────────────────

Объекты без единого вердикта остаются. Неразмеченный кандидат — это не
«не свалка», это «ещё не смотрели», и молча выкидывать такое значит
терять находки на новой территории до того, как их кто-то увидел.

    python scripts/publish_filter.py [--outputs outputs_real]
"""

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("publish")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCREEN = Path("labels_ai_screen.json")
WEB = Path("web-next/public/data/candidates.geojson")

#: Машинные вердикты, при которых объект остаётся.
KEEP_SCREEN = {"dump", "maybe"}

#: Человеческие вердикты, при которых объект остаётся.
KEEP_HUMAN = {"landfill", "unclear"}


def load_screen() -> dict[str, str]:
    if not SCREEN.exists():
        return {}
    data = json.loads(SCREEN.read_text(encoding="utf-8"))
    return {k: v["verdict"] for k, v in (data.get("screen") or {}).items()}


def rebuild_story(kept) -> None:
    """Пересобрать сценарий демонстрации под опубликованный список.

    Сценарий строится на выгрузке, до фильтра. Фильтр правил только
    candidates.geojson, и story.json оставался от полного прогона: на
    странице таймлапса стояло «Мы нашли 49 объектов» при шестнадцати на
    карте, а сцена «доказательная цепочка» наводилась на C00018 — объект,
    который фильтр к тому моменту снял. Наведение на несуществующий объект
    ничем не выдавало себя: сцена просто открывалась на пустом месте.

    Это тот же тихий откат, что уже ловился дважды: шаг, стоящий последним,
    правит один файл из нескольких связанных.
    """
    import json as _json

    import geopandas as gpd

    from vantage.story import build_story

    story_path = Path(WEB).parent / "story.json"
    before = None
    if story_path.exists():
        try:
            before = _json.loads(story_path.read_text(encoding="utf-8"))
        except Exception:
            before = None

    registry_count = 0
    registry_path = Path(WEB).parent / "registry.geojson"
    if registry_path.exists():
        try:
            registry_count = len(gpd.read_file(registry_path))
        except Exception:
            registry_count = 0

    story = build_story(kept, registry_count=registry_count,
                        is_demo=bool(before and before.get("is_demo")))
    story_path.write_text(_json.dumps(story, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    said = next((s["line"] for s in story["scenes"] if s["id"] == "found"), "")
    log.info("сценарий пересобран: %s", said)


def main() -> int:
    import geopandas as gpd

    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", default="outputs_real")
    args = parser.parse_args()

    source = Path(args.outputs) / "candidates.geojson"
    if not source.exists():
        log.error("нет %s", source)
        return 1

    data = gpd.read_file(source)
    screen = load_screen()
    log.info("объектов в прогоне: %d, машинных вердиктов: %d", len(data), len(screen))

    human = data["visual_check"] if "visual_check" in data.columns else None

    def keep(row) -> bool:
        cid = str(row["candidate_id"])
        h = str(row["visual_check"]) if human is not None and row["visual_check"] else None
        s = screen.get(cid)
        # Человек сказал «свалка» или «не разобрать» — оставляем.
        if h in KEEP_HUMAN:
            return True
        # Человек сказал «не свалка» — убираем, даже если просмотр спорит.
        if h == "not_landfill":
            return False
        if s is not None:
            return s in KEEP_SCREEN
        # Никто не смотрел — оставляем: «не смотрели» это не «не свалка».
        return True

    mask = data.apply(keep, axis=1)
    kept = data[mask].copy()

    # Машинный вердикт уезжает на сайт вместе с объектом: карточка должна
    # честно показывать, чем подкреплено присутствие объекта в списке.
    kept["screen"] = kept["candidate_id"].map(lambda c: screen.get(str(c)))

    # Вердикт просмотра подставляется туда, где человек ещё не смотрел, —
    # иначе сайт показывает «подтверждено 0» при четырнадцати проверенных
    # объектах.
    #
    # Источник проверки при этом сохраняется отдельным полем и НЕ
    # смешивается. Просмотр по снимку 0,5 м — это не выезд и не подпись
    # инспектора; выдавать одно за другое нельзя, а на защите спросят
    # именно «кто смотрел».
    same = {"dump": "landfill", "maybe": "unclear"}
    kept["check_source"] = kept.apply(
        lambda r: "human" if r.get("visual_check") else ("screen" if r["screen"] else None),
        axis=1,
    )
    kept["visual_check"] = kept.apply(
        lambda r: r["visual_check"] if r.get("visual_check") else same.get(r["screen"]),
        axis=1,
    )

    removed = len(data) - len(kept)
    log.info("оставлено %d, убрано %d", len(kept), removed)
    if "visual_check" in kept.columns:
        import collections
        log.info("   вердикты человека: %s",
                 dict(collections.Counter(str(v) for v in kept["visual_check"])))
    log.info("   вердикты просмотра: %s",
             dict(__import__("collections").Counter(str(v) for v in kept["screen"])))

    if kept.empty:
        log.error("после фильтра не осталось объектов — выгрузка не тронута")
        return 1

    WEB.parent.mkdir(parents=True, exist_ok=True)
    kept.to_file(WEB, driver="GeoJSON")
    log.info("выгружено: %s", WEB)

    rebuild_story(kept)
    return 0


if __name__ == "__main__":
    sys.exit(main())
