"""Cloud Run Service Entrypoint for System 2 (Eventarc GCS Trigger -> Vertex AI -> Firestore/FCM)."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
from typing import Any

from gemini_agent import GeminiSleepAnalyzer
from notifier import CloudNotifier

SUPPORTED_TRIGGERS = frozenset({"CRY", "MOTION", "BAND_ROLLOVER", "BREATHING_ANOMALY"})
EVENT_OBJECT = re.compile(r"events/(baby_[12])/(evt_[0-9]+_[0-9a-f]{6})(?:\.mp4|_thumb\.jpg)")
SUMMARY_OBJECT = re.compile(r"summaries/(sum_[0-9]+_[0-9a-f]{6})\.json")


class System2AnalyzerService:
    """Core processor for GCS Eventarc CloudEvents and local webhook events."""

    def __init__(
        self,
        analyzer: GeminiSleepAnalyzer | None = None,
        notifier: CloudNotifier | None = None,
    ) -> None:
        self.analyzer = analyzer or GeminiSleepAnalyzer()
        self.notifier = notifier or CloudNotifier()
        self.events_bucket = os.environ.get("GCS_EVENTS_BUCKET", "")

    def handle_gcs_cloudevent(self, event_body: dict[str, Any]) -> dict[str, Any]:
        """Processes a `google.cloud.storage.object.v1.finalized` CloudEvent payload."""
        # Eventarc wraps GCS object resource in `data` or at top-level
        data = event_body.get("data", event_body)
        bucket = data.get("bucket", "")
        object_name = data.get("name", "")
        content_type = data.get("contentType", "video/mp4")
        metadata = data.get("metadata") or {}
        gcs_uri = f"gs://{bucket}/{object_name}" if bucket and object_name else None

        if self.events_bucket:
            summary_match = SUMMARY_OBJECT.fullmatch(object_name)
            if summary_match is not None:
                summary_id = summary_match[1]
                if bucket != self.events_bucket or metadata.get("summary_id") != summary_id:
                    return {"status": "skipped_unrecognized_object"}
                summary_doc = self.notifier.load_sleep_summary(summary_id)
                if not summary_doc or summary_doc.get("gcs_uri") != gcs_uri:
                    return {"status": "skipped_unrecognized_summary"}
                if summary_doc.get("status") != "PENDING_AI":
                    return {"status": "already_summarized", "summary_id": summary_id}
                analysis = self.analyzer.summarize_sleep(summary_doc.get("payload") or {})
                return self.notifier.finalize_sleep_summary(summary_id, analysis)

            match = EVENT_OBJECT.fullmatch(object_name)
            if bucket != self.events_bucket or match is None or metadata.get("event_id") != match[2]:
                return {"status": "skipped_unrecognized_object"}
            baby_id, event_id = match.groups()
            event = self.notifier.load_event(event_id)
            if (not event or event.get("baby_id") != baby_id or event.get("simulated") or
                    (event.get("media") or {}).get("gcs_uri") != gcs_uri or
                    event.get("trigger_type") not in SUPPORTED_TRIGGERS):
                return {"status": "skipped_unrecognized_event"}
            if event.get("status") != "PENDING_AI":
                return {"status": "already_reviewed", "event_id": event_id}
            metadata = {
                **event.get("edge_metrics", {}),
                "baby_id": baby_id,
                "subject_mode": event.get("subject_mode", "BABY"),
                "trigger_type": event["trigger_type"],
            }
            mime_type = "video/mp4" if object_name.endswith(".mp4") else "image/jpeg"
            analysis = self.analyzer.analyze_event(metadata=metadata, gcs_uri=gcs_uri, mime_type=mime_type)
            return self.notifier.finalize_event(event_id, baby_id, event["trigger_type"], analysis)

        summary_match = SUMMARY_OBJECT.fullmatch(object_name)
        if summary_match is not None:
            summary_id = summary_match[1]
            summary_doc = self.notifier.load_sleep_summary(summary_id) or {}
            payload = summary_doc.get("payload") or metadata.get("payload") or {}
            analysis = self.analyzer.summarize_sleep(payload)
            return self.notifier.finalize_sleep_summary(summary_id, analysis)

        trigger_type = str(metadata.get("trigger_type") or "").upper()
        if trigger_type == "PERIODIC_CHECK":
            return {"status": "skipped_periodic_check", "object": object_name}

        # Each event uploads both a preview and a clip. Analyze the clip once.
        if object_name.endswith("_thumb.jpg"):
            return {"status": "skipped_thumbnail_for_video_event", "object": object_name}
        if not trigger_type:
            return {"status": "skipped_missing_trigger", "object": object_name}

        event_id = metadata.get("event_id") or Path(object_name).stem.replace("_thumb", "")
        baby_id = metadata.get("baby_id", "baby_1")

        analysis = self.analyzer.analyze_event(
            metadata=metadata,
            gcs_uri=gcs_uri,
            mime_type=content_type,
        )

        media_uris = (
            {"gcs_snapshot_uri": gcs_uri}
            if object_name.endswith(".jpg")
            else {"gcs_video_uri": gcs_uri}
        )
        return self.notifier.finalize_event(
            event_id=event_id,
            baby_id=baby_id,
            trigger_type=trigger_type,
            analysis=analysis,
            media_uris=media_uris,
        )

    def handle_local_webhook(self, body: dict[str, Any]) -> dict[str, Any]:
        """Processes a direct local webhook call from Edge Node during local testing."""
        event_doc = body["event_doc"]
        event_id = event_doc["event_id"]
        baby_id = event_doc["baby_id"]
        trigger_type = str(event_doc["trigger_type"]).upper()
        if trigger_type == "PERIODIC_CHECK":
            return {"status": "skipped_periodic_check", "event_id": event_id}
        metrics = event_doc.get("edge_metrics", {})

        metadata = {
            "event_id": event_id,
            "baby_id": baby_id,
            "camera_id": event_doc.get("camera_id", "cam_crib_1"),
            "subject_mode": event_doc.get("subject_mode", "PET_TEST"),
            "trigger_type": trigger_type,
            "band_detected": str(metrics.get("band_detected", True)),
            "subject_type": str(metrics.get("subject_type", "")),
            "face_cover_suspected": str(metrics.get("face_cover_suspected", False)),
            "breathing_rpm": str(metrics.get("breathing_rpm") or ""),
            "yamnet_cry_prob": str(metrics.get("yamnet_cry_prob", 0.0)),
            "audio_kind": metrics.get("audio_kind"),
            "motion_score": str(metrics.get("motion_score", 0.0)),
        }

        local_clip = body.get("local_clip_path")
        local_snap = body.get("local_snapshot_path")
        media_path = local_clip or local_snap
        mime_type = "video/mp4" if local_clip else "image/jpeg"

        analysis = self.analyzer.analyze_event(
            metadata=metadata,
            local_media_path=media_path,
            mime_type=mime_type,
        )
        return self.notifier.finalize_event(
            event_id=event_id,
            baby_id=baby_id,
            trigger_type=trigger_type,
            analysis=analysis,
        )

    def handle_local_sleep_summary(self, body: dict[str, Any]) -> dict[str, Any]:
        """Processes a direct local sleep summary request during local testing."""
        summary_id = str(body.get("summary_id") or "sum_local")
        payload = body.get("payload") or {}
        analysis = self.analyzer.summarize_sleep(payload)
        return self.notifier.finalize_sleep_summary(summary_id, analysis)


service = System2AnalyzerService()

# Optional FastAPI ASGI app for Cloud Run / Uvicorn
try:
    from fastapi import FastAPI  # type: ignore[import-not-found]

    app = FastAPI(title="LullabAI Twins System 2 Analyzer")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/")
    def cloudevent_endpoint(body: dict[str, Any]) -> dict[str, Any]:
        return service.handle_gcs_cloudevent(body)

    if not service.events_bucket:
        @app.post("/analyze-local")
        def local_webhook_endpoint(body: dict[str, Any]) -> dict[str, Any]:
            return service.handle_local_webhook(body)

        @app.post("/summarize-local")
        def local_summary_endpoint(body: dict[str, Any]) -> dict[str, Any]:
            return service.handle_local_sleep_summary(body)

except ImportError:
    app = None


class _FallbackHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return
        self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        payload = json.loads(raw.decode("utf-8"))

        if self.path == "/analyze-local" and not service.events_bucket:
            res = service.handle_local_webhook(payload)
        elif self.path == "/":
            res = service.handle_gcs_cloudevent(payload)
        else:
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    if app is not None:
        try:
            import uvicorn  # type: ignore[import-not-found]

            uvicorn.run(app, host="0.0.0.0", port=port)
            return
        except ImportError:
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), _FallbackHTTPHandler)
    print(f"[System2Analyzer] Listening on http://0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

