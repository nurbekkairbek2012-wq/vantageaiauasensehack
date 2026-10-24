/* Тепловая плотность объектов, накопленная по годам.
 *
 * Реализовано через L.GridLayer, а не отдельным canvas поверх карты:
 * GridLayer сам занимается панорамированием, зумом и выгрузкой невидимых
 * тайлов. Слой поверх карты пришлось бы синхронизировать вручную, и он
 * отставал бы на каждом перетаскивании.
 *
 * Радиус влияния задан в МЕТРАХ, а не в пикселях. Пиксельный радиус
 * означал бы, что при отдалении карты пятна расплываются на десятки
 * километров, и «плотность» показывала бы масштаб просмотра, а не
 * положение дел на земле.
 *
 * Рисование в два прохода: сначала копятся полупрозрачные пятна в режиме
 * lighter, потом накопленная яркость переводится в цвет по палитре. Одним
 * проходом получилась бы не плотность, а набор одинаковых пятен.
 */

import L from 'leaflet';

export const HEAT_RADIUS_M = 1200;
const MIN_PX = 10;
const MAX_PX = 260;

/* Выше этого зума слой не рисуется: радиус упирается в потолок в пикселях,
   и поверхность начинает показывать охват меньше настоящего — то есть
   врать. На таком приближении смотрят на отдельный объект. */
export const HEAT_MAX_ZOOM = 15;

const PALETTE: [number, [number, number, number, number]][] = [
  [0.0, [0, 0, 0, 0]],
  [0.25, [76, 29, 149, 90]],
  [0.55, [124, 58, 237, 165]],
  [0.8, [167, 139, 250, 210]],
  [1.0, [237, 233, 254, 240]],
];

export type HeatPoint = { lat: number; lon: number; weight: number; year: number | null };

function paletteAt(t: number) {
  for (let i = 1; i < PALETTE.length; i++) {
    const [hiStop, hi] = PALETTE[i];
    if (t > hiStop && i < PALETTE.length - 1) continue;
    const [loStop, lo] = PALETTE[i - 1];
    const k = hiStop === loStop ? 0 : Math.min(1, (t - loStop) / (hiStop - loStop));
    return lo.map((v, j) => Math.round(v + (hi[j] - v) * k)) as [number, number, number, number];
  }
  return PALETTE[PALETTE.length - 1][1];
}

function radiusPx(zoom: number, lat = 51.15) {
  const mpp = (156543.03392 * Math.cos((lat * Math.PI) / 180)) / 2 ** zoom;
  return Math.max(MIN_PX, Math.min(MAX_PX, HEAT_RADIUS_M / mpp));
}

type HeatOptions = L.GridLayerOptions & { points?: HeatPoint[]; year?: number | null };

/* Внутренности слоя, которые Leaflet раздаёт через `this`. Типы Leaflet их
   не описывают: GridLayerOptions закрыт для своих полей, а _map вообще
   приватное. Один явный интерфейс честнее россыпи `as unknown as`. */
type HeatSelf = {
  options: HeatOptions;
  getTileSize(): L.Point;
  redraw(): void;
  _map: L.Map;
};

export const HeatLayer = L.GridLayer.extend({
  options: { points: [], year: null } as HeatOptions,

  setPoints(this: HeatSelf, points: HeatPoint[]) {
    this.options.points = points;
    this.redraw();
  },

  setYear(this: HeatSelf, year: number | null) {
    this.options.year = year;
    this.redraw();
  },

  createTile(this: HeatSelf, coords: L.Coords) {
    const self = this;
    const size = self.getTileSize();
    const tile = L.DomUtil.create('canvas', 'leaflet-tile') as HTMLCanvasElement;
    tile.width = size.x;
    tile.height = size.y;
    const ctx = tile.getContext('2d');
    if (!ctx) return tile;

    const limit = self.options.year ?? null;
    const points = (self.options.points ?? []).filter(
      (p) => limit == null || (p.year != null && p.year <= limit),
    );
    if (!points.length) return tile;

    const origin = coords.scaleBy(size);
    const radius = radiusPx(coords.z);

    ctx.globalCompositeOperation = 'lighter';
    for (const point of points) {
      const projected = self._map.project(L.latLng(point.lat, point.lon), coords.z);
      const x = projected.x - origin.x;
      const y = projected.y - origin.y;
      if (x < -radius || y < -radius || x > size.x + radius || y > size.y + radius) continue;

      const peak = 0.1 + 0.3 * point.weight;
      const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius);
      gradient.addColorStop(0, `rgba(255,255,255,${peak})`);
      gradient.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = gradient;
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fill();
    }

    const image = ctx.getImageData(0, 0, size.x, size.y);
    const data = image.data;
    for (let i = 0; i < data.length; i += 4) {
      const intensity = data[i + 3] / 255;
      if (intensity <= 0) continue;
      const [r, g, b, a] = paletteAt(intensity);
      data[i] = r;
      data[i + 1] = g;
      data[i + 2] = b;
      data[i + 3] = a;
    }
    ctx.putImageData(image, 0, 0);
    return tile;
  },
});

export type HeatLayerInstance = L.GridLayer & {
  setPoints(points: HeatPoint[]): void;
  setYear(year: number | null): void;
};

export function createHeatLayer(options: HeatOptions): HeatLayerInstance {
  const Ctor = HeatLayer as unknown as new (o: HeatOptions) => HeatLayerInstance;
  return new Ctor({ maxZoom: HEAT_MAX_ZOOM, opacity: 0.9, ...options });
}
