import struct
import tempfile
import unittest
from pathlib import Path

from archive.tools.analyze_dump import analyze, interesting_strings, parse_partitions


def partition_entry(ptype, subtype, offset, size, label, flags=0):
    encoded = label.encode("ascii").ljust(16, b"\0")
    return struct.pack("<HBBII16sI", 0x50AA, ptype, subtype, offset, size, encoded, flags)


class AnalyzeDumpTests(unittest.TestCase):
    def test_parses_ota_partitions(self):
        data = bytearray(b"\xff" * 0x400000)
        entries = b"".join(
            [
                partition_entry(1, 2, 0x9000, 0x6000, "nvs"),
                partition_entry(1, 0, 0xF000, 0x2000, "otadata"),
                partition_entry(0, 0x10, 0x20000, 0x1F0000, "ota_0"),
                partition_entry(0, 0x11, 0x210000, 0x1F0000, "ota_1"),
            ]
        )
        data[0x8000 : 0x8000 + len(entries)] = entries
        data[0x20000] = 0xE9
        data[0x210000] = 0xE9

        parsed = parse_partitions(bytes(data))

        self.assertEqual([p["label"] for p in parsed], ["nvs", "otadata", "ota_0", "ota_1"])
        self.assertTrue(parsed[2]["esp_image_header"])
        self.assertEqual(parsed[3]["offset"], 0x210000)

    def test_interesting_string_offsets(self):
        data = b"\0boring string\0https://ota.example/user1.bin\0"
        matches = interesting_strings(data)
        self.assertEqual(len(matches), 1)
        self.assertIn("user1.bin", matches[0]["text"])

    def test_report_warns_for_truncated_dump(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "short.bin"
            path.write_bytes(b"\xff" * 1024)
            report = analyze(path)
        self.assertFalse(report["looks_like_full_4mb_dump"])
        self.assertTrue(report["warnings"])


if __name__ == "__main__":
    unittest.main()

