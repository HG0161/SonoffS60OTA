#!/usr/bin/env python3
"""Offline ESP32-C3 stock-flash triage for the Sonoff S60."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Any


PARTITION_MAGIC = 0x50AA
PARTITION_ENTRY_SIZE = 32
DEFAULT_PARTITION_OFFSET = 0x8000

PARTITION_TYPES = {0x00: "app", 0x01: "data"}
APP_SUBTYPES = {
    0x00: "factory",
    0x10: "ota_0",
    0x11: "ota_1",
    0x12: "ota_2",
    0x13: "ota_3",
    0x14: "ota_4",
    0x15: "ota_5",
    0x16: "ota_6",
    0x17: "ota_7",
    0x18: "ota_8",
    0x19: "ota_9",
    0x1A: "ota_10",
    0x1B: "ota_11",
    0x1C: "ota_12",
    0x1D: "ota_13",
    0x1E: "ota_14",
    0x1F: "ota_15",
    0x20: "test",
}
DATA_SUBTYPES = {
    0x00: "ota",
    0x01: "phy",
    0x02: "nvs",
    0x03: "coredump",
    0x04: "nvs_keys",
    0x05: "efuse",
    0x80: "esphttpd",
    0x81: "fat",
    0x82: "spiffs",
}

INTERESTING = re.compile(
    rb"(?:ota|upgrade|update|firmware|https?://|sha(?:256)?|digest|signature|"
    rb"certificate|x509|public[ _-]?key|secure[_ -]?version|anti[_ -]?rollback|"
    rb"zeroconf|ewelink|coolkit|user[12]\.bin)",
    re.IGNORECASE,
)
ASCII_RUN = re.compile(rb"[\x20-\x7e]{6,}")


def _subtype_name(ptype: int, subtype: int) -> str:
    if ptype == 0x00:
        return APP_SUBTYPES.get(subtype, f"0x{subtype:02x}")
    if ptype == 0x01:
        return DATA_SUBTYPES.get(subtype, f"0x{subtype:02x}")
    return f"0x{subtype:02x}"


def parse_partitions(data: bytes, offset: int = DEFAULT_PARTITION_OFFSET) -> list[dict[str, Any]]:
    """Parse an ESP-IDF binary partition table."""
    result: list[dict[str, Any]] = []
    cursor = offset
    while cursor + PARTITION_ENTRY_SIZE <= len(data):
        entry = data[cursor : cursor + PARTITION_ENTRY_SIZE]
        magic = struct.unpack_from("<H", entry)[0]
        if magic in (0xFFFF, 0x0000):
            break
        if magic != PARTITION_MAGIC:
            break
        _, ptype, subtype, part_offset, size, raw_label, flags = struct.unpack(
            "<HBBII16sI", entry
        )
        label = raw_label.split(b"\0", 1)[0].decode("ascii", errors="replace")
        end = part_offset + size
        result.append(
            {
                "label": label,
                "type": PARTITION_TYPES.get(ptype, f"0x{ptype:02x}"),
                "subtype": _subtype_name(ptype, subtype),
                "offset": part_offset,
                "size": size,
                "end": end,
                "flags": flags,
                "within_dump": end <= len(data),
                "esp_image_header": end <= len(data) and data[part_offset] == 0xE9,
            }
        )
        cursor += PARTITION_ENTRY_SIZE
    return result


def interesting_strings(data: bytes, limit: int = 500) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for match in ASCII_RUN.finditer(data):
        value = match.group()
        if INTERESTING.search(value):
            matches.append(
                {
                    "offset": match.start(),
                    "text": value.decode("ascii", errors="replace")[:1000],
                }
            )
            if len(matches) >= limit:
                break
    return matches


def analyze(path: Path, partition_offset: int = DEFAULT_PARTITION_OFFSET) -> dict[str, Any]:
    data = path.read_bytes()
    partitions = parse_partitions(data, partition_offset)
    return {
        "file": str(path),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "looks_like_full_4mb_dump": len(data) == 0x400000,
        "partition_table_offset": partition_offset,
        "partitions": partitions,
        "warnings": _warnings(data, partitions),
        "interesting_strings": interesting_strings(data),
    }


def _warnings(data: bytes, partitions: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if len(data) != 0x400000:
        warnings.append("Expected a full 4 MB (0x400000-byte) S60 dump.")
    if not partitions:
        warnings.append("No ESP-IDF partition table found at the selected offset.")
    if any(not p["within_dump"] for p in partitions):
        warnings.append("One or more partitions extend beyond the supplied dump.")
    ota_apps = [p for p in partitions if p["type"] == "app" and p["subtype"].startswith("ota_")]
    if len(ota_apps) < 2:
        warnings.append("Fewer than two OTA application slots were found.")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path, help="full ESP32-C3 flash dump")
    parser.add_argument("--partition-offset", type=lambda v: int(v, 0), default=DEFAULT_PARTITION_OFFSET)
    parser.add_argument("--output", type=Path, help="write JSON report to this path")
    args = parser.parse_args()

    report = analyze(args.dump, args.partition_offset)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

