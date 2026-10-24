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
        """Вердикт на сайте обязан совпадать с вердиктом метки под ним."""
        import geopandas as gpd

        codes = {"свалка": "landfill", "не свалка": "not_landfill",
                 "не понятно": "unclear"}
        labels, published = data
        marked = published[published["visual_check"].notna()]
        if marked.empty:
            pytest.skip("на сайте нет размеченных объектов")

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
        assert found >= 8, (
            f"опознанных свалок стало {found}, было 8. Если это результат "
            "нового просмотра — поправить число в README.md, docs/PITCH.md "
            "и на лендинге. Если нет — данные потеряны."
        )
