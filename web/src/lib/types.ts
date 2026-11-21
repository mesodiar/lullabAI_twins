export type SubjectMode = "BABY" | "PET_TEST";

export type TriggerType =
  | "CRY"
  | "MOTION"
  | "BREATHING_ANOMALY"
  | "BAND_ROLLOVER"
  | "PERIODIC_CHECK";

export type SleepState =
  | "ASLEEP_DEEP"
  | "ASLEEP_LIGHT"
  | "DROWSY"
  | "AWAKE_CALM"
  | "AWAKE_FUSSY"
  | "CRYING"
  | "OUT_OF_CRIB";

export type SafetyStatus = "SAFE" | "WARNING" | "CRITICAL";

export type DetectedSubjectType = "BABY" | "PET" | "ADULT" | "NONE";

export interface EdgeMetrics {
  yamnet_cry_prob: number;
  yamnet_top_label: string;
  motion_score: number;
  band_detected: boolean;
  band_confidence: number;
  breathing_rpm: number | null;
  band_bbox: [number, number, number, number] | null;
  subject_type?: DetectedSubjectType;
  face_cover_suspected?: boolean;
}

export interface AIAnalysis {
  is_false_alarm: boolean;
  safety_alert: boolean;
  posture: "SUPINE" | "PRONE" | "SIDE" | "SITTING_OR_STANDING" | "UNKNOWN";
  face_covered: boolean;
  band_visible_and_secure: boolean;
  sleep_state: SleepState;
  urgency: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  title_th: string;
  reason_hypothesis_th: string;
  recommended_action_th: string;
}

export interface LullabAIEvent {
  event_id: string;
  baby_id: string;
  camera_id: string;
  subject_mode: SubjectMode;
  trigger_type: TriggerType;
  status: "EDGE_DETECTED" | "PENDING_AI" | "ANALYZED" | "FALSE_ALARM_SUPPRESSED";
  simulated?: boolean;
  created_at: string;
  analyzed_at: string | null;
  edge_metrics: EdgeMetrics;
  media?: {
    gcs_video_uri?: string | null;
    gcs_snapshot_uri?: string | null;
    signed_video_url?: string | null;
    signed_snapshot_url?: string | null;
    local_video_path?: string;
    local_snapshot_path?: string;
  };
  ai_analysis: AIAnalysis | null;
}

export interface BabyProfile {
  baby_id: string;
  name: string;
  camera_id: string;
  stream_name: string;
  subject_mode: SubjectMode;
  current_sleep_state: SleepState;
  safety_status: SafetyStatus;
  posture: string;
  face_covered: boolean;
  band_detected: boolean;
  band_bbox_pct?: [number, number, number, number] | null;
  subject_type?: DetectedSubjectType;
  motion_score?: number;
  yamnet_cry_prob?: number;
  breathing_rpm: number | null;
  edge_online: boolean;
  last_ai_title_th?: string;
  last_ai_summary_th?: string;
  last_heartbeat_at?: string;
}

