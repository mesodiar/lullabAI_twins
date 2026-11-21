/* Service Worker for LullabAI Twins Mobile PWA (Two-Stage FCM Web Push) */

self.addEventListener("push", (event) => {
  if (!event.data) return;

  let payload = {};
  try {
    payload = event.data.json();
  } catch {
    payload = { data: { title: "LullabAI Alert", body: event.data.text() } };
  }

  const data = payload.data || payload.notification || {};
  const eventId = data.event_id || "lullabai_default_tag";
  const stage = data.stage || "FAST_PATH_EDGE";
  const isCritical = data.safety_alert === "True" || data.urgency === "CRITICAL";

  const title =
    data.title ||
    (stage === "FAST_PATH_EDGE"
      ? "Possible crying or movement detected. Reviewing event…"
      : "LullabAI event review");

  const options = {
    body: data.body || "Tap to open the live view and event details",
    tag: eventId, // Using the same event_id tag replaces Stage-1 alert with Stage-2 AI insight!
    renotify: stage === "FAST_PATH_EDGE" || isCritical,
    requireInteraction: isCritical,
    vibrate: isCritical ? [300, 100, 300, 100, 400] : [200, 100, 200],
    data: {
      event_id: eventId,
      baby_id: data.baby_id || "baby_1",
      url: "/",
    },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ("focus" in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
    })
  );
});

