# Part 2: understand and safely change the S60 partition table

## Scope and current evidence

The converted S60 has a 4 MiB (`0x400000`) ESP32-C3 flash. Its stock
partition table is at the normal ESP-IDF address, `0x8000`. The offsets below
come from the stock table used by both the bridge and Tasmota builds; a full
post-conversion flash dump has not been captured, so the migration firmware
must verify the table on the device before it enables any write.

The currently understood map is:

| Region | Start | End (exclusive) | Size | Purpose |
|---|---:|---:|---:|---|
| Bootloader | `0x000000` | `0x008000` | `0x008000` | ROM-loaded second-stage bootloader and reserved space |
| Partition table | `0x008000` | `0x009000` | `0x001000` | ESP-IDF entries plus table MD5 |
| `nvs` | `0x009000` | `0x00e000` | `0x005000` | Wi-Fi and application settings |
| `otadata` | `0x00e000` | `0x010000` | `0x002000` | Redundant OTA boot-selection records |
| `phy_init` | `0x010000` | `0x011000` | `0x001000` | RF initialization data |
| Unallocated | `0x011000` | `0x020000` | `0x00f000` | Alignment/reserved gap |
| `ota_0` | `0x020000` | `0x210000` | `0x1f0000` | Recovery bridge on the converted unit |
| `ota_1` | `0x210000` | `0x400000` | `0x1f0000` | Final Tasmota on the converted unit |

There is currently no filesystem partition. The Tasmota overlay's
`esp32_partition_app2880k_fs320k.csv` setting was a link/build aid only. An
application-only OTA never replaced the stock table at `0x8000`, so the table
actually read at runtime remains the stock one.

## Candidate layout

The least disruptive useful change is
[`repartition/partitions-preserve-installed-apps.csv`](../repartition/partitions-preserve-installed-apps.csv):

| Partition | Start | End (exclusive) | Size | Change |
|---|---:|---:|---:|---|
| `nvs` | `0x009000` | `0x00e000` | 20 KiB | unchanged |
| `otadata` | `0x00e000` | `0x010000` | 8 KiB | unchanged |
| `phy_init` | `0x010000` | `0x011000` | 4 KiB | unchanged |
| `ota_0` | `0x020000` | `0x210000` | 1,984 KiB | unchanged; bridge retained |
| `ota_1` | `0x210000` | `0x380000` | 1,472 KiB | shortened in place |
| `spiffs` | `0x380000` | `0x400000` | 512 KiB | new |

The final Tasmota image is 1,299,456 bytes (`0x13d400`), leaving 207,872
bytes (`0x32c00`) between that image's file length and the proposed `ota_1`
limit. The bridge retains its full original slot. No installed application
start address changes, and `otadata` can continue selecting `ota_1` by subtype.

The data subtype is named `spiffs` because that is the conventional Tasmota
partition label/subtype. Whether this exact reduced Tasmota build mounts it
must be established in a disposable build before modifying the hardware.

## What a CSV does—and does not do

The bootloader reads the binary table from flash on every boot. A partition
CSV used while compiling an app does not repartition a device, and uploading
an app-only `.bin` does not contain or install a new table.

Changing the live layout requires replacing the 4 KiB sector at `0x8000` with
a generated binary table plus erased-byte (`0xff`) padding to fill the sector.
ESP-IDF's generator emits the 3 KiB table area; the final 1 KiB is reserved.
The table area includes an MD5 record. It cannot be
updated safely as a collection of ordinary partition writes because the table
itself is not an application/data partition and NOR flash must be erased before
some bit transitions.

There is no redundant partition-table sector in this layout. A loss of power
after erasing `0x8000` and before a verified rewrite can leave the bootloader
with no usable application map. Neither the bridge nor Tasmota can recover
from that state; isolated serial recovery would be required.

## Required gates before any writer exists

1. Run a diagnostic app from the bridge and read the complete `0x8000..0x8fff`
   sector plus the two `otadata` sectors. Save their SHA-256 values and raw
   bytes off-device.
2. Parse the captured table and require exact agreement on every entry,
   including flags and the table MD5. Do not rely only on partition labels.
3. Prove the running app is `ota_1` at `0x210000`, its image is valid, and its
   actual length is at most `0x170000`.
4. Prove a valid recovery bridge image begins in `ota_0` at `0x020000`; compare
   its application digest with the retained bridge artifact.
5. Build Tasmota against the candidate table and test filesystem discovery,
   formatting, write/read, restart persistence, relay, button, and metering on
   recoverable hardware or an equivalent ESP32-C3 setup.
6. Keep mains stable and have an isolated serial recovery method available for
   the real partition-table write.

## Proposed migration sequence

The eventual migration image should be a special bridge build, not a Tasmota
console command. It should expose separate **inspect** and **commit** stages.

Inspect must report the raw table, decoded entries, table MD5, running and boot
partitions, both app-image headers/digests, security state, flash size, and all
gate results. Commit must remain disabled unless every expected byte and digest
matches an explicit allow-list.

On an authorized commit it should:

1. select `ota_1` as the next boot target and verify the resulting `otadata`;
2. prepare the complete 4 KiB replacement table sector in RAM;
3. validate the candidate table in RAM, including bounds, alignment, overlaps,
   required partitions, app containment, and MD5;
4. erase the single sector at `0x8000`, write the complete replacement, read it
   back, and compare every byte;
5. reboot only after successful verification.

The new filesystem region begins at `0x380000`, beyond the end of the installed
Tasmota image. It should be erased/formatted only after the new table has booted
successfully. Do not erase it before the table migration.

## Rejected layouts

- Moving either app start address during the same operation: after reboot the
  table would point at an address where no valid image was staged.
- Replacing the two slots with one large factory app: it removes the recovery
  bridge and changes the boot-selection model.
- Using the Tasmota build CSV as-is: it describes a different layout and cannot
  be installed by an app-only OTA.
- Shrinking `ota_0`: it gains little and reduces headroom for the recovery app.
- Treating the table update as atomic: the S60 has only one table sector.

## Decision for Part 2

The candidate layout is internally consistent and preserves both installed app
offsets. It is suitable for offline build testing and for an inspect-only
diagnostic bridge. It is **not yet approved for device write** because the raw
live table, `otadata`, active-slot identity, and filesystem behavior have not
all been captured and verified.
