self.addEventListener("install", (event) => event.waitUntil(self.skipWaiting()));
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let notice;
  try {
    notice = event.data?.json();
  } catch {
    // Display a generic alert when the message cannot be parsed.
  }
  event.waitUntil(self.registration.showNotification(notice?.title || "LullabAI alert", {
    body: notice?.body || "Open the dashboard to check recent events.",
    icon: "/icon-192.png",
    tag: notice?.id,
    data: { bed: notice?.bed },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const target = new URL("/", self.location.origin);
    const bed = event.notification.data?.bed;
    if (bed === "baby_1" || bed === "baby_2") target.searchParams.set("bed", bed);
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const dashboard = windows.find((client) => {
      const url = new URL(client.url);
      return url.origin === target.origin && url.pathname === target.pathname;
    });
    if (dashboard) {
      const client = target.search && dashboard.url !== target.href
        ? await dashboard.navigate(target.href)
        : dashboard;
      await (client ?? dashboard).focus();
    } else {
      await self.clients.openWindow(target.href);
    }
  })());
});
