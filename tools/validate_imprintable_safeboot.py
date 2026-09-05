#!/usr/bin/env python3
"""Check a built image is publishable and can have credentials written into it.

Run before shipping a recovery Safeboot. It refuses an image that still carries
somebody's real network details, and refuses one the tool could not write into.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from tools.analyze_vendor_ota import parse_esp_image
    from tools.autoflash.imprint import (
        PASSWORD_FIELD_BYTES,
        PASSWORD_MARKER,
        SSID_FIELD_BYTES,
        SSID_MARKER,
        ImprintError,
        find_build_paths,
        find_field,
        imprint,
    )
except ModuleNotFoundError:  # running from inside tools/
    from analyze_vendor_ota import parse_esp_image
    from autoflash.imprint import (
        PASSWORD_FIELD_BYTES,
        PASSWORD_MARKER,
        SSID_FIELD_BYTES,
        SSID_MARKER,
        ImprintError,
        find_build_paths,
        find_field,
        imprint,
    )

SAFEBOOT_PARTITION_BYTES = 0xD0000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    args = parser.parse_args()

    try:
        image = args.image.read_bytes()
    except OSError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    checks: list[tuple[str, bool, str]] = []

    try:
        report = parse_esp_image(image)
        checks.append(("valid ESP application image", True, ""))
        checks.append(("targets ESP32-C3", report["chip_is_esp32_c3"], ""))
        checks.append(("checksum valid", report["checksum_valid"], ""))
        checks.append(("appended SHA-256 valid", report["sha256_valid"] is not False, ""))
    except ValueError as exc:
        checks.append(("valid ESP application image", False, str(exc)))

    checks.append((
        "fits the safeboot partition",
        len(image) <= SAFEBOOT_PARTITION_BYTES,
        f"{len(image):,} of {SAFEBOOT_PARTITION_BYTES:,} bytes",
    ))

    for name, marker, field in (
        ("network name", SSID_MARKER, SSID_FIELD_BYTES),
        ("password", PASSWORD_MARKER, PASSWORD_FIELD_BYTES),
    ):
        try:
            find_field(image, marker, field)
            checks.append((f"{name} placeholder present exactly once", True, ""))
        except ImprintError as exc:
            checks.append((f"{name} placeholder present exactly once", False, str(exc)))

    try:
        patched = imprint(image, "S" * 32, "P" * 63)
        round_trip = parse_esp_image(patched)
        ok = bool(round_trip["checksum_valid"]) and round_trip["sha256_valid"] is not False
        checks.append(("credentials can be written and the image stays valid", ok, ""))
        checks.append((
            "no credential survives into the shipped image",
            b"S" * 32 not in image and b"P" * 63 not in image,
            "",
        ))
    except (ImprintError, ValueError) as exc:
        checks.append(("credentials can be written and the image stays valid", False, str(exc)))

    leaked = find_build_paths(image)
    checks.append((
        "no build-machine paths baked in",
        not leaked,
        "" if not leaked else f"{len(leaked)} found, e.g. {leaked[0][:70].decode(errors='replace')}",
    ))

    print(f"{args.image}  ({len(image):,} bytes)\n")
    for name, ok, detail in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  - ' + detail if detail else ''}")
    passed = all(ok for _, ok, _ in checks)
    print(f"\n{'PUBLISHABLE' if passed else 'NOT PUBLISHABLE'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
