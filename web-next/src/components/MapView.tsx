/* Карта объектов.
 *
 * Leaflet живёт императивно и в React не заворачивается: он сам владеет
 * DOM внутри контейнера. Поэтому здесь один useEffect на создание карты и
 * отдельные — на слои. Оборачивать каждый маркер в компонент значило бы
 * пересоздавать их на каждый рендер списка.
 *
 * Подложка по умолчанию — координатная сетка, а не спутниковые тайлы.
 * Причина не эстетическая: карта обязана открываться без интернета на
 * площадке, а тайлы это сеть. Снимок включается кнопкой, когда сеть есть,
 * и тогда становится доказательством, а не фоном.
 */

import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

export type Basemap = 'grid' | 'sat' | 'scheme';

const TILES: Record<Exclude<Basemap, 'grid'>, { url: string; attribution: string }> = {
  sat: {
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Esri · Maxar',
  },
  scheme: {
    url: 'https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
    attribution: '© OpenStreetMap · © CARTO',
  },
};

type Props = {
  candidates: GeoJSON.FeatureCollection | null;
  registry: GeoJSON.FeatureCollection | null;
  risk: GeoJSON.FeatureCollection | null;
  selected: string | null;
  onSelect: (id: string | null) => void;
  basemap: Basemap;
  showRisk: boolean;
  showRegistry: boolean;
};

export function MapView({
  candidates, registry, risk, selected, onSelect, basemap, showRisk, showRegistry,
}: Props) {
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const tiles = useRef<L.TileLayer | null>(null);
  const layers = useRef<Record<string, L.LayerGroup>>({});
  const shapes = useRef<Map<string, L.Path>>(new Map());

  useEffect(() => {
    if (!host.current || map.current) return;
    const m = L.map(host.current, {
      zoomControl: false,
      attributionControl: true,
      preferCanvas: true,
      minZoom: 9,
      maxZoom: 18,
      zoomSnap: 0.5,
    }).setView([51.21, 71.5], 11);

    L.control.zoom({ position: 'bottomright' }).addTo(m);
    L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(m);

    layers.current = {
      risk: L.layerGroup().addTo(m),
      registry: L.layerGroup().addTo(m),
      candidates: L.layerGroup().addTo(m),
    };
    m.on('click', () => onSelect(null));
    map.current = m;
  }, [onSelect]);

  // подложка
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (tiles.current) { m.removeLayer(tiles.current); tiles.current = null; }
    if (basemap === 'grid') return;
    const cfg = TILES[basemap];
    tiles.current = L.tileLayer(cfg.url, {
      attribution: cfg.attribution, minZoom: 9, maxZoom: 18, keepBuffer: 2,
    }).addTo(m);
    tiles.current.bringToBack();
  }, [basemap]);

  // объекты
  useEffect(() => {
    const m = map.current;
    if (!m || !candidates) return;
    const group = layers.current.candidates;
    group.clearLayers();
    shapes.current.clear();

    const layer = L.geoJSON(candidates, {
      style: () => ({
        color: '#a78bfa', weight: 1.5, fillColor: '#7c3aed', fillOpacity: 0.35,
      }),
      onEachFeature: (f, l) => {
        const id = String(f.properties?.candidate_id ?? '');
        shapes.current.set(id, l as L.Path);
        l.on('click', (e) => { L.DomEvent.stop(e); onSelect(id); });
      },
    });
    group.addLayer(layer);

    // Объекты мелкие: на масштабе области полигон в 1800 м² — доли пикселя.
    // Кольцо-указатель делает их находимыми, не притворяясь, что объект
    // больше, чем он есть: сам полигон рисуется отдельно и в своём размере.
    candidates.features.forEach((f) => {
      const b = L.geoJSON(f).getBounds();
      const id = String(f.properties?.candidate_id ?? '');
      const ring = L.circleMarker(b.getCenter(), {
        radius: 11, color: '#ede9fe', weight: 2, fill: false, opacity: 0.9,
      });
      const dot = L.circleMarker(b.getCenter(), {
        radius: 3.5, color: '#ede9fe', weight: 0, fillColor: '#ede9fe', fillOpacity: 1,
      });
      [ring, dot].forEach((marker) => {
        marker.on('click', (e) => { L.DomEvent.stop(e); onSelect(id); });
        marker.bindTooltip(id, { direction: 'top', offset: [0, -12] });
        group.addLayer(marker);
      });
    });

    if (candidates.features.length) {
      m.fitBounds(L.geoJSON(candidates).getBounds().pad(0.25), { animate: false });
    }
  }, [candidates, onSelect]);

  useEffect(() => {
    const group = layers.current.registry;
    if (!group) return;
    group.clearLayers();
    if (!registry || !showRegistry) return;
    group.addLayer(
      L.geoJSON(registry, {
        style: () => ({ color: '#5b93c9', weight: 1.5, dashArray: '5 4', fillOpacity: 0.1 }),
        onEachFeature: (f, l) =>
          l.bindTooltip(`${f.properties?.name ?? 'объект обращения с отходами'} · известен публично`, {
            direction: 'top',
          }),
      }),
    );
  }, [registry, showRegistry]);

  useEffect(() => {
    const group = layers.current.risk;
    if (!group) return;
    group.clearLayers();
    if (!risk || !showRisk) return;
    group.addLayer(
      L.geoJSON(risk, {
        style: (f) => {
          const cls = Number(f?.properties?.risk_class) || 1;
          // Верхняя граница 0.22: на скриншоте заливка в 0.3 забивала и
          // объекты, и сетку — прогноз выглядел данными.
          return {
            color: '#7c3aed', weight: 0, fillColor: '#7c3aed',
            fillOpacity: 0.05 + cls * 0.045,
          };
        },
      }),
    );
    group.eachLayer((l) => (l as L.GeoJSON).bringToBack());
  }, [risk, showRisk]);

  // выделение
  useEffect(() => {
    shapes.current.forEach((shape, id) => {
      shape.setStyle(
        id === selected
          ? { color: '#ede9fe', weight: 3, fillColor: '#a78bfa', fillOpacity: 0.6 }
          : { color: '#a78bfa', weight: 1.5, fillColor: '#7c3aed', fillOpacity: 0.35 },
      );
    });
    const m = map.current;
    if (m && selected && shapes.current.has(selected)) {
      const target = shapes.current.get(selected)!;
      m.flyTo((target as L.Polygon).getBounds().getCenter(), Math.max(m.getZoom(), 15), {
        duration: 0.7,
      });
    }
  }, [selected]);

  // Сетка живёт на отдельном слое под картой, а не на самом контейнере
  // Leaflet. Его CSS задаёт `background` сокращённым свойством и сбрасывает
  // background-image вместе с цветом; спорить с этим через !important на
  // каждое свойство — гонка, которую проигрываешь при обновлении библиотеки.
  return (
    <div className="relative h-full w-full bg-soot map-grid">
      <div ref={host} className="absolute inset-0" />
    </div>
  );
}
