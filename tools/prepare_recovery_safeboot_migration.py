#!/usr/bin/env python3
"""Prepare a separate guarded S60 migration manifest using private Safeboot."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import shutil
import tempfile
from pathlib import Path

try:
    from tools.safeboot_migration import (
        OFFICIAL_TASMOTA_COMMIT,
        RECOVERY_ARTIFACT,
        RECOVERY_MODE,
        RECOVERY_PATCH,
        load_manifest,
        require_pinned_artifacts,
        require_recovery_manifest,
        sha256_bytes,
        verify_manifest_files,
    )
    from tools.validate_recovery_safeboot import validate
except ModuleNotFoundError:
    from safeboot_migration import (
        OFFICIAL_TASMOTA_COMMIT,
        RECOVERY_ARTIFACT,
        RECOVERY_MODE,
        RECOVERY_PATCH,
        load_manifest,
        require_pinned_artifacts,
        require_recovery_manifest,
        sha256_bytes,
        verify_manifest_files,
    )
    from validate_recovery_safeboot import validate


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = ROOT / "captures" / "safeboot-migration" / "manifest.json"
DEFAULT_PRIVATE = ROOT / "captures" / "safeboot-recovery"
DEFAULT_OUTPUT = ROOT / "captures" / "safeboot-recovery-migration"


def private_write(path: Path, data: bytes) -> None:
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
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE)
    parser.add_argument(
        "--recovery-image",
        type=Path,
        default=DEFAULT_PRIVATE / "tasmota32c3-safeboot-recovery.bin",
    )
    parser.add_argument(
        "--private-header",
        type=Path,
        default=DEFAULT_PRIVATE / "user_config_override.h",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    base_path = args.base_manifest.resolve()
    output = args.output.resolve()
    try:
        base = load_manifest(base_path)
        verify_manifest_files(base_path, base)
        require_pinned_artifacts(base)
        image = args.recovery_image.read_bytes()
        header = args.private_header.read_bytes()
        validation = validate(image, header)
        patch_hash = sha256_bytes(RECOVERY_PATCH.read_bytes())
    except (OSError, KeyError, UnicodeDecodeError, ValueError) as exc:
        parser.error(str(exc))

    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(output, 0o700)
    manifest = copy.deepcopy(base)
    manifest["created_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    manifest["migration_mode"] = RECOVERY_MODE
    manifest["base_manifest_sha256"] = sha256_bytes(base_path.read_bytes())
    manifest["artifacts"] = {}
    for name, record in base["artifacts"].items():
        source = base_path.parent / record["file"]
        destination = output / Path(record["file"]).name
        shutil.copyfile(source, destination)
        os.chmod(destination, 0o600)
        copied = copy.deepcopy(record)
        copied["file"] = destination.name
        manifest["artifacts"][name] = copied

    recovery_name = "tasmota32c3-safeboot-recovery.bin"
    recovery_path = output / recovery_name
    private_write(recovery_path, image)
    manifest["artifacts"][RECOVERY_ARTIFACT] = {
        "file": recovery_name,
        "size": len(image),
        "sha256": sha256_bytes(image),
        "source": str(args.recovery_image.resolve()),
    }

    validation_name = "recovery-safeboot-validation.json"
    validation_bytes = (json.dumps(validation, indent=2, sort_keys=True) + "\n").encode("utf-8")
    private_write(output / validation_name, validation_bytes)
    manifest["recovery_safeboot"] = {
        "artifact": RECOVERY_ARTIFACT,
        "tasmota_commit": OFFICIAL_TASMOTA_COMMIT,
        "patch_sha256": patch_hash,
        "credential_header_sha256": validation["credential_header_sha256"],
        "validation_file": validation_name,
        "validation_sha256": sha256_bytes(validation_bytes),
    }

    manifest_path = output / "manifest.json"
    private_write(
        manifest_path,
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    try:
        loaded = load_manifest(manifest_path)
        verify_manifest_files(manifest_path, loaded)
        require_pinned_artifacts(loaded)
        require_recovery_manifest(manifest_path, loaded)
    except (OSError, KeyError, ValueError) as exc:
        parser.error(f"generated recovery manifest failed validation: {exc}")

    print("RECOVERY MIGRATION PREPARATION: PASS")
    print(f"Manifest: {manifest_path}")
    print(f"Recovery Safeboot: {recovery_path}")
    print(f"SHA-256: {sha256_bytes(image)}")
    print("All files are private and Git-ignored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
