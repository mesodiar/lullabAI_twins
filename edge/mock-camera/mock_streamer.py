"""Mock Camera Server for LullabAI Twins.

Takes the sample photo of a baby wearing a Nanit-style geometric breathing band
(`edge/assets/sample_baby_band.jpg`) and generates a real-time video & audio feed:
- Simulates realistic chest/band breathing expansion at configurable RPM (default 32 BPM)
- Supports runtime simulation modes via HTTP API:
  - NORMAL: Calm breathing (32 RPM) + quiet room audio
  - CRY: Vigorous movement + harmonic infant cry / dog whimper audio burst
  - MOTION: Large torso shift inside the crib ROI
  - ROLLOVER: Occludes/flips the breathing band pattern (simulating rolling onto stomach)
  - APNEA: Stops chest breathing oscillation (0 RPM) to test breathing anomaly alert
"""

from __future__ import annotations

import io
import json
import math
import os
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlparse

import sys
import numpy as np

try:
    import cv2  # type: ignore[import-untyped]
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "detector" / "src"))
    from cv_compat import cv2  # type: ignore[no-redef]

SimulationMode = Literal["NORMAL", "CRY", "MOTION", "ROLLOVER", "APNEA"]


class MockCameraState:
    def __init__(self, image_path: Path) -> None:
        self.lock = threading.Lock()
        self.mode: SimulationMode = "NORMAL"
        self.target_rpm: float = 32.0
        self.fps: int = 15
        self.start_time: float = time.time()

        if not image_path.exists():
            raise FileNotFoundError(f"Sample image not found at {image_path}")

        raw_img = cv2.imread(str(image_path))
        if raw_img is None:
            raise ValueError(f"Failed to decode image at {image_path}")

        # Standardize resolution to 960x720 (4:3 aspect ratio matching original 1024x768)
        self.base_frame = cv2.resize(raw_img, (960, 720), interpolation=cv2.INTER_AREA)
        self.height, self.width = self.base_frame.shape[:2]

        # Precompute meshgrid for smooth radial breathing warp around the chest band
        # In sample_baby_band.jpg, the geometric band spans roughly:
        # x: 34% to 60%, y: 55% to 82%
        self.band_center_x = int(self.width * 0.47)
        self.band_center_y = int(self.height * 0.68)
        self.band_radius_x = int(self.width * 0.16)
        self.band_radius_y = int(self.height * 0.15)

        y_idx, x_idx = np.indices((self.height, self.width), dtype=np.float32)
        self.grid_x = x_idx
        self.grid_y = y_idx

        dx = (x_idx - self.band_center_x) / float(self.band_radius_x)
        dy = (y_idx - self.band_center_y) / float(self.band_radius_y)
        dist_sq = dx * dx + dy * dy
        # Smooth Gaussian weight localized to the chest band
        self.warp_weight = np.exp(-dist_sq * 1.5).astype(np.float32)
        self.dx_norm = dx.astype(np.float32)
        self.dy_norm = dy.astype(np.float32)

    def set_mode(self, mode: SimulationMode, rpm: float | None = None) -> dict:
        with self.lock:
            self.mode = mode
            if rpm is not None:
                self.target_rpm = float(rpm)
            elif mode == "NORMAL":
                self.target_rpm = 32.0
            elif mode == "CRY":
                self.target_rpm = 48.0
            elif mode == "APNEA":
                self.target_rpm = 0.0
            return {"mode": self.mode, "target_rpm": self.target_rpm}

    def render_frame(self, now: float | None = None) -> np.ndarray:
        with self.lock:
            mode = self.mode
            rpm = self.target_rpm

        current_t = now if now is not None else time.time()
        elapsed = current_t - self.start_time
        freq_hz = rpm / 60.0
        breath_phase = math.sin(2.0 * math.pi * freq_hz * elapsed)

        frame = self.base_frame.copy()

        if mode == "ROLLOVER":
            # Simulate baby/pet rolled onto stomach: band pattern is hidden/occluded by back fabric
            x1 = int(self.width * 0.33)
            x2 = int(self.width * 0.61)
            y1 = int(self.height * 0.54)
            y2 = int(self.height * 0.83)
            # Fill band area with plain white fabric texture (matching onesie)
            frame[y1:y2, x1:x2] = (235, 240, 245)
        else:
            # Apply vertical & radial breathing displacement on the chest band
            amplitude_px = 3.8 if mode != "APNEA" else 0.0
            if mode == "CRY":
                amplitude_px = 7.5

            shift_y = breath_phase * amplitude_px * self.warp_weight
            shift_x = breath_phase * (amplitude_px * 0.4) * self.dx_norm * self.warp_weight

            map_x = self.grid_x - shift_x
            map_y = self.grid_y - shift_y

            if mode in ("MOTION", "CRY"):
                # Add whole-body wiggle to trigger motion detector
                wiggle_x = math.sin(elapsed * 9.0) * 14.0
                wiggle_y = math.cos(elapsed * 7.0) * 10.0
                map_x += wiggle_x * self.warp_weight
                map_y += wiggle_y * self.warp_weight

            frame = cv2.remap(
                frame,
                map_x,
                map_y,
                interpolation=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REFLECT_101,
            )

        # Overlay subtle timestamp & simulation status badge in top-left corner
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S")
        badge_text = f"LullabAI MockCam | Mode: {mode} | Sim RPM: {rpm:.0f} | {ts_str}"
        cv2.rectangle(frame, (12, 12), (610, 44), (20, 20, 20), -1)
        cv2.putText(
            frame,
            badge_text,
            (22, 34),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (120, 255, 160) if mode == "NORMAL" else (80, 180, 255),
            1,
            cv2.LINE_AA,
        )
        return frame

    def generate_audio_samples(self, duration_sec: float = 1.0, sample_rate: int = 16000) -> np.ndarray:
        """Generates 16kHz mono float32 audio samples in [-1.0, 1.0]."""
        with self.lock:
            mode = self.mode

        num_samples = int(duration_sec * sample_rate)
        t = np.linspace(0, duration_sec, num_samples, endpoint=False, dtype=np.float32)

        if mode == "CRY":
            # Synthesize an infant cry / dog whimper harmonic burst (fundamental ~440-520 Hz with FM vibrato + harmonics)
            f0 = 460.0 + 45.0 * np.sin(2.0 * np.pi * 4.5 * t)
            phase = 2.0 * np.pi * np.cumsum(f0) / sample_rate
            signal = (
                0.45 * np.sin(phase)
                + 0.30 * np.sin(2.0 * phase)
                + 0.15 * np.sin(3.0 * phase)
                + 0.08 * np.sin(4.0 * phase)
            )
            # Envelope modulation (sob cadence)
            envelope = 0.65 + 0.35 * np.sin(2.0 * np.pi * 2.2 * t)
            noise = np.random.normal(0.0, 0.03, size=num_samples).astype(np.float32)
            return np.clip(signal * envelope + noise, -0.98, 0.98).astype(np.float32)

        # Quiet room ambient floor
        return np.random.normal(0.0, 0.004, size=num_samples).astype(np.float32)


def make_wav_bytes(samples_float32: np.ndarray, sample_rate: int = 16000) -> bytes:
    pcm16 = (np.clip(samples_float32, -1.0, 1.0) * 32767.0).astype(np.int16)
    raw_bytes = pcm16.tobytes()
    buf = io.BytesIO()
    # Write standard RIFF WAV header
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + len(raw_bytes)))
    buf.write(b"WAVEfmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", len(raw_bytes)))
    buf.write(raw_bytes)
    return buf.getvalue()


class MockCameraHandler(BaseHTTPRequestHandler):
    camera_state: MockCameraState

    def log_message(self, format: str, *args) -> None:
        # Suppress noisy per-frame HTTP logs
        pass

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {
                "status": "ok",
                "mode": self.camera_state.mode,
                "target_rpm": self.camera_state.target_rpm,
            }
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        if path == "/frame.jpeg":
            frame = self.camera_state.render_frame()
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
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

        if path == "/audio.wav":
            qs = parse_qs(parsed.query)
            duration = float(qs.get("duration", ["1.5"])[0])
            samples = self.camera_state.generate_audio_samples(duration_sec=min(duration, 10.0))
            wav_bytes = make_wav_bytes(samples)
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav_bytes)))
            self.end_headers()
            self.wfile.write(wav_bytes)
            return

        if path == "/stream.mjpeg":
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Age", "0")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            interval = 1.0 / float(self.camera_state.fps)
            try:
                while True:
                    t0 = time.time()
                    frame = self.camera_state.render_frame()
                    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                    if ok:
                        jpg_bytes = encoded.tobytes()
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(f"Content-Length: {len(jpg_bytes)}\r\n\r\n".encode("ascii"))
                        self.wfile.write(jpg_bytes)
                        self.wfile.write(b"\r\n")
                    elapsed = time.time() - t0
                    time.sleep(max(0.005, interval - elapsed))
            except (BrokenPipeError, ConnectionResetError):
                return

        self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/simulate/"):
            requested_mode = parsed.path.split("/simulate/")[-1].upper()
            if requested_mode not in ("NORMAL", "CRY", "MOTION", "ROLLOVER", "APNEA"):
                self.send_error(400, f"Unsupported mode: {requested_mode}")
                return
            qs = parse_qs(parsed.query)
            rpm = float(qs["rpm"][0]) if "rpm" in qs else None
            updated = self.camera_state.set_mode(requested_mode, rpm=rpm)  # type: ignore[arg-type]
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "updated", **updated}).encode("utf-8"))
            return

        self.send_error(404, "Not Found")


def main() -> None:
    default_img = Path(__file__).resolve().parent.parent / "assets" / "sample_baby_band.jpg"
    img_path = Path(os.environ.get("SAMPLE_IMAGE_PATH", str(default_img)))
    port = int(os.environ.get("MOCK_CAMERA_PORT", "8090"))

    state = MockCameraState(img_path)
    MockCameraHandler.camera_state = state

    server = ThreadingHTTPServer(("0.0.0.0", port), MockCameraHandler)
    print(f"[MockCamera] Streaming {img_path.name} on http://0.0.0.0:{port}/stream.mjpeg")
    print(f"[MockCamera] Control modes via POST http://localhost:{port}/simulate/{{NORMAL|CRY|MOTION|ROLLOVER|APNEA}}")
    server.serve_forever()


if __name__ == "__main__":
    main()
