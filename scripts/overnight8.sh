#!/usr/bin/env bash
# Пересчёт Астаны: сетка прогноза строилась по области по умолчанию.
#
# 4834 км² вместо 406 км² кольца — 19 335 ячеек вместо 1 623. Модель риска
# училась на ячейках, куда прогон не заглядывал, и считала их чистыми: это
# не «мало свалок», это «мы там не смотрели». Отсюда и базовая частота
# 0,00015, и прогноз, растекающийся далеко за пределы изученной области.
cd "$(dirname "$0")/.." || exit 1
PY=".venv/Scripts/python.exe"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a overnight.log; }

say "═══ цепочка 8: пересчёт Астаны с правильной областью ═══"
say "жду окончания Алматы"
s=0
while [ ! -f outputs_almaty/candidates.geojson ] && [ "$s" -lt 5400 ]; do sleep 30; s=$((s+30)); done
sleep 60

say "пересчёт Астаны (без снимков и доверификации — они в кэше)"
"$PY" scripts/finish_all.py --no-signals --no-verify >> finish_all3.log 2>&1 && say "пересчитан" || say "ПЕРЕСЧЁТ УПАЛ"

if [ -f outputs_real/metrics.json ]; then
  say "метрики: $(PYTHONIOENCODING=utf-8 "$PY" -c "
import json;m=json.load(open('outputs_real/metrics.json',encoding='utf-8'))
print('lift %.0f, PR-AUC %.3f, базовая частота %.5f'%(m['lift'],m['pr_auc_future'],m['base_rate_future']))")"
fi
say "═══ цепочка 8 закончена ═══"
