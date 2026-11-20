"""Stream Reader & Pre-Event In-Memory Ring Buffer for LullabAI Twins."""

from __future__ import annotations

from collections import deque
import io
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.request
import wave

import numpy as np

from cv_compat import cv2


class RingBufferStreamReader:
    """Reads video frames & audio chunks from go2rtc (real RTSP camera or mock-camera)."""

    def __init__(
        self,
        video_source_url: str,
        snapshot_url: str | None = None,
        audio_wav_url: str | None = None,
        rtsp_audio_url: str | None = None,
        fps: int = 12,
        pre_roll_seconds: float = 4.0,
        post_roll_seconds: float = 3.0,
    ) -> None:
        self.video_source_url = os.path.expandvars(video_source_url)
        self.snapshot_url = os.path.expandvars(snapshot_url) if snapshot_url else None
        self.audio_wav_url = os.path.expandvars(audio_wav_url) if audio_wav_url else None
        self.rtsp_audio_url = os.path.expandvars(rtsp_audio_url) if rtsp_audio_url else None
        self.fps = fps
        self.pre_roll_seconds = pre_roll_seconds

        max_frames = max(15, int(fps * (pre_roll_seconds + post_roll_seconds)) + 2)
        self._frame_buffer: deque[tuple[float, np.ndarray]] = deque(maxlen=max_frames)
        self._latest_capture: tuple[float, np.ndarray] | None = None
        self._lock = threading.Lock()
        self._cap: cv2.VideoCapture | None = None

    def _read_frame(self) -> np.ndarray | None:
        """Decode one frame from RTSP/MJPEG or the snapshot fallback."""
        frame: np.ndarray | None = None

        # 1. Try persistent OpenCV VideoCapture first (best for H.264 RTSP via go2rtc)
        if self.video_source_url:
            if self._cap is None or not self._cap.isOpened():
                os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
                if self.video_source_url.startswith("rtsp://"):
                    self._cap = cv2.VideoCapture(
                        self.video_source_url,
                        cv2.CAP_FFMPEG,
                        [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000],
                    )
                else:
                    self._cap = cv2.VideoCapture(self.video_source_url)
            ok, captured = self._cap.read()
            if ok and captured is not None:
                frame = captured
            else:
                if self._cap is not None:
                    self._cap.release()
                self._cap = None

        # 2. Fallback to HTTP frame snapshot endpoint (used by mock-camera or when RTSP is warming up)
        if frame is None and self.snapshot_url:
            try:
                with urllib.request.urlopen(self.snapshot_url, timeout=2.5) as resp:
                    raw = resp.read()
                arr = np.frombuffer(raw, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            except Exception:
                frame = None

        return frame

    def capture_forever(self) -> None:
        """Drain the camera stream at source rate and retain its latest frame."""
        while True:
            frame = self._read_frame()
            if frame is None:
                time.sleep(1.0)
                continue
            with self._lock:
                self._latest_capture = (time.monotonic(), frame)
            if self._cap is None:
                time.sleep(1.0 / self.fps)

    def take_latest_frame(self) -> np.ndarray | None:
        """Sample a new frame at the detector's rate and add it to the clip buffer."""
        with self._lock:
            capture = self._latest_capture
            if capture is None or (self._frame_buffer and capture[0] <= self._frame_buffer[-1][0]):
                return None
            self._frame_buffer.append(capture)
            return capture[1]

    def fetch_audio_waveform(self, duration_sec: float = 1.5) -> np.ndarray:
        """Fetches 16kHz mono float32 audio from RTSP (via ffmpeg) or HTTP WAV endpoint."""
        # 1. If a real RTSP audio stream URL is configured, sample 1.5s of 16kHz mono PCM via ffmpeg
        if self.rtsp_audio_url and self.rtsp_audio_url.startswith("rtsp://"):
            try:
                cmd = [
                    "ffmpeg",
                    "-rtsp_transport",
                    "tcp",
                    "-i",
                    self.rtsp_audio_url,
                    "-t",
                    f"{duration_sec:.1f}",
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-f",
                    "s16le",
                    "-loglevel",
                    "quiet",
                    "pipe:1",
                ]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=4.0, check=False)
                if proc.stdout:
                    pcm16 = np.frombuffer(proc.stdout, dtype=np.int16)
                    if pcm16.size > 0:
                        return (pcm16.astype(np.float32) / 32768.0).clip(-1.0, 1.0)
            except Exception:
                pass

        # 2. Fallback to HTTP WAV endpoint (go2rtc or mock-camera)
        if self.audio_wav_url:
            sep = "&" if "?" in self.audio_wav_url else "?"
            url = f"{self.audio_wav_url}{sep}duration={duration_sec:.1f}"
            try:
                with urllib.request.urlopen(url, timeout=3.0) as resp:
                    wav_bytes = resp.read()
                with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                    raw_pcm = wf.readframes(wf.getnframes())
                    pcm16 = np.frombuffer(raw_pcm, dtype=np.int16)
                    return (pcm16.astype(np.float32) / 32768.0).clip(-1.0, 1.0)
            except Exception:
                pass

        return np.zeros(int(16000 * duration_sec), dtype=np.float32)

    def get_buffered_frames(
        self, *, after: float | None = None, through: float | None = None
    ) -> list[np.ndarray]:
        end = time.monotonic() if through is None else through
        start = end - self.pre_roll_seconds if after is None else after
        with self._lock:
            frames = [frame for captured_at, frame in self._frame_buffer if start < captured_at <= end]
        return [frame.copy() for frame in frames]

    def get_latest_frame(self) -> np.ndarray | None:
        with self._lock:
            if not self._frame_buffer:
                return None
            return self._frame_buffer[-1][1].copy()

    def export_clip_and_snapshot(
        self,
        output_dir: Path,
        event_id: str,
        frames: list[np.ndarray] | None = None,
    ) -> tuple[Path, Path]:
        """Writes an MP4 video clip (pre-roll + post-roll) and a high-res JPEG thumbnail."""
        output_dir.mkdir(parents=True, exist_ok=True)
        if frames is None:
            frames = self.get_buffered_frames()

        if not frames:
            raise ValueError("Cannot export clip: ring buffer is empty")

        snapshot_path = output_dir / f"{event_id}_thumb.jpg"
        clip_path = output_dir / f"{event_id}.mp4"

        latest_frame = frames[-1]
        cv2.imwrite(str(snapshot_path), latest_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])

        h, w = latest_frame.shape[:2]
        if hasattr(cv2, "VideoWriter_fourcc"):
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(clip_path), fourcc, float(self.fps), (w, h))
            for frm in frames:
                if frm.shape[:2] != (h, w):
                    frm = cv2.resize(frm, (w, h))
                writer.write(frm)
            writer.release()
        else:
            # In pure-NumPy fallback mode without OpenCV VideoWriter, copy snapshot as placeholder
            clip_path.write_bytes(snapshot_path.read_bytes())

        return clip_path, snapshot_path

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
