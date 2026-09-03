# Beginner guide: Sonoff S60TPG to Tasmota over Wi-Fi

This guide records the process successfully used on two Sonoff S60TPG plugs.
It installs Tasmota without opening the plug.

The S60 is a mains-voltage device. Only continue with a plug you own and can
afford to recover or replace. Keep its power stable while firmware is being
written. A failed first bridge boot could require physical serial recovery.

## 1. Add the plug to eWeLink

1. Plug in the S60TPG.
2. Install the eWeLink Smart Home app.
3. Sign up or use a disposable eWeLink account.
4. Add the plug and connect it to the same 2.4 GHz network as your workstation.
5. Give it any name and room; the name does not affect this process.
6. Open the plug's device settings and enable **LAN Control**.
7. Find its IP address in your router and reserve that address for the plug.
8. Note the workstation's LAN IP address as well.

Write down your values:

```text
PLUG_IP: __________________
WORKSTATION_IP: ___________
STOCK_VERSION: ____________
NETWORK_INTERFACE: ________
WIFI_INTERFACE: ___________
```

The examples below use capitalized placeholders. Replace each placeholder with
your own value. Do not type the placeholder literally.

Open a terminal in the project directory:

```sh
cd "/path/to/Tasmatized Sonoff s60"
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -v
```

## 2. Discover the correct plug

The mDNS discovery identifies the plug's IP, device ID, local port and
encryption mode:

```sh
python3 tools/discover_ewelink.py \
  --timeout 8 \
  --target PLUG_IP \
  --output captures/my-s60/mdns.json
```

The output file belongs to this particular plug. Later commands use it as an
input so that another S60 on the same eWeLink account is not selected by
mistake.

## 3. Probe the stock firmware

This read-only probe records whether common HTTP and DIY-mode endpoints answer:

```sh
python3 tools/probe_s60.py PLUG_IP \
  --output captures/my-s60/probe.json
```

On the tested stock firmware, this confirmed:

- port 80 refused connections;
- port 8081 did not answer unencrypted requests;
- no open `/zeroconf/info` DIY endpoint was available; and
- the probe sent no state-changing requests.

`probe.json` is a separate diagnostic report. It does not replace `mdns.json`.

## 4. Verify the supplied firmware files

Check that every supplied firmware file is complete and unmodified:

```sh
sha256sum -c SHA256SUMS
```

All four results must say `OK`:

```text
artifacts/s60-ota-bridge-v3-1.2.1.ota: OK
artifacts/s60-ota-bridge-v3-idf5.3.1.bin: OK
artifacts/s60-tasmota-15.6.0-trial-cse7766.bin: OK
artifacts/s60-tasmota-15.6.0-final-cse7766.bin: OK
```

## 5. Retrieve the plug's LAN key

The LAN key authenticates and decrypts local eWeLink messages. In this
procedure it is used for the read-only LAN communication check; the firmware
version comes from the OTA metadata query in the next section.

```sh
python3 tools/get_device_key.py \
  --mdns-capture captures/my-s60/mdns.json \
  --output captures/my-s60/device-key.json
```

Enter the eWeLink email and password when prompted. The password is hidden and
is not saved. A disposable account can be used.

## 6. Query the official OTA metadata

This retrieves:

- the firmware model or family;
- the currently installed version;
- the offered update version;
- firmware filenames and private download URLs;
- expected SHA-256 hashes; and
- the vendor OTA host and port needed for the router rule.

It does not install the update.

```sh
python3 tools/query_ota.py \
  --mdns-capture captures/my-s60/mdns.json \
  --output captures/my-s60/ota-metadata.json
```

Example terminal output:

```text
Saved OTA metadata to captures/my-s60/ota-metadata.json with mode 0600.
Current device firmware: SN-ESP32C3-S60-01 version 1.1.1
Available OTA records: 1
Version: 1.2.0 | files: 2
Router interception destination(s):
  Vendor OTA host: 52.57.99.135 | port: 8088 | protocol: TCP
No upgrade command was sent.
```

Record the printed `VENDOR_OTA_IP` and `VENDOR_OTA_PORT`. These can change, so
use the values from your own run. Keep the JSON file private because it
contains device identifiers and signed URLs.

## 7. Verify encrypted LAN communication

`--key-file` is the file created by `get_device_key.py`:

```sh
python3 tools/lan_get_state.py PLUG_IP \
  --key-file captures/my-s60/device-key.json \
  --output captures/my-s60/lan-state.json
```

Expected output on tested stock firmware 1.1.1:

```text
LAN connection: PASS (HTTP 200 OK)
Read-only state query: NOT SUPPORTED (eWeLink error 400 is expected on tested S60 stock firmware)
Saved private response to captures/my-s60/lan-state.json
```

The error 400 means this optional state request is unsupported. HTTP 200 still
confirms that the encrypted LAN connection reached the plug.

The model, current firmware and offered firmware are reported by
`query_ota.py`, not by this LAN-state response.

## 8. Inspect the wrapped recovery bridge

This validates the Sonoff wrapper, its CRC checksums, the embedded ESP32-C3
application, its checksum and SHA-256, its chip type and its size boundaries:

```sh
python3 tools/analyze_vendor_ota.py \
  artifacts/s60-ota-bridge-v3-1.2.1.ota
```

There is a long JSON report. The important block is at the end:

```text
Validation checks:
  PASS  Sonoff wrapper magic
  PASS  Header reserved bytes
  PASS  Header CRC-32
  PASS  Payload offset
  PASS  Payload size
  PASS  Payload CRC-32
  PASS  Metadata CRC-32
  PASS  Metadata reserved bytes
  PASS  ESP32-C3 target
  PASS  ESP image checksum
  PASS  ESP image SHA-256
  PASS  No trailing payload bytes
  OVERALL VALIDATION: PASS
```

Do not continue unless the overall result is `PASS`.

## 9. Temporarily intercept the router traffic

Normally the connection follows this path:

```text
S60 -> router -> eWeLink OTA server
```

For the conversion it must follow this path:

```text
S60 -> router -> your workstation
```

Add a destination-NAT or DNAT rule using the following settings:

```text
Incoming interface:  LAN
Protocol:            TCP
Source IP:           PLUG_IP
Destination IP:      VENDOR_OTA_IP
Destination port:    VENDOR_OTA_PORT
Redirect to IP:      WORKSTATION_IP
Redirect to port:    VENDOR_OTA_PORT
```

This redirects only the plug's OTA connection. A normal WAN port-forward does
not work because this connection begins inside the LAN.

The plug and workstation are also on the same LAN, so the workstation might
otherwise reply directly from its own address. The plug expects the reply to
come from the vendor server. Masquerade or SNAT forces the reply through the
router, which can reverse the address translation.

Some routers handle this automatically under names such as NAT reflection,
hairpin NAT, masquerade or automatic source NAT. If yours does not, add a
separate rule using these settings:

```text
Protocol:          TCP
Source IP:         PLUG_IP
Destination IP:    WORKSTATION_IP
Destination port:  VENDOR_OTA_PORT
Translation:       Masquerade / router LAN address
```

Restrict both settings to the exact plug, workstation, vendor address and port.
Keep them temporary.

### Test the interception

Unplug the new S60 so its reserved IP address is temporarily unused. Find the
workstation network-interface name with `ip link`, then run:

```sh
sudo ./tools/test_ota_firewall.sh \
  NETWORK_INTERFACE PLUG_IP WORKSTATION_IP VENDOR_OTA_IP VENDOR_OTA_PORT
```

Continue only if all three checks pass:

```text
Test 1/3: check that plug-source traffic is intercepted
PASS: the plug-source request was redirected away from the vendor endpoint.
Test 2/3: check that normal workstation traffic is not intercepted
PASS: normal workstation traffic was not intercepted.
Test 3/3: remove the temporary plug address
PASS: the temporary test address was removed.
OVERALL INTERCEPTION PREFLIGHT: PASS
Reconnect the S60 when ready.
```

Reconnect the plug and confirm that it returns at `PLUG_IP` before continuing.

## 10. Install the recovery bridge through stock OTA

This is the first firmware-writing step. A failed first bridge boot could
require physical serial recovery.

The sender:

- checks that you have deliberately moved the safety lock;
- validates the bridge wrapper, CRCs, ESP image, hash and size;
- signs in to eWeLink locally;
- uses `mdns.json` to select the correct device;
- confirms the exact installed firmware version;
- starts a private HTTP server on the vendor OTA port;
- sends the authenticated upgrade command; and
- confirms that the plug downloaded every byte.

Only after checking every value and accepting the recovery risk, move the lock:

```sh
mv RECOVERY_LOCK RECOVERY_LOCK.owner-authorized
```

Now run the sender. `STOCK_VERSION` must exactly match the version printed by
`query_ota.py`. Only stock version 1.1.1 has been hardware-tested so far.

```sh
python3 tools/serve_tasmota_ota.py \
  --listen-ip WORKSTATION_IP \
  --listen-port VENDOR_OTA_PORT \
  --device-ip PLUG_IP \
  --firmware artifacts/s60-ota-bridge-v3-1.2.1.ota \
  --email YOUR_EWELINK_EMAIL \
  --expected-current-version STOCK_VERSION \
  --mdns-capture captures/my-s60/mdns.json \
  --i-understand-stock-has-no-automatic-rollback
```

Enter the eWeLink password at the hidden prompt. A successful transfer ends
with output similar to this sanitized example:

```text
Firmware: artifacts/s60-ota-bridge-v3-1.2.1.ota (858,308 bytes)
Device: S60TPG (SN-ESP32C3-S60-01, stock 1.1.1)
Serving only PLUG_IP at http://WORKSTATION_IP:VENDOR_OTA_PORT/user1.bin and /user2.bin
SHA-256 (complete wrapped file): 10d79d33856bb842b26f0a1b6748751c091ec9738a72dd4de2033fbd0c329ff7
  [PLUG_IP] "GET /user1.bin HTTP/1.1" 206 -
  Served /user1.bin bytes 850,020-858,307 (8,288 bytes)
  Complete firmware byte coverage observed; allowing verification time.
```

Do not publish the real device ID, signed query parameters or complete private
URLs from your terminal output.

## 11. Upload trial Tasmota through the bridge

When the bridge starts, connect the workstation's Wi-Fi to:

```text
Access point: S60-OTA-Bridge-XXXX
Password:     s60-ota-bridge
Page:         http://192.168.4.1/
```

The browser upload button has been unreliable in testing. Use the terminal
command below. Replace `WIFI_INTERFACE` with the workstation's Wi-Fi interface,
which you can find using `ip link`:

```sh
curl --interface WIFI_INTERFACE --http1.1 \
  --silent --show-error \
  --connect-timeout 5 --max-time 180 \
  -H 'Expect:' \
  -H 'Content-Type: application/octet-stream' \
  --data-binary '@artifacts/s60-tasmota-15.6.0-trial-cse7766.bin' \
  --write-out $'\nHTTP %{http_code}; uploaded %{size_upload} bytes; exit %{exitcode}\n' \
  http://192.168.4.1/update
```

Do not continue unless it reports:

```text
Upload verified. Rebooting into the new application...
HTTP 200; uploaded 1299488 bytes; exit 0
```

## 12. Configure and test trial Tasmota

The bridge should reboot and the workstation's Wi-Fi should disconnect. Join:

```text
Access point: tasmota-XXXXXXXX
Password:     s60-tasmota
Page:         http://192.168.4.1/
```

Select the normal 2.4 GHz Wi-Fi network, enter its password and save. Reconnect
the workstation to the normal LAN and open `http://PLUG_IP/`.

Trial Tasmota can perform an automatic first-start restart. If the Tasmota
access point does not appear and `S60-OTA-Bridge-XXXX` returns, do not upload
the trial again. Power-cycle the plug once; the bridge has already selected
the intact trial image for the next boot.

The Tasmota page should show the Sonoff S60TPG profile and the CSE7766 energy
fields.

Test all of the following:

1. Click **Toggle** and confirm that the relay and LED respond.
2. Press the physical button and confirm that it toggles the relay.
3. Connect only a known-safe load.
4. Turn the relay on and wait at least five seconds.
5. Confirm that voltage, current and power readings appear.

Do not click **Restart** until those checks pass. Restarting the trial
intentionally returns to the recovery bridge.

## 13. Install final Tasmota

Click **Restart** on the trial Tasmota page. Reconnect to
`S60-OTA-Bridge-XXXX`, then upload the final image:

```sh
curl --interface WIFI_INTERFACE --http1.1 \
  --silent --show-error \
  --connect-timeout 5 --max-time 180 \
  -H 'Expect:' \
  -H 'Content-Type: application/octet-stream' \
  --data-binary '@artifacts/s60-tasmota-15.6.0-final-cse7766.bin' \
  --write-out $'\nHTTP %{http_code}; uploaded %{size_upload} bytes; exit %{exitcode}\n' \
  http://192.168.4.1/update
```

The successful result is:

```text
Upload verified. Rebooting into the new application...
HTTP 200; uploaded 1299456 bytes; exit 0
```

Reconnect to the normal LAN, open `http://PLUG_IP/`, and test the relay and
meter again. Finally, restart from the Tasmota page. It must return directly to
Tasmota rather than to the bridge access point.

## 14. Clean up

Remove the temporary router interception rules using your router's normal
administration method.

Restore the repository safety lock:

```sh
mv RECOVERY_LOCK.owner-authorized RECOVERY_LOCK
test -f RECOVERY_LOCK && echo "Recovery lock restored"
```

The conversion is complete.
