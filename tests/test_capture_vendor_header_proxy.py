import http.client
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.capture_vendor_header_proxy import StrictRelayServer


class HeaderProxyTests(unittest.TestCase):
    def start_server(self, source_ip="127.0.0.1", fetcher=None):
        self.temp = TemporaryDirectory()
        kwargs = {}
        if fetcher is not None:
            kwargs["fetcher"] = fetcher
        self.server = StrictRelayServer(
            ("127.0.0.1", 0), source_ip=source_ip, upstream_host="52.57.99.135",
            upstream_port=8088, expected_path="/ota/rom/token/user1.bin",
            output=Path(self.temp.name) / "header.bin", **kwargs,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
            self.temp.cleanup()

    def request(self, *, path="/ota/rom/token/user1.bin", host="52.57.99.135:8088", byte_range="bytes=0-23", agent="itead-device"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=2)
        try:
            connection.request("GET", path, headers={"Host": host, "Range": byte_range, "User-Agent": agent})
            response = connection.getresponse()
            return response.status, response.getheader("Content-Length"), response.read()
        finally:
            connection.close()

    def test_wrong_source_is_rejected_without_calling_upstream(self):
        calls = []
        self.start_server(source_ip="192.0.2.50", fetcher=lambda *args: calls.append(args))
        status, length, body = self.request()
        self.assertEqual((status, length, body), (403, "0", b""))
        self.assertEqual(calls, [])

    def test_wrong_path_range_agent_or_host_is_rejected(self):
        self.start_server(fetcher=lambda *args: self.fail("upstream must not be called"))
        variants = [
            {"path": "/wrong"}, {"byte_range": "bytes=0-24"},
            {"agent": "curl"}, {"host": "example.test"},
        ]
        for values in variants:
            with self.subTest(values=values):
                self.assertEqual(self.request(**values), (403, "0", b""))

    def test_exact_1024_bytes_are_saved_but_never_returned_to_device(self):
        header = bytes(range(256)) * 4
        self.start_server(fetcher=lambda *args: {
            "status": 206, "reason": "Partial Content", "content_length": "1024",
            "content_range": "bytes 0-1023/1456404", "content_type": "application/octet-stream", "body": header,
        })
        status, length, body = self.request()
        self.assertEqual((status, length, body), (502, "0", b""))
        self.assertEqual(self.server.output.read_bytes(), header)
        self.assertTrue(self.server.captured.is_set())
        self.assertEqual(self.server.records[0]["firmware_bytes_returned_to_device"], 0)

    def test_empty_400_is_not_saved(self):
        self.start_server(fetcher=lambda *args: {
            "status": 400, "reason": "Bad Request", "content_length": "0",
            "content_range": None, "content_type": None, "body": b"",
        })
        self.assertEqual(self.request(), (502, "0", b""))
        self.assertFalse(self.server.output.exists())
        self.assertFalse(self.server.captured.is_set())


if __name__ == "__main__":
    unittest.main()
