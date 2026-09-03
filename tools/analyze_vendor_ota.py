#!/usr/bin/env python3
"""Validate a Sonoff S60 vendor OTA wrapper and its ESP32-C3 app image."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path
from typing import Any


WRAPPER_SIZE = 100
FRAME_HEADER_SIZE = 24
METADATA_RECORD_SIZE = 76
ESP_MAGIC = 0xE9
ESP32_C3_CHIP_ID = 5


def _cstring(data: bytes) -> str:
    return data.split(b"\0", 1)[0].decode("ascii", errors="replace")


def parse_esp_image(image: bytes) -> dict[str, Any]:
    if len(image) < 24 or image[0] != ESP_MAGIC:
        raise ValueError("payload is not an ESP application image")

    segment_count = image[1]
    if not 1 <= segment_count <= 16:
        raise ValueError(f"invalid ESP segment count: {segment_count}")

    position = 24
    checksum = 0xEF
    segments: list[dict[str, int]] = []
    for index in range(segment_count):
        if position + 8 > len(image):
            raise ValueError(f"truncated segment header {index}")
        load_address, size = struct.unpack_from("<II", image, position)
        data_offset = position + 8
        end = data_offset + size
        if end > len(image):
            raise ValueError(f"truncated segment data {index}")
        for value in image[data_offset:end]:
            checksum ^= value
        segments.append(
            {
                "index": index,
                "header_offset": position,
                "data_offset": data_offset,
                "load_address": load_address,
                "size": size,
            }
        )
        position = end

    # ESP images pad so the one-byte XOR checksum ends on a 16-byte boundary.
    checksum_offset = ((position // 16) + 1) * 16 - 1
    if checksum_offset >= len(image):
        raise ValueError("missing ESP image checksum")
    stored_checksum = image[checksum_offset]

    hash_appended = image[23] == 1
    image_end = checksum_offset + 1
    stored_sha256 = None
    computed_sha256 = None
    sha256_valid = None
    if hash_appended:
        if image_end + 32 > len(image):
            raise ValueError("missing appended ESP image SHA-256")
        stored_sha256 = image[image_end : image_end + 32].hex()
        computed_sha256 = hashlib.sha256(image[:image_end]).hexdigest()
        sha256_valid = stored_sha256 == computed_sha256
        image_end += 32

    return {
        "magic": f"0x{image[0]:02x}",
        "segment_count": segment_count,
        "entry_address": f"0x{struct.unpack_from('<I', image, 4)[0]:08x}",
        "chip_id": struct.unpack_from("<H", image, 12)[0],
        "chip_is_esp32_c3": struct.unpack_from("<H", image, 12)[0] == ESP32_C3_CHIP_ID,
        "hash_appended": hash_appended,
        "segments": segments,
        "checksum_offset": checksum_offset,
        "stored_checksum": f"0x{stored_checksum:02x}",
        "computed_checksum": f"0x{checksum:02x}",
        "checksum_valid": stored_checksum == checksum,
        "stored_sha256": stored_sha256,
        "computed_sha256": computed_sha256,
        "sha256_valid": sha256_valid,
        "parsed_image_size": image_end,
        "trailing_bytes": len(image) - image_end,
    }


def analyze(data: bytes) -> dict[str, Any]:
    if len(data) < WRAPPER_SIZE:
        raise ValueError("file is shorter than the 100-byte Sonoff wrapper")

    header = data[:WRAPPER_SIZE]
    payload = data[WRAPPER_SIZE:]
    payload_offset = struct.unpack_from(">I", header, 0x48)[0]
    declared_size = struct.unpack_from(">I", header, 0x4C)[0]
    stored_payload_crc32 = struct.unpack_from(">I", header, 0x50)[0]
    stored_header_crc32 = struct.unpack_from(">I", header, 0x14)[0]
    stored_record_crc32 = struct.unpack_from(">I", header, 0x54)[0]
    computed_header_crc32 = zlib.crc32(header[:0x14]) & 0xFFFFFFFF
    computed_record_crc32 = zlib.crc32(header[0x18:0x54]) & 0xFFFFFFFF
    computed_payload_crc32 = zlib.crc32(payload) & 0xFFFFFFFF

    return {
        "file_size": len(data),
        "file_sha256": hashlib.sha256(data).hexdigest(),
        "wrapper": {
            "format_version": header[0],
            "image_count": header[1],
            "magic": header[:2].hex(),
            "magic_valid": header[:2] == b"\x03\x01",
            "header_version": _cstring(header[2:10]),
            "header_reserved_zero": header[10:20] == bytes(10),
            "stored_header_crc32": f"{stored_header_crc32:08x}",
            "computed_header_crc32": f"{computed_header_crc32:08x}",
            "header_crc32_valid": stored_header_crc32 == computed_header_crc32,
            "model": _cstring(header[0x18:0x38]),
            "record_version": _cstring(header[0x38:0x48]),
            "payload_offset": payload_offset,
            "payload_offset_valid": payload_offset == WRAPPER_SIZE,
            "declared_payload_size": declared_size,
            "actual_payload_size": len(payload),
            "payload_size_valid": declared_size == len(payload),
            "stored_payload_crc32": f"{stored_payload_crc32:08x}",
            "computed_payload_crc32": f"{computed_payload_crc32:08x}",
            "payload_crc32_valid": stored_payload_crc32 == computed_payload_crc32,
            "stored_record_crc32": f"{stored_record_crc32:08x}",
            "computed_record_crc32": f"{computed_record_crc32:08x}",
            "record_crc32_valid": stored_record_crc32 == computed_record_crc32,
            "record_reserved_zero": header[0x58:0x64] == bytes(12),
        },
        "esp_image": parse_esp_image(payload),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    args = parser.parse_args()
    try:
        result = analyze(args.image.read_bytes())
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    wrapper = result["wrapper"]
    esp = result["esp_image"]
    checks = (
        wrapper["magic_valid"],
        wrapper["header_reserved_zero"],
        wrapper["header_crc32_valid"],
        wrapper["payload_offset_valid"],
        wrapper["payload_size_valid"],
        wrapper["payload_crc32_valid"],
        wrapper["record_crc32_valid"],
        wrapper["record_reserved_zero"],
        esp["chip_is_esp32_c3"],
        esp["checksum_valid"],
        esp["sha256_valid"] is not False,
        esp["trailing_bytes"] == 0,
    )
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
