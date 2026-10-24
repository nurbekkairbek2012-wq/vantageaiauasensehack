"""Доверификация по снимкам высокого разрешения — отдельным шагом, с кэшем.

── Зачем отдельно ──────────────────────────────────────────────────────

Доверификация живёт внутри finish_ring и выключается флагом --no-verify.
Флаг задумывался как «не тянуть тайлы повторно», а работает как «стереть
то, что было»: пересчёт без него переписывает candidates.geojson без
колонок verify_*, и подтверждение двумя независимыми источниками пропадает
молча. Ровно так оно и пропало в ночь на 22 августа — прогон с --no-verify
запустили следом за полным, и 21 объект остался без подтверждения.

Тот же класс ошибки, из-за которого разметку глазами приходится
переносить ПОСЛЕ выгрузки: шаг, переписывающий файл целиком, уничтожает
чужие колонки, не заметив этого.

── Почему с кэшем ──────────────────────────────────────────────────────

Тайлы высокого разрешения тянутся с чужих серверов с паузой между
запросами: два десятка объектов — это минуты, а поставщик ограничивает
частоту. Результат по объекту не меняется от того, что рядом пересчитали
деньги, поэтому он сохраняется и переиспользуется. Ключ — идентификатор
кандидата вместе с координатой: сдвинулся объект — считаем заново.

    python scripts/attach_verify.py [--refresh]
"""

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("verify")

OUTPUTS = Path("outputs_real")
CANDIDATES = OUTPUTS / "candidates.geojson"
WEB = Path("web-next/public/data/candidates.geojson")
CACHE = OUTPUTS / "verification.json"


def key(row) -> str:
    """Идентификатор плюс координата: сдвинувшийся объект считается заново."""
    point = row.geometry.centroid
    return f"{row.candidate_id}@{point.x:.1f},{point.y:.1f}"


def main() -> int:
    import geopandas as gpd

    from vantage import env
    from vantage.config import load_settings
    from vantage.verify import verify_candidates
    from vantage.vlm import build_verifier

    if not CANDIDATES.exists():
        log.error("нет %s — сначала прогон", CANDIDATES)
        return 1

    env.configure()
    settings = load_settings()
    kept = gpd.read_file(CANDIDATES)
    if kept.empty:
        log.error("кандидатов нет")
        return 1

    refresh = "--refresh" in sys.argv
    cached: dict = {}
    if CACHE.exists() and not refresh:
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        log.info("в кэше записей: %d", len(cached))

    keys = {row.candidate_id: key(row) for row in kept.itertuples()}
    todo = kept[~kept["candidate_id"].map(lambda cid: keys[cid] in cached)]
    log.info("к проверке: %d из %d", len(todo), len(kept))

    if not todo.empty:
        results = verify_candidates(todo, settings.verify, vlm=build_verifier())
        for result in results:
            cached[keys[result.candidate_id]] = {
                "n_sources": result.n_sources,
                "n_providers": result.n_providers,
                "scores": result.scores,
                "confirmed": bool(result.is_confirmed(settings.verify)),
            }
        CACHE.write_text(json.dumps(cached, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("кэш обновлён: %s", CACHE)

    # Колонки проставляются из кэша, а не из results: часть объектов взята
    # из прошлых прогонов, и смешивать два источника значений нельзя.
    import numpy as np

    def field(cid: str, name: str, default):
        entry = cached.get(keys.get(cid, ""), None)
        return entry[name] if entry else default

    kept["verify_providers"] = kept["candidate_id"].map(lambda c: field(c, "n_sources", 0))
    kept["verify_confirmed"] = kept["candidate_id"].map(lambda c: field(c, "confirmed", False))
    kept["verify_texture"] = kept["candidate_id"].map(
        lambda c: float(np.mean(list(field(c, "scores", {}).values()) or [0.0]))
    )

    confirmed = int(kept["verify_confirmed"].sum())
    log.info("подтверждены двумя независимыми источниками: %d из %d", confirmed, len(kept))

    kept.to_file(CANDIDATES, driver="GeoJSON")
    if WEB.parent.exists():
        kept.to_file(WEB, driver="GeoJSON")
        log.info("обновлено: %s и %s", CANDIDATES, WEB)

    # Явное предупреждение вместо тихого нуля: пустая доверификация
    # выглядит на сайте так же, как «не проверяли», и заметить её иначе
    # нельзя.
    if confirmed == 0:
        log.warning("НИ ОДНОГО подтверждения — проверьте доступность тайлов")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
