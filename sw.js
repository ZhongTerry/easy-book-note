const CACHE_NAME = 'notedb-v1.1.2';
const ASSETS = [
  '/',
  '/purecss/pure2.1.css',
  '/purecss/pure2.1.js',
  'https://cdn.jsdelivr.net/npm/chart.js',
  '/static/icons/favicon.ico',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

// 安装时缓存核心资源
self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS))
  );
});

// 策略：网络优先，失败则使用缓存
self.addEventListener('fetch', (e) => {
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});