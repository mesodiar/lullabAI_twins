import { Bell, ChevronDown, MoonStar, Settings } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { disableBackgroundPush, enableBackgroundPush, prepareBackgroundPush, type BackgroundPushSetup } from "./backgroundPush";
import "./dashboard.css";
import { CloudReviewSettings, type CloudReviewStatus } from "./components/CloudReviewSettings";
import { VideoRetentionSettings } from "./components/VideoRetentionSettings";
import { EventFeed } from "./components/EventFeed";
import { LivePlayer } from "./components/LivePlayer";
import { SafetyBanner } from "./components/SafetyBanner";
import { SleepSummary } from "./components/SleepSummary";
import { isImportantAlert, notifyLocal, requestLocalNotifications, shouldNotifyLocal } from "./localAlerts";
import type { BabyProfile, EdgeMetrics, LullabAIEvent } from "./lib/types";

const LOCAL_HOST = window.location.hostname;
const USE_HTTPS_PROXY = window.location.protocol === "https:";
const GO2RTC_URL = import.meta.env.VITE_GO2RTC_URL || (USE_HTTPS_PROXY ? "/go2rtc" : `http://${LOCAL_HOST}:1984`);
const EDGE_BASE_URL =
  import.meta.env.VITE_EDGE_BASE_URL || (USE_HTTPS_PROXY ? "/edge" : `http://${LOCAL_HOST}:8095`);
const MOCK_CAMERA_URL =
  import.meta.env.VITE_MOCK_CAMERA_URL || (USE_HTTPS_PROXY ? "/mock" : `http://${LOCAL_HOST}:8090`);
const EDGE_STATUS_URL =
  import.meta.env.VITE_EDGE_STATUS_URL || `${EDGE_BASE_URL}/status`;

const BEDS = ["baby_1", "baby_2"] as const;
const BED_LABELS = { baby_1: "Bed 1", baby_2: "Bed 2" };
const STALE_AFTER_MS = 5_000;
const CRY_CLIP_ID = "n-R3LzEtumg";

type BedId = typeof BEDS[number];
type SimulatedTrigger = "BAND_ROLLOVER" | "MOTION";
type DashboardProfile = BabyProfile & {
  cameraConfigured?: boolean;
  petCoverSuspected?: boolean;
  audioClassifierReady?: boolean;
};

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
  cloud_reviews_available?: boolean;
  cloud_reviews_enabled?: boolean;
  cloud_review_setup_configured?: boolean;
  video_retention_days?: number | null;
  babies?: Partial<Record<BedId, {
    updated_at?: string;
    edge_metrics?: EdgeMetrics & { band_bbox_pct?: BabyProfile["band_bbox_pct"]; face_cover_suspected?: boolean };
  }>>;
  recent_events?: LullabAIEvent[];
}

export function App() {
  const [selectedBabyId, setSelectedBabyId] = useState<BedId>(() =>
    new URLSearchParams(window.location.search).get("bed") === "baby_2" ? "baby_2" : "baby_1"
  );
  const [babies, setBabies] = useState<Record<string, DashboardProfile>>(initialBabies);
  const [events, setEvents] = useState<LullabAIEvent[]>([]);
  const [notificationStatus, setNotificationStatus] = useState(() =>
    "Notification" in window && Notification.permission === "granted"
      ? "Notifications are on while this page is open."
      : "Get alerts while this page is open."
  );
  const [backgroundPush, setBackgroundPush] = useState<BackgroundPushSetup | "unsupported" | null>(null);
  const [setupCode, setSetupCode] = useState("");
  const [changingNotifications, setChangingNotifications] = useState(false);
  const hasBackgroundPush = useRef(false);
  const [useMock, setUseMock] = useState(false);
  const [showDetection, setShowDetection] = useState(true);
  const [connection, setConnection] = useState("");
  const [cloudReview, setCloudReview] = useState<CloudReviewStatus | null>(null);
  const [videoRetention, setVideoRetention] = useState<number | null>();
  const [simulation, setSimulation] = useState<{ bed: BedId; trigger: SimulatedTrigger } | null>(null);
  const [simulationFeedback, setSimulationFeedback] = useState<{ bed: BedId; pending: boolean; message: string } | null>(null);
  const [mockFeedVersion, setMockFeedVersion] = useState(0);
  const [cryTest, setCryTest] = useState<{ bed: BedId; startedAt: number } | null>(null);
  const cryTestDialog = useRef<HTMLDialogElement>(null);

  const activeBaby = babies[selectedBabyId];
  const cameraConfigured = selectedBabyId === "baby_1" || activeBaby.cameraConfigured === true;
  const simulatedTrigger = simulation?.bed === selectedBabyId && (useMock || activeBaby.safety_status === "SAFE")
    ? simulation.trigger
    : undefined;
  const statusBaby: BabyProfile = simulatedTrigger ? {
    ...activeBaby,
    subject_type: simulatedTrigger === "BAND_ROLLOVER" ? "NONE" : "BABY",
    current_sleep_state: "AWAKE_CALM",
    posture: "UNKNOWN",
    safety_status: simulatedTrigger === "BAND_ROLLOVER" ? "CRITICAL" : "SAFE",
  } : activeBaby;
  const monitoring = Boolean(simulatedTrigger || (!useMock && activeBaby.edge_online && activeBaby.last_heartbeat_at));
  const heardCrying = cryTest && events.some((event) =>
    event.baby_id === cryTest.bed && event.trigger_type === "CRY" && !event.simulated &&
    event.edge_metrics.audio_kind === "BABY_CRY" &&
    isImportantAlert(event) && Date.parse(event.created_at) >= cryTest.startedAt
  );

  useEffect(() => {
    let active = true;
    void prepareBackgroundPush().then((setup) => {
      if (!active) return;
      setBackgroundPush(setup);
      if (setup && setup !== "unsupported" && setup.registered) {
        hasBackgroundPush.current = true;
        setNotificationStatus("Background alerts are on for this device.");
      }
    }).catch(() => {});
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let polling = false;
    let notifiedEventIds: Set<string> | null = null;

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
              audioClassifierReady: metrics.audio_classifier_ready,
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
        setVideoRetention(data?.video_retention_days);
        setCloudReview(data ? {
          available: data.cloud_reviews_available === true,
          enabled: data.cloud_reviews_enabled === true,
          setupConfigured: data.cloud_review_setup_configured === true,
        } : null);
        if (Array.isArray(data?.recent_events)) {
          const received = data.recent_events.filter((event) => event?.event_id && event.trigger_type !== "PERIODIC_CHECK");
          const cloudEnabled = data?.cloud_reviews_enabled === true;
          if (notifiedEventIds === null) {
            notifiedEventIds = new Set(
              received.filter((event) => shouldNotifyLocal(event, cloudEnabled)).map((event) => event.event_id)
            );
          } else {
            for (const event of received) {
              if (!notifiedEventIds.has(event.event_id) && shouldNotifyLocal(event, cloudEnabled)) {
                notifiedEventIds.add(event.event_id);
                if (!hasBackgroundPush.current) {
                  void notifyLocal(event, BED_LABELS[event.baby_id as BedId] ?? event.baby_id);
                }
              }
            }
          }
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
        if (!controller.signal.aborted) {
          updateBabies();
          setCloudReview(null);
          setVideoRetention(undefined);
        }
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
    setChangingNotifications(true);
    try {
      if (backgroundPush && backgroundPush !== "unsupported") {
        const updated = backgroundPush.registered
          ? await disableBackgroundPush(backgroundPush)
          : await enableBackgroundPush(backgroundPush, setupCode.trim());
        hasBackgroundPush.current = updated.registered;
        setBackgroundPush(updated);
        setSetupCode("");
        setNotificationStatus(updated.registered
          ? "Background alerts are on for this device."
          : "Background alerts are off for this device.");
      } else {
        setNotificationStatus(await requestLocalNotifications());
      }
    } catch (error) {
      setNotificationStatus(error instanceof Error && error.name === "Error"
        ? error.message
        : "Could not change notifications. Check your browser permissions.");
    } finally {
      setChangingNotifications(false);
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
        yamnet_cry_prob: 0.04,
        yamnet_top_label: "Silence",
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

  const simulate = async (mode: "NORMAL" | "ROLLOVER" | "MOTION", trigger?: SimulatedTrigger) => {
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

  const startCryTest = () => {
    const dialog = cryTestDialog.current;
    if (!dialog || dialog.open) return;
    setSimulation(null);
    setSimulationFeedback(null);
    dialog.showModal();
    setCryTest({ bed: selectedBabyId, startedAt: Date.now() });
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
                {backgroundPush === "unsupported" && (
                  <p className="section-caption">For background alerts on iPhone or iPad, add the HTTPS dashboard to your Home Screen and open it from there.</p>
                )}
                {backgroundPush && backgroundPush !== "unsupported" && !backgroundPush.registered && (
                  <label className="push-setup">
                    Setup code
                    <input type="password" value={setupCode} onChange={(event) => setSetupCode(event.target.value)} autoComplete="off" />
                  </label>
                )}
                <button
                  className="menu-button"
                  type="button"
                  onClick={handleEnableNotifications}
                  disabled={changingNotifications || Boolean(backgroundPush && backgroundPush !== "unsupported" && !backgroundPush.registered && !setupCode.trim())}
                >
                  {backgroundPush && backgroundPush !== "unsupported"
                    ? backgroundPush.registered ? "Turn off background alerts" : "Enable background alerts"
                    : "Enable notifications"}
                </button>
              </section>
            </details>
            <details className="app-menu" name="dashboard-menu">
              <summary className="icon-button" aria-label="Settings"><Settings aria-hidden="true" /></summary>
              <section className="menu-panel" aria-label="Settings">
                <h2>Settings</h2>
                <label><input type="checkbox" checked={useMock} onChange={(event) => setUseMock(event.target.checked)} /> Use mock camera</label>
                <label><input type="checkbox" checked={showDetection} onChange={(event) => setShowDetection(event.target.checked)} /> Show detection boxes</label>
                <CloudReviewSettings status={cloudReview} onChange={(enabled) => {
                  setCloudReview((status) => status && { ...status, enabled });
                }} />
                <VideoRetentionSettings days={videoRetention} setupConfigured={cloudReview?.setupConfigured === true}
                  onChange={setVideoRetention} />
                <details className="menu-detail">
                  <summary>Connection details</summary>
                  <dl>
                    <div><dt>Camera</dt><dd>{activeBaby.camera_id}</dd></div>
                    <div><dt>Stream</dt><dd>{connection}</dd></div>
                    {monitoring && activeBaby.audioClassifierReady !== undefined && (
                      <div><dt>Audio classifier</dt><dd>{activeBaby.audioClassifierReady ? "Ready" : "Unavailable"}</dd></div>
                    )}
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
              <button className="menu-button" type="button" onClick={startCryTest}>Crying</button>
              <button className="menu-button" type="button" onClick={() => simulate("ROLLOVER", "BAND_ROLLOVER")}>Rollover / covered face</button>
              <button className="menu-button" type="button" onClick={() => simulate("MOTION", "MOTION")}>Strong movement</button>
            </fieldset>
            <p className="section-caption simulation-feedback" role="status">
              {simulationFeedback?.bed === selectedBabyId ? simulationFeedback.message : ""}
            </p>
          </details>
        </LivePlayer>
        <EventFeed key={`history-${selectedBabyId}`} edgeBaseUrl={EDGE_BASE_URL} events={events.filter((event) => event.baby_id === selectedBabyId)} />
        <SleepSummary
          babyId={selectedBabyId}
          bedLabel={BED_LABELS[selectedBabyId]}
          edgeBaseUrl={EDGE_BASE_URL}
          cloudReviewsEnabled={cloudReview?.enabled === true}
        />
      </main>
      <dialog
        className="cry-test-dialog"
        ref={cryTestDialog}
        aria-labelledby="cry-test-title"
        onClose={() => setCryTest(null)}
      >
        <div className="cry-test-heading">
          <h2 id="cry-test-title">Crying sound test</h2>
          <button className="menu-button" type="button" onClick={() => cryTestDialog.current?.close()}>Stop test</button>
        </div>
        {cryTest && (
          <>
            <iframe
              src={`https://www.youtube.com/embed/${CRY_CLIP_ID}?autoplay=1&playsinline=1`}
              title="Baby Crying Sound Effect"
              allow="autoplay; encrypted-media"
              allowFullScreen
              referrerPolicy="strict-origin-when-cross-origin"
            />
            <p>Use speakers near the camera so its microphone can hear the clip. Press Play in the video if needed.</p>
            <p className="cry-test-status" role="status">
              {heardCrying
                ? `${BED_LABELS[cryTest.bed]} reported possible crying. See Recent events for the alert.`
                : !babies[cryTest.bed].edge_online
                ? `Waiting for ${BED_LABELS[cryTest.bed]} to connect to Edge.`
                : `Waiting for a cry alert from ${BED_LABELS[cryTest.bed]}…`}
            </p>
            <a className="text-action" href={`https://www.youtube.com/watch?v=${CRY_CLIP_ID}`} target="_blank" rel="noreferrer">
              Open on YouTube
            </a>
          </>
        )}
      </dialog>
    </div>
  );
}
