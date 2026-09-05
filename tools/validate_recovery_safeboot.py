#!/usr/bin/env python3
"""Validate an S60 recovery Safeboot image without displaying credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

try:
    from tools.safeboot_migration import (
        OFFICIAL_TASMOTA_COMMIT,
        PINNED_ARTIFACTS,
        RECOVERY_MARKER,
        SAFEBOOT_SIZE,
        sha256_bytes,
        validate_native_image,
    )
except ModuleNotFoundError:
    from safeboot_migration import (
        OFFICIAL_TASMOTA_COMMIT,
        PINNED_ARTIFACTS,
        RECOVERY_MARKER,
        SAFEBOOT_SIZE,
        sha256_bytes,
        validate_native_image,
    )


def header_value(header: bytes, name: str) -> bytes:
    text = header.decode("ascii")
    match = re.search(rf"^#define {re.escape(name)} \"((?:\\x[0-9a-f]{{2}})+)\"$", text, re.MULTILINE)
    if not match:
        raise ValueError(f"private header has no encoded {name}")
    return bytes.fromhex(match.group(1).replace("\\x", ""))


def validate(image: bytes, header: bytes) -> dict[str, object]:
    report = validate_native_image(image, SAFEBOOT_SIZE, "recovery Safeboot")
    ssid = header_value(header, "STA_SSID1")
    password = header_value(header, "STA_PASS1")
    if RECOVERY_MARKER not in image:
        raise ValueError("recovery marker is absent from the image")
    if ssid not in image:
        raise ValueError("encoded SSID was not linked into the image")
    if password and password not in image:
        raise ValueError("encoded Wi-Fi password was not linked into the image")
    image_hash = sha256_bytes(image)
    if image_hash == PINNED_ARTIFACTS["safeboot"]["sha256"]:
        raise ValueError("recovery image unexpectedly equals official Safeboot")
    return {
        "schema": 1,
        "kind": "s60-volatile-wifi-recovery-safeboot",
        "tasmota_commit": OFFICIAL_TASMOTA_COMMIT,
        "size": len(image),
        "sha256": image_hash,
        "max_size": SAFEBOOT_SIZE,
        "headroom": SAFEBOOT_SIZE - len(image),
        "esp_image": report,
        "recovery_marker_sha256": hashlib.sha256(RECOVERY_MARKER).hexdigest(),
        "credential_header_sha256": sha256_bytes(header),
        "ssid_bytes": len(ssid),
        "password_present": bool(password),
    }


def atomic_private_json(path: Path, value: dict[str, object]) -> None:
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    parent_created = not path.parent.exists()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent_created:
        os.chmod(path.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--private-header", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        image = args.image.read_bytes()
        header = args.private_header.read_bytes()
        report = validate(image, header)
        atomic_private_json(args.output.resolve(), report)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        parser.error(str(exc))
    print("RECOVERY SAFEBOOT VALIDATION: PASS")
    print(f"Image size: {report['size']} bytes; headroom: {report['headroom']} bytes")
    print(f"SHA-256: {report['sha256']}")
    print(f"Private validation report: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
