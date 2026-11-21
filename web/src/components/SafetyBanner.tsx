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
  ASLEEP_DEEP: "คาดว่าหลับ",
  ASLEEP_LIGHT: "คาดว่าหลับตื้น",
  DROWSY: "เคลิ้มหลับ",
  AWAKE_CALM: "คาดว่าตื่น",
  AWAKE_FUSSY: "เริ่มงอแง",
  CRYING: "กำลังร้องไห้",
  OUT_OF_CRIB: "นอกเตียง",
};

const POSTURE_LABELS: Record<string, string> = {
  SUPINE: "นอนหงาย",
  PRONE: "นอนคว่ำ",
  SIDE: "นอนตะแคง",
  SITTING_OR_STANDING: "นั่ง / ยืน",
};

const SUBJECT_LABELS: Record<DetectedSubjectType, string> = {
  NONE: "ไม่พบในภาพ",
  ADULT: "พบผู้ใหญ่",
  PET: "พบน้องหมา",
  BABY: "พบเด็กทารก",
};

export function SafetyBanner({ baby, bedLabel, cameraConfigured, monitoring, simulated, useMock, petCoverSuspected, onViewCamera }: SafetyBannerProps) {
  const isWarning = Boolean(petCoverSuspected) || baby.safety_status !== "SAFE";
  const subject = baby.subject_type ?? (baby.band_detected ? "BABY" : "NONE");
  const state = !monitoring ? "offline" : isWarning ? "warning" : "monitoring";
  const Icon = !monitoring ? VideoOff : isWarning ? TriangleAlert : BedDouble;
  const title = !monitoring
    ? useMock ? "กำลังใช้ภาพจำลอง" : cameraConfigured ? "ยังไม่มีข้อมูลเฝ้าระวัง" : "ยังไม่ได้ตั้งค่ากล้อง"
    : petCoverSuspected
    ? "สงสัยว่าผ้าปิดหน้าน้องหมา"
    : isWarning
    ? baby.safety_status === "CRITICAL" ? "ควรตรวจดูทันที" : "ตรวจพบเสียงคล้ายเสียงร้อง"
    : subject === "NONE" ? "คอก / เตียงว่าง" : SUBJECT_LABELS[subject];
  const note = !monitoring
    ? bedLabel
    : `${bedLabel} · ${simulated ? "เหตุการณ์จำลอง" : isWarning ? "ควรตรวจดู" : subject === "ADULT" ? "พักการแจ้งเตือนเด็ก" : "กำลังเฝ้าระวัง"}`;

  return (
    <section className="status-bar" data-state={state} aria-label="สถานะเตียง">
      <div className="status-lead">
        <span className="status-icon"><Icon aria-hidden="true" /></span>
        <div className="status-copy" role="status" aria-atomic="true">
          <h2>{title}</h2>
          <p>{note}</p>
        </div>
        {monitoring && isWarning && (
          <button className="status-action" type="button" onClick={onViewCamera}>ดูภาพ</button>
        )}
      </div>
      {monitoring && (
        <dl className="status-details">
          <div><dt>ในภาพ</dt><dd>{petCoverSuspected ? "ควรตรวจดูน้องหมา" : SUBJECT_LABELS[subject]}</dd></div>
          <div><dt>การนอน</dt><dd>{isWarning && baby.current_sleep_state !== "CRYING" ? "รอตรวจสอบ" : SLEEP_LABELS[baby.current_sleep_state]}</dd></div>
          <div><dt>ท่านอน</dt><dd>{petCoverSuspected ? "รอตรวจสอบ" : POSTURE_LABELS[baby.posture] ?? "รอตรวจสอบ"}</dd></div>
        </dl>
      )}
    </section>
  );
}
