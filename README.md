# Sonoff S60 stock-to-Tasmota OTA

This project provides a tested **no-opening, no-UART** conversion from the stock
firmware of the Wi-Fi Sonoff S60 (ESP32-C3 / Coolkit SM-049) to Tasmota using
only Over The Air (OTA)

## Current status

**Successfully completed on two devices.** The first UK S60TPG entered the
conversion on stock firmware 1.2.0 after i accidentally update to 1.2 during 
development. It also received wrapped bridge v2. The second entered on stock 
1.1.1 and installed wrapped bridge v3 directly. Both completed
the trial and final custom Tasmota 15.6.0 stages entirely through OTA. Relay,
button, LED, CSE7766 energy metering, and normal reboot were verified. The
active app slot contains final Tasmota and the inactive slot contains recovery
bridge v3.

Start with the [guide](docs/GUIDE.md). The
[AI-generated guide](docs/AI-GENERATED-GUIDE.md) and
[complete technical procedure](docs/HOWTO-S60TPG-OTA-TASMOTA.md) provide more
background. Sanitized device details are in
[docs/device-baseline.md](docs/device-baseline.md), and the decoded updater and
wrapper are documented in [docs/ota-findings.md](docs/ota-findings.md).

The four reviewed firmware images are included in `artifacts/`. Verify them
before use with:

```sh
sha256sum -c SHA256SUMS
```

What is known:

- The device has a 4 MB ESP32-C3 flash.
- Reported stock firmware uses two OTA app slots at `0x20000` and `0x210000`.
- Tested hardware has Secure Boot and flash encryption disabled.
- An owner-authenticated eWeLink command can direct the stock updater to a
  private-LAN HTTP URL.
- The 100-byte Sonoff wrapper, all three CRC-32 fields, and the native ESP32-C3
  payload validation are decoded and reproducibly generated.
- The manifest digest is the SHA-256 of the complete wrapped file. It is
  enforced, but it is supplied by the authenticated owner command; the updater
  does not require a vendor signature.
- Stock 1.2.0 was built without ESP-IDF application rollback.
- A small one-shot OTA bridge has been implemented and hardware-tested as the
  direct first stage from stock 1.1.1. It selects the untouched stock slot
  before starting Wi-Fi, then provides an HTTP upload endpoint. The bridge and
  its wrapped image also pass offline validation.

The remaining irreducible first-boot risk is failure before the bridge reaches
`app_main`; no application can repair that without bootloader rollback. The
hardware trial passed this point, and bridge v3 now confirms itself before
setting its peer as the next-boot fallback.

## Community help wanted: confirm the actual partition map

We have a working map derived from the stock firmware, but we still need an
independent byte-for-byte capture of the partition-table sector from real S60
hardware. If you can safely obtain one, please open a GitHub issue or discussion
and include:

- the exact S60 model, regional suffix and hardware revision;
- the installed firmware version;
- whether the device is untouched stock, vendor-updated, or converted;
- the raw 4 KiB sector at flash offset `0x8000` (`0x8000..0x8fff`);
- the sector's SHA-256; and
- a decoded list of every partition entry, including type, subtype, offset,
  size and flags.

Please do **not** publish a complete flash dump: it may contain Wi-Fi
credentials, device keys and other private data. Do not connect ordinary
grounded serial equipment while the plug is connected to mains. A partition
sector captured earlier from an owned device is also useful.

The expected layout to compare against is documented in
[Part 2: partition-table migration](docs/part-2-repartitioning.md).

## Next steps: partition-table migration

The working conversion deliberately keeps the original Sonoff partition table.
The next phase is to replace it with a verified layout that preserves both
installed application offsets while adding a 512 KiB filesystem partition.

Before an actual partition-table write:

1. Build an inspect-only bridge that captures the live partition-table sector,
   OTA-selection data, active slot, application headers and hashes.
2. Require an exact match against the known stock layout and both installed
   images before enabling any write operation.
3. Build and test Tasmota against the candidate layout, including filesystem
   format, read/write persistence, restart, relay, button and energy metering.
4. Add a separate, explicitly authorized commit mode that writes the complete
   partition-table sector and verifies every byte after the write.
5. Perform the first migration only with stable power and an isolated serial
   recovery method available.
6. After a successful boot, format the new filesystem and repeat the complete
   hardware and OTA regression tests.

The proposed map, required checks and migration sequence are documented in
[Part 2: partition-table migration](docs/part-2-repartitioning.md). The current
[candidate CSV](repartition/partitions-preserve-installed-apps.csv) is for
offline validation only and must not yet be flashed directly.

## License

Project code and modifications are distributed under GPL-3.0-only. The custom
Tasmota binaries remain subject to Tasmota's GPL-3.0-only license. See
[`LICENSE`](LICENSE) and [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
