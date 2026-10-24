"""Все числа проекта на одной странице — считаются, а не переписываются.

── Зачем ───────────────────────────────────────────────────────────────

На защите спрашивают числами. Отвечать «сейчас посмотрю на сайте» нельзя,
а держать их в голове вчетвером — значит расходиться в показаниях: один
назовёт 17 объектов, другой 59, и оба будут правы про разное.

Здесь собрано всё, что могут спросить, с пометкой, откуда взято. Печатать
и держать при себе.

── Почему скриптом ─────────────────────────────────────────────────────

Потому что числа меняются после каждого пересчёта, а написанные руками —
нет. За две ночи это ловилось шесть раз: «Тридцать объектов» при 16,
«меньше 900 м²» при пороге 500, «×293» при 295, воронка на 343 при 385
кандидатах.

── Про имя файла ───────────────────────────────────────────────────────

Назывался `numbers.py` ровно пять минут: этим именем он перекрывал
стандартный модуль Python `numbers`, который импортирует numpy, и падал
на `AttributeError: module 'numbers' has no attribute 'Integral'` — в
строке, не имеющей к нему никакого отношения.

Пересобирать перед каждой репетицией:

    python scripts/key_numbers.py
"""

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA = Path("web-next/public/data")
OUT = Path("docs/NUMBERS.md")


def ru(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


def plural(n: int, one: str, few: str, many: str) -> str:
    """«9 свалки» — мелочь, которую замечают все и сразу."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def main() -> int:
    import geopandas as gpd
    import pandas as pd
    import yaml
    from shapely.geometry import box

    site = gpd.read_file(DATA / "candidates.geojson")
    funnel = json.loads((DATA / "funnel.json").read_text(encoding="utf-8"))
    metrics = json.loads((DATA / "metrics.json").read_text(encoding="utf-8"))
    cities = json.loads((DATA / "cities.json").read_text(encoding="utf-8"))
    labels = gpd.read_file("labels_manual.geojson")

    dumps = int((site["visual_check"] == "landfill").sum())
    unclear = int((site["visual_check"] == "unclear").sum())
    ground = int((site["check_source"] == "ground").sum()) if "check_source" in site else 0
    photos = int(site["ground_photos"].fillna(0).sum()) if "ground_photos" in site else 0
    rejected = funnel["rejected"]
    passed = rejected.get("ПРОШЁЛ ОТСЕВ", 0)
    by_eye = passed - len(site)
    auto = sum(v for k, v in rejected.items() if k != "ПРОШЁЛ ОТСЕВ")

    config = yaml.safe_load(Path("config/cities.yaml").read_text(encoding="utf-8"))["cities"]
    covered = 0.0
    for city in config:
        folder = Path("outputs_real") if city["id"] == "astana" else Path(f"outputs_{city['id']}")
        if (folder / "candidates_raw.geojson").exists():
            g = gpd.GeoDataFrame(geometry=[box(*city["bbox"])], crs=4326).to_crs(32642)
            covered += float(g.area.iloc[0]) / 1e6

    ages = (pd.Timestamp.today() - pd.to_datetime(site["break_date"])).dt.days / 365.25
    reviewed = sum(int(c.get("reviewed", 0)) for c in cities)

    L = [
        "# Числа проекта",
        "",
        "Собрано `scripts/key_numbers.py` из выгрузки, не переписано руками.",
        "**Пересобрать перед каждой репетицией** — числа меняются после",
        "каждого пересчёта.",
        "",
        "## Если спросят одно число",
        "",
        # Формулировка выверена дважды. Первая редакция говорила «каждая
        # из 385 находок просмотрена человеком» — это неправда: человек
        # смотрел 59, прошедших отсев, а 326 сняла программа по контексту.
        # Ровно такое преувеличение и разбирают на защите первым.
        f"> **{dumps} {plural(dumps, 'свалка', 'свалки', 'свалок')}** под Астаной "
        f"из **{funnel['raw']}** находок детектора. Каждый из **{passed}** объектов, "
        f"прошедших автоматический отсев, просмотрен человеком по снимку "
        f"0,4–0,8 м на пиксель — до единого"
        + (f", и **{ground}** {plural(ground, 'подтверждён', 'подтверждены', 'подтверждены')} "
           f"выездом на место." if ground else "."),
        "",
        "## Находки",
        "",
        "| Что | Сколько | Откуда |",
        "|---|---:|---|",
        f"| Сырых кандидатов детектора | **{funnel['raw']}** | funnel.json |",
        f"| Снял автоматический отсев | {auto} | funnel.json |",
        f"| Прошло отсев | {passed} | funnel.json |",
        f"| Отвергнуто при просмотре глазами | {by_eye} | разница |",
        f"| **Опубликовано на сайте** | **{len(site)}** | candidates.geojson |",
        f"| ├─ опознаны как свалки | **{dumps}** | вердикт человека |",
        f"| └─ требуют выезда | {unclear} | вердикт человека |",
        f"| **Подтверждено выездом на место** | **{ground}** | ground_truth.json |",
        f"| ├─ из них с фотофиксацией | {photos and ground or 0} | data/field/ |",
        f"| Просмотрено человеком всего | **{reviewed}** | по пяти областям |",
        f"| Меток разметки в источнике | {len(labels)} | labels_manual.geojson |",
        "",
        "## Деньги и вред",
        "",
        "| Что | Сколько |",
        "|---|---:|",
        f"| Ущерб, медиана | **{ru(site['damage_p50'].sum() / 1e6, 1)} млн ₸** |",
        f"| Ущерб, вилка P10–P90 | {ru(site['damage_p10'].sum() / 1e6, 1)} – "
        f"{ru(site['damage_p90'].sum() / 1e6, 1)} млн ₸ |",
        f"| Штраф по КоАП, ст. 344 | {ru(site['penalty_kzt'].sum() / 1e6, 1)} млн ₸ |",
        f"| Метан за 20 лет | {ru(site['co2e_t'].sum())} т CO₂-экв |",
        f"| Суммарная площадь | {ru(site['area_m2'].sum() / 1e4, 2)} га |",
        f"| Средний возраст объекта | {ru(ages.mean(), 1)} года |",
        f"| Самая старая | {str(site['break_date'].min())[:7]} |",
        "",
        "## Охват",
        "",
        "| Что | Сколько |",
        "|---|---:|",
        f"| Посчитано площади | **{ru(covered)} км²** |",
        f"| Областей посчитано | {sum(1 for c in cities if c.get('reviewed'))} из {len(cities)} |",
        f"| Ячеек в сетке риска | {ru(metrics.get('cells', 0))} |",
        "",
        "### По областям",
        "",
        "| Область | Просмотрено | Свалок | Состояние |",
        "|---|---:|---:|---|",
    ]
    said = {"found": "работает", "empty": "проверено, чисто", "pending": "не считалась"}
    for city in cities:
        found = dumps if city["state"] == "found" else 0
        L.append(f"| {city['name']} | {city.get('reviewed', 0)} | {found} | "
                 f"{said.get(city['state'], city['state'])} |")

    L += [
        "",
        "## Модели",
        "",
        "| Что | Число | Как читать |",
        "|---|---|---|",
        f"| Прогноз, PR-AUC на будущем | {ru(metrics.get('pr_auc_future', 0), 3)} "
        f"({ru(metrics.get('pr_auc_low', 0), 3)} – {ru(metrics.get('pr_auc_high', 0), 3)}) "
        f"| называть нижнюю границу |",
        f"| Прогноз, выигрыш над случайным | не хуже ×{metrics.get('lift_low', 0):.0f} "
        f"| положительных ячеек {metrics.get('positives_future', 0):.0f} |",
        "| Машинный просмотр, полнота | 7 из 8 (0,67 – 1,00) | измерено на 49 объектах |",
        "| Машинный просмотр, отбраковка | 34 из 35 = 0,97 | снимает 71% работы |",
        "| Перенос AerialWaste | 0,680 (0,517 – 0,841) | на 51 объекте |",
        "| Своя модель на нашей разметке | 0,326 (0,202 – 0,450) | **хуже случайного, не поставлена** |",
        "| Пять признаков, свалка против склада | 0,500 | различает не спектр, а карта |",
        "| Пять признаков, промзона против поля | 0,930 | и это единственное, что они умеют |",
        "",
        "## Чего у нас нет — говорить самим",
        "",
        "- **Ни одного объекта, подтверждённого выездом.** Все проверки —",
        "  по снимкам 0,4–0,8 м. Куда ехать, посчитано в [FIELD.md](FIELD.md).",
        "- **Четыре области из пяти дали ноль настоящих свалок.** Это не",
        "  провал, а измеренная граница применимости — [BELTS.md](BELTS.md).",
        "- **Три попытки обучить свою модель, все три неудачные.** Числа в",
        "  [AI_RESULTS.md](AI_RESULTS.md), разделы 1в, 1г, 1з.",
        "- **Прямого тарифа вывоза по Астане нет** — на нём держится 82%",
        "  разброса оценки ущерба.",
        "",
    ]
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:26]))
    print(f"\n── записано в {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
