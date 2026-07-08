/* Talking Gym PWA — app shell cache.
   HTML is network-first (UI updates propagate on next online load);
   static assets are cache-first. Offline falls back to the cached shell. */
const CACHE = "tg-app-v13";
const ASSETS = ["/app", "/app/manifest.webmanifest", "/app/icon-192.png", "/app/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname !== "/app" && !url.pathname.startsWith("/app/")) return;
  // Never proxy media: <video> uses Range requests, which stall when routed
  // through a service worker. The server sets long-lived cache headers instead.
  if (e.request.headers.get("range") || url.pathname.endsWith(".mp4")) return;

  if (url.pathname === "/app") {
    // network-first: always try to get the freshest UI, cache it, fall back offline
    e.respondWith(
      fetch(e.request)
        .then((resp) => {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
          return resp;
        })
        .catch(() => caches.match(e.request, { ignoreSearch: true }))
    );
  } else {
    e.respondWith(
      caches.match(e.request, { ignoreSearch: true }).then((r) => r || fetch(e.request))
    );
  }
});
