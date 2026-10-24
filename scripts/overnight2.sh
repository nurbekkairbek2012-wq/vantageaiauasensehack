#!/usr/bin/env bash
# Ночная цепочка, вторая редакция.
#
# Отличие от первой: окончание шага определяется тем, что файл перестал
# расти, а не поиском слова в логе. Логи пишутся не в UTF-8, и grep по
# «Сводка» не срабатывал никогда — первая цепочка простояла час впустую.
cd "$(dirname "$0")/.." || exit 1
PY=".venv/Scripts/python.exe"
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a overnight.log; }

say "═══ цепочка 2 запущена ═══"

say "1/6 жду второй досчёт"
bash scripts/wait_stable.sh finish_all2.log 120 90
say "1/6 досчёт закончен"

# Воронка: пайплайн научился её сохранять уже после запуска досчёта,
# поэтому один быстрый повтор — без снимков и доверификации.
say "2/6 пересчёт воронки"
"$PY" scripts/finish_ring.py --no-signals --no-verify >> funnel_run.log 2>&1
cp -f outputs_real/funnel.json web-next/public/data/funnel.json 2>/dev/null
say "2/6 воронка: $(cat outputs_real/funnel.json 2>/dev/null | head -c 120)"

say "3/6 сборка сайта"
npm run build --prefix web-next >> build.log 2>&1 && say "3/6 собрано" || say "3/6 СБОРКА УПАЛА"

say "4/6 дымовой тест"
"$PY" scripts/smoke.py >> smoke.log 2>&1 && say "4/6 все страницы чисты" || say "4/6 ЕСТЬ ЗАМЕЧАНИЯ, см. smoke.log"

say "5/6 жду архив AerialWaste"
bash scripts/wait_stable.sh data/aerialwaste/images0.zip 90 120
SIZE=$(stat -c%s data/aerialwaste/images0.zip 2>/dev/null || echo 0)
say "5/6 архив $((SIZE/1048576)) МБ"

if [ "$SIZE" -lt 2500000000 ]; then
  say "5/6 архив недокачан — обучение пропускаю"
else
  say "6/6 эмбеддинги, обучение и проверка переноса"
  "$PY" scripts/train_aerialwaste.py >> aerialwaste.log 2>&1 && \
  "$PY" scripts/eval_on_ours.py >> aerialwaste.log 2>&1
  say "6/6 готово"
fi

say "═══ цепочка 2 закончена ═══"
