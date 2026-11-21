import { BedDouble, TriangleAlert, VideoOff } from "lucide-react";
import type { BabyProfile, DetectedSubjectType, SleepState } from "../lib/types";

interface SafetyBannerProps {
  baby: BabyProfile;
  bedLabel: string;
  cameraConfigured: boolean;
  monitoring: boolean;
  simulated?: boolean;
  useMock: boolean;
  petCoverSuspected?: boolean;
  onViewCamera: () => void;
}

const SLEEP_LABELS: Record<SleepState, string> = {
  ASLEEP_DEEP: "Likely asleep",
  ASLEEP_LIGHT: "Likely in light sleep",
  DROWSY: "Drowsy",
  AWAKE_CALM: "Likely awake",
  AWAKE_FUSSY: "Fussy",
  CRYING: "Crying",
  OUT_OF_CRIB: "Out of crib",
};

const POSTURE_LABELS: Record<string, string> = {
  SUPINE: "On back",
  PRONE: "Face down",
  SIDE: "On side",
  SITTING_OR_STANDING: "Sitting / standing",
};

const SUBJECT_LABELS: Record<DetectedSubjectType, string> = {
  NONE: "None detected",
  ADULT: "Adult detected",
  PET: "Dog detected",
  BABY: "Baby detected",
};

export function SafetyBanner({ baby, bedLabel, cameraConfigured, monitoring, simulated, useMock, petCoverSuspected, onViewCamera }: SafetyBannerProps) {
  const isWarning = Boolean(petCoverSuspected) || baby.safety_status !== "SAFE";
  const subject = baby.subject_type ?? (baby.band_detected ? "BABY" : "NONE");
  const state = !monitoring ? "offline" : isWarning ? "warning" : "monitoring";
  const Icon = !monitoring ? VideoOff : isWarning ? TriangleAlert : BedDouble;
  const title = !monitoring
    ? useMock ? "Showing mock feed" : cameraConfigured ? "No monitoring data" : "Camera not configured"
    : petCoverSuspected
    ? "Possible blanket over dog's face"
    : isWarning
    ? baby.safety_status === "CRITICAL" ? "Check immediately" : "Possible crying detected"
    : subject === "NONE" ? "Crib empty" : SUBJECT_LABELS[subject];
  const note = !monitoring
    ? bedLabel
    : `${bedLabel} · ${simulated ? "Simulated event" : isWarning ? "Check now" : subject === "ADULT" ? "Baby alerts paused" : "Monitoring"}`;

  return (
    <section className="status-bar" data-state={state} aria-label="Bed status">
      <div className="status-lead">
        <span className="status-icon"><Icon aria-hidden="true" /></span>
        <div className="status-copy" role="status" aria-atomic="true">
          <h2>{title}</h2>
          <p>{note}</p>
        </div>
        {monitoring && isWarning && (
          <button className="status-action" type="button" onClick={onViewCamera}>View camera</button>
        )}
      </div>
      {monitoring && (
        <dl className="status-details">
          <div><dt>In view</dt><dd>{petCoverSuspected ? "Check the dog" : SUBJECT_LABELS[subject]}</dd></div>
          <div><dt>Sleep</dt><dd>{isWarning && baby.current_sleep_state !== "CRYING" ? "Unknown" : SLEEP_LABELS[baby.current_sleep_state]}</dd></div>
          <div><dt>Position</dt><dd>{petCoverSuspected ? "Unknown" : POSTURE_LABELS[baby.posture] ?? "Unknown"}</dd></div>
        </dl>
      )}
    </section>
  );
}
