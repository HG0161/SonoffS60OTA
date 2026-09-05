#!/usr/bin/env python3
"""Freeze and validate every offline artifact for an exact S60 migration."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import urllib.request
from pathlib import Path
from typing import Any

try:
    from tools.safeboot_migration import (
        APP0_OFFSET,
        APP0_SIZE,
        CANONICAL_NVS_OFFSET,
        CANONICAL_NVS_SIZE,
        FLASH_SIZE,
        OFFICIAL_URLS,
        OFFICIAL_INSTALL_COMMIT,
        OFFICIAL_TASMOTA_COMMIT,
        PUBLISHED_BLUETOOTH_FILE,
        OLD_OTADATA_OFFSET,
        OTADATA_SIZE,
        SAFEBOOT_OFFSET,
        SAFEBOOT_SIZE,
        SOURCE_PARTITIONS,
        SOURCE_TABLE_SHA256,
        TABLE_OFFSET,
        TABLE_SIZE,
        TARGET_PARTITIONS,
        TARGET_TABLE_SHA256,
        analyze_canonical_nvs,
        parse_partition_sector,
        require_exact_partitions,
        require_pinned_artifacts,
        sha256_bytes,
        validate_native_image,
    )
except ModuleNotFoundError:
    from safeboot_migration import (
        APP0_OFFSET,
        APP0_SIZE,
        CANONICAL_NVS_OFFSET,
        CANONICAL_NVS_SIZE,
        FLASH_SIZE,
        OFFICIAL_URLS,
        OFFICIAL_INSTALL_COMMIT,
        OFFICIAL_TASMOTA_COMMIT,
        PUBLISHED_BLUETOOTH_FILE,
        OLD_OTADATA_OFFSET,
        OTADATA_SIZE,
        SAFEBOOT_OFFSET,
        SAFEBOOT_SIZE,
        SOURCE_PARTITIONS,
        SOURCE_TABLE_SHA256,
        TABLE_OFFSET,
        TABLE_SIZE,
        TARGET_PARTITIONS,
        TARGET_TABLE_SHA256,
        analyze_canonical_nvs,
        parse_partition_sector,
        require_exact_partitions,
        require_pinned_artifacts,
        sha256_bytes,
        validate_native_image,
    )


DEFAULT_OUTPUT = Path("captures/safeboot-migration")


def obtain(path: Path | None, url: str, destination: Path) -> bytes:
    if path is not None:
        data = path.read_bytes()
        source = str(path.resolve())
    else:
        request = urllib.request.Request(url, headers={"User-Agent": "S60MigrationPrep/1"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
        source = url
    destination.write_bytes(data)
    print(f"Pinned {destination.name}: {len(data):,} bytes from {source}")
    return data


def artifact_record(filename: str, data: bytes, source: str) -> dict[str, Any]:
    return {
        "file": filename,
        "size": len(data),
        "sha256": sha256_bytes(data),
        "source": source,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dump", type=Path, required=True)
    parser.add_argument(
        "--bluetooth",
        type=Path,
        default=PUBLISHED_BLUETOOTH_FILE,
        help="Berry-capable ESP32-C3 image (default: reviewed published artifact)",
    )
    parser.add_argument("--factory", type=Path, help="use a local factory image")
    parser.add_argument("--safeboot", type=Path, help="use a local Safeboot image")
    parser.add_argument("--app", type=Path, help="use a local native Tasmota image")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    try:
        source_dump = args.source_dump.read_bytes()
        bluetooth = args.bluetooth.read_bytes()
    except OSError as exc:
        parser.error(str(exc))
    if len(source_dump) != FLASH_SIZE:
        parser.error(f"source dump must be exactly {FLASH_SIZE} bytes")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    source_table = source_dump[TABLE_OFFSET : TABLE_OFFSET + TABLE_SIZE]
    source_report = parse_partition_sector(source_table)
    require_exact_partitions(source_report, SOURCE_PARTITIONS)
    if source_report["sha256"] != SOURCE_TABLE_SHA256:
        parser.error("source table does not match the captured S60 allow-list")

    source_nvs = source_dump[
        CANONICAL_NVS_OFFSET : CANONICAL_NVS_OFFSET + CANONICAL_NVS_SIZE
    ]
    nvs_report = analyze_canonical_nvs(source_nvs)
    old_otadata = source_dump[
        OLD_OTADATA_OFFSET : OLD_OTADATA_OFFSET + OTADATA_SIZE
    ]

    source_table_path = output / "source-partition-table.bin"
    source_nvs_path = output / "source-canonical-nvs.bin"
    old_otadata_path = output / "source-old-otadata.bin"
    source_table_path.write_bytes(source_table)
    source_nvs_path.write_bytes(source_nvs)
    old_otadata_path.write_bytes(old_otadata)

    bluetooth_path = output / "tasmota32c3-bluetooth.bin"
    shutil.copyfile(args.bluetooth, bluetooth_path)
    factory_path = output / "tasmota32c3.factory.bin"
    safeboot_path = output / "tasmota32c3-safeboot.bin"
    app_path = output / "tasmota32c3.bin"
    factory = obtain(args.factory, OFFICIAL_URLS["factory"], factory_path)
    safeboot = obtain(args.safeboot, OFFICIAL_URLS["safeboot"], safeboot_path)
    app = obtain(args.app, OFFICIAL_URLS["app"], app_path)

    bluetooth_report = validate_native_image(bluetooth, 0x1F0000, "Bluetooth")
    safeboot_report = validate_native_image(safeboot, SAFEBOOT_SIZE, "Safeboot")
    app_report = validate_native_image(app, APP0_SIZE, "normal Tasmota")

    if factory[SAFEBOOT_OFFSET : SAFEBOOT_OFFSET + len(safeboot)] != safeboot:
        parser.error("standalone Safeboot differs from the factory image payload")
    if factory[APP0_OFFSET : APP0_OFFSET + len(app)] != app:
        parser.error("standalone Tasmota differs from the factory image payload")
    if len(factory) != APP0_OFFSET + len(app):
        parser.error("factory image has unexpected bytes before/after app0")

    target_table = factory[TABLE_OFFSET : TABLE_OFFSET + TABLE_SIZE]
    target_report = parse_partition_sector(target_table)
    require_exact_partitions(target_report, TARGET_PARTITIONS)
    if target_report["sha256"] != TARGET_TABLE_SHA256:
        parser.error("target table differs from the reviewed official table sector")
    target_table_path = output / "target-official-partition-table.bin"
    target_table_path.write_bytes(target_table)

    artifacts = {
        "source_table": artifact_record(source_table_path.name, source_table, str(args.source_dump.resolve())),
        "source_nvs": artifact_record(source_nvs_path.name, source_nvs, str(args.source_dump.resolve())),
        "source_old_otadata": artifact_record(old_otadata_path.name, old_otadata, str(args.source_dump.resolve())),
        "bluetooth": artifact_record(bluetooth_path.name, bluetooth, str(args.bluetooth.resolve())),
        "factory": artifact_record(factory_path.name, factory, str(args.factory.resolve()) if args.factory else OFFICIAL_URLS["factory"]),
        "safeboot": artifact_record(safeboot_path.name, safeboot, str(args.safeboot.resolve()) if args.safeboot else OFFICIAL_URLS["safeboot"]),
        "app": artifact_record(app_path.name, app, str(args.app.resolve()) if args.app else OFFICIAL_URLS["app"]),
        "target_table": artifact_record(target_table_path.name, target_table, str(factory_path)),
    }
    release = {
        "install_commit": OFFICIAL_INSTALL_COMMIT,
        "tasmota_commit": OFFICIAL_TASMOTA_COMMIT,
    }
    manifest = {
        "schema": 1,
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "official_release": release,
        "source_dump": {
            "path": str(args.source_dump.resolve()),
            "size": len(source_dump),
            "sha256": sha256_bytes(source_dump),
        },
        "source_table": source_report,
        "target_table": target_report,
        "canonical_nvs": nvs_report,
        "images": {
            "bluetooth": bluetooth_report,
            "safeboot": safeboot_report,
            "app": app_report,
        },
        "artifacts": artifacts,
    }
    try:
        require_pinned_artifacts(manifest)
    except ValueError as exc:
        parser.error(str(exc))
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"\nManifest: {manifest_path}")
    print(f"Source table SHA-256: {source_report['sha256']}")
    print(f"Target table SHA-256: {target_report['sha256']}")
    print(f"Canonical NVS preflight: {'PASS' if nvs_report['ready'] else 'FAIL'}")
    if not nvs_report["ready"]:
        print(f"Missing NVS keys: {', '.join(nvs_report['missing_keys']) or 'none'}")
        print(f"Incomplete NVS keys: {', '.join(nvs_report['incomplete_keys']) or 'none'}")
        print(
            "The frozen artifacts are valid, but live migration must not advance "
            "until a later live preflight proves a self-contained 20 KiB NVS."
        )
    print("\nOFFLINE ARTIFACT PREPARATION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
