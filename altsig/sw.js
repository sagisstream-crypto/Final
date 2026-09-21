/* ALTSIG service worker: кэшируем только оболочку, рыночные данные — никогда. */
const CACHE = 'altsig-shell-1';
const SHELL = ['./', './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return;
  if (url.hostname.indexOf('binance') !== -1) return;          // котировки всегда из сети
  if (url.hostname.indexOf('telegram') !== -1) return;         // отправка сигналов — мимо кэша
  if (e.request.mode === 'navigate') {
    e.respondWith(fetch(e.request).catch(() => caches.match('./index.html')));
    return;
  }
  e.respondWith(caches.match(e.request).then(hit => hit || fetch(e.request)));
});
