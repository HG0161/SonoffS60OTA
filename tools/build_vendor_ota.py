#!/usr/bin/env python3
"""Build the decoded 100-byte Sonoff S60 OTA wrapper around an ESP app.

This tool only reads and writes local files. It cannot contact or update a
device. Its output still requires separate compatibility and rollback review
before it is safe to serve to an S60.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import zlib
from pathlib import Path

try:
    from tools.analyze_vendor_ota import ESP32_C3_CHIP_ID, WRAPPER_SIZE, parse_esp_image
except ModuleNotFoundError:
    from analyze_vendor_ota import ESP32_C3_CHIP_ID, WRAPPER_SIZE, parse_esp_image


DEFAULT_MODEL = "FWSW-E32S60-S60-ESP32C3FN4"
DEFAULT_MAX_PAYLOAD_SIZE = 0x1F0000


def _fixed_ascii(value: str, size: int, field: str) -> bytes:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must contain ASCII only") from exc
    if not encoded or len(encoded) >= size:
        raise ValueError(f"{field} must be 1..{size - 1} ASCII bytes")
    return encoded + bytes(size - len(encoded))


def build_wrapper(
    payload: bytes,
    *,
    version: str,
    model: str = DEFAULT_MODEL,
    max_payload_size: int = DEFAULT_MAX_PAYLOAD_SIZE,
) -> bytes:
    """Return one complete S60 OTA file (100-byte wrapper plus payload)."""
    if len(payload) > max_payload_size:
        raise ValueError(
            f"payload is {len(payload):,} bytes; stock slot limit is "
            f"{max_payload_size:,} bytes"
        )

    esp = parse_esp_image(payload)
    if not esp["chip_is_esp32_c3"]:
        raise ValueError(f"ESP image chip id is not ESP32-C3 ({ESP32_C3_CHIP_ID})")
    if not esp["checksum_valid"] or esp["sha256_valid"] is False:
        raise ValueError("ESP image checksum or appended SHA-256 is invalid")
    if esp["trailing_bytes"]:
        raise ValueError(
            f"ESP image has {esp['trailing_bytes']:,} unexplained trailing bytes"
        )

    wrapper = bytearray(WRAPPER_SIZE)

    # 24-byte frame header: format version, record count, target version,
    # reserved bytes, then big-endian CRC-32 of the first 20 bytes.
    wrapper[0] = 3
    wrapper[1] = 1
    wrapper[2:10] = _fixed_ascii(version, 8, "version")
    struct.pack_into(">I", wrapper, 0x14, zlib.crc32(wrapper[:0x14]) & 0xFFFFFFFF)

    # One 76-byte metadata record. Its final 12 bytes are reserved zeros.
    wrapper[0x18:0x38] = _fixed_ascii(model, 32, "model")
    wrapper[0x38:0x48] = _fixed_ascii(version, 16, "version")
    struct.pack_into(">I", wrapper, 0x48, WRAPPER_SIZE)
    struct.pack_into(">I", wrapper, 0x4C, len(payload))
    struct.pack_into(">I", wrapper, 0x50, zlib.crc32(payload) & 0xFFFFFFFF)
    struct.pack_into(">I", wrapper, 0x54, zlib.crc32(wrapper[0x18:0x54]) & 0xFFFFFFFF)

    return bytes(wrapper) + payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path, help="ESP32-C3 application .bin")
    parser.add_argument("output", type=Path, help="output .ota file")
    parser.add_argument("--version", required=True, help="version presented to stock updater")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-payload-size", type=lambda value: int(value, 0), default=DEFAULT_MAX_PAYLOAD_SIZE)
    args = parser.parse_args()

    try:
        result = build_wrapper(
            args.payload.read_bytes(),
            version=args.version,
            model=args.model,
            max_payload_size=args.max_payload_size,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(result)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    print(f"Wrote {len(result):,} bytes to {args.output}")
    print(f"SHA-256 (complete wrapped file): {hashlib.sha256(result).hexdigest()}")
    print("Offline build only; no device command was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
