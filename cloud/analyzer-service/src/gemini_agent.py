"""Vertex AI Gemini Agent (System 2 Deep Reasoner & Safety Check)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from prompts import SafetyAndSleepAnalysis, build_event_prompt, build_system_instruction


class GeminiSleepAnalyzer:
    """Calls Vertex AI Gemini to analyze video/snapshot clips and Edge telemetry."""

    def __init__(
        self,
        project_id: str | None = None,
        location: str | None = None,
        model_id: str | None = None,
    ) -> None:
        self.project_id = project_id if project_id is not None else os.environ.get("GCP_PROJECT_ID", "")
        self.location = location or os.environ.get("VERTEX_LOCATION", "global")
        self.model_id = model_id or os.environ.get("VERTEX_MODEL_ID", "gemini-3.1-flash-lite")
        self._client = None

        if self.project_id:
            from google import genai
            from google.genai import types

            self._client = genai.Client(
                vertexai=True,
                project=self.project_id,
                location=self.location,
                http_options=types.HttpOptions(api_version="v1", timeout=60000),
            )

    def analyze_event(
        self,
        metadata: dict[str, Any],
        gcs_uri: str | None = None,
        mime_type: str = "video/mp4",
        local_media_path: str | Path | None = None,
    ) -> SafetyAndSleepAnalysis:
        """Runs multimodal analysis via Vertex AI Gemini, falling back to deterministic rules in local dev."""
        if self._client is None:
            return self._heuristic_fallback(metadata)

        from google.genai import types

        subject_mode = str(metadata.get("subject_mode", "BABY"))
        face_cover_suspected = str(metadata.get("face_cover_suspected", False)).lower() == "true"
        if (metadata.get("subject_type") == "PET" or
                metadata.get("audio_kind") == "DOG_VOCALIZATION" or face_cover_suspected):
            subject_mode = "PET_TEST"
        if gcs_uri:
            media = types.Part.from_uri(file_uri=gcs_uri, mime_type=mime_type)
        elif local_media_path:
            media = types.Part.from_bytes(data=Path(local_media_path).read_bytes(), mime_type=mime_type)
        else:
            raise ValueError("Vertex AI review requires recorded media")

        response = self._client.models.generate_content(
            model=self.model_id,
            contents=[media, build_event_prompt({**metadata, "subject_mode": subject_mode})],
            config=types.GenerateContentConfig(
                system_instruction=build_system_instruction(subject_mode),
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=SafetyAndSleepAnalysis,
            ),
        )
        if response.parsed is not None:
            return SafetyAndSleepAnalysis.model_validate(response.parsed)
        if response.text:
            return SafetyAndSleepAnalysis.model_validate_json(response.text)
        raise ValueError("Vertex AI returned no review")

    @staticmethod
    def _heuristic_fallback(metadata: dict[str, Any]) -> SafetyAndSleepAnalysis:
        """Deterministic fallback for local testing when Vertex AI credentials are not active."""
        trigger = str(metadata.get("trigger_type") or "").upper()
        band_detected = str(metadata.get("band_detected", "True")).lower() == "true"
        rpm_raw = metadata.get("breathing_rpm")
        rpm = float(rpm_raw) if rpm_raw not in (None, "", "None") else None

        if str(metadata.get("face_cover_suspected", False)).lower() == "true":
            return SafetyAndSleepAnalysis(
                is_false_alarm=False,
                safety_alert=False,
                posture="UNKNOWN",
                face_covered=False,
                band_visible_and_secure=False,
                sleep_state="AWAKE_CALM",
                urgency="HIGH",
                title_th="⚠️ สงสัยว่าผ้าปิดหน้าน้องหมา",
                reason_hypothesis_th="System 1 พบผ้าทับบริเวณที่ติดตามน้องหมา ผลการตรวจภาพยังไม่ยืนยัน",
                recommended_action_th="กรุณาเปิดดูกล้องหรือตรวจดูน้องโดยตรง",
            )

        if trigger == "BAND_ROLLOVER" or not band_detected:
            return SafetyAndSleepAnalysis(
                is_false_alarm=False,
                safety_alert=True,
                posture="PRONE",
                face_covered=False,
                band_visible_and_secure=False,
                sleep_state="ASLEEP_LIGHT",
                urgency="CRITICAL",
                title_th="🚨 แจ้งเตือนด่วน: ไม่เห็นลายแผ่นพันตัว (อาจพลิกคว่ำ)",
                reason_hypothesis_th=(
                    "ระบบตรวจไม่พบลวดลายเรขาคณิตของแผ่นพันตัวบริเวณหน้าอก "
                    "มีความเป็นไปได้ที่น้องจะพลิกตัวคว่ำหน้าหรือผ้าห่มเลื่อนมาคลุมแผ่นพันตัว"
                ),
                recommended_action_th="กรุณาเปิดกล้องสดหรือเข้าไปจัดท่านอนให้นอนหงายทันที",
            )

        if trigger == "BREATHING_ANOMALY" or (rpm is not None and rpm < 8.0):
            return SafetyAndSleepAnalysis(
                is_false_alarm=False,
                safety_alert=True,
                posture="SUPINE",
                face_covered=False,
                band_visible_and_secure=True,
                sleep_state="ASLEEP_DEEP",
                urgency="CRITICAL",
                title_th="🚨 แจ้งเตือนด่วน: จังหวะการขยับหายใจต่ำกว่าเกณฑ์",
                reason_hypothesis_th=(
                    f"ตรวจพบแผ่นพันตัวปกติแต่อัตราการขยับวัดได้ {rpm or 0.0:.0f} ครั้ง/นาที ซึ่งต่ำกว่าเกณฑ์ปกติ"
                ),
                recommended_action_th="กรุณาตรวจสอบการหายใจและความกระชับของแผ่นพันตัวทันที",
            )

        if trigger == "CRY":
            return SafetyAndSleepAnalysis(
                is_false_alarm=False,
                safety_alert=False,
                posture="SUPINE",
                face_covered=False,
                band_visible_and_secure=True,
                sleep_state="CRYING",
                urgency="HIGH",
                title_th="😢 น้องตื่นและส่งเสียงร้อง (ท่านอนหงายปลอดภัย)",
                reason_hypothesis_th=(
                    f"AI ยืนยันเสียงร้องจริง น้องนอนหงายปกติ ไม่มีสิ่งกีดขวางใบหน้า "
                    f"แผ่นพันตัวชัดเจน (หายใจ ~{rpm or 42:.0f} ครั้ง/นาที)"
                ),
                recommended_action_th="สามารถพูดปลอบผ่านไมค์กล้อง หรือเข้าไปดูว่าหิวนม/ผ้าอ้อมเปียกหรือไม่",
            )

        if trigger == "MOTION":
            return SafetyAndSleepAnalysis(
                is_false_alarm=False,
                safety_alert=False,
                posture="SUPINE",
                face_covered=False,
                band_visible_and_secure=True,
                sleep_state="AWAKE_CALM",
                urgency="MEDIUM",
                title_th="👀 น้องขยับตัวตื่นในเตียง (ปลอดภัยดี)",
                reason_hypothesis_th=(
                    f"ตรวจพบการขยับแขนขาในกรอบเตียง นอนหงายปลอดภัย แผ่นพันตัวอยู่ในตำแหน่งปกติ "
                    f"(หายใจ ~{rpm or 34:.0f} ครั้ง/นาที)"
                ),
                recommended_action_th="ยังไม่ต้องทำอะไร รอดูว่าน้องจะหลับต่อเองหรือไม่",
            )

        return SafetyAndSleepAnalysis(
            is_false_alarm=True,
            safety_alert=False,
            posture="UNKNOWN",
            face_covered=False,
            band_visible_and_secure=band_detected,
            sleep_state="AWAKE_CALM",
            urgency="LOW",
            title_th="ผลตรวจสอบไม่ชัดเจน",
            reason_hypothesis_th="ข้อมูลเหตุการณ์ไม่เพียงพอที่จะสรุปผล",
            recommended_action_th="เปิดดูกล้องเพื่อดูสถานะล่าสุด",
        )

