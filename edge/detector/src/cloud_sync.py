"""Upload eligible event media and retrieve reviews without blocking the Edge monitor."""

from __future__ import annotations

from datetime import datetime, timedelta
import os
from pathlib import Path
import threading
import time
from typing import Any

from local_events import LocalEventStore

SUPPORTED_TRIGGERS = frozenset({"CRY", "MOTION", "BAND_ROLLOVER", "BREATHING_ANOMALY"})


class CloudSyncClient:
    def __init__(self, store: LocalEventStore, firestore: Any, bucket: Any) -> None:
        self.store = store
        self.firestore = firestore
        self.bucket = bucket
        self.queue = store.directory / ".cloud_pending"
        self.queue.mkdir(parents=True, exist_ok=True)
        for pending in store.directory.glob("evt_*.json"):
            event = store.load(pending.stem)
            if event and event.get("cloud_review") == "PENDING":
                (self.queue / pending.stem).touch()

    @classmethod
    def from_env(cls, store: LocalEventStore) -> CloudSyncClient | None:
        if os.environ.get("LULLABAI_GCP_ENABLED") != "1":
            return None

        try:
            from google.cloud import firestore, storage

            project = os.environ.get("GCP_PROJECT_ID", "lullabai-twins")
            database = os.environ.get("GCP_FIRESTORE_DATABASE", "lullabai-events")
            bucket = os.environ.get("GCS_EVENTS_BUCKET", f"{project}-lullabai-events")
            client = cls(store, firestore.Client(project=project, database=database),
                         storage.Client(project=project).bucket(bucket))
        except Exception as exc:
            print(f"[CloudSync] Cloud review unavailable ({type(exc).__name__}).")
            return None
        threading.Thread(target=client._run, name="cloud-sync", daemon=True).start()
        print("[CloudSync] GCP event reviews enabled.")
        return client

    def enqueue(self, event: dict[str, Any]) -> None:
        if event.get("simulated") or event["trigger_type"] not in SUPPORTED_TRIGGERS or not event.get("media"):
            return
        # The clip and its metadata are already saved. Keep the queue on disk across restarts.
        pending = {**event, "cloud_review": "PENDING"}
        self.store.save(pending)
        (self.queue / event["event_id"]).touch()
        event.update(pending)

    def with_review(self, event: dict[str, Any]) -> dict[str, Any]:
        if event.get("cloud_review") == "PENDING":
            return self.store.load(event["event_id"]) or event
        return event

    def _run(self) -> None:
        while True:
            try:
                for marker in sorted(self.queue.glob("evt_*")):
                    self._sync(marker)
            except Exception as exc:
                # Let SDKs report failures on the next pass without logging media or credentials.
                print(f"[CloudSync] Will retry cloud review ({type(exc).__name__}).")
            time.sleep(15)

    def _sync(self, marker: Path) -> None:
        from google.api_core.exceptions import AlreadyExists, PreconditionFailed

        event = self.store.load(marker.name)
        if event is None or event.get("cloud_review") != "PENDING":
            marker.unlink(missing_ok=True)
            return

        created = datetime.fromisoformat(event["created_at"])
        media = event["media"]
        relative_url = media.get("local_video_path") or media.get("local_snapshot_path")
        filename = relative_url.removeprefix("/media/") if relative_url else ""
        clip = self.store.media_path(filename)
        if clip is None or filename not in (f"{marker.name}.mp4", f"{marker.name}_thumb.jpg"):
            self.store.save({**event, "cloud_review": "UNAVAILABLE"})
            marker.unlink(missing_ok=True)
            return
        if datetime.now(created.tzinfo) - created >= timedelta(days=1):
            self.store.save({**event, "cloud_review": "UNAVAILABLE"})
            marker.unlink(missing_ok=True)
            return

        ref = self.firestore.collection("events").document(marker.name)
        blob = self.bucket.blob(f"events/{event['baby_id']}/{filename}")
        if marker.stat().st_size == 0:
            document = {k: event[k] for k in ("event_id", "baby_id", "camera_id", "subject_mode",
                                               "trigger_type", "created_at", "edge_metrics")}
            document.update(status="PENDING_AI", expires_at=created + timedelta(days=7),
                            media={"gcs_uri": f"gs://{self.bucket.name}/{blob.name}"})
            try:
                ref.create(document, timeout=10)
            except AlreadyExists:
                pass

            mime_type = "video/mp4" if clip.suffix == ".mp4" else "image/jpeg"
            blob.metadata = {"event_id": marker.name}
            try:
                blob.upload_from_filename(str(clip), content_type=mime_type, if_generation_match=0, timeout=15)
            except PreconditionFailed:
                # The previous attempt uploaded the same immutable object.
                pass
            marker.write_text("uploaded", encoding="utf-8")

        review = ref.get(timeout=10).to_dict() or {}
        if review.get("status") in ("ANALYZED", "FALSE_ALARM_SUPPRESSED") and review.get("ai_analysis"):
            self.store.save({**event, "cloud_review": "REVIEWED", "status": "ANALYZED",
                             "analyzed_at": review["analyzed_at"], "ai_analysis": review["ai_analysis"]})
            marker.unlink(missing_ok=True)
