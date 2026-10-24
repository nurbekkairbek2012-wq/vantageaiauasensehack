#!/usr/bin/env bash
# Шымкент на новой области — после того, как освободится машина.
cd "$(dirname "$0")/.." || exit 1
PY=".venv/Scripts/python.exe"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a overnight.log; }
wait_file() { local p="$1" l="${2:-240}" s=0
  while [ ! -f "$p" ] && [ "$s" -lt $((l*60)) ]; do sleep 20; s=$((s+20)); done; [ -f "$p" ]; }

say "═══ цепочка 7: Шымкент на новой области ═══"
say "жду окончания Алматы, чтобы не делить машину"
wait_file outputs_almaty/candidates_raw.geojson 240
bash scripts/wait_stable.sh outputs_almaty/candidates_raw.geojson 60 20 >/dev/null
# Досчёт Алматы делает цепочка 6; дадим ей закончить.
sleep 300

rm -rf outputs_shymkent
say "прогон Шымкента"
"$PY" scripts/run_city.py shymkent >> run_shymkent2.log 2>&1 && say "прогон готов" || say "ПРОГОН УПАЛ"
if [ -f outputs_shymkent/candidates_raw.geojson ]; then
  say "досчёт Шымкента"
  "$PY" scripts/finish_city.py shymkent >> finish_shymkent2.log 2>&1 && say "досчитан" || say "ДОСЧЁТ УПАЛ"
  if [ -f outputs_shymkent/candidates.geojson ]; then
    say "Шымкент: объектов $(grep -o 'candidate_id' outputs_shymkent/candidates.geojson | wc -l)"
  else
    say "Шымкент: снова ноль объектов"
  fi
fi
say "═══ цепочка 7 закончена ═══"
