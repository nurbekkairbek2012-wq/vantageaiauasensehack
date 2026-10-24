"""Ни один текст не утверждает выезд, которого нет в данных.

── Зачем ───────────────────────────────────────────────────────────────

Записи «осмотр на месте» без фотографий ушли в README, деку, питч и
ответы жюри как «15 свалок, каждая подтверждена выездом». Выездов не было,
и записи отозваны. Тексты пришлось вычищать по одному, и каждый пропуск
означал бы то же утверждение в документе, который уйдёт заказчику.

Здесь это правило записано так, чтобы его нельзя было нарушить молча:
пока в выгрузке нет ни одного объекта, подтверждённого выездом с
фотографией, ни README, ни материалы защиты, ни дека, ни сайт не имеют
права говорить, что выезд был. Появится настоящий выезд — формулировки
станут допустимы, и тест перестанет их ловить сам.

Список фраз — конкретные утверждения, которые уже однажды стояли в
текстах. Слово «выезд» само по себе не запрещено: «ждут выезда»,
«очередь выезда», «после выездной проверки» — правда.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web-next/public/data"

#: Утверждения, что выезд состоялся.
CLAIMS = re.compile(
    r"подтвержд[её]н\w*\s+выезд"          # «подтверждены выездом»
    r"|проверен\w*\s+выезд"               # «проверены выездом»
    r"|проверен\w*\s+(?:человеком\s+)?на\s+месте"  # «проверена человеком на месте»
    r"|осмотр\w*\s+на\s+месте"            # «осмотром на месте»
    r"|кто\s+был\s+на\s+месте:",
    re.IGNORECASE,
)

#: Где эти утверждения стояли или могут появиться: тексты для людей.
CORPUS = (
    "README.md",
    "docs/PITCH.md",
    "docs/QA.md",
    "docs/PILOT.md",
    "docs/NUMBERS.md",
    "docs/FIELD.md",
    "docs/PROPOSAL.md",
    "docs/PROPOSAL_KZ.md",
    "docs/PROPOSAL_EN.md",
    "scripts/build_deck.py",
    "scripts/make_speech_pdf.py",
)

#: Строки, где фраза стоит в отрицании или в правиле, а не в утверждении.
ALLOWED = re.compile(
    r"не\s+(?:было|засчит|говорить)|без\s+(?:такого|фото)|только\s+с\s+фото"
    r"|Не\s+говорите|ни\s+одного|отозван|будет\s+ни"
    # Счётчик, а не утверждение: «подтверждено выездом | 0» и шаблон,
    # который подставит число из данных.
    r"|\|\s*0\s*\||выездом\s+\{",
    re.IGNORECASE,
)


def _ground_count() -> int:
    path = DATA / "candidates.geojson"
    if not path.exists():
        pytest.skip("выгрузки нет")
    features = json.loads(path.read_text(encoding="utf-8")).get("features", [])
    return sum(
        1 for f in features
        if (f.get("properties") or {}).get("check_source") == "ground"
    )


@pytest.mark.parametrize("name", CORPUS)
def test_no_field_claims_without_field_data(name):
    if _ground_count() > 0:
        pytest.skip("в выгрузке есть объекты, подтверждённые выездом")
    path = ROOT / name
    if not path.exists():
        pytest.skip(f"нет {name}")
    bad = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if CLAIMS.search(line) and not ALLOWED.search(line):
            bad.append(f"{name}:{number}: {line.strip()[:120]}")
    assert not bad, (
        "текст утверждает выезд, которого нет в данных:\n  " + "\n  ".join(bad)
    )


def test_site_marks_no_object_as_field_checked():
    """На сайте нет ни одной метки выезда без фотографии с координатами."""
    path = DATA / "candidates.geojson"
    if not path.exists():
        pytest.skip("выгрузки нет")
    for feature in json.loads(path.read_text(encoding="utf-8"))["features"]:
        p = feature.get("properties") or {}
        if p.get("check_source") == "ground":
            assert (p.get("ground_photos_located") or 0) >= 1, p.get("candidate_id")


def test_money_is_counted_on_confirmed_objects_only():
    """Главные суммы экономики — по опознанным, «не разобрать» — отдельно."""
    path = DATA / "economy.json"
    if not path.exists():
        pytest.skip("выгрузки нет")
    economy = json.loads(path.read_text(encoding="utf-8"))
    sure = [o for o in economy["objects"] if o.get("visual_check") == "landfill"]
    if not sure:
        assert economy["basis"] == "listed"
        return
    assert economy["basis"] == "confirmed"
    assert economy["totals"]["objects"] == len(sure)
    total = sum(o["damage_p50"] for o in sure)
    assert economy["totals"]["sum_of_medians"]["damage_kzt"] == total
    unsure = [o for o in economy["objects"] if o.get("visual_check") != "landfill"]
    if unsure:
        assert economy["pending"]["objects"] == len(unsure)
