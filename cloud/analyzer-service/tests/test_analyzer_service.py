"""Unit Tests for LullabAI Twins System 2 (Cloud Run + Vertex AI Analyzer Service)."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "cloud" / "analyzer-service" / "src"))

from gemini_agent import GeminiSleepAnalyzer  # noqa: E402
from main import System2AnalyzerService  # noqa: E402
from notifier import CloudNotifier  # noqa: E402
from prompts import build_event_prompt, build_system_instruction  # noqa: E402


def test_prompts_support_baby_and_pet_test_modes() -> None:
    pet_sys = build_system_instruction("PET_TEST")
    assert "PET_TEST" in pet_sys
    assert "แผ่นพันตัว" in pet_sys

    baby_sys = build_system_instruction("BABY")
    assert "Safe Sleep" in baby_sys

    prompt = build_event_prompt(
        {
            "baby_id": "baby_1",
            "subject_mode": "PET_TEST",
            "trigger_type": "CRY",
            "band_detected": "True",
            "breathing_rpm": "32.0",
            "yamnet_cry_prob": "0.84",
            "motion_score": "11.2",
        }
    )
    assert "32.0" in prompt
    assert "0.84" in prompt


def test_cloudevent_processing_for_cry_and_rollover(tmp_path: Path) -> None:
    analyzer = GeminiSleepAnalyzer(project_id="")
    notifier = CloudNotifier(project_id="", local_events_dir=tmp_path)
    svc = System2AnalyzerService(analyzer=analyzer, notifier=notifier)

    # Pre-create Stage 1 event file in tmp_path
    evt_id = "evt_test_rollover_01"
    (tmp_path / f"{evt_id}.json").write_text(
        json.dumps({"event_id": evt_id, "status": "PENDING_AI"}),
        encoding="utf-8",
    )

    cloudevent = {
        "data": {
            "bucket": "lullabai-events",
            "name": f"events/baby_1/2026-10-31/{evt_id}.mp4",
            "contentType": "video/mp4",
            "metadata": {
                "event_id": evt_id,
                "baby_id": "baby_1",
                "camera_id": "cam_crib_1",
                "subject_mode": "PET_TEST",
                "trigger_type": "BAND_ROLLOVER",
                "band_detected": "False",
                "breathing_rpm": "",
            },
        }
    }

    res = svc.handle_gcs_cloudevent(cloudevent)
    assert res["status"] == "ANALYZED"
    assert res["safety_status"] == "CRITICAL"
    assert res["ai_analysis"]["safety_alert"] is True
    assert res["ai_analysis"]["posture"] == "PRONE"
    assert res["ai_analysis"]["band_visible_and_secure"] is False

    # Verify local event JSON was updated with Stage 2 AI results
    updated_doc = json.loads((tmp_path / f"{evt_id}.json").read_text(encoding="utf-8"))
    assert updated_doc["status"] == "ANALYZED"
    assert updated_doc["ai_analysis"]["urgency"] == "CRITICAL"


def test_cloudevent_skips_thumbnail_when_video_present() -> None:
    svc = System2AnalyzerService(
        analyzer=GeminiSleepAnalyzer(project_id=""),
        notifier=CloudNotifier(project_id=""),
    )
    cloudevent = {
        "data": {
            "bucket": "lullabai-events",
            "name": "events/baby_1/2026-10-31/evt_123_thumb.jpg",
            "contentType": "image/jpeg",
            "metadata": {
                "event_id": "evt_123",
                "trigger_type": "CRY",
            },
        }
    }
    res = svc.handle_gcs_cloudevent(cloudevent)
    assert res["status"] == "skipped_thumbnail_for_video_event"

