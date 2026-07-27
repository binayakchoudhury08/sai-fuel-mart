// Sai Fuel Mart — service worker
//
// This app manages live fuel stock, live ATG sync, and financial data —
// it must always show the true current numbers, never stale cached
// data. So this worker does NOT cache dynamic pages or API responses.
// It only caches static assets (CSS/JS/icons) for a faster load and to
// satisfy the installability requirement for an Android APK wrapper.

const CACHE_NAME = "sfm-static-v1";

const STATIC_ASSETS = [
    "/static/css/daily_closing.css",
    "/static/js/daily_closing.js",
    "/static/images/icons/icon-192.png",
    "/static/images/icons/icon-512.png"
];

self.addEventListener("install", function (event) {
    event.waitUntil(
        caches.open(CACHE_NAME).then(function (cache) {
            return cache.addAll(STATIC_ASSETS).catch(function () {
                // don't fail install if one asset is missing — best effort
            });
        })
    );
    self.skipWaiting();
});

self.addEventListener("activate", function (event) {
    event.waitUntil(
        caches.keys().then(function (keys) {
            return Promise.all(
                keys.filter(function (key) { return key !== CACHE_NAME; })
                    .map(function (key) { return caches.delete(key); })
            );
        })
    );
    self.clients.claim();
});

self.addEventListener("fetch", function (event) {
    const url = new URL(event.request.url);

    // only ever serve cached content for static assets — everything
    // else (pages, /api/*, exports) always goes to the network fresh
    const isStaticAsset = url.pathname.startsWith("/static/");

    if (!isStaticAsset || event.request.method !== "GET") {
        return; // let the browser handle it normally (network)
    }

    event.respondWith(
        caches.match(event.request).then(function (cached) {
            const networkFetch = fetch(event.request).then(function (response) {
                if (response && response.status === 200) {
                    const clone = response.clone();
                    caches.open(CACHE_NAME).then(function (cache) {
                        cache.put(event.request, clone);
                    });
                }
                return response;
            }).catch(function () {
                return cached;
            });

            return cached || networkFetch;
        })
    );
});
