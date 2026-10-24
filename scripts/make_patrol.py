"""Выгрузить ответ модели прогноза: куда ехать и где ставить знак.

Модель риска считает вероятность по 19 621 ячейке, но вероятность — это не
ответ. Ответ — короткий список мест, куда имеет смысл послать人 машину на
этой неделе. Его и выгружаем.

Точная вероятность в файл НЕ попадает, и это не формальность: публичный
контур не должен отдавать градиент уверенности по всей области — по нему
восстанавливается вся модель. Остаётся ранг и класс, то есть порядок
объезда, которого достаточно для работы.
"""

import json
import sys
from pathlib import Path

import geopandas as gpd

from vantage.config import load_settings
from vantage.risk import recommend_placements

OUTPUTS = Path("outputs_real")
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 20

settings = load_settings()
private = gpd.read_file(OUTPUTS / "risk_private.geojson")
print(f"ячеек сетки: {len(private)}")

picked = recommend_placements(private, BUDGET)
print(f"отобрано мест: {len(picked)}")

# recommend_placements возвращает и точную вероятность — её надо снять
# здесь, а не надеяться, что её не заметят на фронтенде.
picked = picked.merge(
    private[["cell_id", "dist_road_m", "dist_settlement_m", "existing_density_3km"]],
    on="cell_id",
    how="left",
)
wgs = picked.to_crs(settings.project.crs_output).copy()
wgs["rank"] = wgs["placement_rank"].astype(int)
for column in ("dist_road_m", "dist_settlement_m"):
    wgs[column] = wgs[column].round(0)
wgs["density_3km"] = wgs["existing_density_3km"].round(2)
wgs = wgs[["rank", "dist_road_m", "dist_settlement_m", "density_3km", "geometry"]].sort_values("rank")

target = OUTPUTS / "patrol.geojson"
wgs.to_file(target, driver="GeoJSON")

# Проверка того же правила, что стережёт публикацию: точной вероятности
# в файле быть не должно.
leaked = '"risk"' in target.read_text(encoding="utf-8")
print("точная вероятность в файле:", "ЕСТЬ — это утечка" if leaked else "нет")
if leaked:
    raise SystemExit(1)

print("записано:", target, json.dumps({"мест": len(wgs)}, ensure_ascii=False))
