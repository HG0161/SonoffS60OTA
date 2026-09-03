import http.client
import threading
import unittest

from tools.serve_tasmota_ota import FirmwareServer, parse_single_range


class FirmwareServerTests(unittest.TestCase):
    def setUp(self):
        self.firmware = bytes(range(100))
        self.server = FirmwareServer(("127.0.0.1", 0), self.firmware, "127.0.0.1")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path="/user1.bin", byte_range=None):
        headers = {} if byte_range is None else {"Range": byte_range}
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=2
        )
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            return response.status, response.getheaders(), response.read()
        finally:
            connection.close()

    def test_serves_only_exact_firmware_paths(self):
        status, _, body = self.request("/wrong.bin")
        self.assertEqual((status, body), (404, b""))
        status, _, body = self.request("/user1.bin?ignored=query")
        self.assertEqual((status, body), (200, self.firmware))

    def test_rejects_wrong_source(self):
        self.server.device_ip = "192.0.2.55"
        status, _, body = self.request()
        self.assertEqual((status, body), (403, b""))
        self.assertEqual(self.server.bytes_served, 0)

    def test_valid_range_and_unique_coverage(self):
        status, headers, body = self.request(byte_range="bytes=0-23")
        self.assertEqual(status, 206)
        self.assertIn(("Content-Range", "bytes 0-23/100"), headers)
        self.assertEqual(body, self.firmware[:24])
        self.request(byte_range="bytes=20-99")
        self.assertEqual(self.server.bytes_served, 104)
        self.assertEqual(self.server.unique_bytes_served(), 100)

    def test_invalid_ranges_return_416_without_bytes(self):
        for value in ("items=0-1", "bytes=-10", "bytes=10-9", "bytes=0-100", "bytes=0-1,3-4"):
            with self.subTest(value=value):
                status, headers, body = self.request(byte_range=value)
                self.assertEqual((status, body), (416, b""))
                self.assertIn(("Content-Range", "bytes */100"), headers)
        self.assertEqual(self.server.bytes_served, 0)

    def test_range_parser_accepts_open_end(self):
        self.assertEqual(parse_single_range("bytes=24-", 100), (24, 99))


if __name__ == "__main__":
    unittest.main()
