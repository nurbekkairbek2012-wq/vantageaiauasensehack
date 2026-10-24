#!/usr/bin/env bash
# Просмотр нового пояса: оценка моделью, потом контактные листы.
#   review_belt.sh astana_east
cd "$(dirname "$0")/.." || exit 1
PY=".venv/Scripts/python.exe"
AREA="$1"
[ -z "$AREA" ] && { echo "укажите область: astana_east | astana_southeast | astana_west"; exit 1; }
DIR="outputs_${AREA}"
[ -f "$DIR/candidates.geojson" ] || { echo "нет $DIR/candidates.geojson"; exit 1; }

echo "── оценка моделью по снимку"
"$PY" scripts/attach_chipmodel.py --outputs "$DIR"
echo "── контактные листы"
"$PY" scripts/review_sheets.py --outputs "$DIR" --out "review_${AREA}"
echo "готово: review_${AREA}/"
