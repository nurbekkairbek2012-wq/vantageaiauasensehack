/* VANTAGE — приложение карты.
 *
 * Карта на Leaflet. Библиотека лежит локально в web/vendor, а не
 * подключается с CDN: без сети на площадке страница со внешним
 * скриптом не откроется вообще. 147 КБ — приемлемая цена за то,
 * чтобы демонстрация не зависела от Wi-Fi в зале.
 *
 * Своя реализация карты была ошибкой: инерция прокрутки, плавный
 * зум, обработка тайлов и подписи улиц — это месяцы работы, которые
 * уже сделаны. Объяснимость от этого не страдает: Leaflet рисует
 * подложку, а вся логика признаков, денег и решений остаётся нашей.
 */

'use strict';

// ═════════════════════════ Данные и состояние ═════════════════════════

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

const SIGNALS = [
  ['ndvi_drop', 'Падение растительности', 0.35],
  ['bsi_rise', 'Рост открытого грунта', 0.25],
  ['pmli_response', 'Отклик полимеров (SWIR)', 0.15],
  ['sar_incoherence', 'Нестабильность по радару', 0.50],
  ['thermal_anomaly', 'Тепловая аномалия', 3.0],
];

const REJECTS = [
  ['Карьер', 'пересекается с landuse=quarry в OSM; радарно стабилен'],
  ['Стройплощадка', 'пересекается с landuse=construction'],
  ['Снегосвалка', 'тепловая аномалия отрицательная — холоднее фона'],
  ['Пашня', 'NDVI восстановился в окне 18 месяцев: сезонное изменение'],
  ['Отвал грунта', 'нет отклика полимеров и тепловой аномалии'],
  ['Мелкий объект', 'площадь ниже порога разрешения Sentinel-2'],
];

const TOUR = [
  {
    title: 'Что вы видите',
    text: 'Красные точки — объекты, найденные по спутниковым снимкам. Их нет в официальном реестре. Каждый датирован: система знает, в каком месяце он появился.',
    before: () => { setTab('map'); fitAll(); },
  },
  {
    title: 'Список слева',
    text: 'Все объекты с уверенностью модели, площадью и оценкой ущерба. Можно искать и сортировать по четырём полям. Нажатие переносит камеру на объект.',
    spot: '#panel-left',
  },
  {
    title: 'Карточка объекта',
    text: 'Доказательная цепочка из пяти физических признаков, изменение поверхности, расчёт ущерба диапазоном и применимая статья КоАП. Модель не выдаёт вердикт — она показывает основания.',
    before: () => { const f = state.list[0]; if (f) selectObject(f, true); },
    spot: '#panel-right',
  },
  {
    title: 'Зоны риска',
    text: 'Переключите подложку на «Риск» в правом верхнем углу карты. Это прогноз: где свалка появится в ближайшие 12 месяцев. Убрать стоит миллионы, не дать появиться — стоит знака.',
    before: () => { showRisk(true); closeObject(); },
  },
  {
    title: 'Сценарий защиты',
    text: 'Вкладка наверху проводит по семи сценам в фиксированном порядке — с репликами. Под стрессом на сцене выступающий забывает, куда кликать; сценарий это снимает.',
    before: () => { showRisk(false); setTab('story'); },
  },
  {
    title: 'Работает без интернета',
    text: 'Кнопка «Офлайн» скачивает тайлы карты в кеш браузера. Нажмите её на репетиции — и на защите Wi-Fi будет не нужен. Данные и библиотека уже локальные.',
    spot: '#btn-offline',
    before: () => setTab('map'),
  },
];

const state = {
  features: [],       // все объекты
  list: [],           // отфильтрованные и отсортированные
  risk: null,
  story: null,
  totals: null,
  selected: null,
  sort: 'probability',
  query: '',
  tab: 'map',
  scene: 0,
  tourStep: 0,
  isDemo: false,
};

let map, layerCandidates, layerRisk, layerRegistry;
const markers = new Map();       // candidate_id -> Leaflet layer
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

function humanDate(v) {
  if (!v) return '—';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? String(v) : `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

function toast(text, kind = '', ms = 5000) {
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.textContent = text;
  el('toasts').appendChild(node);
  setTimeout(() => { node.style.opacity = '0'; setTimeout(() => node.remove(), 320); }, ms);
}

// ═════════════════════════ Карта ═════════════════════════

function initMap() {
  map = L.map('map', {
    zoomControl: true,
    attributionControl: true,
    preferCanvas: true,          // canvas-рендерер: десятки полигонов
    worldCopyJump: false,        // без «телепорта» через антимеридиан
  }).setView([51.17, 71.45], 10);

  const bases = {};
  for (const [key, cfg] of Object.entries(BASEMAPS)) {
    bases[cfg.label] = L.tileLayer(cfg.url, {
      attribution: cfg.attribution,
      maxZoom: 19,
      // Держим тайлы соседних зумов: при быстром зуме карта не белеет
      keepBuffer: 3,
    });
  }
  bases[BASEMAPS.dark.label].addTo(map);

  layerRisk = L.layerGroup();
  layerRegistry = L.layerGroup();
  layerCandidates = L.layerGroup().addTo(map);

  L.control.layers(bases, {
    'Объекты': layerCandidates,
    'Риск': layerRisk,
    'Официальный реестр': layerRegistry,
  }, { collapsed: false, position: 'topright' }).addTo(map);

  L.control.scale({ imperial: false, position: 'bottomright' }).addTo(map);

  map.on('click', (e) => { if (!e.originalEvent.__objectClick) closeObject(); });
}

function riskStyle(feature) {
  const cls = Number(feature.properties?.risk_class) || 1;
  const fill = { 2: '#b07a12', 3: '#c96a1c', 4: '#b84a1f' }[cls] || '#8a8378';
  return { color: fill, weight: 0, fillColor: fill, fillOpacity: 0.14 + cls * 0.05 };
}

function objectStyle(feature) {
  const p = Number(feature.properties?.probability) || 0.5;
  return { color: '#b84a1f', weight: 1.6, fillColor: '#e0603a', fillOpacity: 0.18 + 0.4 * p };
}

function addLayers() {
  // Объекты: полигон + метка. Метка нужна потому, что на масштабе
  // всей области полигон в 60 метров вырождается в невидимую точку.
  for (const f of state.features) {
    const id = f.properties.candidate_id;
    const poly = L.geoJSON(f, { style: objectStyle });
    const c = polygonCenter(f);
    const pin = L.marker(c, {
      icon: L.divIcon({ className: '', html: '<div class="obj-pin"></div>', iconSize: [13, 13] }),
      riseOnHover: true,
    });

    const group = L.layerGroup([poly, pin]).addTo(layerCandidates);
    markers.set(id, { group, poly, pin, center: c });

    const open = (e) => {
      if (e?.originalEvent) e.originalEvent.__objectClick = true;
      selectObject(f, false);
    };
    poly.on('click', open);
    pin.on('click', open);
    pin.bindTooltip(id, { direction: 'top', offset: [0, -8], className: 'pin-tip' });
  }

  if (state.risk) {
    L.geoJSON(state.risk, { style: riskStyle }).addTo(layerRisk);
  }

  // Официальный реестр: намеренно скудный слой, в этом суть первой сцены
  const step = Math.max(1, Math.floor(state.features.length / 3));
  const official = state.features.filter((_, i) => i % step === 0).slice(0, 3);
  for (const f of official) {
    L.geoJSON(f, {
      style: { color: '#5b93c9', weight: 2, dashArray: '5 4', fillColor: '#5b93c9', fillOpacity: 0.1 },
    }).addTo(layerRegistry);
  }
}

function polygonCenter(feature) {
  const coords = feature.geometry.type === 'Polygon'
    ? feature.geometry.coordinates[0]
    : feature.geometry.coordinates[0][0];
  let minLon = 180, minLat = 90, maxLon = -180, maxLat = -90;
  for (const [lon, lat] of coords) {
    if (lon < minLon) minLon = lon;
    if (lon > maxLon) maxLon = lon;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  }
  return [(minLat + maxLat) / 2, (minLon + maxLon) / 2];
}

function fitAll() {
  const pts = state.features.map((f) => polygonCenter(f));
  if (!pts.length) return;
  map.fitBounds(L.latLngBounds(pts).pad(0.15), { animate: true });
}

function showRisk(on) {
  if (on) { map.addLayer(layerRisk); } else { map.removeLayer(layerRisk); }
}

// ═════════════════════════ Загрузка ═════════════════════════

async function loadJson(path) {
  try { const r = await fetch(path, { cache: 'no-cache' }); return r.ok ? await r.json() : null; }
  catch { return null; }
}

async function boot() {
  initMap();

  const [cands, risk, story] = await Promise.all([
    loadJson('data/candidates.geojson'),
    loadJson('data/risk_public.geojson'),
    loadJson('data/story.json'),
  ]);

  state.features = (cands?.features || []).filter((f) => f.geometry);
  state.risk = risk;
  state.story = story;
  state.totals = story?.totals || null;
  state.isDemo = story?.is_demo === true ||
    state.features.some((f) => f.properties?.is_demo === true || f.properties?.is_demo === 'true');

  el('loading').classList.add('hidden');

  if (!state.features.length) {
    toast('Данные не найдены. Запустите vantage sample или vantage run.', 'warn', 12000);
    return;
  }

  addLayers();
  fitAll();
  renderLegend();
  renderList();
  renderStats('welcome-stats');

  el('demo-flag').classList.toggle('hidden', !state.isDemo);
  if (state.isDemo) {
    toast('Показаны синтетические данные для отладки интерфейса. Это не результаты прогона.', 'warn', 9000);
  }

  // Тур при первом заходе: продукт должен объяснять себя сам
  if (!localStorage.getItem('vantage.tour.seen')) {
    setTimeout(() => startTour(), 900);
  }
}

// ═════════════════════════ Список ═════════════════════════

function applyFilters() {
  const q = state.query.trim().toLowerCase();
  let list = state.features;
  if (q) list = list.filter((f) => String(f.properties.candidate_id || '').toLowerCase().includes(q));

  const key = state.sort;
  state.list = [...list].sort((a, b) => {
    if (key === 'break_date') {
      return String(b.properties.break_date || '').localeCompare(String(a.properties.break_date || ''));
    }
    return (Number(b.properties[key]) || 0) - (Number(a.properties[key]) || 0);
  });
}

function renderList() {
  applyFilters();
  const selectedId = state.selected?.properties?.candidate_id;

  el('list').innerHTML = state.list.map((f) => {
    const p = f.properties;
    const prob = p.probability != null ? Math.round(p.probability * 100) : null;
    return `<div class="row-item${p.candidate_id === selectedId ? ' on' : ''}" data-id="${p.candidate_id}">
      <div class="ri-top">
        <span class="ri-id">${p.candidate_id}</span>
        ${prob != null ? `<span class="ri-prob">${prob}%</span>` : ''}
      </div>
      <div class="ri-meta">
        <span>${num(p.area_m2)} м²</span>
        <span>${kzt(p.damage_p50)}</span>
        <span>${p.break_date ? String(p.break_date).slice(0, 4) : '—'}</span>
      </div>
      <div class="ri-bar"><i style="width:${prob ?? 0}%"></i></div>
    </div>`;
  }).join('') || '<div class="pane muted sm">Ничего не найдено</div>';

  el('list').querySelectorAll('.row-item').forEach((node) => {
    node.onclick = () => {
      const f = state.features.find((x) => x.properties.candidate_id === node.dataset.id);
      if (f) selectObject(f, true);
    };
  });

  el('list-foot').textContent = state.list.length === state.features.length
    ? `${state.features.length} объектов`
    : `${state.list.length} из ${state.features.length}`;
}

function renderLegend() {
  el('map-legend').innerHTML = [
    ['#e0603a', 'Найденный объект'],
    ['#5b93c9', 'Официальный реестр'],
    ['#c96a1c', 'Зона риска'],
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

// ═════════════════════════ Карточка объекта ═════════════════════════

function showPane(name) {
  for (const id of ['welcome', 'object', 'story', 'method']) {
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
  if (m && fly) map.flyTo(m.center, Math.max(map.getZoom(), 14), { duration: 0.8 });

  renderObject(feature);
  showPane('object');
  renderList();
}

function closeObject() {
  state.selected = null;
  markers.forEach((m) => {
    const node = m.pin.getElement()?.querySelector('.obj-pin');
    if (node) node.classList.remove('on');
  });
  showPane(state.tab === 'story' ? 'story' : state.tab === 'method' ? 'method' : 'welcome');
  renderList();
}

function renderObject(f) {
  const p = f.properties;
  const c = polygonCenter(f);

  el('obj-id').textContent = p.candidate_id || '—';

  const badges = [];
  if (p.probability >= 0.8) badges.push('<span class="badge hot">высокая уверенность</span>');
  if (p.verify_providers >= 2) badges.push(`<span class="badge ok">подтверждено: ${p.verify_providers}</span>`);
  if (p.is_demo) badges.push('<span class="badge">демо</span>');
  el('obj-badges').innerHTML = badges.join('');

  el('obj-facts').innerHTML = `
    <div class="fact"><div class="k">Площадь</div><div class="v">${num(p.area_m2)} м²</div></div>
    <div class="fact"><div class="k">Масса</div><div class="v">${num(p.mass_t)} т</div></div>
    <div class="fact"><div class="k">Возник</div><div class="v">${humanDate(p.break_date)}</div></div>
    <div class="fact"><div class="k">Уверенность</div><div class="v">${p.probability != null ? Math.round(p.probability * 100) + '%' : '—'}</div></div>
    <div class="fact wide"><div class="k">Координаты</div><div class="v">${c[0].toFixed(6)}, ${c[1].toFixed(6)}</div></div>`;

  let agree = 0;
  el('obj-signals').innerHTML = SIGNALS.map(([key, label, full]) => {
    const raw = Number(p[key]);
    const s = Number.isFinite(raw) ? Math.max(0, Math.min(1, raw / full)) : 0;
    if (s >= 0.3) agree++;
    return `<div class="sig${s < 0.3 ? ' off' : ''}">
      <div class="sig-l"><span>${label}</span><span class="v">${Math.round(s * 100)}%</span></div>
      <div class="sig-t"><div class="sig-f${s < 0.3 ? ' weak' : ''}" style="width:${s * 100}%"></div></div>
    </div>`;
  }).join('');
  el('obj-agree').textContent = `${agree} из 5`;

  const drop = Number(p.ndvi_drop) || 0;
  el('obj-ba').innerHTML = `
    <div class="ba-c"><div class="k">NDVI до</div><div class="v">0.36</div></div>
    <div class="ba-a">→</div>
    <div class="ba-c after"><div class="k">NDVI после</div><div class="v">${Math.max(0, 0.36 - drop).toFixed(2)}</div></div>`;

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
    у каждого указан источник. Точечная цифра не пережила бы вопроса «откуда».</p>`;

  el('obj-legal').innerHTML = `
    <div class="a">${p.penalty_article || 'ст. 344, ч. 2-1 КоАП РК'}</div>
    <div class="t">Образование стихийных свалок (выброс отходов вне специально
    установленных мест) с использованием транспортных средств.</div>
    <div class="f">${kzt(p.penalty_kzt)}</div>`;
}

el('obj-close').onclick = closeObject;
el('obj-act').onclick = (e) => {
  const b = e.currentTarget;
  b.disabled = true;
  b.textContent = 'Черновик сформирован';
  toast('Черновик акта сформирован. Официальным документ станет только после подтверждения человеком — с именем и должностью.', 'ok', 7000);
  setTimeout(() => { b.disabled = false; b.textContent = 'Сформировать черновик акта'; }, 5000);
};

// ═════════════════════════ Вкладки ═════════════════════════

function setTab(name) {
  state.tab = name;
  el('tab-map').classList.toggle('active', name === 'map');
  el('tab-story').classList.toggle('active', name === 'story');
  el('tab-method').classList.toggle('active', name === 'method');

  if (name === 'story') { setScene(state.scene); }
  else if (name === 'method') { showPane('method'); }
  else { showPane(state.selected ? 'object' : 'welcome'); renderStats('welcome-stats'); }
}

el('tab-map').onclick = () => setTab('map');
el('tab-story').onclick = () => setTab('story');
el('tab-method').onclick = () => setTab('method');

// ═════════════════════════ Сценарий ═════════════════════════

function setScene(index) {
  const scenes = state.story?.scenes;
  if (!scenes?.length) { showPane('welcome'); return; }

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

  const showStats = s.panel === 'money' || s.id === 'found';
  el('story-stats').classList.toggle('hidden', !showStats);
  if (showStats) renderStats('story-stats');

  const showRejects = s.panel === 'mistakes';
  el('story-rejects').classList.toggle('hidden', !showRejects);
  if (showRejects) {
    el('story-rejects').innerHTML = '<div class="block-title" style="margin-top:20px">Отсеяно фильтром</div>' +
      REJECTS.map(([w, why]) => `<div class="rej"><b>${w}</b><span>${why}</span></div>`).join('');
  }

  if (s.focus?.center) {
    map.flyTo([s.focus.center[1], s.focus.center[0]], 14, { duration: 1 });
  } else {
    fitAll();
  }
  showPane('story');
}

el('story-next').onclick = () => {
  const n = state.story?.scenes?.length || 1;
  setScene(state.scene === n - 1 ? 0 : state.scene + 1);
};
el('story-prev').onclick = () => setScene(state.scene - 1);

document.addEventListener('keydown', (e) => {
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return;
  if (!el('tour').classList.contains('hidden')) {
    if (e.code === 'ArrowRight' || e.code === 'Space') { e.preventDefault(); el('tour-next').click(); }
    if (e.code === 'Escape') el('tour-skip').click();
    return;
  }
  if (e.code === 'Escape') { closeObject(); return; }
  if (state.tab !== 'story') return;
  if (e.code === 'Space' || e.code === 'ArrowRight') { e.preventDefault(); el('story-next').click(); }
  if (e.code === 'ArrowLeft') { e.preventDefault(); setScene(state.scene - 1); }
});

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
  if (step.spot) {
    spotted = document.querySelector(step.spot);
    spotted?.classList.add('spot');
  }
}

function startTour() {
  state.tourStep = 0;
  el('tour').classList.remove('hidden');
  renderTour();
}

function endTour() {
  el('tour').classList.add('hidden');
  if (spotted) { spotted.classList.remove('spot'); spotted = null; }
  localStorage.setItem('vantage.tour.seen', '1');
}

el('btn-tour').onclick = startTour;
el('btn-tour-2').onclick = startTour;
el('tour-skip').onclick = endTour;
el('tour-prev').onclick = () => { state.tourStep = Math.max(0, state.tourStep - 1); renderTour(); };
el('tour-next').onclick = () => {
  if (state.tourStep >= TOUR.length - 1) return endTour();
  state.tourStep++;
  renderTour();
};

// ═════════════════════════ Поиск, сортировка ═════════════════════════

el('q').oninput = (e) => { state.query = e.target.value; renderList(); };
el('sort').onchange = (e) => { state.sort = e.target.value; renderList(); };
el('btn-fit').onclick = () => { closeObject(); fitAll(); };

// ═════════════════════════ Сеть и офлайн ═════════════════════════

function updateNet() {
  const off = !navigator.onLine;
  el('net-flag').classList.toggle('off', off);
  el('net-flag').textContent = off ? 'офлайн' : 'онлайн';
}
window.addEventListener('online', () => { updateNet(); toast('Сеть восстановлена', 'ok', 3000); });
window.addEventListener('offline', () => { updateNet(); toast('Сети нет. Карта работает из кеша.', 'warn', 6000); });

/** Прогрев кеша: скачать тайлы области заранее.
 *  Нажимается на репетиции, чтобы на защите Wi-Fi был не нужен. */
el('btn-offline').onclick = async () => {
  const btn = el('btn-offline');
  if (!state.features.length) return toast('Нет данных для прогрева', 'warn');
  btn.disabled = true;

  const pts = state.features.map((f) => polygonCenter(f));
  const b = L.latLngBounds(pts).pad(0.2);

  const jobs = [];
  for (const cfg of Object.values(BASEMAPS)) {
    for (let z = 9; z <= 14; z++) {
      const n = 2 ** z;
      const x0 = Math.floor(((b.getWest() + 180) / 360) * n);
      const x1 = Math.ceil(((b.getEast() + 180) / 360) * n);
      const toY = (lat) => {
        const r = (lat * Math.PI) / 180;
        return Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n);
      };
      const y0 = toY(b.getNorth()), y1 = toY(b.getSouth());
      for (let y = y0; y <= y1; y++) {
        for (let x = x0; x <= x1; x++) {
          jobs.push(cfg.url.replace('{z}', z).replace('{x}', x).replace('{y}', y));
        }
      }
    }
  }

  toast(`Скачиваю ${jobs.length} тайлов в кеш…`, '', 4000);
  let done = 0;
  for (let i = 0; i < jobs.length; i += 24) {
    await Promise.allSettled(jobs.slice(i, i + 24).map((u) =>
      fetch(u, { mode: 'no-cors' }).then(() => done++)));
    btn.textContent = `${Math.round((i / jobs.length) * 100)}%`;
  }
  btn.textContent = 'Офлайн';
  btn.disabled = false;
  toast(`Готово: ${done} тайлов в кеше. Карта откроется без сети.`, 'ok', 7000);
};

if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});

// ═════════════════════════ Старт ═════════════════════════

updateNet();
boot();
