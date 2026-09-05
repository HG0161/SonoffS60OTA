#!/usr/bin/env python3
"""Write the build header for a shippable, credential-free recovery Safeboot.

Built once by the maintainer, this image goes in `artifacts/` and can be
published: it contains placeholders where the Wi-Fi name and password go, not
anybody's network details. Each user's copy of the tool writes their own values
into the reserved space and repairs the image's checksums, so nobody has to
install a firmware toolchain.

The placeholders reserve the longest values 802.11 permits - 32 characters of
network name, 63 of password - so a real credential never needs more room than
the placeholder already occupies.
"""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from tools.autoflash.imprint import (
        FILLER,
        PASSWORD_FIELD_BYTES,
        PASSWORD_MARKER,
        SSID_FIELD_BYTES,
        SSID_MARKER,
    )
    from tools.configure_recovery_safeboot import atomic_private_write
except ModuleNotFoundError:  # running from inside tools/
    from autoflash.imprint import (
        FILLER,
        PASSWORD_FIELD_BYTES,
        PASSWORD_MARKER,
        SSID_FIELD_BYTES,
        SSID_MARKER,
    )
    from configure_recovery_safeboot import atomic_private_write

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "captures" / "safeboot-imprintable" / "user_config_override.h"


def c_literal(marker: bytes, field_bytes: int) -> str:
    """The marker followed by filler, as a C string the compiler will not trim.

    The filler must be non-zero: a NUL would terminate the literal early and the
    compiler would allocate less space than the field needs. It is overwritten
    in full when credentials are written in.
    """
    body = marker + FILLER * field_bytes
    return '"' + "".join(f"\\x{byte:02x}" for byte in body) + '"'


def render_header() -> bytes:
    return (
        "/* Generated. Contains placeholders, not credentials - safe to publish\n"
        " * the image built from it. tools/autoflash/imprint.py writes the real\n"
        " * values into the reserved space and repairs the image afterwards. */\n"
        "#ifndef _USER_CONFIG_OVERRIDE_H_\n"
        "#define _USER_CONFIG_OVERRIDE_H_\n\n"
        "#define S60_RECOVERY_WIFI_DEFAULTS\n"
        "#define S60_IMPRINTABLE_BUILD\n"
        "#undef STA_SSID1\n"
        f"#define STA_SSID1 {c_literal(SSID_MARKER, SSID_FIELD_BYTES)}\n"
        "#undef STA_PASS1\n"
        f"#define STA_PASS1 {c_literal(PASSWORD_MARKER, PASSWORD_FIELD_BYTES)}\n"
        "#undef WIFI_CONFIG_TOOL\n"
        "#define WIFI_CONFIG_TOOL WIFI_RETRY\n"
        "#undef WIFI_DEFAULT_HOSTNAME\n"
        '#define WIFI_DEFAULT_HOSTNAME "s60-recovery-%04d"\n\n'
        "#endif  // _USER_CONFIG_OVERRIDE_H_\n"
    ).encode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    atomic_private_write(args.output, render_header())
    print(f"Wrote {args.output}")
    print()
    print("Build it with the pinned Tasmota source, then publish the result:")
    print("  tools/build_recovery_safeboot.sh /tmp/s60-tasmota-imprintable")
    print()
    print("Check the built image really carries the placeholders:")
    print("  python3 tools/validate_imprintable_safeboot.py <image>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
