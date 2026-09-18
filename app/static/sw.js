// Kassenstürzle Service Worker – Modul 1: PWA
const CACHE_NAME   = 'kassenstuerzle-v2';  // v2: CSRF + Security Headers
const CACHE_STATIC = 'kassenstuerzle-static-v1';

// Statische Assets voraufladen (App Shell)
const PRECACHE = [
  '/',
  '/offline',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

// ── Install: App Shell cachen ─────────────────────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_STATIC)
      .then(cache => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

// ── Activate: alte Caches bereinigen ──────────────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys
        .filter(k => k !== CACHE_NAME && k !== CACHE_STATIC)
        .map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch: Routing-Strategie ───────────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Nur GET-Requests cachen
  if (event.request.method !== 'GET') return;

  // API-Aufrufe: Network-First (bei Offline: Fehler zurückgeben, kein Cache)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(event.request).catch(() =>
        new Response(JSON.stringify({ success: false, error: 'Offline' }),
          { headers: { 'Content-Type': 'application/json' } })
      )
    );
    return;
  }

  // Statische Assets (CSS, JS, Fonts, Icons): Cache-First
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then(cached =>
        cached || fetch(event.request).then(res => {
          const clone = res.clone();
          caches.open(CACHE_STATIC).then(c => c.put(event.request, clone));
          return res;
        })
      )
    );
    return;
  }

  // HTML-Seiten: Network-First, Offline-Fallback
  event.respondWith(
    fetch(event.request)
      .then(res => {
        const clone = res.clone();
        caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
        return res;
      })
      .catch(async () => {
        const cached = await caches.match(event.request);
        return cached || caches.match('/offline');
      })
  );
});

// ── Push Notifications (Modul 5 – vorbereitet) ────────────────────────────────
self.addEventListener('push', event => {
  if (!event.data) return;
  let data;
  try { data = event.data.json(); }
  catch { data = { title: 'Kassenstürzle', body: event.data.text() }; }

  event.waitUntil(
    self.registration.showNotification(data.title || 'Kassenstürzle', {
      body:    data.body  || '',
      icon:    '/static/icons/icon-192.png',
      badge:   '/static/icons/badge-72.png',
      vibrate: [200, 100, 200],
      tag:     'kassenstuerzle',
      renotify: true,
      data:    { url: data.url || '/' }
    })
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: 'window' }).then(list => {
      for (const c of list) if (c.url === '/' && 'focus' in c) return c.focus();
      if (clients.openWindow) return clients.openWindow(event.notification.data?.url || '/');
    })
  );
});
