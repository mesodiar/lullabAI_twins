import React from "react";
import { LullabAIEvent } from "../lib/types";

interface EventFeedProps {
  events: LullabAIEvent[];
}

export const EventFeed: React.FC<EventFeedProps> = ({ events }) => {
  return (
    <div className="rounded-2xl bg-slate-900 border border-slate-800 p-4">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-100">
            🔔 ประวัติการแจ้งเตือน & ผลวิเคราะห์ AI (เก็บคลิปย้อนหลัง 7 วัน)
          </h3>
          <p className="text-xs text-slate-400">
            แสดงการทำงานแบบ Two-Stage: แจ้งเตือนด่วนจาก Edge ทันที ➔ อัปเดตผลวิเคราะห์จาก Vertex AI Gemini
          </p>
        </div>
        <span className="text-xs px-2.5 py-1 rounded-full bg-slate-800 text-slate-300">
          {events.length} รายการ
        </span>
      </div>

      <div className="space-y-3 max-h-[420px] overflow-y-auto pr-1">
        {events.map((evt) => {
          const isPending = evt.status === "PENDING_AI";
          const ai = evt.ai_analysis;
          const isCritical =
            ai?.safety_alert ||
            ai?.urgency === "CRITICAL" ||
            evt.trigger_type === "BAND_ROLLOVER";

          return (
            <div
              key={evt.event_id}
              className={`rounded-xl border p-3.5 transition-all ${
                isPending
                  ? "bg-indigo-950/40 border-indigo-500/50 animate-pulse"
                  : isCritical
                  ? "bg-rose-950/30 border-rose-500/40"
                  : "bg-slate-950/60 border-slate-800"
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span
                    className={`text-[11px] font-semibold px-2 py-0.5 rounded-md ${
                      isPending
                        ? "bg-amber-500/20 text-amber-300 border border-amber-500/40"
                        : "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                    }`}
                  >
                    {isPending
                      ? "⚡ Stage 1: Fast-Path Alert (กำลังรอ Gemini AI...)"
                      : "🧠 Stage 2: วิเคราะห์โดย Gemini AI แล้ว"}
                  </span>
                  <span className="text-xs font-mono text-slate-400">
                    Trigger: {evt.trigger_type}
                  </span>
                </div>
                <span className="text-[11px] text-slate-400">
                  {new Date(evt.created_at).toLocaleTimeString("th-TH")}
                </span>
              </div>

              <div className="mt-2">
                <div className="text-sm font-semibold text-slate-100">
                  {ai
                    ? ai.title_th
                    : "🚨 แจ้งเตือนด่วนจาก Edge Node — กำลังอัปโหลดคลิป 7 วินาทีให้ AI ตรวจสอบ..."}
                </div>
                <p className="text-xs text-slate-300 mt-1 leading-relaxed">
                  {ai
                    ? ai.reason_hypothesis_th
                    : `ตรวจพบค่า Trigger (${evt.trigger_type}) • สถานะในเตียง: ${
                        evt.edge_metrics.band_detected ? "พบเด็กทารก" : "ควรตรวจสอบเตียง!"
                      } • Motion Score: ${evt.edge_metrics.motion_score.toFixed(1)}`}
                </p>
                {ai?.recommended_action_th && (
                  <div className="mt-2 text-xs text-indigo-300 bg-indigo-950/40 border border-indigo-500/20 rounded-lg px-2.5 py-1.5">
                    💡 <strong>คำแนะนำ:</strong> {ai.recommended_action_th}
                  </div>
                )}
              </div>

              <div className="mt-2.5 pt-2 border-t border-slate-800/80 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-400">
                <div className="flex items-center gap-3">
                  <span>
                    👶 Baby:{" "}
                    {evt.edge_metrics.band_detected ? "✅ Infant" : "⚠️ Check Crib"}
                  </span>
                  <span>
                    🏃 Motion: {evt.edge_metrics.motion_score.toFixed(1)}
                  </span>
                  <span>
                    🔊 Cry Prob: {(evt.edge_metrics.yamnet_cry_prob * 100).toFixed(0)}%
                  </span>
                </div>
                <span className="font-mono text-[10px] text-slate-500">
                  ID: {evt.event_id}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

