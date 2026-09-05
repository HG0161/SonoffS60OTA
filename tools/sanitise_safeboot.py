#!/usr/bin/env python3
"""Strip build-machine paths from an image so it can be published.

Compilers bake __FILE__ into assertion and log strings, which on a personal
machine carry a username and directory layout. This overwrites those paths in
place with a neutral one, NUL-pads the remainder so nothing moves, and repairs
the image's checksum and appended SHA-256 afterwards.

Only log text changes. No code, no data, no addresses.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

try:
    from tools.analyze_vendor_ota import parse_esp_image
    from tools.autoflash.imprint import find_build_paths, redact_build_paths
except ModuleNotFoundError:  # running from inside tools/
    from analyze_vendor_ota import parse_esp_image
    from autoflash.imprint import find_build_paths, redact_build_paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", type=Path, help="default: alongside, named -public")
    args = parser.parse_args()

    try:
        image = args.image.read_bytes()
    except OSError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    found = find_build_paths(image)
    if not found:
        print("No build-machine paths found; nothing to strip.")
        return 0

    print(f"Found {len(found)} build path(s):")
    for path in found:
        print(f"  {path.decode(errors='replace')[:100]}")

    cleaned = redact_build_paths(image)
    remaining = find_build_paths(cleaned)
    report = parse_esp_image(cleaned)

    output = args.output or args.image.with_name(args.image.stem + "-public" + args.image.suffix)
    problems = []
    if remaining:
        problems.append(f"{len(remaining)} path(s) still present")
    if len(cleaned) != len(image):
        problems.append("image length changed")
    if not report["checksum_valid"] or report["sha256_valid"] is False:
        problems.append("image no longer validates")
    if problems:
        print("\nFAIL: " + "; ".join(problems), file=sys.stderr)
        return 1

    output.write_bytes(cleaned)
    print(f"\nWrote {output}")
    print(f"  {len(cleaned):,} bytes, checksum and SHA-256 repaired, no paths remaining")
    print(f"\nConfirm before publishing:")
    print(f"  python3 tools/validate_imprintable_safeboot.py {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
