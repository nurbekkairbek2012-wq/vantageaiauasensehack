"""Снимок в кэше обязан принадлежать тому месту, за которым его просили.

── Что случилось ───────────────────────────────────────────────────────

Кэш звался по номеру кандидата. Номер сквозной внутри области, но не между
областями: C00317 есть и в северном кольце, и в западной промзоне, а
объекты эти лежат в 24 км друг от друга.

Снимок северного кольца, скачанный в 03:36, был показан вместо снимка
промзоны в 04:50. На контактном листе я принял его за настоящую свалку —
и не ошибся бы, если бы это была она: северный C00317 свалкой и является.
Пять снимков из сорока одного оказались чужими.

Ошибка не давала о себе знать ничем: файл на месте, картинка открывается,
местность правдоподобная. Ровно тот класс, который здесь встречается чаще
всего.

Это второй раз, когда ключ этого кэша оказался неполным. Первый — без
зума: два скрипта качали на разном зуме, и одно и то же измерение давало
то 0,643, то 0,333 в зависимости от порядка запуска.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from highres_cache import cache_name  # noqa: E402


class TestCacheKeyNamesAPlace:
    def test_two_places_never_share_a_file(self):
        """Северный C00317 и западный C00317 — разные места и разные файлы."""
        north = cache_name(51.16185, 71.54983, 17)
        west = cache_name(51.20931, 71.20158, 17)
        assert north != west

    def test_the_same_place_reuses_the_file(self):
        """Иначе кэш бесполезен: каждый пересчёт качал бы всё заново."""
        assert cache_name(51.16185, 71.54983, 17) == cache_name(51.16185, 71.54983, 17)

    def test_zoom_is_part_of_the_key(self):
        """Первая версия ключа его теряла, и измерение зависело от порядка запуска."""
        assert cache_name(51.16185, 71.54983, 17) != cache_name(51.16185, 71.54983, 18)

    def test_a_metre_apart_is_a_different_file(self):
        """Пять знаков — около метра. Мельче пикселя снимка, и этого достаточно."""
        assert cache_name(51.16185, 71.54983, 17) != cache_name(51.16187, 71.54983, 17)

    def test_no_script_keys_the_cache_by_candidate_id(self):
        """Ни один скрипт не должен вернуться к имени по номеру объекта.

        Ищется по исходникам: возврат в одном скрипте не проявился бы
        нигде, пока кто-нибудь не запустил бы два просмотра подряд.
        """
        import re

        # Проверяются только скрипты, делящие этот кэш. deck_assets.py тоже
        # пишет png по имени, но это снимки разделов сайта для деки —
        # к местности они отношения не имеют.
        bad = re.compile(r'f"\{(name|cid|candidate_id)\}[^"]*\.png"')
        users = ("eval_on_ours.py", "review_sheets.py", "attach_chipmodel.py")
        offenders = []
        for path in [ROOT / "scripts" / n for n in users]:
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#", 1)[0]
                if bad.search(code) and "unlink" not in code and "legacy" not in code:
                    offenders.append(f"{path.name}:{i}: {code.strip()[:70]}")
        assert not offenders, (
            "кэш снимков снова именуется по номеру объекта: " + chr(10).join(offenders)
        )


class TestPlaceholderDetection:
    """Серый квадрат «съёмки нет» не должен доходить до листа просмотра.

    Сетка 3x3 склеивается из девяти тайлов, и у поставщика может не быть
    съёмки на части из них. Живая половина вытягивает общий разброс выше
    порога, кадр принимается — а объект оказывается ровно под заглушкой.

    Так на лист западной промзоны попал C00166 с надписью «Map data not
    yet available» поперёк центра.
    """

    def _grid(self, core_flat: bool):
        import numpy as np

        rng = np.random.default_rng(0)
        a = rng.integers(0, 255, (300, 300, 3)).astype("uint8")
        if core_flat:
            a[100:200, 100:200] = 200  # ровный серый в середине
        return a

    def test_fully_flat_frame_is_rejected(self):
        import numpy as np

        from review_sheets import looks_like_placeholder

        assert looks_like_placeholder(np.full((300, 300, 3), 200, dtype="uint8"))

    def test_frame_flat_only_in_the_middle_is_rejected(self):
        """Тот самый случай: края живые, объект под заглушкой."""
        from review_sheets import looks_like_placeholder

        assert looks_like_placeholder(self._grid(core_flat=True))

    def test_real_frame_passes(self):
        from review_sheets import looks_like_placeholder

        assert not looks_like_placeholder(self._grid(core_flat=False))
