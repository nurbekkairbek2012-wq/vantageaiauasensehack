/* Service worker: офлайн-first.
 *
 * Демонстрация не должна зависеть от сети на площадке. Wi-Fi в зале
 * может не работать, мобильный интернет в подвале не ловит, а очередь
 * из ста команд на одну точку доступа кладёт её надёжно.
 *
 * Стратегия по типам ресурсов разная, и это осознанно:
 *
 *   оболочка (html/css/js) — cache-first: она не меняется во время
 *       выступления, а из кеша открывается мгновенно;
 *   данные (geojson/json)  — network-first с откатом в кеш: если сеть
 *       есть, показываем свежий прогон; если нет — последний известный.
 *
 * Обратный порядок был бы хуже: cache-first на данных означал бы, что
 * после нового прогона карта продолжает показывать вчерашний результат.
 */

const VERSION = 'vantage-v1';
const SHELL = [
  './',
  './index.html',
  './styles.css',
  './app.js',
  './manifest.webmanifest',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(VERSION)
      .then((cache) => cache.addAll(SHELL))
      // Не ждём закрытия старых вкладок: перед выступлением нужна
      // предсказуемая версия, а не постепенная миграция.
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key !== VERSION).map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

function isData(url) {
  return url.pathname.includes('/data/') ||
         url.pathname.endsWith('.geojson') ||
         url.pathname.endsWith('.json');
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (isData(url)) {
    // network-first: свежий прогон важнее скорости
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(VERSION).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match(request).then((cached) =>
          cached || new Response(
            JSON.stringify({ error: 'нет сети и нет кеша для этого файла' }),
            { status: 503, headers: { 'Content-Type': 'application/json; charset=utf-8' } }
          )
        ))
    );
    return;
  }

  // cache-first для оболочки
  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request).then((response) => {
      const copy = response.clone();
      caches.open(VERSION).then((cache) => cache.put(request, copy));
      return response;
    }))
  );
});

/* Ручной прогрев кеша перед выступлением:
   на странице выполнить navigator.serviceWorker.controller.postMessage({type:'warm'}) */
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'warm') {
    const extra = [
      './data/candidates.geojson',
      './data/risk_public.geojson',
      './data/story.json',
    ];
    event.waitUntil(
      caches.open(VERSION).then((cache) =>
        Promise.allSettled(extra.map((path) => cache.add(path)))
      )
    );
  }
});
