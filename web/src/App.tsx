import { Bell, ChevronDown, MoonStar, Settings } from "lucide-react";
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
const BED_LABELS = { baby_1: "Bed 1", baby_2: "Bed 2" };
const STALE_AFTER_MS = 5_000;

type BedId = typeof BEDS[number];
type DashboardProfile = BabyProfile & { cameraConfigured?: boolean; petCoverSuspected?: boolean };

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
      ? "Notifications are on while this page is open."
      : "Get alerts while this page is open."
  );
  const [useMock, setUseMock] = useState(false);
  const [showDetection, setShowDetection] = useState(true);
  const [connection, setConnection] = useState("");
  const [simulation, setSimulation] = useState<{ bed: BedId; trigger: SimulatedTrigger } | null>(null);
  const [simulationFeedback, setSimulationFeedback] = useState<{ bed: BedId; pending: boolean; message: string } | null>(null);
  const [mockFeedVersion, setMockFeedVersion] = useState(0);

  const activeBaby = babies[selectedBabyId];
  const cameraConfigured = selectedBabyId === "baby_1" || activeBaby.cameraConfigured === true;
  const simulatedTrigger = simulation?.bed === selectedBabyId && (useMock || activeBaby.safety_status === "SAFE")
    ? simulation.trigger
    : undefined;
  const statusBaby: BabyProfile = simulatedTrigger ? {
    ...activeBaby,
    subject_type: simulatedTrigger === "BAND_ROLLOVER" ? "NONE" : "BABY",
    current_sleep_state: simulatedTrigger === "CRY" ? "CRYING" : "AWAKE_CALM",
    posture: "UNKNOWN",
    safety_status: simulatedTrigger === "BAND_ROLLOVER" ? "CRITICAL" : simulatedTrigger === "CRY" ? "WARNING" : "SAFE",
  } : activeBaby;
  const monitoring = Boolean(simulatedTrigger || (!useMock && activeBaby.edge_online && activeBaby.last_heartbeat_at));

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
      setNotificationStatus("Could not enable notifications.");
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
    setSimulation({ bed: selectedBabyId, trigger });
    void notifyLocal(event, BED_LABELS[selectedBabyId]);
  };

  const simulate = async (mode: "NORMAL" | "CRY" | "ROLLOVER" | "MOTION", trigger?: SimulatedTrigger) => {
    if (simulationFeedback?.pending) return;
    setSimulationFeedback({
      bed: selectedBabyId,
      pending: true,
      message: trigger ? "Starting simulation…" : "Resetting simulation…",
    });
    if (trigger) handleSimulateTrigger(trigger);
    else setSimulation(null);

    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 3000);
    let message: string;
    try {
      const response = await fetch(`${MOCK_CAMERA_URL}/simulate/${mode}`, { method: "POST", signal: controller.signal });
      if (!response.ok || (await response.json())?.mode !== mode) throw new Error("Mock camera did not confirm the mode");
      if (!trigger) setMockFeedVersion((version) => version + 1);
      message = trigger ? "Simulation started." : "Simulation cleared. Mock feed reset to normal.";
    } catch {
      message = trigger
        ? "Test event added. Could not update the mock feed."
        : "Simulation cleared. Could not reset the mock feed.";
    } finally {
      window.clearTimeout(timeout);
    }
    setSimulationFeedback({ bed: selectedBabyId, pending: false, message });
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
          <div className="beds" role="group" aria-label="Select a bed">
            {BEDS.map((bed) => (
              <button type="button" key={bed} aria-pressed={selectedBabyId === bed} onClick={() => setSelectedBabyId(bed)}>
                {BED_LABELS[bed]}
              </button>
            ))}
          </div>
          <div className="quick-actions">
            <details className="app-menu" name="dashboard-menu">
              <summary className="icon-button" aria-label="Notifications"><Bell aria-hidden="true" /></summary>
              <section className="menu-panel" aria-label="Notifications">
                <h2>Notifications</h2>
                <p className="section-caption" role="status">{notificationStatus}</p>
                <button className="menu-button" type="button" onClick={handleEnableNotifications}>Enable notifications</button>
              </section>
            </details>
            <details className="app-menu" name="dashboard-menu">
              <summary className="icon-button" aria-label="Settings"><Settings aria-hidden="true" /></summary>
              <section className="menu-panel" aria-label="Camera settings">
                <h2>Camera settings</h2>
                <label><input type="checkbox" checked={useMock} onChange={(event) => setUseMock(event.target.checked)} /> Use mock camera</label>
                <label><input type="checkbox" checked={showDetection} onChange={(event) => setShowDetection(event.target.checked)} /> Show detection boxes</label>
                <details className="menu-detail">
                  <summary>Connection details</summary>
                  <dl>
                    <div><dt>Camera</dt><dd>{activeBaby.camera_id}</dd></div>
                    <div><dt>Stream</dt><dd>{connection}</dd></div>
                    <div><dt>Motion score</dt><dd>{monitoring ? activeBaby.motion_score?.toFixed(1) ?? "—" : "—"}</dd></div>
                    <div><dt>Clip storage</dt><dd>Local (Edge)</dd></div>
                  </dl>
                </details>
              </section>
            </details>
          </div>
        </div>
      </header>

      <main className="dashboard">
        <SafetyBanner
          baby={statusBaby}
          bedLabel={BED_LABELS[selectedBabyId]}
          cameraConfigured={cameraConfigured}
          monitoring={monitoring}
          simulated={Boolean(simulatedTrigger)}
          useMock={useMock}
          petCoverSuspected={!simulatedTrigger && activeBaby.petCoverSuspected}
          onViewCamera={viewCamera}
        />
        <LivePlayer
          key={`camera-${selectedBabyId}`}
          baby={activeBaby}
          cameraConfigured={cameraConfigured}
          useMock={useMock}
          mockFeedVersion={mockFeedVersion}
          showDetection={showDetection}
          go2rtcUrl={GO2RTC_URL}
          edgeBaseUrl={EDGE_BASE_URL}
          mockCameraUrl={MOCK_CAMERA_URL}
          onConnectionChange={setConnection}
        >
          <details className="simulation-panel">
            <summary>
              Simulate an event
              <ChevronDown aria-hidden="true" />
            </summary>
            <label><input type="checkbox" checked={activeBaby.subject_mode === "PET_TEST"} onChange={toggleSubjectMode} /> Simulate with a dog</label>
            <fieldset className="simulation-actions" disabled={simulationFeedback?.pending} aria-label="Simulation actions">
              <button className="menu-button" type="button" onClick={() => simulate("NORMAL")}>Reset simulation</button>
              <button className="menu-button" type="button" onClick={() => simulate("CRY", "CRY")}>Crying</button>
              <button className="menu-button" type="button" onClick={() => simulate("ROLLOVER", "BAND_ROLLOVER")}>Rollover / covered face</button>
              <button className="menu-button" type="button" onClick={() => simulate("MOTION", "MOTION")}>Strong movement</button>
            </fieldset>
            <p className="section-caption simulation-feedback" role="status">
              {simulationFeedback?.bed === selectedBabyId ? simulationFeedback.message : ""}
            </p>
          </details>
        </LivePlayer>
        <EventFeed key={`history-${selectedBabyId}`} edgeBaseUrl={EDGE_BASE_URL} events={events.filter((event) => event.baby_id === selectedBabyId)} />
      </main>
    </div>
  );
}
