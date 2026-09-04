"""Shared validation helpers for the guarded S60 Safeboot migration.

This module is intentionally side-effect free.  Device-writing tools import
the constants and validators here so that every phase agrees on exact byte
ranges and partition entries.
"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from tools.analyze_vendor_ota import parse_esp_image
except ModuleNotFoundError:
    from analyze_vendor_ota import parse_esp_image


FLASH_SIZE = 0x400000
SECTOR_SIZE = 0x1000
TABLE_OFFSET = 0x8000
TABLE_SIZE = 0x1000
TABLE_DATA_SIZE = 0xC00
PARTITION_ENTRY_SIZE = 32
PARTITION_MAGIC = 0x50AA
MD5_MAGIC = 0xEBEB

SOURCE_TABLE_SHA256 = "f63f66bbf23b9e291c7eb5dcf24be820190dacf4bf52af515d9664526a4f4daf"

SOURCE_PARTITIONS = (
    ("nvs", 0x01, 0x02, 0x009000, 0x010000, 0x0),
    ("reserve", 0x01, 0x02, 0x019000, 0x004000, 0x0),
    ("otadata", 0x01, 0x00, 0x01D000, 0x002000, 0x0),
    ("phy_init", 0x01, 0x01, 0x01F000, 0x001000, 0x0),
    ("ota_0", 0x00, 0x10, 0x020000, 0x1F0000, 0x0),
    ("ota_1", 0x00, 0x11, 0x210000, 0x1F0000, 0x0),
)

TARGET_PARTITIONS = (
    ("nvs", 0x01, 0x02, 0x009000, 0x005000, 0x0),
    ("otadata", 0x01, 0x00, 0x00E000, 0x002000, 0x0),
    ("safeboot", 0x00, 0x00, 0x010000, 0x0D0000, 0x0),
    ("app0", 0x00, 0x10, 0x0E0000, 0x2D0000, 0x0),
    ("spiffs", 0x01, 0x82, 0x3B0000, 0x050000, 0x0),
)

CANONICAL_NVS_OFFSET = 0x9000
CANONICAL_NVS_SIZE = 0x5000
OLD_OTADATA_OFFSET = 0x1D000
NEW_OTADATA_OFFSET = 0xE000
OTADATA_SIZE = 0x2000
OLD_OTA0_OFFSET = 0x20000
OLD_OTA1_OFFSET = 0x210000
OLD_OTA_SLOT_SIZE = 0x1F0000
SAFEBOOT_OFFSET = 0x10000
SAFEBOOT_SIZE = 0xD0000
APP0_OFFSET = 0xE0000
APP0_SIZE = 0x2D0000
SPIFFS_OFFSET = 0x3B0000
SPIFFS_SIZE = 0x50000
SAFEBOOT_STAGE_OFFSET = OLD_OTA0_OFFSET

OFFICIAL_RELEASE_BASE = (
    "https://raw.githubusercontent.com/tasmota/install/firmware/firmware/release"
)
OFFICIAL_URLS = {
    "factory": f"{OFFICIAL_RELEASE_BASE}/tasmota32c3.factory.bin",
    "safeboot": f"{OFFICIAL_RELEASE_BASE}/tasmota32c3-safeboot.bin",
    "app": f"{OFFICIAL_RELEASE_BASE}/tasmota32c3.bin",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sector_aligned_size(size: int) -> int:
    if size < 0:
        raise ValueError("size cannot be negative")
    return (size + SECTOR_SIZE - 1) // SECTOR_SIZE * SECTOR_SIZE


def partition_tuple(entry: dict[str, Any]) -> tuple[str, int, int, int, int, int]:
    return (
        entry["label"],
        entry["type"],
        entry["subtype"],
        entry["offset"],
        entry["size"],
        entry["flags"],
    )


def parse_partition_sector(sector: bytes) -> dict[str, Any]:
    """Decode and validate a complete 4 KiB ESP-IDF partition-table sector."""
    if len(sector) != TABLE_SIZE:
        raise ValueError(f"partition sector must be exactly {TABLE_SIZE} bytes")

    entries: list[dict[str, Any]] = []
    md5_offset: int | None = None
    stored_md5: bytes | None = None
    for offset in range(0, TABLE_DATA_SIZE, PARTITION_ENTRY_SIZE):
        entry = sector[offset : offset + PARTITION_ENTRY_SIZE]
        magic = struct.unpack_from("<H", entry)[0]
        if magic == PARTITION_MAGIC:
            _, ptype, subtype, part_offset, size, raw_label, flags = struct.unpack(
                "<HBBII16sI", entry
            )
            label = raw_label.split(b"\0", 1)[0].decode("ascii", errors="strict")
            entries.append(
                {
                    "label": label,
                    "type": ptype,
                    "subtype": subtype,
                    "offset": part_offset,
                    "size": size,
                    "end": part_offset + size,
                    "flags": flags,
                }
            )
            continue
        if magic == MD5_MAGIC:
            if entry[2:16] != b"\xff" * 14:
                raise ValueError("partition MD5 entry has invalid reserved bytes")
            md5_offset = offset
            stored_md5 = entry[16:32]
            break
        raise ValueError(f"unexpected partition-table magic 0x{magic:04x} at 0x{offset:x}")

    if not entries:
        raise ValueError("partition table contains no entries")
    if md5_offset is None or stored_md5 is None:
        raise ValueError("partition table has no MD5 entry")
    calculated_md5 = hashlib.md5(sector[:md5_offset]).digest()
    if stored_md5 != calculated_md5:
        raise ValueError("partition table MD5 does not validate")
    if any(value != 0xFF for value in sector[md5_offset + 32 :]):
        raise ValueError("partition table reserved/padding bytes are not erased")

    validate_partition_bounds(entries)
    return {
        "entries": entries,
        "entry_count": len(entries),
        "md5_offset": md5_offset,
        "stored_md5": stored_md5.hex(),
        "calculated_md5": calculated_md5.hex(),
        "sha256": sha256_bytes(sector),
    }


def validate_partition_bounds(entries: Iterable[dict[str, Any]]) -> None:
    ordered = sorted(entries, key=lambda entry: entry["offset"])
    previous_end = TABLE_OFFSET + TABLE_SIZE
    for entry in ordered:
        start = entry["offset"]
        end = entry["end"]
        if start < previous_end:
            raise ValueError(f"partition {entry['label']} overlaps a previous region")
        if end <= start or end > FLASH_SIZE:
            raise ValueError(f"partition {entry['label']} exceeds 4 MiB flash")
        if entry["type"] == 0 and start % 0x10000:
            raise ValueError(f"app partition {entry['label']} is not 64 KiB aligned")
        if entry["type"] == 1 and start % SECTOR_SIZE:
            raise ValueError(f"data partition {entry['label']} is not sector aligned")
        previous_end = end


def require_exact_partitions(
    report: dict[str, Any], expected: Iterable[tuple[str, int, int, int, int, int]]
) -> None:
    actual = tuple(partition_tuple(entry) for entry in report["entries"])
    wanted = tuple(expected)
    if actual != wanted:
        raise ValueError(f"partition entries differ\nactual={actual!r}\nexpected={wanted!r}")


def validate_native_image(data: bytes, maximum_size: int, label: str) -> dict[str, Any]:
    if len(data) > maximum_size:
        raise ValueError(f"{label} is too large: {len(data)} > {maximum_size}")
    report = parse_esp_image(data)
    if not report["chip_is_esp32_c3"]:
        raise ValueError(f"{label} does not target ESP32-C3")
    if not report["checksum_valid"]:
        raise ValueError(f"{label} has an invalid ESP checksum")
    if report["sha256_valid"] is False:
        raise ValueError(f"{label} has an invalid appended SHA-256")
    if report["trailing_bytes"]:
        raise ValueError(f"{label} has {report['trailing_bytes']} trailing bytes")
    return report


NVS_PAGE_STATES = {
    0xFFFFFFFF: "empty",
    0xFFFFFFFE: "active",
    0xFFFFFFFC: "full",
    0xFFFFFFF8: "freeing",
    0xFFFFFFF0: "corrupt",
}
NVS_KNOWN_TYPES = {
    0x01, 0x02, 0x04, 0x08, 0x11, 0x12, 0x14, 0x18,
    0x21, 0x41, 0x42, 0x48,
}
NVS_VARIABLE_TYPES = {0x21, 0x41, 0x42}
NVS_BLOB_DATA = 0x42
NVS_BLOB_INDEX = 0x48


def analyze_canonical_nvs(data: bytes) -> dict[str, Any]:
    """Check whether the retained official-size NVS window is self-contained enough.

    This is a conservative structural gate, not a replacement for boot testing.
    It validates page headers and records top-level NVS keys without exposing
    their values.
    """
    if len(data) != CANONICAL_NVS_SIZE:
        raise ValueError(f"canonical NVS capture must be {CANONICAL_NVS_SIZE} bytes")

    pages: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    keys: set[str] = set()
    for page_index in range(CANONICAL_NVS_SIZE // SECTOR_SIZE):
        page = data[page_index * SECTOR_SIZE : (page_index + 1) * SECTOR_SIZE]
        state, sequence = struct.unpack_from("<II", page, 0)
        state_name = NVS_PAGE_STATES.get(state, f"unknown-0x{state:08x}")
        stored_crc = struct.unpack_from("<I", page, 28)[0]
        calculated_crc = zlib.crc32(page[4:28], 0xFFFFFFFF) & 0xFFFFFFFF
        header_crc_valid = state == 0xFFFFFFFF or stored_crc == calculated_crc
        page_keys: list[str] = []
        invalid_entries: list[int] = []

        if state != 0xFFFFFFFF:
            bitmap = page[32:64]
            index = 0
            while index < 126:
                entry_state = (bitmap[index // 4] >> ((index % 4) * 2)) & 0x03
                entry = page[64 + index * 32 : 96 + index * 32]
                namespace, entry_type, span, chunk_index = struct.unpack_from(
                    "<BBBB", entry, 0
                )
                raw_key = entry[8:24].split(b"\0", 1)[0]
                if entry_state == 2 and entry_type in NVS_KNOWN_TYPES and span >= 1:
                    span_fits = index + span <= 126
                    span_written = span_fits and all(
                        ((bitmap[part // 4] >> ((part % 4) * 2)) & 0x03) == 2
                        for part in range(index, index + span)
                    )
                    stored_entry_crc = struct.unpack_from("<I", entry, 4)[0]
                    calculated_entry_crc = (
                        zlib.crc32(entry[:4] + entry[8:32], 0xFFFFFFFF) & 0xFFFFFFFF
                    )
                    entry_crc_valid = stored_entry_crc == calculated_entry_crc
                    try:
                        key = raw_key.decode("ascii", errors="strict")
                    except UnicodeDecodeError:
                        key = ""
                    if key and all(0x20 <= ord(char) <= 0x7E for char in key):
                        page_keys.append(key)
                        keys.add(key)
                    record: dict[str, Any] = {
                        "page": page_index,
                        "entry": index,
                        "namespace": namespace,
                        "type": entry_type,
                        "span": span,
                        "chunk_index": chunk_index,
                        "key": key,
                        "span_fits": span_fits,
                        "span_written": span_written,
                        "entry_crc_valid": entry_crc_valid,
                    }
                    if entry_type in NVS_VARIABLE_TYPES and span_fits:
                        data_length = struct.unpack_from("<H", entry, 24)[0]
                        payload = page[
                            64 + (index + 1) * 32 : 64 + (index + span) * 32
                        ][:data_length]
                        stored_data_crc = struct.unpack_from("<I", entry, 28)[0]
                        calculated_data_crc = (
                            zlib.crc32(payload, 0xFFFFFFFF) & 0xFFFFFFFF
                        )
                        record.update(
                            {
                                "data_length": data_length,
                                "span_length_valid": span == 1 + (data_length + 31) // 32,
                                "data_crc_valid": stored_data_crc == calculated_data_crc,
                            }
                        )
                    elif entry_type == NVS_BLOB_INDEX:
                        total_size, chunk_count, chunk_start = struct.unpack_from(
                            "<IBB", entry, 24
                        )
                        record.update(
                            {
                                "total_size": total_size,
                                "chunk_count": chunk_count,
                                "chunk_start": chunk_start,
                            }
                        )
                    records.append(record)
                    record_valid = span_fits and span_written and entry_crc_valid
                    if entry_type in NVS_VARIABLE_TYPES:
                        record_valid = (
                            record_valid
                            and record.get("span_length_valid", False)
                            and record.get("data_crc_valid", False)
                        )
                    if not record_valid:
                        invalid_entries.append(index)
                    index += span
                else:
                    if entry_state == 2:
                        invalid_entries.append(index)
                    index += 1

        pages.append(
            {
                "index": page_index,
                "offset": CANONICAL_NVS_OFFSET + page_index * SECTOR_SIZE,
                "state": state_name,
                "sequence": sequence,
                "header_crc_valid": header_crc_valid,
                "keys": sorted(set(page_keys)),
                "invalid_entries": invalid_entries,
            }
        )

    active_pages = [page for page in pages if page["state"] == "active"]
    empty_pages = [page for page in pages if page["state"] == "empty"]
    invalid_headers = [page for page in pages if not page["header_crc_valid"]]
    invalid_entry_pages = [page for page in pages if page["invalid_entries"]]
    required_keys = {"Settings", "sta.apinfo"}
    missing_keys = sorted(required_keys - keys)

    required_records: dict[str, dict[str, Any]] = {}
    incomplete_keys: list[str] = []
    for required_key in sorted(required_keys):
        indexes = [
            record
            for record in records
            if record["key"] == required_key
            and record["type"] == NVS_BLOB_INDEX
            and record["entry_crc_valid"]
        ]
        complete = False
        detail: dict[str, Any] = {"index_records": len(indexes), "complete": False}
        if len(indexes) == 1:
            blob_index = indexes[0]
            start = blob_index["chunk_start"]
            count = blob_index["chunk_count"]
            expected_chunks = [((start + number) & 0xFF) for number in range(count)]
            chunks = [
                record
                for record in records
                if record["key"] == required_key
                and record["namespace"] == blob_index["namespace"]
                and record["type"] == NVS_BLOB_DATA
                and record["chunk_index"] in expected_chunks
            ]
            chunks_by_index = {record["chunk_index"]: record for record in chunks}
            ordered_chunks = [chunks_by_index.get(chunk) for chunk in expected_chunks]
            chunk_metadata_valid = all(
                chunk is not None
                and chunk["span_fits"]
                and chunk["span_written"]
                and chunk["entry_crc_valid"]
                and chunk.get("span_length_valid", False)
                and chunk.get("data_crc_valid", False)
                for chunk in ordered_chunks
            )
            observed_size = sum(
                chunk.get("data_length", 0) for chunk in ordered_chunks if chunk is not None
            )
            complete = (
                count > 0
                and len(chunks) == count
                and len(chunks_by_index) == count
                and chunk_metadata_valid
                and observed_size == blob_index["total_size"]
            )
            detail.update(
                {
                    "namespace": blob_index["namespace"],
                    "total_size": blob_index["total_size"],
                    "chunk_count": count,
                    "chunk_start": start,
                    "found_chunks": sorted(chunks_by_index),
                    "observed_size": observed_size,
                    "complete": complete,
                }
            )
        required_records[required_key] = detail
        if not complete:
            incomplete_keys.append(required_key)

    ready = (
        len(active_pages) == 1
        and bool(empty_pages)
        and not invalid_headers
        and not invalid_entry_pages
        and not missing_keys
        and not incomplete_keys
    )
    return {
        "size": len(data),
        "sha256": sha256_bytes(data),
        "pages": pages,
        "keys": sorted(keys),
        "required_keys": sorted(required_keys),
        "missing_keys": missing_keys,
        "incomplete_keys": incomplete_keys,
        "required_records": required_records,
        "invalid_entry_pages": [page["index"] for page in invalid_entry_pages],
        "ready": ready,
    }


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not load manifest {path}: {exc}") from exc
    if manifest.get("schema") != 1:
        raise ValueError("unsupported migration manifest schema")
    return manifest


def verify_manifest_files(manifest_path: Path, manifest: dict[str, Any]) -> None:
    root = manifest_path.parent
    for name, artifact in manifest["artifacts"].items():
        path = root / artifact["file"]
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"could not read {name} artifact: {exc}") from exc
        if len(data) != artifact["size"]:
            raise ValueError(f"{name} size changed")
        if sha256_bytes(data) != artifact["sha256"]:
            raise ValueError(f"{name} SHA-256 changed")


def artifact_bytes(
    manifest_path: Path, manifest: dict[str, Any], name: str
) -> bytes:
    """Read one already-verified manifest artifact."""
    try:
        artifact = manifest["artifacts"][name]
    except KeyError as exc:
        raise ValueError(f"manifest has no {name} artifact") from exc
    path = manifest_path.parent / artifact["file"]
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"could not read {name} artifact: {exc}") from exc
    if len(data) != artifact["size"]:
        raise ValueError(f"{name} size changed")
    if sha256_bytes(data) != artifact["sha256"]:
        raise ValueError(f"{name} SHA-256 changed")
    return data


def load_evidence(path: Path, expected_phase: str, max_age_seconds: int) -> dict[str, Any]:
    """Load a host-validated live-phase record and reject stale evidence."""
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not load {expected_phase} evidence {path}: {exc}") from exc
    if evidence.get("schema") != 1 or evidence.get("phase") != expected_phase:
        raise ValueError(f"{path} is not {expected_phase} evidence")
    if evidence.get("status") != "PASS":
        raise ValueError(f"{expected_phase} evidence did not pass")
    try:
        created = datetime.fromisoformat(evidence["created_utc"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{expected_phase} evidence has an invalid timestamp") from exc
    if created.tzinfo is None:
        raise ValueError(f"{expected_phase} evidence timestamp has no timezone")
    age = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()
    if age < -60 or age > max_age_seconds:
        raise ValueError(
            f"{expected_phase} evidence is not fresh (age {age:.0f}s; "
            f"limit {max_age_seconds}s)"
        )
    return evidence
