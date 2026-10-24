#!/usr/bin/env bash
# Ночной прогон по поясам вокруг Астаны — последовательно, чтобы не делить
# машину между тремя тяжёлыми процессами.
#
# Одного кольца мало: там промзона и стройки. Свалки возникают у сёл —
# Коянды, Жибек Жолы, Караоткель, — куда возят мусор из города и где нет
# ни полигона, ни контроля.
cd "$(dirname "$0")/.." || exit 1
PY=".venv/Scripts/python.exe"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a overnight.log; }
wait_file() { local p="$1" l="${2:-300}" s=0
  while [ ! -f "$p" ] && [ "$s" -lt $((l*60)) ]; do sleep 30; s=$((s+30)); done; [ -f "$p" ]; }

say "═══ ночь: пояса вокруг Астаны ═══"

say "жду восточный прогон, он уже идёт"
if wait_file outputs_astana_east/candidates_raw.geojson 300; then
  bash scripts/wait_stable.sh outputs_astana_east/candidates_raw.geojson 60 20 >/dev/null
  say "восток: прогон дошёл, досчитываю"
  "$PY" scripts/finish_city.py astana_east >> finish_astana_east.log 2>&1 \
    && say "восток досчитан" || say "ВОСТОК: ДОСЧЁТ УПАЛ"
  [ -f outputs_astana_east/candidates.geojson ] \
    && say "восток: объектов $(grep -o 'candidate_id' outputs_astana_east/candidates.geojson | wc -l)" \
    || say "восток: объектов нет"
fi

for area in astana_southeast astana_west; do
  say "прогон $area"
  "$PY" scripts/run_city.py "$area" >> "run_${area}.log" 2>&1 \
    && say "$area посчитан" || say "$area: ПРОГОН УПАЛ"
  if [ -f "outputs_${area}/candidates_raw.geojson" ]; then
    say "досчёт $area"
    "$PY" scripts/finish_city.py "$area" >> "finish_${area}.log" 2>&1 \
      && say "$area досчитан" || say "$area: ДОСЧЁТ УПАЛ"
    [ -f "outputs_${area}/candidates.geojson" ] \
      && say "$area: объектов $(grep -o 'candidate_id' "outputs_${area}/candidates.geojson" | wc -l)" \
      || say "$area: объектов нет"
  fi
done

say "═══ пояса посчитаны — дальше слияние и просмотр ═══"
