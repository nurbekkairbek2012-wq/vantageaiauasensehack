/* VANTAGE — приложение карты.
 *
 * Карта на Leaflet, библиотека лежит локально в web/vendor: со скриптом
 * с CDN страница без сети не открылась бы вообще.
 */

'use strict';

// ═════════════════════════ Константы ═════════════════════════

const BASEMAPS = {
  dark: {
    url: 'https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
    attribution: '© OpenStreetMap · © CARTO',
    label: 'Схема',
  },
  sat: {
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Esri · Maxar · Earthstar Geographics',
    label: 'Спутник',
  },
};

/* Esri World Imagery Wayback — архив исторических снимков.
 * Номера релизов получены из официального конфига waybackconfig.json,
 * по одному на год, ближе к середине лета: съёмка без снега. */
const WAYBACK = {
  2017: '3319', 2018: '14829', 2019: '16681', 2020: '9549', 2021: '8432',
  2022: '13851', 2023: '64776', 2024: '32553', 2025: '49999', 2026: '26334',
};
const waybackUrl = (release, z, x, y) =>
  `https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile/${release}/${z}/${y}/${x}`;

/* Тепловая плотность объектов.
 *
 * Радиус влияния задан в МЕТРАХ, а не в пикселях. Пиксельный радиус
 * означал бы, что при отдалении карты пятна расплываются на десятки
 * километров, и «плотность» показывала бы масштаб просмотра, а не
 * положение дел на земле.
 *
 * 1200 м выбраны по смыслу задачи: свалки образуются группами вдоль
 * подъездных дорог, и пятно такого размера показывает именно очаг, а не
 * отдельный объект — для отдельных объектов есть точки и полигоны. */
const HEAT_RADIUS_M = 1200;
const HEAT_MIN_PX = 10;
const HEAT_MAX_PX = 260;

/* Выше этого зума слой не рисуется. Причина не в скорости: радиус пятна
   упирается в потолок в пикселях, и поверхность начинает показывать
   охват меньше настоящего — то есть врать. На таком приближении смотрят
   на отдельный объект, а для него есть полигон и карточка. */
const HEAT_MAX_ZOOM = 15;

/* Палитра плотности: от прозрачного через охру к красному. Совпадает с
   палитрой слоя риска, чтобы два слоя читались как одна шкала. */
const HEAT_PALETTE = [
  [0.00, [0, 0, 0, 0]],
  [0.25, [176, 122, 18, 90]],
  [0.55, [201, 106, 28, 160]],
  [0.80, [184, 74, 31, 205]],
  [1.00, [224, 96, 58, 235]],
];

const MIN_ZOOM = 8;
const MAX_ZOOM = 18;
const CLUSTER_ZOOM = 13;      // ниже этого масштаба точки объединяются
const CLUSTER_PX = 46;        // радиус объединения в пикселях экрана

/* Прогрев офлайн-кеша. Бюджет согласован с ёмкостью кеша тайлов в sw.js:
   просить больше, чем кеш может удержать, бессмысленно — начало вытеснится
   раньше, чем закончится закачка. */
const MAX_PREWARM_ZOOM = 15;
const PREWARM_BUDGET = 3000;

/* Пять физических признаков. Шкала нормировки — запасная: настоящая
   приезжает в story.json полем signal_scales, потому что считает эти
   величины Python, и держать их вторую копию здесь означает рано или
   поздно разойтись с ней. Так уже было с радаром: здесь стояло 0.50,
   в sar.py — 3.0, и панель показывала признак вшестеро сильнее. */
const SIGNALS = [
  ['ndvi_drop', 'Падение растительности', 0.35],
  ['bsi_rise', 'Рост открытого грунта', 0.25],
  ['pmli_response', 'Отклик полимеров (SWIR)', 0.15],
  ['sar_incoherence', 'Нестабильность по радару', 3.0],
  ['thermal_anomaly', 'Тепловая аномалия', 3.0],
];

const signalScale = (key, fallback) => {
  const value = Number(state.story?.signal_scales?.[key]);
  return Number.isFinite(value) && value > 0 ? value : fallback;
};

const REJECTS = [
  ['Карьер', 'пересекается с landuse=quarry в OSM; радарно стабилен'],
  ['Стройплощадка', 'пересекается с landuse=construction'],
  ['Снегосвалка', 'тепловая аномалия отрицательная — холоднее фона'],
  ['Пашня', 'NDVI восстановился в окне 18 месяцев: сезонное изменение'],
  ['Отвал грунта', 'нет отклика полимеров и тепловой аномалии'],
  ['Мелкий объект', 'площадь ниже порога разрешения Sentinel-2'],
];

const FORECAST_FEATURES = [
  'расстояние до проезжей дороги — без подъезда свалка не образуется',
  'удалённость от жилья — ближе заметят, дальше невыгодно везти',
  'расстояние до легального полигона',
  'плотность существующих свалок в радиусе 3 и 10 км',
  'расстояние до ближайшей известной свалки',
  'укрытость от глаз: подъезд близко, жильё далеко',
];

const RISK_CLASSES = [
  ['#b84a1f', 'Высокий — сюда ставить знак и фотоловушку в первую очередь'],
  ['#c96a1c', 'Повышенный'],
  ['#b07a12', 'Умеренный'],
];

const TOUR = [
  { title: 'Что вы видите',
    text: 'Красные точки — объекты, найденные по спутниковым снимкам. Каждый датирован: система знает, в каком месяце он появился.',
    before: () => { setTab('map'); fitAll(); } },
  { title: 'Список и фильтры',
    text: 'Все объекты с уверенностью, площадью и оценкой ущерба. Поиск, сортировка по четырём полям и фильтры по уверенности, ущербу, площади и подтверждению.',
    spot: '#panel-left' },
  { title: 'Снимок: было и стало',
    text: 'В карточке объекта — два спутниковых снимка разных лет с перетаскиваемой шторкой. Это архив Esri Wayback: реальная съёмка, а не рисунок.',
    before: () => { const f = state.list[0]; if (f) selectObject(f, true); },
    spot: '#panel-right' },
  { title: 'Таймлайн и плотность',
    text: 'Ползунок показывает, как объекты появлялись год за годом, а тепловой слой — как разрастались очаги. Нажмите «играть»: в конце поверх накопленного прошлого ляжет прогноз на год вперёд.',
    before: () => { closeObject(); showTimeline(true); },
    spot: '#timeline' },
  { title: 'Прогноз',
    text: 'Вкладка «Прогноз» — сильнейшая часть системы. Она отвечает не на вопрос «где свалки есть», а на вопрос «где они появятся».',
    before: () => { showTimeline(false); setTab('forecast'); } },
  { title: 'Работает без интернета',
    text: 'Кнопка «Офлайн» скачивает тайлы в кеш браузера. Нажмите её на репетиции — и на защите Wi-Fi будет не нужен.',
    spot: '#btn-offline', before: () => setTab('map') },
];

// ═════════════════════════ Состояние ═════════════════════════

const state = {
  features: [], list: [], risk: null, registry: null, story: null,
  totals: null, metrics: null,
  selected: null,
  sort: 'probability', query: '',
  filters: { prob: 0, damage: 0, area: 0, verifiedOnly: false },
  bounds: { damage: [0, 1], area: [0, 1] },
  tab: 'map', scene: 0, tourStep: 0,
  isDemo: false,
  timelineYear: null, playing: false,
  baYears: [2019, 2026],
};

let map, layerCandidates, layerRisk, layerRegistry, layerClusters, layerHeat;
const markers = new Map();
const el = (id) => document.getElementById(id);

// ═════════════════════════ Форматирование ═════════════════════════

const MONTHS = ['января','февраля','марта','апреля','мая','июня',
                'июля','августа','сентября','октября','ноября','декабря'];

const num = (v, d = 0) => (v == null || Number.isNaN(+v)) ? '—'
  : (+v).toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d });

function kzt(v) {
  if (v == null || Number.isNaN(+v)) return '—';
  const n = Math.abs(+v);
  if (n >= 1e9) return `${(v / 1e9).toFixed(1)} млрд ₸`;
  if (n >= 1e6) return `${(v / 1e6).toFixed(1)} млн ₸`;
  if (n >= 1e3) return `${Math.round(v / 1e3).toLocaleString('ru-RU')} тыс ₸`;
  return `${Math.round(v).toLocaleString('ru-RU')} ₸`;
}

const humanDate = (v) => {
  if (!v) return '—';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? String(v) : `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
};

const yearOf = (v) => { const d = new Date(v); return Number.isNaN(d.getTime()) ? null : d.getFullYear(); };

function toast(text, kind = '', ms = 5000) {
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.textContent = text;
  el('toasts').appendChild(node);
  setTimeout(() => { node.style.opacity = '0'; setTimeout(() => node.remove(), 320); }, ms);
}

/* Уверенность объекта. Источников два, и путать их нельзя.
 *
 * probability     — вероятность обученной сети;
 * evidence_score  — согласие пяти физических признаков.
 *
 * Второе есть всегда, первое — только когда сеть удалось обучить. А
 * обучить её без ручной разметки не выходит: положительные примеры
 * берутся из полигонов ТБО в OSM, но внутри существующего полигона
 * детектор изменений ничего не находит — там и в 2018 году была голая
 * поверхность. Поэтому карта показывает то, что есть, и подписывает,
 * что именно она показывает. Подставить ноль вместо пустой вероятности
 * нельзя: ноль читается как «модель уверена, что это не свалка». */
function confidence(p) {
  const model = Number(p.probability);
  if (Number.isFinite(model)) return { value: model, model: true };
  const evidence = Number(p.evidence_score);
  if (Number.isFinite(evidence)) return { value: evidence, model: false };
  return { value: null, model: false };
}

const CONFIDENCE_LABEL = {
  true: 'Уверенность модели',
  false: 'Согласие признаков',
};

function levelClass(pct) {
  if (pct >= 60) return 'hi';
  if (pct >= 30) return 'mid';
  return 'low';
}

function trackHtml(pct) {
  const p = Math.max(0, Math.min(100, Math.round(pct)));
  return `<div class="track"><i class="${levelClass(p)}" style="width:${p}%"></i></div>`;
}

// ═════════════════════════ Карта ═════════════════════════

/** Растровый слой накопительной плотности.
 *
 * Реализован через L.GridLayer, а не отдельным canvas поверх карты:
 * GridLayer сам занимается панорамированием, зумом и выгрузкой невидимых
 * тайлов. Слой поверх карты пришлось бы синхронизировать вручную, и он
 * отставал бы на каждом перетаскивании.
 *
 * Рисование в два прохода. Сначала копятся полупрозрачные пятна в режиме
 * 'lighter' — там, где объектов больше, яркость выше. Потом накопленная
 * яркость переводится в цвет по палитре. Одним проходом получилась бы
 * не плотность, а просто набор пятен одного цвета.
 */
const HeatLayer = L.GridLayer.extend({
  createTile(coords) {
    const size = this.getTileSize();
    const tile = L.DomUtil.create('canvas', 'leaflet-tile');
    tile.width = size.x;
    tile.height = size.y;
    const ctx = tile.getContext('2d');

    const points = heatPoints();
    if (!points.length) return tile;

    const origin = coords.scaleBy(size);
    const radius = heatRadiusPx(coords.z);
    // Пятна за краем тайла всё равно заходят внутрь: берём запас в радиус.
    const margin = radius;

    ctx.globalCompositeOperation = 'lighter';
    for (const point of points) {
      const projected = map.project(point.latlng, coords.z);
      const x = projected.x - origin.x;
      const y = projected.y - origin.y;
      if (x < -margin || y < -margin || x > size.x + margin || y > size.y + margin) continue;

      const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius);
      const peak = 0.10 + 0.28 * point.weight;
      gradient.addColorStop(0, `rgba(255,255,255,${peak})`);
      gradient.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = gradient;
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fill();
    }

    colourise(ctx, size.x, size.y);
    return tile;
  },
});

/** Радиус пятна в пикселях для данного зума.
 *  Метры переводятся в пиксели через масштаб Web Mercator на широте
 *  Астаны; иначе пятно жило бы своей жизнью при каждом зуме. */
function heatRadiusPx(zoom) {
  const metresPerPixel = (156543.03392 * Math.cos((51.15 * Math.PI) / 180)) / 2 ** zoom;
  return Math.max(HEAT_MIN_PX, Math.min(HEAT_MAX_PX, HEAT_RADIUS_M / metresPerPixel));
}

/** Объекты, попадающие в текущий срез таймлайна.
 *  Вес — корень из площади: свалка вдесятеро большей площади заметнее,
 *  но не в десять раз, иначе один объект съедает всю шкалу. */
let heatCache = { key: null, points: [] };

function heatPoints() {
  // Кеш по году среза: createTile вызывается для каждого тайла экрана, а
  // набор точек между тайлами один и тот же. Без кеша проекция и разбор
  // геометрии повторялись бы двадцать раз на каждое движение карты.
  const key = `${state.timelineYear ?? 'all'}:${state.features.length}`;
  if (heatCache.key === key) return heatCache.points;

  const limit = state.timelineYear;
  // reduce, а не Math.max(...массив): спред раскладывает массив в аргументы
  // вызова и на тысячах объектов падает с RangeError.
  let maxArea = 1;
  const areas = state.features.map((f) => {
    const value = Math.sqrt(Math.max(0, +f.properties.area_m2 || 0));
    if (value > maxArea) maxArea = value;
    return value;
  });

  const points = [];
  state.features.forEach((f, i) => {
    const year = yearOf(f.properties.break_date);
    if (limit != null && (year == null || year > limit)) return;
    const center = polygonCenter(f);
    points.push({ latlng: L.latLng(center[0], center[1]), weight: areas[i] / maxArea });
  });

  heatCache = { key, points };
  return points;
}

/** Перевести накопленную яркость в цвет по палитре. */
function colourise(ctx, width, height) {
  const image = ctx.getImageData(0, 0, width, height);
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
}

function paletteAt(t) {
  for (let i = 1; i < HEAT_PALETTE.length; i++) {
    const [stopHi, colourHi] = HEAT_PALETTE[i];
    if (t > stopHi && i < HEAT_PALETTE.length - 1) continue;
    const [stopLo, colourLo] = HEAT_PALETTE[i - 1];
    const k = stopHi === stopLo ? 0 : Math.min(1, (t - stopLo) / (stopHi - stopLo));
    return colourLo.map((v, j) => Math.round(v + (colourHi[j] - v) * k));
  }
  return HEAT_PALETTE[HEAT_PALETTE.length - 1][1];
}

const redrawHeat = () => { if (layerHeat && map.hasLayer(layerHeat)) layerHeat.redraw(); };

function initMap() {
  map = L.map('map', {
    zoomControl: true, attributionControl: true,
    preferCanvas: true, worldCopyJump: false,
    minZoom: MIN_ZOOM, maxZoom: MAX_ZOOM,
    zoomSnap: 0.5, maxBoundsViscosity: 0.7,
  }).setView([51.17, 71.45], 10);

  const bases = {};
  for (const cfg of Object.values(BASEMAPS)) {
    bases[cfg.label] = L.tileLayer(cfg.url, {
      attribution: cfg.attribution, minZoom: MIN_ZOOM, maxZoom: MAX_ZOOM, keepBuffer: 3,
    });
  }
  bases[BASEMAPS.dark.label].addTo(map);

  layerRisk = L.layerGroup();
  layerRegistry = L.layerGroup();
  layerCandidates = L.layerGroup().addTo(map);
  layerClusters = L.layerGroup().addTo(map);
  layerHeat = new HeatLayer({ opacity: 0.85, minZoom: MIN_ZOOM, maxZoom: HEAT_MAX_ZOOM });

  L.control.layers(bases, {
    'Объекты': layerCandidates,
    'Плотность по годам': layerHeat,
    'Риск': layerRisk,
    'Известные объекты': layerRegistry,
  }, { collapsed: false, position: 'topright' }).addTo(map);

  L.control.scale({ imperial: false, position: 'bottomright' }).addTo(map);

  map.on('click', (e) => { if (!e.originalEvent.__objectClick) closeObject(); });
  map.on('zoomend moveend', updateClustering);
}

const objectStyle = (f) => {
  // 0.5 как запасное значение: объект без оценки не должен ни выделяться,
  // ни пропадать — он должен выглядеть ровно посередине.
  const p = confidence(f.properties || {}).value ?? 0.5;
  return { color: '#b84a1f', weight: 1.6, fillColor: '#e0603a', fillOpacity: 0.18 + 0.4 * p };
};

const riskStyle = (f) => {
  const c = Number(f.properties?.risk_class) || 1;
  const fill = { 2: '#b07a12', 3: '#c96a1c', 4: '#b84a1f' }[c] || '#8a8378';
  return { color: fill, weight: 0, fillColor: fill, fillOpacity: 0.14 + c * 0.05 };
};

function polygonCenter(f) {
  const coords = f.geometry.type === 'Polygon'
    ? f.geometry.coordinates[0] : f.geometry.coordinates[0][0];
  let minLon = 180, minLat = 90, maxLon = -180, maxLat = -90;
  for (const [lon, lat] of coords) {
    if (lon < minLon) minLon = lon;
    if (lon > maxLon) maxLon = lon;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  }
  return [(minLat + maxLat) / 2, (minLon + maxLon) / 2];
}

function addLayers() {
  for (const f of state.features) {
    const id = f.properties.candidate_id;
    const poly = L.geoJSON(f, { style: objectStyle });
    const c = polygonCenter(f);
    const pin = L.marker(c, {
      icon: L.divIcon({ className: '', html: '<div class="obj-pin"></div>', iconSize: [13, 13] }),
      riseOnHover: true,
    });
    const group = L.layerGroup([poly, pin]);
    markers.set(id, { group, poly, pin, center: c, feature: f });

    const open = (e) => { if (e?.originalEvent) e.originalEvent.__objectClick = true; selectObject(f, false); };
    poly.on('click', open);
    pin.on('click', open);
    pin.bindTooltip(id, { direction: 'top', offset: [0, -8] });
  }

  if (state.risk) L.geoJSON(state.risk, { style: riskStyle }).addTo(layerRisk);

  if (state.registry?.features?.length) {
    L.geoJSON(state.registry, {
      style: { color: '#5b93c9', weight: 2, dashArray: '5 4', fillColor: '#5b93c9', fillOpacity: 0.12 },
      onEachFeature: (f, l) => {
        const name = f.properties?.name || 'объект обращения с отходами';
        l.bindTooltip(`${name} · известен публично`, { direction: 'top' });
      },
    }).addTo(layerRegistry);
    layerRegistry.addTo(map);
  }
  updateClustering();
}

/** Кластеризация: на дальнем масштабе десятки точек сливаются в кашу.
 *  Своя реализация вместо плагина — сетка по экранным пикселям. */
function updateClustering() {
  if (!map) return;
  layerCandidates.clearLayers();
  layerClusters.clearLayers();

  const visible = visibleFeatures();
  const zoom = map.getZoom();

  if (zoom >= CLUSTER_ZOOM) {
    for (const f of visible) markers.get(f.properties.candidate_id)?.group.addTo(layerCandidates);
    return;
  }

  const cells = new Map();
  for (const f of visible) {
    const m = markers.get(f.properties.candidate_id);
    if (!m) continue;
    const p = map.latLngToContainerPoint(m.center);
    const key = `${Math.floor(p.x / CLUSTER_PX)}:${Math.floor(p.y / CLUSTER_PX)}`;
    if (!cells.has(key)) cells.set(key, []);
    cells.get(key).push(m);
  }

  for (const group of cells.values()) {
    if (group.length === 1) { group[0].group.addTo(layerCandidates); continue; }
    const lat = group.reduce((s, m) => s + m.center[0], 0) / group.length;
    const lon = group.reduce((s, m) => s + m.center[1], 0) / group.length;
    const size = 26 + Math.min(18, group.length * 2);
    L.marker([lat, lon], {
      icon: L.divIcon({
        className: '',
        html: `<div class="cluster" style="width:${size}px;height:${size}px">${group.length}</div>`,
        iconSize: [size, size],
      }),
    }).on('click', () => {
      map.flyToBounds(L.latLngBounds(group.map((m) => m.center)).pad(0.4), { duration: 0.7 });
    }).addTo(layerClusters);
  }
}

function fitAll() {
  const pts = visibleFeatures().map(polygonCenter);
  if (!pts.length) return;
  map.fitBounds(L.latLngBounds(pts).pad(0.15), { animate: true });
}

function lockBounds() {
  const pts = state.features.map(polygonCenter);
  if (!pts.length) return;
  map.setMaxBounds(L.latLngBounds(pts).pad(1.2));
}

const showRisk = (on) => on ? map.addLayer(layerRisk) : map.removeLayer(layerRisk);

// ═════════════════════════ Загрузка ═════════════════════════

async function loadJson(path) {
  try { const r = await fetch(path, { cache: 'no-cache' }); return r.ok ? await r.json() : null; }
  catch { return null; }
}

async function boot() {
  initMap();

  const [cands, risk, registry, story, metrics] = await Promise.all([
    loadJson('data/candidates.geojson'),
    loadJson('data/risk_public.geojson'),
    loadJson('data/registry.geojson'),
    loadJson('data/story.json'),
    loadJson('data/metrics.json'),
  ]);

  state.features = (cands?.features || []).filter((f) => f.geometry);
  state.risk = risk;
  state.registry = registry;
  state.story = story;
  state.totals = story?.totals || null;
  state.metrics = metrics;
  state.isDemo = story?.is_demo === true ||
    state.features.some((f) => f.properties?.is_demo === true || f.properties?.is_demo === 'true');

  el('loading').classList.add('hidden');

  if (!state.features.length) {
    toast('Данные не найдены. Запустите vantage sample или vantage run.', 'warn', 12000);
    return;
  }

  computeBounds();
  addLayers();
  fitAll();
  lockBounds();
  renderLegend();
  renderList();
  renderStats('welcome-stats');
  renderCompare();
  renderForecast();
  renderMetrics();
  buildTimeline();

  el('demo-flag').classList.toggle('hidden', !state.isDemo);
  if (state.isDemo) {
    toast('Синтетические данные для отладки интерфейса. Это не результаты прогона.', 'warn', 9000);
  }

  applyDeepLink();

  if (!localStorage.getItem('vantage.tour.seen') && !location.search) {
    setTimeout(startTour, 900);
  }
}

/** Границы ползунков-фильтров.
 *  Через reduce, а не Math.min(...массив): спред раскладывает массив в
 *  аргументы вызова, и на настоящем прогоне по области, где объектов
 *  тысячи, вкладка падает с RangeError ещё до появления карты. */
function minMax(values) {
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of values) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return Number.isFinite(lo) ? [lo, hi] : [0, 1];
}

function computeBounds() {
  state.bounds.damage = minMax(state.features.map((f) => +f.properties.damage_p50 || 0));
  state.bounds.area = minMax(state.features.map((f) => +f.properties.area_m2 || 0));
}

// ═════════════════════════ Фильтры и список ═════════════════════════

function visibleFeatures() {
  const f = state.filters;
  const q = state.query.trim().toLowerCase();
  const dmgMin = state.bounds.damage[0] + (state.bounds.damage[1] - state.bounds.damage[0]) * (f.damage / 100);
  const areaMin = state.bounds.area[0] + (state.bounds.area[1] - state.bounds.area[0]) * (f.area / 100);

  return state.features.filter((x) => {
    const p = x.properties;
    if (q && !String(p.candidate_id || '').toLowerCase().includes(q)) return false;
    if ((confidence(p).value ?? 0) * 100 < f.prob) return false;
    if ((Number(p.damage_p50) || 0) < dmgMin) return false;
    if ((Number(p.area_m2) || 0) < areaMin) return false;
    if (f.verifiedOnly && (Number(p.verify_providers) || 0) < 2) return false;
    if (state.timelineYear != null) {
      const y = yearOf(p.break_date);
      if (y == null || y > state.timelineYear) return false;
    }
    return true;
  });
}

function renderList() {
  const list = visibleFeatures();
  const key = state.sort;
  state.list = [...list].sort((a, b) => {
    if (key === 'break_date') {
      return String(b.properties.break_date || '').localeCompare(String(a.properties.break_date || ''));
    }
    if (key === 'probability') {
      return (confidence(b.properties).value ?? 0) - (confidence(a.properties).value ?? 0);
    }
    return (Number(b.properties[key]) || 0) - (Number(a.properties[key]) || 0);
  });

  const selectedId = state.selected?.properties?.candidate_id;

  el('list').innerHTML = state.list.map((f) => {
    const p = f.properties;
    const conf = confidence(p);
    const prob = conf.value != null ? Math.round(conf.value * 100) : null;
    return `<div class="row-item${p.candidate_id === selectedId ? ' on' : ''}" data-id="${p.candidate_id}">
      <div class="ri-top">
        <span class="ri-id">${p.candidate_id}</span>
        ${prob != null ? `<span class="ri-prob ${levelClass(prob)}" title="${CONFIDENCE_LABEL[conf.model]}">${prob}%${conf.model ? '' : '*'}</span>` : ''}
      </div>
      <div class="ri-meta">
        <span>${num(p.area_m2)} м²</span><span>${kzt(p.damage_p50)}</span>
        <span>${p.break_date ? String(p.break_date).slice(0, 4) : '—'}</span>
      </div>
      <div class="ri-item-track">${trackHtml(prob ?? 0)}</div>
    </div>`;
  }).join('') || '<div class="pane muted sm">Ничего не найдено. Смягчите фильтры.</div>';

  el('list').querySelectorAll('.row-item').forEach((node) => {
    node.onclick = () => {
      const f = state.features.find((x) => x.properties.candidate_id === node.dataset.id);
      if (f) { selectObject(f, true); closeSheet(); }
    };
  });

  el('list-foot').textContent = state.list.length === state.features.length
    ? `${state.features.length} объектов`
    : `${state.list.length} из ${state.features.length}`;

  updateClustering();
}

function bindFilters() {
  const sync = () => {
    el('f-prob-v').textContent = `${state.filters.prob}%`;
    const d = state.bounds.damage[0] + (state.bounds.damage[1] - state.bounds.damage[0]) * (state.filters.damage / 100);
    const a = state.bounds.area[0] + (state.bounds.area[1] - state.bounds.area[0]) * (state.filters.area / 100);
    el('f-dmg-v').textContent = kzt(d);
    el('f-area-v').textContent = `${num(a)} м²`;
    renderList();
  };
  el('f-prob').oninput = (e) => { state.filters.prob = +e.target.value; sync(); };
  el('f-dmg').oninput = (e) => { state.filters.damage = +e.target.value; sync(); };
  el('f-area').oninput = (e) => { state.filters.area = +e.target.value; sync(); };
  el('f-verified').onchange = (e) => { state.filters.verifiedOnly = e.target.checked; renderList(); };
  el('f-reset').onclick = () => {
    state.filters = { prob: 0, damage: 0, area: 0, verifiedOnly: false };
    el('f-prob').value = 0; el('f-dmg').value = 0; el('f-area').value = 0;
    el('f-verified').checked = false;
    sync();
  };
  el('btn-filters').onclick = () => {
    const box = el('filters');
    box.classList.toggle('hidden');
    el('btn-filters').classList.toggle('solid', !box.classList.contains('hidden'));
  };
  sync();
}

// ═════════════════════════ Таймлайн ═════════════════════════

function buildTimeline() {
  const years = [...new Set(state.features.map((f) => yearOf(f.properties.break_date)).filter(Boolean))].sort();
  if (years.length < 2) return;
  state.years = years;

  const range = el('tl-range');
  range.min = 0; range.max = years.length; range.value = years.length;
  el('tl-marks').innerHTML = `<span>${years[0]}</span><span>${years[years.length - 1]}</span><span>все</span>`;

  range.oninput = (e) => {
    const i = +e.target.value;
    state.timelineYear = i >= years.length ? null : years[i];
    el('tl-year').textContent = state.timelineYear ?? 'все';
    renderList();
    redrawHeat();
    el('tl-count').textContent = state.list.length;
  };
  el('tl-count').textContent = state.features.length;

  el('tl-play').onclick = playTimeline;
  el('tl-toggle').onclick = () => showTimeline(el('timeline').classList.contains('hidden'));
}

function showTimeline(on) {
  el('timeline').classList.toggle('hidden', !on);
  el('tl-toggle').classList.toggle('on', on);
  el('tl-toggle').classList.toggle('hidden', on);
  // Слой плотности живёт вместе с ползунком: сам по себе, без движения по
  // годам, он показывает только итог и ничего не объясняет.
  if (on && layerHeat && !map.hasLayer(layerHeat)) {
    map.addLayer(layerHeat);
    toast('Слой плотности следует за ползунком: видно, как очаги разрастаются.', '', 6000);
  }
}

function playTimeline() {
  if (state.playing || !state.years) return;
  state.playing = true;
  const range = el('tl-range');
  let i = 0;
  el('tl-play').textContent = '■';

  const step = () => {
    if (!state.playing || i > state.years.length) {
      state.playing = false;
      el('tl-play').textContent = '▶';
      // Последний кадр — прогноз: где объектов ещё нет, но они появятся.
      // Ради этого перехода таймлапс и нужен: накопленное прошлое и
      // предсказанное будущее показываются в одной шкале цветов.
      if (state.risk?.features?.length) {
        showRisk(true);
        toast('Тем же цветом — прогноз на 12 месяцев вперёд.', '', 7000);
      }
      return;
    }
    range.value = i;
    range.dispatchEvent(new Event('input'));
    i++;
    setTimeout(step, 900);
  };
  step();
}

// ═════════════════════════ Карточка объекта ═════════════════════════

function showPane(name) {
  for (const id of ['welcome', 'object', 'story', 'method', 'forecast']) {
    el(`pane-${id}`).classList.toggle('hidden', id !== name);
  }
}

function selectObject(feature, fly) {
  state.selected = feature;
  const id = feature.properties.candidate_id;

  markers.forEach((m, key) => {
    const node = m.pin.getElement()?.querySelector('.obj-pin');
    if (node) node.classList.toggle('on', key === id);
  });

  const m = markers.get(id);
  if (m && fly) map.flyTo(m.center, Math.max(map.getZoom(), 15), { duration: 0.8 });

  renderObject(feature);
  showPane('object');
  renderList();
  history.replaceState(null, '', `?id=${encodeURIComponent(id)}`);
}

function closeObject() {
  state.selected = null;
  markers.forEach((m) => m.pin.getElement()?.querySelector('.obj-pin')?.classList.remove('on'));
  showPane({ story: 'story', method: 'method', forecast: 'forecast' }[state.tab] || 'welcome');
  renderList();
  history.replaceState(null, '', location.pathname);
}

function tileXY(lat, lon, z) {
  const n = 2 ** z;
  const rad = (lat * Math.PI) / 180;
  return [
    Math.floor(((lon + 180) / 360) * n),
    Math.floor(((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * n),
  ];
}

/** Мозаика 3×3 тайлов вокруг точки для конкретного релиза Wayback. */
function tileGrid(lat, lon, z, release) {
  const [cx, cy] = tileXY(lat, lon, z);
  const cells = [];
  for (let dy = -1; dy <= 1; dy++) {
    for (let dx = -1; dx <= 1; dx++) {
      const url = release
        ? waybackUrl(release, z, cx + dx, cy + dy)
        : BASEMAPS.sat.url.replace('{z}', z).replace('{x}', cx + dx).replace('{y}', cy + dy);
      cells.push(`<img src="${url}" alt="" loading="lazy">`);
    }
  }
  return cells.join('');
}

/** Слайдер «было / стало» на архивных снимках Esri Wayback.
 *  Это настоящая съёмка разных лет, а не перекрашенная картинка —
 *  и именно поэтому она убеждает без единого слова. */
function renderBeforeAfter(center) {
  const [lat, lon] = center;
  const z = 17;
  const [y0, y1] = state.baYears;

  el('ba-before').innerHTML = tileGrid(lat, lon, z, WAYBACK[y0]);
  el('ba-after').innerHTML = tileGrid(lat, lon, z, WAYBACK[y1]);
  el('ba-tag-l').textContent = y0;
  el('ba-tag-r').textContent = y1;
  setSplit(50);
}

function setSplit(percent) {
  const p = Math.max(2, Math.min(98, percent));
  el('ba-clip').style.width = `${p}%`;
  el('ba-clip').querySelector('.baimg-layer').style.width = `${(100 / p) * 100}%`;
  el('ba-handle').style.left = `${p}%`;
}

function bindSplitter() {
  const box = el('obj-ba-img');
  let dragging = false;
  const move = (clientX) => {
    const r = box.getBoundingClientRect();
    setSplit(((clientX - r.left) / r.width) * 100);
  };
  box.addEventListener('pointerdown', (e) => { dragging = true; box.setPointerCapture(e.pointerId); move(e.clientX); });
  box.addEventListener('pointermove', (e) => { if (dragging) move(e.clientX); });
  box.addEventListener('pointerup', (e) => { dragging = false; box.releasePointerCapture(e.pointerId); });

  el('ba-swap').onclick = () => {
    const years = Object.keys(WAYBACK).map(Number).sort((a, b) => a - b);
    const i = years.indexOf(state.baYears[0]);
    state.baYears = [years[(i + 1) % (years.length - 1)], years[years.length - 1]];
    if (state.selected) renderBeforeAfter(polygonCenter(state.selected));
  };
}

function renderObject(f) {
  const p = f.properties;
  const c = polygonCenter(f);

  el('obj-id').textContent = p.candidate_id || '—';

  const conf = confidence(p);
  const badges = [];
  if (conf.value >= 0.8) {
    badges.push(`<span class="badge hot">${conf.model ? 'высокая уверенность' : 'признаки согласны'}</span>`);
  }
  if (!conf.model && conf.value != null) {
    badges.push('<span class="badge">оценка по признакам, не моделью</span>');
  }
  if (p.verify_providers >= 2) badges.push(`<span class="badge ok">подтверждено: ${p.verify_providers}</span>`);
  if (p.is_demo) badges.push('<span class="badge">демо</span>');
  el('obj-badges').innerHTML = badges.join('');

  el('obj-facts').innerHTML = `
    <div class="fact"><div class="k">Площадь</div><div class="v">${num(p.area_m2)} м²</div></div>
    <div class="fact"><div class="k">Масса</div><div class="v">${num(p.mass_t)} т</div></div>
    <div class="fact"><div class="k">Возник</div><div class="v">${humanDate(p.break_date)}</div></div>
    <div class="fact"><div class="k">${CONFIDENCE_LABEL[conf.model]}</div><div class="v">${conf.value != null ? Math.round(conf.value * 100) + '%' : '—'}</div></div>
    <div class="fact wide"><div class="k">Координаты</div><div class="v">${c[0].toFixed(6)}, ${c[1].toFixed(6)}</div></div>`;

  const appeared = yearOf(p.break_date);
  const years = Object.keys(WAYBACK).map(Number).sort((a, b) => a - b);
  state.baYears = [
    years.find((y) => y >= (appeared ? appeared - 2 : 2019)) || 2019,
    years[years.length - 1],
  ];
  renderBeforeAfter(c);

  el('obj-links').innerHTML = `
    <a href="https://yandex.ru/maps/?ll=${c[1]},${c[0]}&z=18&l=sat" target="_blank" rel="noopener">Яндекс</a>
    <a href="https://www.google.com/maps/@${c[0]},${c[1]},18z/data=!3m1!1e3" target="_blank" rel="noopener">Google</a>
    <a href="https://www.openstreetmap.org/?mlat=${c[0]}&mlon=${c[1]}#map=18/${c[0]}/${c[1]}" target="_blank" rel="noopener">OSM</a>`;

  let agree = 0;
  el('obj-signals').innerHTML = SIGNALS.map(([key, label, fallback]) => {
    const raw = Number(p[key]);
    const full = signalScale(key, fallback);
    const pct = Math.round(Number.isFinite(raw) ? Math.max(0, Math.min(1, raw / full)) * 100 : 0);
    if (pct >= 30) agree++;
    return `<div class="sig${pct < 30 ? ' off' : ''}">
      <div class="sig-l"><span>${label}</span><span class="v ${levelClass(pct)}">${pct}%</span></div>
      ${trackHtml(pct)}</div>`;
  }).join('');
  // n_agreeing считается тем же кодом, что и evidence_score, — если оно
  // приехало в данных, показываем его, а не пересчитанное по округлённым
  // процентам: расхождение в одну единицу на защите пришлось бы объяснять.
  const agreeing = Number.isFinite(Number(p.n_agreeing)) ? Number(p.n_agreeing) : agree;
  el('obj-agree').textContent = `${agreeing} из 5`;

  // Уровни NDVI берутся из данных, а не подставляются. Раньше здесь стояло
  // фиксированное «до = 0.36»: на защите это первый вопрос, на который
  // нечего ответить, потому что цифра ничем не подтверждена.
  const before = Number(p.ndvi_before);
  const after = Number(p.ndvi_after);
  const drop = Number(p.ndvi_drop);
  el('obj-ba').innerHTML = Number.isFinite(before) && Number.isFinite(after) ? `
    <div class="ba-c"><div class="k">NDVI до</div><div class="v">${before.toFixed(2)}</div></div>
    <div class="ba-a">→</div>
    <div class="ba-c after"><div class="k">NDVI после</div><div class="v">${after.toFixed(2)}</div></div>`
    : `
    <div class="ba-c"><div class="k">Падение NDVI</div><div class="v">${Number.isFinite(drop) ? drop.toFixed(2) : '—'}</div></div>
    <div class="ba-a"></div>
    <div class="ba-c after"><div class="k">Уровни</div><div class="v muted">нет в данных</div></div>`;

  const p10 = +p.damage_p10, p50 = +p.damage_p50, p90 = +p.damage_p90;
  const pos = Number.isFinite(p10) && Number.isFinite(p90) && p90 > p10
    ? ((p50 - p10) / (p90 - p10)) * 100 : 50;
  el('obj-money').innerHTML = `
    <div class="mhead">${kzt(p50)}</div>
    <div class="msub">медианная оценка чистого ущерба</div>
    <div class="mband">
      <div class="mtrack"><div class="mfill"></div><div class="mmark" style="left:${pos}%"></div></div>
      <div class="mlabels"><span>P10 ${kzt(p10)}</span><span>P90 ${kzt(p90)}</span></div>
    </div>
    <div class="kv"><span>Метан за 20 лет</span><b>${num(p.co2e_t)} т CO₂-экв.</b></div>
    <p class="muted sm mt">Диапазон получен методом Монте-Карло по восьми допущениям,
    у каждого указан источник.</p>`;

  el('obj-legal').innerHTML = `
    <div class="a">${p.penalty_article || 'ст. 344, ч. 2-1 КоАП РК'}</div>
    <div class="t">Образование стихийных свалок (выброс отходов вне специально
    установленных мест) с использованием транспортных средств.</div>
    <div class="f">${kzt(p.penalty_kzt)}</div>`;
}

el('obj-close').onclick = closeObject;

el('obj-share').onclick = async () => {
  const url = location.href;
  try { await navigator.clipboard.writeText(url); toast('Ссылка на объект скопирована', 'ok', 3000); }
  catch { toast(url, '', 9000); }
};

/** Печать акта. PDF генерируется браузером через диалог печати —
 *  это ноль зависимостей и работает офлайн. Документ выходит
 *  ЧЕРНОВИКОМ: официальным он становится только после подтверждения
 *  человеком в закрытом контуре. */
el('obj-act').onclick = () => {
  const f = state.selected;
  if (!f) return;
  const p = f.properties;
  const conf = confidence(p);
  const c = polygonCenter(f);
  el('act-print').innerHTML = `
    <div class="draft">ЧЕРНОВИК. Документ сформирован автоматически системой VANTAGE
    на основе вероятностной модели и НЕ является официальным. Требуется проверка
    и подтверждение уполномоченным лицом.</div>
    <h1>АКТ о выявлении несанкционированного размещения отходов</h1>
    <div class="sub">№ ${p.candidate_id} от ${new Date().toLocaleDateString('ru-RU')}</div>

    <h2>1. Сведения об объекте</h2>
    <table>
      <tr><td>Координаты (WGS84)</td><td>${c[0].toFixed(6)}, ${c[1].toFixed(6)}</td></tr>
      <tr><td>Площадь</td><td>${num(p.area_m2)} м²</td></tr>
      <tr><td>Дата возникновения</td><td>${humanDate(p.break_date)}</td></tr>
      <tr><td>Оценка массы отходов</td><td>${num(p.mass_t)} т</td></tr>
    </table>

    <h2>2. Основания выявления</h2>
    <table>
      <tr><td>Метод</td><td>дистанционное зондирование: Sentinel-2, Sentinel-1, Landsat 8/9</td></tr>
      <tr><td>${conf.model ? 'Оценка модели' : 'Согласие физических признаков'}</td><td>${conf.value != null ? Math.round(conf.value * 100) + '%' : '—'}</td></tr>
      <tr><td>Подтверждено источников</td><td>${p.verify_providers ?? 0}</td></tr>
    </table>

    <h2>3. Оценка ущерба</h2>
    <table>
      <tr><td>Диапазон (P10–P90)</td><td>${kzt(p.damage_p10)} – ${kzt(p.damage_p90)}</td></tr>
      <tr><td>Медианная оценка</td><td>${kzt(p.damage_p50)}</td></tr>
      <tr><td>Эмиссия за 20 лет</td><td>${num(p.co2e_t)} т CO₂-экв.</td></tr>
    </table>

    <h2>4. Применимая норма</h2>
    <table>
      <tr><td>Статья</td><td>${p.penalty_article || 'ст. 344, ч. 2-1 КоАП РК'}</td></tr>
      <tr><td>Размер санкции</td><td>${kzt(p.penalty_kzt)}</td></tr>
    </table>

    <div class="sign">
      <div>подпись проверяющего</div>
      <div>должность, ФИО</div>
    </div>
    <div class="foot">
      Результаты получены методом дистанционного зондирования и представляют собой
      оценку вероятности, а не юридическое доказательство. Решение о статусе объекта,
      размере ущерба и применении санкций принимается уполномоченным лицом по итогам
      выездной проверки. VANTAGE · Future Minds Hackathon 2026.
    </div>`;
  toast('Открываю диалог печати. Сохраните как PDF.', 'ok', 5000);
  setTimeout(() => window.print(), 350);
};

// ═════════════════════════ Панели ═════════════════════════

function renderLegend() {
  el('map-legend').innerHTML = [
    ['#e0603a', 'Найденный объект'],
    ['#5b93c9', 'Известен публично'],
    ['#c96a1c', 'Плотность и прогноз'],
  ].map(([c, l]) => `<div class="lg"><span class="sw" style="background:${c}"></span>${l}</div>`).join('');
}

function renderStats(target) {
  const t = state.totals;
  if (!t) return (el(target).innerHTML = '');
  el(target).innerHTML = `
    <div class="st big"><span class="k">Объектов найдено</span><span class="v">${num(t.objects)}</span></div>
    <div class="st"><span class="k">Суммарная площадь</span><span class="v">${num(t.area_ha, 1)} га</span></div>
    <div class="st"><span class="k">Ущерб P10–P90</span><span class="v">${kzt(t.damage_p10)} – ${kzt(t.damage_p90)}</span></div>
    <div class="st"><span class="k">Метан за 20 лет</span><span class="v">${num(t.co2e_t)} т CO₂-экв.</span></div>`;
}

function renderCompare() {
  const known = state.registry?.features?.length ?? state.totals?.registry_known ?? 0;
  const found = state.features.length;
  el('welcome-compare').innerHTML = `
    <div class="compare-row">
      <div class="compare-n">${known}</div>
      <div class="compare-l">объектов обращения с отходами<br>размечено в открытых данных</div>
    </div>
    <div class="compare-row found">
      <div class="compare-n">${found}</div>
      <div class="compare-l">объектов нашла система<br>по спутниковым снимкам</div>
    </div>`;
}

function renderForecast() {
  el('fc-features').innerHTML = FORECAST_FEATURES
    .map((t, i) => `<div class="fcf"><span class="n">${i + 1}</span><span>${t}</span></div>`).join('');
  el('fc-classes').innerHTML = RISK_CLASSES
    .map(([c, l]) => `<div class="rclass"><span class="sw" style="background:${c}"></span>${l}</div>`).join('');
}

/** Метрики модели. Если обучения на настоящих данных не было, честно
 *  сообщаем об этом вместо того, чтобы показать красивые числа. */
function renderMetrics() {
  const m = state.metrics;
  const html = m ? `
    <div class="metrics-grid">
      <div class="metric-c"><div class="k">PR-AUC (прогноз)</div><div class="v">${(m.pr_auc_future ?? 0).toFixed(2)}</div></div>
      <div class="metric-c"><div class="k">Базовая частота</div><div class="v">${(m.base_rate_future ?? 0).toFixed(3)}</div></div>
      <div class="metric-c"><div class="k">Выигрыш над случайным</div><div class="v">×${(m.lift ?? 0).toFixed(1)}</div></div>
      <div class="metric-c"><div class="k">Отсечка</div><div class="v">${m.cutoff ?? '—'}</div></div>
    </div>` : `
    <div class="metric-note">
      Модель ещё не обучена на настоящих данных Астаны: нужна разметка
      выборки и полный прогон. Показывать здесь цифры, полученные на
      синтетике, было бы обманом — поэтому их здесь нет.
    </div>`;
  el('fc-metrics').innerHTML = html;
  el('mt-metrics').innerHTML = html;
}

// ═════════════════════════ Вкладки и сценарий ═════════════════════════

function setTab(name) {
  state.tab = name;
  for (const t of ['map', 'forecast', 'story', 'method']) {
    el(`tab-${t}`).classList.toggle('active', t === name);
  }
  if (name === 'story') setScene(state.scene);
  else if (name === 'method') showPane('method');
  else if (name === 'forecast') { showPane('forecast'); showRisk(true); }
  else { showPane(state.selected ? 'object' : 'welcome'); renderStats('welcome-stats'); }
}

for (const t of ['map', 'forecast', 'story', 'method']) el(`tab-${t}`).onclick = () => setTab(t);

el('fc-show').onclick = () => {
  showRisk(true);
  map.removeLayer(layerCandidates);
  fitAll();
  toast('Слой зон риска включён. Объекты временно скрыты.', '', 4500);
  setTimeout(() => map.addLayer(layerCandidates), 4000);
};

function setScene(index) {
  const scenes = state.story?.scenes;
  if (!scenes?.length) return showPane('welcome');

  state.scene = Math.max(0, Math.min(scenes.length - 1, index));
  const s = scenes[state.scene];

  el('story-bar').style.width = `${((state.scene + 1) / scenes.length) * 100}%`;
  el('story-n').textContent = state.scene + 1;
  el('story-total').textContent = scenes.length;
  el('story-title').textContent = s.title;
  el('story-line').textContent = s.line;
  el('story-prev').disabled = state.scene === 0;
  el('story-next').textContent = state.scene === scenes.length - 1 ? 'Заново' : 'Дальше →';

  const layers = new Set(s.layers || ['candidates']);
  layers.has('candidates') ? map.addLayer(layerCandidates) : map.removeLayer(layerCandidates);
  layers.has('risk') ? map.addLayer(layerRisk) : map.removeLayer(layerRisk);
  layers.has('registry') ? map.addLayer(layerRegistry) : map.removeLayer(layerRegistry);

  const stats = s.panel === 'money' || s.id === 'found';
  el('story-stats').classList.toggle('hidden', !stats);
  if (stats) renderStats('story-stats');

  const rej = s.panel === 'mistakes';
  el('story-rejects').classList.toggle('hidden', !rej);
  if (rej) {
    el('story-rejects').innerHTML = '<div class="block-title" style="margin-top:20px">Отсеяно фильтром</div>' +
      REJECTS.map(([w, why]) => `<div class="rej"><b>${w}</b><span>${why}</span></div>`).join('');
  }

  if (s.focus?.center) map.flyTo([s.focus.center[1], s.focus.center[0]], 15, { duration: 1 });
  else fitAll();
  showPane('story');
}

el('story-next').onclick = () => {
  const n = state.story?.scenes?.length || 1;
  setScene(state.scene === n - 1 ? 0 : state.scene + 1);
};
el('story-prev').onclick = () => setScene(state.scene - 1);

// ═════════════════════════ Тур ═════════════════════════

let spotted = null;

function renderTour() {
  const step = TOUR[state.tourStep];
  el('tour-n').textContent = state.tourStep + 1;
  el('tour-total').textContent = TOUR.length;
  el('tour-title').textContent = step.title;
  el('tour-text').textContent = step.text;
  el('tour-prev').disabled = state.tourStep === 0;
  el('tour-next').textContent = state.tourStep === TOUR.length - 1 ? 'Готово' : 'Дальше';

  if (spotted) { spotted.classList.remove('spot'); spotted = null; }
  step.before?.();
  if (step.spot) { spotted = document.querySelector(step.spot); spotted?.classList.add('spot'); }
}

const startTour = () => { state.tourStep = 0; el('tour').classList.remove('hidden'); renderTour(); };
const endTour = () => {
  el('tour').classList.add('hidden');
  if (spotted) { spotted.classList.remove('spot'); spotted = null; }
  localStorage.setItem('vantage.tour.seen', '1');
};

el('btn-tour').onclick = startTour;
el('btn-tour-2').onclick = startTour;
el('tour-skip').onclick = endTour;
el('tour-prev').onclick = () => { state.tourStep = Math.max(0, state.tourStep - 1); renderTour(); };
el('tour-next').onclick = () => {
  if (state.tourStep >= TOUR.length - 1) return endTour();
  state.tourStep++; renderTour();
};

// ═════════════════════════ Режим презентации ═════════════════════════

function togglePresent() {
  const on = document.body.classList.toggle('present');
  el('btn-present').classList.toggle('solid', on);
  if (on && document.documentElement.requestFullscreen) {
    document.documentElement.requestFullscreen().catch(() => {});
    toast('Режим презентации. Клавиша P — выйти.', '', 4000);
  } else if (!on && document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  }
  setTimeout(() => map.invalidateSize(), 300);
}
el('btn-present').onclick = togglePresent;

// ═════════════════════════ Мобильный список ═════════════════════════

const closeSheet = () => el('panel-left').classList.remove('open');
el('sheet-toggle').onclick = () => el('panel-left').classList.toggle('open');

// ═════════════════════════ Ссылки на объект ═════════════════════════

function applyDeepLink() {
  const id = new URLSearchParams(location.search).get('id');
  if (!id) return;
  const f = state.features.find((x) => x.properties.candidate_id === id);
  if (f) selectObject(f, true);
  else toast(`Объект ${id} не найден в текущем наборе данных`, 'warn', 7000);
}

// ═════════════════════════ Клавиатура ═════════════════════════

document.addEventListener('keydown', (e) => {
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return;

  if (!el('tour').classList.contains('hidden')) {
    if (e.code === 'ArrowRight' || e.code === 'Space') { e.preventDefault(); el('tour-next').click(); }
    if (e.code === 'Escape') el('tour-skip').click();
    return;
  }
  if (e.code === 'KeyP') { togglePresent(); return; }
  if (e.code === 'KeyT') { showTimeline(el('timeline').classList.contains('hidden')); return; }
  if (e.code === 'Escape') { closeObject(); return; }
  if (state.tab !== 'story') return;
  if (e.code === 'Space' || e.code === 'ArrowRight') { e.preventDefault(); el('story-next').click(); }
  if (e.code === 'ArrowLeft') { e.preventDefault(); setScene(state.scene - 1); }
});

el('q').oninput = (e) => { state.query = e.target.value; renderList(); };
el('sort').onchange = (e) => { state.sort = e.target.value; renderList(); };

// ═════════════════════════ Сеть и офлайн ═════════════════════════

function updateNet() {
  const off = !navigator.onLine;
  el('net-flag').classList.toggle('off', off);
  el('net-flag').textContent = off ? 'офлайн' : 'онлайн';
}
window.addEventListener('online', () => { updateNet(); toast('Сеть восстановлена', 'ok', 3000); });
window.addEventListener('offline', () => { updateNet(); toast('Сети нет. Карта работает из кеша.', 'warn', 6000); });

el('btn-offline').onclick = async () => {
  const btn = el('btn-offline');
  if (!state.features.length) return toast('Нет данных для прогрева', 'warn');
  btn.disabled = true;

  const b = L.latLngBounds(state.features.map(polygonCenter)).pad(0.2);
  const jobs = [];
  // Глубина прогрева подбирается под размер области. На кольце вокруг
  // города до z=15 это тысячи тайлов, на области целиком — сотни тысяч:
  // такой прогрев не помещается в кеш браузера, вешает вкладку и выглядит
  // для тайл-сервера как атака. Поэтому набор режется по бюджету.
  outer:
  for (let z = 9; z <= MAX_PREWARM_ZOOM; z++) {
    for (const cfg of Object.values(BASEMAPS)) {
      const [x0, y0] = tileXY(b.getNorth(), b.getWest(), z);
      const [x1, y1] = tileXY(b.getSouth(), b.getEast(), z);
      for (let y = y0; y <= y1; y++) {
        for (let x = x0; x <= x1; x++) {
          if (jobs.length >= PREWARM_BUDGET) break outer;
          jobs.push(cfg.url.replace('{z}', z).replace('{x}', x).replace('{y}', y));
        }
      }
    }
  }

  if (jobs.length >= PREWARM_BUDGET) {
    toast(`Область большая: прогреваю ${PREWARM_BUDGET} тайлов ближних масштабов.`, '', 6000);
  }
  toast(`Скачиваю ${jobs.length} тайлов в кеш…`, '', 4000);
  let done = 0;
  for (let i = 0; i < jobs.length; i += 24) {
    await Promise.allSettled(jobs.slice(i, i + 24).map((u) => fetch(u, { mode: 'no-cors' }).then(() => done++)));
    btn.textContent = `${Math.round((i / jobs.length) * 100)}%`;
  }
  btn.textContent = 'Офлайн';
  btn.disabled = false;
  toast(`Готово: ${done} тайлов в кеше.`, 'ok', 7000);
};

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
  navigator.serviceWorker.addEventListener('message', (event) => {
    if (event.data?.type !== 'updated') return;
    const node = document.createElement('div');
    node.className = 'toast ok';
    node.innerHTML = 'Вышла новая версия интерфейса. <button class="link" style="color:inherit">Обновить</button>';
    node.querySelector('button').onclick = () => location.reload();
    el('toasts').appendChild(node);
  });
}

// ═════════════════════════ Старт ═════════════════════════

bindSplitter();
updateNet();
boot().then(bindFilters);
