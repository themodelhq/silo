/**
 * Silo Service Worker
 * Cache-first for the app shell (works fully offline once installed),
 * network-first for anything else, with a background-sync queue for
 * actions taken while offline (envelope transfers, transactions, etc.)
 */

const CACHE_VERSION = "silo-v1";
const APP_SHELL = [
  "./",
  "./index.html",
  "./offline.html",
  "./manifest.json",
  "./css/styles.css",
  "./js/config.js",
  "./js/app.js",
  "./js/parser.js",
  "./js/envelope-engine.js",
  "./js/storage.js",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

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
