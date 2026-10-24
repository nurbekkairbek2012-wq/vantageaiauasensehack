"""Сценарий демонстрации обязан описывать то, что лежит на карте.

── Что случилось ───────────────────────────────────────────────────────

Сценарий строится при выгрузке, а фильтр публикации стоит после и правил
только candidates.geojson. Числа разошлись: на странице таймлапса стояло
«Мы нашли 49 объектов» при шестнадцати на карте, а сцена «доказательная
цепочка» наводилась на C00018 — объект, который фильтр к тому моменту снял.

Наведение на несуществующий объект ничем себя не выдавало: сцена просто
открывалась на пустом месте. Заметить это можно было, только пройдя
таймлапс до конца — то есть на защите.

Это третий случай одного и того же: **шаг, стоящий последним, правит один
файл из нескольких связанных.** Первые два — слияние городов, обходившее
фильтр, и указатель бота с 49 объектами против 16 на сайте.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web-next/public/data"

pytestmark = pytest.mark.skipif(
    not ((DATA / "story.json").exists() and (DATA / "candidates.geojson").exists()),
    reason="нет выгрузки",
)


@pytest.fixture(scope="module")
def site():
    import geopandas as gpd

    story = json.loads((DATA / "story.json").read_text(encoding="utf-8"))
    published = gpd.read_file(DATA / "candidates.geojson")
    return story, published


class TestStoryDescribesThePublishedList:
    def test_object_count_is_the_published_one(self, site):
        """«Мы нашли N» — это N на карте, а не N до фильтра."""
        story, published = site
        line = next(s["line"] for s in story["scenes"] if s["id"] == "found")
        assert str(len(published)) in line, (
            f"сценарий говорит {line!r}, на карте {len(published)} объектов"
        )

    def test_money_line_counts_the_same_objects(self, site):
        """Сумма ущерба обязана относиться к тому же списку."""
        story, published = site
        scene = next((s for s in story["scenes"] if s["id"] == "money"), None)
        if scene is None:
            pytest.skip("сцены про деньги нет")
        assert str(len(published)) in scene["line"], (
            f"сценарий говорит {scene['line']!r}, на карте {len(published)} объектов"
        )

    def test_every_focus_points_at_a_published_object(self, site):
        """Сцена не должна наводиться на объект, снятый с публикации.

        Это и есть та ошибка, которую невозможно заметить в коде: карта
        улетает на координаты, объекта там нет, панель пуста.
        """
        story, published = site
        ids = set(published["candidate_id"].astype(str))
        broken = [
            (s["id"], s["focus"]["candidate_id"])
            for s in story["scenes"]
            if s.get("focus") and str(s["focus"].get("candidate_id")) not in ids
        ]
        assert not broken, f"сцены наводятся на снятые объекты: {broken}"

    def test_the_shown_object_is_one_a_human_confirmed(self, site):
        """Показывать на сцене стоит объект, который человек назвал свалкой.

        Наводиться на «не разобрать» значит открывать панель доказательств
        над объектом, про который мы сами не уверены, — и первый же вопрос
        из зала будет про него.
        """
        story, published = site
        focus = next((s["focus"]["candidate_id"] for s in story["scenes"]
                      if s.get("focus")), None)
        if focus is None:
            pytest.skip("сцен с наведением нет")
        row = published[published["candidate_id"].astype(str) == str(focus)]
        assert not row.empty
        verdict = row.iloc[0].get("visual_check")
        assert verdict == "landfill", (
            f"сцена наводится на {focus} с вердиктом {verdict!r} — "
            "на демонстрации показывать стоит подтверждённую свалку"
        )
