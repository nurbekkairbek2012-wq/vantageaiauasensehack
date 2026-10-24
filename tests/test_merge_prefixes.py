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
        naive = {a[:3].upper() for a in areas}
        assert len(naive) < len(areas), (
            "первые три буквы стали различать области — но полагаться на это "
            "нельзя: следующая область снова начнётся с astana_"
        )

    def test_unknown_area_stops_instead_of_guessing(self, tmp_path, monkeypatch):
        """Слияние с незаведённой областью обязано остановиться, а не угадать."""
        import geopandas as gpd
        from shapely.geometry import Point

        import merge_cities

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
