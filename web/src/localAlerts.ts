import type { LullabAIEvent, TriggerType } from "./lib/types";

const TITLES: Record<TriggerType, string> = {
  CRY: "ตรวจพบเสียงคล้ายเสียงร้อง",
  MOTION: "ขยับตัวแรง",
  BREATHING_ANOMALY: "ตรวจพบจังหวะหายใจผิดปกติ",
  BAND_ROLLOVER: "สงสัยว่าพลิกคว่ำหรือหน้าถูกบัง",
  PERIODIC_CHECK: "ตรวจตามรอบ",
};

export function isPetCoverAlert(event: LullabAIEvent) {
  return event.trigger_type === "BAND_ROLLOVER" && event.edge_metrics.face_cover_suspected === true;
}

export function alertTitle(event: LullabAIEvent) {
  return isPetCoverAlert(event) ? "สงสัยว่าผ้าปิดหน้าน้องหมา" : TITLES[event.trigger_type];
}

export function isImportantAlert(event: LullabAIEvent) {
  return event.trigger_type !== "PERIODIC_CHECK" &&
    event.status !== "FALSE_ALARM_SUPPRESSED" && !event.ai_analysis?.is_false_alarm;
}

export async function requestLocalNotifications() {
  if (!window.isSecureContext || !("Notification" in window)) {
    return "การแจ้งเตือนต้องใช้เบราว์เซอร์ที่รองรับและเปิดผ่าน HTTPS หรือ localhost";
  }
  if (await Notification.requestPermission() !== "granted") {
    return "เบราว์เซอร์ยังไม่ได้อนุญาตการแจ้งเตือน";
  }
  if ("serviceWorker" in navigator) {
    try {
      await navigator.serviceWorker.register("/local-notifications-sw.js");
    } catch {
      // Desktop browsers can still display notifications directly.
    }
  }
  return "เปิดแจ้งเตือนขณะเปิดหน้าเว็บแล้ว";
}

export async function notifyLocal(event: LullabAIEvent, bedLabel: string) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;

  const title = event.simulated ? `ทดสอบ · ${alertTitle(event)}` : alertTitle(event);
  const options = { body: bedLabel, tag: event.event_id };
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
