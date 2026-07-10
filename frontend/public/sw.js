// T230-ALERTING-PUSH-NOTIFICATIONS: minimal service worker for Web Push delivery.
// Registered from settings.tsx when the user enables push notifications. Handles two
// events: an incoming push message (show a notification) and a click on that notification
// (focus/open the relevant page). Does not implement offline caching / PWA install beyond
// what T230-UX-PWA's manifest.json already provides — push delivery only.

self.addEventListener('push', (event) => {
  let data = { title: 'StockAI', body: 'You have a new alert', url: '/' };
  try {
    if (event.data) data = { ...data, ...event.data.json() };
  } catch (e) {
    // Non-JSON payload — fall back to the default above rather than throwing.
  }

  const options = {
    body: data.body,
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    tag: data.tag || undefined, // same tag = browser replaces the previous notification instead of stacking
    data: { url: data.url || '/' },
  };

  event.waitUntil(self.registration.showNotification(data.title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || '/';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      // Focus an already-open tab on the same origin instead of always opening a new one.
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.navigate(targetUrl);
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    })
  );
});
