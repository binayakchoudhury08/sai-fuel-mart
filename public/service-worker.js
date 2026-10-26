/* Caches only the app's static files. Pages and data always come from the
   server so nobody ever sees stale balances. */
const CACHE = "sfm-static-v3.10.0";
const ASSETS = ["/static/css/app.css", "/static/js/app.js", "/static/fonts/Barlow-Regular.woff2",
  "/static/fonts/Barlow-Medium.woff2", "/static/fonts/Barlow-SemiBold.woff2", "/static/fonts/Barlow-Bold.woff2",
  "/static/fonts/BarlowCondensed-SemiBold.woff2", "/static/fonts/BarlowCondensed-Bold.woff2",
  "/static/icons/icon-192.png", "/static/offline.html", "/static/js/livecam.js"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting())); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/static/")) {
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => {
      const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); return res; })));
    return;
  }
  if (req.mode === "navigate") e.respondWith(fetch(req).catch(() => caches.match("/static/offline.html")));
});
