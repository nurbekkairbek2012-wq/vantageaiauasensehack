"""Точка входа: python -m vantage.bot"""

from __future__ import annotations

import logging
import sys

from ..config import load_settings
from .app import BotContext, build_application, subscribers_from_env
from .reports import ReportStore


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("vantage.bot")

    settings = load_settings()
    outputs = settings.paths.resolve("outputs")

    candidates = None
    candidates_path = outputs / "candidates.geojson"
    if candidates_path.exists():
        import geopandas as gpd

        candidates = gpd.read_file(candidates_path)
        log.info("Загружено кандидатов для сопоставления: %d", len(candidates))
    else:
        log.warning(
            "Файл %s не найден — сообщения жителей будут приниматься, "
            "но сопоставить их со спутником пока не с чем.",
            candidates_path,
        )

    context = BotContext(
        store=ReportStore(outputs / "citizen_reports.jsonl"),
        candidates=candidates,
        subscribers=subscribers_from_env(),
    )

    try:
        application = build_application(context)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    log.info("Бот запущен. Подписчиков на оповещения: %d", len(context.subscribers))
    application.run_polling()
    return 0


if __name__ == "__main__":
    sys.exit(main())
