#!/usr/bin/env bash
# Города: досчёт Алматы, затем прогон и досчёт Шымкента.
#
# ── Про признак окончания ───────────────────────────────────────────────
#
# Первая редакция ждала, пока перестанет расти лог прогона. Это оказалось
# неверно: чтение снимков идёт молча по нескольку минут, лог замолкает, и
# цепочка решила, что Алматы «не дошёл», — запустив Шымкент параллельно
# ему и обучению сети. Три тяжёлых процесса на одной машине.
#
# Признак окончания у прогона ровно один: файл candidates_raw.geojson,
# который пишется последней строкой. Его и ждём.
cd "$(dirname "$0")/.." || exit 1
PY=".venv/Scripts/python.exe"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a overnight.log; }

# ждать_файл ПУТЬ ПРЕДЕЛ_МИНУТ
wait_file() {
  local path="$1" limit="${2:-180}" spent=0
  while [ ! -f "$path" ] && [ "$spent" -lt $((limit * 60)) ]; do
    sleep 20; spent=$((spent + 20))
  done
  [ -f "$path" ]
}

say "═══ цепочка 4 (2-я редакция): города ═══"

say "жду результат прогона Алматы"
if wait_file outputs_almaty/candidates_raw.geojson 180; then
  # Файл появился — но пишется он не мгновенно; дадим дописаться.
  bash scripts/wait_stable.sh outputs_almaty/candidates_raw.geojson 30 10 >/dev/null
  say "Алматы: прогон дошёл, досчитываю"
  "$PY" scripts/finish_city.py almaty >> finish_almaty.log 2>&1 \
    && say "Алматы досчитан" || say "ДОСЧЁТ АЛМАТЫ УПАЛ"
else
  say "Алматы: результата нет и через три часа — дальше без него"
fi

say "прогон Шымкента"
"$PY" scripts/run_city.py shymkent >> run_shymkent.log 2>&1 \
  && say "Шымкент посчитан" || say "ПРОГОН ШЫМКЕНТА УПАЛ"

if [ -f outputs_shymkent/candidates_raw.geojson ]; then
  say "досчёт Шымкента"
  "$PY" scripts/finish_city.py shymkent >> finish_shymkent.log 2>&1 \
    && say "Шымкент досчитан" || say "ДОСЧЁТ ШЫМКЕНТА УПАЛ"
fi

say "═══ цепочка 4 закончена — слияние ждёт решения ═══"
