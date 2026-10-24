#!/usr/bin/env bash
# Обучение на трёх источниках, как только дособерётся казахстанский набор.
cd "$(dirname "$0")/.." || exit 1
PY=".venv/Scripts/python.exe"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a overnight.log; }

say "═══ модель: жду казахстанский набор ═══"
# Признак готовности — сборщик перестал добавлять снимки.
prev=-1; still=0
for _ in $(seq 1 600); do
  n=$(ls data/kz_dataset/*.png 2>/dev/null | wc -l)
  if [ "$n" = "$prev" ]; then
    still=$((still+30))
    [ "$still" -ge 300 ] && break
  else
    still=0; prev=$n
  fi
  sleep 30
done
say "казахстанский набор: $(ls data/kz_dataset/*.png 2>/dev/null | wc -l) снимков"

say "обучение на трёх источниках"
"$PY" scripts/train_combined.py >> combined.log 2>&1 && say "обучено" || say "ОБУЧЕНИЕ УПАЛО"

say "проверка на наших объектах: старая модель"
"$PY" scripts/eval_on_ours.py --model models/aerialwaste_chip.joblib >> combined.log 2>&1
say "проверка на наших объектах: новая модель"
"$PY" scripts/eval_on_ours.py --model models/combined_chip.joblib >> combined.log 2>&1

say "═══ модель готова, сравнение в combined.log ═══"
