import React from "react";
import { BabyProfile } from "../lib/types";

interface SleepSummaryProps {
  baby: BabyProfile;
}

export const SleepSummary: React.FC<SleepSummaryProps> = ({ baby }) => {
  return (
    <div className="rounded-2xl bg-slate-900 border border-slate-800 p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-100">
          📊 สรุปการนอน & สุขภาพระบบ ({baby.name})
        </h3>
        <span className="text-xs px-2 py-0.5 rounded-md bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
          Periodic Check: ทุก 5 นาที
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
        <div className="rounded-xl bg-slate-950/70 border border-slate-800 p-3">
          <div className="text-[11px] text-slate-400">เวลานอนสะสมวันนี้</div>
          <div className="text-lg font-bold text-slate-100 mt-1">11 ชม. 20 นาที</div>
          <div className="text-[11px] text-emerald-400 mt-0.5">หลับต่อเนื่อง 2 ชม. 15 นาที</div>
        </div>

        <div className="rounded-xl bg-slate-950/70 border border-slate-800 p-3">
          <div className="text-[11px] text-slate-400">การเคลื่อนไหว (Motion)</div>
          <div className="text-lg font-bold text-emerald-300 mt-1">
            {(baby.motion_score ?? 0.5).toFixed(1)}
          </div>
          <div className="text-[11px] text-slate-400 mt-0.5">เกณฑ์แจ้งเตือน: &gt; 4.5</div>
        </div>

        <div className="rounded-xl bg-slate-950/70 border border-slate-800 p-3">
          <div className="text-[11px] text-slate-400">สิ่งที่ตรวจพบในคอก/เตียง</div>
          <div className="text-lg font-bold text-indigo-300 mt-1">
            {baby.subject_type === "ADULT"
              ? "🧑 พบผู้ใหญ่"
              : baby.subject_type === "PET"
              ? "🐶 พบน้องหมา"
              : baby.band_detected
              ? "👶 พบเด็กทารก"
              : "🛏️ คอก/เตียงว่าง"}
          </div>
          <div className="text-[11px] text-slate-400 mt-0.5">Edge Vision AI</div>
        </div>

        <div className="rounded-xl bg-slate-950/70 border border-slate-800 p-3">
          <div className="text-[11px] text-slate-400">การจัดเก็บคลิป Cloud</div>
          <div className="text-lg font-bold text-slate-100 mt-1">TTL 7 วัน</div>
          <div className="text-[11px] text-slate-400 mt-0.5">ลบอัตโนมัติใน GCS</div>
        </div>
      </div>
    </div>
  );
};

