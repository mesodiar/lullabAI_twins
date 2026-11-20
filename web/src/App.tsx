import React, { useEffect, useState } from "react";
import { EventFeed } from "./components/EventFeed";
import { LivePlayer } from "./components/LivePlayer";
import { SafetyBanner } from "./components/SafetyBanner";
import { SleepSummary } from "./components/SleepSummary";
import {
  INITIAL_BABIES,
  INITIAL_DEMO_EVENTS,
  registerWebPushNotifications,
  triggerLocalBrowserNotification,
} from "./lib/firebase";
import { BabyProfile, LullabAIEvent, TriggerType } from "./lib/types";

const GO2RTC_URL = import.meta.env.VITE_GO2RTC_URL || "http://localhost:1984";
const EDGE_BASE_URL =
  import.meta.env.VITE_EDGE_BASE_URL || "http://localhost:8095";
const MOCK_CAMERA_URL =
  import.meta.env.VITE_MOCK_CAMERA_URL || "http://localhost:8090";
const EDGE_STATUS_URL =
  import.meta.env.VITE_EDGE_STATUS_URL || `${EDGE_BASE_URL}/status`;

export const App: React.FC = () => {
  const [selectedBabyId, setSelectedBabyId] = useState<"baby_1" | "baby_2">("baby_1");
  const [babies, setBabies] = useState<Record<string, BabyProfile>>(INITIAL_BABIES);
  const [events, setEvents] = useState<LullabAIEvent[]>(INITIAL_DEMO_EVENTS);
  const [pushStatus, setPushStatus] = useState<string>("ยังไม่ได้เปิด Web Push");

  const activeBaby = babies[selectedBabyId];

  // Poll local Edge Node status if running on localhost
  useEffect(() => {
    const controller = new AbortController();
    let polling = false;
    const pollEdgeStatus = async () => {
      if (polling) return;
      polling = true;
      try {
        const resp = await fetch(EDGE_STATUS_URL, { signal: controller.signal });
        if (!resp.ok) return;
        const data = await resp.json();
        const b1 = data?.babies?.baby_1;
        if (b1 && b1.edge_metrics) {
          const metrics = b1.edge_metrics;
          const subjType = metrics.subject_type || (metrics.band_detected ? "BABY" : "NONE");
          const motionVal = metrics.motion_score ?? 0;

          const liveTitle =
            subjType === "ADULT"
              ? "🧑 ตรวจพบผู้ใหญ่ในหน้าจอ (พักการแจ้งเตือน)"
              : subjType === "PET"
              ? "🐶 ตรวจพบน้องหมาในหน้าจอ (กำลังเฝ้าระวัง)"
              : subjType === "BABY"
              ? "👶 ตรวจพบเด็กทารกในเตียง (กำลังเฝ้าระวัง)"
              : "🛏️ ไม่พบเด็กหรือน้องหมาในหน้าจอ (Empty View)";

          const liveSummary =
            subjType === "ADULT"
              ? "AI ตรวจพบสัดส่วนร่างกายและใบหน้าของผู้ใหญ่ ไม่ใช่เด็กหรือน้องหมา"
              : subjType === "PET"
              ? "AI ตรวจพบน้องหมาในกล้อง และกำลังเฝ้าระวังการเคลื่อนไหวและเสียงร้อง"
              : subjType === "BABY"
              ? "AI ตรวจพบเด็กทารกในกรอบเตียงและกำลังเฝ้าระวังการเคลื่อนไหวและเสียงร้อง"
              : "กล้องกำลังทำงานปกติ ขณะนี้ไม่พบเด็กหรือน้องหมาในกรอบภาพ";

          const isMonitored = subjType === "BABY" || subjType === "PET";

          setBabies((prev) => ({
            ...prev,
            baby_1: {
              ...prev.baby_1,
              band_detected: isMonitored,
              band_bbox_pct: subjType !== "NONE" ? (metrics.band_bbox_pct ?? null) : null,
              subject_type: subjType,
              current_sleep_state:
                isMonitored
                  ? motionVal > 4.5
                    ? "AWAKE_CALM"
                    : "ASLEEP_DEEP"
                  : "OUT_OF_CRIB",
              posture:
                isMonitored
                  ? "SUPINE"
                  : subjType === "ADULT"
                  ? "SITTING_OR_STANDING"
                  : "UNKNOWN",
              safety_status: "SAFE",
              last_ai_title_th: liveTitle,
              last_ai_summary_th: liveSummary,
              motion_score: motionVal,
              yamnet_cry_prob: metrics.yamnet_cry_prob ?? 0,
              breathing_rpm: null,
              edge_online: true,
              last_heartbeat_at: b1.updated_at,
            },
          }));
        }
        if (Array.isArray(data?.recent_events) && data.recent_events.length > 0) {
          setEvents((prev) => {
            const existingIds = new Set(prev.map((e) => e.event_id));
            const newEvents = (data.recent_events as LullabAIEvent[]).filter(
              (e) => e && e.event_id && !existingIds.has(e.event_id)
            );
            return newEvents.length > 0 ? [...newEvents, ...prev].slice(0, 25) : prev;
          });
        }
      } catch {
        // Ignore when local Edge container isn't running
      } finally {
        polling = false;
      }
    };
    void pollEdgeStatus();
    const interval = window.setInterval(pollEdgeStatus, 250);
    return () => {
      window.clearInterval(interval);
      controller.abort();
    };
  }, []);

  const handleEnablePush = async () => {
    const res = await registerWebPushNotifications();
    setPushStatus(res.message);
  };

  const toggleSubjectMode = () => {
    setBabies((prev) => {
      const current = prev[selectedBabyId];
      const nextMode = current.subject_mode === "PET_TEST" ? "BABY" : "PET_TEST";
      return {
        ...prev,
        [selectedBabyId]: {
          ...current,
          subject_mode: nextMode,
        },
      };
    });
  };

  const handleSimulateTrigger = (trigger: TriggerType) => {
    const eventId = `evt_${Date.now()}`;
    const isRollover = trigger === "BAND_ROLLOVER";
    const isCry = trigger === "CRY";
    const isMotion = trigger === "MOTION";

    const stage1Event: LullabAIEvent = {
      event_id: eventId,
      baby_id: selectedBabyId,
      camera_id: activeBaby.camera_id,
      subject_mode: activeBaby.subject_mode,
      trigger_type: trigger,
      status: "PENDING_AI",
      created_at: new Date().toISOString(),
      analyzed_at: null,
      edge_metrics: {
        yamnet_cry_prob: isCry ? 0.89 : 0.04,
        yamnet_top_label: isCry
          ? activeBaby.subject_mode === "PET_TEST"
            ? "Whimper (dog) / Baby cry"
            : "Baby cry, infant cry"
          : "Silence",
        motion_score: isMotion || isCry ? 14.6 : 0.9,
        band_detected: !isRollover,
        band_confidence: isRollover ? 0.0 : 0.96,
        breathing_rpm: null,
        band_bbox: isRollover ? null : [337, 409, 219, 207],
        subject_type: isRollover ? "NONE" : "BABY",
      },
      ai_analysis: null,
    };

    // Stage 1 (Immediate Fast-Path Alert <1s)
    setEvents((prev) => [stage1Event, ...prev]);
    setBabies((prev) => ({
      ...prev,
      [selectedBabyId]: {
        ...prev[selectedBabyId],
        band_detected: !isRollover,
        subject_type: isRollover ? "NONE" : "BABY",
        breathing_rpm: null,
        safety_status: isRollover ? "CRITICAL" : isCry ? "WARNING" : "SAFE",
        last_ai_title_th:
          trigger === "PERIODIC_CHECK"
            ? "🔄 กำลังส่ง Snapshot รอบ 5 นาทีให้ Gemini ตรวจสอบ..."
            : `⚡ Fast-Path Alert (${trigger}) — กำลังรอผลจาก Gemini AI...`,
      },
    }));

    if (trigger !== "PERIODIC_CHECK") {
      triggerLocalBrowserNotification(
        `🚨 [Fast-Path] ตรวจพบ ${trigger}`,
        "แจ้งเตือนด่วนจาก Edge Node — กำลังรอผลวิเคราะห์ละเอียดจาก Gemini AI...",
        eventId
      );
    }

    // Stage 2 (Simulated 3s Cloud Run + Vertex AI Gemini deep analysis completion)
    window.setTimeout(() => {
      const aiResult = isRollover
        ? {
            is_false_alarm: false,
            safety_alert: true,
            posture: "PRONE" as const,
            face_covered: false,
            band_visible_and_secure: false,
            sleep_state: "ASLEEP_LIGHT" as const,
            urgency: "CRITICAL" as const,
            title_th: "🚨 แจ้งเตือนด่วน: ทารกพลิกคว่ำหน้า หรือใบหน้าถูกปิดบัง!",
            reason_hypothesis_th:
              "Gemini AI ตรวจพบท่านอนคว่ำหน้า (PRONE) หรือมีสิ่งกีดขวางบริเวณใบหน้า/ทางเดินหายใจ",
            recommended_action_th: "กรุณาเปิดดูกล้องสดและจัดท่านอนให้หงายทันที",
          }
        : isCry
        ? {
            is_false_alarm: false,
            safety_alert: false,
            posture: "SUPINE" as const,
            face_covered: false,
            band_visible_and_secure: true,
            sleep_state: "CRYING" as const,
            urgency: "HIGH" as const,
            title_th:
              activeBaby.subject_mode === "PET_TEST"
                ? "🐶 ตรวจพบเสียงร้อง/คราง (ปลอดภัยดี)"
                : "😢 น้องตื่นและส่งเสียงร้อง (ท่านอนหงายปลอดภัย)",
            reason_hypothesis_th:
              "ยืนยันเสียงร้องจริง นอนหงายปลอดภัย ไม่มีผ้าคลุมใบหน้า",
            recommended_action_th: "เข้าไปปลอบหรือเช็คความต้องการเบื้องต้น",
          }
        : isMotion
        ? {
            is_false_alarm: false,
            safety_alert: false,
            posture: "SUPINE" as const,
            face_covered: false,
            band_visible_and_secure: true,
            sleep_state: "AWAKE_CALM" as const,
            urgency: "MEDIUM" as const,
            title_th: "👀 ขยับตัวในเตียง (ปลอดภัยดี)",
            reason_hypothesis_th:
              "ขยับตัวปกติ นอนหงายปลอดภัย ไม่พบสิ่งกีดขวางทางเดินหายใจ",
            recommended_action_th: "ยังไม่ต้องดำเนินการใดๆ",
          }
        : {
            is_false_alarm: false,
            safety_alert: false,
            posture: "SUPINE" as const,
            face_covered: false,
            band_visible_and_secure: true,
            sleep_state: "ASLEEP_DEEP" as const,
            urgency: "LOW" as const,
            title_th: "😴 หลับสบาย ท่านอนหงายปลอดภัย (Periodic 5m Check)",
            reason_hypothesis_th:
              "ผลตรวจตามรอบ 5 นาที: ตรวจพบทารกนอนหงายปลอดภัย ไม่มีผ้าคลุมหน้า",
            recommended_action_th: "ปลอดภัยดี ไม่ต้องดำเนินการใดๆ",
          };

      setEvents((prev) =>
        prev.map((item) =>
          item.event_id === eventId
            ? {
                ...item,
                status: "ANALYZED",
                analyzed_at: new Date().toISOString(),
                ai_analysis: aiResult,
              }
            : item
        )
      );

      setBabies((prev) => ({
        ...prev,
        [selectedBabyId]: {
          ...prev[selectedBabyId],
          current_sleep_state: aiResult.sleep_state,
          safety_status: aiResult.safety_alert
            ? "CRITICAL"
            : aiResult.urgency === "HIGH"
            ? "WARNING"
            : "SAFE",
          posture: aiResult.posture,
          last_ai_title_th: aiResult.title_th,
          last_ai_summary_th: aiResult.reason_hypothesis_th,
        },
      }));

      if (trigger !== "PERIODIC_CHECK" || aiResult.safety_alert) {
        triggerLocalBrowserNotification(
          aiResult.title_th,
          `${aiResult.reason_hypothesis_th} • ${aiResult.recommended_action_th}`,
          eventId
        );
      }
    }, 2800);
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 pb-12">
      {/* Top App Navigation Bar */}
      <header className="sticky top-0 z-30 bg-slate-950/90 backdrop-blur border-b border-slate-800 px-4 py-3">
        <div className="max-w-5xl mx-auto flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="h-9 w-9 rounded-xl bg-gradient-to-br from-indigo-500 to-emerald-500 flex items-center justify-center font-bold text-slate-950 shadow">
              L
            </div>
            <div>
              <h1 className="text-base font-bold leading-tight">
                LullabAI Twins
              </h1>
              <p className="text-[11px] text-slate-400">
                Markerless Baby Vision & Dual-System AI Monitor (No Band Needed)
              </p>
            </div>
          </div>

          {/* Twins Switcher + Mode Toggle + Web Push Button */}
          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex rounded-xl bg-slate-900 p-1 border border-slate-800">
              <button
                onClick={() => setSelectedBabyId("baby_1")}
                className={`px-3 py-1 rounded-lg text-xs font-medium transition ${
                  selectedBabyId === "baby_1"
                    ? "bg-indigo-600 text-white"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                👶 เตียง 1 (Active)
              </button>
              <button
                onClick={() => setSelectedBabyId("baby_2")}
                className={`px-3 py-1 rounded-lg text-xs font-medium transition ${
                  selectedBabyId === "baby_2"
                    ? "bg-indigo-600 text-white"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                👶 เตียง 2 (Twin B)
              </button>
            </div>

            <button
              onClick={toggleSubjectMode}
              className="text-xs px-3 py-1.5 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-200 transition"
            >
              สลับโหมด: {activeBaby.subject_mode === "PET_TEST" ? "🐶 ทดสอบกับหมา" : "👶 ทารกจริง"}
            </button>

            <button
              onClick={handleEnablePush}
              className="text-xs px-3 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-medium transition"
              title={pushStatus}
            >
              🔔 เปิดรับแจ้งเตือน (FCM)
            </button>
          </div>
        </div>
      </header>

      {/* Main Content Container */}
      <main className="max-w-5xl mx-auto px-4 pt-4 space-y-4">
        <SafetyBanner baby={activeBaby} />

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
          <div className="lg:col-span-7 space-y-4">
            <LivePlayer
              baby={activeBaby}
              go2rtcUrl={GO2RTC_URL}
              edgeBaseUrl={EDGE_BASE_URL}
              mockCameraUrl={MOCK_CAMERA_URL}
              onSimulateTrigger={handleSimulateTrigger}
            />
            <SleepSummary baby={activeBaby} />
          </div>

          <div className="lg:col-span-5">
            <EventFeed
              events={events.filter((e) => e.baby_id === selectedBabyId)}
            />
          </div>
        </div>
      </main>
    </div>
  );
};

