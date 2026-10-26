/* Do.iT service worker: lets alarms show system notifications (required on Android Chrome). */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
    for (const c of list) if ('focus' in c) return c.focus();
    return self.clients.openWindow('/app');
  }));
});

// Needed so browsers offer "Install app" (we do not cache anything).
self.addEventListener('fetch', () => {});
