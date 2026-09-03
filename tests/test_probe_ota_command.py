import unittest
from types import SimpleNamespace

from tools.probe_ota_command import CaptureHandler


class OtaCommandProbeTests(unittest.TestCase):
    def test_all_plausible_methods_serve_zero_bytes(self):
        for method in ("GET", "HEAD", "POST", "PUT"):
            with self.subTest(method=method):
                handler = object.__new__(CaptureHandler)
                handler.command = method
                handler.path = "/probe.bin"
                handler.client_address = ("127.0.0.1", 12345)
                handler.headers = {}
                handler.server = SimpleNamespace(captures=[])
                calls = []
                handler.send_response = lambda status: calls.append(("status", status))
                handler.send_header = lambda name, value: calls.append((name, value))
                handler.end_headers = lambda: calls.append(("end", None))

                getattr(CaptureHandler, "do_" + method)(handler)

                self.assertIn(("status", 404), calls)
                self.assertIn(("Content-Length", "0"), calls)
                self.assertEqual(handler.server.captures[0]["method"], method)


if __name__ == "__main__":
    unittest.main()
