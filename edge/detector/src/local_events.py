"""Persistent event history and media for the local Edge service."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

RECENT_EVENT_LIMIT = 15
MEDIA_NAME = re.compile(r"evt_[0-9]+_[0-9a-f]{6}(?:\.mp4|_thumb\.jpg)")


class LocalEventStore:
    def __init__(self, directory: str | Path = "local_events") -> None:
        self.directory = Path(directory)
        self.media_dir = self.directory / "media"

    def save(self, event: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{event['event_id']}.json"
        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            staged = Path(directory) / target.name
            staged.write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
            staged.replace(target)

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

    def save_media(self, clip: Path, snapshot: Path) -> dict[str, str]:
        self.media_dir.mkdir(parents=True, exist_ok=True)
        media: dict[str, str] = {}

        if snapshot.is_file() and snapshot.stat().st_size:
            snapshot.replace(self.media_dir / snapshot.name)
            media["local_snapshot_path"] = f"/media/{snapshot.name}"

        if clip.is_file() and clip.stat().st_size:
            playable = clip.with_name("playable.mp4")
            try:
                subprocess.run(
                    ["ffmpeg", "-loglevel", "error", "-y", "-i", str(clip), "-an",
                     "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264",
                     "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(playable)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=30,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                print(f"[Edge] Clip encoding unavailable for {clip.name}: {type(exc).__name__}")
            else:
                playable.replace(self.media_dir / clip.name)
                media["local_video_path"] = f"/media/{clip.name}"
        return media

    def media_path(self, name: str) -> Path | None:
        if not MEDIA_NAME.fullmatch(name):
            return None
        path = self.media_dir / name
        return path if not path.is_symlink() and path.is_file() else None
