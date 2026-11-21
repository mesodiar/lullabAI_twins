self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const dashboard = windows.find((client) => new URL(client.url).origin === self.location.origin);
    if (dashboard) await dashboard.focus();
  })());
});
