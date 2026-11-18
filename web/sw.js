/* Oversyte service worker: receives theft alerts as push notifications. v2 */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil((async () => {
  // never keep stale copies of the dashboard around
  for (const k of await caches.keys()) await caches.delete(k);
  await self.clients.claim();
})()));

// Network-only; present so browsers treat the dashboard as an installable app.
self.addEventListener("fetch", () => {});

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) { data = { title: "Oversyte", body: event.data?.text() }; }
  const title = data.title || "Possible theft";
  const opts = {
    body: data.body || "Open Oversyte to watch the clip.",
    icon: "/icon-192.png",
    badge: "/badge-72.png",
    tag: data.tag || "oversyte",
    renotify: true,
    requireInteraction: true,
    vibrate: [300, 120, 300, 120, 300],
    timestamp: data.ts ? data.ts * 1000 : Date.now(),
    data: { url: data.url || "/" },
  };
  if (data.image) opts.image = data.image;
  event.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if (new URL(w.url).origin === self.location.origin) {
        await w.focus();
        if ("navigate" in w) { try { await w.navigate(url); return; } catch (_) {} }
        w.postMessage({ type: "open", url });
        return;
      }
    }
    await self.clients.openWindow(url);
  })());
});
