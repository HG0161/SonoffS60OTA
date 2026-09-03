import hashlib
import struct
import unittest
import zlib

from tools.analyze_vendor_ota import analyze, validation_checks
from tools.build_vendor_ota import build_wrapper


def make_esp_image() -> bytes:
    header = bytearray(24)
    header[0] = 0xE9
    header[1] = 1
    struct.pack_into("<I", header, 4, 0x40380000)
    struct.pack_into("<H", header, 12, 5)
    header[23] = 1
    segment = bytes(range(32))
    body = bytes(header) + struct.pack("<II", 0x3C000020, len(segment)) + segment
    checksum = 0xEF
    for value in segment:
        checksum ^= value
    checksum_offset = ((len(body) // 16) + 1) * 16 - 1
    body += bytes(checksum_offset - len(body)) + bytes([checksum])
    return body + hashlib.sha256(body).digest()


class VendorOtaAnalysisTests(unittest.TestCase):
    def test_valid_wrapper_and_esp_image(self):
        payload = make_esp_image()
        wrapped = build_wrapper(payload, version="9.9.9", model="TEST-MODEL")
        result = analyze(wrapped)

        self.assertTrue(result["wrapper"]["header_crc32_valid"])
        self.assertTrue(result["wrapper"]["record_crc32_valid"])
        self.assertTrue(result["wrapper"]["payload_size_valid"])
        self.assertTrue(result["wrapper"]["payload_crc32_valid"])
        self.assertTrue(result["esp_image"]["checksum_valid"])
        self.assertTrue(result["esp_image"]["sha256_valid"])
        self.assertEqual(result["esp_image"]["trailing_bytes"], 0)
        self.assertTrue(all(passed for _, passed in validation_checks(result)))

    def test_payload_mutation_breaks_payload_crc_only(self):
        wrapped = bytearray(build_wrapper(make_esp_image(), version="9.9.9"))
        wrapped[-1] ^= 1
        result = analyze(bytes(wrapped))

        self.assertTrue(result["wrapper"]["header_crc32_valid"])
        self.assertTrue(result["wrapper"]["record_crc32_valid"])
        self.assertFalse(result["wrapper"]["payload_crc32_valid"])
        self.assertFalse(result["esp_image"]["sha256_valid"])
        failed = {label for label, passed in validation_checks(result) if not passed}
        self.assertEqual(failed, {"Payload CRC-32", "ESP image SHA-256"})

    def test_rejects_image_with_trailing_bytes(self):
        with self.assertRaisesRegex(ValueError, "trailing bytes"):
            build_wrapper(make_esp_image() + b"padding", version="9.9.9")

    def test_rejects_payload_over_slot_size(self):
        with self.assertRaisesRegex(ValueError, "slot limit"):
            build_wrapper(make_esp_image(), version="9.9.9", max_payload_size=8)


if __name__ == "__main__":
    unittest.main()
