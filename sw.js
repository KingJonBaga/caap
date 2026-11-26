/**
 * sw.js — NURTURA app-shell service worker
 *
 * Caches all static files so the app opens offline (design.md §5).
 * API calls (/api/*) are never cached — they always go to the network.
 *
 * To force a cache refresh, increment the version number in CACHE_NAME.
 */

const CACHE_NAME = 'nurtura-app-shell-v2';

/** Files that make up the app shell. */
const urlsToCache = [
  '/',
  '/index.html',
  // CSS
  '/css/tokens.css',
  '/css/typography.css',
  '/css/login.css',
  '/css/inputs.css',
  '/css/buttons.css',
  '/css/dashboard.css',
  '/css/provider.css',
  '/css/caseload.css',
  '/css/caretaker.css',
  '/css/register.css',
  '/css/care-tools.css',
  '/css/schedule.css',
  '/css/auth-polish.css',
  '/css/polish.css',
  // JS
  '/js/breakpoints.js',
  '/js/session.js',
  '/js/utils.js',
  '/js/offline-store.js',
  '/js/sync-ui.js',
  '/js/auth.js',
  '/js/navigation.js',
  '/js/admin.js',
  '/js/provider.js',
  '/js/caretaker.js',
  '/js/init.js',
];

// ── Install: pre-cache the app shell ─────────────────────────────────────────

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(urlsToCache);
    })
  );
  // Skip the waiting phase so the new SW activates without a second reload.
  self.skipWaiting();
});

// ── Activate: clean up old caches ────────────────────────────────────────────

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      );
    })
  );
  // Claim all open clients so they switch to this SW immediately.
  self.clients.claim();
});

// ── Fetch: serve from cache, fall back to network ────────────────────────────

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Never intercept API calls — always go straight to the network.
  if (url.pathname.startsWith('/api/')) {
    return;
  }

  // For everything else, try the cache first (cache-first strategy).
  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) {
        return cached;
      }
      // Not in cache — fetch from network and cache the response for next time.
      return fetch(request).then((networkResponse) => {
        // Only cache successful, non-opaque responses to our own origin.
        if (
          networkResponse &&
          networkResponse.status === 200 &&
          networkResponse.type === 'basic'
        ) {
          const responseToCache = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(request, responseToCache);
          });
        }
        return networkResponse;
      });
    })
  );
});
