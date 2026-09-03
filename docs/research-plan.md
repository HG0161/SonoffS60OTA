# Research plan

## Objective

Convert an owner-controlled Wi-Fi Sonoff S60 from stock eWeLink firmware to
Tasmota entirely over the local network.

## Evidence so far

Community teardown work identifies the module as a Coolkit SM-049 containing an
ESP32-C3 and 4 MB flash. A factory boot log reportedly shows ESP-IDF 4.4.2 and
OTA application partitions at `0x20000` and `0x210000`. Hardware inspection with
`esptool get_security_info` reportedly found Secure Boot and flash encryption
disabled.

The eWeLink developer API documents an `upgrade` command containing a model,
version, one or more download URLs, a SHA-256 digest, and image names. Older
Sonoff DIY Mode firmware exposes a local `/zeroconf/ota_unlock` followed by
`/zeroconf/ota_flash`. The S60 is not advertised as a DIY Mode product, so this
project treats that route as a hypothesis rather than a feature.

## Hypotheses

### H1: S60 exposes the DIY Mode information and OTA endpoints

The non-mutating probe checks `/zeroconf/info` on common Sonoff LAN ports. If it
responds with `otaUnlock`, the next experiment is to document the exact firmware
version and test `ota_unlock` on a recoverable unit. An image upload remains a
separate, explicitly confirmed operation.

**Result on firmware 1.1.1:** the S60 advertises `_ewelink._tcp.local` on port
8081 with `encrypt=true`, but does not answer an unencrypted DIY-style info
request. The open DIY endpoint hypothesis is therefore not supported in normal
eWeLink mode. Continue through authenticated encrypted LAN control.

### H2: Vendor OTA accepts any image matching the supplied SHA-256

The public eWeLink command format shows an integrity digest but does not establish
whether the S60 also verifies an embedded signature or model/version metadata.
A stock dump and a genuine vendor update are needed to determine this.

The 508 KiB limit documented for the old Sonoff DIY Mode API does not apply by
evidence to this separate S60 cloud updater. However, the current official
Tasmota ESP32-C3 app image is 149,376 bytes larger than the apparent 2,031,616
byte stock OTA slot. Testing therefore requires a deliberately reduced custom
app even if all validation gates prove permissive.

### H3: Vendor OTA is signed or the command is cloud-authorized

If firmware verification uses an embedded public key, arbitrary stock-to-Tasmota
OTA is blocked unless there is a device-side implementation flaw. If only the
command is authenticated, an owner-authenticated eWeLink session might still be
able to supply a local image URL. We should prefer documented, owner-controlled
API use and avoid attempting to compromise vendor infrastructure.

**Partial result on firmware 1.1.1:** an owner-authenticated cloud `upgrade`
command successfully directed the S60 to an arbitrary private-LAN URL. The
device requested the first 24 bytes twice. This establishes command reachability
but says nothing yet about image signature or digest enforcement.

ESP-IDF OTA ordinarily reuses the existing bootloader and partition table. That
prevents using a whole-flash Tasmota factory image as the OTA payload, but does
not by itself prove that a compatible app-only image cannot boot. Compatibility
and vendor validation must be measured rather than assumed.

### H4: The updater enforces only version/model policy

If validation is limited to hashes, image headers, model and monotonically
increasing versions, a correctly packaged ESP-IDF OTA application may be enough.
The first payload should be a minimal diagnostic/recovery image, not Tasmota, so
partition selection and reboot behavior can be tested independently.

## Experiment order

1. Record the label model and eWeLink-reported firmware version.
2. Give the device a stable IP on an isolated IoT/test network.
3. Run `tools/probe_s60.py` and preserve its JSON output.
4. On one development unit, obtain and preserve a full 4 MB serial flash dump.
5. Run `tools/analyze_dump.py`; inspect partitions and OTA-related strings.
6. Capture one genuine eWeLink update, including DNS names, TLS destinations,
   response metadata and the downloaded binary where legally/technically
   available.
7. Compare the genuine OTA binary with the app partition from the before/after
   dumps to determine packaging and metadata.
8. Build a harmless image-size/validation experiment with a reliable serial
   recovery path.
9. Only after successful rollback testing, package the ESP32-C3 Tasmota payload.

## Required artifacts

- Exact product label/model and region (for example S60TPF or S60TPG).
- Stock firmware version before and after an offered vendor update.
- `probe_s60.py` output.
- Full 4 MB stock dump from an owned test unit.
- If possible, full 4 MB dump after a genuine vendor update.
- Genuine OTA response metadata and binary URL/file.

Keep dumps under `captures/`; this directory is git-ignored because a dump can
contain Wi-Fi credentials, device keys, tokens and other secrets.
