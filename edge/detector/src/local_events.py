"""Persistent event history and media for the local Edge service."""

from __future__ import annotations

import json
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import threading
import time
from typing import Any

RECENT_EVENT_LIMIT = 15
EVENT_ID = re.compile(r"evt_[0-9]+_[0-9a-f]{6}")
MEDIA_NAME = re.compile(r"evt_[0-9]+_[0-9a-f]{6}(?:\.mp4|_thumb\.jpg)")
VIDEO_RETENTION_OPTIONS = (1, 3, 7, 14, 30, 90)
RETENTION_CHECK_INTERVAL = 60 * 60


class LocalEventStore:
    def __init__(self, directory: str | Path = "local_events") -> None:
        self.directory = Path(directory)
        self.media_dir = self.directory / "media"
        self._retention_file = self.directory / "video-retention.json"
        self._retention_lock = threading.Lock()
        self._retention_changed = threading.Event()
        self.video_retention_days: int | None = None
        try:
            days = json.loads(self._retention_file.read_text(encoding="utf-8"))
            if type(days) is int and days in VIDEO_RETENTION_OPTIONS:
                self.video_retention_days = days
        except (OSError, ValueError):
            pass

    def set_video_retention(self, days: int | None) -> None:
        if days is not None and (type(days) is not int or days not in VIDEO_RETENTION_OPTIONS):
            raise ValueError("Invalid video retention")
        with self._retention_lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.directory) as directory:
                staged = Path(directory) / self._retention_file.name
                staged.write_text(json.dumps(days), encoding="utf-8")
                staged.replace(self._retention_file)
            self.video_retention_days = days
        self._retention_changed.set()

    def run_video_retention(self) -> None:
        while True:
            self._retention_changed.clear()
            try:
                self.prune_videos()
            except OSError as exc:
                print(f"[Edge] Video retention could not scan storage ({type(exc).__name__}).")
            self._retention_changed.wait(RETENTION_CHECK_INTERVAL)

    def prune_videos(self) -> None:
        with self._retention_lock:
            days = self.video_retention_days
            if days is None or self.media_dir.is_symlink():
                return
            cutoff = time.time() - days * 86400
            for path in self.media_dir.glob("*.mp4"):
                if not MEDIA_NAME.fullmatch(path.name):
                    continue
                try:
                    info = path.lstat()
                    if stat.S_ISREG(info.st_mode) and info.st_mtime <= cutoff:
                        path.unlink()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    print(f"[Edge] Could not expire {path.name} ({type(exc).__name__}).")

    def with_available_video(self, event: dict[str, Any]) -> dict[str, Any]:
        media = event.get("media") or {}
        video = media.get("local_video_path")
        if not isinstance(video, str) or not video.startswith("/media/"):
            return event
        if self.media_path(video.removeprefix("/media/")) is not None:
            return event
        available = {**media, "local_video_unavailable": True}
        available.pop("local_video_path", None)
        available.pop("local_video_has_audio", None)
        return {**event, "media": available}

    def save(self, event: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{event['event_id']}.json"
        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            staged = Path(directory) / target.name
            staged.write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
            staged.replace(target)

    def load(self, event_id: str) -> dict[str, Any] | None:
        if not EVENT_ID.fullmatch(event_id):
            return None
        try:
            event = json.loads((self.directory / f"{event_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return event if isinstance(event, dict) and event.get("event_id") == event_id else None

    def recent(self, baby_id: str) -> list[dict[str, Any]]:
        events = []
        # The generated filenames start with the event's Unix timestamp.
        for path in sorted(self.directory.glob("evt_*.json"), reverse=True):
            try:
                event = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(event, dict) or event.get("event_id") != path.stem or event.get("baby_id") != baby_id:
                continue
            if event.get("trigger_type") == "PERIODIC_CHECK":
                continue
            if event.get("status") == "PENDING_AI":
                event["status"] = "EDGE_DETECTED"
            events.append(event)
            if len(events) >= RECENT_EVENT_LIMIT:
                break
        return events

    def save_media(self, clip: Path, snapshot: Path, audio: Path | None = None) -> dict[str, str | bool]:
        self.media_dir.mkdir(parents=True, exist_ok=True)
        media: dict[str, str | bool] = {}

        if snapshot.is_file() and snapshot.stat().st_size:
            snapshot.replace(self.media_dir / snapshot.name)
            media["local_snapshot_path"] = f"/media/{snapshot.name}"

        if clip.is_file() and clip.stat().st_size:
            playable = clip.with_name("playable.mp4")
            for audio_source in (audio, None) if audio is not None else (None,):
                audio_input = ["-i", str(audio_source)] if audio_source is not None else []
                audio_mapping = ["-map", "1:a:0", "-c:a", "aac", "-shortest"] if audio_source is not None else ["-an"]
                try:
                    subprocess.run(
                        ["ffmpeg", "-loglevel", "error", "-y", "-i", str(clip), *audio_input, "-map", "0:v:0",
                         "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                         *audio_mapping, "-movflags", "+faststart", str(playable)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=30,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    print(f"[Edge] Clip encoding failed for {clip.name}: {type(exc).__name__}")
                else:
                    playable.replace(self.media_dir / clip.name)
                    media["local_video_path"] = f"/media/{clip.name}"
                    media["local_video_has_audio"] = audio_source is not None
                    break
        return media

    def media_path(self, name: str) -> Path | None:
        if not MEDIA_NAME.fullmatch(name):
            return None
        path = self.media_dir / name
        return path if not path.is_symlink() and path.is_file() else None
