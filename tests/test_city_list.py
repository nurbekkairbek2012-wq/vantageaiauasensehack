"""Переключатель областей обязан описывать ровно то, что лежит на карте.

Кнопка с числом — это обещание: нажми и увидишь столько объектов. Пока
областей было три и границы не пересекались, обещание выполнялось само
собой. Теперь их пять, они перекрываются по краям, и две из них проверены
и пусты — каждое из этих обстоятельств однажды уже дало неверную кнопку.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]




class TestCityListMatchesTheSite:
    """Переключатель областей обязан описывать ровно то, что на карте.

    Кнопка с числом — это обещание: нажми и увидишь столько объектов.
    Границы областей перекрываются (северное кольцо и юго-восточный пояс
    делят полосу в 6 км), и объект из полосы засчитывался обеим. На кнопке
    «Юго-восток» стояла единица при нуле настоящих находок из 63
    просмотренных.
    """

    def test_counts_add_up_to_what_is_published(self):
        """Сумма по областям равна числу объектов на сайте.

        Больше — значит кто-то посчитан дважды. Меньше — значит объект
        лежит вне всех областей, и на карте его никто не найдёт.
        """
        import json

        import geopandas as gpd

        cities_path = ROOT / "web-next/public/data/cities.json"
        published_path = ROOT / "web-next/public/data/candidates.geojson"
        if not (cities_path.exists() and published_path.exists()):
            pytest.skip("нет выгрузки")

        cities = json.loads(cities_path.read_text(encoding="utf-8"))
        published = gpd.read_file(published_path)
        total = sum(int(c["count"]) for c in cities)
        assert total == len(published), (
            f"по областям {total}, на сайте {len(published)} — "
            "объекты считаются дважды либо теряются между областями"
        )

    def test_empty_areas_are_not_called_unchecked(self):
        """Проверенная пустая область не должна выглядеть как непосчитанная.

        «Не проверяли» — дыра в охвате. «Проверили, чисто» — работающая
        система. Путать их значит отдавать сильную сторону за слабую.
        """
        import json

        cities_path = ROOT / "web-next/public/data/cities.json"
        if not cities_path.exists():
            pytest.skip("нет выгрузки")

        for city in json.loads(cities_path.read_text(encoding="utf-8")):
            if city.get("reviewed"):
                assert city.get("state") != "pending", (
                    f"{city['name']}: просмотрено {city['reviewed']} находок, "
                    "а область помечена как непосчитанная"
                )
            if city["count"] > 0:
                assert city.get("state") == "found", f"{city['name']}: объекты есть, состояние не found"

    def test_short_label_survives(self):
        """Короткая подпись обязательна: без неё ряд кнопок рвётся на телефоне.

        Поле уже терялось однажды при пересборке списка, и заметно это
        было только на экране 360 px.
        """
        import json

        cities_path = ROOT / "web-next/public/data/cities.json"
        if not cities_path.exists():
            pytest.skip("нет выгрузки")

        for city in json.loads(cities_path.read_text(encoding="utf-8")):
            short = city.get("short", "")
            assert short and len(short) <= 12, (
                f"{city['id']}: подпись {short!r} — пустая или длиннее 12 знаков"
            )


class TestFunnelAddsUp:
    """Столбцы воронки обязаны давать в сумме число сырых кандидатов.

    Несходящаяся сумма на лендинге — первое, что считает проверяющий, и
    после этого он не верит ни одному числу на сайте.

    Так и было: автоматический отсев снимал 326 из 385, оставалось 59 — а
    на карте семнадцать. Сорок два объекта убрал не алгоритм, а просмотр
    глазами, и этого шага в воронке не было. Столбцы давали 343 при 385.

    Шаг стоит показывать ещё и потому, что он сильный: команда, отвергшая
    сорок две собственные находки, вызывает больше доверия, чем команда с
    ровным списком.
    """

    def test_rejected_plus_published_equals_raw(self):
        import json

        import geopandas as gpd

        funnel_path = ROOT / "web-next/public/data/funnel.json"
        published_path = ROOT / "web-next/public/data/candidates.geojson"
        if not (funnel_path.exists() and published_path.exists()):
            pytest.skip("нет выгрузки")

        funnel = json.loads(funnel_path.read_text(encoding="utf-8"))
        rejected = funnel.get("rejected") or {}
        published = len(gpd.read_file(published_path))

        auto = sum(v for k, v in rejected.items() if k != "ПРОШЁЛ ОТСЕВ")
        passed = rejected.get("ПРОШЁЛ ОТСЕВ", 0)
        by_eye = passed - published

        assert by_eye >= 0, (
            f"на сайте {published} объектов, а отсев пропустил только {passed} — "
            "выгрузка не может содержать больше, чем прошло фильтр"
        )
        assert auto + by_eye + published == funnel["raw"], (
            f"воронка: {auto} автоотсев + {by_eye} просмотр + {published} на сайте "
            f"= {auto + by_eye + published}, а сырых кандидатов {funnel['raw']}"
        )
