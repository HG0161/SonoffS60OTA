# Sonoff S60 stock-to-Tasmota OTA research

This project investigates a **no-opening, no-UART** conversion from the stock
firmware of the Wi-Fi Sonoff S60 (ESP32-C3 / Coolkit SM-049) to Tasmota.

The target is specifically the Wi-Fi S60. The Zigbee S60 is different hardware
and is out of scope.

## Current status

**Successfully reproduced on two devices.** Two UK S60TPG plugs were converted
from stock firmware 1.1.1 to custom Tasmota 15.6.0 entirely through OTA. The
second clean run installed wrapped bridge v3 directly from stock. Relay,
button, LED, CSE7766 energy metering, and normal reboot were verified. The
active app slot contains final Tasmota and the inactive slot contains recovery
bridge v3.

Start with the [beginner's guide](docs/BEGINNER-GUIDE-S60TPG-OTA.md), or use the
[complete technical procedure](docs/HOWTO-S60TPG-OTA-TASMOTA.md). Sanitized
device details are in [docs/device-baseline.md](docs/device-baseline.md), and
the decoded updater and wrapper are documented in
[docs/ota-findings.md](docs/ota-findings.md).

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

## Safety and scope

Only test devices you own or have permission to test. The tools here do not
require opening a mains-powered plug. Do not connect a normal USB-UART adapter
to an S60 while the S60 is connected to mains: the low-voltage electronics are
not safely isolated from mains.

The basic LAN probe performs information requests only. It does not send an
upgrade command, switch the relay, change Wi-Fi settings, or contact eWeLink.
The separate experimental OTA-command and live sender tools are state-changing.
Every sender checks `RECOVERY_LOCK` before asking for credentials or serving a
firmware body.

## Quick start

Create a Python virtual environment if desired, then install the one external
Python dependency:

```sh
python3 -m pip install -r requirements.txt
```

Run the unit tests:

```sh
python3 -m unittest discover -s tests -v
```

Probe one S60 at a known IP address:

```sh
python3 tools/probe_s60.py 192.168.1.50 --output captures/probe.json
```

Discover its advertised eWeLink LAN service without a subnet scan:

```sh
python3 tools/discover_ewelink.py \
  --target 192.168.1.50 \
  --output captures/my-s60/mdns.json
```

For an encrypted stock device, retrieve the matching LAN key through an
owner-authenticated eWeLink session. Run this yourself in a local terminal so
the hidden password prompt is never passed through chat:

```sh
python3 tools/get_device_key.py \
  --mdns-capture captures/my-s60/mdns.json \
  --output captures/my-s60/device-key.json
```

The password and cloud token are not stored. The device key is written with
mode `0600` under the git-ignored `captures/` directory.

Validate encrypted, read-only LAN communication:

```sh
python3 tools/lan_get_state.py 192.168.1.50 \
  --key-file captures/my-s60/device-key.json \
  --output captures/my-s60/lan-state.json
```

Query the owner-authorized vendor OTA metadata without issuing an upgrade:

```sh
python3 tools/query_ota.py \
  --mdns-capture captures/my-s60/mdns.json \
  --output captures/my-s60/ota-metadata.json
```

Before any live OTA, add the source-specific DNAT and MASQUERADE rules from the
complete guide and check them on the router with `iptables -t nat -C`. The
Python unit suite does **not** inspect live router rules. For a behavioural
preflight, stop the OTA server, unplug the S60 so its address is free, then run:

```sh
sudo ./tools/test_ota_firewall.sh \
  INTERFACE PLUG_IP WORKSTATION_IP VENDOR_OTA_IP 8088
```

Continue only if it prints both interception `PASS` messages and confirms that
the temporary address was removed. Substitute every placeholder; the script
has no built-in network addresses. See the
[beginner's guide](docs/BEGINNER-GUIDE-S60TPG-OTA.md#part-4-temporarily-redirect-the-plugs-update-download)
for the complete sequence and limitations.

Download the genuine artifacts without triggering the device update, requiring
the SHA-256 values supplied by the authenticated manifest:

```sh
python3 tools/download_ota.py
```

Analyze a full 4 MB stock flash dump obtained from a sacrificial/recoverable
test unit:

```sh
python3 tools/analyze_dump.py flash_dump.bin --output captures/dump-report.json
```

The analyzer parses the ESP-IDF partition table and searches readable firmware
strings for OTA URLs, endpoints, certificates, digest checks, version checks,
and signature-related code clues. It does not upload the dump anywhere.

## Research gates

An OTA flasher will only be implemented after these gates are answered:

1. **Fingerprint:** exact S60 model, hardware revision and stock firmware.
2. **Reachability:** local DIY/LAN OTA endpoint, authenticated cloud command,
   or another owner-controlled trigger.
3. **Validation (answered):** wrapper CRCs, payload CRC, owner-supplied whole-file
   SHA-256, model/version metadata, and native ESP image verification; no vendor
   signature was found.
4. **Layout (answered):** two `0x1f0000` application slots; an OTA payload must
   be at most 2,031,616 bytes.
5. **Recovery (answered at application level):** bridge v3 confirms itself,
   pins itself before erasing its peer, and selects an uploaded app only after
   validation. Failure before `app_main` remains outside software recovery.
6. **Payload (answered):** reduced trial and final Tasmota 15.6.0 images fit the
   stock slots and were verified on the S60TPG, including CSE7766 metering.

See [docs/research-plan.md](docs/research-plan.md) for the working hypothesis
and experiment order.

## License

Project code and modifications are distributed under GPL-3.0-only. The custom
Tasmota binaries remain subject to Tasmota's GPL-3.0-only license. See
[`LICENSE`](LICENSE) and [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
