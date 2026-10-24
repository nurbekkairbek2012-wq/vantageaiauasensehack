/* Service worker: офлайн-first.
 *
 * Демонстрация не должна зависеть от сети на площадке. Wi-Fi в зале
 * может не работать, а очередь из ста команд на одну точку доступа
 * кладёт её надёжно.
 *
 * Три стратегии по типам ресурсов, и каждая выбрана по своей причине:
 *
 *   оболочка (html/css/js)  cache-first — не меняется во время
 *       выступления, из кеша открывается мгновенно;
 *   данные (geojson/json)   network-first с откатом в кеш — если сеть
 *       есть, показываем свежий прогон; если нет, последний известный.
 *       Обратный порядок означал бы, что после нового прогона карта
 *       продолжает показывать вчерашний результат;
 *   тайлы подложки          cache-first — они неизменяемы по своей
 *       природе: тайл z/x/y всегда один и тот же. Ходить за ним в сеть
 *       повторно бессмысленно, а офлайн он нужен обязательно.
 */

const VERSION = 'vantage-v3';
const SHELL_CACHE = `${VERSION}-shell`;
const DATA_CACHE = `${VERSION}-data`;
const TILE_CACHE = `${VERSION}-tiles`;

const SHELL = [
  './',
  './index.html',
  './landing.css',
  './landing.js',
  './app.html',
  './styles.css',
  './app.js',
  './vendor/leaflet.js',
  './vendor/leaflet.css',
  './manifest.webmanifest',
];

const TILE_HOSTS = new Set([
  'basemaps.cartocdn.com',
  'server.arcgisonline.com',
]);

//: Больше этого числа тайлов не храним. Область Астаны на зумах 9–14
//: укладывается примерно в полторы тысячи; запас нужен на перемещения
//: по карте, но не бесконечный: браузер вычистит переполненное
//: хранилище целиком и без предупреждения.
const MAX_TILES = 4000;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL))
      // Перед выступлением нужна предсказуемая версия, а не постепенная
      // миграция по мере закрытия вкладок.
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

function isData(url) {
  return url.pathname.includes('/data/') ||
         url.pathname.endsWith('.geojson') ||
         url.pathname.endsWith('.json');
}

async function trimTileCache() {
  const cache = await caches.open(TILE_CACHE);
  const keys = await cache.keys();
  if (keys.length <= MAX_TILES) return;
  // Удаляем самые старые: порядок ключей в Cache API — порядок добавления.
  await Promise.all(keys.slice(0, keys.length - MAX_TILES).map((k) => cache.delete(k)));
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  // --- Тайлы подложки: cache-first --------------------------------- //
  if (TILE_HOSTS.has(url.hostname)) {
    event.respondWith(
      caches.open(TILE_CACHE).then(async (cache) => {
        const cached = await cache.match(request);
        if (cached) return cached;
        try {
          // Тайлы отдаются без CORS-заголовков, поэтому запрос идёт
          // в режиме no-cors и ответ получается непрозрачным. Для
          // отрисовки в canvas через <img> этого достаточно.
          const response = await fetch(request);
          if (response && (response.ok || response.type === 'opaque')) {
            cache.put(request, response.clone());
            trimTileCache();
          }
          return response;
        } catch {
          // Пустой прозрачный PNG вместо разорванной картинки:
          // дыра в подложке выглядит как поломка, а её нет.
          return new Response(null, { status: 504 });
        }
      })
    );
    return;
  }

  if (url.origin !== self.location.origin) return;

  // --- Данные: network-first ---------------------------------------- //
  if (isData(url)) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(DATA_CACHE).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.open(DATA_CACHE).then((cache) => cache.match(request).then((cached) =>
          cached || new Response(
            JSON.stringify({ error: 'нет сети и нет кеша для этого файла' }),
            { status: 503, headers: { 'Content-Type': 'application/json; charset=utf-8' } }
          )
        )))
    );
    return;
  }

  // --- Оболочка: cache-first ---------------------------------------- //
  event.respondWith(
    caches.open(SHELL_CACHE).then(async (cache) => {
      const cached = await cache.match(request);
      if (cached) return cached;
      const response = await fetch(request);
      if (response.ok) cache.put(request, response.clone());
      return response;
    })
  );
});

self.addEventListener('message', (event) => {
  if (event.data?.type === 'warm') {
    event.waitUntil(
      caches.open(DATA_CACHE).then((cache) =>
        Promise.allSettled([
          './data/candidates.geojson',
          './data/risk_public.geojson',
          './data/story.json',
        ].map((p) => cache.add(p)))
      )
    );
  }
});
