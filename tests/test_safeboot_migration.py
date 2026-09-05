import datetime as dt
import hashlib
import http.client
import json
import struct
import tempfile
import threading
import unittest
import zlib
from pathlib import Path

from tools.safeboot_migration import (
    MD5_MAGIC,
    OFFICIAL_INSTALL_COMMIT,
    OFFICIAL_TASMOTA_COMMIT,
    PARTITION_MAGIC,
    PINNED_ARTIFACTS,
    RECOVERY_MODE,
    SECTOR_SIZE,
    SOURCE_PARTITIONS,
    SOURCE_TABLE_SHA256,
    TARGET_PARTITIONS,
    analyze_canonical_nvs,
    parse_partition_sector,
    require_pinned_artifacts,
    sector_aligned_size,
    sha256_bytes,
)
from tools.configure_recovery_safeboot import render_header
from tools.validate_recovery_safeboot import header_value
from tools.serve_safeboot_migration import (
    COMMIT_FLAG,
    RESTORE_FLAG,
    MigrationServer,
    MigrationState,
    parse_report,
    phase_confirmation_refusal,
)


def partition_sector(entries):
    data = bytearray(b"\xff" * SECTOR_SIZE)
    offset = 0
    for label, ptype, subtype, start, size, flags in entries:
        struct.pack_into(
            "<HBBII16sI",
            data,
            offset,
            PARTITION_MAGIC,
            ptype,
            subtype,
            start,
            size,
            label.encode("ascii").ljust(16, b"\0"),
            flags,
        )
        offset += 32
    digest = hashlib.md5(data[:offset]).digest()
    data[offset : offset + 32] = struct.pack("<H", MD5_MAGIC) + b"\xff" * 14 + digest
    return bytes(data)


def canonical_nvs():
    data = bytearray(b"\xff" * 0x5000)
    page = bytearray(b"\xff" * SECTOR_SIZE)
    struct.pack_into("<II", page, 0, 0xFFFFFFFE, 7)
    struct.pack_into("<I", page, 28, zlib.crc32(page[4:28], 0xFFFFFFFF) & 0xFFFFFFFF)

    def mark_written(index):
        shift = (index % 4) * 2
        page[32 + index // 4] &= ~(0x03 << shift)
        page[32 + index // 4] |= 0x02 << shift

    def entry_crc(entry):
        struct.pack_into(
            "<I", entry, 4, zlib.crc32(entry[:4] + entry[8:32], 0xFFFFFFFF) & 0xFFFFFFFF
        )

    next_entry = 0
    for key, chunk_index in (("Settings", 0), ("sta.apinfo", 128)):
        payload = key.encode("ascii")[:4]
        entry = bytearray(b"\xff" * 32)
        struct.pack_into("<BBBB", entry, 0, 1, 0x42, 2, chunk_index)
        entry[8:24] = key.encode("ascii").ljust(16, b"\0")
        struct.pack_into("<H", entry, 24, len(payload))
        struct.pack_into("<I", entry, 28, zlib.crc32(payload, 0xFFFFFFFF) & 0xFFFFFFFF)
        entry_crc(entry)
        page[64 + next_entry * 32 : 96 + next_entry * 32] = entry
        page[96 + next_entry * 32 : 128 + next_entry * 32] = payload.ljust(32, b"\xff")
        mark_written(next_entry)
        mark_written(next_entry + 1)
        next_entry += 2

        index_entry = bytearray(b"\xff" * 32)
        struct.pack_into("<BBBB", index_entry, 0, 1, 0x48, 1, 0xFF)
        index_entry[8:24] = key.encode("ascii").ljust(16, b"\0")
        struct.pack_into("<IBBH", index_entry, 24, len(payload), 1, chunk_index, 0xFFFF)
        entry_crc(index_entry)
        page[64 + next_entry * 32 : 96 + next_entry * 32] = index_entry
        mark_written(next_entry)
        next_entry += 1
    data[:SECTOR_SIZE] = page
    return bytes(data)


class MigrationFixture:
    def __init__(self, root: Path):
        self.root = root
        # The production source table is deliberately allow-listed by hash.
        # Unit tests need its exact bytes, so construct it and assert the fixture.
        self.source_table = partition_sector(SOURCE_PARTITIONS)
        if sha256_bytes(self.source_table) != SOURCE_TABLE_SHA256:
            raise AssertionError("source table fixture does not match production allow-list")
        self.target_table = partition_sector(TARGET_PARTITIONS)
        self.safeboot = bytes((index * 29 + 11) & 0xFF for index in range(12_345))
        self.app = bytes((index * 17 + 3) & 0xFF for index in range(16_789))
        (root / "target.bin").write_bytes(self.target_table)
        (root / "safeboot.bin").write_bytes(self.safeboot)
        (root / "app.bin").write_bytes(self.app)
        self.manifest = {
            "schema": 1,
            "artifacts": {
                "target_table": {
                    "file": "target.bin",
                    "size": len(self.target_table),
                    "sha256": sha256_bytes(self.target_table),
                },
                "safeboot": {
                    "file": "safeboot.bin",
                    "size": len(self.safeboot),
                    "sha256": sha256_bytes(self.safeboot),
                },
                "app": {
                    "file": "app.bin",
                    "size": len(self.app),
                    "sha256": sha256_bytes(self.app),
                },
            },
        }
        self.manifest_path = root / "manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.evidence_dir = root / "live"

    def state(self, phase: str) -> MigrationState:
        return MigrationState(
            self.manifest_path,
            self.manifest,
            phase,
            "http://192.168.1.10:8089",
            self.evidence_dir,
            7200,
        )

    def write_preflight_evidence(self, nvs_hash: str) -> None:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence = {
            "schema": 1,
            "phase": "preflight",
            "status": "PASS",
            "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "manifest_sha256": sha256_bytes(self.manifest_path.read_bytes()),
            "table_sha256": SOURCE_TABLE_SHA256,
            "nvs_sha256": nvs_hash,
        }
        (self.evidence_dir / "preflight-report.json").write_text(
            json.dumps(evidence), encoding="utf-8"
        )

    def write_stage_evidence(self, nvs_hash: str) -> None:
        evidence = {
            "schema": 1,
            "phase": "stage",
            "status": "PASS",
            "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "manifest_sha256": sha256_bytes(self.manifest_path.read_bytes()),
            "nvs_sha256": nvs_hash,
            "staged_sha256": sha256_bytes(self.safeboot),
        }
        (self.evidence_dir / "stage-report.json").write_text(
            json.dumps(evidence), encoding="utf-8"
        )

    def enable_recovery(self) -> bytes:
        recovery = bytes((index * 31 + 7) & 0xFF for index in range(13_579))
        (self.root / "recovery.bin").write_bytes(recovery)
        self.manifest["migration_mode"] = RECOVERY_MODE
        self.manifest["artifacts"]["recovery_safeboot"] = {
            "file": "recovery.bin",
            "size": len(recovery),
            "sha256": sha256_bytes(recovery),
        }
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        return recovery

    def write_commit_evidence(self, safeboot_hash: str) -> None:
        evidence = {
            "schema": 1,
            "phase": "commit",
            "status": "PASS",
            "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "manifest_sha256": sha256_bytes(self.manifest_path.read_bytes()),
            "safeboot_sha256": safeboot_hash,
        }
        (self.evidence_dir / "commit-report.json").write_text(
            json.dumps(evidence), encoding="utf-8"
        )


def send_chunks(state: MigrationState, kind: str, data: bytes) -> None:
    for index, offset in enumerate(range(0, len(data), SECTOR_SIZE)):
        state.receive_capture(kind, index, data[offset : offset + SECTOR_SIZE].hex().encode())


class MigrationValidationTests(unittest.TestCase):
    def test_commit_and_restore_require_separate_host_confirmations(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "REPARTITION_LOCK"
            lock_path.write_text("locked\n", encoding="utf-8")
            self.assertIn(
                "repartition lock is active",
                phase_confirmation_refusal("commit", False, False, lock_path),
            )
            lock_path.unlink()
            self.assertEqual(
                phase_confirmation_refusal("commit", False, False, lock_path),
                f"commit requires {COMMIT_FLAG}",
            )
            self.assertIsNone(
                phase_confirmation_refusal("commit", True, False, lock_path)
            )
            self.assertEqual(
                phase_confirmation_refusal("restore", False, False, lock_path),
                f"restore requires {RESTORE_FLAG}",
            )
            self.assertIsNone(
                phase_confirmation_refusal("restore", False, True, lock_path)
            )

    def test_private_recovery_header_round_trip_without_plaintext(self):
        ssid = "S60 test café"
        password = 'safe-quote-"-test'
        header = render_header(ssid, password)
        self.assertEqual(header_value(header, "STA_SSID1"), ssid.encode("utf-8"))
        self.assertEqual(header_value(header, "STA_PASS1"), password.encode("utf-8"))
        self.assertNotIn(ssid.encode("utf-8"), header)
        self.assertNotIn(password.encode("utf-8"), header)

    def test_partition_fixtures_and_nvs_gate(self):
        source = parse_partition_sector(partition_sector(SOURCE_PARTITIONS))
        self.assertEqual(source["sha256"], SOURCE_TABLE_SHA256)
        report = analyze_canonical_nvs(canonical_nvs())
        self.assertTrue(report["ready"])
        self.assertEqual(report["missing_keys"], [])
        self.assertEqual(sector_aligned_size(12_345), 16_384)

    def test_nvs_gate_rejects_blob_whose_index_survives_but_chunk_does_not(self):
        nvs = bytearray(canonical_nvs())
        # Erase the Settings BLOB_DATA entry and its payload while leaving its
        # BLOB_IDX record written. Key-name-only validation would miss this.
        nvs[32] |= 0x0F
        report = analyze_canonical_nvs(bytes(nvs))
        self.assertFalse(report["ready"])
        self.assertEqual(report["missing_keys"], [])
        self.assertIn("Settings", report["keys"])
        self.assertEqual(report["incomplete_keys"], ["Settings"])

    def test_pinned_artifact_gate_rejects_manifest_substitution(self):
        manifest = {
            "official_release": {
                "install_commit": OFFICIAL_INSTALL_COMMIT,
                "tasmota_commit": OFFICIAL_TASMOTA_COMMIT,
            },
            "artifacts": {
                name: dict(expected) for name, expected in PINNED_ARTIFACTS.items()
            },
        }
        require_pinned_artifacts(manifest)
        manifest["artifacts"]["safeboot"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "safeboot sha256 differs"):
            require_pinned_artifacts(manifest)

    def test_report_parser_rejects_ambiguous_input(self):
        self.assertEqual(parse_report(b"status=PASS\nvalue=1\n"), {"status": "PASS", "value": "1"})
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_report(b"status=PASS\nstatus=FAIL\n")
        with self.assertRaisesRegex(ValueError, "not ASCII"):
            parse_report(b"status=\xff")

    def test_read_only_preflight_records_only_complete_validated_captures(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = MigrationFixture(Path(directory))
            state = fixture.state("preflight")
            rendered = state.render_berry().decode()
            self.assertNotIn("flash.write", rendered)
            self.assertNotIn("flash.erase", rendered)
            self.assertNotIn("@SOURCE", rendered)

            nvs = canonical_nvs()
            otadata = b"\xa5" * 0x2000
            send_chunks(state, "table", fixture.source_table)
            send_chunks(state, "nvs", nvs)
            send_chunks(state, "otadata", otadata)
            body = (
                f"session={state.session}\nstatus=PASS\nflash_size={0x400000}\ncurrent_ota=1\n"
                f"table_sha256={SOURCE_TABLE_SHA256.upper()}\n"
                f"nvs_sha256={sha256_bytes(nvs).upper()}\n"
                f"otadata_sha256={sha256_bytes(otadata).upper()}\n"
            ).encode()
            state.receive_report("preflight", body)
            self.assertTrue(state.complete)
            evidence = json.loads((fixture.evidence_dir / "preflight-report.json").read_text())
            self.assertEqual(evidence["status"], "PASS")
            self.assertTrue(evidence["canonical_nvs"]["ready"])

    def test_preflight_refuses_incomplete_or_changed_table(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = MigrationFixture(Path(directory))
            state = fixture.state("preflight")
            nvs = canonical_nvs()
            otadata = b"\x00" * 0x2000
            send_chunks(state, "nvs", nvs)
            send_chunks(state, "otadata", otadata)
            with self.assertRaisesRegex(ValueError, "incomplete"):
                state.receive_report(
                    "preflight", f"session={state.session}\nstatus=PASS\n".encode()
                )
            changed = bytearray(fixture.source_table)
            changed[-1] = 0
            with self.assertRaisesRegex(ValueError, "padding"):
                parse_partition_sector(bytes(changed))

    def test_preflight_records_structural_nvs_refusal_and_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = MigrationFixture(Path(directory))
            state = fixture.state("preflight")
            nvs = bytearray(canonical_nvs())
            nvs[32] |= 0x0F
            otadata = b"\x5a" * 0x2000
            send_chunks(state, "table", fixture.source_table)
            send_chunks(state, "nvs", bytes(nvs))
            send_chunks(state, "otadata", otadata)
            with self.assertRaisesRegex(ValueError, "not self-contained"):
                state.receive_report(
                    "preflight",
                    f"session={state.session}\nstatus=PASS\n".encode(),
                )
            self.assertTrue(state.complete)
            self.assertEqual(state.result_status, "FAIL")
            evidence = json.loads(
                (fixture.evidence_dir / "preflight-report.json").read_text()
            )
            self.assertEqual(evidence["status"], "FAIL")
            self.assertEqual(
                evidence["reason"], "canonical_nvs_not_self_contained"
            )
            self.assertEqual(evidence["canonical_nvs"]["incomplete_keys"], ["Settings"])

    def test_recovery_preflight_accepts_but_records_incomplete_nvs(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = MigrationFixture(Path(directory))
            state = fixture.state("preflight")
            state.recovery_mode = True
            state.manifest["migration_mode"] = RECOVERY_MODE
            nvs = bytearray(canonical_nvs())
            nvs[32] |= 0x0F
            nvs = bytes(nvs)
            otadata = b"\x6b" * 0x2000
            send_chunks(state, "table", fixture.source_table)
            send_chunks(state, "nvs", nvs)
            send_chunks(state, "otadata", otadata)
            report = (
                f"session={state.session}\nstatus=PASS\nflash_size={0x400000}\ncurrent_ota=1\n"
                f"table_sha256={SOURCE_TABLE_SHA256}\n"
                f"nvs_sha256={sha256_bytes(nvs)}\n"
                f"otadata_sha256={sha256_bytes(otadata)}\n"
            ).encode()
            state.receive_report("preflight", report)
            evidence = json.loads(
                (fixture.evidence_dir / "preflight-report.json").read_text()
            )
            self.assertEqual(evidence["status"], "PASS")
            self.assertFalse(evidence["canonical_nvs_ready"])
            self.assertEqual(evidence["migration_mode"], RECOVERY_MODE)
            self.assertEqual(evidence["canonical_nvs"]["incomplete_keys"], ["Settings"])

    def test_stage_requires_preflight_and_independent_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = MigrationFixture(Path(directory))
            with self.assertRaisesRegex(ValueError, "preflight evidence"):
                fixture.state("stage")

            nvs_hash = sha256_bytes(canonical_nvs())
            fixture.write_preflight_evidence(nvs_hash)
            state = fixture.state("stage")
            rendered = state.render_berry().decode()
            self.assertIn(
                "if count < 4096 flash.erase(stage_address + offset, 0x1000) end",
                rendered,
            )
            self.assertIn(
                sha256_bytes(
                    b"\xff"
                    * (
                        sector_aligned_size(len(fixture.safeboot))
                        - len(fixture.safeboot)
                    )
                ),
                rendered,
            )
            self.assertIn("staged Safeboot sector padding is not erased", rendered)
            send_chunks(state, "staged", fixture.safeboot)
            report = (
                f"session={state.session}\nstatus=PASS\ncurrent_ota=1\n"
                f"table_sha256={SOURCE_TABLE_SHA256}\nnvs_sha256={nvs_hash}\n"
                f"staged_sha256={sha256_bytes(fixture.safeboot)}\n"
                f"staged_size={len(fixture.safeboot)}\n"
            ).encode()
            state.receive_report("stage", report)
            self.assertEqual(
                (fixture.evidence_dir / "staged-safeboot-readback.bin").read_bytes(),
                fixture.safeboot,
            )

    def test_commit_is_not_self_executing_and_uses_safe_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = MigrationFixture(Path(directory))
            nvs_hash = sha256_bytes(canonical_nvs())
            fixture.write_preflight_evidence(nvs_hash)
            fixture.write_stage_evidence(nvs_hash)
            state = fixture.state("commit")
            rendered = state.render_berry().decode()
            self.assertTrue(rendered.rstrip().endswith("return commit"))
            self.assertIn('tasmota.cmd("Restart 99")', rendered)
            self.assertNotRegex(rendered, r"(?m)^\s*flash\.factory\(")
            self.assertIn(state.arm_token, rendered)
            self.assertIn(sha256_bytes(fixture.target_table), rendered)

    def test_restore_is_armed_and_requires_official_app_before_factory_write(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = MigrationFixture(Path(directory))
            recovery = fixture.enable_recovery()
            fixture.evidence_dir.mkdir(parents=True, exist_ok=True)
            fixture.write_commit_evidence(sha256_bytes(recovery))
            state = fixture.state("restore")
            rendered = state.render_berry().decode()
            self.assertTrue(rendered.rstrip().endswith("return restore"))
            self.assertIn("official app0 must be running", rendered)
            self.assertIn("running app0 differs from pinned official image", rendered)
            self.assertIn(sha256_bytes(fixture.app), rendered)
            self.assertIn(sha256_bytes(recovery), rendered)
            self.assertIn(state.arm_token, rendered)

            send_chunks(state, "restored", fixture.safeboot)
            report = (
                f"session={state.session}\nstatus=PASS\ncurrent_ota=0\n"
                f"target_table_sha256={sha256_bytes(fixture.target_table)}\n"
                f"app_sha256={sha256_bytes(fixture.app)}\n"
                f"official_safeboot_sha256={sha256_bytes(fixture.safeboot)}\n"
                f"official_safeboot_size={len(fixture.safeboot)}\n"
            ).encode()
            state.receive_report("restore", report)
            self.assertEqual(
                (fixture.evidence_dir / "official-safeboot-readback.bin").read_bytes(),
                fixture.safeboot,
            )

    def test_http_server_exposes_only_the_selected_phase_to_the_selected_device(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = MigrationFixture(Path(directory))
            state = fixture.state("preflight")
            server = MigrationServer(("127.0.0.1", 0), "127.0.0.1", state)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(
                    "127.0.0.1", server.server_address[1], timeout=2
                )
                connection.request("GET", "/berry/preflight.be")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), state.render_berry())
                connection.close()

                connection = http.client.HTTPConnection(
                    "127.0.0.1", server.server_address[1], timeout=2
                )
                connection.request("GET", "/target-table")
                response = connection.getresponse()
                self.assertEqual(response.status, 404)
                response.read()
                connection.close()

                server.device_ip = "192.0.2.55"
                connection = http.client.HTTPConnection(
                    "127.0.0.1", server.server_address[1], timeout=2
                )
                connection.request("GET", "/berry/preflight.be")
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
