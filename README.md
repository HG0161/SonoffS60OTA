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

## Part 2: WIP NOT TESTED exact Safeboot-layout migration

A private complete 4 MiB post-conversion dump has now supplied the actual S60
partition sector. Its table MD5 is valid and the complete sector SHA-256 is
allow-listed. The confirmed entries, boundaries and hash are recorded in
[the actual partition map](docs/s60-actual-partition-map.md). Never publish the
complete dump or NVS captures: they may contain Wi-Fi credentials and device
keys.

The selected target is no longer the earlier preserve-in-place candidate. It
is an exact byte-for-byte copy of the partition sector embedded in one frozen
official `tasmota32c3.factory.bin`: 20 KiB NVS, canonical `otadata`, Safeboot,
2,880 KiB `app0`, and 320 KiB SPIFFS.

The migration is implemented as resumable `preflight`, `stage`, and separately
armed `commit` phases. It pins and hashes every image, requires Bluetooth to be
running from high old `ota_1`, validates the retained NVS, independently
captures staged Safeboot read-back bytes, and keeps the destructive table
writer behind `REPARTITION_LOCK`. The scripts and Berry source compile and pass
offline tests, but the commit has **not** yet been trialled on this S60
hardware. Power loss during its one non-redundant table-sector replacement may
still require physical recovery.

Start or resume with:

```sh
python3 tools/safeboot_migration_status.py
```

Read the [guarded migration plan](docs/part-2-safeboot-migration-plan.md) and
the complete [operator runbook](docs/part-2-safeboot-migration-runbook.md)
before starting a live phase. The older
[preserve-in-place proposal](docs/part-2-repartitioning.md) is retained only as
superseded design history and must not be flashed.

## License

Project code and modifications are distributed under GPL-3.0-only. The custom
Tasmota binaries remain subject to Tasmota's GPL-3.0-only license. See
[`LICENSE`](LICENSE) and [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
