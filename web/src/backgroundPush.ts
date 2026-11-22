export interface BackgroundPushSetup {
  publicKey: string;
  registration: ServiceWorkerRegistration;
  subscription: PushSubscription | null;
  registered: boolean;
}

async function updateSubscription(path: string, method: string, subscription: PushSubscription, setupCode?: string) {
  const response = await fetch(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(setupCode ? { "X-Setup-Code": setupCode } : {}),
    },
    body: JSON.stringify(subscription.toJSON()),
  });
  if (response.status === 403) throw new Error("Incorrect setup code.");
  if (!response.ok) throw new Error("The Edge server could not save this notification setting.");
  return response.json() as Promise<{ registered?: boolean }>;
}

export async function prepareBackgroundPush(): Promise<BackgroundPushSetup | "unsupported" | null> {
  const response = await fetch("/push/config", { cache: "no-store" });
  if (!response.ok) return null;
  const { publicKey } = await response.json();
  if (typeof publicKey !== "string" || !publicKey) return null;
  if (!window.isSecureContext || !("Notification" in window) || !("serviceWorker" in navigator) || !("PushManager" in window)) {
    return "unsupported";
  }

  await navigator.serviceWorker.register("/local-notifications-sw.js");
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.getSubscription();
  const registered = subscription
    ? (await updateSubscription("/push/subscription-status", "POST", subscription)).registered === true
    : false;
  return { publicKey, registration, subscription, registered };
}

export async function enableBackgroundPush(setup: BackgroundPushSetup, setupCode: string): Promise<BackgroundPushSetup> {
  const key = setup.publicKey.replace(/-/g, "+").replace(/_/g, "/");
  const applicationServerKey = Uint8Array.from(atob(key.padEnd(Math.ceil(key.length / 4) * 4, "=")), (byte) => byte.charCodeAt(0));
  const subscription = setup.subscription ?? await setup.registration.pushManager.subscribe({
    applicationServerKey,
    userVisibleOnly: true,
  });
  await updateSubscription("/push/subscriptions", "POST", subscription, setupCode);
  return { ...setup, subscription, registered: true };
}

export async function disableBackgroundPush(setup: BackgroundPushSetup): Promise<BackgroundPushSetup> {
  await setup.subscription?.unsubscribe();
  if (setup.subscription) {
    try {
      await updateSubscription("/push/subscriptions", "DELETE", setup.subscription);
    } catch {
      // The push service will reject the old endpoint after unsubscribe.
    }
  }
  return { ...setup, subscription: null, registered: false };
}
