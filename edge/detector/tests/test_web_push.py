"""Regression tests for background notifications and subscription enrollment."""

import base64
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from web_push import WebPushService, subscription_fields, supported_endpoint


class WebPushEndpointTest(unittest.TestCase):
    def test_subscription_endpoints_are_restricted_to_push_services(self):
        for endpoint in (
            "https://fcm.googleapis.com/fcm/send/test",
            "https://updates.push.services.mozilla.com/wpush/v2/test",
            "https://web.push.apple.com/Qtest",
        ):
            self.assertTrue(supported_endpoint(endpoint))
        for endpoint in (
            "http://fcm.googleapis.com/fcm/send/test",
            "https://fcm.googleapis.com:8443/fcm/send/test",
            "https://fcm.googleapis.com@127.0.0.1/test",
            "https://updates.push.services.mozilla.com.example.com/test",
            "https://127.0.0.1/test",
        ):
            self.assertFalse(supported_endpoint(endpoint))


@unittest.skipUnless(importlib.util.find_spec("pywebpush"), "requires the Edge Web Push dependency")
class WebPushDeliveryTest(unittest.TestCase):
    def test_event_uses_vapid_and_expired_subscription_is_removed(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from requests import Response

        def encode(key: bytes) -> str:
            return base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / "vapid.pem"
            key.write_bytes(ec.generate_private_key(ec.SECP256R1()).private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
            service = WebPushService(root, key, "mailto:push@example.com", "test-code-with-24-characters")
            client_key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
            encode_key = encode(client_key)
            auth = encode(bytes(range(16)))
            endpoint = "https://fcm.googleapis.com/fcm/send/test"
            fields = subscription_fields({"endpoint": endpoint, "keys": {"p256dh": encode_key, "auth": auth}})
            self.assertTrue(service.can_enroll("test-code-with-24-characters"))
            self.assertFalse(service.can_enroll("wrong-code"))
            service.enroll(fields)
            self.assertTrue(service.contains(fields))

            posts = []

            def capture(url, **kwargs):
                posts.append((url, kwargs))
                response = Response()
                response.status_code = 410
                response.reason = "Gone"
                response._content = b"expired"
                return response

            service.session = SimpleNamespace(post=capture)
            service.submit({"trigger_type": "PERIODIC_CHECK"})
            service.submit({"trigger_type": "CRY", "baby_id": "baby_2", "event_id": "evt_test"})
            service.pending.join()

            self.assertEqual(len(posts), 1)
            sent_url, push_request = posts[0]
            self.assertEqual(sent_url, endpoint)
            self.assertIn("vapid ", push_request["headers"]["authorization"].lower())
            self.assertEqual(push_request["headers"]["content-encoding"], "aes128gcm")
            self.assertNotIn(json.dumps("Possible crying detected").encode(), push_request["data"])
            self.assertFalse(service.contains(fields))


if __name__ == "__main__":
    unittest.main()
