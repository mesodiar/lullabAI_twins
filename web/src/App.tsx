import { Bell, MoonStar, Settings } from "lucide-react";
import { useEffect, useState } from "react";
import "./dashboard.css";
import { EventFeed } from "./components/EventFeed";
import { LivePlayer, type SimulatedTrigger } from "./components/LivePlayer";
import { SafetyBanner } from "./components/SafetyBanner";
import { isImportantAlert, notifyLocal, requestLocalNotifications } from "./localAlerts";
import type { BabyProfile, EdgeMetrics, LullabAIEvent } from "./lib/types";

const LOCAL_HOST = window.location.hostname;
const GO2RTC_URL = import.meta.env.VITE_GO2RTC_URL || `http://${LOCAL_HOST}:1984`;
const EDGE_BASE_URL =
  import.meta.env.VITE_EDGE_BASE_URL || `http://${LOCAL_HOST}:8095`;
const MOCK_CAMERA_URL =
  import.meta.env.VITE_MOCK_CAMERA_URL || `http://${LOCAL_HOST}:8090`;
const EDGE_STATUS_URL =
  import.meta.env.VITE_EDGE_STATUS_URL || `${EDGE_BASE_URL}/status`;

const BEDS = ["baby_1", "baby_2"] as const;
const BED_LABELS = { baby_1: "เตียง 1", baby_2: "เตียง 2" };
const STALE_AFTER_MS = 5_000;

type BedId = typeof BEDS[number];
type DashboardProfile = BabyProfile & { cameraConfigured?: boolean; petCoverSuspected?: boolean; simulated?: string };

const initialBabies: Record<BedId, DashboardProfile> = {
  baby_1: initialProfile("baby_1", "cam_crib_1"),
  baby_2: initialProfile("baby_2", "cam_crib_2"),
};

function initialProfile(bed: BedId, cameraId: string): DashboardProfile {
  return {
    baby_id: bed,
    name: BED_LABELS[bed],
    camera_id: cameraId,
    stream_name: `${bed}_main`,
    subject_mode: "BABY",
    current_sleep_state: "OUT_OF_CRIB",
    safety_status: "SAFE",
    posture: "UNKNOWN",
    face_covered: false,
    band_detected: false,
    breathing_rpm: null,
    edge_online: false,
  };
}

interface EdgeStatus {
  babies?: Partial<Record<BedId, {
    updated_at?: string;
    edge_metrics?: EdgeMetrics & { band_bbox_pct?: BabyProfile["band_bbox_pct"]; face_cover_suspected?: boolean };
  }>>;
  recent_events?: LullabAIEvent[];
}

export function App() {
  const [selectedBabyId, setSelectedBabyId] = useState<BedId>("baby_1");
  const [babies, setBabies] = useState<Record<string, DashboardProfile>>(initialBabies);
  const [events, setEvents] = useState<LullabAIEvent[]>([]);
  const [notificationStatus, setNotificationStatus] = useState(() =>
    "Notification" in window && Notification.permission === "granted"
      ? "เปิดแจ้งเตือนขณะเปิดหน้าเว็บแล้ว"
      : "แจ้งเตือนขณะเปิดหน้าเว็บ"
  );
  const [useMock, setUseMock] = useState(false);
  const [showDetection, setShowDetection] = useState(true);
  const [connection, setConnection] = useState("");

  const activeBaby = babies[selectedBabyId];
  const cameraConfigured = selectedBabyId === "baby_1" || activeBaby.cameraConfigured === true;
  const monitoring = Boolean(activeBaby.simulated || (!useMock && activeBaby.edge_online && activeBaby.last_heartbeat_at));

  useEffect(() => {
    const controller = new AbortController();
    let polling = false;
    let seenEventIds: Set<string> | null = null;

    const updateBabies = (snapshot?: EdgeStatus["babies"]) => {
      const now = Date.now();
      setBabies((previous) => {
        const updates: Record<string, DashboardProfile> = {};
        for (const bed of BEDS) {
          const current = previous[bed];
          const telemetry = snapshot?.[bed];
          const heartbeat = telemetry?.updated_at ?? current.last_heartbeat_at;
          const online = Boolean(heartbeat && now - Date.parse(heartbeat) < STALE_AFTER_MS);
          const configured = telemetry !== undefined || current.cameraConfigured;
          const metrics = telemetry?.edge_metrics;

          if (metrics && telemetry?.updated_at !== current.last_heartbeat_at) {
            const subject = metrics.subject_type ?? (metrics.band_detected ? "BABY" : "NONE");
            const monitored = subject === "BABY" || subject === "PET";
            const warning = metrics.face_cover_suspected === true;
            updates[bed] = {
              ...current,
              cameraConfigured: configured,
              band_detected: monitored,
              band_bbox_pct: metrics.band_bbox_pct ?? null,
              subject_type: subject,
              petCoverSuspected: warning,
              simulated: undefined,
              current_sleep_state: monitored
                ? metrics.motion_score > 4.5 ? "AWAKE_CALM" : "ASLEEP_DEEP"
                : "OUT_OF_CRIB",
              posture: subject === "ADULT" ? "SITTING_OR_STANDING" : "UNKNOWN",
              safety_status: warning ? "WARNING" : "SAFE",
              motion_score: metrics.motion_score,
              yamnet_cry_prob: metrics.yamnet_cry_prob,
              edge_online: online,
              last_heartbeat_at: heartbeat,
            };
          } else if (online !== current.edge_online || configured !== current.cameraConfigured) {
            updates[bed] = { ...current, edge_online: online, cameraConfigured: configured };
          }
        }
        return Object.keys(updates).length ? { ...previous, ...updates } : previous;
      });
    };

    const pollEdgeStatus = async () => {
      if (polling) return;
      polling = true;
      try {
        const response = await fetch(EDGE_STATUS_URL, { signal: controller.signal });
        const data: EdgeStatus | undefined = response.ok ? await response.json() : undefined;
        if (controller.signal.aborted) return;
        updateBabies(data?.babies);
        if (Array.isArray(data?.recent_events)) {
          const received = data.recent_events.filter((event) => event?.event_id && event.trigger_type !== "PERIODIC_CHECK");
          const nextEventIds = new Set(received.map((event) => event.event_id));
          if (seenEventIds) {
            for (const event of received) {
              if (!seenEventIds.has(event.event_id) && isImportantAlert(event)) {
                void notifyLocal(event, BED_LABELS[event.baby_id as BedId] ?? event.baby_id);
              }
            }
          }
          seenEventIds = nextEventIds;
          if (!received.length) return;
          setEvents((previous) => {
            const byId = new Map(previous.map((event) => [event.event_id, event]));
            for (const event of received) {
              const existing = byId.get(event.event_id);
              if (existing?.analyzed_at && (!event.analyzed_at || Date.parse(existing.analyzed_at) > Date.parse(event.analyzed_at))) continue;
              byId.set(event.event_id, event);
            }
            return [...byId.values()]
              .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
              .slice(0, 30);
          });
        }
      } catch {
        if (!controller.signal.aborted) updateBabies();
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

  const handleEnableNotifications = async () => {
    try {
      setNotificationStatus(await requestLocalNotifications());
    } catch {
      setNotificationStatus("เปิดการแจ้งเตือนไม่สำเร็จ");
    }
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

  const handleSimulateTrigger = (trigger: SimulatedTrigger) => {
    const eventId = `sim_${Date.now()}`;
    const isRollover = trigger === "BAND_ROLLOVER";
    const isCry = trigger === "CRY";

    const event: LullabAIEvent = {
      event_id: eventId,
      baby_id: selectedBabyId,
      camera_id: activeBaby.camera_id,
      subject_mode: activeBaby.subject_mode,
      trigger_type: trigger,
      status: "EDGE_DETECTED",
      simulated: true,
      created_at: new Date().toISOString(),
      analyzed_at: null,
      edge_metrics: {
        yamnet_cry_prob: isCry ? 0.89 : 0.04,
        yamnet_top_label: isCry
          ? activeBaby.subject_mode === "PET_TEST"
            ? "Whimper (dog) / Baby cry"
            : "Baby cry, infant cry"
          : "Silence",
        motion_score: isRollover ? 0.9 : 14.6,
        band_detected: !isRollover,
        band_confidence: isRollover ? 0.0 : 0.96,
        breathing_rpm: null,
        band_bbox: isRollover ? null : [337, 409, 219, 207],
        subject_type: isRollover ? "NONE" : "BABY",
      },
      ai_analysis: null,
    };

    setEvents((prev) => [event, ...prev]);
    setBabies((prev) => ({
      ...prev,
      [selectedBabyId]: {
        ...prev[selectedBabyId],
        band_detected: !isRollover,
        band_bbox_pct: null,
        subject_type: isRollover ? "NONE" : "BABY",
        breathing_rpm: null,
        petCoverSuspected: false,
        simulated: eventId,
        posture: "UNKNOWN",
        current_sleep_state: isCry ? "CRYING" : "AWAKE_CALM",
        safety_status: isRollover ? "CRITICAL" : isCry ? "WARNING" : "SAFE",
      },
    }));

    void notifyLocal(event, BED_LABELS[selectedBabyId]);
  };

  const simulate = async (mode: "NORMAL" | "CRY" | "ROLLOVER" | "MOTION", trigger?: SimulatedTrigger) => {
    try {
      await fetch(`${MOCK_CAMERA_URL}/simulate/${mode}`, { method: "POST" });
    } catch {
      // The browser can still simulate an alert when the mock camera is unavailable.
    }
    if (trigger) handleSimulateTrigger(trigger);
  };

  const viewCamera = () => {
    document.getElementById("camera-title")?.focus();
  };

  return (
    <div className="dashboard-shell">
      <header className="app-bar">
        <div className="app-bar-inner">
          <h1 className="brand">
            <span className="brand-icon"><MoonStar aria-hidden="true" /></span>
            LullabAI Twins
          </h1>
          <div className="beds" role="group" aria-label="เลือกเตียง">
            {BEDS.map((bed) => (
              <button type="button" key={bed} aria-pressed={selectedBabyId === bed} onClick={() => setSelectedBabyId(bed)}>
                {BED_LABELS[bed]}
              </button>
            ))}
          </div>
          <div className="quick-actions">
            <details className="app-menu" name="dashboard-menu">
              <summary className="icon-button" aria-label="การแจ้งเตือน"><Bell aria-hidden="true" /></summary>
              <section className="menu-panel" aria-label="การแจ้งเตือน">
                <h2>การแจ้งเตือน</h2>
                <p className="section-caption" role="status">{notificationStatus}</p>
                <button className="menu-button" type="button" onClick={handleEnableNotifications}>เปิดรับแจ้งเตือน</button>
              </section>
            </details>
            <details className="app-menu" name="dashboard-menu">
              <summary className="icon-button" aria-label="ตั้งค่า"><Settings aria-hidden="true" /></summary>
              <section className="menu-panel" aria-label="ตั้งค่ากล้อง">
                <h2>ตั้งค่ากล้อง</h2>
                <label><input type="checkbox" checked={useMock} onChange={(event) => setUseMock(event.target.checked)} /> ใช้ภาพจำลอง</label>
                <label><input type="checkbox" checked={showDetection} onChange={(event) => setShowDetection(event.target.checked)} /> แสดงกรอบตรวจจับ</label>
                <details className="menu-detail">
                  <summary>ข้อมูลการเชื่อมต่อ</summary>
                  <dl>
                    <div><dt>กล้อง</dt><dd>{activeBaby.camera_id}</dd></div>
                    <div><dt>สตรีม</dt><dd>{connection}</dd></div>
                    <div><dt>การเคลื่อนไหว</dt><dd>{monitoring ? activeBaby.motion_score?.toFixed(1) ?? "—" : "—"}</dd></div>
                    <div><dt>เก็บคลิป</dt><dd>บนเครื่อง Edge</dd></div>
                  </dl>
                </details>
                <details className="menu-detail">
                  <summary>ทดสอบเหตุการณ์</summary>
                  <label><input type="checkbox" checked={activeBaby.subject_mode === "PET_TEST"} onChange={toggleSubjectMode} /> จำลองกับน้องหมา</label>
                  <div className="simulation-actions">
                    <button className="menu-button" type="button" onClick={() => simulate("NORMAL")}>รีเซ็ตภาพจำลอง</button>
                    <button className="menu-button" type="button" onClick={() => simulate("CRY", "CRY")}>เสียงร้อง</button>
                    <button className="menu-button" type="button" onClick={() => simulate("ROLLOVER", "BAND_ROLLOVER")}>พลิกคว่ำ / หน้าถูกบัง</button>
                    <button className="menu-button" type="button" onClick={() => simulate("MOTION", "MOTION")}>ดิ้นแรง</button>
                  </div>
                </details>
              </section>
            </details>
          </div>
        </div>
      </header>

      <main className="dashboard">
        <SafetyBanner
          baby={activeBaby}
          bedLabel={BED_LABELS[selectedBabyId]}
          cameraConfigured={cameraConfigured}
          monitoring={monitoring}
          simulated={Boolean(activeBaby.simulated)}
          useMock={useMock}
          petCoverSuspected={activeBaby.petCoverSuspected}
          onViewCamera={viewCamera}
        />
        <LivePlayer
          key={`camera-${selectedBabyId}`}
          baby={activeBaby}
          cameraConfigured={cameraConfigured}
          useMock={useMock}
          showDetection={showDetection}
          go2rtcUrl={GO2RTC_URL}
          edgeBaseUrl={EDGE_BASE_URL}
          mockCameraUrl={MOCK_CAMERA_URL}
          onConnectionChange={setConnection}
        />
        <EventFeed key={`history-${selectedBabyId}`} edgeBaseUrl={EDGE_BASE_URL} events={events.filter((event) => event.baby_id === selectedBabyId)} />
      </main>
    </div>
  );
}
