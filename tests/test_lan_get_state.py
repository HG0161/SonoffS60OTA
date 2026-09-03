import unittest

from tools.lan_get_state import result_messages


class LanGetStateMessageTests(unittest.TestCase):
    def test_explains_expected_s60_unsupported_response(self):
        messages = result_messages(
            {
                "http_status": 200,
                "http_reason": "OK",
                "reply": {"error": 400, "encrypt": True},
            }
        )

        self.assertEqual(messages[0], "LAN connection: PASS (HTTP 200 OK)")
        self.assertIn("NOT SUPPORTED", messages[1])
        self.assertIn("expected", messages[1])

    def test_reports_decrypted_success(self):
        messages = result_messages(
            {
                "http_status": 200,
                "http_reason": "OK",
                "reply": {"error": 0},
                "decrypted": {"switch": "off"},
            }
        )

        self.assertIn("PASS", messages[1])
        self.assertIn("decrypted", messages[1])

    def test_reports_http_failure(self):
        self.assertEqual(
            result_messages({"http_status": 503, "http_reason": "Unavailable"}),
            ["LAN connection: FAIL (HTTP 503 Unavailable)"],
        )


if __name__ == "__main__":
    unittest.main()
