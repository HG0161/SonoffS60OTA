# S60 private-Safeboot recovery contingency

This path is used only after the official-Safeboot live NVS preflight and one
controlled `Reset 4` attempt both refuse. It does **not** relax or bypass the
official procedure. It creates a separate manifest, evidence directory and
explicit recovery mode.

The target partition-table sector remains the byte-exact official sector with
SHA-256
`619e5a9b645198b25d04949997221f587776965dfd13cbceddf9862c53e655c1`.
Only the initial factory-partition payload is temporarily different.

## Why a source patch is required

Defining `STA_SSID1` and `STA_PASS1` alone is insufficient. Tasmota Safeboot is
`FIRMWARE_MINIMAL`; its normal `SettingsLoad()` path deliberately skips default
settings initialization. The reviewed patch calls `SettingsDefaultSet1()` and
`SettingsDefaultSet2()` in recovery Safeboot only. This copies the compiled
Wi-Fi values into RAM on every boot while `SettingsSave()` remains disabled.

With the official 20 KiB NVS table installed, ESP32 Arduino initialization
detects the truncated NVS state (`ESP_ERR_NVS_NO_FREE_PAGES`), erases that new
NVS partition and initializes it again. The recovery build does not depend on
the old Tasmota `Settings` blob surviving that erase.

## Private offline preparation

Never type the Wi-Fi password into chat, a shell argument or a committed file.
Run the local hidden-input helper:

```bash
python3 tools/configure_recovery_safeboot.py
```

It creates this Git-ignored mode-0600 header:

```text
captures/safeboot-recovery/user_config_override.h
```

Use a clean checkout of pinned Tasmota commit
`db3ae7e0276fdc38b7aeb241a2a8e33d8ffd6892`, then build:

```bash
tools/build_recovery_safeboot.sh /tmp/s60-tasmota-recovery-src
```

The build script:

- refuses any other source commit or tracked source modifications;
- applies the reviewed volatile-defaults patch;
- builds only `tasmota32c3-safeboot`;
- requires a valid ESP32-C3 image within the 832 KiB factory partition;
- proves the recovery marker, SSID and password were linked without printing
  them;
- stores the image and validation report with mode 0600;
- cleans credential-bearing build intermediates and restores the source tree.

Create the separate recovery migration manifest:

```bash
python3 tools/prepare_recovery_safeboot_migration.py
python3 tools/safeboot_migration_status.py \
  --manifest captures/safeboot-recovery-migration/manifest.json
```

Do not continue unless both commands say PASS. The status must identify
`PRIVATE VOLATILE-WIFI RECOVERY IMAGE` and the exact official target table.

## Guarded live sequence

The plug must still be running Bluetooth from old high `ota_1`; Berry
`flash.current_ota()` must return `1`. Run `SaveData 0` again immediately before
the new preflight.

Run recovery preflight, stage and commit with the recovery manifest:

```bash
python3 tools/serve_safeboot_migration.py preflight \
  --manifest captures/safeboot-recovery-migration/manifest.json \
  --listen-ip <WORKSTATION_LAN_IP> --device-ip 192.168.1.96

python3 tools/serve_safeboot_migration.py stage \
  --manifest captures/safeboot-recovery-migration/manifest.json \
  --listen-ip <WORKSTATION_LAN_IP> --device-ip 192.168.1.96
```

Recovery preflight records incomplete NVS as expected but still requires the
exact source table, high running slot, complete host uploads and a stable NVS
hash. Stage writes only inactive old `ota_0` and uploads a complete read-back.

Commit retains the same safety lock, explicit risk flag, separate loader and
single-use arm token as the official path. Use the exact command printed by the
status tool; never invent or reuse a token.

After commit, the plug should boot private recovery Safeboot and reconnect with
its reserved address. Upload the pinned official `tasmota32c3.bin` through its
Firmware Upgrade page. Normal Tasmota may reconnect directly or start its
supported setup AP; configure Wi-Fi there if required.

## Restore exact official Safeboot

After the pinned normal image is stable, require Berry
`flash.current_ota() == 0`. Start the cleanup server:

```bash
python3 tools/serve_safeboot_migration.py restore \
  --manifest captures/safeboot-recovery-migration/manifest.json \
  --listen-ip <WORKSTATION_LAN_IP> --device-ip 192.168.1.96 \
  --i-confirm-normal-app-is-stable
```

Loading the printed closure performs no writes. The separate arm command first
requires the exact official table, pinned official running app and private
recovery Safeboot hashes. It then writes official Safeboot at `0x010000`,
uploads every read-back byte to the host, verifies its SHA-256, and erases the
remaining factory-partition tail.

Power loss during this cleanup does not overwrite the running canonical app0.
Leave that app running and retry cleanup; do not deliberately select a partial
factory image. Completion requires `restore-report.json` with status `PASS`.

The custom image and its build files contain Wi-Fi credentials. Keep all
`captures/safeboot-recovery*` directories private and never publish them.
