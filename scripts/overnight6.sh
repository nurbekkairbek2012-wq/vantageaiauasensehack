#!/usr/bin/env bash
# Алматы на новой области: прогон уже идёт, здесь досчёт после него.
cd "$(dirname "$0")/.." || exit 1
PY=".venv/Scripts/python.exe"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a overnight.log; }
wait_file() { local p="$1" l="${2:-200}" s=0
  while [ ! -f "$p" ] && [ "$s" -lt $((l*60)) ]; do sleep 20; s=$((s+20)); done; [ -f "$p" ]; }

say "═══ цепочка 6: Алматы на новой области ═══"
if wait_file outputs_almaty/candidates_raw.geojson 200; then
  bash scripts/wait_stable.sh outputs_almaty/candidates_raw.geojson 30 10 >/dev/null
  say "прогон дошёл, досчитываю"
  "$PY" scripts/finish_city.py almaty >> finish_almaty3.log 2>&1 && say "досчитан" || say "ДОСЧЁТ УПАЛ"
  if [ -f outputs_almaty/candidates.geojson ]; then
    say "Алматы: объектов $(grep -o 'candidate_id' outputs_almaty/candidates.geojson | wc -l)"
  else
    say "Алматы: снова ноль объектов — область всё ещё не подходит"
  fi
else
  say "прогон не дошёл за отведённое время"
fi
say "═══ цепочка 6 закончена ═══"
