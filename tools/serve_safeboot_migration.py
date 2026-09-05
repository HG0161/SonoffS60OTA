#!/usr/bin/env python3
"""Serve one guarded S60 Safeboot migration phase to one LAN device.

Each invocation exposes exactly one rendered Berry program.  The plug uploads
read-back flash bytes to this server, which validates them before recording a
phase PASS.  The irreversible commit payload is unavailable unless the
repository lock is deliberately renamed and an explicit risk flag is given.
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.server
import ipaddress
import json
import os
import re
import secrets
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

try:
    from tools.safeboot_migration import (
        CANONICAL_NVS_SIZE,
        OTADATA_SIZE,
        SAFEBOOT_SIZE,
        SECTOR_SIZE,
        SOURCE_PARTITIONS,
        SOURCE_TABLE_SHA256,
        TABLE_SIZE,
        TARGET_PARTITIONS,
        TARGET_TABLE_SHA256,
        analyze_canonical_nvs,
        artifact_bytes,
        load_evidence,
        load_manifest,
        parse_partition_sector,
        require_exact_partitions,
        require_pinned_artifacts,
        require_recovery_manifest,
        is_recovery_manifest,
        safeboot_artifact_name,
        sector_aligned_size,
        sha256_bytes,
        verify_manifest_files,
    )
except ModuleNotFoundError:
    from safeboot_migration import (
        CANONICAL_NVS_SIZE,
        OTADATA_SIZE,
        SAFEBOOT_SIZE,
        SECTOR_SIZE,
        SOURCE_PARTITIONS,
        SOURCE_TABLE_SHA256,
        TABLE_SIZE,
        TARGET_PARTITIONS,
        TARGET_TABLE_SHA256,
        analyze_canonical_nvs,
        artifact_bytes,
        load_evidence,
        load_manifest,
        parse_partition_sector,
        require_exact_partitions,
        require_pinned_artifacts,
        require_recovery_manifest,
        is_recovery_manifest,
        safeboot_artifact_name,
        sector_aligned_size,
        sha256_bytes,
        verify_manifest_files,
    )


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "repartition" / "berry"
LOCK_PATH = ROOT / "REPARTITION_LOCK"
COMMIT_FLAG = "--i-accept-power-loss-may-require-opening-the-plug"
PHASES = ("preflight", "stage", "commit", "restore")
MAX_POST_SIZE = 9000
RESTORE_FLAG = "--i-confirm-normal-app-is-stable"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def parse_report(body: bytes) -> dict[str, str]:
    try:
        text = body.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("report is not ASCII") from exc
    report: dict[str, str] = {}
    for line in text.splitlines():
        if not line:
            continue
        if "=" not in line:
            raise ValueError("report line has no equals sign")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise ValueError(f"invalid report key {key!r}")
        if key in report:
            raise ValueError(f"duplicate report key {key!r}")
        if len(value) > 256 or any(ord(char) < 0x20 or ord(char) > 0x7E for char in value):
            raise ValueError(f"invalid report value for {key}")
        report[key] = value
    return report


def require_report_fields(report: dict[str, str], expected: dict[str, str]) -> None:
    for key, value in expected.items():
        actual = report.get(key)
        if key.endswith("sha256") and actual is not None:
            actual = actual.lower()
        wanted = value.lower() if key.endswith("sha256") else value
        if actual != wanted:
            raise ValueError(f"report {key} differs: {actual!r} != {value!r}")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write(
        path,
        (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def phase_confirmation_refusal(
    phase: str,
    commit_confirmed: bool,
    restore_confirmed: bool,
    lock_path: Path = LOCK_PATH,
) -> str | None:
    if phase == "commit":
        if lock_path.exists():
            return f"repartition lock is active at {lock_path}"
        if not commit_confirmed:
            return f"commit requires {COMMIT_FLAG}"
    if phase == "restore" and not restore_confirmed:
        return f"restore requires {RESTORE_FLAG}"
    return None


class MigrationState:
    def __init__(
        self,
        manifest_path: Path,
        manifest: dict[str, Any],
        phase: str,
        base_url: str,
        evidence_dir: Path,
        max_evidence_age: int,
    ) -> None:
        self.manifest_path = manifest_path
        self.manifest = manifest
        self.phase = phase
        self.base_url = base_url
        self.evidence_dir = evidence_dir
        self.session = secrets.token_hex(12)
        self.arm_token = secrets.token_urlsafe(15)
        self.complete = False
        self.result_status: str | None = None
        self.captures: dict[str, dict[int, bytes]] = {}
        self.manifest_sha256 = sha256_bytes(manifest_path.read_bytes())

        self.recovery_mode = is_recovery_manifest(manifest)
        if phase == "restore" and not self.recovery_mode:
            raise ValueError("official migration manifests have no recovery image to restore")
        self.safeboot_name = safeboot_artifact_name(manifest)
        self.safeboot = artifact_bytes(manifest_path, manifest, self.safeboot_name)
        self.official_safeboot = artifact_bytes(manifest_path, manifest, "safeboot")
        self.app = artifact_bytes(manifest_path, manifest, "app")
        self.target_table = artifact_bytes(manifest_path, manifest, "target_table")
        target_report = parse_partition_sector(self.target_table)
        require_exact_partitions(target_report, TARGET_PARTITIONS)
        if target_report["sha256"] != TARGET_TABLE_SHA256:
            raise ValueError("target table differs from the reviewed official sector")
        self.target_table_sha256 = target_report["sha256"]

        self.live_preflight: dict[str, Any] | None = None
        self.stage_evidence: dict[str, Any] | None = None
        if phase in {"stage", "commit"}:
            self.live_preflight = load_evidence(
                evidence_dir / "preflight-report.json", "preflight", max_evidence_age
            )
            if self.live_preflight.get("manifest_sha256") != self.manifest_sha256:
                raise ValueError("preflight evidence belongs to a different manifest")
            if self.live_preflight.get("table_sha256") != SOURCE_TABLE_SHA256:
                raise ValueError("preflight evidence has the wrong source table")
        if phase == "commit":
            self.stage_evidence = load_evidence(
                evidence_dir / "stage-report.json", "stage", max_evidence_age
            )
            if self.stage_evidence.get("manifest_sha256") != self.manifest_sha256:
                raise ValueError("stage evidence belongs to a different manifest")
            if self.stage_evidence.get("nvs_sha256") != self.live_preflight.get("nvs_sha256"):
                raise ValueError("stage and preflight evidence disagree about NVS")
            if self.stage_evidence.get("staged_sha256") != sha256_bytes(self.safeboot):
                raise ValueError("stage evidence does not match pinned Safeboot")
        self.commit_evidence: dict[str, Any] | None = None
        if phase == "restore":
            self.commit_evidence = load_evidence(
                evidence_dir / "commit-report.json", "commit", max_evidence_age
            )
            if self.commit_evidence.get("manifest_sha256") != self.manifest_sha256:
                raise ValueError("commit evidence belongs to a different manifest")
            if self.commit_evidence.get("safeboot_sha256") != sha256_bytes(self.safeboot):
                raise ValueError("commit evidence does not match recovery Safeboot")

    def replacements(self) -> dict[str, str]:
        safeboot_length = len(self.safeboot)
        copy_length = sector_aligned_size(safeboot_length)
        staging_pad_length = copy_length - safeboot_length
        erased_tail_length = SAFEBOOT_SIZE - copy_length
        if erased_tail_length < 0:
            raise ValueError("Safeboot does not fit the canonical partition")
        live_nvs = (
            self.live_preflight["nvs_sha256"] if self.live_preflight is not None else ""
        )
        return {
            "BASE_URL": self.base_url,
            "SESSION": self.session,
            "ARM_TOKEN": self.arm_token,
            "SOURCE_TABLE_SHA256": SOURCE_TABLE_SHA256,
            "TARGET_TABLE_SHA256": self.target_table_sha256,
            "LIVE_NVS_SHA256": live_nvs,
            "SAFEBOOT_SHA256": sha256_bytes(self.safeboot),
            "SAFEBOOT_LENGTH": str(safeboot_length),
            "SAFEBOOT_COPY_LENGTH": str(copy_length),
            "STAGING_PAD_LENGTH": str(staging_pad_length),
            "STAGING_PAD_SHA256": sha256_bytes(b"\xff" * staging_pad_length),
            "ERASED_TAIL_LENGTH": str(erased_tail_length),
            "ERASED_TAIL_SHA256": sha256_bytes(b"\xff" * erased_tail_length),
            "ERASED_OTADATA_SHA256": sha256_bytes(b"\xff" * OTADATA_SIZE),
            "OFFICIAL_SAFEBOOT_SHA256": sha256_bytes(self.official_safeboot),
            "OFFICIAL_SAFEBOOT_LENGTH": str(len(self.official_safeboot)),
            "OFFICIAL_SAFEBOOT_COPY_LENGTH": str(sector_aligned_size(len(self.official_safeboot))),
            "OFFICIAL_STAGING_PAD_LENGTH": str(
                sector_aligned_size(len(self.official_safeboot)) - len(self.official_safeboot)
            ),
            "OFFICIAL_STAGING_PAD_SHA256": sha256_bytes(
                b"\xff"
                * (sector_aligned_size(len(self.official_safeboot)) - len(self.official_safeboot))
            ),
            "OFFICIAL_ERASED_TAIL_LENGTH": str(
                SAFEBOOT_SIZE - sector_aligned_size(len(self.official_safeboot))
            ),
            "OFFICIAL_ERASED_TAIL_SHA256": sha256_bytes(
                b"\xff" * (SAFEBOOT_SIZE - sector_aligned_size(len(self.official_safeboot)))
            ),
            "APP_SHA256": sha256_bytes(self.app),
            "APP_LENGTH": str(len(self.app)),
        }

    def render_berry(self) -> bytes:
        template_path = TEMPLATE_DIR / f"{self.phase}.be.tmpl"
        try:
            source = template_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"cannot read Berry template: {exc}") from exc
        for name, value in self.replacements().items():
            source = source.replace(f"@{name}@", value)
        leftovers = sorted(set(re.findall(r"@[A-Z][A-Z0-9_]+@", source)))
        if leftovers:
            raise ValueError(f"unrendered Berry placeholders: {', '.join(leftovers)}")
        encoded = source.encode("utf-8")
        if len(encoded) > 32 * 1024:
            raise ValueError("rendered Berry source exceeds webclient.get_string limit")
        return encoded

    def safeboot_chunk(self, index: int) -> bytes:
        if self.phase not in {"stage", "restore"}:
            raise ValueError("Safeboot chunks are unavailable in this phase")
        payload = self.official_safeboot if self.phase == "restore" else self.safeboot
        count = (len(payload) + SECTOR_SIZE - 1) // SECTOR_SIZE
        if index < 0 or index >= count:
            raise ValueError("Safeboot chunk index is outside the artifact")
        return payload[index * SECTOR_SIZE : (index + 1) * SECTOR_SIZE]

    def receive_capture(self, kind: str, index: int, body: bytes) -> None:
        allowed = {
            "preflight": {"table": TABLE_SIZE, "nvs": CANONICAL_NVS_SIZE, "otadata": OTADATA_SIZE},
            "stage": {"staged": len(self.safeboot)},
            "commit": {},
            "restore": {"restored": len(self.official_safeboot)},
        }[self.phase]
        if kind not in allowed:
            raise ValueError(f"capture {kind!r} is unavailable in {self.phase}")
        total = allowed[kind]
        chunk_count = (total + SECTOR_SIZE - 1) // SECTOR_SIZE
        if index < 0 or index >= chunk_count:
            raise ValueError("capture chunk index is outside the expected region")
        expected_size = min(SECTOR_SIZE, total - index * SECTOR_SIZE)
        if len(body) != expected_size * 2:
            raise ValueError("capture hex length differs")
        try:
            data = bytes.fromhex(body.decode("ascii"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("capture is not valid hexadecimal") from exc
        if len(data) != expected_size:
            raise ValueError("decoded capture length differs")
        chunks = self.captures.setdefault(kind, {})
        if index in chunks and chunks[index] != data:
            raise ValueError("duplicate capture chunk has different bytes")
        chunks[index] = data

    def assembled_capture(self, kind: str, total: int) -> bytes:
        chunks = self.captures.get(kind, {})
        count = (total + SECTOR_SIZE - 1) // SECTOR_SIZE
        missing = [index for index in range(count) if index not in chunks]
        if missing:
            raise ValueError(f"capture {kind} is incomplete; missing chunks {missing[:8]}")
        data = b"".join(chunks[index] for index in range(count))
        if len(data) != total:
            raise ValueError(f"assembled {kind} capture length differs")
        return data

    def receive_report(self, requested_phase: str, body: bytes) -> None:
        if requested_phase != self.phase:
            raise ValueError("report phase differs from served phase")
        report = parse_report(body)
        if report.get("session") != self.session:
            raise ValueError("report session differs")
        if self.phase == "preflight":
            self._finish_preflight(report)
        elif self.phase == "stage":
            self._finish_stage(report)
        elif self.phase == "commit":
            self._finish_commit(report)
        else:
            self._finish_restore(report)

    def _base_evidence(self, phase: str, status: str) -> dict[str, Any]:
        return {
            "schema": 1,
            "phase": phase,
            "status": status,
            "created_utc": utc_now(),
            "session": self.session,
            "manifest": str(self.manifest_path),
            "manifest_sha256": self.manifest_sha256,
        }

    def _finish_preflight(self, report: dict[str, str]) -> None:
        table = self.assembled_capture("table", TABLE_SIZE)
        nvs = self.assembled_capture("nvs", CANONICAL_NVS_SIZE)
        otadata = self.assembled_capture("otadata", OTADATA_SIZE)
        table_report = parse_partition_sector(table)
        require_exact_partitions(table_report, SOURCE_PARTITIONS)
        if table_report["sha256"] != SOURCE_TABLE_SHA256:
            raise ValueError("uploaded table is not the allow-listed source table")
        nvs_report = analyze_canonical_nvs(nvs)
        if not nvs_report["ready"] and not self.recovery_mode:
            atomic_write(self.evidence_dir / "live-partition-table.bin", table)
            atomic_write(self.evidence_dir / "live-canonical-nvs.bin", nvs)
            atomic_write(self.evidence_dir / "live-old-otadata.bin", otadata)
            evidence = self._base_evidence("preflight", "FAIL")
            evidence.update(
                {
                    "reason": "canonical_nvs_not_self_contained",
                    "table_sha256": sha256_bytes(table),
                    "nvs_sha256": sha256_bytes(nvs),
                    "otadata_sha256": sha256_bytes(otadata),
                    "canonical_nvs": nvs_report,
                }
            )
            atomic_json(self.evidence_dir / "preflight-report.json", evidence)
            self.complete = True
            self.result_status = "FAIL"
            raise ValueError(
                "live canonical NVS is not self-contained: "
                f"missing={nvs_report['missing_keys']}, "
                f"incomplete={nvs_report['incomplete_keys']}, "
                f"invalid_pages={nvs_report['invalid_entry_pages']}"
            )
        expected = {
            "status": "PASS",
            "flash_size": str(0x400000),
            "current_ota": "1",
            "table_sha256": sha256_bytes(table),
            "nvs_sha256": sha256_bytes(nvs),
            "otadata_sha256": sha256_bytes(otadata),
        }
        require_report_fields(report, expected)
        atomic_write(self.evidence_dir / "live-partition-table.bin", table)
        atomic_write(self.evidence_dir / "live-canonical-nvs.bin", nvs)
        atomic_write(self.evidence_dir / "live-old-otadata.bin", otadata)
        evidence = self._base_evidence("preflight", "PASS")
        evidence.update(expected)
        evidence["canonical_nvs"] = nvs_report
        evidence["canonical_nvs_ready"] = nvs_report["ready"]
        evidence["migration_mode"] = self.manifest.get("migration_mode", "official")
        atomic_json(self.evidence_dir / "preflight-report.json", evidence)
        self.complete = True
        self.result_status = "PASS"

    def _finish_stage(self, report: dict[str, str]) -> None:
        staged = self.assembled_capture("staged", len(self.safeboot))
        if staged != self.safeboot:
            raise ValueError("host read-back differs byte-for-byte from pinned Safeboot")
        expected = {
            "status": "PASS",
            "current_ota": "1",
            "table_sha256": SOURCE_TABLE_SHA256,
            "nvs_sha256": self.live_preflight["nvs_sha256"],
            "staged_sha256": sha256_bytes(self.safeboot),
            "staged_size": str(len(self.safeboot)),
        }
        require_report_fields(report, expected)
        atomic_write(self.evidence_dir / "staged-safeboot-readback.bin", staged)
        evidence = self._base_evidence("stage", "PASS")
        evidence.update(expected)
        atomic_json(self.evidence_dir / "stage-report.json", evidence)
        self.complete = True
        self.result_status = "PASS"

    def _finish_commit(self, report: dict[str, str]) -> None:
        status = report.get("status")
        if status not in {"PASS", "FAIL"}:
            raise ValueError("commit status is neither PASS nor FAIL")
        expected = {
            "current_ota": "1",
            "target_table_sha256": self.target_table_sha256,
            "safeboot_sha256": sha256_bytes(self.safeboot),
        }
        require_report_fields(report, expected)
        evidence = self._base_evidence("commit", status)
        evidence.update(expected)
        evidence["rollback"] = report.get("rollback")
        atomic_json(self.evidence_dir / "commit-report.json", evidence)
        self.complete = True
        self.result_status = status

    def _finish_restore(self, report: dict[str, str]) -> None:
        status = report.get("status")
        if status not in {"PASS", "FAIL"}:
            raise ValueError("restore status is neither PASS nor FAIL")
        expected = {
            "current_ota": "0",
            "target_table_sha256": self.target_table_sha256,
            "app_sha256": sha256_bytes(self.app),
            "official_safeboot_sha256": sha256_bytes(self.official_safeboot),
            "official_safeboot_size": str(len(self.official_safeboot)),
        }
        require_report_fields(report, expected)
        if status == "PASS":
            restored = self.assembled_capture("restored", len(self.official_safeboot))
            if restored != self.official_safeboot:
                raise ValueError("host read-back differs byte-for-byte from official Safeboot")
            atomic_write(self.evidence_dir / "official-safeboot-readback.bin", restored)
        evidence = self._base_evidence("restore", status)
        evidence.update(expected)
        atomic_json(self.evidence_dir / "restore-report.json", evidence)
        self.complete = True
        self.result_status = status


class MigrationHandler(http.server.BaseHTTPRequestHandler):
    server_version = "S60SafebootMigration/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"  [{self.client_address[0]}] {fmt % args}")

    def _response(self, status: int, body: bytes = b"", content_type: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _allowed(self) -> bool:
        if self.client_address[0] != self.server.device_ip:
            self._response(403, b"wrong source device\n")
            return False
        return True

    def do_GET(self) -> None:
        if not self._allowed():
            return
        path = urllib.parse.urlsplit(self.path).path
        state: MigrationState = self.server.state
        try:
            if path == f"/berry/{state.phase}.be":
                self._response(200, state.render_berry(), "text/plain; charset=utf-8")
                return
            match = re.fullmatch(r"/chunk/safeboot/([0-9]+)", path)
            if match:
                chunk = state.safeboot_chunk(int(match.group(1)))
                self._response(200, chunk.hex().encode("ascii"))
                return
            if path == "/target-table" and state.phase == "commit":
                self._response(200, state.target_table.hex().encode("ascii"))
                return
            self._response(404, b"not found\n")
        except ValueError as exc:
            self._response(409, f"refused: {exc}\n".encode("utf-8"))

    def do_POST(self) -> None:
        if not self._allowed():
            return
        try:
            content_length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            self._response(400, b"bad content length\n")
            return
        if content_length < 0 or content_length > MAX_POST_SIZE:
            self._response(413, b"body too large\n")
            return
        body = self.rfile.read(content_length)
        path = urllib.parse.urlsplit(self.path).path
        state: MigrationState = self.server.state
        try:
            capture = re.fullmatch(r"/capture/([a-z-]+)/([0-9]+)", path)
            if capture:
                state.receive_capture(capture.group(1), int(capture.group(2)), body)
                self._response(200)
                return
            report = re.fullmatch(r"/report/([a-z]+)", path)
            if report:
                state.receive_report(report.group(1), body)
                self._response(200)
                return
            self._response(404, b"not found\n")
        except ValueError as exc:
            print(f"  REFUSED {path}: {exc}")
            self._response(409, f"refused: {exc}\n".encode("utf-8"))


class MigrationServer(http.server.HTTPServer):
    def __init__(self, address: tuple[str, int], device_ip: str, state: MigrationState):
        super().__init__(address, MigrationHandler)
        self.device_ip = device_ip
        self.state = state


def validate_lan_ipv4(value: str, option: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"{option} is not an IP address") from exc
    if address.version != 4 or not (address.is_private or address.is_loopback):
        raise ValueError(f"{option} must be a private IPv4 LAN address")
    return str(address)


def berry_loader(base_url: str, phase: str) -> str:
    prefix = (
        "def s60_urlbeload(url) var wc=webclient() wc.begin(url) var st=wc.GET() "
        "if st!=200 wc.close() raise 'connection_error',format('status:%i',st) end "
        "var code=wc.get_string() return compile(code)() end "
    )
    url = f"{base_url}/berry/{phase}.be"
    if phase in {"commit", "restore"}:
        name = "s60_commit" if phase == "commit" else "s60_restore"
        return prefix + f"{name}=s60_urlbeload('{url}')"
    return prefix + f"return s60_urlbeload('{url}')"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=PHASES)
    parser.add_argument("--manifest", type=Path, default=Path("captures/safeboot-migration/manifest.json"))
    parser.add_argument("--listen-ip", required=True)
    parser.add_argument("--listen-port", type=int, default=8089)
    parser.add_argument("--device-ip", required=True)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--max-evidence-age", type=int, default=7200)
    parser.add_argument("--wait", type=int, default=1800, help="seconds to wait for a phase report")
    parser.add_argument(COMMIT_FLAG, action="store_true")
    parser.add_argument(RESTORE_FLAG, action="store_true")
    args = parser.parse_args()

    try:
        listen_ip = validate_lan_ipv4(args.listen_ip, "--listen-ip")
        device_ip = validate_lan_ipv4(args.device_ip, "--device-ip")
    except ValueError as exc:
        parser.error(str(exc))
    if not 1 <= args.listen_port <= 65535:
        parser.error("--listen-port must be 1..65535")
    if args.max_evidence_age <= 0 or args.wait <= 0:
        parser.error("evidence age and wait must be positive")

    manifest_path = args.manifest.resolve()
    evidence_dir = (args.evidence_dir or manifest_path.parent / "live").resolve()
    try:
        manifest = load_manifest(manifest_path)
        verify_manifest_files(manifest_path, manifest)
        require_pinned_artifacts(manifest)
        require_recovery_manifest(manifest_path, manifest)
    except (KeyError, ValueError) as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2

    refusal = phase_confirmation_refusal(
        args.phase,
        args.i_accept_power_loss_may_require_opening_the_plug,
        args.i_confirm_normal_app_is_stable,
    )
    if refusal is not None:
        print(f"REFUSING: {refusal}", file=sys.stderr)
        return 2

    base_url = f"http://{listen_ip}:{args.listen_port}"
    try:
        state = MigrationState(
            manifest_path, manifest, args.phase, base_url, evidence_dir, args.max_evidence_age
        )
        rendered = state.render_berry()
    except (OSError, ValueError, KeyError) as exc:
        print(f"REFUSING: {exc}", file=sys.stderr)
        return 2

    print(f"Phase: {args.phase}")
    print(f"Manifest: {manifest_path}")
    print(f"Berry source: {len(rendered):,} bytes")
    print(f"Serving only device {device_ip} at {base_url}")
    print("\nPaste this as ONE line in the Berry console (do not prefix it with `br`):")
    print(berry_loader(base_url, args.phase))
    if args.phase in {"commit", "restore"}:
        function_name = "s60_commit" if args.phase == "commit" else "s60_restore"
        print("\nLoading performs no writes. After it prints a loaded closure, arm separately with:")
        print(f'{function_name}("{state.arm_token}")')

    try:
        server = MigrationServer((listen_ip, args.listen_port), device_ip, state)
    except OSError as exc:
        print(f"REFUSING: could not bind server: {exc}", file=sys.stderr)
        return 2
    server.timeout = 0.5
    deadline = time.monotonic() + args.wait
    try:
        while not state.complete and time.monotonic() < deadline:
            server.handle_request()
    except KeyboardInterrupt:
        print("\nStopped; no phase result was recorded.")
        return 130
    finally:
        server.server_close()

    if not state.complete:
        print("TIMEOUT: no complete, validated phase report was received.", file=sys.stderr)
        return 3
    print(f"\n{args.phase.upper()} RESULT: {state.result_status}")
    print(f"Evidence: {evidence_dir / (args.phase + '-report.json')}")
    return 0 if state.result_status == "PASS" else 4


if __name__ == "__main__":
    raise SystemExit(main())
