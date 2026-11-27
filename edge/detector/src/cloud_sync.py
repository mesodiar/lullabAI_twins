"""Upload eligible event media and retrieve reviews without blocking the Edge monitor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from typing import Any, Callable, TypeVar

from local_events import LocalEventStore
from sleep_summary import SleepSummaryStore

SUPPORTED_TRIGGERS = frozenset({"CRY", "MOTION", "BAND_ROLLOVER", "BREATHING_ANOMALY"})
Result = TypeVar("Result")


class CloudReviewDisabled(Exception):
    """The event belongs to an earlier analysis mode."""


# Temporary testing mode: notify on every Vertex AI review (including is_false_alarm=True / LOW urgency).
# Set to False for production to notify only on confirmed MEDIUM/HIGH/CRITICAL or safety alerts.
NOTIFY_ALL_AI_REVIEWS = os.environ.get("LULLABAI_NOTIFY_ALL_AI_REVIEWS", "1") == "1"


def should_notify_for_ai_review(
    review_status: str | None,
    ai_analysis: dict[str, Any] | None,
    *,
    include_all: bool = NOTIFY_ALL_AI_REVIEWS,
) -> bool:
    """Return True when Vertex AI review should trigger a parent notification."""
    if not isinstance(ai_analysis, dict):
        return False
    if include_all:
        return True
    if review_status == "FALSE_ALARM_SUPPRESSED" or ai_analysis.get("is_false_alarm") is True:
        return False
    if ai_analysis.get("safety_alert") is True:
        return True
    urgency = str(ai_analysis.get("urgency") or "").upper()
    return urgency in ("MEDIUM", "HIGH", "CRITICAL")


class CloudSyncClient:
    def __init__(
        self,
        store: LocalEventStore,
        firestore: Any,
        bucket: Any,
        web_push: Any | None = None,
    ) -> None:
        self.store = store
        self.summary_store = SleepSummaryStore(store)
        self.firestore = firestore
        self.bucket = bucket
        self.web_push = web_push
        self.queue = store.directory / ".cloud_pending"
        self.queue.mkdir(parents=True, exist_ok=True)
        self._mode_file = store.directory / "cloud-review-mode"
        self._mode_lock = threading.Lock()
        self._wake = threading.Event()
        self._enabled_since: datetime | None = None
        try:
            saved = datetime.fromisoformat(self._mode_file.read_text(encoding="utf-8").strip())
            if saved.tzinfo is not None:
                self._enabled_since = saved
        except (OSError, ValueError):
            pass
        for pending in store.directory.glob("evt_*.json"):
            event = store.load(pending.stem)
            if event and event.get("cloud_review") == "PENDING":
                (self.queue / pending.stem).touch()
        if self.summary_store.summaries_dir.is_dir():
            for pending in self.summary_store.summaries_dir.glob("baby_*_*h.json"):
                try:
                    summary = json.loads(pending.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(summary, dict) and summary.get("status") == "PENDING_AI" and summary.get("summary_id"):
                    (self.queue / str(summary["summary_id"])).touch()

    @classmethod
    def from_env(cls, store: LocalEventStore, web_push: Any | None = None) -> CloudSyncClient | None:
        if os.environ.get("LULLABAI_GCP_ENABLED") != "1":
            return None

        try:
            from google.cloud import firestore, storage

            project = os.environ.get("GCP_PROJECT_ID", "twins-monitor")
            database = os.environ.get("GCP_FIRESTORE_DATABASE", "lullabai-events")
            bucket = os.environ.get("GCS_EVENTS_BUCKET", f"{project}-lullabai-events")
            client = cls(store, firestore.Client(project=project, database=database),
                         storage.Client(project=project).bucket(bucket), web_push=web_push)
        except Exception as exc:
            print(f"[CloudSync] Cloud review unavailable ({type(exc).__name__}).")
            return None
        threading.Thread(target=client._run, name="cloud-sync", daemon=True).start()
        print("[CloudSync] GCP event reviews available.")
        return client

    @property
    def enabled(self) -> bool:
        return self._enabled_since is not None

    def set_enabled(self, enabled: bool) -> None:
        with self._mode_lock:
            if self.enabled == enabled:
                return
            since = datetime.now(timezone.utc) if enabled else None
            with TemporaryDirectory(dir=self.store.directory) as directory:
                staged = Path(directory) / self._mode_file.name
                staged.write_text(since.isoformat() if since else "local", encoding="utf-8")
                staged.replace(self._mode_file)
            self._enabled_since = since

    def _eligible(self, created: datetime) -> bool:
        since = self._enabled_since
        return since is not None and created >= since

    def _cloud_call(self, created: datetime, operation: Callable[..., Result],
                    *args: Any, **kwargs: Any) -> Result:
        # Let an admitted request finish before changing modes. Check again before each request.
        with self._mode_lock:
            if not self._eligible(created):
                raise CloudReviewDisabled
            return operation(*args, retry=None, **kwargs)

    def enqueue(self, event: dict[str, Any]) -> None:
        if event.get("simulated") or event["trigger_type"] not in SUPPORTED_TRIGGERS or not event.get("media"):
            return
        created = datetime.fromisoformat(event["created_at"])
        if not self._eligible(created):
            return
        # The clip and its metadata are already saved. Keep the queue on disk across restarts.
        pending = {**event, "cloud_review": "PENDING"}
        self.store.save(pending)
        (self.queue / event["event_id"]).touch()
        event.update(pending)

    def with_review(self, event: dict[str, Any]) -> dict[str, Any]:
        if event.get("cloud_review") == "PENDING":
            stored = self.store.load(event["event_id"]) or event
            if stored.get("cloud_review") == "PENDING" and not self._eligible(datetime.fromisoformat(stored["created_at"])):
                return {key: value for key, value in stored.items() if key != "cloud_review"}
            return stored
        return event

    def enqueue_sleep_summary(self, summary_doc: dict[str, Any]) -> dict[str, Any]:
        created = datetime.fromisoformat(summary_doc["created_at"])
        if not self._eligible(created):
            self.summary_store.save_summary(summary_doc)
            return summary_doc
        pending = {**summary_doc, "status": "PENDING_AI"}
        self.summary_store.save_summary(pending)
        (self.queue / pending["summary_id"]).touch()
        self._wake.set()
        return pending

    def _run(self) -> None:
        while True:
            self._wake.clear()
            has_pending_summaries = False
            try:
                for marker in sorted(self.queue.glob("evt_*")):
                    try:
                        self._sync(marker)
                    except CloudReviewDisabled:
                        # A mode change interrupted this event. Remove the pending flag on the next pass.
                        continue
                for marker in sorted(self.queue.glob("sum_*")):
                    try:
                        self._sync_summary(marker)
                    except CloudReviewDisabled:
                        continue
                has_pending_summaries = any(self.queue.glob("sum_*"))
            except Exception as exc:
                # Let SDKs report failures on the next pass without logging media or credentials.
                print(f"[CloudSync] Will retry cloud review ({type(exc).__name__}).")
            self._wake.wait(3 if has_pending_summaries else 15)

    def _sync_summary(self, marker: Path) -> None:
        summary = self.summary_store.find_by_id(marker.name)
        if summary is None or summary.get("status") != "PENDING_AI":
            marker.unlink(missing_ok=True)
            return

        created = datetime.fromisoformat(summary["created_at"])
        if not self._eligible(created):
            self.summary_store.save_summary({**summary, "status": "LOCAL_ONLY"})
            marker.unlink(missing_ok=True)
            return

        if datetime.now(created.tzinfo) - created >= timedelta(hours=1):
            self.summary_store.save_summary({**summary, "status": "UNAVAILABLE"})
            marker.unlink(missing_ok=True)
            return

        from google.api_core.exceptions import AlreadyExists, PreconditionFailed

        summary_id = marker.name
        ref = self.firestore.collection("sleep_summaries").document(summary_id)
        blob = self.bucket.blob(f"summaries/{summary_id}.json")
        if marker.stat().st_size == 0:
            document = {
                "summary_id": summary_id,
                "baby_id": summary["baby_id"],
                "window_hours": summary["window_hours"],
                "created_at": summary["created_at"],
                "status": "PENDING_AI",
                "expires_at": created + timedelta(days=7),
                "gcs_uri": f"gs://{self.bucket.name}/{blob.name}",
                "payload": summary.get("payload") or {},
            }
            try:
                self._cloud_call(created, ref.create, document, timeout=10)
            except AlreadyExists:
                pass

            blob.metadata = {"summary_id": summary_id}
            trigger_bytes = json.dumps({"summary_id": summary_id}).encode("utf-8")
            try:
                self._cloud_call(
                    created,
                    blob.upload_from_string,
                    trigger_bytes,
                    content_type="application/json",
                    if_generation_match=0,
                    timeout=15,
                )
            except PreconditionFailed:
                pass
            marker.write_text("uploaded", encoding="utf-8")

        result = self._cloud_call(created, ref.get, timeout=10).to_dict() or {}
        if result.get("status") == "COMPLETED" and result.get("ai_summary"):
            completed = {
                **summary,
                "status": "COMPLETED",
                "analyzed_at": result.get("analyzed_at"),
                "ai_summary": result["ai_summary"],
            }
            completed.pop("payload", None)
            self.summary_store.save_summary(completed)
            marker.unlink(missing_ok=True)

    def _sync(self, marker: Path) -> None:
        event = self.store.load(marker.name)
        if event is None or event.get("cloud_review") != "PENDING":
            marker.unlink(missing_ok=True)
            return

        created = datetime.fromisoformat(event["created_at"])
        if not self._eligible(created):
            event.pop("cloud_review", None)
            self.store.save(event)
            marker.unlink(missing_ok=True)
            return

        from google.api_core.exceptions import AlreadyExists, PreconditionFailed

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
                self._cloud_call(created, ref.create, document, timeout=10)
            except AlreadyExists:
                pass

            mime_type = "video/mp4" if clip.suffix == ".mp4" else "image/jpeg"
            blob.metadata = {"event_id": marker.name}
            try:
                self._cloud_call(created, blob.upload_from_filename, str(clip),
                                 content_type=mime_type, if_generation_match=0, timeout=15)
            except PreconditionFailed:
                # The previous attempt uploaded the same immutable object.
                pass
            marker.write_text("uploaded", encoding="utf-8")

        review = self._cloud_call(created, ref.get, timeout=10).to_dict() or {}
        if review.get("status") in ("ANALYZED", "FALSE_ALARM_SUPPRESSED") and review.get("ai_analysis"):
            review_status = str(review["status"])
            ai_analysis = review["ai_analysis"]
            reviewed_event = {
                **event,
                "cloud_review": "REVIEWED",
                "status": review_status,
                "analyzed_at": review["analyzed_at"],
                "ai_analysis": ai_analysis,
            }
            self.store.save(reviewed_event)
            marker.unlink(missing_ok=True)
            if self.web_push is not None and should_notify_for_ai_review(review_status, ai_analysis):
                self.web_push.submit(reviewed_event)
