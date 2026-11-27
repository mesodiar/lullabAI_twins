"""Main Orchestrator for LullabAI Twins Edge Node (System 1 Reflex)."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
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
from local_events import LocalEventStore, RECENT_EVENT_LIMIT
from motion import MotionDetector, MotionResult
from pet_cover import PetCoverDetector
from sleep_summary import BED_IDS, DEFAULT_SUMMARY_WINDOW, SUMMARY_WINDOWS, SleepSummaryStore
from stream_reader import RingBufferStreamReader
from web_push import WebPushService, subscription_fields

CV_ERROR = getattr(cv2, "error", RuntimeError)

DEFAULT_CLOUD_MIN_DURATIONS: dict[str, float] = {
    "CRY": 10.0,
    "MOTION": 10.0,
    "BAND_ROLLOVER": 0.0,
    "BREATHING_ANOMALY": 0.0,
}


class SustainedSignalTracker:
    """Tracks continuous active duration per signal key, allowing brief pauses."""

    def __init__(self, grace_seconds: float = 2.5) -> None:
        self.grace_seconds = max(0.0, grace_seconds)
        self._episodes: dict[str, tuple[float, float]] = {}
        self._escalated: set[str] = set()

    def update(self, key: str, active: bool, now: float) -> float:
        episode = self._episodes.get(key)
        if active:
            if episode is None or (now - episode[1]) > self.grace_seconds:
                self._episodes[key] = (now, now)
                self._escalated.discard(key)
                return 0.0
            started_at, _ = episode
            self._episodes[key] = (started_at, now)
            return max(0.0, now - started_at)
        if episode is not None and (now - episode[1]) > self.grace_seconds:
            self._episodes.pop(key, None)
            self._escalated.discard(key)
        return 0.0

    def duration(self, key: str, now: float) -> float:
        episode = self._episodes.get(key)
        if episode is None or (now - episode[1]) > self.grace_seconds:
            return 0.0
        return max(0.0, now - episode[0])

    def mark_escalated(self, key: str) -> bool:
        if key in self._escalated:
            return False
        self._escalated.add(key)
        return True

    def is_active(self, key: str, now: float) -> bool:
        episode = self._episodes.get(key)
        return episode is not None and (now - episode[1]) <= self.grace_seconds


class BabyMonitorPipeline:
    """Runs System 1 Reflex (Breathing Band + Motion + YAMNet Cry) for one crib."""

    def __init__(
        self,
        baby_cfg: dict[str, Any],
        global_cfg: dict[str, Any],
        event_store: LocalEventStore,
        web_push: WebPushService | None = None,
        cloud_sync: CloudSyncClient | None = None,
    ) -> None:
        self.baby_id: str = baby_cfg["baby_id"]
        self.camera_id: str = baby_cfg["camera_id"]
        self.subject_mode: str = os.environ.get("SUBJECT_MODE") or global_cfg.get("subject_mode", "PET_TEST")
        self.fps: int = int(global_cfg.get("target_fps", 12))
        self.post_roll_seconds: float = float(global_cfg.get("post_roll_seconds", 3.0))
        self.cooldowns: dict[str, float] = global_cfg.get(
            "cooldowns",
            {"CRY": 30.0, "BAND_ROLLOVER": 25.0, "BREATHING_ANOMALY": 25.0, "MOTION": 60.0},
        )
        self.cloud_min_durations: dict[str, float] = {
            **DEFAULT_CLOUD_MIN_DURATIONS,
            **{k: float(v) for k, v in (global_cfg.get("cloud_review_min_duration_seconds") or {}).items()},
        }
        self._sustained_tracker = SustainedSignalTracker(
            grace_seconds=float(global_cfg.get("cloud_review_grace_seconds", 2.5))
        )
        self._episode_lock = threading.Lock()
        self._episode_events: dict[str, dict[str, Any]] = {}
        self._escalated_event_ids: set[str] = set()
        self.event_store = event_store
        self.web_push = web_push
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
            audio_wav_url=None if env_sub_rtsp else baby_cfg.get("audio_wav_url"),
            rtsp_audio_url=rtsp_audio_url,
            fps=self.fps,
            pre_roll_seconds=float(global_cfg.get("pre_roll_seconds", 4.0)),
            post_roll_seconds=self.post_roll_seconds,
        )
        self.band_tracker = BreathingBandTracker(
            min_markers=int(thresholds.get("band_min_markers", 8)),
            apnea_low_rpm_threshold=float(thresholds.get("apnea_low_rpm", 8.0)),
        )
        self.pet_cover_detector = PetCoverDetector() if baby_cfg.get("pet_cover_detection", False) else None
        self._cover_alerted = False
        self.motion_detector = MotionDetector(
            roi_normalized=roi,  # type: ignore[arg-type]
            motion_threshold=float(thresholds.get("motion_score", 4.5)),
        )
        self.audio_detector = AudioCryDetector(
            cry_threshold=float(thresholds.get("cry_prob", 0.55)),
        )

        self._last_alert_at: dict[str, float] = {}
        self._latest_audio: AudioDetectionResult = AudioDetectionResult(0.0, "Silence", 0.0, False)
        self._latest_status: dict[str, Any] = {}
        self.recent_events: deque[dict[str, Any]] = deque(maxlen=RECENT_EVENT_LIMIT)

    def evaluate_triggers(
        self,
        band_res: BandTrackingResult,
        motion_res: MotionResult,
        audio_res: AudioDetectionResult,
        now: float,
        *,
        face_cover_suspected: bool = False,
    ) -> str | None:
        """Determines highest-priority trigger type respecting per-type cooldowns."""
        # If an ADULT is detected in front of the camera, suppress baby motion triggers
        candidates: list[str] = []
        if not face_cover_suspected:
            self._cover_alerted = False
        if face_cover_suspected and not self._cover_alerted:
            candidates.append("BAND_ROLLOVER")
        if band_res.subject_type != "ADULT":
            if band_res.rollover_suspected and not face_cover_suspected:
                candidates.append("BAND_ROLLOVER")
            if audio_res.cry_triggered:
                candidates.append("CRY")
            if motion_res.motion_triggered and band_res.subject_type in ("BABY", "PET"):
                candidates.append("MOTION")

        for trigger in candidates:
            cooldown = float(self.cooldowns.get(trigger, 30.0))
            cooldown_key = (audio_res.audio_kind or trigger) if trigger == "CRY" else trigger
            last_time = self._last_alert_at.get(cooldown_key, 0.0)
            if (now - last_time) >= cooldown:
                self._last_alert_at[cooldown_key] = now
                if trigger == "BAND_ROLLOVER" and face_cover_suspected:
                    self._cover_alerted = True
                return trigger

        return None

    def _signal_key(self, trigger_type: str, audio_kind: str | None) -> str:
        return (audio_kind or trigger_type) if trigger_type == "CRY" else trigger_type

    def _update_sustained_signals(
        self,
        band_res: BandTrackingResult,
        motion_res: MotionResult,
        audio_res: AudioDetectionResult,
        now: float,
    ) -> None:
        """Tracks active CRY/MOTION episodes and escalates to Vertex AI once sustained past threshold."""
        active_cry_key = (audio_res.audio_kind or "CRY") if audio_res.cry_triggered else None
        for cry_key in ("BABY_CRY", "DOG_VOCALIZATION", "CRY"):
            duration = self._sustained_tracker.update(cry_key, active_cry_key == cry_key, now)
            if not self._sustained_tracker.is_active(cry_key, now):
                with self._episode_lock:
                    self._episode_events.pop(cry_key, None)
            elif duration >= self.cloud_min_durations.get("CRY", 10.0):
                self._escalate_episode(cry_key, duration)

        motion_active = bool(
            band_res.subject_type != "ADULT"
            and motion_res.motion_triggered
            and band_res.subject_type in ("BABY", "PET")
        )
        motion_duration = self._sustained_tracker.update("MOTION", motion_active, now)
        if not self._sustained_tracker.is_active("MOTION", now):
            with self._episode_lock:
                self._episode_events.pop("MOTION", None)
        elif motion_duration >= self.cloud_min_durations.get("MOTION", 10.0):
            self._escalate_episode("MOTION", motion_duration)

    def _escalate_episode(self, signal_key: str, duration: float) -> None:
        if not self._sustained_tracker.mark_escalated(signal_key):
            return
        with self._episode_lock:
            event_doc = self._episode_events.get(signal_key)
            if event_doc is None:
                return
            self._escalated_event_ids.add(event_doc["event_id"])
            event_doc["edge_metrics"]["sustained_duration_s"] = round(duration, 1)
            has_media = bool(event_doc.get("media"))
            already_queued = "cloud_review" in event_doc
        if has_media and not already_queued and self.cloud_sync is not None:
            print(
                f"[Edge:{self.baby_id}] ☁️ Escalating sustained {signal_key} "
                f"({duration:.1f}s) to Vertex AI (id={event_doc['event_id']})"
            )
            self.event_store.save(event_doc)
            self.cloud_sync.enqueue(event_doc)

    def step_once(self, frame_bgr: np.ndarray, audio_waveform: np.ndarray | None = None) -> dict[str, Any]:
        """Processes one frame (+ optional audio chunk) and returns current telemetry & any triggered event."""
        now = time.time()
        band_res = self.band_tracker.process_frame(frame_bgr, timestamp=now)
        motion_res = self.motion_detector.process_frame(frame_bgr)
        face_cover_suspected = self.pet_cover_detector is not None and self.pet_cover_detector.process_frame(
            frame_bgr,
            band_res.band_bbox if band_res.subject_type == "PET" else None,
            time.monotonic(),
        )

        if audio_waveform is not None:
            self._latest_audio = self.audio_detector.analyze_waveform(audio_waveform)

        audio_res = self._latest_audio
        self._update_sustained_signals(band_res, motion_res, audio_res, now)
        trigger = self.evaluate_triggers(
            band_res, motion_res, audio_res, now, face_cover_suspected=face_cover_suspected
        )

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
            "audio_kind": audio_res.audio_kind,
            "audio_classifier_ready": self.audio_detector.ready,
            "motion_score": motion_res.motion_score,
            "band_detected": band_res.band_detected,
            "band_confidence": band_res.band_confidence,
            "breathing_rpm": None,
            "band_bbox": band_res.band_bbox,
            "band_bbox_pct": band_bbox_pct,
            "subject_type": band_res.subject_type,
            "face_cover_suspected": face_cover_suspected,
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
        """Publishes the local detection immediately, then records its media on disk."""
        started_at = time.monotonic()
        event_id = f"evt_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        event_doc: dict[str, Any] = {
            "event_id": event_id,
            "baby_id": self.baby_id,
            "camera_id": self.camera_id,
            "subject_mode": self.subject_mode,
            "trigger_type": trigger_type,
            "status": "EDGE_DETECTED",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "analyzed_at": None,
            "edge_metrics": metrics,
            "media": {},
            "ai_analysis": None,
        }

        print(f"[Edge:{self.baby_id}] ⚡ Trigger={trigger_type} (id={event_id}) | Metrics={metrics}")
        self.recent_events.appendleft(event_doc)

        signal_key = self._signal_key(trigger_type, metrics.get("audio_kind"))
        min_duration = float(self.cloud_min_durations.get(trigger_type, 0.0))
        immediate_cloud = min_duration <= 0.0
        if not immediate_cloud:
            with self._episode_lock:
                self._episode_events[signal_key] = event_doc

        # Keep the pre-roll frames before the ring buffer advances during the alert.
        frames = self.reader.get_buffered_frames(through=started_at)
        try:
            self.event_store.save(event_doc)
        except OSError as exc:
            print(f"[Edge:{self.baby_id}] Could not save event {event_id}: {exc}")
        if self.web_push is not None and not (self.cloud_sync is not None and self.cloud_sync.enabled):
            self.web_push.submit(event_doc)

        deadline = started_at + self.post_roll_seconds
        time.sleep(max(0.0, deadline - time.monotonic()))
        frames.extend(self.reader.get_buffered_frames(after=started_at, through=deadline))

        if frames:
            try:
                # Stage inside the event volume so completed media can be published atomically.
                with tempfile.TemporaryDirectory(prefix=".capture_", dir=self.event_store.directory) as tmp_dir:
                    clip_path, snap_path, audio_path = self.reader.export_clip_and_snapshot(
                        Path(tmp_dir), event_id=event_id, frames=frames, include_audio=trigger_type == "CRY"
                    )
                    media = self.event_store.save_media(clip_path, snap_path, audio_path)
                    with self._episode_lock:
                        event_doc["media"] = media
                        should_enqueue = immediate_cloud or event_id in self._escalated_event_ids
                    self.event_store.save(event_doc)
                    if self.cloud_sync is not None and should_enqueue and "cloud_review" not in event_doc:
                        self.cloud_sync.enqueue(event_doc)
            except (OSError, ValueError, CV_ERROR) as exc:
                print(f"[Edge:{self.baby_id}] Could not save media for {event_id}: {exc}")

        return event_doc

    def _run_audio(self) -> None:
        while True:
            started_at = time.monotonic()
            audio = self.reader.get_buffered_audio(started_at - 1.5, started_at)
            waveform = audio.astype(np.float32) / 32768.0 if audio is not None else np.empty(0, dtype=np.float32)
            self._latest_audio = self.audio_detector.analyze_waveform(waveform)
            # Overlap reads so a short vocalization can span two reads.
            time.sleep(max(0.0, 0.75 - (time.monotonic() - started_at)))

    def run_forever(self) -> None:
        interval = 1.0 / float(self.fps)
        threading.Thread(target=self.reader.capture_forever, daemon=True).start()
        threading.Thread(target=self.reader.capture_audio_forever, daemon=True).start()
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
    event_store: LocalEventStore
    summary_store: SleepSummaryStore | None = None
    web_push: WebPushService | None = None
    cloud_sync: CloudSyncClient | None = None
    settings_setup_code: str = ""

    def log_message(self, format: str, *args) -> None:
        pass

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")

    def _send_media(self, name: str, *, head_only: bool = False) -> None:
        path = self.event_store.media_path(name)
        if path is None:
            self.send_error(404)
            return

        try:
            source = path.open("rb")
        except FileNotFoundError:
            self.send_error(404)
            return
        with source:
            size = os.fstat(source.fileno()).st_size
            start, end = 0, size - 1
            byte_range = self.headers.get("Range")
            if byte_range and not head_only:
                match = re.fullmatch(r"bytes=([0-9]{0,20})-([0-9]{0,20})", byte_range)
                if match and any(match.groups()):
                    first, last = match.groups()
                    if first:
                        start = int(first)
                        if last:
                            end = min(end, int(last))
                    else:
                        start = max(0, size - int(last))
                if not match or not any(match.groups()) or start > end:
                    self.send_response(416)
                    self._send_cors_headers()
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

            partial = bool(byte_range and not head_only)
            self.send_response(206 if partial else 200)
            self._send_cors_headers()
            self.send_header("Content-Type", "video/mp4" if path.suffix == ".mp4" else "image/jpeg")
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if not head_only:
                source.seek(start)
                remaining = end - start + 1
                try:
                    while remaining:
                        chunk = source.read(min(remaining, 64 * 1024))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

    def do_HEAD(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/media/"):
            self._send_media(path.removeprefix("/media/"), head_only=True)
        else:
            self.send_error(404)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def _send_json(self, status: int, value: dict[str, Any], *, cors: bool = False) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if cors:
            self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _push_subscription(self, action: str) -> None:
        service = self.web_push
        if service is None:
            self._send_json(503, {"error": "Web Push is not configured"})
            return
        if action == "enroll" and not service.can_enroll(self.headers.get("X-Setup-Code", "")):
            self._send_json(403, {"error": "Invalid setup code"})
            return
        if self.headers.get_content_type() != "application/json":
            self._send_json(415, {"error": "Expected a JSON subscription"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("Invalid subscription length")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Invalid subscription")
            fields = subscription_fields(body)
        except (ValueError, OSError):
            self._send_json(400, {"error": "Invalid push subscription"})
            return
        try:
            if action == "enroll":
                service.enroll(fields)
            elif action == "remove":
                service.remove(fields)
            elif action == "test":
                if not service.contains(fields):
                    self._send_json(403, {"error": "This device is not enrolled for background alerts."})
                    return
                service.submit_test(str(body.get("baby_id") or "baby_1"))
            else:
                self._send_json(200, {"registered": service.contains(fields)})
                return
        except sqlite3.Error:
            self._send_json(503, {"error": "Subscription storage unavailable"})
            return
        self._send_json(200, {"ok": True})

    def _read_settings_json(self) -> dict[str, Any] | None:
        code = self.headers.get("X-Setup-Code", "")
        if not self.settings_setup_code or not compare_digest(code.encode(), self.settings_setup_code.encode()):
            self._send_json(403, {"error": "Invalid Edge setup code."})
            return None
        if self.headers.get_content_type() != "application/json":
            self._send_json(415, {"error": "Expected a JSON setting."})
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 256:
                raise ValueError("Invalid setting length")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Expected a settings object")
        except (ValueError, OSError):
            self._send_json(400, {"error": "Invalid settings request."})
            return None
        return body

    def _set_cloud_reviews(self) -> None:
        cloud = self.cloud_sync
        if cloud is None:
            self._send_json(503, {"error": "GCP is unavailable on Edge."})
            return
        body = self._read_settings_json()
        if body is None:
            return
        if not isinstance(body.get("enabled"), bool):
            self._send_json(400, {"error": "Choose a valid analysis mode."})
            return
        try:
            cloud.set_enabled(body["enabled"])
        except OSError:
            self._send_json(503, {"error": "Edge could not save the analysis mode."})
            return
        self._send_json(200, {"enabled": cloud.enabled})

    def _set_video_retention(self) -> None:
        body = self._read_settings_json()
        if body is None:
            return
        try:
            self.event_store.set_video_retention(body["days"])
        except (KeyError, ValueError):
            self._send_json(400, {"error": "Choose a valid video retention period."})
            return
        except OSError:
            self._send_json(503, {"error": "Edge could not save the video retention setting."})
            return
        self._send_json(200, {"days": self.event_store.video_retention_days})

    def _get_summary_store(self) -> SleepSummaryStore:
        if self.summary_store is None:
            return SleepSummaryStore(self.event_store)
        return self.summary_store

    def _sleep_summary_response(self, baby_id: str, hours: int) -> dict[str, Any]:
        store = self._get_summary_store()
        digest = store.build_digest(baby_id, hours)
        saved = store.load_saved(baby_id, hours)
        saved_view = None
        if saved is not None:
            saved_view = {
                "summary_id": saved.get("summary_id"),
                "created_at": saved.get("created_at"),
                "analyzed_at": saved.get("analyzed_at"),
                "status": saved.get("status"),
                "ai_summary": saved.get("ai_summary"),
            }
        return {
            "baby_id": baby_id,
            "window_hours": hours,
            "window_start": digest["window_start"],
            "window_end": digest["window_end"],
            "subject_mode": digest["subject_mode"],
            "stats": digest["stats"],
            "hourly_activity": digest["hourly_activity"],
            "other_bed": digest["other_bed"],
            "local_summary": digest["local_summary"],
            "saved_summary": saved_view,
            "cloud_reviews_available": self.cloud_sync is not None,
            "cloud_reviews_enabled": self.cloud_sync is not None and self.cloud_sync.enabled,
        }

    def _request_sleep_summary(self) -> None:
        if self.headers.get_content_type() != "application/json":
            self._send_json(415, {"error": "Expected a JSON request."}, cors=True)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 512:
                raise ValueError("Invalid request length")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Expected an object")
            baby_id = str(body.get("baby_id") or "baby_1")
            hours = int(body.get("hours", DEFAULT_SUMMARY_WINDOW))
            if baby_id not in BED_IDS or hours not in SUMMARY_WINDOWS:
                raise ValueError("Invalid baby_id or hours")
        except (ValueError, OSError):
            self._send_json(400, {"error": "Choose a valid bed and time window."}, cors=True)
            return

        store = self._get_summary_store()
        request_doc = store.create_request(baby_id, hours)
        try:
            if self.cloud_sync is not None and self.cloud_sync.enabled:
                self.cloud_sync.enqueue_sleep_summary(request_doc)
            else:
                store.save_summary(request_doc)
        except OSError:
            self._send_json(503, {"error": "Edge could not save the sleep summary."}, cors=True)
            return
        self._send_json(200, self._sleep_summary_response(baby_id, hours), cors=True)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/push/subscriptions":
            self._push_subscription("enroll")
        elif path == "/push/subscription-status":
            self._push_subscription("status")
        elif path == "/push/test":
            self._push_subscription("test")
        elif path == "/cloud-review":
            self._set_cloud_reviews()
        elif path == "/video-retention":
            self._set_video_retention()
        elif path == "/sleep-summary":
            self._request_sleep_summary()
        else:
            self.send_error(404)

    def do_DELETE(self) -> None:
        if urlparse(self.path).path == "/push/subscriptions":
            self._push_subscription("remove")
        else:
            self.send_error(404)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        baby_id = qs.get("baby_id", ["baby_1"])[0]

        if path == "/push/config":
            self._send_json(200, {"publicKey": self.web_push.public_key if self.web_push else None})
            return

        if path.startswith("/media/"):
            self._send_media(path.removeprefix("/media/"))
            return

        if path == "/sleep-summary":
            try:
                hours = int(qs.get("hours", [str(DEFAULT_SUMMARY_WINDOW)])[0])
                if baby_id not in BED_IDS or hours not in SUMMARY_WINDOWS:
                    raise ValueError("Invalid parameters")
            except ValueError:
                self._send_json(400, {"error": "Choose a valid bed and time window."}, cors=True)
                return
            self._send_json(200, self._sleep_summary_response(baby_id, hours), cors=True)
            return

        if path in ("/status", "/health"):
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            all_events: list[dict[str, Any]] = []
            for p in self.pipelines.values():
                all_events.extend(p.recent_events.copy())
            all_events.sort(key=lambda event: event["created_at"], reverse=True)
            if self.cloud_sync is not None:
                all_events = [self.cloud_sync.with_review(event) for event in all_events]
            data = {
                "status": "ok",
                "cloud_reviews_available": self.cloud_sync is not None,
                "cloud_reviews_enabled": self.cloud_sync is not None and self.cloud_sync.enabled,
                "cloud_review_setup_configured": bool(self.settings_setup_code),
                "video_retention_days": self.event_store.video_retention_days,
                "babies": {bid: p._latest_status for bid, p in self.pipelines.items()},
                "recent_events": [self.event_store.with_available_video(event) for event in all_events],
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
    event_store = LocalEventStore()
    threading.Thread(target=event_store.run_video_retention, name="video-retention", daemon=True).start()
    web_push = WebPushService.from_env(event_store.directory)
    cloud_sync = CloudSyncClient.from_env(event_store, web_push=web_push)

    pipelines: dict[str, BabyMonitorPipeline] = {}
    for baby_cfg in cfg.get("babies", []):
        if not baby_cfg.get("enabled", False):
            continue
        pipeline = BabyMonitorPipeline(baby_cfg, global_cfg, event_store, web_push, cloud_sync)
        pipeline.recent_events.extend(event_store.recent(pipeline.baby_id))
        pipelines[pipeline.baby_id] = pipeline
        t = threading.Thread(target=pipeline.run_forever, daemon=True)
        t.start()

    EdgeStatusHandler.pipelines = pipelines
    EdgeStatusHandler.event_store = event_store
    EdgeStatusHandler.summary_store = SleepSummaryStore(event_store)
    EdgeStatusHandler.web_push = web_push
    EdgeStatusHandler.cloud_sync = cloud_sync
    EdgeStatusHandler.settings_setup_code = os.environ.get("WEB_PUSH_SETUP_CODE", "")
    status_port = int(os.environ.get("EDGE_STATUS_PORT", "8095"))
    server = ThreadingHTTPServer(("0.0.0.0", status_port), EdgeStatusHandler)
    print(f"[EdgeNode] Telemetry server listening on http://0.0.0.0:{status_port}/status")
    server.serve_forever()


if __name__ == "__main__":
    main()

