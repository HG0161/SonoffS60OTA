# Sonoff S60TPG stock-to-Tasmota OTA guide

This guide records the successful no-opening, no-UART conversion of a UK
Sonoff S60TPG (BS1363, ESP32-C3) from stock firmware 1.1.1 to a custom Tasmota
15.6.0 build. The relay, button, LED, normal reboot, and CSE7766 energy meter
were verified on the converted device.

Only use this on hardware you own. A failed first application boot can still
require opening the mains-powered plug and using isolated serial equipment.
Never connect an ordinary grounded USB-UART adapter while the plug is on mains.

## What the finished flash contains

The stock bootloader and partition table remain unchanged. The two
`0x1f0000` (2,031,616-byte) application slots contain:

- active slot: final S60 Tasmota 15.6.0;
- inactive slot: S60 OTA recovery bridge v3.

Never upload a Tasmota `factory.bin` through this procedure. Use only a native
ESP32-C3 application `.bin` smaller than `0x1f0000`.

## Requirements

- Linux computer connected to the same 2.4 GHz LAN as the plug;
- Python 3 and this project directory;
- an owner-controlled eWeLink account containing the test plug;
- LAN access to the plug and administrative access to the router;
- for the tested UniFi method, SSH access to the gateway;
- stable mains power throughout every write.

Examples below use these tested addresses; substitute yours:

| Purpose | Address |
|---|---|
| S60 | `192.168.1.96` |
| workstation | `192.168.1.141` |
| UniFi gateway | `192.168.1.1` |
| captured vendor OTA host | `52.57.99.135:8088` |

## 1. Pair and inventory the stock plug

Pair the S60 in eWeLink, enable **LAN control**, and reserve its IP address at
the router. Do not accept the offered vendor update while preparing the flash.

From the project directory:

```sh
python3 tools/get_device_key.py
python3 tools/query_ota.py
python3 tools/lan_get_state.py 192.168.1.96
```

Enter the disposable eWeLink account credentials only at the local hidden
prompt. Keys and captured metadata are stored under the git-ignored
`captures/` directory with restrictive permissions.

## 2. Validate the payloads

The current artifacts are:

```text
artifacts/s60-ota-bridge-v3-1.2.1.ota
artifacts/s60-ota-bridge-v3-idf5.3.1.bin
artifacts/s60-tasmota-15.6.0-trial-cse7766.bin
artifacts/s60-tasmota-15.6.0-final-cse7766.bin
```

Validate the wrapped first-stage image:

```sh
python3 tools/analyze_vendor_ota.py \
  artifacts/s60-ota-bridge-v3-1.2.1.ota
sha256sum artifacts/s60-ota-bridge-v3-1.2.1.ota
```

Expected wrapped-file SHA-256:

```text
10d79d33856bb842b26f0a1b6748751c091ec9738a72dd4de2033fbd0c329ff7
```

Build the final Tasmota image locally when required:

```sh
./tools/build_final_tasmota.sh
```

The script checks the slot size, ESP32-C3 image type, checksum, embedded hash,
and CSE7766 driver. A failure while producing the optional combined factory
image is harmless if the script subsequently prints `FINAL BUILD READY`.

## 3. Redirect only the plug's vendor OTA connection

The S60 signs a fresh request to the vendor OTA server, so replaying its URL
from the workstation does not work. Redirect only traffic from the plug to the
workstation. On the tested UniFi gateway:

```sh
ssh root@192.168.1.1
iptables -t nat -I PREROUTING 1 \
  -s 192.168.1.96 -d 52.57.99.135 -p tcp --dport 8088 \
  -j DNAT --to-destination 192.168.1.141:8088
iptables -t nat -I POSTROUTING 1 \
  -s 192.168.1.96 -d 192.168.1.141 -p tcp --dport 8088 \
  -j MASQUERADE
```

Both rules are required. DNAT alone causes asymmetric replies and a TCP reset.
Keep the SSH session open so the rules can be removed promptly.

## 4. Perform the stock first-stage OTA

The live sender is deliberately guarded. Confirm the exact IPs, model, current
version, wrapped-image validation, and recovery plan before disabling or
renaming `RECOVERY_LOCK`.

Run the sender on the workstation, substituting your account email:

```sh
python3 tools/serve_tasmota_ota.py \
  --listen-ip 192.168.1.141 \
  --listen-port 8088 \
  --device-ip 192.168.1.96 \
  --firmware artifacts/s60-ota-bridge-v3-1.2.1.ota \
  --email YOUR_EWELINK_EMAIL \
  --expected-current-version 1.1.1 \
  --i-understand-stock-has-no-automatic-rollback
```

The S60 downloads the file using many HTTP `206 Partial Content` range
requests. Do not interrupt it. Completion requires exact byte coverage of the
whole wrapped file. The app becoming offline and the blue LED flashing three
times are expected during the update.

The original hardware run installed wrapped bridge v2 through the stock path,
then installed native bridge v3 through Tasmota. The consolidated wrapped v3
above uses the same validated wrapper and updater path, but has not yet been
independently exercised as a stock first-stage payload.

## 5. Enter the bridge and install trial Tasmota

After the bridge boots, join:

```text
SSID: S60-OTA-Bridge-XXXX
Password: s60-ota-bridge
URL: http://192.168.4.1/
```

If the browser upload button is unreliable, use the raw endpoint:

```sh
curl --interface wlo1 --http1.1 --silent --show-error \
  --connect-timeout 5 --max-time 180 \
  -H 'Expect:' -H 'Content-Type: application/octet-stream' \
  --data-binary '@artifacts/s60-tasmota-15.6.0-trial-cse7766.bin' \
  --write-out $'\nHTTP %{http_code}; uploaded %{size_upload} bytes; exit %{exitcode}\n' \
  http://192.168.4.1/update
```

Continue only after seeing `Upload verified`, HTTP 200, the exact file size,
and exit 0. The bridge pins itself before erasing its peer and selects the new
application only after ESP-IDF validates the complete image.

## 6. Configure and test the trial

On first boot, join the `tasmota-XXXXXX-XXXX` setup AP with password
`s60-tasmota`, browse to `http://192.168.4.1/`, and enter the normal LAN Wi-Fi
credentials. Reconnect the workstation to the LAN and open the plug's reserved
address.

Verify all of the following before proceeding:

1. Tasmota identifies the device as **Sonoff S60TPG**.
2. Web relay toggle works.
3. The physical button toggles the relay and the LED behaves correctly.
4. With the relay on and a known-safe load attached, Voltage, Current, Active
   Power, Apparent Power, Reactive Power, Power Factor, and Energy appear.
5. A restart returns successfully.

Zero voltage with the relay off is normal on the tested S60. The energy totals
also begin at zero.

## 7. Install the final normal-boot image

The trial deliberately selects the bridge for its next boot. Click **Restart**
in Tasmota, rejoin `S60-OTA-Bridge-XXXX`, then upload the final image:

```sh
curl --interface wlo1 --http1.1 --silent --show-error \
  --connect-timeout 5 --max-time 180 \
  -H 'Expect:' -H 'Content-Type: application/octet-stream' \
  --data-binary '@artifacts/s60-tasmota-15.6.0-final-cse7766.bin' \
  --write-out $'\nHTTP %{http_code}; uploaded %{size_upload} bytes; exit %{exitcode}\n' \
  http://192.168.4.1/update
```

Reconnect to the LAN, confirm metering and relay operation, and perform one
final restart. The final build boots normally instead of automatically
selecting the bridge.

## 8. Remove temporary network rules

On the gateway, delete the exact rules added earlier:

```sh
iptables -t nat -D PREROUTING \
  -s 192.168.1.96 -d 52.57.99.135 -p tcp --dport 8088 \
  -j DNAT --to-destination 192.168.1.141:8088
iptables -t nat -D POSTROUTING \
  -s 192.168.1.96 -d 192.168.1.141 -p tcp --dport 8088 \
  -j MASQUERADE
```

Remove any temporary UniFi firewall/DNAT policies as well. The disposable
eWeLink account is no longer needed by Tasmota.

## Recovery and future upgrades

The bridge remains in the inactive app slot, but an ordinary Tasmota web OTA
will overwrite that slot. To preserve the two-stage layout for an upgrade:

1. upload `s60-ota-bridge-v3-idf5.3.1.bin` using Tasmota's **Firmware Upgrade**
   page (this refreshes and boots the bridge slot);
2. join the bridge AP;
3. send the new validated native Tasmota app to `/update`.

Keep both bridge and final Tasmota artifacts offline. Do not use the stock
eWeLink updater again after conversion.

