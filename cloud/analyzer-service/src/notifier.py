"""Persist System 2 reviews for the Edge dashboard to retrieve."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from prompts import SafetyAndSleepAnalysis, SleepSummaryAnalysis


class CloudNotifier:
    def __init__(
        self,
        project_id: str | None = None,
        local_events_dir: str | Path | None = None,
    ) -> None:
        self.project_id = project_id if project_id is not None else os.environ.get("GCP_PROJECT_ID", "")
        self.local_events_dir = Path(local_events_dir) if local_events_dir else None

        self._firestore_client = None
        if self.project_id:
            from google.cloud import firestore

            database = os.environ.get("GCP_FIRESTORE_DATABASE", "lullabai-events")
            self._firestore_client = firestore.Client(project=self.project_id, database=database)

    def load_event(self, event_id: str) -> dict[str, Any] | None:
        return self._firestore_client.collection("events").document(event_id).get(timeout=10).to_dict()

    def finalize_event(
        self,
        event_id: str,
        baby_id: str,
        trigger_type: str,
        analysis: SafetyAndSleepAnalysis,
        media_uris: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        status = "FALSE_ALARM_SUPPRESSED" if analysis.is_false_alarm else "ANALYZED"
        update = {
            "status": status,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "ai_analysis": analysis.to_dict(),
        }

        if self._firestore_client is not None:
            # Preserve the edge event, its original timestamp, and its retention policy.
            self._firestore_client.collection("events").document(event_id).update(update, timeout=10)
        elif self.local_events_dir:
            local_file = self.local_events_dir / f"{event_id}.json"
            if local_file.exists():
                existing = json.loads(local_file.read_text(encoding="utf-8"))
                existing.update(update)
                if media_uris:
                    existing["media"] = media_uris
                local_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

        safety_status = "SAFE"
        if analysis.safety_alert or analysis.urgency == "CRITICAL":
            safety_status = "CRITICAL"
        elif analysis.urgency == "HIGH":
            safety_status = "WARNING"
        return {"event_id": event_id, "baby_id": baby_id, "safety_status": safety_status, **update}

    def load_sleep_summary(self, summary_id: str) -> dict[str, Any] | None:
        if self._firestore_client is not None:
            return self._firestore_client.collection("sleep_summaries").document(summary_id).get(timeout=10).to_dict()
        if self.local_events_dir:
            local_file = self.local_events_dir / f"{summary_id}.json"
            if local_file.exists():
                return json.loads(local_file.read_text(encoding="utf-8"))
        return None

    def finalize_sleep_summary(
        self,
        summary_id: str,
        analysis: SleepSummaryAnalysis,
    ) -> dict[str, Any]:
        update = {
            "status": "COMPLETED",
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "ai_summary": analysis.to_dict(),
        }
        if self._firestore_client is not None:
            self._firestore_client.collection("sleep_summaries").document(summary_id).update(update, timeout=10)
        elif self.local_events_dir:
            local_file = self.local_events_dir / f"{summary_id}.json"
            if local_file.exists():
                existing = json.loads(local_file.read_text(encoding="utf-8"))
                existing.update(update)
                local_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"summary_id": summary_id, **update}
