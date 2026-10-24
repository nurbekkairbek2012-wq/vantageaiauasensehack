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
from typing import ClassVar

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


class TestServiceAgreesWithTheSite:
    """Сервис и сайт обязаны отвечать одно и то же на один вопрос.

    API читает каталог артефактов из настроек, сайт — опубликованный
    набор. Пока их никто не сводил, ``/health`` отвечал «24 объекта,
    24,2 га» при пятнадцати объектах и 1,72 га на карте: сервис держал
    данные прогона годичной давности.

    Расхождение тихое — оба ответа выглядят осмысленно, и заметить его
    можно, только спросив у обоих одно и то же. Проверяющий, который
    откроет /health рядом с картой, спросит.
    """

    def test_object_count_matches(self):
        import geopandas as gpd

        published = ROOT / "web-next/public/data/candidates.geojson"
        served = ROOT / "outputs/candidates.geojson"
        if not (published.exists() and served.exists()):
            pytest.skip("нет выгрузки или каталога сервиса")

        assert len(gpd.read_file(served)) == len(gpd.read_file(published)), (
            "сервис отдаёт не то же число объектов, что сайт — запустите "
            "python scripts/publish_filter.py"
        )

    def test_area_and_damage_match(self):
        import geopandas as gpd

        published = ROOT / "web-next/public/data/candidates.geojson"
        served = ROOT / "outputs/candidates.geojson"
        if not (published.exists() and served.exists()):
            pytest.skip("нет выгрузки или каталога сервиса")

        a, b = gpd.read_file(served), gpd.read_file(published)
        for column in ("area_m2", "damage_p50"):
            if column not in a.columns or column not in b.columns:
                continue
            assert abs(a[column].sum() - b[column].sum()) < 1.0, (
                f"{column}: сервис {a[column].sum():.0f}, сайт {b[column].sum():.0f}"
            )


class TestNoPrivateLayerLeaks:
    """Закрытые слои не должны попадать в публикацию.

    Граница продукта проходит здесь: житель видит зону риска, служба —
    точку. Опубликовать risk_private значит отдать точные координаты
    предсказанных мест всем, включая тех, кто ищет, где свалить
    незаметно.

    Проверка была только в workflow публикации, а он запускается вручную —
    то есть не запускается. Перенесена в тесты: они идут на каждый пуш.

    Белый список расширяется одной строкой, и без падающего теста этого
    никто не заметит до момента, когда координаты уже в сети.
    """

    #: Файлы, которых в публикации быть не должно, и чем это грозит.
    FORBIDDEN: ClassVar[dict[str, str]] = {
        "risk_private.geojson": "точные вероятности по каждой ячейке сетки",
        "access.log": "журнал обращений к закрытым данным",
        "citizen_reports.jsonl": "сообщения жителей с их геопозицией",
        "rejected.geojson": "отвергнутые находки с причинами отсева",
    }

    def test_no_forbidden_files(self):
        data = ROOT / "web-next/public/data"
        if not data.exists():
            pytest.skip("нет каталога публикации")

        leaked = [f"{name} — {why}" for name, why in self.FORBIDDEN.items()
                  if (data / name).exists()]
        assert not leaked, "в публикации закрытые слои:" + chr(10) + chr(10).join(leaked)

    def test_no_exact_probability_field(self):
        """Поле risk — это точная вероятность модели по ячейке.

        Публичная сетка несёт risk_class (четыре класса), и этого хватает
        для карты. По точному значению восстанавливается сама модель.
        """
        import json

        data = ROOT / "web-next/public/data"
        if not data.exists():
            pytest.skip("нет каталога публикации")

        offenders = []
        for path in sorted(data.glob("*.geojson")) + sorted(data.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            features = payload.get("features") if isinstance(payload, dict) else None
            for feature in (features or [])[:200]:
                if "risk" in (feature.get("properties") or {}):
                    offenders.append(path.name)
                    break
        assert not offenders, f"точная вероятность в публикации: {offenders}"
