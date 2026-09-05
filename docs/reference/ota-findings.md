# Stock 1.2.0 OTA findings

## Authenticated manifest

The owner-authorized `/v2/device/ota/query` call for firmware family
`SN-ESP32C3-S60-01` and installed version 1.1.1 returns one update:

- Target version: 1.2.0
- Files: `user1.bin` and `user2.bin`
- Each file has a SHA-256 digest.
- Each URL has the form
  `http://<manifest-ip>:8088/ota/rom/<opaque-32-chars>/userN.1024.new.2.bin`.

The full URLs, digests and device identifier are kept only in the private,
git-ignored `captures/` directory.

## Download behavior

A normal HTTP GET to the exact manifest IP, port and path receives an empty
`400 Bad Request`; no binary data is returned and no partial artifact remains.
This suggests at least one of:

- the download needs a device-specific HTTP header;
- the link must first be activated by an `upgrade` command;
- the IP requires a virtual-host `Host` value not supplied in the manifest;
- the server intentionally accepts only the embedded client's request shape.

Public historical examples use the same `/ota/rom/<opaque>/...` layout. Modern
examples also mention `eu-otadl.coolkit.cc`, which resolves to a different
CoolKit OTA server. Because the opaque path may act as a bearer token, it must
not be replayed to an alternate host without explicit owner authorization.

Cross-host one-byte tests were explicitly authorized. HTTPS failed certificate
verification before sending the path because the CoolKit endpoint uses a key
rejected as too weak. Plain HTTP to `eu-otadl.coolkit.cc:8088` returned the same
empty 400; port 80 returned 404. Simple virtual-host substitution is therefore
not sufficient.

## Owner-authorized local URL probe

An explicitly authorized `upgrade` command was sent through the authenticated
eWeLink WebSocket using two private-LAN probe URLs. The local server contained
no firmware, returned `404` with `Content-Length: 0` for every request, and the
command supplied deliberately invalid all-zero SHA-256 digests.

The S60 accepted the command far enough to enter OTA mode and made two identical
requests for the first file:

- Method and path: `GET /probe-user1.bin`
- Range: `bytes=0-23`
- User agent: `itead-device`
- Connection: `Keep-Alive`

It did not request the second file. The server served zero firmware bytes. The
cloud stream reported OTA start and then the device temporarily offline. After
the test the S60 continued answering ICMP at its existing LAN address with no
packet loss. Its blue LED initially flashed three times, the documented
firmware-upgrade state, before becoming solid blue again; the device then
returned online in the eWeLink app without a power cycle. No successful update
occurred; Device Settings subsequently confirmed that the installed version
remained 1.1.1.

This proves that an owner-authenticated command can direct the stock S60 updater
to an arbitrary private-LAN HTTP URL. It does not yet prove what the first 24
bytes must contain, whether the supplied digest is enforced, whether an embedded
signature is checked, or whether the updater will accept a third-party ESP32-C3
application.

## Exact request-shape replay

The exact request shape observed from the S60 was replayed from the workstation
to the manifest host and opaque path: `Range: bytes=0-23`, user agent
`itead-device`, the manifest Host value, and a keep-alive connection. This was
tested both with the saved metadata and immediately after obtaining a fresh
owner-authenticated manifest. No device or upgrade command was sent for either
replay.

The server again returned an empty `400 Bad Request` with `Content-Length: 0`.
Missing ordinary HTTP headers and simple manifest expiry therefore do not
explain the protected download. The remaining leading explanations are
command-time activation of the opaque URL or device/server-side authorization
not visible in the HTTP headers captured on the private LAN.

## Blocked command-time activation test

The S60's source address was temporarily blocked from the manifest endpoint
`52.57.99.135:8088` at the router. Before the OTA experiment, the rule was
verified by assigning the S60 address to the workstation: that source was
rejected while the workstation's normal source address could still reach the
endpoint. The temporary address was then removed before reconnecting the S60.

With that guard active, a fresh owner-authenticated 1.2.0 manifest was obtained.
The exact 24-byte request returned an empty HTTP 400 before the command. A
genuine upgrade command containing the official URLs and digests was then sent,
and the workstation repeated the same request 22 times over 12 seconds. Every
post-command request also returned an empty HTTP 400; no firmware byte was
stored. The cloud stream reported OTA start and then the device offline, but the
S60 remained reachable at `192.168.1.96` with its expected MAC address and zero
ICMP packet loss, but it stayed offline in the eWeLink app. A power cycle was
required for it to reconnect to eWeLink. Device Settings then confirmed that it
was still running firmware 1.1.1.

This rules out global URL activation caused by the cloud command alone. It does
not rule out authorization tied to the device's source address or an activation
step performed only when the firmware server receives the device's own initial
request.

## Implications for a Tasmota first flash

The legacy DIY Mode route is not available on the tested S60: it did not expose
the unencrypted `/zeroconf/ota_unlock` and `/zeroconf/ota_flash` API. The legacy
DIY protocol's documented 508 KiB limit belongs to that ESP8266-oriented path
and does not establish a size limit for the S60's distinct cloud OTA mechanism.

The S60 cloud OTA path is real and accepts owner-supplied private-LAN URLs, so it
is inaccurate to say the device has no OTA-flash path at all. ESP-IDF OTA normally
writes an inactive application partition and reuses the installed bootloader and
partition table. Reusing them is a compatibility constraint, not proof that a
third-party app can never boot.

Current official Tasmota artifacts do not fit unchanged: the stock app-slot span
appears to be `0x1f0000` (2,031,616 bytes), while the current official
`tasmota32c3.bin` is 2,180,992 bytes, 149,376 bytes too large. The corresponding
factory image is 3,098,496 bytes and is intended for serial flashing at offset
zero. A smaller custom app could remove the size objection, but image validation,
signature policy, bootloader compatibility, rollback behavior, and required
partition data remain unproven blockers.

## Vendor firmware header — 24 bytes captured (2026-09-01)

The device's actual HTTP request to the vendor server was intercepted using a
router-level DNAT rule and a fail-closed relay proxy running on the workstation.

### Interception method

The S60 at `192.168.1.96` connects to `52.57.99.135:8088` to download firmware.
A PREROUTING DNAT rule on the UniFi Cloud Gateway Ultra redirected those packets
to `192.168.1.141:8088` (workstation relay). A POSTROUTING MASQUERADE rule was
also required to avoid asymmetric routing — without it the S60 would receive
SYN-ACK from the workstation's LAN address rather than from the vendor server,
causing an immediate TCP RST.

UniFi's graphical "Destination NAT" policy does not apply to LAN-originated
traffic (it operates on the WAN PREROUTING chain only). The rules must be added
directly via SSH to the gateway:

```
iptables -t nat -A PREROUTING \
  -s 192.168.1.96 -d 52.57.99.135 -p tcp --dport 8088 \
  -j DNAT --to-destination 192.168.1.141:8088

iptables -t nat -A POSTROUTING \
  -s 192.168.1.96 -d 192.168.1.141 -p tcp --dport 8088 \
  -j MASQUERADE
```

SSH is disabled by default on the UCG-Ultra and must be enabled via the UniFi OS
console at `https://<gateway-ip>` (System → SSH), not via the Network application.

### Device request shape (confirmed)

The device does not use the bare manifest URL. It appends its own filename and
three query parameters:

```
GET /ota/rom/<opaque>/user1.1024.new.2.bin?deviceid=<id>&ts=<unix-seconds>&sign=<sha256-hmac>
Range: bytes=0-23
User-Agent: itead-device
Host: 52.57.99.135:8088
Connection: Keep-Alive
```

The `ts` field is the current Unix timestamp in seconds. The `sign` field is a
fresh HMAC-SHA256 (key unknown) recomputed on every retry, approximately every
2.5 seconds. **This is why all workstation replays received an empty 400 Bad
Request: the timestamp embedded in the signed URL was stale.** The vendor server
validates the signature and rejects requests with an expired or incorrect token.
The device's own freshly-signed request is accepted.

The file extension in the actual device request (`user1.1024.new.2.bin`) differs
from the bare filename in the eWeLink manifest response (`user1.bin`). The device
constructs the full URL itself from the opaque path prefix.

### Captured header

The relay forwarded the first valid signed request and captured 24 bytes of the
vendor server's response body (HTTP 206 Partial Content, Range: bytes=0-23):

```
offset  hex                                      ascii
00      03 01 31 2e 32 2e 30 00  00 00 00 00     ..1.2.0.....
0c      00 00 00 00 00 00 00 00  3c 76 cc 65     ........<v.e
```

Interpretation:

| Offset | Size | Value | Meaning |
|--------|------|-------|---------|
| 0 | 1 | `0x03` | Sonoff/Itead proprietary OTA format magic |
| 1 | 1 | `0x01` | Format sub-version |
| 2 | 6 | `31 2e 32 2e 30 00` | "1.2.0\0" — firmware version, null-terminated |
| 8 | 12 | `00 × 12` | Reserved / padding |
| 20 | 4 | `3C 76 CC 65` | Big-endian CRC-32 of bytes `0..19` |

The standard ESP32 application image magic byte (`0xE9`) does not appear in the
first 24 bytes. The complete capture later established that the raw ESP32-C3
image begins at byte 100. The HMAC key used for URL signing and whether the
device validates the SHA-256 digest supplied in the manifest before writing
flash remain unconfirmed.

## Complete vendor image structure

The relayed `user1` response is 1,456,404 bytes. Its SHA-256 exactly matches the
digest supplied by the authenticated eWeLink manifest. Offline parsing with
`tools/analyze_vendor_ota.py` established:

- bytes `0..99` are a Sonoff wrapper;
- the wrapper declares a 1,456,304-byte payload at offset `0x4c`, in big-endian
  form;
- wrapper offset `0x14` contains the big-endian standard CRC-32 of the first
  20 wrapper bytes (`3c76cc65`);
- wrapper offset `0x50` contains the big-endian standard CRC-32 of the complete
  payload (`1c94adbe`);
- wrapper offset `0x54` contains the big-endian standard CRC-32 of metadata
  record bytes `0..59`, which are wrapper bytes `0x18..0x53` (`10fa05c5`);
- byte 100 is the start of a normal six-segment ESP32-C3 application image;
- the native ESP image XOR checksum is valid;
- the native appended ESP image SHA-256 is valid; and
- the parsed ESP image consumes the payload exactly, leaving no trailing
  vendor-signature bytes.

The complete 100-byte wrapper has this layout:

| Wrapper offset | Size | Meaning |
|---|---:|---|
| `0x00` | 1 | format version (`3`) |
| `0x01` | 1 | metadata record count (`1`) |
| `0x02` | 8 | null-terminated target version |
| `0x0a` | 10 | reserved zeros |
| `0x14` | 4 | big-endian CRC-32 of wrapper bytes `0x00..0x13` |
| `0x18` | 32 | null-terminated firmware model |
| `0x38` | 16 | null-terminated target version |
| `0x48` | 4 | big-endian payload offset (`100`) |
| `0x4c` | 4 | big-endian payload size |
| `0x50` | 4 | big-endian CRC-32 of payload |
| `0x54` | 4 | big-endian CRC-32 of wrapper bytes `0x18..0x53` |
| `0x58` | 12 | reserved zeros |

`tools/build_vendor_ota.py` recreates this structure locally and validates the
native ESP32-C3 image before writing output. Rebuilding the genuine 1.2.0 file
from its payload, model and version produces the authentic 100 wrapper bytes
exactly.

The captured file itself remains private under `captures/`.

### Implications

Workstation-side replay of the manifest URL was always rejected because the
request lacked a valid per-request HMAC signature. Device-side traffic
interception is the only practical method to observe the vendor server's response
without knowing the signing key.

The proprietary header and digest checks have since been resolved as described
below. Router DNAT is not required for a substitute image: the earlier safe
probe proved that the authenticated command can supply a private-LAN HTTP URL
directly.

## Stock 1.2.0 validation chain

The genuine 1.2.0 application was reconstructed as an ELF and its OTA path was
traced to the ESP-IDF calls. The stock updater performs this sequence:

1. Parse the 24-byte frame header and verify its CRC-32.
2. Parse the 76-byte metadata record and verify its CRC-32, model and version
   fields.
3. Select the inactive application partition with
   `esp_ota_get_next_update_partition`.
4. Stream the payload through `esp_ota_begin` and `esp_ota_write` while
   accumulating the declared payload CRC-32.
5. Compare the completed payload CRC and call `esp_ota_end`, which verifies the
   native ESP image structure, XOR checksum, appended SHA-256 and chip/security
   constraints.
6. Compute SHA-256 over the complete wrapped file and compare it to `digest`
   from the authenticated upgrade command.
7. Only after all checks succeed, select the written partition with
   `esp_ota_set_boot_partition`.

The command's `digest` field is owner-controlled; no embedded or trailing
vendor signature is present, and no separate public-key verification occurs in
this path. The authentic wrapper can be reconstructed byte-for-byte from its
payload using `tools/build_vendor_ota.py`.

## Rollback result

Stock 1.2.0 uses ESP-IDF v4.4.2 and was compiled without
`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`. The compiled `esp_ota_begin` lacks the
pending-verification guard, and the rollback state/messages compiled by that
option are absent. A third-party application that never reaches its own code
therefore remains selected; the stock bootloader will not automatically return
to the previous slot.

This invalidates the earlier assumption that the untouched slot alone was a
sufficient failsafe. `RECOVERY_LOCK` remains active.

## One-shot recovery bridge

`bridge/` contains a small ESP-IDF 5.3.1 ESP32-C3 application designed for the
first non-vendor OTA. Its recovery invariant is:

1. At the beginning of `app_main`, discover the running and peer application
   partitions and select the peer (untouched stock) for the next boot.
2. Start a WPA2 SoftAP and a local browser upload page only after that selection
   succeeds. Any later crash or power cycle therefore returns to stock.
3. Immediately before erasing the peer for a browser upload, select the running
   bridge again. An interrupted second-stage upload reboots into the bridge.
4. Select the uploaded image only after `esp_ota_end` validates it.

The reviewed native bridge v3 is 858,208 bytes and its SHA-256 is
`7d41da7e04dbd6f81b6edcff8a47d565d6df077bdaf8d8d68873a5b2f424bf40`.
The Sonoff-wrapped v3 image is 858,308 bytes with whole-file SHA-256
`10d79d33856bb842b26f0a1b6748751c091ec9738a72dd4de2033fbd0c329ff7`.
Offline analysis confirms:

- ESP32-C3 chip ID 5 and five loadable segments;
- valid native XOR checksum and appended ESP image SHA-256;
- no unexplained trailing bytes;
- all wrapper/header/payload CRC-32 values valid; and
- payload size safely below the `0x1f0000` stock slot limit.

Disassembly of the built ELF confirms that `app_main` calls
`esp_ota_get_running_partition`, `esp_ota_get_next_update_partition`, and
`esp_ota_set_boot_partition(peer)` before either the access-point or web-server
startup functions.

The bridge cannot protect against a bootloader rejection or a failure before
`app_main` begins. Wrapped bridge v3 was successfully installed directly from
stock 1.1.1 during a second clean hardware run. The reviewed trial Tasmota then
selected the retained bridge as its next-boot fallback before the final image
was installed and verified.
