# S60TPG 1.1.1 recovery status

## Current state

- The device is online at its existing LAN address and advertises stock
  `fwVersion` 1.2.0 through its encrypted eWeLink mDNS state.
- The official 1.2.0 `user1` file was captured completely. Its 1,456,404-byte
  file hash matches the SHA-256 digest returned by the authenticated vendor
  manifest.
- No authentic 1.1.1 application image or 1.1.1 manifest is present in the
  project, Claude cache, Codex temporary files, or ordinary local download
  locations searched on 2026-09-01.
- No public 1.1.1 download was found in searches by firmware family, internal
  model string, device model, and vendor filename.

## Why the previous application may still be recoverable

ESP-IDF OTA writes an update to a non-running OTA slot and updates `otadata` to
boot it. Published S60TPG boot information identifies two application slots:

- `ota_0` at `0x20000`
- `ota_1` at `0x210000`

Each slot spans approximately `0x1f0000` bytes. With only one completed vendor
update, the original 1.1.1 application should normally remain in the other
slot. Static strings in the captured 1.2.0 application do not include
`esp_ota_erase_last_boot_app_partition`, although absence of a string is not
proof that the previous slot was preserved.

Do not send another OTA command: the updater normally chooses the non-running
slot, which is exactly where the possible 1.1.1 copy should reside.

## Recovery paths

### Preferred: preserve the flash over serial

With the plug disconnected from mains and the ESP32-C3 placed into download
mode, read the complete 4 MiB flash before writing anything:

```text
esptool.py --chip esp32c3 --port <PORT> read_flash 0x000000 0x400000 flash_dump.bin
```

The dump must remain private because data partitions may contain Wi-Fi and
eWeLink credentials. After capture, parse the partition table and identify
which slot contains version 1.1.1. Only then consider changing `otadata` or
extracting an app-only recovery image.

Opening this mains-rated plug is mechanically difficult and must never be done
while connected to mains power.

### Alternative: request a sanitized app extraction

An S60TPG investigator documented taking a 4 MiB dump while firmware 1.1.1 was
installed. The full dump should not be shared because it may contain secrets,
but its app-only OTA partition could provide a recovery candidate:

https://www.elektroda.com/news/news4122886.html

Any externally obtained image must be quarantined and validated for the exact
firmware family, wrapper format, ESP32-C3 image integrity, size, and provenance
before it is considered for use.

## Network cleanup

The temporary UCG rules used during interception matched:

- S60 source `192.168.1.96`
- vendor destination `52.57.99.135:8088`
- relay destination `192.168.1.141:8088`

A read-only SSH audit was attempted, but the gateway correctly required
interactive authentication. Confirm in UniFi that the temporary DNAT,
MASQUERADE, and earlier blocking rules are disabled or removed.
