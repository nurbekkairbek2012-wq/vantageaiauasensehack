"""Отличить «признак про класс» от «признак про место».

── Зачем ───────────────────────────────────────────────────────────────

Дважды за ночь на 23 августа правдоподобное измерение оказывалось
неверным одним и тем же способом.

Первый раз — сезонность: свалки Астаны 0,158–0,257 против пашни Алматы
0,055–0,140, диапазоны не пересекаются, скрипт написал «признак разделяет
группы». Второй — падение вегетации: свалки севера против ложных находок
востока, ROC-AUC 1,000.

Оба раза сравнивались объекты РАЗНЫХ МЕСТ, и разделение объяснялось местом,
а не классом. Проверяется это третьей группой: объекты того же места, что
и положительные, но другого класса. Если признак про класс, они лягут к
отрицательным; если про место — к положительным.

Для падения вегетации ответ был: 0,500 внутри одного места против 0,933
между местами. То есть чистое место.

── Как пользоваться ────────────────────────────────────────────────────

Скрипт получает три набора и печатает две величины по каждому признаку.
Смотреть надо на первую: вторая почти всегда велика, и именно она
соблазняет.

    python scripts/check_confound.py
"""

import json
import sys
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Признаки, которые проверяются. Порядок — как в объяснении на карточке.
SIGNALS = (
    ("evidence_score", "сила признаков"),
    ("ndvi_drop", "падение вегетации"),
    ("bsi_rise", "рост открытого грунта"),
    ("pmli_response", "отклик полимеров"),
    ("sar_incoherence", "нестабильность радара"),
    ("thermal_anomaly", "тепловая аномалия"),
)

#: Ниже этого качества внутри одного места признак не различает класс.
#: Половина — это ровно случайное угадывание.
CHANCE = 0.5


def load(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [f["properties"] for f in data.get("features", [])]


def main() -> int:
    from sklearn.metrics import roc_auc_score

    here = "outputs_real/candidates.geojson"
    elsewhere = "outputs_astana_east/candidates.geojson"
    if not Path(here).exists() or not Path(elsewhere).exists():
        print("нужны оба прогона: север и восточный пояс")
        return 1

    north = load(here)
    east = load(elsewhere)
    dumps = [p for p in north if p.get("visual_check") == "landfill"]
    same_place = [p for p in north if p.get("visual_check") == "not_landfill"]

    print(f"положительные (свалки севера): {len(dumps)}")
    print(f"отрицательные ТОГО ЖЕ места:   {len(same_place)}")
    print(f"отрицательные ДРУГОГО места:   {len(east)}")
    print()
    print(f"{'признак':26s} {'класс':>8s} {'место':>8s}   вывод")

    for key, name in SIGNALS:
        a = np.array([p[key] for p in dumps if isinstance(p.get(key), (int, float))])
        b = np.array([p[key] for p in same_place if isinstance(p.get(key), (int, float))])
        c = np.array([p[key] for p in east if isinstance(p.get(key), (int, float))])
        if len(a) < 2 or len(b) < 3 or len(c) < 5:
            print(f"{name:26s} {'—':>8s} {'—':>8s}   данных мало")
            continue

        by_class = roc_auc_score(np.r_[np.ones(len(a)), np.zeros(len(b))], np.r_[a, b])
        by_place = roc_auc_score(np.r_[np.ones(len(b)), np.zeros(len(c))], np.r_[b, c])
        # Отклонение от случайности в любую сторону — это различение;
        # 0,1 различает не хуже 0,9, просто наоборот.
        strength = abs(by_class - CHANCE)
        note = "про класс" if strength > 0.2 else "про МЕСТО, не про класс"
        print(f"{name:26s} {by_class:8.3f} {by_place:8.3f}   {note}")

    print()
    print("«класс»  — свалки против не-свалок ОДНОГО места. Только эта колонка")
    print("           говорит, различает ли признак свалку.")
    print("«место»  — не-свалки одного места против другого. Высокое значение")
    print("           здесь объясняет ложное разделение и соблазняет первым.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
