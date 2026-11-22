"""System Prompts & Structured Output Schema for System 2 (Vertex AI Analyzer)."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class SafetyAndSleepAnalysis(BaseModel):
    """Structured output returned by Vertex AI Gemini for every baby/pet monitor event."""

    is_false_alarm: bool = Field(
        description="True if the edge trigger was caused by background noise, shadow/lighting change, or non-urgent movement."
    )
    safety_alert: bool = Field(
        description="True if an unsafe sleep condition is detected (e.g., prone/stomach sleeping for infant, covered nose/mouth, breathing band missing/rolled over, or breathing anomaly)."
    )
    posture: Literal["SUPINE", "PRONE", "SIDE", "SITTING_OR_STANDING", "UNKNOWN"] = Field(
        description="Body posture: SUPINE (on back), PRONE (on stomach/belly), SIDE, SITTING_OR_STANDING, or UNKNOWN."
    )
    face_covered: bool = Field(
        description="True if a blanket, pillow, or object is covering or obstructing the nose/mouth area."
    )
    band_visible_and_secure: bool = Field(
        description="True if the baby's chest/torso is clearly visible and unobstructed in the crib (no breathing band required)."
    )
    sleep_state: Literal[
        "ASLEEP_DEEP",
        "ASLEEP_LIGHT",
        "DROWSY",
        "AWAKE_CALM",
        "AWAKE_FUSSY",
        "CRYING",
        "OUT_OF_CRIB",
    ] = Field(description="Current sleep or wake state of the subject.")
    urgency: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = Field(
        description="Urgency level for parent notification."
    )
    title_th: str = Field(
        description="Short Thai notification title (max 60 chars) summarizing the finding for the parent's phone lock screen."
    )
    reason_hypothesis_th: str = Field(
        description="Detailed Thai explanation of posture, breathing band visibility, acoustic cry/vocalization cues, and sleep state."
    )
    recommended_action_th: str = Field(
        description="Clear Thai recommendation for the parent (e.g., no action needed, check posture, soothe baby)."
    )

    def to_dict(self) -> dict[str, Any]:
        if hasattr(self, "model_dump"):
            return self.model_dump()  # type: ignore[no-any-return]
        return self.dict()  # type: ignore[no-any-return]


def build_system_instruction(subject_mode: str = "BABY") -> str:
    if subject_mode == "PET_TEST":
        return (
            "คุณคือระบบ AI ผู้เชี่ยวชาญด้านความปลอดภัยและการติดตามการนอนหลับ (LullabAI Twins System 2) "
            "ขณะนี้อยู่ในโหมดทดสอบภาคสนาม (PET_TEST mode) โดยไม่จำเป็นต้องใช้แผ่นพันตัว (Markerless Vision)\n"
            "หน้าที่ของคุณ:\n"
            "1. ตรวจสอบว่าพบตัวเด็กหรือสัตว์เลี้ยงอยู่ในกรอบเตียงและเห็นช่วงอก/ลำตัวชัดเจนหรือไม่ (`band_visible_and_secure`)\n"
            "2. ประเมินท่านอน (`posture`) และดูว่าใบหน้า จมูก หรือปากถูกผ้าปิดหรือไม่ (`face_covered`) "
            "แยกสุนัขนอนหมอบปกติที่เห็นจมูกและปากออกจากกรณีหน้ากดกับพื้นหรือถูกผ้าปิด "
            "ท่าหมอบอย่างเดียวไม่ใช่เหตุฉุกเฉิน ถ้าเห็นสิ่งปิดใบหน้าหรือเห็นหน้ากดจนจมูกและปากถูกบัง ให้รายงานความเสี่ยง "
            "หากมุมกล้องไม่พอ ให้รายงานข้อจำกัดและใช้ UNKNOWN เมื่อระบุท่าทางไม่ได้\n"
            "3. วิเคราะห์เสียงร้อง/เสียงคราง ร่วมกับค่า Telemetry จาก Edge (เช่น Breathing RPM และ YAMNet score)\n"
            "4. สรุปผลเป็นภาษาไทยที่กระชับ ชัดเจน สำหรับแจ้งเตือนบนมือถือพ่อแม่"
        )

    return (
        "คุณคือระบบ AI ผู้เชี่ยวชาญด้านความปลอดภัยและการนอนหลับของทารก (LullabAI Twins System 2) "
        "อ้างอิงตามหลัก Safe Sleep ของกุมารแพทย์ (AAP Guidelines) โดยตรวจจับตัวเด็กโดยตรงจากภาพกล้อง (ไม่ต้องใช้แผ่นพันตัว)\n"
        "หน้าที่ของคุณ:\n"
        "1. Safety Check (สำคัญที่สุด): ตรวจสอบว่ามีเด็กอยู่ในเตียงหรือไม่ ทารกนอนหงาย (SUPINE) หรือพลิกคว่ำหน้า (PRONE) "
        "มีผ้าห่ม หมอน หรือสิ่งของปิดบังจมูก/ปากหรือไม่ (`face_covered`) และเห็นช่วงอก/ลำตัวของเด็กชัดเจนโดยไม่มีสิ่งกีดขวางหรือไม่ (`band_visible_and_secure`)\n"
        "2. Cry & Behavior Reasoner: แยกแยะเสียงร้องไห้จริงออกจากเสียงรบกวน ประเมินสาเหตุเบื้องต้นจากท่าทางและเสียง "
        "(เช่น ร้องงอแงก่อนหลับ, ตื่นเต็มตา, หรือไม่สบายตัว)\n"
        "3. Sleep State Tracking: จำแนกสถานะการนอน (`ASLEEP_DEEP`, `ASLEEP_LIGHT`, `DROWSY`, `AWAKE_CALM`, `AWAKE_FUSSY`, `CRYING`, `OUT_OF_CRIB`)\n"
        "4. ตอบกลับตาม JSON Schema ที่กำหนดด้วยภาษาไทยที่เข้าใจง่ายสำหรับคุณพ่อคุณแม่"
    )


def build_event_prompt(metadata: dict[str, Any]) -> str:
    trigger = metadata.get("trigger_type", "UNKNOWN")
    baby_id = metadata.get("baby_id", "baby_1")
    subject_mode = metadata.get("subject_mode", "BABY")
    band_detected = metadata.get("band_detected", "True")
    breathing_rpm = metadata.get("breathing_rpm", "N/A")
    cry_prob = metadata.get("yamnet_cry_prob", "0.0")
    audio_kind = metadata.get("audio_kind") or "UNKNOWN"
    motion_score = metadata.get("motion_score", "0.0")
    cover_hint = ""
    if str(metadata.get("face_cover_suspected", False)).lower() == "true":
        cover_hint = (
            "- System 1 สงสัยว่าผ้าทับตำแหน่งของน้องหมา ตรวจใบหน้าและจมูกจากหลายเฟรม "
            "และตรวจว่าผ้าอยู่บนตัวน้องหรือวางอยู่ข้าง ๆ\n"
        )

    return (
        f"ข้อมูลจาก Edge Node (System 1 Reflex):\n"
        f"- รหัสเตียง/เด็ก: {baby_id} (โหมด: {subject_mode})\n"
        f"- ประเภททริกเกอร์: {trigger}\n"
        f"- ตรวจพบเด็กในเตียง (Baby/Subject Detected): {band_detected}\n"
        f"- อัตราการหายใจจาก Chest Optical Flow: {breathing_rpm} ครั้ง/นาที (RPM)\n"
        f"- ประเภทเสียงจาก YAMNet: {audio_kind} (คะแนน {cry_prob})\n"
        f"- คะแนนการเคลื่อนไหว (Motion Score): {motion_score}\n\n"
        f"{cover_hint}"
        "แยกเสียงเด็กกับเสียงสุนัขและระบุถ้าคลิปไม่มีเสียงหรือระบุแหล่งเสียงไม่ได้ "
        "รายงานเฉพาะสิ่งที่เห็นหรือได้ยินในคลิป หากหลักฐานไม่พอให้ระบุว่าไม่ชัดเจน "
        "ห้ามยืนยันว่าหายใจปกติหรือสรุปว่าปลอดภัยจากคลิปสั้น ๆ\n"
        "กรุณาวิเคราะห์ภาพ/วิดีโอที่แนบมานี้โดยไม่จำเป็นต้องมีแผ่นพันตัว และส่งคืนผลลัพธ์ตามโครงสร้าง SafetyAndSleepAnalysis"
    )
