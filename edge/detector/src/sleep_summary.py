"""Aggregate Edge event history into sleep summaries and persist AI briefings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import tempfile
import uuid
from typing import Any

from local_events import LocalEventStore

SUMMARY_WINDOWS = (6, 24, 72)
DEFAULT_SUMMARY_WINDOW = 24
BED_IDS = ("baby_1", "baby_2")
SUMMARY_ID = re.compile(r"sum_[0-9]+_[0-9a-f]{6}")
EVENT_TS = re.compile(r"evt_([0-9]+)_[0-9a-f]{6}\.json")
MAX_TIMELINE_EVENTS = 60


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _is_safety_alert(event: dict[str, Any]) -> bool:
    trigger = str(event.get("trigger_type") or "").upper()
    metrics = event.get("edge_metrics") or {}
    ai = event.get("ai_analysis") or {}
    return bool(
        trigger in ("BAND_ROLLOVER", "BREATHING_ANOMALY")
        or metrics.get("face_cover_suspected")
        or ai.get("safety_alert")
        or ai.get("urgency") == "CRITICAL"
    )


def _is_disturbance(event: dict[str, Any]) -> bool:
    trigger = str(event.get("trigger_type") or "").upper()
    return trigger == "CRY" or _is_safety_alert(event)


def _build_local_summary(
    *,
    window_hours: int,
    subject_mode: str,
    stats: dict[str, Any],
    other_bed_events: int,
) -> dict[str, Any]:
    total = int(stats["total_events"])
    sound_total = int(stats["cry_count"]) + int(stats["dog_vocalization_count"])
    safety = int(stats["safety_alert_count"])
    quiet_mins = int(stats["longest_quiet_minutes"])
    cross_bed = int(stats["cross_bed_wakeups"])

    quiet_h, quiet_m = divmod(quiet_mins, 60)
    quiet_str = f"{quiet_h} ชม. {quiet_m} นาที" if quiet_h else f"{quiet_m} นาที"
    subject_label = "น้องหมา" if subject_mode == "PET_TEST" else "น้อง"

    if total == 0:
        quality = "RESTFUL"
        headline = f"ช่วง {window_hours} ชม.ที่ผ่านมาสงบดี ไม่มีเหตุการณ์แจ้งเตือน"
        summary = f"ไม่พบเสียงร้องหรือแจ้งเตือนความปลอดภัยของ{subject_label}ในช่วง {window_hours} ชั่วโมงที่ผ่านมา"
        patterns = [f"ช่วงสงบต่อเนื่องยาวนาน {quiet_str}"]
        recs = ["ระบบกำลังเฝ้าระวังตามปกติ"]
    elif safety > 0 or sound_total >= 6:
        quality = "RESTLESS"
        headline = f"มีเหตุการณ์ค่อนข้างถี่ ({total} ครั้งใน {window_hours} ชม.)"
        summary = (
            f"ในช่วง {window_hours} ชั่วโมงที่ผ่านมา พบเหตุการณ์ทั้งหมด {total} ครั้ง "
            f"(เสียง {sound_total} ครั้ง, ความปลอดภัย {safety} ครั้ง) "
            f"ช่วงที่สงบต่อเนื่องนานที่สุดคือ {quiet_str}"
        )
        patterns = [
            f"พบเหตุการณ์เสียง {sound_total} ครั้ง และแจ้งเตือนความปลอดภัย {safety} ครั้ง",
            f"ช่วงสงบต่อเนื่องยาวสุด {quiet_str}",
        ]
        recs = ["เปิดดูคลิปช่วงที่มีแจ้งเตือนถี่หรือแจ้งเตือนความปลอดภัยเพื่อตรวจสอบเพิ่มเติม"]
    else:
        quality = "FAIR"
        headline = f"พักผ่อนได้พอใช้ มีเหตุการณ์ {total} ครั้ง (สงบสุด {quiet_str})"
        summary = (
            f"ในช่วง {window_hours} ชั่วโมงที่ผ่านมา {subject_label}มีเหตุการณ์ทั้งหมด {total} ครั้ง "
            f"(เสียง {sound_total} ครั้ง) โดยมีช่วงสงบต่อเนื่องนานที่สุด {quiet_str}"
        )
        patterns = [
            f"ช่วงสงบต่อเนื่องนานที่สุด {quiet_str}",
            f"พบเหตุการณ์เสียง {sound_total} ครั้ง และไม่มีแจ้งเตือนความปลอดภัย",
        ]
        recs = ["เปิดใช้ GCP review เพื่อรับสรุปวิเคราะห์เชิงลึกจาก Gemini"]

    twins_insight = None
    if other_bed_events > 0:
        if cross_bed > 0:
            twins_insight = (
                f"พบเหตุการณ์ใกล้เคียงกันระหว่าง 2 เตียง {cross_bed} ครั้ง "
                f"(อีกเตียงมี {other_bed_events} เหตุการณ์ในช่วงเดียวกัน)"
            )
        else:
            twins_insight = f"อีกเตียงมี {other_bed_events} เหตุการณ์ในช่วงเดียวกัน แต่ไม่พบว่าปลุกกัน"

    return {
        "sleep_quality": quality,
        "headline_th": headline,
        "summary_th": summary,
        "key_patterns_th": patterns,
        "twins_insight_th": twins_insight,
        "recommendations_th": recs,
    }


class SleepSummaryStore:
    """Computes local sleep stats and persists Vertex AI sleep summaries on Edge."""

    def __init__(self, event_store: LocalEventStore) -> None:
        self.event_store = event_store
        self.summaries_dir = event_store.directory / "summaries"

    def _summary_file(self, baby_id: str, window_hours: int) -> Path:
        return self.summaries_dir / f"{baby_id}_{window_hours}h.json"

    def load_saved(self, baby_id: str, window_hours: int) -> dict[str, Any] | None:
        if baby_id not in BED_IDS or window_hours not in SUMMARY_WINDOWS:
            return None
        path = self._summary_file(baby_id, window_hours)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def save_summary(self, summary_doc: dict[str, Any]) -> None:
        baby_id = summary_doc.get("baby_id")
        window_hours = summary_doc.get("window_hours")
        if baby_id not in BED_IDS or window_hours not in SUMMARY_WINDOWS:
            raise ValueError("Invalid summary target")
        self.summaries_dir.mkdir(parents=True, exist_ok=True)
        target = self._summary_file(baby_id, int(window_hours))
        with tempfile.TemporaryDirectory(dir=self.summaries_dir) as directory:
            staged = Path(directory) / target.name
            staged.write_text(json.dumps(summary_doc, ensure_ascii=False, indent=2), encoding="utf-8")
            staged.replace(target)

    def find_by_id(self, summary_id: str) -> dict[str, Any] | None:
        if not SUMMARY_ID.fullmatch(summary_id) or not self.summaries_dir.is_dir():
            return None
        for path in self.summaries_dir.glob("baby_*_*h.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("summary_id") == summary_id:
                return data
        return None

    def collect_window_events(
        self, window_hours: int, now: datetime | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        current = now or datetime.now(timezone.utc)
        cutoff = current - timedelta(hours=window_hours)
        cutoff_ts = int(cutoff.timestamp()) - 3600
        grouped: dict[str, list[tuple[datetime, dict[str, Any]]]] = {bed: [] for bed in BED_IDS}

        if not self.event_store.directory.is_dir():
            return {bed: [] for bed in BED_IDS}

        for path in self.event_store.directory.glob("evt_*.json"):
            match = EVENT_TS.fullmatch(path.name)
            if match is None:
                continue
            if int(match[1]) < cutoff_ts:
                continue
            try:
                event = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            bed = event.get("baby_id")
            if bed not in grouped or event.get("simulated") or event.get("trigger_type") == "PERIODIC_CHECK":
                continue
            created = _parse_iso(event.get("created_at"))
            if created is None or created < cutoff or created > current + timedelta(minutes=5):
                continue
            grouped[bed].append((created, event))

        return {
            bed: [evt for _, evt in sorted(items, key=lambda pair: pair[0])]
            for bed, items in grouped.items()
        }

    def build_digest(
        self,
        baby_id: str,
        window_hours: int = DEFAULT_SUMMARY_WINDOW,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if baby_id not in BED_IDS:
            raise ValueError("Invalid baby_id")
        if window_hours not in SUMMARY_WINDOWS:
            raise ValueError("Invalid window_hours")

        current = now or datetime.now(timezone.utc)
        window_start = current - timedelta(hours=window_hours)
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc

        all_events = self.collect_window_events(window_hours, now=current)
        bed_events = all_events[baby_id]
        other_bed_id = "baby_2" if baby_id == "baby_1" else "baby_1"
        other_events = all_events[other_bed_id]

        cry_count = 0
        dog_count = 0
        motion_count = 0
        safety_count = 0
        pet_signals = 0

        disturbance_times: list[datetime] = []
        for event in bed_events:
            trigger = str(event.get("trigger_type") or "").upper()
            metrics = event.get("edge_metrics") or {}
            audio_kind = metrics.get("audio_kind")
            if trigger == "CRY":
                if audio_kind == "DOG_VOCALIZATION":
                    dog_count += 1
                    pet_signals += 1
                else:
                    cry_count += 1
            elif trigger == "MOTION":
                motion_count += 1
            if _is_safety_alert(event):
                safety_count += 1
            if metrics.get("subject_type") == "PET" or event.get("subject_mode") == "PET_TEST":
                pet_signals += 1
            if _is_disturbance(event):
                created = _parse_iso(event.get("created_at"))
                if created is not None:
                    disturbance_times.append(created)

        # Longest quiet stretch without cries or safety alerts.
        anchors = [window_start, *sorted(disturbance_times), current]
        longest_delta = timedelta(0)
        quiet_start = window_start
        quiet_end = current
        for left, right in zip(anchors, anchors[1:]):
            gap = right - left
            if gap > longest_delta:
                longest_delta = gap
                quiet_start = left
                quiet_end = right
        longest_quiet_minutes = max(0, int(longest_delta.total_seconds() // 60))

        # Cross-bed wakeup correlation (within 5 minutes between beds).
        other_disturbances = [
            ts for evt in other_events if _is_disturbance(evt)
            for ts in [_parse_iso(evt.get("created_at"))] if ts is not None
        ]
        cross_bed_wakeups = 0
        for ts in disturbance_times:
            if any(abs((ts - other_ts).total_seconds()) <= 300 for other_ts in other_disturbances):
                cross_bed_wakeups += 1

        # Hourly (or 3-hour for 72h) activity buckets for the UI sparkbar and Gemini.
        bucket_hours = 3 if window_hours == 72 else 1
        bucket_count = window_hours // bucket_hours
        bucket_seconds = bucket_hours * 3600
        buckets: list[dict[str, Any]] = []
        for idx in range(bucket_count):
            b_start = window_start + timedelta(seconds=idx * bucket_seconds)
            local_start = b_start.astimezone(local_tz)
            buckets.append({
                "hour_start": b_start.isoformat(),
                "label": local_start.strftime("%H:%M"),
                "total": 0,
                "sound": 0,
                "safety": 0,
                "motion": 0,
            })

        for event in bed_events:
            created = _parse_iso(event.get("created_at"))
            if created is None:
                continue
            offset = (created - window_start).total_seconds()
            idx = min(bucket_count - 1, max(0, int(offset // bucket_seconds)))
            bucket = buckets[idx]
            bucket["total"] += 1
            trigger = str(event.get("trigger_type") or "").upper()
            if _is_safety_alert(event):
                bucket["safety"] += 1
            elif trigger == "CRY":
                bucket["sound"] += 1
            else:
                bucket["motion"] += 1

        subject_mode = "PET_TEST" if bed_events and pet_signals > len(bed_events) / 2 else "BABY"
        stats = {
            "total_events": len(bed_events),
            "cry_count": cry_count,
            "dog_vocalization_count": dog_count,
            "motion_count": motion_count,
            "safety_alert_count": safety_count,
            "longest_quiet_minutes": longest_quiet_minutes,
            "longest_quiet_start": quiet_start.isoformat(),
            "longest_quiet_end": quiet_end.isoformat(),
            "cross_bed_wakeups": cross_bed_wakeups,
        }

        # Compact timeline for Vertex AI Gemini (prioritize safety/cry, cap at MAX_TIMELINE_EVENTS).
        compact_timeline: list[dict[str, Any]] = []
        for event in bed_events[-MAX_TIMELINE_EVENTS:]:
            created = _parse_iso(event.get("created_at"))
            metrics = event.get("edge_metrics") or {}
            ai = event.get("ai_analysis") or {}
            item: dict[str, Any] = {
                "time_local": created.astimezone(local_tz).strftime("%Y-%m-%d %H:%M") if created else "",
                "trigger": event.get("trigger_type"),
                "audio_kind": metrics.get("audio_kind"),
                "motion_score": metrics.get("motion_score"),
            }
            if metrics.get("face_cover_suspected"):
                item["face_cover_suspected"] = True
            if ai.get("title_th"):
                item["ai_title_th"] = ai["title_th"]
            if ai.get("sleep_state"):
                item["sleep_state"] = ai["sleep_state"]
            compact_timeline.append(item)

        other_bed_summary = {
            "baby_id": other_bed_id,
            "total_events": len(other_events),
            "disturbance_count": len(other_disturbances),
        }
        local_summary = _build_local_summary(
            window_hours=window_hours,
            subject_mode=subject_mode,
            stats=stats,
            other_bed_events=len(other_events),
        )

        return {
            "baby_id": baby_id,
            "window_hours": window_hours,
            "window_start": window_start.isoformat(),
            "window_end": current.isoformat(),
            "local_timezone": str(local_tz),
            "subject_mode": subject_mode,
            "stats": stats,
            "hourly_activity": buckets,
            "other_bed": other_bed_summary,
            "compact_timeline": compact_timeline,
            "local_summary": local_summary,
        }

    def create_request(self, baby_id: str, window_hours: int = DEFAULT_SUMMARY_WINDOW) -> dict[str, Any]:
        digest = self.build_digest(baby_id, window_hours)
        now_iso = datetime.now(timezone.utc).isoformat()
        summary_id = f"sum_{int(datetime.now(timezone.utc).timestamp())}_{uuid.uuid4().hex[:6]}"
        return {
            "summary_id": summary_id,
            "baby_id": baby_id,
            "window_hours": window_hours,
            "created_at": now_iso,
            "analyzed_at": None,
            "status": "LOCAL_ONLY",
            "stats": digest["stats"],
            "hourly_activity": digest["hourly_activity"],
            "other_bed": digest["other_bed"],
            "subject_mode": digest["subject_mode"],
            "local_summary": digest["local_summary"],
            "ai_summary": None,
            "payload": {
                "baby_id": baby_id,
                "window_hours": window_hours,
                "window_start": digest["window_start"],
                "window_end": digest["window_end"],
                "local_timezone": digest["local_timezone"],
                "subject_mode": digest["subject_mode"],
                "stats": digest["stats"],
                "hourly_activity": digest["hourly_activity"],
                "other_bed": digest["other_bed"],
                "compact_timeline": digest["compact_timeline"],
            },
        }
