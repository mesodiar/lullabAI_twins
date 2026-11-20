"""Cloud Sync Client for Edge Node (Fast-Path Firestore + FCM + GCS Upload)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any
import urllib.request


FAST_PATH_TITLES_TH = {
    "CRY": "🚨 ตรวจพบเสียงร้อง — กำลังให้ AI วิเคราะห์...",
    "BAND_ROLLOVER": "⚠️ ตรวจพบความเสี่ยงพลิกคว่ำ/หน้าถูกปิดบัง — กำลังตรวจสอบด่วน!",
    "BREATHING_ANOMALY": "⚠️ จังหวะการขยับหน้าอกหายใจผิดปกติ — กำลังตรวจสอบด่วน!",
    "MOTION": "👀 น้องขยับตัวแรงในเตียง — กำลังวิเคราะห์ภาพ...",
    "PERIODIC_CHECK": "🔄 อัปเดตสถานะการนอนตามรอบปกติ",
}


class CloudSyncClient:
    """Manages Stage-1 Fast-Path alerts (Firestore + FCM) and GCS media uploads."""

    def __init__(
        self,
        project_id: str | None = None,
        gcs_bucket: str | None = None,
        fcm_topic: str = "lullabai_parents",
        local_events_dir: str | Path = "./local_events",
        local_analyzer_url: str | None = None,
    ) -> None:
        self.project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
        self.gcs_bucket = gcs_bucket or os.environ.get("GCS_EVENTS_BUCKET", "")
        self.fcm_topic = fcm_topic
        self.local_events_dir = Path(local_events_dir)
        self.local_analyzer_url = local_analyzer_url or os.environ.get("LOCAL_ANALYZER_URL")

        self._firestore_client = None
        self._storage_client = None
        self._firebase_initialized = False

        if self.project_id and os.environ.get("DRY_RUN", "false").lower() != "true":
            self._init_gcp_clients()

    def _init_gcp_clients(self) -> None:
        try:
            from google.cloud import firestore, storage  # type: ignore[import-not-found]
            import firebase_admin  # type: ignore[import-not-found]

            self._firestore_client = firestore.Client(project=self.project_id)
            self._storage_client = storage.Client(project=self.project_id)
            if not firebase_admin._apps:
                firebase_admin.initialize_app()
            self._firebase_initialized = True
        except Exception as exc:
            print(f"[CloudSyncClient] Running in Local/Dry-Run mode (GCP init skipped: {exc})")
            self._firestore_client = None
            self._storage_client = None

    def send_fast_path_alert(self, event_doc: dict[str, Any]) -> None:
        """Stage 1 (T+0.5s): Writes PENDING_AI event to Firestore and fires immediate FCM push."""
        event_id = event_doc["event_id"]
        baby_id = event_doc["baby_id"]
        trigger_type = event_doc["trigger_type"]
        metrics = event_doc.get("edge_metrics", {})

        # 1. Persist locally for dev visibility / offline audit
        self.local_events_dir.mkdir(parents=True, exist_ok=True)
        local_json = self.local_events_dir / f"{event_id}.json"
        local_json.write_text(json.dumps(event_doc, ensure_ascii=False, indent=2), encoding="utf-8")

        # 2. Write to Cloud Firestore if configured
        if self._firestore_client is not None:
            self._firestore_client.collection("events").document(event_id).set(event_doc)
            self._firestore_client.collection("babies").document(baby_id).set(
                {
                    "baby_id": baby_id,
                    "camera_id": event_doc["camera_id"],
                    "subject_mode": event_doc["subject_mode"],
                    "latest_event_id": event_id,
                    "latest_trigger_type": trigger_type,
                    "breathing_rpm": metrics.get("breathing_rpm"),
                    "band_detected": metrics.get("band_detected", False),
                    "last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
                    "edge_online": True,
                },
                merge=True,
            )

        # 3. Fire Stage-1 Fast-Path FCM Web Push (skip silent periodic checks)
        if trigger_type != "PERIODIC_CHECK" and self._firebase_initialized:
            try:
                from firebase_admin import messaging  # type: ignore[import-not-found]

                title = FAST_PATH_TITLES_TH.get(trigger_type, "🔔 แจ้งเตือนจาก LullabAI")
                subj = metrics.get("subject_type", "BABY")
                body = f"[{baby_id}] ตรวจพบเหตุการณ์ ({trigger_type}, {subj}) • รอผลตรวจละเอียดจาก Gemini AI สักครู่..."

                msg = messaging.Message(
                    topic=self.fcm_topic,
                    data={
                        "event_id": event_id,
                        "baby_id": baby_id,
                        "stage": "FAST_PATH_EDGE",
                        "trigger_type": trigger_type,
                        "title": title,
                        "body": body,
                    },
                    webpush=messaging.WebpushConfig(
                        headers={"Urgency": "high"},
                        notification=messaging.WebpushNotification(
                            title=title,
                            body=body,
                            tag=event_id,  # Same tag allows Stage-2 AI result to replace this notification!
                            renotify=True,
                        ),
                    ),
                )
                messaging.send(msg)
            except Exception as exc:
                print(f"[CloudSyncClient] Fast-path FCM send warning: {exc}")

    def upload_media_and_trigger_system2(
        self,
        event_doc: dict[str, Any],
        clip_path: Path | None,
        snapshot_path: Path,
    ) -> dict[str, str | None]:
        """Uploads snapshot & MP4 clip to GCS with custom metadata to trigger Eventarc -> Cloud Run."""
        event_id = event_doc["event_id"]
        baby_id = event_doc["baby_id"]
        date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        metrics = event_doc.get("edge_metrics", {})

        gcs_video_uri: str | None = None
        gcs_snapshot_uri: str | None = None

        custom_metadata = {
            "event_id": str(event_id),
            "baby_id": str(baby_id),
            "camera_id": str(event_doc["camera_id"]),
            "subject_mode": str(event_doc["subject_mode"]),
            "trigger_type": str(event_doc["trigger_type"]),
            "band_detected": str(metrics.get("band_detected", False)),
            "breathing_rpm": str(metrics.get("breathing_rpm") or ""),
            "yamnet_cry_prob": str(metrics.get("yamnet_cry_prob", 0.0)),
            "motion_score": str(metrics.get("motion_score", 0.0)),
        }

        if self._storage_client is not None and self.gcs_bucket:
            bucket = self._storage_client.bucket(self.gcs_bucket)

            snap_blob_name = f"events/{baby_id}/{date_prefix}/{event_id}_thumb.jpg"
            snap_blob = bucket.blob(snap_blob_name)
            snap_blob.metadata = custom_metadata
            snap_blob.upload_from_filename(str(snapshot_path), content_type="image/jpeg")
            gcs_snapshot_uri = f"gs://{self.gcs_bucket}/{snap_blob_name}"

            if clip_path and clip_path.exists():
                clip_blob_name = f"events/{baby_id}/{date_prefix}/{event_id}.mp4"
                clip_blob = bucket.blob(clip_blob_name)
                clip_blob.metadata = custom_metadata
                clip_blob.upload_from_filename(str(clip_path), content_type="video/mp4")
                gcs_video_uri = f"gs://{self.gcs_bucket}/{clip_blob_name}"

        # Optional: If running local analyzer service in dev/test mode, invoke it directly
        if self.local_analyzer_url:
            try:
                payload = json.dumps(
                    {
                        "event_doc": event_doc,
                        "local_snapshot_path": str(snapshot_path.resolve()),
                        "local_clip_path": str(clip_path.resolve()) if clip_path else None,
                    }
                ).encode("utf-8")
                req = urllib.request.Request(
                    f"{self.local_analyzer_url.rstrip('/')}/analyze-local",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=15.0)
            except Exception as exc:
                print(f"[CloudSyncClient] Local analyzer webhook skipped: {exc}")

        return {
            "gcs_video_uri": gcs_video_uri,
            "gcs_snapshot_uri": gcs_snapshot_uri,
        }

