/* Service worker: офлайн-first.
 *
 * Демонстрация не должна зависеть от сети на площадке. Wi-Fi в зале
 * может не работать, а очередь из ста команд на одну точку доступа
 * кладёт её надёжно.
 *
 * Стратегии по типам ресурсов:
 *
 *   HTML-страницы     network-first — иначе после выката новой версии
 *       пользователь продолжает видеть старую и не понимает, почему
 *       ничего не изменилось. Ровно это и произошло с предыдущей
 *       версией: cache-first на оболочке залипает намертво;
 *   css/js            stale-while-revalidate — отдаём мгновенно из кеша
 *       и параллельно обновляем в фоне;
 *   данные            network-first с откатом в кеш — свежий прогон
 *       важнее скорости загрузки;
 *   тайлы подложки    cache-first — тайл z/x/y неизменяем по своей
 *       природе, ходить за ним повторно бессмысленно.
 */

const VERSION = 'vantage-v4';
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

const MAX_TILES = 4000;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL))
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
      .then(() => self.clients.matchAll({ type: 'window' }))
      .then((clients) => {
        // Сообщаем открытым вкладкам, что версия сменилась: страница
        // предложит перезагрузиться, а не будет молча показывать смесь
        // старого интерфейса и новых данных.
        for (const client of clients) {
          client.postMessage({ type: 'updated', version: VERSION });
        }
      })
  );
});

const isHtml = (request, url) =>
  request.mode === 'navigate' ||
  url.pathname.endsWith('.html') ||
  url.pathname.endsWith('/');

const isData = (url) =>
  url.pathname.includes('/data/') ||
  url.pathname.endsWith('.geojson') ||
  url.pathname.endsWith('.json');

async function trimTileCache() {
  const cache = await caches.open(TILE_CACHE);
  const keys = await cache.keys();
  if (keys.length <= MAX_TILES) return;
  await Promise.all(keys.slice(0, keys.length - MAX_TILES).map((k) => cache.delete(k)));
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  // --- Тайлы подложки: cache-first ---------------------------------- //
  if (TILE_HOSTS.has(url.hostname)) {
    event.respondWith(
      caches.open(TILE_CACHE).then(async (cache) => {
        const cached = await cache.match(request);
        if (cached) return cached;
        try {
          const response = await fetch(request);
          if (response && (response.ok || response.type === 'opaque')) {
            cache.put(request, response.clone());
            trimTileCache();
          }
          return response;
        } catch {
          return new Response(null, { status: 504 });
        }
      })
    );
    return;
  }

  if (url.origin !== self.location.origin) return;

  // --- HTML: network-first ------------------------------------------ //
  if (isHtml(request, url)) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match(request).then((cached) => cached || caches.match('./index.html')))
    );
    return;
  }

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

  // --- Остальное: stale-while-revalidate ---------------------------- //
  event.respondWith(
    caches.open(SHELL_CACHE).then(async (cache) => {
      const cached = await cache.match(request);
      const network = fetch(request)
        .then((response) => {
          if (response.ok) cache.put(request, response.clone());
          return response;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});

self.addEventListener('message', (event) => {
  if (event.data?.type === 'skip-waiting') self.skipWaiting();
});
