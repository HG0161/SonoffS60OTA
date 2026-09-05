#!/usr/bin/env python3
"""Create the private compile-time Wi-Fi header for S60 recovery Safeboot."""

from __future__ import annotations

import argparse
import getpass
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "captures" / "safeboot-recovery" / "user_config_override.h"


def c_byte_literal(value: str) -> str:
    encoded = value.encode("utf-8")
    return '"' + "".join(f"\\x{byte:02x}" for byte in encoded) + '"'


def validate_credentials(ssid: str, password: str) -> None:
    ssid_size = len(ssid.encode("utf-8"))
    password_size = len(password.encode("utf-8"))
    if not 1 <= ssid_size <= 32:
        raise ValueError("SSID must be 1..32 UTF-8 bytes")
    if "\0" in ssid or "\0" in password or "\n" in ssid or "\n" in password:
        raise ValueError("SSID and password cannot contain NUL or newline characters")
    valid_password = (
        password_size == 0
        or 8 <= password_size <= 63
        or (password_size == 64 and all(char in "0123456789abcdefABCDEF" for char in password))
    )
    if not valid_password:
        raise ValueError("password must be empty, 8..63 UTF-8 bytes, or a 64-digit hexadecimal PSK")


def render_header(ssid: str, password: str) -> bytes:
    validate_credentials(ssid, password)
    return (
        "/* PRIVATE: generated locally; contains encoded Wi-Fi credentials. */\n"
        "#ifndef _USER_CONFIG_OVERRIDE_H_\n"
        "#define _USER_CONFIG_OVERRIDE_H_\n\n"
        "#define S60_RECOVERY_WIFI_DEFAULTS\n"
        "#undef STA_SSID1\n"
        f"#define STA_SSID1 {c_byte_literal(ssid)}\n"
        "#undef STA_PASS1\n"
        f"#define STA_PASS1 {c_byte_literal(password)}\n"
        "#undef WIFI_CONFIG_TOOL\n"
        "#define WIFI_CONFIG_TOOL WIFI_RETRY\n"
        "#undef WIFI_DEFAULT_HOSTNAME\n"
        "#define WIFI_DEFAULT_HOSTNAME \"s60-recovery-%04d\"\n\n"
        "#endif  // _USER_CONFIG_OVERRIDE_H_\n"
    ).encode("ascii")


def atomic_private_write(path: Path, data: bytes) -> None:
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
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    ssid = input("Wi-Fi SSID: ")
    password = getpass.getpass("Wi-Fi password (hidden): ")
    confirmation = getpass.getpass("Repeat Wi-Fi password: ")
    if password != confirmation:
        parser.error("passwords differ")
    try:
        header = render_header(ssid, password)
    except (UnicodeEncodeError, ValueError) as exc:
        parser.error(str(exc))
    output = args.output.resolve()
    atomic_private_write(output, header)
    print(f"Private recovery header written with mode 0600: {output}")
    print("The plaintext credentials were not printed or stored in command-line arguments.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
