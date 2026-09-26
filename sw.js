const CACHE_NAME = 'notedb-static-v1.2.0';
const ASSETS = [
  '/',
  '/purecss/pure2.1.css',
  '/purecss/pure2.1.js',
  '/static/icons/favicon.ico',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => Promise.all(
      ASSETS.map((asset) => cache.add(asset).catch(() => undefined)),
    )),
  );
  self.skipWaiting();
});

// Remove the old cache that intercepted pages and API requests.
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((key) => key.startsWith('notedb-') && key !== CACHE_NAME)
        .map((key) => caches.delete(key)),
    )),
  );
  self.clients.claim();
});

function isStaticAsset(url) {
  return url.pathname === '/' ||
    url.pathname === '/manifest.json' ||
    url.pathname.startsWith('/static/') ||
    url.pathname.startsWith('/purecss/');
}

// Reading pages and APIs are user-specific and may return HTML or JSON. Do not
// proxy them through Cache Storage: a cache miss must never become undefined.
self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin || !isStaticAsset(url)) {
    return;
  }

  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok && response.type === 'basic') {
          caches.open(CACHE_NAME).then((cache) => cache.put(request, response.clone()));
        }
        return response;
      })
      .catch(() => caches.match(request).then(
        (cached) => cached || new Response('Offline and no cached asset is available.', {
          status: 503,
          headers: {'Content-Type': 'text/plain; charset=utf-8'},
        }),
      )),
  );
});
