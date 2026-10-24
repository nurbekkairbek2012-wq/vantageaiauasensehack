#!/usr/bin/env bash
# Пересчёт городов: слои OSM во время первого досчёта пришли пустыми.
cd "$(dirname "$0")/.." || exit 1
PY=".venv/Scripts/python.exe"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a overnight.log; }
say "═══ цепочка 5: пересчёт городов с живым Overpass ═══"
for c in almaty shymkent; do
  say "досчёт $c"
  "$PY" scripts/finish_city.py "$c" >> "finish_${c}2.log" 2>&1 \
    && say "$c готов" || say "$c УПАЛ"
  if [ -f "outputs_${c}/candidates.geojson" ]; then
    say "$c: объектов $(grep -o 'candidate_id' "outputs_${c}/candidates.geojson" | wc -l)"
  else
    say "$c: объектов НЕТ"
  fi
done
say "═══ цепочка 5 закончена ═══"
