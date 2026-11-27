"""Optional VAPID Web Push transport for the local Edge service."""

from __future__ import annotations

import base64
from contextlib import closing
import hmac
import json
import os
from pathlib import Path
from queue import Full, Queue
import re
import sqlite3
import threading
from typing import Any
from urllib.parse import urlsplit


EVENT_TITLES = {
    "CRY": "Possible crying detected",
    "MOTION": "Strong movement detected",
    "BREATHING_ANOMALY": "Possible irregular breathing",
    "BAND_ROLLOVER": "Possible rollover or covered face",
}
BEDS = {"baby_1": "Bed 1", "baby_2": "Bed 2"}
BASE64URL = re.compile(r"[A-Za-z0-9_-]+")


def supported_endpoint(endpoint: Any) -> bool:
    if not isinstance(endpoint, str) or len(endpoint) > 2048 or not endpoint.isascii():
        return False
    if any(character.isspace() or ord(character) < 32 for character in endpoint) or "\\" in endpoint:
        return False
    try:
        url = urlsplit(endpoint)
        host = url.hostname or ""
        valid_host = (
            host in {"fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"}
            or host.endswith((".push.apple.com", ".notify.windows.com"))
        )
        return bool(
            url.scheme == "https" and valid_host and url.port in (None, 443)
            and url.username is None and url.password is None and url.path not in ("", "/")
            and not url.fragment
        )
    except ValueError:
        return False


def decode_key(value: Any, size: int) -> bytes:
    if not isinstance(value, str) or len(value) != (size * 8 + 5) // 6 or not BASE64URL.fullmatch(value):
        raise ValueError("Invalid subscription key")
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if len(decoded) != size:
        raise ValueError("Invalid subscription key")
    return decoded


def subscription_fields(data: dict[str, Any]) -> tuple[str, str, str]:
    endpoint = data.get("endpoint")
    keys = data.get("keys")
    if not supported_endpoint(endpoint) or not isinstance(keys, dict):
        raise ValueError("Unsupported push subscription")
    auth = keys.get("auth")
    p256dh = keys.get("p256dh")
    decode_key(auth, 16)
    if not decode_key(p256dh, 65).startswith(b"\x04"):
        raise ValueError("Invalid subscription key")
    return endpoint, p256dh, auth


class WebPushService:
    def __init__(self, directory: Path, key_file: Path, subject: str, setup_code: str) -> None:
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid, VapidException
        from pywebpush import WebPushException, webpush
        from requests import RequestException, Session

        class PushSession(Session):
            def post(self, url, **kwargs):
                return super().post(url, allow_redirects=False, **kwargs)

        try:
            self.key = Vapid.from_file(str(key_file))
        except VapidException as exc:
            raise ValueError("Invalid VAPID private key") from exc
        public = self.key.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        if len(public) != 65:
            raise ValueError("Web Push requires a P-256 VAPID key")
        self.public_key = base64.urlsafe_b64encode(public).decode("ascii").rstrip("=")
        self.subject = subject
        self.setup_code = setup_code
        self.webpush = webpush
        self.webpush_error = WebPushException
        self.request_error = RequestException
        self.session = PushSession()
        self.db_path = directory / "push_subscriptions.sqlite3"
        directory.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS subscriptions "
                "(endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL)"
            )
        self.pending: Queue[str] = Queue(maxsize=128)
        threading.Thread(target=self._run, daemon=True, name="web-push").start()

    @classmethod
    def from_env(cls, directory: Path) -> WebPushService | None:
        subject = os.environ.get("WEB_PUSH_SUBJECT", "").strip()
        code = os.environ.get("WEB_PUSH_SETUP_CODE", "").strip()
        if not subject and not code:
            return None
        if len(code) < 24 or not code.isascii() or not subject.startswith(("mailto:", "https://")):
            print("[WebPush] Disabled: set a contact address and a setup code of at least 24 ASCII characters.")
            return None
        key_file = Path(os.environ.get("WEB_PUSH_KEY_FILE", "/run/lullabai-push/vapid.pem"))
        try:
            return cls(directory, key_file, subject, code)
        except (ImportError, OSError, ValueError, sqlite3.Error) as exc:
            print(f"[WebPush] Disabled: could not initialize VAPID or subscription database ({type(exc).__name__}).")
            return None

    def can_enroll(self, code: str) -> bool:
        return hmac.compare_digest(code.encode("utf-8"), self.setup_code.encode("utf-8"))

    def enroll(self, fields: tuple[str, str, str]) -> None:
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute(
                "INSERT INTO subscriptions (endpoint, p256dh, auth) VALUES (?, ?, ?) "
                "ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth",
                fields,
            )

    def contains(self, fields: tuple[str, str, str]) -> bool:
        with closing(sqlite3.connect(self.db_path)) as db:
            return db.execute(
                "SELECT 1 FROM subscriptions WHERE endpoint=? AND p256dh=? AND auth=?", fields
            ).fetchone() is not None

    def remove(self, fields: tuple[str, str, str]) -> None:
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("DELETE FROM subscriptions WHERE endpoint=? AND p256dh=? AND auth=?", fields)

    def submit(self, event: dict[str, Any]) -> None:
        trigger = event["trigger_type"]
        title = EVENT_TITLES.get(trigger)
        if not title:
            return
        ai = event.get("ai_analysis") or {}
        metrics = event.get("edge_metrics", {})
        bed = event["baby_id"]
        bed_label = BEDS.get(bed, bed)
        if ai.get("title_th"):
            title = str(ai["title_th"])
            action = str(ai.get("recommended_action_th") or "").strip()
            body = f"{bed_label} · {action}" if action else bed_label
        else:
            if trigger == "CRY" and metrics.get("audio_kind") == "DOG_VOCALIZATION":
                title = "Possible dog vocalization"
            elif trigger == "BAND_ROLLOVER" and metrics.get("face_cover_suspected"):
                title = "Possible blanket over dog's face"
            body = bed_label
        payload = json.dumps({"id": event["event_id"], "bed": bed, "title": title, "body": body}, ensure_ascii=False)
        try:
            self.pending.put_nowait(payload)
        except Full:
            print("[WebPush] Delivery queue full; event remains available on the dashboard.")

    def submit_test(self, baby_id: str = "baby_1") -> None:
        bed = baby_id if baby_id in BEDS else "baby_1"
        self.submit({
            "event_id": f"test_push_{bed}",
            "baby_id": bed,
            "trigger_type": "CRY",
            "ai_analysis": {
                "title_th": "ทดสอบแจ้งเตือนจาก Vertex AI",
                "recommended_action_th": "ระบบแจ้งเตือนผ่าน GCP พร้อมใช้งาน",
            },
        })

    def _run(self) -> None:
        while True:
            payload = self.pending.get()
            try:
                self._deliver(payload)
            except (OSError, sqlite3.Error) as exc:
                print(f"[WebPush] Could not send alert ({type(exc).__name__}).")
            finally:
                self.pending.task_done()

    def _deliver(self, payload: str) -> None:
        with closing(sqlite3.connect(self.db_path)) as db:
            subscriptions = db.execute("SELECT endpoint, p256dh, auth FROM subscriptions").fetchall()
        for endpoint, p256dh, auth in subscriptions:
            if not supported_endpoint(endpoint):
                continue
            try:
                self.webpush(
                    subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
                    data=payload,
                    vapid_private_key=self.key,
                    vapid_claims={"sub": self.subject},
                    requests_session=self.session,
                    ttl=120,
                    timeout=8,
                )
            except self.webpush_error as exc:
                code = exc.response.status_code if exc.response is not None else None
                if code in (404, 410):
                    self.remove((endpoint, p256dh, auth))
                else:
                    print(f"[WebPush] Delivery failed (HTTP {code if code else 'unknown'}).")
            except (ValueError, self.request_error) as exc:
                print(f"[WebPush] Delivery failed ({type(exc).__name__}).")
