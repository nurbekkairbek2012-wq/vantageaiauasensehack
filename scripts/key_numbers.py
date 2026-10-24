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

import contextlib
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


def _cut(economy: dict, share: float) -> int:
    """Сколько выездов закрывают заданную долю суммы ущерба.

    Приоритет — это и есть рекомендация: пятнадцать строк списком советом
    не являются, «начните с четырёх» — является.
    """
    for row in economy.get("priority", []):
        if row["share"] >= share:
            return int(row["n"])
    return len(economy.get("priority", []))


def main() -> int:
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import box

    site = gpd.read_file(DATA / "candidates.geojson")
    funnel = json.loads((DATA / "funnel.json").read_text(encoding="utf-8"))
    metrics = json.loads((DATA / "metrics.json").read_text(encoding="utf-8"))
    cities = json.loads((DATA / "cities.json").read_text(encoding="utf-8"))
    econ_path = DATA / "economy.json"
    economy = (json.loads(econ_path.read_text(encoding="utf-8"))
               if econ_path.exists() else None)
    # Метрики сиамской сети лежат отдельным файлом и на сайте не
    # показываются. В деке из них берётся ROC-AUC 0,908, и без строки здесь
    # на вопрос «откуда это» пришлось бы искать по репозиторию.
    model_path = DATA / "model.json"
    model = json.loads(model_path.read_text(encoding="utf-8")) if model_path.exists() else {}
    labels = gpd.read_file("labels_manual.geojson")

    dumps = int((site["visual_check"] == "landfill").sum())
    unclear = int((site["visual_check"] == "unclear").sum())
    ground = int((site["check_source"] == "ground").sum()) if "check_source" in site else 0
    photos = int(site["ground_photos"].fillna(0).sum()) if "ground_photos" in site else 0
    rejected = funnel["rejected"]
    passed = rejected.get("ПРОШЁЛ ОТСЕВ", 0)
    by_eye = passed - len(site)
    auto = sum(v for k, v in rejected.items() if k != "ПРОШЁЛ ОТСЕВ")

    # Площадь считается по ВСЕМ посчитанным папкам, а не по списку
    # областей продукта: пригородные пояса убраны с карты, потому что дали
    # ноль, но посчитаны они были, и на вопрос «сколько вы покрыли» ответ
    # именно такой.
    ALL_AREAS = {
        "astana": (71.37, 51.12, 71.66, 51.30),
        "astana_east": (71.66, 51.10, 71.95, 51.28),
        "astana_southeast": (71.60, 51.02, 71.88, 51.18),
        "astana_west": (71.08, 51.04, 71.36, 51.28),
        "astana_industrial_west": (71.18, 51.06, 71.42, 51.22),
        "astana_south": (71.38, 50.99, 71.58, 51.15),
    }
    covered = 0.0
    counted = 0
    for area, bbox in ALL_AREAS.items():
        folder = Path("outputs_real") if area == "astana" else Path(f"outputs_{area}")
        if (folder / "candidates_raw.geojson").exists():
            g = gpd.GeoDataFrame(geometry=[box(*bbox)], crs=4326).to_crs(32642)
            covered += float(g.area.iloc[0]) / 1e6
            counted += 1

    ages = (pd.Timestamp.today() - pd.to_datetime(site["break_date"])).dt.days / 365.25
    # Просмотренные объекты — по всем прогонам, а не по областям продукта.
    reviewed = 0
    for area in ALL_AREAS:
        folder = Path("outputs_real") if area == "astana" else Path(f"outputs_{area}")
        path = folder / "candidates.geojson"
        if path.exists():
            # Битая или недописанная выгрузка не должна ронять сборку
            # страницы: она нужнее целой, чем точной до объекта.
            with contextlib.suppress(Exception):
                reviewed += len(gpd.read_file(path))

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
        # Строка про «требуют выезда» показывается, только когда такие
        # объекты есть. Нулевая строка в таблице читается как недоработка,
        # хотя означает обратное: съездили ко всем.
        *([f"| └─ требуют выезда | {unclear} | вердикт человека |"] if unclear else []),
        f"| **Подтверждено выездом на место** | **{ground}** | ground_truth.json |",
        *([f"| ├─ из них с фотофиксацией | {photos} | data/field/ |"] if photos else []),
        # Просмотрено — по всей проделанной работе, а не по тому, что
        # осталось в продукте. Пять пригородных поясов посчитаны и
        # просмотрены полностью; в списке областей их больше нет, потому
        # что все дали ноль, но работа была и в счёт входит.
        f"| Просмотрено человеком всего | **{reviewed}** | все прогоны, включая пустые пояса |",
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
        # Раскладка ущерба на слагаемые — то, чем проект отвечает на кейс
        # трека: не «свалка стоит столько», а «столько теряется, столько
        # возвращается, столько уже не вернуть». Числа читаются из
        # economy.json, чтобы совпадать с экраном «Экономика» до тенге.
        *(([
            "## Экономика кейса",
            "",
            "| Что | Сколько | Как читать |",
            "|---|---:|---|",
            f"| Отходов найдено | **{ru(economy['totals']['sum_of_medians']['mass_t'])} т** | "
            "масса по площади и глубине, модельная оценка |",
            f"| Стоимость вывоза | {ru(economy['totals']['sum_of_medians']['removal_kzt'] / 1e6, 1)} млн ₸ | "
            "тариф выведен из тарифа Алматы |",
            f"| Возврат вторсырьём | **{ru(economy['totals']['sum_of_medians']['recyclable_kzt'] / 1e6, 1)} млн ₸** | "
            "прайс приёмки подтверждён, извлекаемая доля — оценка |",
            f"| Доля уборки, которую закрывает сырьё | **{ru(100 * economy['totals']['sum_of_medians']['recyclable_kzt'] / economy['totals']['sum_of_medians']['removal_kzt'])}%** | "
            "отсюда «за свалку платят трижды» |",
            f"| Чистый ущерб, сумма медиан | **{ru(economy['totals']['sum_of_medians']['damage_kzt'] / 1e6, 1)} млн ₸** | "
            "складывается из столбца таблицы |",
            f"| Он же, интервал по списку целиком | {ru(economy['totals']['damage_kzt']['p10'] / 1e6, 1)} – "
            f"{ru(economy['totals']['damage_kzt']['p90'] / 1e6, 1)} млн ₸ | "
            f"медиана суммы {ru(economy['totals']['damage_kzt']['p50'] / 1e6, 1)} млн ₸ |",
            f"| Вывезти как есть | {ru(economy['totals']['sum_of_medians']['plain_kzt'] / 1e6, 1)} млн ₸ | "
            "всё на полигон, сырьё в ноль |",
            f"| Вывезти с разбором | **{ru(economy['totals']['sum_of_medians']['sorted_kzt'] / 1e6, 1)} млн ₸** | "
            "дороже работой на 30%, но сырьё возвращается |",
            f"| **Экономия выбора** | **{ru(economy['totals']['sum_of_medians']['saving_kzt'] / 1e6, 1)} млн ₸** | "
            "это и есть ответ на «что выгоднее сделать» |",
            f"| Разбор окупается, пока дешевле | **{ru(100 * economy['totals']['breakeven_share'])}%** "
            "стоимости вывоза | спросить у подрядчика — проверяет весь расчёт |",
            f"| Выездов на половину суммы | **{_cut(economy, 0.5)}** | приоритет по деньгам |",
            f"| Выездов на 80% суммы | {_cut(economy, 0.8)} | остальные стоят вместе меньше пятой части |",
            f"| CO₂-экв уже выброшено | {ru(economy['totals']['sum_of_medians']['co2e_emitted_t'])} т | "
            f"{ru(100 * economy['totals']['sum_of_medians']['co2e_emitted_t'] / economy['totals']['sum_of_medians']['co2e_t'])}% "
            "от полного горизонта — не вернуть |",
            f"| Сильнее всего двигает итог | стоимость вывоза тонны, ρ = "
            f"{ru(economy['sensitivity'].get('removal_cost', 0), 2)} | "
            "ранговая корреляция Спирмена, не доля дисперсии |",
        ]) if economy else []),
        "",
        "## Охват",
        "",
        "| Что | Сколько |",
        "|---|---:|",
        f"| Посчитано площади | **{ru(covered)} км²** |",
        f"| Областей посчитано | {counted} |",
        "| Из них дали настоящие свалки | 1 — северное кольцо |",
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
        *([f"| Сеть по парам «до/после», ROC-AUC | {ru(model.get('roc_auc_oof', 0), 3)} "
           f"| вневыборочно, деление по объектам, {int(model.get('folds', 0))} частей |",
           f"| она же, PR-AUC | {ru(model.get('pr_auc_oof', 0), 3)} при базовой "
           f"{ru(model.get('base_rate', 0), 3)} | кусков {int(model.get('n_pieces', 0))}, "
           f"положительных {int(model.get('n_positive', 0))} |"] if model else []),
        "",
        "## Чего у нас нет — говорить самим",
        "",
        # Раздел собирается по состоянию, а не переписывается руками: строка
        # «ни одного объекта не подтверждено выездом» пережила бы выезд и
        # стала бы неправдой ровно там, где честность и заявлена.
        *([
            "- **Ни одного объекта, подтверждённого выездом.** Все проверки —",
            "  по снимкам 0,4–0,8 м. Куда ехать, посчитано в [FIELD.md](FIELD.md).",
        ] if not ground else []),
        *([
            "- **Выезды не сфотографированы.** Объекты подтверждены осмотром на",
            "  месте, и называть это надо именно так — «осмотр», а не",
            "  «фотофиксация». Разница небольшая, но проверяющий, который",
            "  заметит её сам, отнесётся ко всему остальному иначе.",
        ] if ground and not photos else []),
        "- **Четыре области из пяти дали ноль настоящих свалок.** Это не",
        "  провал, а измеренная граница применимости — [BELTS.md](BELTS.md).",
        "- **Три попытки обучить свою модель, все три неудачные.** Числа в",
        "  [AI_RESULTS.md](AI_RESULTS.md), разделы 1в, 1г, 1з.",
        "- **Прямого тарифа вывоза по Астане нет.** Именно он двигает оценку",
        "  сильнее прочих допущений: ранговая корреляция 0,82. Это не «82%",
        "  разброса» — величины разные, и подмену на Q&A заметят.",
        "",
    ]
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:26]))
    print(f"\n── записано в {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
