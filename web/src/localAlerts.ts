import type { LullabAIEvent, TriggerType } from "./lib/types";

const TITLES: Record<TriggerType, string> = {
  CRY: "Possible crying detected",
  MOTION: "Strong movement detected",
  BREATHING_ANOMALY: "Possible irregular breathing",
  BAND_ROLLOVER: "Possible rollover or covered face",
  PERIODIC_CHECK: "Periodic check",
};

export function isPetCoverAlert(event: LullabAIEvent) {
  return event.trigger_type === "BAND_ROLLOVER" && event.edge_metrics.face_cover_suspected === true;
}

export function alertTitle(event: LullabAIEvent) {
  if (event.trigger_type === "CRY" && event.edge_metrics.audio_kind === "DOG_VOCALIZATION") {
    return "Possible dog vocalization";
  }
  return isPetCoverAlert(event) ? "Possible blanket over dog's face" : TITLES[event.trigger_type];
}

export function isImportantAlert(event: LullabAIEvent) {
  return event.trigger_type !== "PERIODIC_CHECK" &&
    event.status !== "FALSE_ALARM_SUPPRESSED" &&
    !event.ai_analysis?.is_false_alarm;
}

export function shouldNotifyLocal(event: LullabAIEvent, cloudReviewsEnabled: boolean) {
  if (event.simulated) return true;
  if (!isImportantAlert(event)) return false;
  if (!cloudReviewsEnabled) return true;
  const ai = event.ai_analysis;
  if (event.cloud_review !== "REVIEWED" || !ai) return false;
  return ai.safety_alert || ai.urgency !== "LOW";
}

export async function requestLocalNotifications() {
  if (!window.isSecureContext || !("Notification" in window)) {
    return "Notifications require a supported browser and HTTPS or localhost.";
  }
  if (await Notification.requestPermission() !== "granted") {
    return "Notifications are blocked by your browser.";
  }
  if ("serviceWorker" in navigator) {
    try {
      await navigator.serviceWorker.register("/local-notifications-sw.js");
    } catch {
      // Desktop browsers can still display notifications directly.
    }
  }
  return "Notifications are on while this page is open.";
}

export async function notifyLocal(event: LullabAIEvent, bedLabel: string) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;

  const ai = event.ai_analysis;
  const title = event.simulated
    ? `Test · ${alertTitle(event)}`
    : ai?.title_th || alertTitle(event);
  const body = ai?.recommended_action_th ? `${bedLabel} · ${ai.recommended_action_th}` : bedLabel;
  const options = { body, tag: event.event_id };
  try {
    const registration = await navigator.serviceWorker?.getRegistration();
    if (registration?.active) {
      await registration.showNotification(title, options);
    } else {
      new Notification(title, options);
    }
  } catch {
    // The event still appears in the dashboard when system notifications are unavailable.
  }
}
