import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path

from tools.get_device_key import (
    APP_SECRET,
    EwelinkError,
    select_device,
    signed_login_body,
    target_id_from_mdns,
    write_secret,
)


class DeviceKeyTests(unittest.TestCase):
    def test_login_signature_covers_exact_body(self):
        body, headers = signed_login_body("owner@example.test", "not-a-real-password", "+44")
        expected = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).digest()
        self.assertEqual(json.loads(body)["email"], "owner@example.test")
        self.assertTrue(headers["Authorization"].startswith("Sign "))
        import base64
        self.assertEqual(base64.b64decode(headers["Authorization"][5:]), expected)

    def test_extracts_one_id_from_repeated_mdns_packets(self):
        capture = {
            "packets": [
                {"records": [{"type": "TXT", "value": ["id=abc123", "encrypt=true"]}]},
                {"records": [{"type": "TXT", "value": ["id=abc123"]}]},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mdns.json"
            path.write_text(json.dumps(capture))
            self.assertEqual(target_id_from_mdns(path), "abc123")

    def test_selects_device_and_requires_key(self):
        devices = [{"deviceid": "one", "devicekey": "secret"}, {"deviceid": "two"}]
        self.assertEqual(select_device(devices, "one")["devicekey"], "secret")
        with self.assertRaises(EwelinkError):
            select_device(devices, "two")

    def test_secret_file_permissions(self):
        device = {"deviceid": "abc", "devicekey": "secret", "name": "test", "extra": {"uiid": 5}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key.json"
            write_secret(path, device)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text())["devicekey"], "secret")


if __name__ == "__main__":
    unittest.main()

