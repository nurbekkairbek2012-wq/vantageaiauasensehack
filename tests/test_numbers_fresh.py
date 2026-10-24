"""Страница чисел обязана совпадать с выгрузкой, из которой собрана.

Она генерируется скриптом — и именно поэтому может устареть незаметно:
файл на месте, таблицы красивые, числа от позапрошлого прогона. Ровно так
устаревали вписанные руками числа на лендинге, в деке и в HANDOFF.md,
и ловились они только вычиткой.

Тест собирает страницу заново и сравнивает с той, что лежит в репозитории.
Расхождение значит одно: `python scripts/key_numbers.py` не запускали
после последнего пересчёта.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs/NUMBERS.md"
DATA = ROOT / "web-next/public/data/candidates.geojson"

pytestmark = pytest.mark.skipif(
    not (PAGE.exists() and DATA.exists()),
    reason="нет страницы или выгрузки",
)


def test_page_matches_the_data():
    before = PAGE.read_text(encoding="utf-8")
    run = subprocess.run(
        [sys.executable, str(ROOT / "scripts/key_numbers.py")],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert run.returncode == 0, f"скрипт упал: {run.stderr[-400:]}"
    after = PAGE.read_text(encoding="utf-8")

    if before != after:
        # Возвращаем как было: тест не должен молча править репозиторий.
        # Пусть человек запустит скрипт сам и увидит, что изменилось.
        PAGE.write_text(before, encoding="utf-8")
        diff = [
            f"  было:  {a}\n  стало: {b}"
            for a, b in zip(before.splitlines(), after.splitlines(), strict=False)
            if a != b
        ]
        pytest.fail(
            "docs/NUMBERS.md разошёлся с выгрузкой — запустите "
            "python scripts/key_numbers.py:\n" + "\n".join(diff[:6])
        )
