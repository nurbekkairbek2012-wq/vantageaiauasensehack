/* Карта объектов.
 *
 * Leaflet живёт императивно и в React не заворачивается: он сам владеет
 * DOM внутри контейнера. Поэтому здесь один useEffect на создание карты и
 * отдельные — на слои. Оборачивать каждый маркер в компонент значило бы
 * пересоздавать их на каждый рендер списка.
 *
 * Подложка по умолчанию — спутниковый снимок. Раньше была координатная
 * сетка ради работы без интернета, и это оказалось плохим обменом: свалка
 * на пустом фоне не доказывает ничего, а на снимке доказывает сама себя.
 *
 * Зоны риска рисуются тепловой поверхностью, а не заливкой ячеек. Ячейка —
 * это шаг расчётной сетки, и показывать её границы значит утверждать, что
 * риск обрывается на километровой меже. Он не обрывается.
 */

import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { createBasemapLayer, type Basemap } from './basemaps';
import { createHeatOverlay, riskToPoints, type HeatOverlay } from './HeatOverlay';

export type { Basemap };

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
  const heat = useRef<HeatOverlay | null>(null);
  const layers = useRef<Record<string, L.LayerGroup>>({});
  const shapes = useRef<Map<string, L.Path>>(new Map());
  const fitted = useRef(false);

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
    tiles.current = createBasemapLayer(basemap).addTo(m);
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
        color: '#c4b5fd', weight: 1.5, fillColor: '#7c3aed', fillOpacity: 0.35,
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
    //
    // На снимке кольцо получило тёмную подложку: белая линия поверх
    // светлого поля — например зимнего снега или бетона — исчезает.
    candidates.features.forEach((f) => {
      const b = L.geoJSON(f).getBounds();
      const id = String(f.properties?.candidate_id ?? '');
      const halo = L.circleMarker(b.getCenter(), {
        radius: 11, color: '#0d0918', weight: 4, fill: false, opacity: 0.55,
      });
      const ring = L.circleMarker(b.getCenter(), {
        radius: 11, color: '#ede9fe', weight: 2, fill: false, opacity: 0.95,
      });
      const dot = L.circleMarker(b.getCenter(), {
        radius: 3.5, color: '#ede9fe', weight: 0, fillColor: '#ede9fe', fillOpacity: 1,
      });
      group.addLayer(halo);
      [ring, dot].forEach((marker) => {
        marker.on('click', (e) => { L.DomEvent.stop(e); onSelect(id); });
        marker.bindTooltip(id, { direction: 'top', offset: [0, -12] });
        group.addLayer(marker);
      });
    });

    // Подгонка охвата — один раз. Повторная на каждое обновление слоя
    // отменяла бы выбор объекта: карта отпрыгивала бы обратно на область.
    if (candidates.features.length && !fitted.current) {
      m.fitBounds(L.geoJSON(candidates).getBounds().pad(0.25), { animate: false });
      fitted.current = true;
    }
  }, [candidates, onSelect]);

  useEffect(() => {
    const group = layers.current.registry;
    if (!group) return;
    group.clearLayers();
    if (!registry || !showRegistry) return;
    group.addLayer(
      L.geoJSON(registry, {
        style: () => ({ color: '#7dd3fc', weight: 1.5, dashArray: '5 4', fillOpacity: 0.08 }),
        onEachFeature: (f, l) =>
          l.bindTooltip(`${f.properties?.name ?? 'объект обращения с отходами'} · известен публично`, {
            direction: 'top',
          }),
      }),
    );
  }, [registry, showRegistry]);

  // зоны риска — тепловой поверхностью
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (!showRisk || !risk) {
      if (heat.current) { m.removeLayer(heat.current); heat.current = null; }
      return;
    }
    if (!heat.current) {
      heat.current = createHeatOverlay(riskToPoints(risk), { kind: 'risk', opacity: 0.72 });
      heat.current.addTo(m);
    } else {
      heat.current.setPoints(riskToPoints(risk));
    }
  }, [risk, showRisk]);

  // выделение
  useEffect(() => {
    shapes.current.forEach((shape, id) => {
      shape.setStyle(
        id === selected
          ? { color: '#ede9fe', weight: 3, fillColor: '#a78bfa', fillOpacity: 0.6 }
          : { color: '#c4b5fd', weight: 1.5, fillColor: '#7c3aed', fillOpacity: 0.35 },
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

  return (
    <div className="relative h-full w-full bg-soot">
      <div ref={host} className="absolute inset-0" />
    </div>
  );
}
