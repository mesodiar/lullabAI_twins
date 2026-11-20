import React, { useEffect, useRef, useState } from "react";
import { BabyProfile, TriggerType } from "../lib/types";
import { connectGo2RTCStream, StreamProtocol } from "../lib/webrtc";

interface LivePlayerProps {
  baby: BabyProfile;
  go2rtcUrl: string;
  edgeBaseUrl?: string;
  mockCameraUrl: string;
  onSimulateTrigger: (trigger: TriggerType) => void;
}

export const LivePlayer: React.FC<LivePlayerProps> = ({
  baby,
  go2rtcUrl,
  edgeBaseUrl = "http://localhost:8095",
  mockCameraUrl,
  onSimulateTrigger,
}) => {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [protocol, setProtocol] = useState<StreamProtocol>("CONNECTING");
  const [muted, setMuted] = useState<boolean>(true);
  const [showBandOverlay, setShowBandOverlay] = useState<boolean>(true);
  const [frameAspectRatio, setFrameAspectRatio] = useState(16 / 9);
  const [mjpegStep, setMjpegStep] = useState<"EDGE_CAM" | "GO2RTC" | "MOCK" | "STATIC">("EDGE_CAM");
  const [forceSource, setForceSource] = useState<"AUTO_CAMERA" | "MOCK_DEMO">("AUTO_CAMERA");

  useEffect(() => {
    if (forceSource === "MOCK_DEMO") {
      setProtocol("MJPEG_MOCK");
      setMjpegStep("MOCK");
      return;
    }
    if (!videoRef.current) return;
    setMjpegStep("EDGE_CAM");

    const conn = connectGo2RTCStream(
      videoRef.current,
      go2rtcUrl,
      baby.stream_name,
      (proto) => setProtocol(proto),
      () => {
        setProtocol("MJPEG_MOCK");
      }
    );

    return () => {
      conn.close();
    };
  }, [baby.stream_name, go2rtcUrl, forceSource]);

  const handleModeClick = async (
    simMode: "NORMAL" | "CRY" | "ROLLOVER" | "MOTION",
    trigger?: TriggerType
  ) => {
    try {
      await fetch(`${mockCameraUrl}/simulate/${simMode}`, { method: "POST" });
    } catch {
      // Mock camera container may not be running; still run dashboard simulation
    }
    if (trigger) {
      onSimulateTrigger(trigger);
    }
  };

  return (
    <div className="rounded-2xl bg-slate-900 border border-slate-800 overflow-hidden shadow-xl">
      {/* Top Stream Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-slate-900/90 border-b border-slate-800">
        <div className="flex items-center gap-2.5">
          <span className="relative flex h-2.5 w-2.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500"></span>
          </span>
          <span className="font-semibold text-sm text-slate-100">{baby.name}</span>
          <span className="text-xs px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
            {baby.subject_mode === "PET_TEST" ? "🐶 โหมดทดสอบ (ไม่ต้องใช้แผ่นพันตัว)" : "👶 โหมดตรวจจับเด็กอัตโนมัติ (No Band Needed)"}
          </span>
        </div>

        <div className="flex items-center gap-2 text-xs">
          <button
            onClick={() =>
              setForceSource((s) => (s === "AUTO_CAMERA" ? "MOCK_DEMO" : "AUTO_CAMERA"))
            }
            className={`px-2.5 py-1 rounded-md border font-medium transition ${
              forceSource === "AUTO_CAMERA"
                ? "bg-indigo-500/20 border-indigo-500/40 text-indigo-300"
                : "bg-amber-500/20 border-amber-500/40 text-amber-300"
            }`}
          >
            {forceSource === "AUTO_CAMERA" ? "📹 กล้องจริง (TP260)" : "🧪 ภาพจำลอง (Mock)"}
          </button>
          <span className="px-2.5 py-1 rounded-md bg-slate-800 text-slate-300 font-mono">
            {protocol === "WEBRTC" && "⚡ WebRTC (<0.2s)"}
            {protocol === "MSE" && "☁️ Live MSE"}
            {protocol === "MJPEG_MOCK" &&
              (mjpegStep === "EDGE_CAM" || mjpegStep === "GO2RTC"
                ? "📹 TP260 Live MJPEG"
                : "🎥 Mock Stream / Sample")}
            {protocol === "CONNECTING" && "⏳ Connecting..."}
          </span>
          <button
            onClick={() => setShowBandOverlay((v) => !v)}
            className={`px-2.5 py-1 rounded-md border transition ${
              showBandOverlay
                ? "bg-emerald-500/20 border-emerald-500/40 text-emerald-300"
                : "bg-slate-800 border-slate-700 text-slate-400"
            }`}
          >
            {showBandOverlay ? "🎯 กรอบตรวจจับเด็ก: เปิด" : "🎯 กรอบตรวจจับเด็ก: ปิด"}
          </button>
          <button
            onClick={() => setMuted((m) => !m)}
            className="px-2.5 py-1 rounded-md bg-slate-800 hover:bg-slate-700 text-slate-200 transition"
          >
            {muted ? "🔇 เปิดเสียง" : "🔊 ปิดเสียง"}
          </button>
        </div>
      </div>

      {/* Video / MJPEG Viewport with Nanit-style Breathing Band AI Overlay */}
      <div
        className="relative w-full bg-slate-950 overflow-hidden flex items-center justify-center"
        style={{ aspectRatio: frameAspectRatio }}
        onLoadCapture={(event) => {
          if (event.target instanceof HTMLImageElement && event.target.naturalHeight > 0) {
            setFrameAspectRatio(event.target.naturalWidth / event.target.naturalHeight);
          }
        }}
      >
        {protocol === "WEBRTC" || protocol === "MSE" || protocol === "CONNECTING" ? (
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted={muted}
            onResize={(event) => {
              const video = event.currentTarget;
              if (video.videoHeight > 0) setFrameAspectRatio(video.videoWidth / video.videoHeight);
            }}
            className="w-full h-full object-cover"
          />
        ) : mjpegStep === "EDGE_CAM" ? (
          <img
            src={`${edgeBaseUrl}/stream.mjpeg?baby_id=${encodeURIComponent(baby.baby_id)}`}
            alt="TP260 Live Camera Stream via Edge Detector"
            onError={() => setMjpegStep("GO2RTC")}
            className="w-full h-full object-cover"
          />
        ) : mjpegStep === "GO2RTC" ? (
          <img
            src={`${go2rtcUrl}/api/stream.mjpeg?src=${encodeURIComponent(baby.stream_name)}`}
            alt="go2rtc MJPEG Stream"
            onError={() => setMjpegStep("MOCK")}
            className="w-full h-full object-cover"
          />
        ) : mjpegStep === "MOCK" ? (
          <img
            src={`${mockCameraUrl}/stream.mjpeg`}
            alt="Mock Camera Stream"
            onError={() => setMjpegStep("STATIC")}
            className="w-full h-full object-cover"
          />
        ) : (
          <img
            src="/sample_baby_band.jpg"
            alt="Sample Baby with Breathing Band"
            className="w-full h-full object-cover animate-pulse"
            style={{ animationDuration: "1.875s" }}
          />
        )}

        {/* Infant vs Adult AI Classification Bounding Box Overlay */}
        {showBandOverlay && baby.subject_type === "ADULT" && baby.band_bbox_pct && (
          <div
            className="absolute border-2 border-sky-400/90 rounded-xl bg-sky-400/10 pointer-events-none transition-all duration-300 shadow-[0_0_20px_rgba(56,189,248,0.35)]"
            style={{
              left: `${baby.band_bbox_pct[0]}%`,
              top: `${baby.band_bbox_pct[1]}%`,
              width: `${baby.band_bbox_pct[2]}%`,
              height: `${baby.band_bbox_pct[3]}%`,
            }}
          >
            <div className="absolute -top-7 left-0 whitespace-nowrap bg-sky-950/90 border border-sky-400/60 text-sky-200 text-[11px] font-medium px-2.5 py-0.5 rounded-md shadow">
              🧑 ตรวจพบผู้ใหญ่ (Adult — พักการแจ้งเตือนเด็ก)
            </div>
          </div>
        )}

        {showBandOverlay && baby.subject_type !== "ADULT" && baby.band_detected && (
          <div
            className="absolute border-2 border-emerald-400/90 rounded-xl bg-emerald-400/10 pointer-events-none transition-all duration-300 shadow-[0_0_20px_rgba(52,211,153,0.35)]"
            style={{
              left: `${baby.band_bbox_pct ? baby.band_bbox_pct[0] : 34}%`,
              top: `${baby.band_bbox_pct ? baby.band_bbox_pct[1] : 54}%`,
              width: `${baby.band_bbox_pct ? baby.band_bbox_pct[2] : 26}%`,
              height: `${baby.band_bbox_pct ? baby.band_bbox_pct[3] : 28}%`,
            }}
          >
            <div className="absolute -top-7 left-0 whitespace-nowrap bg-emerald-950/90 border border-emerald-400/60 text-emerald-200 text-[11px] font-medium px-2.5 py-0.5 rounded-md shadow">
              {baby.subject_type === "PET"
                ? "🐶 ตรวจพบน้องหมาในคอก (Pet Detected)"
                : "👶 ตรวจพบเด็กทารก (Infant Detected)"}
            </div>
          </div>
        )}

        {showBandOverlay && !baby.band_detected && baby.subject_type !== "ADULT" && (
          <div className="absolute bottom-4 left-4 right-4 bg-slate-900/90 border border-slate-700 text-slate-300 px-3 py-2 rounded-xl text-xs flex items-center justify-between">
            <span>🛏️ ยังไม่พบเด็กหรือน้องหมาในกรอบคอกกั้น (กำลังสแกน...)</span>
            <span className="font-semibold text-indigo-300">AI Scanning</span>
          </div>
        )}
      </div>

      {/* Interactive Simulation Bar for Testing without Physical Camera */}
      <div className="px-4 py-3 bg-slate-900/95 border-t border-slate-800 flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-slate-400">
          🧪 ทดสอบจำลองเหตุการณ์ (System 1 Fast-Path ➔ System 2 Gemini AI):
        </span>
        <div className="flex flex-wrap gap-1.5">
          <button
            onClick={() => handleModeClick("NORMAL", "PERIODIC_CHECK")}
            className="text-xs px-2.5 py-1.5 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 border border-emerald-500/30 transition"
          >
            😴 หลับปกติ (Safe)
          </button>
          <button
            onClick={() => handleModeClick("CRY", "CRY")}
            className="text-xs px-2.5 py-1.5 rounded-lg bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 border border-amber-500/30 transition"
          >
            🚨 จำลองเสียงร้อง (Cry)
          </button>
          <button
            onClick={() => handleModeClick("ROLLOVER", "BAND_ROLLOVER")}
            className="text-xs px-2.5 py-1.5 rounded-lg bg-rose-500/15 hover:bg-rose-500/25 text-rose-300 border border-rose-500/30 transition"
          >
            ⚠️ จำลองพลิกคว่ำ/หน้าถูกปิดบัง
          </button>
          <button
            onClick={() => handleModeClick("MOTION", "MOTION")}
            className="text-xs px-2.5 py-1.5 rounded-lg bg-sky-500/15 hover:bg-sky-500/25 text-sky-300 border border-sky-500/30 transition"
          >
            👀 จำลองดิ้นแรง (Motion)
          </button>
        </div>
      </div>
    </div>
  );
};

