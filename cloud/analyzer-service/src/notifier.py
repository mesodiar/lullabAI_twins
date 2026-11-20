"""Firestore State Updater & Stage-2 FCM Notification Dispatcher."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from prompts import SafetyAndSleepAnalysis


class CloudNotifier:
    """Updates Firestore (`events/{id}` & `babies/{id}`) and dispatches Stage-2 FCM push."""

    def __init__(
        self,
        project_id: str | None = None,
        fcm_topic: str = "lullabai_parents",
        local_events_dir: str | Path | None = None,
    ) -> None:
        self.project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
        self.fcm_topic = fcm_topic
        self.local_events_dir = Path(local_events_dir) if local_events_dir else None

        self._firestore_client = None
        self._firebase_initialized = False

        if self.project_id and os.environ.get("DRY_RUN", "false").lower() != "true":
            try:
                from google.cloud import firestore  # type: ignore[import-not-found]
                import firebase_admin  # type: ignore[import-not-found]

                self._firestore_client = firestore.Client(project=self.project_id)
                if not firebase_admin._apps:
                    firebase_admin.initialize_app()
                self._firebase_initialized = True
            except Exception as exc:
                print(f"[CloudNotifier] Running in Local/Dry-Run mode ({exc})")

    def finalize_event(
        self,
        event_id: str,
        baby_id: str,
        trigger_type: str,
        analysis: SafetyAndSleepAnalysis,
        media_uris: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        """Persists System 2 analysis and sends Stage-2 FCM replacement notification."""
        analyzed_at = datetime.now(timezone.utc).isoformat()
        status = "FALSE_ALARM_SUPPRESSED" if analysis.is_false_alarm else "ANALYZED"
        analysis_dict = analysis.to_dict()

        if analysis.safety_alert or analysis.urgency == "CRITICAL":
            safety_status = "CRITICAL"
        elif analysis.urgency == "HIGH":
            safety_status = "WARNING"
        else:
            safety_status = "SAFE"

        update_payload: dict[str, Any] = {
            "status": status,
            "analyzed_at": analyzed_at,
            "ai_analysis": analysis_dict,
        }
        if media_uris:
            update_payload["media"] = media_uris

        # 1. Update local event JSON if running in local dev mode
        if self.local_events_dir:
            local_file = self.local_events_dir / f"{event_id}.json"
            if local_file.exists():
                existing = json.loads(local_file.read_text(encoding="utf-8"))
                existing.update(update_payload)
                local_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

        # 2. Update Cloud Firestore (`events/{event_id}` and `babies/{baby_id}`)
        if self._firestore_client is not None:
            self._firestore_client.collection("events").document(event_id).set(update_payload, merge=True)
            self._firestore_client.collection("babies").document(baby_id).set(
                {
                    "baby_id": baby_id,
                    "current_sleep_state": analysis.sleep_state,
                    "safety_status": safety_status,
                    "posture": analysis.posture,
                    "face_covered": analysis.face_covered,
                    "band_visible_and_secure": analysis.band_visible_and_secure,
                    "last_ai_title_th": analysis.title_th,
                    "last_ai_summary_th": analysis.reason_hypothesis_th,
                    "last_analyzed_at": analyzed_at,
                },
                merge=True,
            )

        # 3. Send Stage-2 FCM Notification (replaces Stage-1 notification using same `tag: event_id`)
        #    Note: Silent 5-minute PERIODIC_CHECK only alerts the parent if `safety_alert` is True!
        should_notify = (
            not analysis.is_false_alarm
            and (trigger_type != "PERIODIC_CHECK" or analysis.safety_alert)
        )
        if should_notify and self._firebase_initialized:
            try:
                from firebase_admin import messaging  # type: ignore[import-not-found]

                urgency_header = "high" if analysis.urgency in ("HIGH", "CRITICAL") else "normal"
                msg = messaging.Message(
                    topic=self.fcm_topic,
                    data={
                        "event_id": event_id,
                        "baby_id": baby_id,
                        "stage": "ANALYZED_AI",
                        "urgency": analysis.urgency,
                        "sleep_state": analysis.sleep_state,
                        "safety_alert": str(analysis.safety_alert),
                        "title": analysis.title_th,
                        "body": f"{analysis.reason_hypothesis_th} • คำแนะนำ: {analysis.recommended_action_th}",
                    },
                    webpush=messaging.WebpushConfig(
                        headers={"Urgency": urgency_header},
                        notification=messaging.WebpushNotification(
                            title=analysis.title_th,
                            body=f"{analysis.reason_hypothesis_th}\n💡 {analysis.recommended_action_th}",
                            tag=event_id,  # Replaces the Fast-Path notification seamlessly
                            renotify=analysis.safety_alert,
                        ),
                    ),
                )
                messaging.send(msg)
            except Exception as exc:
                print(f"[CloudNotifier] Stage-2 FCM send warning: {exc}")

        return {
            "event_id": event_id,
            "baby_id": baby_id,
            "status": status,
            "safety_status": safety_status,
            "ai_analysis": analysis_dict,
        }
