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


class SleepSummaryAnalysis(BaseModel):
    """Structured overnight / daily sleep summary returned by Vertex AI Gemini."""

    sleep_quality: Literal["RESTFUL", "FAIR", "RESTLESS", "INSUFFICIENT_DATA"] = Field(
        description="Overall sleep/rest quality during the requested window."
    )
    headline_th: str = Field(
        description="Short Thai headline (max 80 chars) summarizing the sleep window at a glance."
    )
    summary_th: str = Field(
        description="2-4 concise Thai sentences summarizing sleep rhythm, longest quiet stretch, cry/sound clusters, and any safety alerts."
    )
    key_patterns_th: list[str] = Field(
        default_factory=list,
        description="2-4 concise Thai bullet points noting peak disturbance hours, recurring triggers, or posture/cover observations.",
    )
    twins_insight_th: str | None = Field(
        default=None,
        description="Optional Thai insight comparing Bed 1 and Bed 2 or noting cross-bed wakeups when both beds have data; otherwise null.",
    )
    recommendations_th: list[str] = Field(
        default_factory=list,
        description="1-3 practical, supportive Thai suggestions for the next sleep window.",
    )

    def to_dict(self) -> dict[str, Any]:
        if hasattr(self, "model_dump"):
            return self.model_dump()  # type: ignore[no-any-return]
        return self.dict()  # type: ignore[no-any-return]


def build_sleep_summary_system_instruction(subject_mode: str = "BABY") -> str:
    if subject_mode == "PET_TEST":
        return (
            "คุณคือผู้ช่วยสรุปผลการพักผ่อนและความปลอดภัยของ LullabAI Twins (โหมดทดสอบกับน้องหมา PET_TEST)\n"
            "หน้าที่ของคุณคืออ่านข้อมูลเหตุการณ์ที่รวบรวมจาก Edge ในช่วงเวลาที่กำหนด แล้วสรุปเป็นภาษาไทยที่อ่านง่าย กระชับ และตรงตามข้อมูลจริง:\n"
            "1. สรุปภาพรวมช่วงเวลาที่สงบยาวนานที่สุด ช่วงที่มีเสียงเห่า/คราง การขยับตัว หรือการแจ้งเตือนผ้าคลุมหน้า\n"
            "2. ระบุรูปแบบเวลาที่เกิดเหตุการณ์บ่อย (key_patterns_th)\n"
            "3. หากมีข้อมูลเทียบกับอีกเตียง/อีกจุด ให้สรุปความเชื่อมโยงใน twins_insight_th ถ้าไม่มีให้ใส่ null\n"
            "4. ห้ามแต่งตัวเลขหรือเหตุการณ์ที่ไม่มีในข้อมูล หากไม่มีเหตุการณ์เลยให้ระบุว่าสงบหรือข้อมูลยังไม่เพียงพอ\n"
            "5. ตอบกลับตาม JSON Schema SleepSummaryAnalysis เท่านั้น"
        )

    return (
        "คุณคือผู้ช่วยสรุปผลการนอนของทารกแฝด LullabAI Twins สำหรับคุณพ่อคุณแม่ที่ต้องการอ่านสรุปตอนเช้าแบบรวดเร็ว\n"
        "หน้าที่ของคุณคือวิเคราะห์ข้อมูลเหตุการณ์การนอน (เสียงร้อง, การขยับตัว, แจ้งเตือนความปลอดภัย, และผลวิเคราะห์คลิปก่อนหน้า) "
        "แล้วสรุปเป็นภาษาไทยที่อบอุ่น กระชับ และอ้างอิงตามข้อมูลจริง:\n"
        "1. ประเมินภาพรวมคุณภาพการนอน (`RESTFUL`, `FAIR`, `RESTLESS`, `INSUFFICIENT_DATA`) และช่วงเวลาที่หลับสงบยาวนานที่สุด\n"
        "2. สรุปรูปแบบสำคัญ เช่น ช่วงเวลาที่ตื่นร้องบ่อย การขยับตัว หรือเหตุการณ์ความปลอดภัย (`key_patterns_th`)\n"
        "3. ถ้ามีข้อมูลของแฝดอีกเตียง ให้วิเคราะห์ความเชื่อมโยง เช่น เตียงหนึ่งร้องแล้วปลุกอีกเตียงหรือไม่ (`twins_insight_th`) ถ้าไม่มีข้อมูลอีกเตียงให้ใส่ null\n"
        "4. ให้คำแนะนำเชิงปฏิบัติที่อ่อนโยนและเป็นประโยชน์สำหรับรอบการนอนถัดไป (`recommendations_th`) โดยไม่อ้างการวินิจฉัยทางการแพทย์\n"
        "5. ห้ามสร้างตัวเลขหรือเวลาที่ไม่มีในข้อมูล และตอบกลับตาม JSON Schema SleepSummaryAnalysis เท่านั้น"
    )


def build_sleep_summary_prompt(payload: dict[str, Any]) -> str:
    import json

    compact = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        "ข้อมูลสรุปเหตุการณ์จาก Edge Node สำหรับช่วงเวลาที่เลือก:\n"
        f"{compact}\n\n"
        "โปรดวิเคราะห์ข้อมูลด้านบนและสร้างรายงานสรุปผลการนอนตามโครงสร้าง SleepSummaryAnalysis "
        "โดยระบุเวลาให้สอดคล้องกับ timezone ที่แนบมา (local_timezone) หากมี"
    )
