/* AgentHive Service Worker — Web Push notifications */

function sendAck(nid, shown) {
  if (!nid) return Promise.resolve();
  return fetch("/api/push/ack", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      nid,
      shown,
      ts: Date.now(),
      ua: (self.navigator && self.navigator.userAgent) || "",
    }),
    keepalive: true,
  }).catch(() => {});
}

self.addEventListener("push", (event) => {
  let data = { title: "AgentHive", body: "An agent finished.", url: "/" };
  try {
    data = Object.assign(data, event.data.json());
  } catch {
    // use defaults
  }

  const shownPromise = self.registration
    .showNotification(data.title, {
      body: data.body,
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      vibrate: [100, 50, 100],
      data: { url: data.url, nid: data.nid },
    })
    .then(() => true, () => false);

  event.waitUntil(
    shownPromise.then((ok) => sendAck(data.nid, ok)),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const url = event.notification.data?.url || "/";

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((windowClients) => {
      // Focus existing tab if one is open
      for (const client of windowClients) {
        if (client.url.includes(self.location.origin)) {
          client.focus();
          // Let the app decide how to navigate (split-screen aware)
          client.postMessage({ type: "notification-navigate", url });
          return;
        }
      }
      // Otherwise open a new tab
      return clients.openWindow(url);
    }),
  );
});
