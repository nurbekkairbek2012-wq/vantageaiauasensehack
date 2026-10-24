"""Разметку глазами обязано быть чем воспроизвести.

── Что случилось ───────────────────────────────────────────────────────

Ночью 23 августа выяснилось, что девять из шестнадцати отметок на сайте
существуют только в опубликованном файле. В источнике разметки
(`labels_manual.geojson`) их не было: просмотр листов записал вердикты
прямо в выгрузку.

Пять из этих девяти — «свалка». То есть главное число всего продукта,
«восемь объектов опознаны как свалки», держалось на файле, который любой
пересчёт переписал бы, откатив сайт до трёх. Ни один тест этого не ловил:
файл на месте, объекты на месте, просто свалок втрое меньше.

── Почему именно так ───────────────────────────────────────────────────

Отметки хранятся геометрией, а не номером объекта: номера живут до
следующего прогона. Значит, проверка тоже должна идти по месту — «накрывает
ли объект хоть одну метку», а не «совпадают ли номера».

Это тот же класс ошибки, что уже ловился дважды: **шаг, переписывающий
файл целиком, тихо отменяет работу предыдущего шага.** Здесь работой были
часы просмотра снимков глазами.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "labels_manual.geojson"
PUBLISHED = ROOT / "web-next/public/data/candidates.geojson"

pytestmark = pytest.mark.skipif(
    not (LABELS.exists() and PUBLISHED.exists()),
    reason="нет разметки или выгрузки — на чистой копии проверять нечего",
)


@pytest.fixture(scope="module")
def data():
    import geopandas as gpd

    labels = gpd.read_file(LABELS)
    published = gpd.read_file(PUBLISHED).to_crs(labels.crs)
    return labels, published


class TestPublishedMarksComeFromTheSource:
    def test_every_published_mark_has_a_label_behind_it(self, data):
        """Отметка на сайте без метки в источнике — не воспроизводится.

        Такой объект переживёт ровно до следующего пересчёта, и падение
        числа свалок будет выглядеть как результат работы детектора, а не
        как потеря данных.
        """
        import geopandas as gpd

        labels, published = data
        marked = published[published["visual_check"].notna()]
        if marked.empty:
            pytest.skip("на сайте нет размеченных объектов")

        covered = gpd.sjoin(marked[["candidate_id", "geometry"]],
                            labels[["geometry"]], predicate="contains", how="inner")
        orphan = sorted(set(marked["candidate_id"]) - set(covered["candidate_id"]))
        assert not orphan, (
            f"отметки живут только в выгрузке: {orphan}. "
            "Запустить scripts/attach_visual.py нельзя — он их сотрёт. "
            "Сначала вернуть метки в labels_manual.geojson по геометрии."
        )

    def test_verdicts_agree_with_the_source(self, data):
        """Вердикт на сайте обязан совпадать с источником, из которого взят.

        Источников два, и порядок силы между ними жёсткий:

            выезд (ground_truth.json)  >  просмотр по снимку (labels_manual)

        Объект, подтверждённый человеком на месте, сверяется с записью о
        выезде, а не с меткой по снимку: выезд её и перебивает. Сверять его
        с меткой значило бы требовать, чтобы съёмка знала то, что видно
        только вблизи, — и тест падал бы ровно на тех объектах, ради
        которых на выезд и ездили.
        """
        import json

        import geopandas as gpd

        codes = {"свалка": "landfill", "не свалка": "not_landfill",
                 "не понятно": "unclear"}
        labels, published = data

        ground_path = ROOT / "ground_truth.json"
        ground = {}
        if ground_path.exists():
            for record in json.loads(ground_path.read_text(encoding="utf-8")):
                ground[str(record["candidate_id"])] = codes.get(record["verdict"])

        visited = published[published.get("check_source") == "ground"]
        for row in visited.itertuples():
            expected = ground.get(str(row.candidate_id))
            assert expected is not None, (
                f"{row.candidate_id} помечен как подтверждённый выездом, но "
                "записи о выезде нет в ground_truth.json"
            )
            assert row.visual_check == expected, (
                f"{row.candidate_id}: на сайте {row.visual_check!r}, "
                f"в записи о выезде {expected!r}"
            )

        # Дальше сверяются только те, чей вердикт пришёл со снимка.
        published = published[published.get("check_source") != "ground"]
        marked = published[published["visual_check"].notna()]
        if marked.empty:
            pytest.skip("все объекты на сайте подтверждены выездом")

        joined = gpd.sjoin(marked[["candidate_id", "visual_check", "geometry"]],
                           labels[["verdict", "geometry"]],
                           predicate="contains", how="inner")
        joined = joined[~joined.index.duplicated(keep="first")]
        wrong = joined[joined["verdict"].map(codes) != joined["visual_check"]]
        assert wrong.empty, (
            "вердикт на сайте разошёлся с источником у "
            f"{sorted(wrong['candidate_id'])}"
        )

    def test_landfill_count_is_what_the_product_claims(self, data):
        """Число опознанных свалок — главное число продукта.

        Оно стоит в питч-деке, в README и на лендинге. Если оно меняется,
        менять надо и их, а не узнавать об этом на защите.
        """
        _labels, published = data
        found = int((published["visual_check"] == "landfill").sum())
        # Было 9 до пересмотра 23 августа в 08:45: C00031 и C00178 оказались
        # автомобильной разборкой — площадкой с хозяином и забором, а не
        # стихийной свалкой. Отходы там есть, но вопрос стоит не так.
        assert found >= 7, (
            f"опознанных свалок стало {found}, было 7. Если это результат "
            "нового просмотра — поправить число в README.md, docs/PITCH.md "
            "и на лендинге. Если нет — данные потеряны."
        )
