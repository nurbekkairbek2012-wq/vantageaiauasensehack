#!/usr/bin/env bash
# Ждать, пока файл перестанет меняться.
#
# Определять окончание процесса по логу оказалось нельзя: pgrep в этой
# среде отсутствует, wmic процесс не находит, а лог пишется не в UTF-8 —
# первая ночная цепочка застряла именно на grep по слову «Сводка».
#
# Размер файла — признак, не зависящий ни от кодировки, ни от системы.
#   wait_stable.sh ФАЙЛ [СЕКУНД_ПОКОЯ] [ПРЕДЕЛ_МИНУТ]
FILE="$1"; QUIET="${2:-90}"; LIMIT="${3:-240}"
prev=-1; still=0; spent=0
while [ "$spent" -lt $((LIMIT * 60)) ]; do
  size=$(stat -c%s "$FILE" 2>/dev/null || echo 0)
  if [ "$size" = "$prev" ] && [ "$size" != "0" ]; then
    still=$((still + 15))
    [ "$still" -ge "$QUIET" ] && { echo "стабилен: $FILE ($((size/1048576)) МБ)"; exit 0; }
  else
    still=0; prev=$size
  fi
  sleep 15; spent=$((spent + 15))
done
echo "предел ожидания: $FILE"; exit 1
