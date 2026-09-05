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
        TARGET_TABLE_SHA256,
        load_manifest,
        is_recovery_manifest,
        require_pinned_artifacts,
        require_recovery_manifest,
        parse_partition_sector,
        require_exact_partitions,
        verify_manifest_files,
    )
    from tools.serve_safeboot_migration import LOCK_PATH
except ModuleNotFoundError:
    from safeboot_migration import (
        TARGET_PARTITIONS,
        TARGET_TABLE_SHA256,
        load_manifest,
        is_recovery_manifest,
        require_pinned_artifacts,
        require_recovery_manifest,
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
        require_pinned_artifacts(manifest)
        require_recovery_manifest(manifest_path, manifest)
        recovery_mode = is_recovery_manifest(manifest)
        target = parse_partition_sector(
            (manifest_path.parent / manifest["artifacts"]["target_table"]["file"]).read_bytes()
        )
        require_exact_partitions(target, TARGET_PARTITIONS)
        if target["sha256"] != TARGET_TABLE_SHA256:
            raise ValueError("target table differs from the reviewed official sector")
        phases = ("preflight", "stage", "commit", "restore") if recovery_mode else (
            "preflight",
            "stage",
            "commit",
        )
        evidence = {
            phase: read_optional_evidence(evidence_dir / f"{phase}-report.json")
            for phase in phases
        }
    except (OSError, KeyError, ValueError) as exc:
        print(f"LOCAL VALIDATION: FAIL: {exc}")
        return 2

    print("LOCAL ARTIFACT VALIDATION: PASS")
    print(f"Manifest: {manifest_path}")
    release = manifest["official_release"]
    print(
        "Official artifact set: "
        f"tasmota/install@{release['install_commit']} "
        f"(Tasmota@{release['tasmota_commit']})"
    )
    print(f"Target table: {target['sha256']} (exact official layout)")
    if recovery_mode:
        recovery = manifest["artifacts"]["recovery_safeboot"]
        print("Safeboot payload: PRIVATE VOLATILE-WIFI RECOVERY IMAGE")
        print(f"  size {recovery['size']} bytes; SHA-256 {recovery['sha256']}")
        print("  The partition table remains byte-exact official; restore official Safeboot later.")
    else:
        print("Safeboot payload: exact pinned official image")
    frozen_nvs = manifest.get("canonical_nvs", {})
    if not frozen_nvs.get("ready", False):
        missing = ", ".join(frozen_nvs.get("missing_keys", [])) or "none"
        incomplete = ", ".join(frozen_nvs.get("incomplete_keys", [])) or "none"
        print("Frozen source NVS: NOT SELF-CONTAINED in the future 20 KiB window")
        print(f"  missing keys: {missing}; incomplete blobs: {incomplete}")
        if recovery_mode:
            print("  Recovery mode removes this NVS state as a Safeboot Wi-Fi dependency.")
            print("  Live preflight still captures and pins its exact hash before any writes.")
        else:
            print("  Back up configuration, put Bluetooth in high ota_1, and try read-only preflight.")
            print("  Use the runbook's Reset 4 recovery only if that live preflight still refuses.")
    else:
        print("Frozen source NVS: structurally self-contained in the future 20 KiB window")
    for phase in phases:
        print(phase_line(phase, evidence[phase]))
    print(f"Commit lock: {'ACTIVE (safe)' if LOCK_PATH.exists() else 'RENAMED (commit can be armed)'}")

    failed = next(
        (
            phase
            for phase in phases
            if evidence[phase] is not None and evidence[phase].get("status") != "PASS"
        ),
        None,
    )
    if failed:
        print(f"\nSTOP: {failed} did not pass. Do not advance automatically.")
        return 3
    if evidence["preflight"] is None:
        next_phase = "preflight"
        if recovery_mode:
            note = (
                "Recovery preflight may accept structurally incomplete NVS, but still captures "
                "and pins its exact live hash for stage and commit."
            )
        else:
            note = (
                "First back up configuration, put the pinned Bluetooth image in old ota_1, "
                "then run preflight; use Reset 4 only if the live NVS still refuses."
            )
    elif evidence["stage"] is None:
        next_phase = "stage"
        note = "This writes only the inactive old ota_0 and independently captures its read-back."
    elif evidence["commit"] is None:
        next_phase = "commit"
        note = "Destructive: reread the runbook, use stable power, then deliberately rename the lock."
    elif recovery_mode and evidence["restore"] is None:
        app = manifest["artifacts"]["app"]
        print("\nNEXT 1: upload the pinned normal image through the recovery Safeboot web page:")
        print(f"  {manifest_path.parent / app['file']}")
        print(f"  SHA-256 {app['sha256']}")
        print("After canonical app0 is stable and Berry is available, run:")
        print(
            "  python3 tools/serve_safeboot_migration.py restore "
            f"--manifest {manifest_path} --listen-ip <WORKSTATION_LAN_IP> "
            f"--device-ip {args.device_ip} --i-confirm-normal-app-is-stable"
        )
        return 0
    elif recovery_mode:
        print("\nCOMPLETE: exact official table, app0 and Safeboot have host-validated evidence.")
        return 0
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
        f"--manifest {manifest_path} --listen-ip <WORKSTATION_LAN_IP> "
        f"--device-ip {args.device_ip}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
