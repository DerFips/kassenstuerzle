// Kassenstürzle Service Worker – Modul 1: PWA
//
// Registriert wird /sw.js (Scope '/'), nicht /static/sw.js: mit dem Scope
// '/static/' steuerte der Worker keine einzige Seite. Die Offline-Seite kam
// nie, und "Push aktivieren" wartete endlos auf navigator.serviceWorker.ready.
//
// Seit der Worker alle Seiten steuert, gilt:
//  * Nur Anfragen an den eigenen Host werden angefasst. Fuer den Worker gilt
//    connect-src 'self'; ein fetch() auf ein CDN schluege fehl.
//  * HTML-Seiten werden NICHT gecacht. Sie enthalten Finanzdaten und laegen
//    sonst nach dem Abmelden weiter lesbar im Browser-Cache.
const CACHE_STATIC = 'kassenstuerzle-static-v2';

// Statische Assets voraufladen (App Shell)
const PRECACHE = [
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
// Entfernt auch 'kassenstuerzle-v2', in dem fruehere Versionen ganze Seiten
// ablegten.
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys
        .filter(k => k !== CACHE_STATIC)
        .map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch: Routing-Strategie ───────────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  if (event.request.method !== 'GET') return;
  // Fremde Hosts (CDN) laedt der Browser selbst.
  if (url.origin !== self.location.origin) return;

  // API-Aufrufe: immer Netz; offline eine JSON-Fehlermeldung statt eines
  // Netzwerkfehlers.
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(event.request).catch(() =>
        new Response(JSON.stringify({ success: false, error: 'Offline' }),
          { status: 503, headers: { 'Content-Type': 'application/json' } })
      )
    );
    return;
  }

  // Icons und Manifest: Cache-First
  if (url.pathname.startsWith('/static/') && url.pathname !== '/static/sw.js') {
    event.respondWith(
      caches.match(event.request).then(cached =>
        cached || fetch(event.request).then(res => {
          if (res.ok) {
            const clone = res.clone();
            caches.open(CACHE_STATIC).then(c => c.put(event.request, clone));
          }
          return res;
        })
      )
    );
    return;
  }

  // Seitenaufrufe: immer Netz, ohne Verbindung die Offline-Seite.
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => caches.match('/offline'))
    );
  }
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
      // c.url ist eine vollstaendige Adresse - der fruehere Vergleich mit '/'
      // traf nie, jeder Klick oeffnete ein weiteres Fenster.
      const ziel = new URL(event.notification.data?.url || '/', self.location.origin);
      for (const c of list) {
        if (new URL(c.url).origin === self.location.origin && 'focus' in c) {
          return c.focus().then(w => (w && 'navigate' in w) ? w.navigate(ziel.href) : w);
        }
      }
      if (clients.openWindow) return clients.openWindow(ziel.href);
    })
  );
});
