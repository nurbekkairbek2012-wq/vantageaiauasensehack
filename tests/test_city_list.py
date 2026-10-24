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
