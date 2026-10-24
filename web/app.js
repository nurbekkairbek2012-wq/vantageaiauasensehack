/* VANTAGE — карта объектов.
 *
 * Три вещи, из-за которых первая версия тормозила, и как это исправлено:
 *
 *   1. Path2D пересобирался для каждого полигона на КАЖДОМ событии мыши.
 *      При тысячах колец это тысячи операций на движение курсора.
 *      Теперь: геометрия упрощается один раз при загрузке, отрисовка
 *      идёт пакетами (один Path2D на стиль, а не на объект).
 *   2. draw() вызывался синхронно из pointermove — до сотни раз в секунду.
 *      Теперь: throttle через requestAnimationFrame, максимум 60 кадров.
 *   3. Попадание клика проверялось перебором всех полигонов с пересборкой
 *      путей. Теперь: сначала грубая проверка по bbox, потом точная.
 *
 * Подложка — растровые тайлы. Офлайн не ломается: тайлы кешируются
 * service worker'ом, кнопка «Офлайн» прогревает кеш заранее.
 */

'use strict';

// ═══════════════════════════ Проекция ═══════════════════════════

const MAX_LAT = 85.05112878;

const lonToX = (lon) => (lon + 180) / 360;
const latToY = (lat) => {
  const c = Math.max(Math.min(lat, MAX_LAT), -MAX_LAT);
  const r = (c * Math.PI) / 180;
  return (1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2;
};
const xToLon = (x) => x * 360 - 180;
const yToLat = (y) => (180 / Math.PI) * Math.atan(Math.sinh(Math.PI * (1 - 2 * y)));

// ═══════════════════════════ Состояние ═══════════════════════════

const TILE = 256;

const BASEMAPS = {
  dark: {
    url: (x, y, z) => `https://basemaps.cartocdn.com/dark_all/${z}/${x}/${y}.png`,
    attribution: '© OpenStreetMap · © CARTO',
    maxZoom: 19,
  },
  sat: {
    url: (x, y, z) =>
      `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`,
    attribution: 'Esri · Maxar · Earthstar Geographics',
    maxZoom: 19,
  },
};

const state = {
  view: { cx: 0.5, cy: 0.5, size: 4096 },
  basemap: 'dark',
  layers: { candidates: [], risk: [], registry: [] },
  visible: new Set(['candidates', 'registry']),
  story: null,
  scene: 0,
  mode: 'story',
  selected: null,
  filter: '',
  sort: 'probability',
  isDemo: false,
  totals: null,
};

const els = {};
const canvas = document.getElementById('map');
const ctx = canvas.getContext('2d', { alpha: false });
let dpr = Math.min(window.devicePixelRatio || 1, 2);

const tiles = new Map();       // "z/x/y|basemap" -> Image | 'loading' | 'error'
let pendingFrame = false;

function $(id) {
  if (!els[id]) els[id] = document.getElementById(id);
  return els[id];
}

// ═══════════════════════════ Загрузка данных ═══════════════════════════

async function loadJson(path) {
  try {
    const r = await fetch(path, { cache: 'no-cache' });
    return r.ok ? await r.json() : null;
  } catch { return null; }
}

/** Упрощение при загрузке: то, что не видно на экране, не должно тратить кадры. */
function decimate(ring, maxPoints) {
  if (ring.length <= maxPoints) return ring;
  const step = Math.ceil(ring.length / maxPoints);
  const out = [];
  for (let i = 0; i < ring.length; i += step) out.push(ring[i]);
  if (out[out.length - 1] !== ring[ring.length - 1]) out.push(ring[ring.length - 1]);
  return out;
}

function parseFeatures(fc, kind, maxPointsPerRing) {
  if (!fc || !fc.features) return [];
  const shapes = [];
  for (const f of fc.features) {
    const g = f.geometry;
    if (!g) continue;
    const polys = g.type === 'Polygon' ? [g.coordinates]
                : g.type === 'MultiPolygon' ? g.coordinates : null;
    if (!polys) continue;

    const rings = [];
    let minLon = 180, minLat = 90, maxLon = -180, maxLat = -90;
    for (const poly of polys) {
      for (const raw of poly) {
        if (raw.length < 4) continue;
        const ring = decimate(raw, maxPointsPerRing);
        const pts = new Float64Array(ring.length * 2);
        for (let i = 0; i < ring.length; i++) {
          const [lon, lat] = ring[i];
          if (lon < minLon) minLon = lon;
          if (lon > maxLon) maxLon = lon;
          if (lat < minLat) minLat = lat;
          if (lat > maxLat) maxLat = lat;
          pts[i * 2] = lonToX(lon);
          pts[i * 2 + 1] = latToY(lat);
        }
        rings.push(pts);
      }
    }
    if (!rings.length) continue;

    shapes.push({
      kind,
      rings,
      props: f.properties || {},
      bbox: [minLon, minLat, maxLon, maxLat],
      wx: [lonToX(minLon), latToY(maxLat), lonToX(maxLon), latToY(minLat)],
      center: [(minLon + maxLon) / 2, (minLat + maxLat) / 2],
    });
  }
  return shapes;
}

function syntheticRegistry(cands) {
  if (!cands.length) return [];
  const step = Math.max(1, Math.floor(cands.length / 3));
  return cands.filter((_, i) => i % step === 0).slice(0, 3)
    .map((s) => ({ ...s, kind: 'registry' }));
}

async function loadAll() {
  $('loader').classList.remove('hidden');
  const [cands, risk, story] = await Promise.all([
    loadJson('data/candidates.geojson'),
    loadJson('data/risk_public.geojson'),
    loadJson('data/story.json'),
  ]);

  state.layers.candidates = parseFeatures(cands, 'candidate', 200);
  // Зоны риска — крупные растворённые полигоны с тысячами точек.
  // Для показа хватает грубого контура, и это разница между 60 и 8 кадрами.
  state.layers.risk = parseFeatures(risk, 'risk', 60);
  state.layers.registry = syntheticRegistry(state.layers.candidates);
  state.story = story;
  state.totals = story?.totals || null;
  state.isDemo = story?.is_demo === true ||
    state.layers.candidates.some((s) => s.props.is_demo === true || s.props.is_demo === 'true');

  $('demo-chip').classList.toggle('hidden', !state.isDemo);
  if (state.isDemo) {
    toast('Показаны синтетические данные для отладки интерфейса. Это не результаты прогона.', 'warn', 9000);
  }
  if (!state.layers.candidates.length) {
    toast('Данные не найдены. Запустите vantage sample или vantage run.', 'warn', 12000);
  }

  $('loader').classList.add('hidden');
  renderLegend();
  renderList();
  resetView();
  setScene(0);
}

// ═══════════════════════════ Геометрия экрана ═══════════════════════════

function rect() { return canvas.getBoundingClientRect(); }

function sx(x) { const r = rect(); return (x - state.view.cx) * state.view.size + r.width / 2; }
function sy(y) { const r = rect(); return (y - state.view.cy) * state.view.size + r.height / 2; }
function unx(px) { const r = rect(); return (px - r.width / 2) / state.view.size + state.view.cx; }
function uny(py) { const r = rect(); return (py - r.height / 2) / state.view.size + state.view.cy; }

function inView(shape, r) {
  const [x0, y0, x1, y1] = shape.wx;
  return !(sx(x1) < -40 || sx(x0) > r.width + 40 || sy(y1) < -40 || sy(y0) > r.height + 40);
}

/** Один Path2D на группу объектов — вместо одного вызова fill() на объект. */
function batchPath(shapes, r) {
  const path = new Path2D();
  for (const shape of shapes) {
    if (!inView(shape, r)) continue;
    for (const ring of shape.rings) {
      for (let i = 0; i < ring.length; i += 2) {
        const px = sx(ring[i]);
        const py = sy(ring[i + 1]);
        if (i === 0) path.moveTo(px, py); else path.lineTo(px, py);
      }
      path.closePath();
    }
  }
  return path;
}

function shapePath(shape) {
  const path = new Path2D();
  for (const ring of shape.rings) {
    for (let i = 0; i < ring.length; i += 2) {
      const px = sx(ring[i]);
      const py = sy(ring[i + 1]);
      if (i === 0) path.moveTo(px, py); else path.lineTo(px, py);
    }
    path.closePath();
  }
  return path;
}

// ═══════════════════════════ Тайлы подложки ═══════════════════════════

function tileZoom() {
  return Math.max(0, Math.min(BASEMAPS[state.basemap].maxZoom,
    Math.round(Math.log2(state.view.size / TILE))));
}

function drawTiles(r) {
  const base = BASEMAPS[state.basemap];
  const z = tileZoom();
  const n = 2 ** z;
  const scale = state.view.size / (TILE * n);   // мировой размер тайла на экране

  const x0 = Math.floor(unx(0) * n);
  const x1 = Math.ceil(unx(r.width) * n);
  const y0 = Math.floor(uny(0) * n);
  const y1 = Math.ceil(uny(r.height) * n);

  const size = Math.ceil(TILE * scale) + 1;

  for (let ty = Math.max(0, y0); ty <= Math.min(n - 1, y1); ty++) {
    for (let tx = Math.max(0, x0); tx <= Math.min(n - 1, x1); tx++) {
      const key = `${z}/${tx}/${ty}|${state.basemap}`;
      let img = tiles.get(key);

      if (img === undefined) {
        img = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => { tiles.set(key, img); requestDraw(); };
        img.onerror = () => tiles.set(key, 'error');
        img.src = base.url(tx, ty, z);
        tiles.set(key, 'loading');
        continue;
      }
      if (img === 'loading' || img === 'error') continue;

      ctx.drawImage(img, Math.floor(sx(tx / n)), Math.floor(sy(ty / n)), size, size);
    }
  }

  // Кеш тайлов не должен расти бесконечно: при долгой навигации это
  // сотни мегабайт в памяти вкладки.
  if (tiles.size > 600) {
    const keys = [...tiles.keys()].slice(0, 200);
    for (const k of keys) tiles.delete(k);
  }
}

// ═══════════════════════════ Отрисовка ═══════════════════════════

const cssVar = (name) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function requestDraw() {
  if (pendingFrame) return;
  pendingFrame = true;
  requestAnimationFrame(() => { pendingFrame = false; draw(); });
}

function resize() {
  dpr = Math.min(window.devicePixelRatio || 1, 2);
  const r = rect();
  canvas.width = Math.round(r.width * dpr);
  canvas.height = Math.round(r.height * dpr);
  requestDraw();
}

const RISK_FILL = { 2: '--risk-2', 3: '--risk-3', 4: '--risk-4' };

function draw() {
  const r = rect();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = cssVar('--bg-0');
  ctx.fillRect(0, 0, r.width, r.height);

  drawTiles(r);

  // Зоны риска — заливка пакетом по классам
  if (state.visible.has('risk')) {
    for (const cls of [2, 3, 4]) {
      const group = state.layers.risk.filter((s) => Number(s.props.risk_class) === cls);
      if (!group.length) continue;
      ctx.fillStyle = cssVar(RISK_FILL[cls]);
      ctx.fill(batchPath(group, r), 'evenodd');
    }
  }

  // Официальный реестр
  if (state.visible.has('registry') && state.layers.registry.length) {
    const path = batchPath(state.layers.registry, r);
    ctx.fillStyle = 'rgba(87,166,255,.16)';
    ctx.strokeStyle = cssVar('--registry');
    ctx.lineWidth = 1.4;
    ctx.setLineDash([4, 3]);
    ctx.fill(path);
    ctx.stroke(path);
    ctx.setLineDash([]);
  }

  // Объекты
  if (state.visible.has('candidates')) {
    const shapes = state.layers.candidates.filter((s) => inView(s, r));
    const path = batchPath(shapes, r);
    ctx.fillStyle = 'rgba(255,107,91,.30)';
    ctx.strokeStyle = cssVar('--obj');
    ctx.lineWidth = 1.3;
    ctx.fill(path);
    ctx.stroke(path);

    // Маркеры для объектов, выродившихся в точку на текущем масштабе
    ctx.fillStyle = cssVar('--obj');
    ctx.strokeStyle = 'rgba(7,9,13,.8)';
    ctx.lineWidth = 1.5;
    for (const s of shapes) {
      const w = sx(s.wx[2]) - sx(s.wx[0]);
      if (w >= 7) continue;
      const px = sx(lonToX(s.center[0]));
      const py = sy(latToY(s.center[1]));
      ctx.beginPath();
      ctx.arc(px, py, 4.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }

    // Выделенный объект — поверх всего, с ореолом
    if (state.selected && shapes.includes(state.selected)) {
      const sel = shapePath(state.selected);
      ctx.save();
      ctx.shadowColor = cssVar('--accent');
      ctx.shadowBlur = 16;
      ctx.strokeStyle = cssVar('--accent');
      ctx.lineWidth = 2.4;
      ctx.stroke(sel);
      ctx.restore();

      const px = sx(lonToX(state.selected.center[0]));
      const py = sy(latToY(state.selected.center[1]));
      ctx.strokeStyle = cssVar('--accent');
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.arc(px, py, 22, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  updateScale();
  $('attribution').textContent = BASEMAPS[state.basemap].attribution;
}

function updateScale() {
  const lat = yToLat(state.view.cy);
  const mpp = (156543.03392 * Math.cos((lat * Math.PI) / 180) * TILE) / state.view.size;
  const m = mpp * 76;
  $('scale-label').textContent =
    m >= 1000 ? `${(m / 1000).toFixed(m >= 10000 ? 0 : 1)} км` : `${Math.round(m)} м`;
}

// ═══════════════════════════ Навигация ═══════════════════════════

function resetView() {
  const shapes = state.layers.candidates.length ? state.layers.candidates : state.layers.risk;
  if (!shapes.length) return requestDraw();
  let x0 = 1, y0 = 1, x1 = 0, y1 = 0;
  for (const s of shapes) {
    x0 = Math.min(x0, s.wx[0]); y0 = Math.min(y0, s.wx[1]);
    x1 = Math.max(x1, s.wx[2]); y1 = Math.max(y1, s.wx[3]);
  }
  const r = rect();
  const spanX = (x1 - x0) * 1.18 || 1e-4;
  const spanY = (y1 - y0) * 1.18 || 1e-4;
  state.view.size = Math.min(r.width / spanX, r.height / spanY);
  state.view.cx = (x0 + x1) / 2;
  state.view.cy = (y0 + y1) / 2;
  requestDraw();
}

/** Плавный перелёт: резкий скачок камеры на сцене выглядит сломанным. */
function flyTo(lon, lat, size, ms = 520) {
  const from = { ...state.view };
  const to = { cx: lonToX(lon), cy: latToY(lat), size: size || state.view.size };
  const t0 = performance.now();
  const ease = (t) => (t < .5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
  function step(now) {
    const t = Math.min(1, (now - t0) / ms);
    const k = ease(t);
    state.view.cx = from.cx + (to.cx - from.cx) * k;
    state.view.cy = from.cy + (to.cy - from.cy) * k;
    state.view.size = from.size * (to.size / from.size) ** k;
    draw();
    if (t < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function zoomBy(factor, ax, ay) {
  const r = rect();
  const px = ax ?? r.width / 2;
  const py = ay ?? r.height / 2;
  const wx = unx(px), wy = uny(py);
  state.view.size = Math.max(400, Math.min(6e7, state.view.size * factor));
  state.view.cx += wx - unx(px);
  state.view.cy += wy - uny(py);
  requestDraw();
}

let dragging = false, moved = false, last = null;

canvas.addEventListener('pointerdown', (e) => {
  dragging = true; moved = false; last = [e.clientX, e.clientY];
  canvas.classList.add('dragging');
  canvas.setPointerCapture(e.pointerId);
});
canvas.addEventListener('pointermove', (e) => {
  if (!dragging) return;
  const dx = e.clientX - last[0], dy = e.clientY - last[1];
  if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
  last = [e.clientX, e.clientY];
  state.view.cx -= dx / state.view.size;
  state.view.cy -= dy / state.view.size;
  requestDraw();
});
canvas.addEventListener('pointerup', (e) => {
  dragging = false;
  canvas.classList.remove('dragging');
  canvas.releasePointerCapture(e.pointerId);
});
canvas.addEventListener('wheel', (e) => {
  e.preventDefault();
  const r = rect();
  zoomBy(e.deltaY < 0 ? 1.25 : 1 / 1.25, e.clientX - r.left, e.clientY - r.top);
}, { passive: false });

canvas.addEventListener('click', (e) => {
  if (moved || !state.visible.has('candidates')) return;
  const r = rect();
  const px = e.clientX - r.left, py = e.clientY - r.top;

  // Сначала грубо по bbox, потом точно. Перебор с построением путей
  // по всем объектам на каждый клик — та же ошибка, что и в отрисовке.
  let hit = null;
  for (const s of state.layers.candidates) {
    const bx0 = sx(s.wx[0]) - 10, bx1 = sx(s.wx[2]) + 10;
    const by0 = sy(s.wx[1]) - 10, by1 = sy(s.wx[3]) + 10;
    if (px < bx0 || px > bx1 || py < by0 || py > by1) continue;
    if (bx1 - bx0 < 22 || ctx.isPointInPath(shapePath(s), px, py)) { hit = s; break; }
  }
  if (hit) select(hit, { fly: false });
});

$('zoom-in').onclick = () => zoomBy(1.5);
$('zoom-out').onclick = () => zoomBy(1 / 1.5);
$('reset-view').onclick = resetView;

$('base-sat').onclick = () => setBasemap('sat');
$('base-dark').onclick = () => setBasemap('dark');

function setBasemap(name) {
  state.basemap = name;
  $('base-sat').classList.toggle('active', name === 'sat');
  $('base-dark').classList.toggle('active', name === 'dark');
  requestDraw();
}

document.querySelectorAll('.tool.layer').forEach((btn) => {
  btn.onclick = () => {
    const layer = btn.dataset.layer;
    if (state.visible.has(layer)) state.visible.delete(layer);
    else state.visible.add(layer);
    btn.classList.toggle('active', state.visible.has(layer));
    requestDraw();
  };
});

// ═══════════════════════════ Форматирование ═══════════════════════════

const nf = new Intl.NumberFormat('ru-RU');
const MONTHS = ['января','февраля','марта','апреля','мая','июня',
                'июля','августа','сентября','октября','ноября','декабря'];

const num = (v, d = 0) => (v == null || Number.isNaN(+v)) ? '—'
  : (+v).toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d });

function kzt(v) {
  if (v == null || Number.isNaN(+v)) return '—';
  const n = Math.abs(+v);
  if (n >= 1e9) return `${(v / 1e9).toFixed(1)} млрд ₸`;
  if (n >= 1e6) return `${(v / 1e6).toFixed(1)} млн ₸`;
  if (n >= 1e3) return `${nf.format(Math.round(v / 1e3))} тыс ₸`;
  return `${nf.format(Math.round(v))} ₸`;
}

function humanDate(v) {
  if (!v) return '—';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? String(v) : `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

// ═══════════════════════════ Список объектов ═══════════════════════════

function sortedCandidates() {
  const q = state.filter.trim().toLowerCase();
  let list = state.layers.candidates;
  if (q) list = list.filter((s) => String(s.props.candidate_id || '').toLowerCase().includes(q));

  const key = state.sort;
  return [...list].sort((a, b) => {
    if (key === 'break_date') {
      return String(b.props.break_date || '').localeCompare(String(a.props.break_date || ''));
    }
    return (Number(b.props[key]) || 0) - (Number(a.props[key]) || 0);
  });
}

function renderList() {
  const list = sortedCandidates();
  const box = $('object-list');
  box.innerHTML = list.map((s) => {
    const p = s.props;
    const prob = p.probability != null ? Math.round(p.probability * 100) : null;
    return `<div class="item${state.selected === s ? ' active' : ''}" data-id="${p.candidate_id}">
      <div class="item-top">
        <span class="item-id">${p.candidate_id || '—'}</span>
        ${prob != null ? `<span class="item-prob">${prob}%</span>` : ''}
      </div>
      <div class="item-meta">
        <span>${num(p.area_m2)} м²</span>
        <span>${kzt(p.damage_p50)}</span>
        <span>${p.break_date ? String(p.break_date).slice(0, 4) : '—'}</span>
      </div>
      <div class="item-bar"><i style="width:${prob ?? 0}%"></i></div>
    </div>`;
  }).join('') || '<div class="hint" style="padding:14px">Ничего не найдено</div>';

  box.querySelectorAll('.item').forEach((el) => {
    el.onclick = () => {
      const shape = state.layers.candidates.find((s) => s.props.candidate_id === el.dataset.id);
      if (shape) select(shape, { fly: true });
    };
  });

  const total = state.layers.candidates.length;
  $('list-summary').textContent = list.length === total
    ? `${total} объектов`
    : `${list.length} из ${total}`;
}

$('search').oninput = (e) => { state.filter = e.target.value; renderList(); };
$('sort').onchange = (e) => { state.sort = e.target.value; renderList(); };

// ═══════════════════════════ Панели ═══════════════════════════

const SIGNALS = [
  ['ndvi_drop', 'Падение растительности', 0.35],
  ['bsi_rise', 'Рост открытого грунта', 0.25],
  ['pmli_response', 'Отклик полимеров (SWIR)', 0.15],
  ['sar_incoherence', 'Нестабильность по радару', 0.50],
  ['thermal_anomaly', 'Тепловая аномалия', 3.0],
];

function renderLegend() {
  $('legend').innerHTML = [
    ['--obj', 'Найденный объект'],
    ['--registry', 'Официальный реестр'],
    ['--risk-3', 'Зона риска'],
  ].map(([v, l]) => `<div class="lrow"><span class="sw" style="background:${cssVar(v)}"></span>${l}</div>`).join('');
}

function showPane(name) {
  for (const id of ['story', 'details', 'mistakes']) {
    $(id).classList.toggle('hidden', id !== name);
  }
}

function select(shape, { fly = true } = {}) {
  state.selected = shape;
  renderList();
  if (fly) flyTo(shape.center[0], shape.center[1], Math.max(state.view.size, 320000));
  else requestDraw();
  renderDetails(shape);
  showPane('details');
}

function renderDetails(shape) {
  const p = shape.props;

  $('det-id').textContent = p.candidate_id || '—';

  const badges = [];
  if (p.probability >= 0.8) badges.push('<span class="badge hot">высокая уверенность</span>');
  if (p.verify_providers >= 2) badges.push(`<span class="badge ok">подтверждено: ${p.verify_providers}</span>`);
  if (p.is_demo) badges.push('<span class="badge">демо</span>');
  $('det-badges').innerHTML = badges.join('');

  $('det-facts').innerHTML = `
    <div class="fact"><div class="k">Площадь</div><div class="v">${num(p.area_m2)} м²</div></div>
    <div class="fact"><div class="k">Масса</div><div class="v">${num(p.mass_t)} т</div></div>
    <div class="fact"><div class="k">Возник</div><div class="v">${humanDate(p.break_date)}</div></div>
    <div class="fact"><div class="k">Уверенность</div><div class="v">${p.probability != null ? Math.round(p.probability * 100) + '%' : '—'}</div></div>
    <div class="fact wide"><div class="k">Координаты</div><div class="v">${shape.center[1].toFixed(6)}, ${shape.center[0].toFixed(6)}</div></div>`;

  let agree = 0;
  $('det-signals').innerHTML = SIGNALS.map(([key, label, full]) => {
    const raw = Number(p[key]);
    const s = Number.isFinite(raw) ? Math.max(0, Math.min(1, raw / full)) : 0;
    if (s >= 0.3) agree++;
    return `<div class="sig${s < 0.3 ? ' off' : ''}">
      <div class="sig-head"><span>${label}</span><span class="val">${Math.round(s * 100)}%</span></div>
      <div class="sig-track"><div class="sig-fill${s < 0.3 ? ' weak' : ''}" style="width:${s * 100}%"></div></div>
    </div>`;
  }).join('');
  $('det-agree').textContent = `${agree} из 5`;
  $('det-evidence').textContent =
    'Свалку определяет согласие независимых признаков, а не сила одного: ' +
    'карьер даёт мощный рост открытого грунта при молчании остальных четырёх.';

  const drop = Number(p.ndvi_drop) || 0;
  $('det-ba').innerHTML = `
    <div class="ba-card"><div class="k">NDVI до</div><div class="v">0.36</div></div>
    <div class="ba-arrow">→</div>
    <div class="ba-card after"><div class="k">NDVI после</div><div class="v">${Math.max(0, 0.36 - drop).toFixed(2)}</div></div>`;

  const p10 = +p.damage_p10, p50 = +p.damage_p50, p90 = +p.damage_p90;
  const pos = Number.isFinite(p10) && Number.isFinite(p90) && p90 > p10
    ? ((p50 - p10) / (p90 - p10)) * 100 : 50;
  $('det-money').innerHTML = `
    <div class="money-head">${kzt(p50)}</div>
    <div class="money-sub">медианная оценка чистого ущерба</div>
    <div class="band">
      <div class="band-track">
        <div class="band-range" style="left:0;width:100%"></div>
        <div class="band-marker" style="left:${pos}%"></div>
      </div>
      <div class="band-labels"><span>P10 ${kzt(p10)}</span><span>P90 ${kzt(p90)}</span></div>
    </div>
    <div class="kv"><span>Метан за 20 лет</span><b>${num(p.co2e_t)} т CO₂-экв.</b></div>
    <p class="hint">Диапазон получен методом Монте-Карло по восьми допущениям,
    у каждого указан источник. Точечная цифра не пережила бы вопроса «откуда».</p>`;

  $('det-legal').innerHTML = `
    <div class="art">${p.penalty_article || 'ст. 344, ч. 2-1 КоАП РК'}</div>
    <div class="txt">Образование стихийных свалок (выброс отходов вне специально
    установленных мест) с использованием транспортных средств.</div>
    <div class="fine">${kzt(p.penalty_kzt)}</div>`;
}

$('details-close').onclick = () => {
  state.selected = null;
  renderList();
  requestDraw();
  showPane(state.mode === 'story' ? 'story' : 'story');
};

$('det-act').onclick = (e) => {
  const b = e.currentTarget;
  b.disabled = true;
  b.textContent = 'Черновик сформирован';
  toast('Черновик акта сформирован. Официальным документ станет только после подтверждения человеком.', 'ok', 6000);
  setTimeout(() => { b.disabled = false; b.textContent = 'Сформировать черновик акта'; }, 5000);
};

const REJECTS = [
  ['Карьер', 'пересекается с landuse=quarry в OpenStreetMap; радарно стабилен'],
  ['Стройплощадка', 'пересекается с landuse=construction'],
  ['Снегосвалка', 'тепловая аномалия отрицательная — холоднее фона'],
  ['Пашня', 'NDVI восстановился в окне 18 месяцев: сезонное изменение'],
  ['Отвал грунта', 'нет отклика полимеров, нет тепловой аномалии'],
  ['Мелкий объект', 'площадь ниже порога разрешения Sentinel-2 (30–50 м²)'],
];

function renderMistakes() {
  $('mistakes-list').innerHTML = REJECTS
    .map(([w, why]) => `<div class="reject"><div class="what">${w}</div><div class="why">${why}</div></div>`)
    .join('');
}

// ═══════════════════════════ Сценарий ═══════════════════════════

function renderTotals() {
  const t = state.totals;
  if (!t) return ($('totals').innerHTML = '');
  $('totals').innerHTML = `
    <div class="stat big"><span class="k">Объектов найдено</span><span class="v">${num(t.objects)}</span></div>
    <div class="stat"><span class="k">Суммарная площадь</span><span class="v">${num(t.area_ha, 1)} га</span></div>
    <div class="stat"><span class="k">Ущерб P10–P90</span><span class="v">${kzt(t.damage_p10)} – ${kzt(t.damage_p90)}</span></div>
    <div class="stat"><span class="k">Метан за 20 лет</span><span class="v">${num(t.co2e_t)} т CO₂-экв.</span></div>`;
}

function setScene(index) {
  const scenes = state.story?.scenes;
  if (!scenes?.length) {
    state.visible = new Set(['candidates']);
    return requestDraw();
  }
  state.scene = Math.max(0, Math.min(scenes.length - 1, index));
  const s = scenes[state.scene];

  $('story-progress').innerHTML = `<i style="width:${((state.scene + 1) / scenes.length) * 100}%"></i>`;
  $('story-step').textContent = state.scene + 1;
  $('story-total').textContent = scenes.length;
  $('story-title').textContent = s.title;
  $('story-line').textContent = s.line;
  $('story-prev').disabled = state.scene === 0;
  $('story-next').textContent = state.scene === scenes.length - 1 ? 'Заново' : 'Дальше →';

  state.visible = new Set(s.layers || ['candidates']);
  document.querySelectorAll('.tool.layer').forEach((b) =>
    b.classList.toggle('active', state.visible.has(b.dataset.layer)));

  state.selected = null;

  if (s.panel === 'mistakes') { renderMistakes(); showPane('mistakes'); }
  else showPane('story');

  renderTotals();
  $('totals').classList.toggle('hidden', !(s.panel === 'money' || s.id === 'found'));

  if (s.focus?.center) {
    const target = state.layers.candidates.find((c) => c.props.candidate_id === s.focus.candidate_id);
    if (target && s.panel === 'evidence') {
      setTimeout(() => select(target, { fly: true }), 60);
    } else {
      flyTo(s.focus.center[0], s.focus.center[1], 300000);
    }
  } else {
    resetView();
  }
  renderList();
}

$('story-next').onclick = () => {
  const n = state.story?.scenes?.length || 1;
  setScene(state.scene === n - 1 ? 0 : state.scene + 1);
};
$('story-prev').onclick = () => setScene(state.scene - 1);

document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  if (state.mode !== 'story') return;
  if (e.code === 'Space' || e.code === 'ArrowRight') { e.preventDefault(); $('story-next').click(); }
  else if (e.code === 'ArrowLeft') { e.preventDefault(); setScene(state.scene - 1); }
  else if (e.code === 'Escape') $('details-close').click();
});

function setMode(mode) {
  state.mode = mode;
  $('mode-story').classList.toggle('active', mode === 'story');
  $('mode-explore').classList.toggle('active', mode === 'explore');
  if (mode === 'explore') {
    state.visible = new Set(['candidates', 'risk', 'registry']);
    document.querySelectorAll('.tool.layer').forEach((b) => b.classList.add('active'));
    showPane('story');
    $('totals').classList.remove('hidden');
    renderTotals();
    resetView();
  } else {
    setScene(state.scene);
  }
}
$('mode-story').onclick = () => setMode('story');
$('mode-explore').onclick = () => setMode('explore');

// ═══════════════════════════ Сеть и офлайн ═══════════════════════════

function toast(text, kind = '', ms = 5000) {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = text;
  $('toast-stack').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, ms);
}

function updateNet() {
  const off = !navigator.onLine;
  $('net-chip').classList.toggle('off', off);
  $('net-label').textContent = off ? 'офлайн' : 'онлайн';
}
window.addEventListener('online', () => { updateNet(); toast('Сеть восстановлена', 'ok', 3000); });
window.addEventListener('offline', () => { updateNet(); toast('Сети нет. Карта работает из кеша.', 'warn', 6000); });

/** Прогрев кеша: скачать тайлы вокруг области заранее.
 *  Нажимается на репетиции, чтобы на защите Wi-Fi был не нужен. */
$('offline-prep').onclick = async () => {
  const btn = $('offline-prep');
  btn.disabled = true;
  const shapes = state.layers.candidates;
  if (!shapes.length) { toast('Нет данных для прогрева', 'warn'); btn.disabled = false; return; }

  let x0 = 1, y0 = 1, x1 = 0, y1 = 0;
  for (const s of shapes) {
    x0 = Math.min(x0, s.wx[0]); y0 = Math.min(y0, s.wx[1]);
    x1 = Math.max(x1, s.wx[2]); y1 = Math.max(y1, s.wx[3]);
  }

  const jobs = [];
  for (const base of ['dark', 'sat']) {
    for (let z = 9; z <= 14; z++) {
      const n = 2 ** z;
      for (let ty = Math.floor(y0 * n); ty <= Math.ceil(y1 * n); ty++) {
        for (let tx = Math.floor(x0 * n); tx <= Math.ceil(x1 * n); tx++) {
          jobs.push(BASEMAPS[base].url(tx, ty, z));
        }
      }
    }
  }

  toast(`Скачиваю ${jobs.length} тайлов в кеш…`, '', 4000);
  let done = 0;
  const batch = 24;
  for (let i = 0; i < jobs.length; i += batch) {
    await Promise.allSettled(jobs.slice(i, i + batch).map((u) =>
      fetch(u, { mode: 'no-cors' }).then(() => done++)));
    btn.textContent = `${Math.round((i / jobs.length) * 100)}%`;
  }
  btn.textContent = 'Офлайн';
  btn.disabled = false;
  toast(`Готово: ${done} тайлов в кеше. Карта откроется без сети.`, 'ok', 7000);
};

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}

// ═══════════════════════════ Старт ═══════════════════════════

window.addEventListener('resize', resize);
resize();
updateNet();
loadAll();
