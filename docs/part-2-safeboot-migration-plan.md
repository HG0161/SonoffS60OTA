# Part 2: guarded migration to the exact Tasmota Safeboot layout

## Status

This is the reviewed design behind the staged tools. The operator procedure is
in [`part-2-safeboot-migration-runbook.md`](part-2-safeboot-migration-runbook.md).
The destructive commit remains locked by default and has not been trialled on
this S60 hardware.

The source S60TPG table was decoded from a complete 4 MiB flash dump and its
MD5 record was verified. See
[`s60-actual-partition-map.md`](s60-actual-partition-map.md).

## Objective

Replace the stock S60 table with an exact copy of the partition table embedded
in the official release `tasmota32c3.factory.bin`:

| Partition | Type | Subtype | Start | End (exclusive) | Size |
|---|---|---|---:|---:|---:|
| `nvs` | data | NVS | `0x009000` | `0x00e000` | 20 KiB (`0x005000`) |
| `otadata` | data | OTA | `0x00e000` | `0x010000` | 8 KiB (`0x002000`) |
| `safeboot` | app | factory | `0x010000` | `0x0e0000` | 832 KiB (`0x0d0000`) |
| `app0` | app | OTA 0 | `0x0e0000` | `0x3b0000` | 2,880 KiB (`0x2d0000`) |
| `spiffs` | data | SPIFFS | `0x3b0000` | `0x400000` | 320 KiB (`0x050000`) |

Every entry and flag, the table MD5 record, and erased-byte padding in the
4 KiB table sector must match the selected official factory artifact exactly.

## Consequences of the exact layout

The exact table deliberately does not preserve the S60-specific system-data
layout:

- `nvs` shrinks from 64 KiB to the official 20 KiB;
- `reserve` is removed;
- `otadata` moves from `0x01d000` to `0x00e000`;
- `phy_init` is removed;
- Safeboot starts at the official `0x010000` instead of the S60 app boundary
  at `0x020000`.

Safeboot has no initial Wi-Fi configuration UI. The existing first 20 KiB of
NVS must therefore be proven readable after the logical shrink before commit.
Configuration export is mandatory, but it cannot restore connectivity until
normal Tasmota is running.

The captured dump does not presently pass that requirement. Its live
`Settings` blob index declares two chunks: chunk 1 is within the retained
20 KiB, while chunk 0 is in the page beginning at `0x010000`. That page will
become Safeboot. Seeing the `Settings` key in the retained pages is therefore
not sufficient; every indexed chunk and its CRC must validate inside the
retained window.

## Required artifacts

Freeze these before touching the layout:

1. The private original 4 MiB S60 dump.
2. The original table sector and both old `otadata` sectors.
3. The current `0x009000..0x00dfff` NVS bytes.
4. A Tasmota configuration export and the S60 GPIO template.
5. The exact Bluetooth image currently running.
6. One pinned official release `tasmota32c3.factory.bin`.
7. The official Safeboot and normal C3 application images from the same
   release.
8. The official table sector extracted from that factory image.
9. SHA-256 values and byte lengths for every artifact.

The private dump and NVS data must remain outside Git.

## Phase 0: offline validation

Do not write the plug during this phase.

1. Extract `0x8000..0x8fff` from the pinned official factory image rather than
   regenerating an approximately equivalent table.
2. Parse the extracted table independently and require exact agreement with
   the target above, including flags and MD5.
3. Verify image magic and complete ESP image boundaries at `0x010000` and
   `0x0e0000` inside the factory image.
4. Require Safeboot to fit within `0x0d0000` and final Tasmota within
   `0x2d0000`.
5. Parse the current first five NVS pages (`0x009000..0x00dfff`). Require an
   active page, adequate free space, and complete, CRC-valid blob indexes and
   chunks for Tasmota `Settings` and Wi-Fi `sta.apinfo`. The frozen source dump
   is expected to fail this check and is retained as evidence, not accepted as
   migration-ready NVS.
6. Prepare an inspect-only Berry preflight that reports the live table hash,
   running partition, flash size, NVS state and every artifact hash.
7. Prepare a separate commit operation protected by an exact, single-use
   confirmation token.

## Phase 1: put Bluetooth in the high stock slot

1. While Bluetooth Tasmota runs from old `ota_0` at `0x020000`, upload the
   exact same Bluetooth binary through the normal Firmware Upgrade page.
2. Let Tasmota write and validate old `ota_1` at `0x210000`, select it and
   reboot.
3. Require:
   - the expected Tasmota version and build hash;
   - Tasmota Information marks old `ota_1` active;
   - Berry `flash.current_ota()` returns `1`;
   - the live partition sector still matches the original S60 hash;
   - Wi-Fi and Berry remain usable.
4. Back up Tasmota configuration, the S60 template, rules and custom files.
5. Because the captured NVS is not self-contained, issue documented Tasmota
   `Reset 4` while high Bluetooth is active. This resets firmware settings to
   defaults while retaining Wi-Fi and may cause NVS to recreate compact
   records. Reconfirm Wi-Fi, Berry access and old `ota_1` after restart.
6. Disable periodic settings saves, then capture and validate the current
   first 20 KiB of NVS again. `Reset 4` is not the proof: stop unless the live
   preflight independently finds every declared `Settings` and `sta.apinfo`
   blob chunk, correct total sizes, valid entry/data CRCs, and usable page
   structure wholly within that range.

Before the table commit, the low Bluetooth image remains a bootable fallback.

## Phase 2: stage the exact official Safeboot payload

Safeboot ultimately belongs at `0x010000`, which overlaps the source table's
NVS tail, `reserve`, old `otadata`, `phy_init`, and low application. It must be
written only while Bluetooth executes from the high slot.

1. Serve the pinned Safeboot payload from the local workstation. Do not fetch
   an unpinned moving URL during migration.
2. Stage and verify a complete copy in the inactive low stock application
   area at `0x020000` first.
3. Read the staged bytes back using the RAM-only flash-download service and
   require their SHA-256 to match the pinned artifact.
4. Keep the official table sector, original table sector and exact Safeboot
   length available for the final operation.

Staging at `0x020000` proves the payload before any system-data address is
overwritten. It is temporary; the commit operation copies the verified image
downward to the canonical `0x010000` address while running from high flash.

## Phase 3: canonical-layout commit

This is the destructive phase. External power must be stable and isolated
serial recovery must be accepted as unavailable.

Immediately before commit, require:

- execution from old `ota_1` at `0x210000`;
- the live table equals the allow-listed S60 table-sector hash;
- the staged Safeboot copy equals its allow-listed hash;
- the first 20 KiB NVS preflight still passes;
- the target sector exactly equals `0x8000..0x8fff` from the pinned official
  factory image;
- the original table and live old `otadata` are buffered in RAM for a
  same-session source-layout restoration attempt if any commit write fails.

Commit sequence:

1. Copy the verified Safeboot image downward from staging at `0x020000` to its
   canonical address `0x010000`, one 4 KiB sector at a time. Read a source
   sector before erasing/writing its lower destination.
2. Verify the canonical Safeboot hash and its erased partition tail in flash.
3. Erase and verify the new `otadata` range at `0x00e000..0x00ffff` so the
   bootloader selects the factory partition after the table changes.
4. Erase/write the exact official table sector at `0x8000..0x8fff`.
5. Read the table back and compare every byte and its SHA-256.
6. If a commit operation fails while Bluetooth remains alive, make one
   controlled attempt to restore and verify the original table and live old
   `otadata`. Do not reboot after a rollback attempt.
7. Reboot immediately using the already-running application; do not invoke
   `flash.factory()` after changing the table.

`flash.factory()` must not be used in this exact-layout migration. ESP-IDF's
in-RAM partition registry still describes the source S60 table until reboot,
so the function would erase the old `otadata` address at `0x01d000`. That
address is inside canonical Safeboot after it has been copied to `0x010000`,
and erasing it would corrupt the staged recovery image. With the new
`0x00e000` `otadata` sectors erased and a factory entry present, the bootloader
selects Safeboot without that call.

Writing Safeboot at `0x010000` destroys the old NVS tail and old boot metadata.
After that copy begins, an unexpected reset relies on the bootloader locating
the still-valid high Bluetooth image until the official table is committed.
Power loss during the table-sector erase/write can require physical recovery.

## Phase 4: verify official Safeboot

1. Require the device to return at its reserved address using configuration
   from the retained canonical 20 KiB NVS region.
2. Confirm the running application is factory `safeboot` at `0x010000`.
3. Confirm the live table sector exactly matches the official factory image.
4. Confirm `app0` at `0x0e0000` is not selected before a valid image is
   installed.
5. Stop if the Safeboot firmware-upload page is unavailable.

There is no Wi-Fi setup fallback in Safeboot. Failure to read the retained NVS
configuration is not recoverable over Wi-Fi.

## Phase 5: install normal Tasmota

1. Upload the pinned normal `tasmota32c3.bin` through Safeboot.
2. Safeboot writes it to official `app0` at `0x0e0000`.
3. Confirm the running partition offset, version and image hash after reboot.
4. Confirm SPIFFS at `0x3b0000` can be formatted, written, read and retained
   across restart.
5. Restore or verify the S60 template and test relay, button, LED, CSE7766
   metering, Wi-Fi, web UI and normal restart.
6. Perform one subsequent Safeboot-mediated OTA cycle before declaring the
   migration complete.

## Layout progression

| Stage | Low region | High region | Partition table |
|---|---|---|---|
| Initial | Bluetooth in old `ota_0` | Previous Tasmota in old `ota_1` | Original S60 |
| High staging | Bluetooth in old `ota_0` | Bluetooth active in old `ota_1` | Original S60 |
| Safeboot staging | Verified Safeboot copy begins at `0x020000` | Bluetooth active at `0x210000` | Original S60 |
| Commit | Safeboot copied to `0x010000` | Bluetooth remains executing at `0x210000` | Replaced once with exact official table |
| First canonical boot | Safeboot active at `0x010000` | Old bytes are now inside unpopulated `app0`/SPIFFS regions | Exact official |
| Final | Safeboot at `0x010000` | Normal Tasmota in `app0`; SPIFFS formatted | Exact official |

## Optimizations retained after review

1. Use normal Tasmota OTA to establish the high Bluetooth copy instead of raw
   partition copying.
2. Extract the table sector from the pinned factory binary; do not recreate it
   from CSV during the live operation.
3. Stage and hash Safeboot before overwriting canonical low addresses.
4. Perform only one partition-table replacement.
5. Separate inspect, stage and commit operations; commit remains disabled by
   default.
6. Verify large images from the workstation instead of retaining them in
   Berry RAM.
7. Preserve an in-RAM original table for immediate restoration after a
   detected table-write error.

## Go/no-go conditions

Implementation may begin only if all answers are yes:

- Is an exact copy of the selected official release table required despite
  the increased Wi-Fi recovery risk?
- After backup and controlled NVS recreation, does the live first 20 KiB NVS
  region independently validate complete `Settings` and `sta.apinfo` blobs?
- Are all artifacts from one pinned official release and independently
  hashed?
- Are Safeboot and final Tasmota smaller than their official partitions?
- Are inspect and commit separate, with commit disabled by default?
- Is stable power available for the sole table-sector write?
- Will the private dump and NVS data remain outside Git?
