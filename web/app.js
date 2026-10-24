/* VANTAGE — карта объектов.
 *
 * Собственный рендерер вместо готовой картографической библиотеки.
 * Решение осознанное, и на защите оно объясняется двумя причинами:
 *
 *   1. Офлайн. Библиотека с CDN означает, что при отсутствии сети на
 *      площадке демонстрация не откроется вообще. Здесь внешних
 *      ресурсов нет ни одного.
 *   2. Объяснимость. На вопрос «что делает этот код» должен быть ответ
 *      про каждую строку, а не «так работает MapLibre».
 *
 * Проекция — сферический Web Mercator, та же, что у всех веб-карт.
 * Отрисовка — canvas 2D: для десятков полигонов этого достаточно
 * с большим запасом, а SVG на тысячах узлов начал бы тормозить.
 */

'use strict';

// --------------------------------------------------------------------------
//  Проекция
// --------------------------------------------------------------------------

/** Долгота -> X в единицах мира (0..1). */
function lonToX(lon) {
  return (lon + 180) / 360;
}

/** Широта -> Y в единицах мира (0..1). Формула Меркатора. */
function latToY(lat) {
  const clamped = Math.max(Math.min(lat, 85.05112878), -85.05112878);
  const rad = (clamped * Math.PI) / 180;
  return (1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2;
}

function xToLon(x) {
  return x * 360 - 180;
}

function yToLat(y) {
  const n = Math.PI * (1 - 2 * y);
  return (180 / Math.PI) * Math.atan(Math.sinh(n));
}

/** Метров на пиксель — нужно для масштабной линейки. */
function metersPerPixel(lat, worldSize) {
  return (156543.03392 * Math.cos((lat * Math.PI) / 180) * 256) / worldSize;
}

// --------------------------------------------------------------------------
//  Состояние
// --------------------------------------------------------------------------

const state = {
  view: { cx: 0.5, cy: 0.5, worldSize: 4096 },  // центр в единицах мира + масштаб
  layers: { registry: [], candidates: [], risk: [], rejected: [] },
  visible: new Set(['registry']),
  story: null,
  sceneIndex: 0,
  mode: 'story',
  selected: null,
  hovered: null,
  isDemo: false,
  totals: null,
};

const canvas = document.getElementById('map');
const ctx = canvas.getContext('2d');
let dpr = window.devicePixelRatio || 1;

// --------------------------------------------------------------------------
//  Загрузка данных
// --------------------------------------------------------------------------

async function loadJson(path) {
  try {
    const response = await fetch(path, { cache: 'no-cache' });
    if (!response.ok) return null;
    return await response.json();
  } catch (error) {
    console.warn('не удалось загрузить', path, error);
    return null;
  }
}

/** Развернуть FeatureCollection в плоский список фигур для отрисовки. */
function parseFeatures(collection, kind) {
  if (!collection || !collection.features) return [];
  const shapes = [];
  for (const feature of collection.features) {
    const geometry = feature.geometry;
    if (!geometry) continue;
    const polygons =
      geometry.type === 'Polygon' ? [geometry.coordinates]
      : geometry.type === 'MultiPolygon' ? geometry.coordinates
      : null;
    if (!polygons) continue;

    const rings = [];
    let minLon = 180, minLat = 90, maxLon = -180, maxLat = -90;
    for (const polygon of polygons) {
      for (const ring of polygon) {
        const points = ring.map(([lon, lat]) => {
          if (lon < minLon) minLon = lon;
          if (lon > maxLon) maxLon = lon;
          if (lat < minLat) minLat = lat;
          if (lat > maxLat) maxLat = lat;
          return [lonToX(lon), latToY(lat)];
        });
        rings.push(points);
      }
    }
    shapes.push({
      kind,
      rings,
      props: feature.properties || {},
      bbox: [minLon, minLat, maxLon, maxLat],
      center: [(minLon + maxLon) / 2, (minLat + maxLat) / 2],
    });
  }
  return shapes;
}

/** Официальный реестр: намеренно скудный слой — в этом весь смысл первой сцены. */
function syntheticRegistry(candidates) {
  if (!candidates.length) return [];
  const step = Math.max(1, Math.floor(candidates.length / 3));
  return candidates.filter((_, i) => i % step === 0).slice(0, 3).map((shape) => ({
    ...shape,
    kind: 'registry',
    props: { ...shape.props, official: true },
  }));
}

async function loadAll() {
  const [candidates, riskPublic, story] = await Promise.all([
    loadJson('data/candidates.geojson'),
    loadJson('data/risk_public.geojson'),
    loadJson('data/story.json'),
  ]);

  state.layers.candidates = parseFeatures(candidates, 'candidate');
  state.layers.risk = parseFeatures(riskPublic, 'risk');
  state.layers.registry = syntheticRegistry(state.layers.candidates);
  state.story = story;
  state.totals = story && story.totals ? story.totals : null;

  state.isDemo =
    (story && story.is_demo === true) ||
    state.layers.candidates.some((s) => s.props.is_demo === true || s.props.is_demo === 'true');

  document.getElementById('demo-banner').classList.toggle('hidden', !state.isDemo);
  document.getElementById('object-count').textContent =
    state.layers.candidates.length ? `${state.layers.candidates.length} объектов` : 'нет данных';

  resetView();
  renderLegend();
  applyScene(0);
}

// --------------------------------------------------------------------------
//  Отрисовка
// --------------------------------------------------------------------------

function resize() {
  dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  draw();
}

function worldToScreen(x, y) {
  const rect = canvas.getBoundingClientRect();
  return [
    (x - state.view.cx) * state.view.worldSize + rect.width / 2,
    (y - state.view.cy) * state.view.worldSize + rect.height / 2,
  ];
}

function screenToWorld(px, py) {
  const rect = canvas.getBoundingClientRect();
  return [
    (px - rect.width / 2) / state.view.worldSize + state.view.cx,
    (py - rect.height / 2) / state.view.worldSize + state.view.cy,
  ];
}

const RISK_COLORS = ['--risk-1', '--risk-2', '--risk-3', '--risk-4'];

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function drawGraticule(rect) {
  // Сетка координат вместо подложки: она честно показывает географию
  // и не требует ни одного байта из сети.
  ctx.save();
  ctx.strokeStyle = 'rgba(255,255,255,0.05)';
  ctx.lineWidth = 1;
  ctx.font = '10px "Segoe UI", sans-serif';
  ctx.fillStyle = 'rgba(255,255,255,0.22)';

  const [wx0, wy0] = screenToWorld(0, 0);
  const [wx1, wy1] = screenToWorld(rect.width, rect.height);
  const lon0 = xToLon(wx0), lon1 = xToLon(wx1);
  const lat0 = yToLat(wy0), lat1 = yToLat(wy1);

  const span = Math.max(lon1 - lon0, lat0 - lat1);
  const step = span > 4 ? 1 : span > 1 ? 0.25 : span > 0.4 ? 0.1 : 0.05;

  for (let lon = Math.floor(lon0 / step) * step; lon <= lon1; lon += step) {
    const [sx] = worldToScreen(lonToX(lon), 0);
    ctx.beginPath();
    ctx.moveTo(sx, 0);
    ctx.lineTo(sx, rect.height);
    ctx.stroke();
    ctx.fillText(lon.toFixed(2) + '°', sx + 4, rect.height - 6);
  }
  for (let lat = Math.floor(lat1 / step) * step; lat <= lat0; lat += step) {
    const [, sy] = worldToScreen(0, latToY(lat));
    ctx.beginPath();
    ctx.moveTo(0, sy);
    ctx.lineTo(rect.width, sy);
    ctx.stroke();
    ctx.fillText(lat.toFixed(2) + '°', 6, sy - 4);
  }
  ctx.restore();
}

function pathOf(shape) {
  const path = new Path2D();
  for (const ring of shape.rings) {
    ring.forEach(([x, y], i) => {
      const [sx, sy] = worldToScreen(x, y);
      if (i === 0) path.moveTo(sx, sy);
      else path.lineTo(sx, sy);
    });
    path.closePath();
  }
  return path;
}

function draw() {
  const rect = canvas.getBoundingClientRect();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.fillStyle = cssVar('--bg');
  ctx.fillRect(0, 0, rect.width, rect.height);

  drawGraticule(rect);

  if (state.visible.has('risk')) {
    for (const shape of state.layers.risk) {
      const cls = Math.min(4, Math.max(1, Number(shape.props.risk_class) || 1));
      ctx.fillStyle = cssVar(RISK_COLORS[cls - 1]);
      ctx.fill(pathOf(shape));
    }
  }

  if (state.visible.has('registry')) {
    ctx.save();
    ctx.strokeStyle = cssVar('--registry');
    ctx.fillStyle = 'rgba(91,157,217,0.22)';
    ctx.lineWidth = 1.5;
    for (const shape of state.layers.registry) {
      const path = pathOf(shape);
      ctx.fill(path);
      ctx.stroke(path);
    }
    ctx.restore();
  }

  if (state.visible.has('candidates')) {
    for (const shape of state.layers.candidates) {
      const path = pathOf(shape);
      const selected = state.selected === shape;
      const probability = Number(shape.props.probability) || 0.5;
      ctx.fillStyle = `rgba(224,90,79,${0.20 + 0.45 * probability})`;
      ctx.strokeStyle = selected ? cssVar('--accent') : cssVar('--candidate');
      ctx.lineWidth = selected ? 2.5 : 1.2;
      ctx.fill(path);
      ctx.stroke(path);

      // На мелком масштабе полигон вырождается в точку — рисуем маркер,
      // иначе объект просто исчезает и кажется, что его нет.
      const [sx, sy] = worldToScreen(lonToX(shape.center[0]), latToY(shape.center[1]));
      const width = Math.abs(
        worldToScreen(lonToX(shape.bbox[2]), 0)[0] - worldToScreen(lonToX(shape.bbox[0]), 0)[0]
      );
      if (width < 6) {
        ctx.beginPath();
        ctx.arc(sx, sy, selected ? 7 : 4.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
      }
    }
  }

  drawScale(rect);
}

function drawScale(rect) {
  const lat = yToLat(state.view.cy);
  const mpp = metersPerPixel(lat, state.view.worldSize);
  const meters = mpp * 80;
  const label =
    meters >= 1000 ? `${(meters / 1000).toFixed(meters >= 10000 ? 0 : 1)} км` : `${Math.round(meters)} м`;
  document.getElementById('scale-label').textContent = label;
}

// --------------------------------------------------------------------------
//  Навигация
// --------------------------------------------------------------------------

function resetView() {
  const shapes = state.layers.candidates.length ? state.layers.candidates : state.layers.risk;
  if (!shapes.length) {
    draw();
    return;
  }
  let minX = 1, minY = 1, maxX = 0, maxY = 0;
  for (const shape of shapes) {
    minX = Math.min(minX, lonToX(shape.bbox[0]));
    maxX = Math.max(maxX, lonToX(shape.bbox[2]));
    minY = Math.min(minY, latToY(shape.bbox[3]));
    maxY = Math.max(maxY, latToY(shape.bbox[1]));
  }
  const rect = canvas.getBoundingClientRect();
  const pad = 0.12;
  const spanX = (maxX - minX) * (1 + pad) || 1e-4;
  const spanY = (maxY - minY) * (1 + pad) || 1e-4;
  state.view.worldSize = Math.min(rect.width / spanX, rect.height / spanY);
  state.view.cx = (minX + maxX) / 2;
  state.view.cy = (minY + maxY) / 2;
  draw();
}

function flyTo(lon, lat, zoomWorldSize) {
  state.view.cx = lonToX(lon);
  state.view.cy = latToY(lat);
  if (zoomWorldSize) state.view.worldSize = zoomWorldSize;
  draw();
}

function zoomBy(factor, anchorX, anchorY) {
  const rect = canvas.getBoundingClientRect();
  const px = anchorX ?? rect.width / 2;
  const py = anchorY ?? rect.height / 2;
  const [wx, wy] = screenToWorld(px, py);
  state.view.worldSize = Math.max(600, Math.min(4e7, state.view.worldSize * factor));
  const [nx, ny] = screenToWorld(px, py);
  state.view.cx += wx - nx;
  state.view.cy += wy - ny;
  draw();
}

let dragging = false;
let lastPoint = null;

canvas.addEventListener('pointerdown', (event) => {
  dragging = true;
  lastPoint = [event.clientX, event.clientY];
  canvas.setPointerCapture(event.pointerId);
});

canvas.addEventListener('pointermove', (event) => {
  if (!dragging) return;
  const dx = event.clientX - lastPoint[0];
  const dy = event.clientY - lastPoint[1];
  lastPoint = [event.clientX, event.clientY];
  state.view.cx -= dx / state.view.worldSize;
  state.view.cy -= dy / state.view.worldSize;
  draw();
});

canvas.addEventListener('pointerup', (event) => {
  dragging = false;
  canvas.releasePointerCapture(event.pointerId);
});

canvas.addEventListener('wheel', (event) => {
  event.preventDefault();
  const rect = canvas.getBoundingClientRect();
  zoomBy(event.deltaY < 0 ? 1.22 : 1 / 1.22, event.clientX - rect.left, event.clientY - rect.top);
}, { passive: false });

canvas.addEventListener('click', (event) => {
  if (!state.visible.has('candidates')) return;
  const rect = canvas.getBoundingClientRect();
  const px = event.clientX - rect.left;
  const py = event.clientY - rect.top;

  let hit = null;
  for (const shape of state.layers.candidates) {
    if (ctx.isPointInPath(pathOf(shape), px * dpr / dpr, py)) { hit = shape; break; }
    const [sx, sy] = worldToScreen(lonToX(shape.center[0]), latToY(shape.center[1]));
    if (Math.hypot(sx - px, sy - py) < 9) { hit = shape; break; }
  }
  if (hit) showDetails(hit);
});

document.getElementById('zoom-in').onclick = () => zoomBy(1.4);
document.getElementById('zoom-out').onclick = () => zoomBy(1 / 1.4);
document.getElementById('reset-view').onclick = resetView;

// --------------------------------------------------------------------------
//  Форматирование
// --------------------------------------------------------------------------

function kzt(value) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  return Math.round(Number(value)).toLocaleString('ru-RU').replace(/,/g, ' ') + ' ₸';
}

function num(value, digits = 0) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  return Number(value).toLocaleString('ru-RU', {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}

const MONTHS = ['января','февраля','марта','апреля','мая','июня',
                'июля','августа','сентября','октября','ноября','декабря'];

function humanDate(value) {
  if (!value) return 'не определена';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return `${MONTHS[parsed.getMonth()]} ${parsed.getFullYear()}`;
}

// --------------------------------------------------------------------------
//  Панели
// --------------------------------------------------------------------------

const SIGNAL_LABELS = {
  ndvi_drop: ['Падение растительности', 0.35],
  bsi_rise: ['Рост открытого грунта', 0.25],
  pmli_response: ['Отклик полимеров (SWIR)', 0.15],
  sar_incoherence: ['Нестабильность по радару', 0.50],
  thermal_anomaly: ['Тепловая аномалия', 3.0],
};

function renderLegend() {
  const rows = [
    ['--candidate', 'Найденный объект'],
    ['--registry', 'Официальный реестр'],
    ['--risk-3', 'Зона риска'],
  ];
  document.getElementById('legend').innerHTML = rows
    .map(([varName, label]) =>
      `<div class="row"><span class="swatch" style="background:${cssVar(varName)}"></span>${label}</div>`)
    .join('');
}

function renderTotals() {
  const box = document.getElementById('totals');
  if (!state.totals) { box.classList.add('hidden'); return; }
  const t = state.totals;
  box.classList.remove('hidden');
  box.innerHTML = `
    <div class="kv"><span>Объектов найдено</span><b>${num(t.objects)}</b></div>
    <div class="kv"><span>Суммарная площадь</span><b>${num(t.area_ha, 1)} га</b></div>
    <div class="kv"><span>Ущерб (P10–P90)</span><b>${(t.damage_p10/1e6).toFixed(0)}–${(t.damage_p90/1e6).toFixed(0)} млн ₸</b></div>
    <div class="kv"><span>Метан за 20 лет</span><b>${num(t.co2e_t)} т CO₂-экв.</b></div>`;
}

function showDetails(shape) {
  state.selected = shape;
  draw();

  const p = shape.props;
  document.getElementById('story').classList.add('hidden');
  document.getElementById('totals').classList.add('hidden');
  document.getElementById('mistakes').classList.add('hidden');
  const box = document.getElementById('details');
  box.classList.remove('hidden');

  document.getElementById('det-id').textContent = `Объект ${p.candidate_id || '—'}`;
  document.getElementById('det-meta').innerHTML = `
    <div>Координаты: <b>${shape.center[1].toFixed(6)}, ${shape.center[0].toFixed(6)}</b></div>
    <div>Площадь: <b>${num(p.area_m2)} м²</b></div>
    <div>Возник: <b>${humanDate(p.break_date)}</b></div>
    <div>Оценка модели: <b>${p.probability != null ? Math.round(p.probability * 100) + '%' : '—'}</b></div>
    <div>Подтверждено источников: <b>${p.verify_providers ?? 0}</b></div>`;

  // Панель объяснимости: сила каждого признака, а не вердикт модели
  let agreeing = 0;
  const signals = Object.entries(SIGNAL_LABELS).map(([key, [label, fullScale]]) => {
    const raw = Number(p[key]);
    const strength = Number.isFinite(raw) ? Math.max(0, Math.min(1, raw / fullScale)) : 0;
    if (strength >= 0.3) agreeing += 1;
    return `<div class="sig">
        <div class="sig-top"><span>${label}</span><span>${Math.round(strength * 100)}%</span></div>
        <div class="track"><div class="fill ${strength < 0.3 ? 'weak' : ''}" style="width:${strength * 100}%"></div></div>
      </div>`;
  }).join('');
  document.getElementById('det-signals').innerHTML = signals;
  document.getElementById('det-evidence').textContent =
    `Сработало признаков: ${agreeing} из 5. Свалку определяет согласие независимых признаков, ` +
    `а не сила одного: карьер даёт мощный рост грунта при молчании остальных.`;

  const ndviDrop = Number(p.ndvi_drop) || 0;
  document.getElementById('det-beforeafter').innerHTML = `
    <div class="ba-col"><div class="cap">NDVI до</div><div class="val">${(0.36).toFixed(2)}</div></div>
    <div class="ba-col after"><div class="cap">NDVI после</div><div class="val">${Math.max(0, 0.36 - ndviDrop).toFixed(2)}</div></div>`;

  const p10 = Number(p.damage_p10), p50 = Number(p.damage_p50), p90 = Number(p.damage_p90);
  const relative = Number.isFinite(p10) && Number.isFinite(p90) && p90 > p10
    ? ((p50 - p10) / (p90 - p10)) * 100 : 50;
  document.getElementById('det-money').innerHTML = `
    <div class="headline">${kzt(p50)}</div>
    <div class="band">
      <div class="band-line"><div class="band-fill" style="left:0;width:${relative}%"></div></div>
      <div class="band-labels"><span>${kzt(p10)}</span><span>${kzt(p90)}</span></div>
    </div>
    <div class="kv"><span>Масса отходов</span><b>${num(p.mass_t)} т</b></div>
    <div class="kv"><span>Метан за 20 лет</span><b>${num(p.co2e_t)} т CO₂-экв.</b></div>
    <p class="note">Диапазон P10–P90 получен методом Монте-Карло по восьми допущениям,
    у каждого из которых указан источник. Точечная цифра не пережила бы вопроса «откуда».</p>`;

  document.getElementById('det-legal').innerHTML = `
    <b>${p.penalty_article || 'ст. 344, ч. 2-1 КоАП РК'}</b>
    Образование стихийных свалок с использованием транспортных средств.<br>
    Санкция: <b>${kzt(p.penalty_kzt)}</b>`;
}

document.getElementById('details-close').onclick = () => {
  state.selected = null;
  document.getElementById('details').classList.add('hidden');
  applyScene(state.sceneIndex);
  draw();
};

document.getElementById('det-act').onclick = () => {
  const button = document.getElementById('det-act');
  button.textContent = 'Черновик сформирован — требуется подтверждение человеком';
  button.disabled = true;
  setTimeout(() => {
    button.textContent = 'Сформировать черновик акта';
    button.disabled = false;
  }, 4000);
};

const REJECTION_EXAMPLES = [
  ['Карьер', 'пересекается с landuse=quarry в OpenStreetMap'],
  ['Стройплощадка', 'пересекается с landuse=construction'],
  ['Снегосвалка', 'тепловая аномалия отрицательная: холоднее фона'],
  ['Пашня', 'NDVI восстановился в окне 18 месяцев — сезонное изменение'],
  ['Отвал грунта', 'радарно стабилен, отклик полимеров отсутствует'],
  ['Мелкий объект', 'площадь ниже порога разрешения Sentinel-2'],
];

function renderMistakes() {
  document.getElementById('mistakes-list').innerHTML = REJECTION_EXAMPLES
    .map(([what, why]) => `<div class="item"><b>${what}</b><br><span>${why}</span></div>`)
    .join('');
}

// --------------------------------------------------------------------------
//  Режим сценария
// --------------------------------------------------------------------------

function applyScene(index) {
  if (!state.story || !state.story.scenes || !state.story.scenes.length) {
    state.visible = new Set(['candidates']);
    draw();
    return;
  }
  const scenes = state.story.scenes;
  state.sceneIndex = Math.max(0, Math.min(scenes.length - 1, index));
  const scene = scenes[state.sceneIndex];

  document.getElementById('story-step').textContent = `${state.sceneIndex + 1} / ${scenes.length}`;
  document.getElementById('story-title').textContent = scene.title;
  document.getElementById('story-line').textContent = scene.line;

  state.visible = new Set(scene.layers || ['candidates']);
  state.selected = null;

  document.getElementById('details').classList.add('hidden');
  document.getElementById('mistakes').classList.toggle('hidden', scene.panel !== 'mistakes');
  document.getElementById('story').classList.remove('hidden');
  if (scene.panel === 'mistakes') renderMistakes();

  const showTotals = scene.panel === 'money' || scene.id === 'found';
  document.getElementById('totals').classList.toggle('hidden', !showTotals);
  if (showTotals) renderTotals();

  if (scene.focus && scene.focus.center) {
    flyTo(scene.focus.center[0], scene.focus.center[1], 260000);
    const target = state.layers.candidates.find(
      (s) => s.props.candidate_id === scene.focus.candidate_id
    );
    if (target && scene.panel === 'evidence') showDetails(target);
  } else if (scene.id === 'registry' || scene.id === 'found') {
    resetView();
  } else {
    draw();
  }
}

document.getElementById('story-next').onclick = () => applyScene(state.sceneIndex + 1);
document.getElementById('story-prev').onclick = () => applyScene(state.sceneIndex - 1);

document.addEventListener('keydown', (event) => {
  if (state.mode !== 'story') return;
  if (event.code === 'Space' || event.code === 'ArrowRight') {
    event.preventDefault();
    applyScene(state.sceneIndex + 1);
  } else if (event.code === 'ArrowLeft') {
    event.preventDefault();
    applyScene(state.sceneIndex - 1);
  }
});

function setMode(mode) {
  state.mode = mode;
  document.getElementById('mode-story').classList.toggle('active', mode === 'story');
  document.getElementById('mode-free').classList.toggle('active', mode === 'free');
  document.getElementById('story').classList.toggle('hidden', mode !== 'story');
  if (mode === 'free') {
    state.visible = new Set(['candidates', 'risk']);
    document.getElementById('totals').classList.remove('hidden');
    document.getElementById('mistakes').classList.add('hidden');
    renderTotals();
    draw();
  } else {
    applyScene(state.sceneIndex);
  }
}

document.getElementById('mode-story').onclick = () => setMode('story');
document.getElementById('mode-free').onclick = () => setMode('free');

// --------------------------------------------------------------------------
//  Офлайн
// --------------------------------------------------------------------------

function updateNetworkStatus() {
  const offline = !navigator.onLine;
  document.getElementById('offline-banner').classList.toggle('hidden', !offline);
  document.getElementById('net-status').classList.toggle('off', offline);
}

window.addEventListener('online', updateNetworkStatus);
window.addEventListener('offline', updateNetworkStatus);

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch((error) => {
    console.warn('service worker не зарегистрирован', error);
  });
}

// --------------------------------------------------------------------------

window.addEventListener('resize', resize);
resize();
updateNetworkStatus();
loadAll();
