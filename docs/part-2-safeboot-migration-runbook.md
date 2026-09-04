# Part 2 operator runbook: exact Tasmota Safeboot migration

## Scope and present status

This runbook migrates the one captured 4 MiB S60TPG from its Sonoff dual-OTA
layout to the byte-for-byte partition table embedded in the pinned official
`tasmota32c3.factory.bin`.

The Python validators, host server and rendered Berry syntax are tested
offline. The destructive commit has **not** been trialled on this S60 hardware.
An interruption while the table sector is being replaced can still make
network recovery impossible. The scripts reduce and gate that risk; they
cannot create a bootloader rollback that the source device does not have.

Private flash, NVS and live evidence are written below
`captures/safeboot-migration/`, which Git ignores.

The captured source image has exposed one important prerequisite: its current
`Settings` blob is split across the future 20 KiB NVS boundary. Chunk 1 is in
the retained range, but chunk 0 begins at `0x010000`, where official Safeboot
must be installed. The current NVS therefore **cannot** survive the exact
layout as-is. The live preflight checks complete blob chunks, not merely key
names, and will refuse this state.

## The three live stages

| Stage | Device writes | Boot/layout effect | Can merely loading it write? |
|---|---|---|---|
| `preflight` | None | Requires Bluetooth active in old `ota_1`; captures table, canonical NVS and old `otadata` | No |
| `stage` | Official Safeboot into inactive old `ota_0` at `0x020000` | Source table and boot selection remain unchanged | Yes; this stage is intentionally a writer |
| `commit` | Copies Safeboot to `0x010000`, erases new `otadata`, replaces table | First reboot uses exact official Safeboot layout | **No**; loading returns a closure, and a separate fresh token must arm it |

The workstation server accepts requests only from the selected plug IP. Each
phase gets a random session identifier. The plug SHA-256 hashes live flash and
uploads read-back bytes; the workstation independently reassembles and checks
them before writing a PASS report.

## Stage 0: frozen artifacts

The existing prepared bundle can be checked at any time without contacting the
plug:

```bash
python3 tools/safeboot_migration_status.py
```

To recreate it from the private full dump and the exact Bluetooth binary:

```bash
python3 tools/prepare_safeboot_migration.py \
  --source-dump /absolute/path/to/flashdownload.bin \
  --bluetooth /absolute/path/to/tasmota32c3-bluetooth.bin
```

Preparation must end with `OFFLINE ARTIFACT PREPARATION: PASS`. Never commit
the generated `captures/` directory.

## Stage 1: make old `ota_1` the live safety island

1. Before resetting anything, use **Configuration > Backup Configuration** to
   download a Tasmota configuration backup. Separately record the S60 template
   and any rules, Berry files or other custom settings. Keep the backup outside
   Git with the flash dump.
2. In normal Tasmota Firmware Upgrade, upload the pinned file shown as the
   `bluetooth` artifact in `captures/safeboot-migration/manifest.json`. It is
   the same build already running; normal OTA should install it in old
   `ota_1` at `0x210000`.
3. After the plug returns, open the **Berry scripting console** and run:

   ```berry
   import flash print(flash.current_ota())
   ```

   Stop unless it prints `1`.
4. Because the captured `Settings` blob is not self-contained in the future
   20 KiB NVS, run `Reset 4` in the **ordinary Tasmota console**. Tasmota defines
   this as resetting to firmware defaults while retaining Wi-Fi. It is being
   used here to ask Tasmota/NVS to recreate compact current records; it is not
   itself proof that compaction succeeded.
5. When the plug returns, reconfirm Wi-Fi, the Berry console and
   `flash.current_ota() == 1`. Then run `SaveData 0` in the ordinary console,
   wait several seconds, and avoid configuration changes until normal Tasmota
   is installed at the end. This freezes the NVS bytes every later phase
   hashes.
6. Keep the plug powered from a stable outlet and keep its reserved address at
   `192.168.1.96`.

Do not run `Reset 4` until the configuration backup is safely stored. If it
loses Wi-Fi or disables access to the Berry console, stop; do not attempt the
raw migration.

## Stage 2: read-only live preflight

On the workstation, replace `<WORKSTATION_LAN_IP>` with the LAN address the
plug can reach:

```bash
python3 tools/serve_safeboot_migration.py preflight \
  --listen-ip <WORKSTATION_LAN_IP> \
  --device-ip 192.168.1.96
```

The server prints one long `s60_urlbeload(...)` line. Paste it as **one line**
into the Berry scripting console. Do not add `br`; `Br` is only the prefix used
when entering a Berry expression through the ordinary Tasmota command console.

Advance only when both ends say PASS and this file exists:

```text
captures/safeboot-migration/live/preflight-report.json
```

The host has then independently validated:

- ESP32-C3, 4 MiB flash and old `ota_1` active;
- the exact allow-listed source partition sector;
- all five retained NVS pages, including complete, CRC-valid blob indexes and
  every declared data chunk for `Settings` and `sta.apinfo` entirely within
  `0x009000..0x00dfff`;
- live old `otadata` bytes and all reported hashes.

`Reset 4` is only a candidate way to produce that state. The host-side PASS is
the gate. If preflight still reports `Settings` incomplete, stop; do not edit,
copy or fabricate raw NVS pages.

## Stage 3: stage and independently read back Safeboot

Start a new, single-purpose server:

```bash
python3 tools/serve_safeboot_migration.py stage \
  --listen-ip <WORKSTATION_LAN_IP> \
  --device-ip 192.168.1.96
```

Paste the printed one-line loader in the Berry scripting console. The device
downloads Safeboot as 4 KiB hexadecimal chunks, writes each chunk to inactive
old `ota_0`, reads it back, and uploads that read-back to the host. This is
deliberately slower than trusting a download result.

Advance only when both ends say PASS and the host has produced:

```text
captures/safeboot-migration/live/stage-report.json
captures/safeboot-migration/live/staged-safeboot-readback.bin
```

At this point the source table is untouched and Bluetooth is still executing
from high old `ota_1`. If this stage fails, stop; do not proceed to commit.

## Stage 4: separately unlock, load and arm the commit

The two preceding evidence records default to a two-hour validity window.
Rerun preflight (and stage if its evidence is stale) rather than bypassing a
freshness refusal.

Read `REPARTITION_LOCK`, then deliberately rename it and start the commit
server with its explicit risk acknowledgement:

```bash
mv REPARTITION_LOCK REPARTITION_LOCK.owner-authorized
python3 tools/serve_safeboot_migration.py commit \
  --listen-ip <WORKSTATION_LAN_IP> \
  --device-ip 192.168.1.96 \
  --i-accept-power-loss-may-require-opening-the-plug
```

The server prints two separate Berry commands:

1. Paste the loader. This downloads and compiles the commit code but performs
   **no flash writes**. It assigns the returned closure to `s60_commit`.
2. Recheck stable power and the PASS reports, then paste the separately printed
   `s60_commit("...")` token command once.

The armed closure revalidates the source table, NVS, active high slot, staged
Safeboot, erased padding and downloaded target sector before the first
destructive write. It then:

1. copies staged Safeboot downward in overlap-safe 4 KiB operations;
2. hashes the canonical copy and erases/verifies the unused Safeboot tail;
3. erases/verifies canonical `otadata` at `0x00e000`;
4. writes and byte-compares the exact official table at `0x008000`;
5. reports PASS and issues `Restart 99`, which deliberately avoids a settings
   save through the now-stale in-memory source partition registry.

It never calls `flash.factory()`. That function would use the cached source
table and erase old `otadata` at `0x01d000`, which is now inside Safeboot.

Restore the host safety lock as soon as the server exits, even if the device
does not return:

```bash
mv REPARTITION_LOCK.owner-authorized REPARTITION_LOCK
```

## Stage 5: install normal official Tasmota

The first canonical boot should be official Safeboot at the same reserved IP.
Use its Firmware Upgrade page to upload the exact file printed by:

```bash
python3 tools/safeboot_migration_status.py
```

It should be:

```text
captures/safeboot-migration/tasmota32c3.bin
```

After normal Tasmota boots, restore the saved configuration (or deliberately
reapply its settings), then run `SaveData 1`. Verify the S60 template, relay,
button, LED, CSE7766 metering, Wi-Fi and a normal reboot. In the Berry console,
this should show `safeboot`, `app0` and 320 KiB `spiffs` at the exact official
offsets:

```berry
import partition_core print(partition_core.Partition())
```

## Stop conditions

- Any phase says REFUSED, FAIL, times out, or reports a changed NVS/table hash:
  stop at that phase and preserve its console output.
- Live preflight reports incomplete `Settings` or `sta.apinfo`: stop. Do not
  proceed merely because the key names appear in a dump.
- Commit reports `S60 ROLLBACK PASS`: do not reboot or remove power even though
  the script restored the source table and live old `otadata`; inspect first.
- Commit reports `S60 ROLLBACK FAILED`: do not reboot or remove power. The
  still-running high application is the only remaining repair session.
- Power disappears during commit, or commit says PASS but the plug never
  returns: Wi-Fi recovery is not assured. Physical serial recovery may be the
  only remaining route.

Use `python3 tools/safeboot_migration_status.py` after any interruption to see
the last host-validated checkpoint and the next stage. It never contacts or
writes the plug.
