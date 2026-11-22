"""Stream Reader & Pre-Event In-Memory Ring Buffer for LullabAI Twins."""

from __future__ import annotations

from collections import deque
import io
import os
from pathlib import Path
import select
import subprocess
import threading
import time
import urllib.request
import wave

import numpy as np

from cv_compat import cv2

AUDIO_SAMPLE_RATE = 16_000
TimedFrame = tuple[float, np.ndarray]


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
        self._frame_buffer: deque[TimedFrame] = deque(maxlen=max_frames)
        self._latest_capture: TimedFrame | None = None
        self._lock = threading.Lock()
        self._audio_buffer: deque[tuple[float, np.ndarray]] = deque()
        self._audio_lock = threading.Lock()
        self._audio_buffer_seconds = max(1.5, pre_roll_seconds + post_roll_seconds) + 3
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

    def _buffer_audio(self, started_at: float, samples: np.ndarray) -> None:
        with self._audio_lock:
            self._audio_buffer.append((started_at, samples))
            cutoff = started_at - self._audio_buffer_seconds
            while self._audio_buffer:
                first_at, first_samples = self._audio_buffer[0]
                if first_at + len(first_samples) / AUDIO_SAMPLE_RATE >= cutoff:
                    break
                self._audio_buffer.popleft()

    def _capture_rtsp_audio(self) -> None:
        command = [
            "ffmpeg", "-nostdin", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-analyzeduration", "100000", "-probesize", "32768",
            "-i", self.rtsp_audio_url, "-map", "0:a:0",
            "-ac", "1", "-ar", str(AUDIO_SAMPLE_RATE), "-f", "s16le", "-flush_packets", "1", "pipe:1",
        ]
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0
        ) as process:
            assert process.stdout is not None
            next_sample_at: float | None = None
            remainder = b""
            try:
                # Stop and reconnect if the camera stops producing audio.
                while select.select([process.stdout], [], [], 5)[0]:
                    data = process.stdout.read(4096)
                    if not data:
                        break
                    data = remainder + data
                    complete_bytes = len(data) - len(data) % 2
                    remainder = data[complete_bytes:]
                    if not complete_bytes:
                        continue
                    samples = np.frombuffer(data[:complete_bytes], dtype="<i2")
                    duration = len(samples) / AUDIO_SAMPLE_RATE
                    started_at = time.monotonic() - duration
                    if next_sample_at is not None and abs(started_at - next_sample_at) < 0.4:
                        started_at = next_sample_at
                    self._buffer_audio(started_at, samples)
                    next_sample_at = started_at + duration
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()

    def _capture_wav_audio(self, duration: float) -> None:
        url = self.audio_wav_url
        if url is None:
            return
        separator = "&" if "?" in url else "?"
        with urllib.request.urlopen(f"{url}{separator}duration={duration:.1f}", timeout=3) as response:
            content = response.read()
        with wave.open(io.BytesIO(content), "rb") as source:
            if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (AUDIO_SAMPLE_RATE, 1, 2):
                return
            samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
        if samples.size:
            self._buffer_audio(time.monotonic() - len(samples) / AUDIO_SAMPLE_RATE, samples)

    def capture_audio_forever(self) -> None:
        """Drain the microphone once for both the detector and event clips."""
        if not self.rtsp_audio_url and not self.audio_wav_url:
            return
        while True:
            if self.rtsp_audio_url and self.rtsp_audio_url.startswith("rtsp://"):
                try:
                    self._capture_rtsp_audio()
                except OSError:
                    # The camera or FFmpeg may be temporarily unavailable.
                    pass
            if not self.audio_wav_url:
                time.sleep(1)
                continue
            started_at = time.monotonic()
            try:
                self._capture_wav_audio(1.5)
            except (OSError, EOFError, ValueError, wave.Error):
                pass
            time.sleep(max(0.0, 1.5 - (time.monotonic() - started_at)))

    def get_buffered_audio(self, start: float, end: float) -> np.ndarray | None:
        count = max(0, round((end - start) * AUDIO_SAMPLE_RATE))
        with self._audio_lock:
            blocks = list(self._audio_buffer)
        audio: np.ndarray | None = None
        for captured_at, samples in blocks:
            offset = round((captured_at - start) * AUDIO_SAMPLE_RATE)
            left = max(0, offset)
            right = min(count, offset + len(samples))
            if right <= left:
                continue
            if audio is None:
                audio = np.zeros(count, dtype="<i2")
            audio[left:right] = samples[left - offset:right - offset]
        return audio

    def get_buffered_frames(
        self, *, after: float | None = None, through: float | None = None
    ) -> list[TimedFrame]:
        end = time.monotonic() if through is None else through
        start = end - self.pre_roll_seconds if after is None else after
        with self._lock:
            frames = [(captured_at, frame) for captured_at, frame in self._frame_buffer if start < captured_at <= end]
        return [(captured_at, frame.copy()) for captured_at, frame in frames]

    def get_latest_frame(self) -> np.ndarray | None:
        with self._lock:
            if not self._frame_buffer:
                return None
            return self._frame_buffer[-1][1].copy()

    def export_clip_and_snapshot(
        self,
        output_dir: Path,
        event_id: str,
        frames: list[TimedFrame] | None = None,
        *,
        include_audio: bool = False,
    ) -> tuple[Path, Path, Path | None]:
        """Stages time-aligned video and audio plus a thumbnail for one event."""
        output_dir.mkdir(parents=True, exist_ok=True)
        if frames is None:
            frames = self.get_buffered_frames()

        if not frames:
            raise ValueError("Cannot export clip: ring buffer is empty")

        snapshot_path = output_dir / f"{event_id}_thumb.jpg"
        clip_path = output_dir / f"{event_id}.mp4"

        started_at = frames[0][0]
        frame_count = max(1, round((frames[-1][0] - started_at) * self.fps) + 1)
        audio = self.get_buffered_audio(started_at, started_at + frame_count / self.fps) if include_audio else None

        latest_frame = frames[-1][1]
        cv2.imwrite(str(snapshot_path), latest_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])

        h, w = latest_frame.shape[:2]
        if hasattr(cv2, "VideoWriter_fourcc"):
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(clip_path), fourcc, float(self.fps), (w, h))
            try:
                frame_index = 0
                for index in range(frame_count):
                    at = started_at + index / self.fps
                    # Keep the video timeline at the capture rate even if some frames arrived late.
                    while frame_index + 1 < len(frames) and frames[frame_index + 1][0] <= at + 0.5 / self.fps:
                        frame_index += 1
                    frame = frames[frame_index][1]
                    if frame.shape[:2] != (h, w):
                        frame = cv2.resize(frame, (w, h))
                    writer.write(frame)
            finally:
                writer.release()
        else:
            # In pure-NumPy fallback mode without OpenCV VideoWriter, copy snapshot as placeholder
            clip_path.write_bytes(snapshot_path.read_bytes())

        audio_path = None
        if audio is not None:
            audio_path = output_dir / "microphone.wav"
            with wave.open(str(audio_path), "wb") as destination:
                destination.setparams((1, 2, AUDIO_SAMPLE_RATE, 0, "NONE", "not compressed"))
                destination.writeframes(audio.tobytes())
        return clip_path, snapshot_path, audio_path

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
