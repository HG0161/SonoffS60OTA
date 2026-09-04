#!/usr/bin/env python3
"""Validate local S60 migration state and print the next resumable stage."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

try:
    from tools.safeboot_migration import (
        TARGET_PARTITIONS,
        load_manifest,
        parse_partition_sector,
        require_exact_partitions,
        verify_manifest_files,
    )
    from tools.serve_safeboot_migration import LOCK_PATH
except ModuleNotFoundError:
    from safeboot_migration import (
        TARGET_PARTITIONS,
        load_manifest,
        parse_partition_sector,
        require_exact_partitions,
        verify_manifest_files,
    )
    from serve_safeboot_migration import LOCK_PATH


def read_optional_evidence(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid evidence {path}: {exc}") from exc
    if value.get("schema") != 1:
        raise ValueError(f"unsupported evidence schema in {path}")
    return value


def age_text(value: dict[str, Any]) -> str:
    try:
        created = dt.datetime.fromisoformat(value["created_utc"])
        age = dt.datetime.now(dt.timezone.utc) - created.astimezone(dt.timezone.utc)
    except (KeyError, TypeError, ValueError):
        return "unknown age"
    seconds = max(0, int(age.total_seconds()))
    if seconds < 120:
        return f"{seconds}s old"
    if seconds < 7200:
        return f"{seconds // 60}m old"
    return f"{seconds // 3600}h old"


def phase_line(name: str, evidence: dict[str, Any] | None) -> str:
    if evidence is None:
        return f"{name:10} NOT RUN"
    return f"{name:10} {evidence.get('status', 'UNKNOWN'):7} ({age_text(evidence)})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=Path("captures/safeboot-migration/manifest.json")
    )
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--device-ip", default="192.168.1.96")
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    evidence_dir = (args.evidence_dir or manifest_path.parent / "live").resolve()
    try:
        manifest = load_manifest(manifest_path)
        verify_manifest_files(manifest_path, manifest)
        target = parse_partition_sector(
            (manifest_path.parent / manifest["artifacts"]["target_table"]["file"]).read_bytes()
        )
        require_exact_partitions(target, TARGET_PARTITIONS)
        evidence = {
            phase: read_optional_evidence(evidence_dir / f"{phase}-report.json")
            for phase in ("preflight", "stage", "commit")
        }
    except (OSError, KeyError, ValueError) as exc:
        print(f"LOCAL VALIDATION: FAIL: {exc}")
        return 2

    print("LOCAL ARTIFACT VALIDATION: PASS")
    print(f"Manifest: {manifest_path}")
    print(f"Target table: {target['sha256']} (exact official layout)")
    frozen_nvs = manifest.get("canonical_nvs", {})
    if not frozen_nvs.get("ready", False):
        missing = ", ".join(frozen_nvs.get("missing_keys", [])) or "none"
        incomplete = ", ".join(frozen_nvs.get("incomplete_keys", [])) or "none"
        print("Frozen source NVS: NOT SELF-CONTAINED in the future 20 KiB window")
        print(f"  missing keys: {missing}; incomplete blobs: {incomplete}")
        print("  Back up configuration, use the runbook's Reset 4 step only from high ota_1,")
        print("  and require a new live preflight PASS before any repartition write.")
    else:
        print("Frozen source NVS: structurally self-contained in the future 20 KiB window")
    for phase in ("preflight", "stage", "commit"):
        print(phase_line(phase, evidence[phase]))
    print(f"Commit lock: {'ACTIVE (safe)' if LOCK_PATH.exists() else 'RENAMED (commit can be armed)'}")

    failed = next(
        (
            phase
            for phase in ("preflight", "stage", "commit")
            if evidence[phase] is not None and evidence[phase].get("status") != "PASS"
        ),
        None,
    )
    if failed:
        print(f"\nSTOP: {failed} did not pass. Do not advance automatically.")
        return 3
    if evidence["preflight"] is None:
        next_phase = "preflight"
        note = (
            "First back up configuration, put the pinned Bluetooth image in old ota_1, "
            "then follow the runbook's controlled NVS recreation step."
        )
    elif evidence["stage"] is None:
        next_phase = "stage"
        note = "This writes only the inactive old ota_0 and independently captures its read-back."
    elif evidence["commit"] is None:
        next_phase = "commit"
        note = "Destructive: reread the runbook, use stable power, then deliberately rename the lock."
    else:
        app = manifest["artifacts"]["app"]
        print("\nNEXT: upload the pinned normal image through the Safeboot web page:")
        print(f"  {manifest_path.parent / app['file']}")
        print(f"  SHA-256 {app['sha256']}")
        return 0

    print(f"\nNEXT PHASE: {next_phase}")
    print(note)
    print("Run (replace the workstation address):")
    print(
        f"  python3 tools/serve_safeboot_migration.py {next_phase} "
        f"--listen-ip <WORKSTATION_LAN_IP> --device-ip {args.device_ip}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
