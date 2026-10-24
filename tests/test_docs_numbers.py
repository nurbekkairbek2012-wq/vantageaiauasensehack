"""Числа в текстах защиты обязаны совпадать с выгрузкой.

── Зачем ───────────────────────────────────────────────────────────────

Весь проект держится на правиле «ни одно число не вписано руками»: сайт
читает выгрузку, дека читает выгрузку, страница чисел пересобирается
скриптом и сверяется тестом. Ровно поэтому расхождения между экранами
находились нами, а не проверяющим.

У материалов защиты — README, PITCH.md, QA.md — этого правила нет и быть
не может: это связная речь, а не таблица, и подставлять в неё значения
шаблоном значит получить текст, который нельзя прочитать вслух. Числа там
вписаны руками, и это осознанная плата.

Плата имеет срок годности. Стоит пересчитать прогон — и «5 645 тонн» в
README останется от прошлой выгрузки, а на экране будет другое. На защите
это худший из возможных провалов: не ошибка в расчёте, а два разных числа
об одном и том же в двух документах одной команды.

── Что делает тест ─────────────────────────────────────────────────────

Берёт величины из economy.json и проверяет, что каждая
встречается в тех текстах, где она названа. Тест не умеет проверить, что
число стоит в правильном предложении, — он ловит другое и более важное:
**после пересчёта старого значения в тексте не останется**, потому что
нового там не появится, и тест скажет, какой файл открыть.

Отдельно проверяется внутренняя арифметика выгрузки: слагаемые сходятся
в итог, воронка сходится в сумму. Это дешёвая защита от расхождения,
которое иначе видно только глазами и только если сложить столбец.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web-next/public/data"

pytestmark = pytest.mark.skipif(
    not (DATA / "economy.json").exists(),
    reason="нет выгрузки",
)


def ru(value: float, digits: int = 0) -> str:
    """Число так, как оно набрано в тексте: пробел разряда, запятая дроби."""
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


@pytest.fixture(scope="module")
def facts() -> dict[str, list]:
    # Воронка читается из economy.json, а не из funnel.json: там она уже
    # сведена с опубликованным списком, и брать два источника ради одних
    # и тех же чисел значит однажды получить между ними расхождение.
    economy = json.loads((DATA / "economy.json").read_text(encoding="utf-8"))
    sums = economy["totals"]["sum_of_medians"]
    queue = economy["queue"]

    def cut(share: float) -> int:
        for row in economy["priority"]:
            if row["share"] >= share:
                return int(row["n"])
        return len(economy["priority"])

    #: величина → (как она набрана, в каких файлах названа)
    #:
    #: Список файлов — не «где могла бы встретиться», а где встречается
    #: сейчас. Появится число в новом тексте — строку сюда надо дописать,
    #: и это правильная цена: иначе тест сторожит не весь корпус, а его
    #: половину, и об этом никто не знает.
    return {
        "масса отходов, т": [
            ru(sums["mass_t"]).replace(" ", " "),
            ["README.md", "docs/QA.md", "docs/PITCH.md"],
        ],
        "стоимость вывоза, млн ₸": [
            ru(sums["removal_kzt"] / 1e6, 1),
            ["README.md", "docs/QA.md", "docs/PITCH.md"],
        ],
        "возврат вторсырьём, млн ₸": [
            ru(sums["recyclable_kzt"] / 1e6, 1),
            ["README.md", "docs/QA.md", "docs/PITCH.md"],
        ],
        "чистые потери, млн ₸": [
            ru(sums["damage_kzt"] / 1e6, 1),
            ["docs/QA.md", "docs/PITCH.md"],
        ],
        "доля возврата, %": [
            str(round(100 * sums["recyclable_kzt"] / sums["removal_kzt"])),
            ["README.md", "docs/QA.md", "docs/PITCH.md"],
        ],
        "сырых находок": [str(queue["raw"]), ["README.md", "docs/QA.md", "docs/PITCH.md"]],
        "просмотрено человеком": [
            str(queue["reviewed"]),
            ["README.md", "docs/QA.md", "docs/PITCH.md"],
        ],
        "опубликовано": [str(queue["published"]), ["README.md", "docs/QA.md", "docs/PITCH.md"]],
        "выездов на половину суммы": [str(cut(0.5)), ["README.md", "docs/QA.md"]],
        "выездов на 80% суммы": [str(cut(0.8)), ["docs/QA.md"]],
        "CO₂-экв уже выброшено, т": [
            ru(sums["co2e_emitted_t"]).replace(" ", " "),
            ["docs/QA.md"],
        ],
        "CO₂-экв всего, т": [
            ru(sums["co2e_t"]).replace(" ", " "),
            ["docs/QA.md"],
        ],
    }


def test_every_number_is_current(facts):
    """Каждая величина встречается в тех текстах, где она названа.

    Падение означает не «текст неправильный», а «выгрузку пересчитали, а
    тексты нет». В сообщении сразу видно, какое число стало каким.
    """
    stale: list[str] = []
    for label, (value, files) in facts.items():
        for name in files:
            text = (ROOT / name).read_text(encoding="utf-8")
            # Неразрывный пробел в разрядах набирается по-разному, и
            # сверять надо по смыслу, а не по коду символа.
            haystack = text.replace(" ", " ").replace(" ", " ")
            if value not in haystack:
                stale.append(f"{name}: «{label}» сейчас {value}, в тексте этого числа нет")
    assert not stale, (
        "тексты защиты отстали от выгрузки:\n  " + "\n  ".join(stale)
        + "\n\nЭто не ошибка расчёта. Это два разных числа об одном и том же "
          "в двух документах одной команды — худшее, что можно принести на защиту."
    )


def test_components_add_up_to_the_total(facts):
    """Вывоз минус сырьё плюс климат сходится с чистым ущербом.

    Проверяется на суммах медиан — именно они стоят крупным на экране и
    именно их складывает читатель. Полного равенства быть не может:
    медиана разности не равна разности медиан. Но расхождение больше
    процента означало бы, что слагаемые и итог посчитаны по разным
    данным, а не по-разному округлены.
    """
    economy = json.loads((DATA / "economy.json").read_text(encoding="utf-8"))
    s = economy["totals"]["sum_of_medians"]
    assembled = s["removal_kzt"] - s["recyclable_kzt"] + s["climate_kzt"]
    assert abs(assembled - s["damage_kzt"]) / s["damage_kzt"] < 0.01, (
        f"слагаемые дают {assembled / 1e6:.1f} млн ₸, "
        f"а итог {s['damage_kzt'] / 1e6:.1f} млн ₸"
    )


def test_funnel_adds_up(facts):
    """Воронка сходится: снятое отсевом плюс дошедшее до человека — это всё."""
    economy = json.loads((DATA / "economy.json").read_text(encoding="utf-8"))
    q = economy["queue"]
    assert q["auto_rejected"] + q["reviewed"] == q["raw"], (
        f"{q['auto_rejected']} + {q['reviewed']} ≠ {q['raw']}: "
        "несходящаяся воронка — первое, что считает проверяющий"
    )
    assert q["published"] <= q["reviewed"]
    assert q["ground"] <= q["published"]


def test_deck_and_docs_agree_on_the_live_link():
    """Живой адрес в деке и в README — один и тот же.

    Два адреса на одно и то же расходятся в первый же день, и второй из
    них обычно оказывается мёртвым. Проверка дешёвая, а поймать это
    иначе можно только вручную открыв оба.
    """
    import re

    deck = (ROOT / "scripts/build_deck.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    live = (ROOT / "scripts/check_live.py").read_text(encoding="utf-8")

    pattern = re.compile(r"[a-z0-9-]+\.vercel\.app")
    in_deck = set(pattern.findall(deck))
    in_check = set(pattern.findall(live)) - {"ваш-адрес.vercel.app"}
    in_readme = set(pattern.findall(readme))

    assert len(in_deck) == 1, f"в деке адресов не один: {sorted(in_deck)}"
    assert in_deck <= in_check, (
        f"дека печатает {sorted(in_deck)}, а проверяется {sorted(in_check)}")
    if in_readme:
        assert in_readme <= in_deck | in_check, (
            f"в README адрес, который никто не проверяет: {sorted(in_readme)}")
