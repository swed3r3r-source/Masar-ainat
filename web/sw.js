/* عامل الخدمة لتطبيق السائق — يتيح فتح التطبيق دون اتصال.

   قاعدة حاكمة: **لا تُخزَّن استجابات API الحساسة في الذاكرة المؤقتة.**
   يُخزَّن هيكل التطبيق فقط (HTML/CSS/JS)، أما بيانات الرحلات فتُدار في
   ذاكرة التطبيق نفسه بضوابط الجلسة، لأن تخزين بيانات العينات والمواقع في
   ذاكرة المتصفح المشتركة يخالف تصنيفها كبيانات حساسة (§29).
*/

const VERSION = 'masar-shell-v1';
const SHELL = [
  '/driver',
  '/static/css/masar.css',
  '/static/js/core.js',
  '/static/js/driver.js',
  '/static/js/map.js',
  '/manifest.webmanifest',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(VERSION)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key !== VERSION).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // لا تخزين لأي مسار API — البيانات التشغيلية حساسة
  if (url.pathname.startsWith('/api/')) return;

  event.respondWith(
    caches.match(request).then((cached) => {
      const network = fetch(request)
        .then((response) => {
          if (response.ok && SHELL.some((path) => url.pathname === path)) {
            const copy = response.clone();
            caches.open(VERSION).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => cached || caches.match('/driver'));
      return cached || network;
    }),
  );
});
