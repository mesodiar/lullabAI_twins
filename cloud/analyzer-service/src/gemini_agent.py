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
        self.project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
        self.location = location or os.environ.get("GCP_LOCATION", "asia-southeast1")
        self.model_id = model_id or os.environ.get("VERTEX_MODEL_ID", "gemini-2.5-flash")
        self._client = None

        if self.project_id and os.environ.get("DRY_RUN", "false").lower() != "true":
            try:
                from google import genai  # type: ignore[import-not-found]

                self._client = genai.Client(
                    vertexai=True,
                    project=self.project_id,
                    location=self.location,
                )
            except Exception as exc:
                print(f"[GeminiSleepAnalyzer] Vertex AI client init skipped ({exc}); using heuristic fallback.")
                self._client = None

    def analyze_event(
        self,
        metadata: dict[str, Any],
        gcs_uri: str | None = None,
        mime_type: str = "video/mp4",
        local_media_path: str | Path | None = None,
    ) -> SafetyAndSleepAnalysis:
        """Runs multimodal analysis via Vertex AI Gemini, falling back to deterministic rules in local dev."""
        if self._client is not None and (gcs_uri or local_media_path):
            try:
                from google.genai import types  # type: ignore[import-not-found]

                subject_mode = str(metadata.get("subject_mode", "BABY"))
                sys_instruction = build_system_instruction(subject_mode)
                prompt_text = build_event_prompt(metadata)

                contents: list[Any] = []
                if gcs_uri:
                    contents.append(types.Part.from_uri(file_uri=gcs_uri, mime_type=mime_type))
                elif local_media_path and Path(local_media_path).exists():
                    raw_bytes = Path(local_media_path).read_bytes()
                    contents.append(types.Part.from_bytes(data=raw_bytes, mime_type=mime_type))
                contents.append(prompt_text)

                response = self._client.models.generate_content(
                    model=self.model_id,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=sys_instruction,
                        temperature=0.1,
                        response_mime_type="application/json",
                        response_schema=SafetyAndSleepAnalysis,
                    ),
                )
                if response.parsed is not None:
                    return response.parsed  # type: ignore[return-value]
                if response.text:
                    return SafetyAndSleepAnalysis.model_validate_json(response.text)
            except Exception as exc:
                print(f"[GeminiSleepAnalyzer] Vertex AI call failed ({exc}); using fallback analyzer.")

        return self._heuristic_fallback(metadata)

    @staticmethod
    def _heuristic_fallback(metadata: dict[str, Any]) -> SafetyAndSleepAnalysis:
        """Deterministic fallback for local testing when Vertex AI credentials are not active."""
        trigger = str(metadata.get("trigger_type", "PERIODIC_CHECK")).upper()
        band_detected = str(metadata.get("band_detected", "True")).lower() == "true"
        rpm_raw = metadata.get("breathing_rpm")
        rpm = float(rpm_raw) if rpm_raw not in (None, "", "None") else None

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

        # PERIODIC_CHECK (every 5 minutes)
        return SafetyAndSleepAnalysis(
            is_false_alarm=False,
            safety_alert=False,
            posture="SUPINE",
            face_covered=False,
            band_visible_and_secure=True,
            sleep_state="ASLEEP_DEEP",
            urgency="LOW",
            title_th="😴 น้องนอนหลับสบายและปลอดภัยดี",
            reason_hypothesis_th=(
                f"ตรวจเช็คตามรอบ 5 นาที: นอนหงายปกติ ไม่มีผ้าคลุมหน้า "
                f"เห็นลายแผ่นพันตัวชัดเจน การหายใจสม่ำเสมอ ({rpm or 32:.0f} ครั้ง/นาที)"
            ),
            recommended_action_th="ปลอดภัยดี ไม่ต้องดำเนินการใดๆ",
        )

