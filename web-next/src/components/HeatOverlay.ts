/* Тепловая поверхность: плотность объектов и зоны риска.
 *
 * Раньше это был L.GridLayer — слой, нарезанный на тайлы. Расчёт был на то,
 * что Leaflet сам займётся панорамированием и выгрузкой невидимого. Расчёт
 * не оправдался дважды, и обе поломки видны глазом.
 *
 * Первое: каждое изменение года требует redraw(), а redraw() у GridLayer
 * пересоздаёт все тайлы. Между «старые убрали» и «новые нарисовали» есть
 * кадр пустоты — на воспроизведении таймлапса это ровное мигание раз в год
 * вместо плавного хода времени.
 *
 * Второе: у тайлов есть швы. Пятно, попавшее на границу, рисуется дважды
 * с накоплением по краям, и поверхность выглядит сложенной из квадратов —
 * ровно то, чего тепловая карта не должна показывать никогда, потому что
 * квадрат читается как измеренная ячейка, а не как след сглаживания.
 *
 * Здесь один холст на всю видимую область. Панорамирование достаётся
 * бесплатно: холст лежит в overlayPane, который Leaflet двигает сам.
 * Перерисовка нужна только по окончании движения и по кадрам анимации.
 *
 * Радиус влияния задан в МЕТРАХ. Пиксельный радиус означал бы, что при
 * отдалении пятна расплываются на десятки километров, и «плотность»
 * показывала бы масштаб просмотра, а не положение дел на земле.
 */

import L from 'leaflet';

export type HeatPoint = { lat: number; lon: number; weight: number; year: number | null };

export type HeatKind = 'density' | 'risk';

/* Два назначения — два радиуса. Плотность объектов отвечает на вопрос
   «где скапливается», и ей нужно широкое сглаживание. Риск считается по
   ячейкам около километра, и раздувать его вдвое значит размазывать
   границу зоны там, где модель её провела. */
const RADIUS_M: Record<HeatKind, number> = { density: 1200, risk: 900 };

const MIN_PX = 8;
const MAX_PX = 320;

/** Запас холста за краями экрана, чтобы при перетаскивании не было пустоты. */
const PAD = 0.18;

/** За сколько лет пятно набирает полную силу. Ноль дал бы то же мигание. */
const FADE_YEARS = 0.85;

const PALETTE: [number, [number, number, number, number]][] = [
  [0.0, [0, 0, 0, 0]],
  [0.22, [76, 29, 149, 80]],
  [0.5, [124, 58, 237, 155]],
  [0.78, [167, 139, 250, 205]],
  [1.0, [237, 233, 254, 238]],
];

/* Палитра переводится в таблицу на 256 значений один раз. Иначе
   интерполяция считалась бы для каждого пикселя каждого кадра — миллион
   вызовов на кадр там, где хватает одного просмотра таблицы. */
const LUT = (() => {
  const table = new Uint8ClampedArray(256 * 4);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    let color = PALETTE[PALETTE.length - 1][1];
    for (let j = 1; j < PALETTE.length; j++) {
      const [hiStop, hi] = PALETTE[j];
      const [loStop, lo] = PALETTE[j - 1];
      if (t > hiStop && j < PALETTE.length - 1) continue;
      const k = hiStop === loStop ? 0 : Math.min(1, Math.max(0, (t - loStop) / (hiStop - loStop)));
      color = lo.map((v, c) => v + (hi[c] - v) * k) as [number, number, number, number];
      break;
    }
    table[i * 4] = color[0];
    table[i * 4 + 1] = color[1];
    table[i * 4 + 2] = color[2];
    table[i * 4 + 3] = color[3];
  }
  return table;
})();

function metersPerPixel(zoom: number, lat: number) {
  return (156543.03392 * Math.cos((lat * Math.PI) / 180)) / 2 ** zoom;
}

/** Мягкое начало и конец: линейное появление читается как скачок яркости. */
function smoothstep(x: number) {
  const t = Math.min(1, Math.max(0, x));
  return t * t * (3 - 2 * t);
}

type Options = { kind?: HeatKind; opacity?: number };

export class HeatOverlay extends L.Layer {
  private points: HeatPoint[] = [];
  private time: number | null = null;
  private kind: HeatKind;
  private opacity: number;

  private canvas: HTMLCanvasElement | null = null;
  private ctx: CanvasRenderingContext2D | null = null;
  private sprite: HTMLCanvasElement | null = null;
  private spriteRadius = -1;
  private frame = 0;

  constructor(points: HeatPoint[] = [], options: Options = {}) {
    super();
    this.points = points;
    this.kind = options.kind ?? 'density';
    this.opacity = options.opacity ?? 1;
  }

  onAdd(map: L.Map): this {
    const canvas = L.DomUtil.create('canvas', 'leaflet-layer vantage-heat') as HTMLCanvasElement;
    canvas.style.opacity = String(this.opacity);
    canvas.style.pointerEvents = 'none';
    // Под маркерами и полигонами: тепловая поверхность — это контекст,
    // а не данные, по которым кликают.
    canvas.style.zIndex = '200';
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    map.getPanes().overlayPane.appendChild(canvas);

    map.on('moveend zoomend resize', this.reset, this);
    if (map.options.zoomAnimation && L.Browser.any3d) {
      map.on('zoomanim', this.onZoomAnim, this);
    }
    this.reset();
    return this;
  }

  onRemove(map: L.Map): this {
    if (this.frame) { cancelAnimationFrame(this.frame); this.frame = 0; }
    map.off('moveend zoomend resize', this.reset, this);
    map.off('zoomanim', this.onZoomAnim, this);
    this.canvas?.remove();
    this.canvas = null;
    this.ctx = null;
    return this;
  }

  setPoints(points: HeatPoint[]): this {
    this.points = points;
    this.schedule();
    return this;
  }

  /** Время как непрерывная величина, а не номер года: 2021.4 — допустимо. */
  setTime(time: number | null): this {
    this.time = time;
    this.schedule();
    return this;
  }

  setHeatOpacity(value: number): this {
    this.opacity = value;
    if (this.canvas) this.canvas.style.opacity = String(value);
    return this;
  }

  /* Во время анимации кадры приходят чаще, чем их имеет смысл рисовать.
     Склейка через requestAnimationFrame гарантирует один проход на кадр
     экрана, сколько бы раз ни позвали. */
  private schedule() {
    if (this.frame || !this._map) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.draw();
    });
  }

  private onZoomAnim(event: L.ZoomAnimEvent) {
    const map = this._map;
    if (!map || !this.canvas) return;
    /* Оба метода приватные. Публичного способа удержать холст на месте
       во время анимации зума у Leaflet нет — собственные слои библиотеки
       пользуются этими же двумя. Один явный тип честнее россыпи as any. */
    const inner = map as unknown as {
      _getCenterOffset(center: L.LatLng): L.Point;
      _getMapPanePos(): L.Point;
    };
    const scale = map.getZoomScale(event.zoom, map.getZoom());
    const offset = inner
      ._getCenterOffset(event.center)
      .multiplyBy(-scale)
      .subtract(inner._getMapPanePos());
    L.DomUtil.setTransform(this.canvas, offset, scale);
  }

  private reset() {
    const map = this._map;
    const canvas = this.canvas;
    if (!map || !canvas) return;

    const size = map.getSize();
    const padX = Math.round(size.x * PAD);
    const padY = Math.round(size.y * PAD);
    const width = size.x + padX * 2;
    const height = size.y + padY * 2;

    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
    }

    const corner = map.containerPointToLayerPoint([-padX, -padY]);
    L.DomUtil.setTransform(canvas, corner, 1);
    this.draw();
  }

  /** Спрайт пятна рисуется один раз на радиус и потом только копируется. */
  private spriteFor(radius: number) {
    if (this.sprite && this.spriteRadius === radius) return this.sprite;
    const size = radius * 2;
    const sprite = document.createElement('canvas');
    sprite.width = size;
    sprite.height = size;
    const ctx = sprite.getContext('2d');
    if (ctx) {
      const gradient = ctx.createRadialGradient(radius, radius, 0, radius, radius, radius);
      gradient.addColorStop(0, 'rgba(255,255,255,1)');
      gradient.addColorStop(0.45, 'rgba(255,255,255,0.42)');
      gradient.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, size, size);
    }
    this.sprite = sprite;
    this.spriteRadius = radius;
    return sprite;
  }

  private draw() {
    const map = this._map;
    const canvas = this.canvas;
    const ctx = this.ctx;
    if (!map || !canvas || !ctx) return;

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!this.points.length) return;

    const size = map.getSize();
    const padX = Math.round(size.x * PAD);
    const padY = Math.round(size.y * PAD);

    const zoom = map.getZoom();
    const mpp = metersPerPixel(zoom, map.getCenter().lat);
    const radius = Math.round(
      Math.max(MIN_PX, Math.min(MAX_PX, RADIUS_M[this.kind] / mpp)),
    );
    const sprite = this.spriteFor(radius);

    // Первый проход: копим яркость. lighter складывает перекрытия — из
    // этого и получается плотность, а не набор одинаковых пятен.
    ctx.globalCompositeOperation = 'lighter';

    const bounds = map.getBounds().pad(PAD + 0.05);
    let painted = 0;

    for (const point of this.points) {
      let strength = point.weight;

      if (this.time != null && point.year != null) {
        strength *= smoothstep((this.time - point.year) / FADE_YEARS);
      }
      if (strength <= 0.001) continue;
      if (!bounds.contains([point.lat, point.lon])) continue;

      const pixel = map.latLngToContainerPoint([point.lat, point.lon]);
      ctx.globalAlpha = Math.min(1, 0.12 + 0.55 * strength);
      ctx.drawImage(sprite, pixel.x + padX - radius, pixel.y + padY - radius);
      painted++;
    }

    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
    if (!painted) return;

    // Второй проход: накопленная альфа переводится в цвет по палитре.
    const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const data = image.data;
    for (let i = 0; i < data.length; i += 4) {
      const alpha = data[i + 3];
      if (alpha === 0) continue;
      const at = alpha * 4;
      data[i] = LUT[at];
      data[i + 1] = LUT[at + 1];
      data[i + 2] = LUT[at + 2];
      data[i + 3] = LUT[at + 3];
    }
    ctx.putImageData(image, 0, 0);
  }
}

export function createHeatOverlay(points: HeatPoint[] = [], options: Options = {}) {
  return new HeatOverlay(points, options);
}

/** Центры ячеек прогноза: полигоны риска приходят слипшимися в MultiPolygon. */
export function riskToPoints(risk: GeoJSON.FeatureCollection | null): HeatPoint[] {
  if (!risk) return [];
  const out: HeatPoint[] = [];
  let maxClass = 1;
  risk.features.forEach((f) => {
    const cls = Number(f.properties?.risk_class) || 1;
    if (cls > maxClass) maxClass = cls;
  });

  risk.features.forEach((f) => {
    const cls = Number(f.properties?.risk_class) || 1;
    const weight = cls / maxClass;
    const polygons: number[][][][] =
      f.geometry.type === 'MultiPolygon'
        ? (f.geometry.coordinates as number[][][][])
        : f.geometry.type === 'Polygon'
          ? [f.geometry.coordinates as number[][][]]
          : [];

    polygons.forEach((polygon) => {
      const ring = polygon[0];
      if (!ring?.length) return;
      let lon = 0;
      let lat = 0;
      for (const [x, y] of ring) { lon += x; lat += y; }
      out.push({ lat: lat / ring.length, lon: lon / ring.length, weight, year: null });
    });
  });
  return out;
}
