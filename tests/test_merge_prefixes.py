"""Приставки к номерам объектов обязаны различать области.

── Зачем приставки ─────────────────────────────────────────────────────

Номер кандидата сквозной внутри области: C00012 есть и в северном кольце,
и в западной промзоне. При слиянии простая склейка объединила бы разные
объекты — карточка на сайте показывала бы один, а печать акта брала бы
другой.

── Чем это чуть не кончилось ───────────────────────────────────────────

Запасной вариант приставки — первые три буквы идентификатора области. Пока
областей было три и назывались они astana, almaty, shymkent, он работал.

После отказа от Алматы и Шымкента все области стали астанинскими:

    astana, astana_east, astana_southeast,
    astana_west, astana_industrial_west, astana_south

Первые три буквы у всех шести — «AST». Незаведённая приставка молча
свернула бы шесть областей в одну, и номера столкнулись бы без единого
сообщения об ошибке.

Теперь отсутствие приставки — остановка с объяснением, а этот тест держит
таблицу полной и различающей.
"""

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture(scope="module")
def areas():
    text = (ROOT / "config/cities.yaml").read_text(encoding="utf-8")
    return [c["id"] for c in yaml.safe_load(text)["cities"]]


class TestPrefixesSeparateAreas:
    def test_every_area_has_one(self, areas):
        """Область без приставки нельзя слить — слияние обязано это сказать."""
        from merge_cities import PREFIX

        missing = [a for a in areas if a not in PREFIX]
        assert not missing, (
            f"нет приставки для {missing}. Добавить в PREFIX "
            "в scripts/merge_cities.py — иначе номера столкнутся"
        )

    def test_prefixes_are_distinct(self, areas):
        """Две области с одной приставкой — то же столкновение номеров."""
        from merge_cities import PREFIX

        used = [PREFIX[a] for a in areas if a in PREFIX]
        assert len(set(used)) == len(used), f"приставки повторяются: {sorted(used)}"

    def test_the_old_fallback_would_have_collided(self, areas):
        """Тот самый запасной вариант, ради которого написан этот файл.

        Проверка не про код, а про то, почему запасного варианта больше
        нет: она падает, если кто-нибудь решит его вернуть как разумный.
        """
        if len(areas) < 2:
            pytest.skip("область одна — сливать нечего")
        naive = {a[:3].upper() for a in areas}
        assert len(naive) < len(areas), (
            "первые три буквы стали различать области — но полагаться на это "
            "нельзя: следующая область снова начнётся с astana_"
        )

    def test_unknown_area_stops_instead_of_guessing(self, tmp_path, monkeypatch):
        """Слияние с незаведённой областью обязано остановиться, а не угадать."""
        import geopandas as gpd
        import merge_cities
        from shapely.geometry import Point

        folder = tmp_path / "outputs_astana_nowhere"
        folder.mkdir()
        gpd.GeoDataFrame(
            {"candidate_id": ["C00001"]}, geometry=[Point(71.4, 51.1)], crs=4326
        ).to_file(folder / "candidates.geojson", driver="GeoJSON")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["merge", f"astana_nowhere:{folder}"])
        with pytest.raises(SystemExit) as stop:
            merge_cities.main()
        assert "приставк" in str(stop.value)


class TestOverlapDuplicatesAreRemoved:
    """Объект из полосы пересечения областей не должен считаться дважды.

    Границы перекрываются намеренно — иначе свалка на краю разрезалась бы
    пополам. Но объект в полосе находится ДВАЖДЫ, по разу в каждом
    прогоне, и получает два разных номера.

    Так вышло с полем строительного мусора у железнодорожных путей:
    C00061 в северном кольце и C00056 на юге, расстояние между центрами —
    ноль метров. После слияния сайт показал бы девять свалок как десять, а
    сумма ущерба выросла бы на несуществующий объект.

    Ловится только по месту: номера разные, площади разные (контур
    обрезается границей области), а точка одна.
    """

    def _two_areas(self, distance_m: float):
        import geopandas as gpd
        from shapely.geometry import Point

        # UTM 42N: метры, чтобы расстояние значило то, что написано.
        return gpd.GeoDataFrame(
            {"candidate_id": ["AST-C00061", "AUG-C00056"],
             "area_m2": [3327.0, 2100.0]},
            geometry=[Point(500000, 5666000), Point(500000 + distance_m, 5666000)],
            crs=32642,
        )

    def test_same_place_collapses_to_one(self):
        from merge_cities import drop_duplicates_by_place

        kept = drop_duplicates_by_place(self._two_areas(0.0))
        assert len(kept) == 1

    def test_the_bigger_contour_survives(self):
        """У большего контур обрезан границей области меньше."""
        from merge_cities import drop_duplicates_by_place

        kept = drop_duplicates_by_place(self._two_areas(10.0))
        assert list(kept["candidate_id"]) == ["AST-C00061"]

    def test_neighbours_are_not_glued(self):
        """Две настоящие свалки в сотне метров — разные объекты.

        Порог нельзя поднимать бесконечно: свалки бывают рядом, и
        склеенные в одну они дадут заниженный счёт и заниженный ущерб.
        """
        from merge_cities import drop_duplicates_by_place

        kept = drop_duplicates_by_place(self._two_areas(120.0))
        assert len(kept) == 2

    def test_empty_input_survives(self):
        import geopandas as gpd
        from merge_cities import drop_duplicates_by_place

        empty = gpd.GeoDataFrame({"candidate_id": [], "area_m2": []},
                                 geometry=[], crs=32642)
        assert drop_duplicates_by_place(empty).empty
