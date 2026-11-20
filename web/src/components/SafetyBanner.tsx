import React from "react";
import { BabyProfile } from "../lib/types";

interface SafetyBannerProps {
  baby: BabyProfile;
}

const SLEEP_STATE_LABELS: Record<string, string> = {
  ASLEEP_DEEP: "หลับสนิท (Deep Sleep)",
  ASLEEP_LIGHT: "หลับตื้น (Light Sleep)",
  DROWSY: "เคลิ้มใกล้หลับ (Drowsy)",
  AWAKE_CALM: "ตื่นสงบ (Awake Calm)",
  AWAKE_FUSSY: "เริ่มงอแง (Fussy)",
  CRYING: "กำลังร้องไห้ (Crying)",
  OUT_OF_CRIB: "ไม่อยู่ในเตียง",
};

const POSTURE_LABELS: Record<string, string> = {
  SUPINE: "นอนหงาย (ปลอดภัย)",
  PRONE: "นอนคว่ำ (ควรระวัง!)",
  SIDE: "นอนตะแคง",
  SITTING_OR_STANDING: "นั่ง/ยืน",
  UNKNOWN: "รอตรวจสอบ",
};

export const SafetyBanner: React.FC<SafetyBannerProps> = ({ baby }) => {
  const isCritical = baby.safety_status === "CRITICAL";
  const isWarning = baby.safety_status === "WARNING";

  const bannerTheme = isCritical
    ? "bg-rose-950/70 border-rose-500/70 text-rose-100"
    : isWarning
    ? "bg-amber-950/70 border-amber-500/70 text-amber-100"
    : "bg-emerald-950/50 border-emerald-500/40 text-emerald-100";

  return (
    <div className={`rounded-2xl border p-4 transition-all duration-300 ${bannerTheme}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold">
              {baby.last_ai_title_th || "😴 สถานะปกติ"}
            </span>
            <span
              className={`text-[11px] font-bold px-2 py-0.5 rounded-full uppercase ${
                isCritical
                  ? "bg-rose-500 text-white animate-pulse"
                  : isWarning
                  ? "bg-amber-500 text-slate-950"
                  : "bg-emerald-500/30 text-emerald-200 border border-emerald-400/40"
              }`}
            >
              {baby.safety_status}
            </span>
          </div>
          <p className="text-xs opacity-90 mt-1 leading-relaxed">
            {baby.last_ai_summary_th}
          </p>
        </div>

        {/* Subject Classification & Sleep Status Pills */}
        <div className="flex items-center gap-3">
          <div className="rounded-xl bg-slate-950/60 border border-white/10 px-3.5 py-2 text-center min-w-[115px]">
            <div className="text-[11px] uppercase tracking-wider opacity-75">
              ผู้ที่อยู่ในเฟรม
            </div>
            <div className="text-xs font-bold mt-1 text-emerald-300">
              {baby.subject_type === "ADULT"
                ? "🧑 ผู้ใหญ่ (Adult)"
                : baby.subject_type === "PET"
                ? "🐶 น้องหมา (Pet)"
                : baby.band_detected
                ? "👶 เด็กทารก (Infant)"
                : "🛏️ คอก/เตียงว่าง"}
            </div>
          </div>

          <div className="rounded-xl bg-slate-950/60 border border-white/10 px-3.5 py-2 text-center min-w-[120px]">
            <div className="text-[11px] uppercase tracking-wider opacity-75">
              สถานะการนอน
            </div>
            <div className="text-xs font-semibold mt-1">
              {SLEEP_STATE_LABELS[baby.current_sleep_state] || baby.current_sleep_state}
            </div>
          </div>

          <div className="rounded-xl bg-slate-950/60 border border-white/10 px-3.5 py-2 text-center min-w-[120px]">
            <div className="text-[11px] uppercase tracking-wider opacity-75">
              ท่านอน & ความปลอดภัย
            </div>
            <div className="text-xs font-semibold mt-1">
              {POSTURE_LABELS[baby.posture] || baby.posture}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

