# Confirmed Sonoff S60TPG partition map

This table was decoded from a complete 4 MiB flash dump captured from the
converted UK Sonoff S60TPG on 4 September 2026. The partition table begins at
flash offset `0x8000`.

| Partition | Type | Subtype | Start | End (exclusive) | Size | Flags | Installed content |
|---|---|---|---:|---:|---:|---:|---|
| Partition table | — | — | `0x008000` | `0x009000` | 4 KiB (`0x001000`) | — | Original Sonoff partition table |
| `nvs` | data (`0x01`) | NVS (`0x02`) | `0x009000` | `0x019000` | 64 KiB (`0x010000`) | `0x0` | Configuration data |
| `reserve` | data (`0x01`) | NVS (`0x02`) | `0x019000` | `0x01d000` | 16 KiB (`0x004000`) | `0x0` | Sonoff reserved data |
| `otadata` | data (`0x01`) | OTA (`0x00`) | `0x01d000` | `0x01f000` | 8 KiB (`0x002000`) | `0x0` | OTA boot-selection records |
| `phy_init` | data (`0x01`) | PHY (`0x01`) | `0x01f000` | `0x020000` | 4 KiB (`0x001000`) | `0x0` | Radio initialization data |
| `ota_0` | app (`0x00`) | OTA 0 (`0x10`) | `0x020000` | `0x210000` | 1,984 KiB (`0x1f0000`) | `0x0` | Tasmota 15.6.0.1 Bluetooth, active when captured |
| `ota_1` | app (`0x00`) | OTA 1 (`0x11`) | `0x210000` | `0x400000` | 1,984 KiB (`0x1f0000`) | `0x0` | Previous custom S60 Tasmota 15.6.0 |

## Partition-table validation

The table contains six partition entries followed by an ESP-IDF MD5 record.

```text
Stored table MD5:     e598c5db7f49d780d00f99a0829c5312
Calculated table MD5: e598c5db7f49d780d00f99a0829c5312
MD5 valid:            yes

SHA-256 of 0x8000..0x8fff:
f63f66bbf23b9e291c7eb5dcf24be820190dacf4bf52af515d9664526a4f4daf
```

The table covers the complete flash without a filesystem partition or an
unallocated gap between the data partitions and `ota_0`.

## Sensitive source material

The full flash dump is deliberately not included in this repository. It can
contain Wi-Fi credentials and other private values in cleartext. Only the
decoded partition metadata and hashes above are suitable for publication.
