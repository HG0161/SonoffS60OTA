# Successful no-disassembly OTA conversion: Sonoff S60TPG to Tasmota 15.6.0

I have now successfully converted a UK Sonoff S60TPG (BS1363, ESP32-C3) from
stock eWeLink firmware 1.1.1 to Tasmota 15.6.0 without opening the plug or using
UART.

## Confirmed working

- Normal Tasmota boot and reboot
- Web relay control
- Physical button
- Status LED
- CSE7766 energy metering
- Voltage, current, active/apparent/reactive power, power factor and energy
- Recovery application retained in the second OTA partition

The tested GPIO template was:

```json
{"NAME":"Sonoff S60TPG","GPIO":[1,1,1,1,224,544,1,3104,1,32,1,0,0,0,0,0,0,0,1,1,1,1],"FLAG":0,"BASE":1}
```

## Important safety warning

Only attempt this on hardware you own and can afford to recover or replace.
The stock firmware does not provide reliable automatic application rollback.
A failure before the first replacement application reaches `app_main()` may
still require opening the plug and using properly isolated serial equipment.

Never connect an ordinary grounded USB-UART adapter while this mains-powered
plug is connected to the supply. Its low-voltage electronics must be treated as
potentially live.

## Why this OTA route works

The S60 does not use the old Sonoff DIY-mode OTA API. Its modern stock updater
receives an authenticated owner command through eWeLink, then downloads a
proprietary wrapped ESP32-C3 application using HTTP range requests.

The wrapper is 100 bytes and contains version/model fields, three CRC-32 values,
the native application length and the application payload. The manifest digest
is the SHA-256 of the complete wrapped file. The updater verifies the wrapper,
digest and native ESP image, but the tested path did not require a vendor
signature over the replacement application.

The stock partition table provides two application slots of `0x1f0000` bytes
(2,031,616 bytes). The OTA payload must therefore be a native ESP32-C3
application smaller than that. A Tasmota `factory.bin` is not suitable.

## Safe two-stage design

I did not send Tasmota directly as the first replacement. The first OTA installs
a small ESP-IDF recovery bridge into the inactive stock slot.

Bridge v3:

1. Confirms itself immediately so ESP-IDF rollback cannot later discard it.
2. Selects the untouched peer application as the next-boot fallback.
3. Starts a password-protected Wi-Fi AP and local upload page.
4. Before erasing the peer, selects itself as the recovery boot target.
5. Streams the uploaded image into the peer slot.
6. Selects the peer only after `esp_ota_end()` validates the entire ESP image.

The bridge AP is accessed at `http://192.168.4.1/`. It accepts a raw native
ESP32-C3 application at `/update`.

## Network interception

The device creates fresh signed OTA URLs which cannot simply be replayed from a
computer. On the tested UniFi gateway, only the plug's traffic to the vendor OTA
server was temporarily redirected to the workstation running the owner-authorized
firmware server.

Example rules, using placeholders:

```sh
iptables -t nat -I PREROUTING 1 \
  -s PLUG_IP -d VENDOR_OTA_IP -p tcp --dport 8088 \
  -j DNAT --to-destination WORKSTATION_IP:8088

iptables -t nat -I POSTROUTING 1 \
  -s PLUG_IP -d WORKSTATION_IP -p tcp --dport 8088 \
  -j MASQUERADE
```

Both rules are necessary. Without the MASQUERADE rule, asymmetric LAN replies
cause the device to reset the connection. These temporary rules must be removed
after conversion.

## Installation sequence

1. Pair the stock plug with a disposable owner-controlled eWeLink account.
2. Enable LAN control and reserve the plug's LAN address.
3. Retrieve the owner-authorized device key and current OTA metadata.
4. Build and offline-validate the wrapped recovery bridge.
5. Add source-specific DNAT and MASQUERADE rules at the router.
6. Start the local range-capable server and send the authenticated upgrade
   command.
7. Wait for exact byte coverage of the complete wrapped bridge file.
8. Join the recovery bridge AP.
9. Upload the reduced trial Tasmota application to `/update`.
10. Configure Tasmota Wi-Fi and test relay, button, LED and metering.
11. Restart into the bridge and upload the final normal-boot Tasmota image.
12. Confirm a normal reboot and remove all temporary router rules.

Example bridge upload command:

```sh
curl --interface YOUR_WIFI_INTERFACE --http1.1 --silent --show-error \
  --connect-timeout 5 --max-time 180 \
  -H 'Expect:' -H 'Content-Type: application/octet-stream' \
  --data-binary '@s60-tasmota.bin' \
  --write-out $'\nHTTP %{http_code}; uploaded %{size_upload} bytes; exit %{exitcode}\n' \
  http://192.168.4.1/update
```

Proceed only after the bridge responds with `Upload verified`, HTTP 200, the
exact expected byte count and exit code 0.

## Tasmota build detail

`FIRMWARE_LITE` was used to remain comfortably below the stock slot limit. It
undefines `USE_ENERGY_SENSOR` after `user_config_override.h` is processed, so
simply adding `USE_CSE7766` to the override is insufficient. The custom build
re-enables the energy subsystem after `tasmota_globals.h` is included, disables
the unrelated default energy drivers, and retains only CSE7766.

The tested final native image was 1,299,456 bytes, leaving 732,160 bytes of
headroom in the stock application slot.

With the relay off, the tested S60 reports zero voltage. With the relay on, the
meter immediately produced sensible voltage/current/power readings.

## Finished partition layout

| Application slot | Contents |
|---|---|
| Active | Final custom Tasmota 15.6.0 with S60TPG/CSE7766 support |
| Inactive | ESP-IDF OTA recovery bridge v3 |

The original bootloader and partition table remain in place. A normal future
Tasmota web upgrade will overwrite the inactive bridge slot. To retain the
recovery design, refresh/boot the bridge first and use it to install the new
native Tasmota application into the peer slot.

I have also prepared the full reproducible scripts, wrapper analyzer, guarded
range server, bridge source, Tasmota build overlay and longer step-by-step guide.
They should be reviewed and published together rather than distributing an
unexplained binary.

