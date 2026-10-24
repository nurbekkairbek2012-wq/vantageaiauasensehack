"""Контекст из OpenStreetMap: рядом с чем стоит находка.

Свалки тяготеют к определённым местам: к грунтовым съездам, задворкам
промзон, железной дороге, окрестностям легальных полигонов. Здесь для
каждого объекта экзамена считается расстояние до ближайшего:

  rail_m        — железной дороги;
  industrial_m  — промзоны (landuse=industrial);
  landfill_m    — легального полигона или площадки отходов;
  track_m       — грунтовки (highway=track).

Проверка — та же, что у всех признаков: различает ли признак свалку и
не-свалку ВНУТРИ северного кольца (класс) и не мерит ли он просто район
(место). Запросы кэшируются: повторный запуск сети не требует.

    python scripts/osm_context.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
OUT = ROOT / "data/eval/osm_context.csv"

LAYERS = {
    "rail_m": 'way["railway"="rail"]',
    "industrial_m": 'way["landuse"="industrial"]',
    "landfill_m": 'nwr["landuse"="landfill"];nwr["amenity"~"^(waste_disposal|waste_transfer_station)$"]',
    "track_m": 'way["highway"="track"]',
}


def main() -> int:
    import numpy as np
    from build_eval_set import build
    from sklearn.metrics import roc_auc_score

    from vantage.context import OverpassClient, distance_to_layer, overpass_to_gdf

    frame = build().to_crs(32642).reset_index(drop=True)
    w, s, e, n = frame.to_crs(4326).total_bounds
    bbox = f"{s - 0.01},{w - 0.01},{n + 0.01},{e + 0.01}"
    client = OverpassClient(ROOT / "data/cache")
    for column, selector in LAYERS.items():
        parts = "".join(f"{p}({bbox});" for p in selector.split(";"))
        payload = client.query(f"[out:json][timeout:180];({parts});out geom;")
        layer = overpass_to_gdf(payload, target_crs="EPSG:32642")
        frame[column] = distance_to_layer(frame, layer) if not layer.empty else np.inf
        print(f"   {column:14} объектов OSM: {len(layer)}")

    frame[["candidate_id", "truth", "area", *LAYERS]].to_csv(OUT, index=False)
    d = frame[frame["truth"].isin(["landfill", "not_landfill"])]
    y = (d["truth"] == "landfill").to_numpy().astype(int)
    home = (d["area"] == "outputs_real").to_numpy()
    print(f"\n   {'признак':14} {'класс':>6} {'место':>6}  (ближе = свалка? AUC по −расстоянию)")
    for column in LAYERS:
        x = -np.log1p(d[column].replace(np.inf, 1e6).to_numpy())
        print(f"   {column:14} {roc_auc_score(y[home], x[home]):6.2f} "
              f"{roc_auc_score(home[y == 0], x[y == 0]):6.2f}")
    print(f"── записано в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
