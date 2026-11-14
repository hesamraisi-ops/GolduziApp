const CACHE_NAME = 'golduzi-v210';
self.addEventListener('install', (e) => { self.skipWaiting(); });
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  // برای فایل‌های HTML همیشه اول سرور را چک کن
  if (e.request.mode === 'navigate' || (e.request.method === 'GET' && e.request.headers.get('accept').includes('text/html'))) {
      return e.respondWith(fetch(e.request).catch(() => caches.match('/')));
  }
  if (e.request.method !== 'GET' || e.request.url.includes('/api/')) return;
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});