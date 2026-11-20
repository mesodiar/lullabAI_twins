"""Main Orchestrator for LullabAI Twins Edge Node (System 1 Reflex)."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse
import uuid

import numpy as np
import yaml

from audio_cry import AudioCryDetector, AudioDetectionResult
from breathing_band import BandTrackingResult, BreathingBandTracker
from cloud_sync import CloudSyncClient
from cv_compat import cv2
from motion import MotionDetector, MotionResult
from stream_reader import RingBufferStreamReader


class BabyMonitorPipeline:
    """Runs System 1 Reflex (Breathing Band + Motion + YAMNet Cry + 5-min Periodic Check) for one crib."""

    def __init__(
        self,
        baby_cfg: dict[str, Any],
        global_cfg: dict[str, Any],
        cloud_sync: CloudSyncClient,
    ) -> None:
        self.baby_id: str = baby_cfg["baby_id"]
        self.camera_id: str = baby_cfg["camera_id"]
        self.subject_mode: str = os.environ.get("SUBJECT_MODE") or global_cfg.get("subject_mode", "PET_TEST")
        self.fps: int = int(global_cfg.get("target_fps", 12))
        self.post_roll_seconds: float = float(global_cfg.get("post_roll_seconds", 3.0))
        self.periodic_interval_sec: float = float(global_cfg.get("periodic_snapshot_interval_sec", 300.0))
        self.cooldowns: dict[str, float] = global_cfg.get(
            "cooldowns",
            {"CRY": 30.0, "BAND_ROLLOVER": 25.0, "BREATHING_ANOMALY": 25.0, "MOTION": 60.0},
        )
        self.cloud_sync = cloud_sync

        thresholds = baby_cfg.get("thresholds", {})
        roi = tuple(baby_cfg.get("crib_roi", [0.10, 0.05, 0.90, 0.95]))

        # Prefer direct RTSP env vars (e.g. BABY_1_SUB_RTSP from edge/.env) when set for real cameras
        env_prefix = self.baby_id.upper()
        env_sub_rtsp = os.environ.get(f"{env_prefix}_SUB_RTSP") or os.environ.get(f"{env_prefix}_MAIN_RTSP")
        video_source_url = env_sub_rtsp if env_sub_rtsp else baby_cfg["video_source_url"]
        rtsp_audio_url = env_sub_rtsp if env_sub_rtsp else baby_cfg.get("rtsp_audio_url")

        self.reader = RingBufferStreamReader(
            video_source_url=video_source_url,
            snapshot_url=baby_cfg.get("snapshot_url"),
            audio_wav_url=baby_cfg.get("audio_wav_url"),
            rtsp_audio_url=rtsp_audio_url,
            fps=self.fps,
            pre_roll_seconds=float(global_cfg.get("pre_roll_seconds", 4.0)),
            post_roll_seconds=self.post_roll_seconds,
        )
        self.band_tracker = BreathingBandTracker(
            min_markers=int(thresholds.get("band_min_markers", 8)),
            apnea_low_rpm_threshold=float(thresholds.get("apnea_low_rpm", 8.0)),
        )
        self.motion_detector = MotionDetector(
            roi_normalized=roi,  # type: ignore[arg-type]
            motion_threshold=float(thresholds.get("motion_score", 4.5)),
        )
        self.audio_detector = AudioCryDetector(
            subject_mode=self.subject_mode,  # type: ignore[arg-type]
            cry_threshold=float(thresholds.get("cry_prob", 0.55)),
        )

        self._last_alert_at: dict[str, float] = {}
        self._last_periodic_at: float = time.time()
        self._latest_audio: AudioDetectionResult = AudioDetectionResult(0.0, "Silence", 0.0, False)
        self._latest_status: dict[str, Any] = {}
        self.recent_events: deque[dict[str, Any]] = deque(maxlen=15)

    def evaluate_triggers(
        self,
        band_res: BandTrackingResult,
        motion_res: MotionResult,
        audio_res: AudioDetectionResult,
        now: float,
    ) -> str | None:
        """Determines highest-priority trigger type respecting per-type cooldowns."""
        # If an ADULT is detected in front of the camera, suppress baby motion triggers
        if band_res.subject_type == "ADULT":
            return None

        candidates: list[str] = []
        if band_res.rollover_suspected:
            candidates.append("BAND_ROLLOVER")
        if audio_res.cry_triggered:
            candidates.append("CRY")
        if motion_res.motion_triggered and band_res.subject_type in ("BABY", "PET"):
            candidates.append("MOTION")

        for trigger in candidates:
            cooldown = float(self.cooldowns.get(trigger, 30.0))
            last_time = self._last_alert_at.get(trigger, 0.0)
            if (now - last_time) >= cooldown:
                self._last_alert_at[trigger] = now
                return trigger

        if (now - self._last_periodic_at) >= self.periodic_interval_sec:
            self._last_periodic_at = now
            return "PERIODIC_CHECK"

        return None

    def step_once(self, frame_bgr: np.ndarray, audio_waveform: np.ndarray | None = None) -> dict[str, Any]:
        """Processes one frame (+ optional audio chunk) and returns current telemetry & any triggered event."""
        now = time.time()
        band_res = self.band_tracker.process_frame(frame_bgr, timestamp=now)
        motion_res = self.motion_detector.process_frame(frame_bgr)

        if audio_waveform is not None:
            self._latest_audio = self.audio_detector.analyze_waveform(audio_waveform)

        audio_res = self._latest_audio
        trigger = self.evaluate_triggers(band_res, motion_res, audio_res, now)

        h_frm, w_frm = frame_bgr.shape[:2]
        band_bbox_pct: list[float] | None = None
        if band_res.band_bbox is not None and w_frm > 0 and h_frm > 0:
            bx, by, bw, bh = band_res.band_bbox
            band_bbox_pct = [
                round((bx / w_frm) * 100.0, 2),
                round((by / h_frm) * 100.0, 2),
                round((bw / w_frm) * 100.0, 2),
                round((bh / h_frm) * 100.0, 2),
            ]

        metrics = {
            "yamnet_cry_prob": audio_res.cry_prob,
            "yamnet_top_label": audio_res.top_label,
            "motion_score": motion_res.motion_score,
            "band_detected": band_res.band_detected,
            "band_confidence": band_res.band_confidence,
            "breathing_rpm": None,
            "band_bbox": band_res.band_bbox,
            "band_bbox_pct": band_bbox_pct,
            "subject_type": band_res.subject_type,
        }

        self._latest_status = {
            "baby_id": self.baby_id,
            "camera_id": self.camera_id,
            "subject_mode": self.subject_mode,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "edge_metrics": metrics,
            "triggered": trigger,
        }
        return self._latest_status

    def handle_triggered_event(self, trigger_type: str, metrics: dict[str, Any]) -> dict[str, Any]:
        """Executes 2-stage flow: Fast-Path Firestore/FCM immediately, then post-roll clip upload to GCS."""
        started_at = time.monotonic()
        event_id = f"evt_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        event_doc: dict[str, Any] = {
            "event_id": event_id,
            "baby_id": self.baby_id,
            "camera_id": self.camera_id,
            "subject_mode": self.subject_mode,
            "trigger_type": trigger_type,
            "status": "PENDING_AI",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "analyzed_at": None,
            "edge_metrics": metrics,
            "media": {
                "gcs_video_uri": None,
                "gcs_snapshot_uri": None,
                "signed_video_url": None,
                "signed_snapshot_url": None,
            },
            "ai_analysis": None,
        }

        print(f"[Edge:{self.baby_id}] ⚡ Trigger={trigger_type} (id={event_id}) | Metrics={metrics}")
        self.recent_events.appendleft(event_doc)

        # Keep the pre-roll frames before the ring buffer advances during the alert.
        frames = self.reader.get_buffered_frames(through=started_at)
        self.cloud_sync.send_fast_path_alert(event_doc)

        if trigger_type != "PERIODIC_CHECK":
            deadline = started_at + self.post_roll_seconds
            time.sleep(max(0.0, deadline - time.monotonic()))
            frames.extend(self.reader.get_buffered_frames(after=started_at, through=deadline))

        # Export clip + snapshot and upload to GCS to wake up System 2 (Cloud Run + Vertex AI)
        with tempfile.TemporaryDirectory(prefix="lullabai_edge_") as tmp_dir:
            clip_path, snap_path = self.reader.export_clip_and_snapshot(
                Path(tmp_dir),
                event_id=event_id,
                frames=frames,
            )
            media_uris = self.cloud_sync.upload_media_and_trigger_system2(
                event_doc=event_doc,
                clip_path=clip_path if trigger_type != "PERIODIC_CHECK" else None,
                snapshot_path=snap_path,
            )
            event_doc["media"].update(media_uris)

        return event_doc

    def _run_audio(self) -> None:
        # RTSP audio capture blocks while FFmpeg reads a waveform.
        while True:
            started_at = time.monotonic()
            waveform = self.reader.fetch_audio_waveform(duration_sec=1.5)
            self._latest_audio = self.audio_detector.analyze_waveform(waveform)
            time.sleep(max(0.0, 1.5 - (time.monotonic() - started_at)))

    def run_forever(self) -> None:
        interval = 1.0 / float(self.fps)
        threading.Thread(target=self.reader.capture_forever, daemon=True).start()
        threading.Thread(target=self._run_audio, daemon=True).start()
        print(f"[Edge:{self.baby_id}] Started System 1 monitoring loop at {self.fps} FPS (mode={self.subject_mode})")
        while True:
            t0 = time.monotonic()
            frame = self.reader.take_latest_frame()
            if frame is None:
                time.sleep(interval)
                continue

            status = self.step_once(frame)
            trigger = status.get("triggered")
            if trigger:
                # Export from the buffer while camera capture and analysis continue.
                metrics_copy = dict(status["edge_metrics"])
                threading.Thread(
                    target=self.handle_triggered_event,
                    args=(trigger, metrics_copy),
                    daemon=True,
                ).start()

            elapsed = time.monotonic() - t0
            time.sleep(max(0.005, interval - elapsed))


class EdgeStatusHandler(BaseHTTPRequestHandler):
    pipelines: dict[str, BabyMonitorPipeline] = {}

    def log_message(self, format: str, *args) -> None:
        pass

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        baby_id = qs.get("baby_id", ["baby_1"])[0]

        if path in ("/status", "/health"):
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            all_events: list[dict[str, Any]] = []
            for p in self.pipelines.values():
                all_events.extend(list(p.recent_events))
            data = {
                "status": "ok",
                "babies": {bid: p._latest_status for bid, p in self.pipelines.items()},
                "recent_events": all_events[:15],
            }
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/frame.jpeg":
            pipeline = self.pipelines.get(baby_id) or next(iter(self.pipelines.values()), None)
            frame = pipeline.reader.get_latest_frame() if pipeline else None
            if frame is None:
                self.send_error(503, "Camera frame not ready yet")
                return
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
            if not ok:
                self.send_error(500, "Failed to encode frame")
                return
            jpg_bytes = encoded.tobytes()
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpg_bytes)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(jpg_bytes)
            return

        if path == "/stream.mjpeg":
            pipeline = self.pipelines.get(baby_id) or next(iter(self.pipelines.values()), None)
            if pipeline is None:
                self.send_error(404, "Baby pipeline not found")
                return
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Age", "0")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            interval = 1.0 / float(max(1, pipeline.fps))
            try:
                while True:
                    t0 = time.time()
                    frame = pipeline.reader.get_latest_frame()
                    if frame is not None:
                        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                        if ok:
                            jpg_bytes = encoded.tobytes()
                            self.wfile.write(b"--frame\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(f"Content-Length: {len(jpg_bytes)}\r\n\r\n".encode("ascii"))
                            self.wfile.write(jpg_bytes)
                            self.wfile.write(b"\r\n")
                    elapsed = time.time() - t0
                    time.sleep(max(0.02, interval - elapsed))
            except (BrokenPipeError, ConnectionResetError):
                return

        self.send_error(404)


def main() -> None:
    cfg_path = Path(os.environ.get("EDGE_CONFIG_PATH", Path(__file__).resolve().parent.parent / "config.yaml"))
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    global_cfg = cfg.get("global", {})
    cloud_sync = CloudSyncClient()

    pipelines: dict[str, BabyMonitorPipeline] = {}
    for baby_cfg in cfg.get("babies", []):
        if not baby_cfg.get("enabled", False):
            continue
        pipeline = BabyMonitorPipeline(baby_cfg, global_cfg, cloud_sync)
        pipelines[pipeline.baby_id] = pipeline
        t = threading.Thread(target=pipeline.run_forever, daemon=True)
        t.start()

    EdgeStatusHandler.pipelines = pipelines
    status_port = int(os.environ.get("EDGE_STATUS_PORT", "8095"))
    server = ThreadingHTTPServer(("0.0.0.0", status_port), EdgeStatusHandler)
    print(f"[EdgeNode] Telemetry server listening on http://0.0.0.0:{status_port}/status")
    server.serve_forever()


if __name__ == "__main__":
    main()

