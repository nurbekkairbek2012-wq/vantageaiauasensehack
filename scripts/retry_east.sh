#!/usr/bin/env bash
# Повтор досчёта востока: Overpass лежал, и первая попытка не прошла.
cd "$(dirname "$0")/.." || exit 1
PY=".venv/Scripts/python.exe"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a overnight.log; }
for attempt in 1 2 3 4 5 6; do
  [ -f outputs_astana_east/candidates.geojson ] && { say "восток уже досчитан"; exit 0; }
  say "восток: попытка досчёта $attempt"
  "$PY" scripts/finish_city.py astana_east >> finish_astana_east.log 2>&1
  if [ -f outputs_astana_east/candidates.geojson ]; then
    say "восток досчитан: объектов $(grep -o 'candidate_id' outputs_astana_east/candidates.geojson | wc -l)"
    exit 0
  fi
  say "восток: не вышло, жду 10 минут (Overpass лежит)"
  sleep 600
done
say "восток: шесть попыток впустую — Overpass недоступен долго"
