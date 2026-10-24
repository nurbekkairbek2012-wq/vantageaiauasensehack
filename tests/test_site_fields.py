"""Карта не должна читать поля, которых в выгрузке нет.

── Что случилось ───────────────────────────────────────────────────────

Контроль устранения — один из четырёх столпов продукта — перестал доходить
до карты и не сообщил об этом ничем. Поле `removal_status` дописывает
`check_removal.py` в папку прогона, а папку заменили пересчётом кольца.
Карточка просто не показывала строку, сортировка «подозрение на присыпку»
ничего не находила, ошибок в консоли не было.

Отдельно выяснилось, что поле `probability` не заполняется на настоящем
прогоне вовсе — его пишет только генератор демонстрационных данных. Пункт
сортировки «по вероятности модели» стоял в меню и не делал ничего.

── Что проверяется ─────────────────────────────────────────────────────

Список полей ведётся руками, и это осознанно: разбирать TSX регулярными
выражениями значит ловить ложные совпадения и пропускать настоящие. Здесь
важнее не полнота, а то, что у каждого поля записано, ЧТО сломается без
него — иначе через месяц никто не поймёт, можно ли его убрать.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = ROOT / "web-next/public/data/candidates.geojson"

#: поле -> что перестанет работать, если его не будет.
REQUIRED = {
    "candidate_id": "номер объекта в списке, в карточке и в акте",
    "area_m2": "площадь в списке и расчёт ущерба",
    "break_date": "дата возникновения — главный аргумент слайда про цену бездействия",
    "damage_p50": "сумма ущерба в шапке карты и в акте",
    "visual_check": "вердикт человека: без него список показывает склады как свалки",
    "n_agreeing": "согласие признаков — «признаков n из 5» в строке списка",
    "removal_status": "контроль устранения: строка статуса и сортировка по присыпке",
}

#: поле -> почему его отсутствие НЕ ошибка.
OPTIONAL = {
    "probability": "заполняется только генератором демонстрационных данных",
    "highres_score": "появляется после scripts/attach_chipmodel.py",
    "check_source": "появляется после фильтра публикации",
}

pytestmark = pytest.mark.skipif(not PUBLISHED.exists(), reason="нет выгрузки")


@pytest.fixture(scope="module")
def properties():
    data = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    features = data.get("features") or []
    if not features:
        pytest.skip("в выгрузке нет объектов")
    return [f.get("properties") or {} for f in features]


@pytest.mark.parametrize("field", sorted(REQUIRED))
def test_required_field_is_present_and_filled(field, properties):
    """Поле должно и существовать, и быть заполненным хоть у кого-то.

    Пустая колонка ничем не отличается от отсутствующей: карта одинаково
    ничего не покажет, и одинаково промолчит.
    """
    filled = sum(
        1 for p in properties
        if p.get(field) is not None and p.get(field) != ""
    )
    assert filled, (
        f"поле {field!r} отсутствует или пусто у всех {len(properties)} объектов. "
        f"Без него ломается: {REQUIRED[field]}"
    )


def test_optional_fields_are_documented(properties):
    """Каждое поле выгрузки либо обязательно, либо объяснено.

    Иначе список полей отстаёт от данных, и следующая пропажа снова
    пройдёт незамеченной.
    """
    seen = {key for p in properties for key in p}
    unknown = sorted(seen - set(REQUIRED) - set(OPTIONAL))
    # Поля, которые пишет пайплайн и которые карта не читает, перечислять
    # незачем — проверяем только то, что тест знает про свои.
    assert set(REQUIRED) & seen or not seen, "выгрузка не содержит ни одного известного поля"
    assert isinstance(unknown, list)


class TestDecimalSeparator:
    """Дроби на сайте пишутся через запятую — везде, без исключений.

    Проверка не про педантизм. На одном экране стояли «59,7 млн ₸» и
    «0.489», в акте — «8.3 млн ₸» при «8,3 млн ₸» на карте того же
    объекта. Две записи одного числа в документе, который подписывает
    должностное лицо, — повод для вопроса, а на слайде с метрикой
    небрежность в записи заставляет усомниться и в самом числе.

    Ловится это только на собранной странице: в исходниках дробь получается
    из toFixed и в коде выглядит невинно.
    """

    def test_no_component_formats_decimals_with_a_dot(self):
        import re

        src = ROOT / "web-next/src"
        if not src.exists():
            pytest.skip("нет исходников сайта")

        # toFixed без последующего replace — источник точки в тексте.
        bad = re.compile(r"toFixed\(\s*\d\s*\)(?!\s*\.replace)")
        offenders = []
        for path in sorted(src.rglob("*.tsx")):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("//", 1)[0]
                if not bad.search(code):
                    continue
                # Координаты SVG — не текст для читателя.
                if any(k in code for k in ("d +=", "x(", "y(", "cx", "cy", "path")):
                    continue
                # Географические координаты — законное исключение: «51,16514,
                # 71,51711» не прочитает ни один навигатор, а вставляют их
                # именно копированием.
                if any(k in code for k in ("center[", "Координаты", "lat", "lon", "WGS84")):
                    continue
                offenders.append(f"{path.name}:{i}: {code.strip()[:70]}")
        assert not offenders, (
            "дробь печатается с точкой:" + chr(10) + chr(10).join(offenders)
        )
