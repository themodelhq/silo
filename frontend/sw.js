/**
 * Silo Service Worker
 * Cache-first for the app shell (works fully offline once installed),
 * network-first for anything else, with a background-sync queue for
 * actions taken while offline (envelope transfers, transactions, etc.)
 */

const CACHE_VERSION = "silo-v3";
const APP_SHELL = [
  "./",
  "./index.html",
  "./offline.html",
  "./manifest.json",
  "./css/styles.css",
  "./js/app.js",
  "./js/parser.js",
  "./js/envelope-engine.js",
  "./js/storage.js",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

// Files that must never be served stale — config.js controls which backend
// the app talks to (see js/config.js), so a cached copy surviving a
// redeploy could point Settings → Payment account (and the envelope
// Transfer To "Account" option) at a stale or missing backend URL, showing
// "The backend isn't configured yet" even after the person fixed it. It's
// deliberately left out of APP_SHELL above (index.html loads it via a
// versioned, cache-busted URL — see the script tag) and is always fetched
// fresh here regardless of that; netlify.toml also sets a matching
// no-cache header so the CDN/browser HTTP cache can't serve it stale
// either. The rest of the app shell stays cache-first so the installed PWA
// still opens instantly offline.
const NETWORK_FIRST = ["./js/config.js"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((key) => key !== CACHE_VERSION).map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  const isNetworkFirst = NETWORK_FIRST.some((path) => url.pathname.endsWith(path.replace("./", "/")));

  if (isNetworkFirst) {
    event.respondWith(
      fetch(request, { cache: "no-store" })
        .then((response) => {
          if (response && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_VERSION).then((cache) => cache.put(request, clone));
          }
          return response;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      const networkFetch = fetch(request)
        .then((response) => {
          if (response && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_VERSION).then((cache) => cache.put(request, clone));
          }
          return response;
        })
        .catch(() => cached || caches.match("./offline.html"));

      // Cache-first for the app shell so the installed app opens instantly
      // offline; fall back to network for anything not yet cached.
      return cached || networkFetch;
    })
  );
});

// Background sync: replay queued actions (transactions/transfers made while
// offline) once connectivity returns. The queue itself lives in IndexedDB
// (see js/storage.js); this just triggers a client-side flush.
self.addEventListener("sync", (event) => {
  if (event.tag === "silo-sync-queue") {
    event.waitUntil(
      self.clients.matchAll().then((clients) => {
        clients.forEach((client) => client.postMessage({ type: "FLUSH_SYNC_QUEUE" }));
      })
    );
  }
});

// Push notifications (salary day, bill reminders, etc.) — wired to a real
// push service (FCM/OneSignal) at deployment time; this handler renders
// whatever payload arrives.
self.addEventListener("push", (event) => {
  if (!event.data) return;
  const payload = event.data.json();
  event.waitUntil(
    self.registration.showNotification(payload.title || "Silo", {
      body: payload.body || "",
      icon: "./icons/icon-192.png",
      badge: "./icons/icon-192.png",
    })
  );
});
