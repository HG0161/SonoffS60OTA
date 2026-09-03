# AI-generated guide: install Tasmota on a Sonoff S60TPG without opening it

This is the plain-English version of the successful S60TPG OTA procedure.
“OTA” means updating firmware over Wi-Fi. You do not open the plug or connect
wires to its circuit board.

This is still an experimental procedure, not a normal supported Tasmota
installer. Read the whole guide before starting.

## Important safety warning

The S60 is a mains-voltage device.

- Only work on a plug you own and can afford to replace.
- Never open it while connected to mains.
- Never attach a normal USB serial adapter while it is connected to mains.
- Keep power stable during every firmware upload.
- If your model, firmware, or screen differs from this guide, stop and ask.

There is a small chance that a failed first boot will require an experienced
person with electrically isolated serial equipment to recover the plug.

## Make sure you have the correct plug

This guide is for:

```text
Model: S60TPG
Plug: UK BS1363 Wi-Fi model
Processor: ESP32-C3
Tested stock firmware: 1.1.1
```

It is **not** for the Zigbee S60 or a different regional model.

## What you need

1. The S60TPG plug.
2. An Android or iPhone with the eWeLink app.
3. A disposable eWeLink account used only for this plug.
4. A 2.4 GHz Wi-Fi network.
5. A Linux computer connected to the same network.
6. This complete project folder, including `tools/` and `artifacts/`.
7. Administrator access to your router.

The tested router was a UniFi Cloud Gateway Ultra, but UniFi is not inherently
required. The router must be able to redirect one plug's outbound connection
to one workstation and rewrite the return path. See **Router compatibility**
before starting.

### Router compatibility

The required features may be called different things by router vendors:

- a LAN-side or outbound **destination NAT/port-forward** rule, restricted to
  the plug's source IP and the vendor OTA destination IP/port; and
- **source NAT**, **SNAT**, **masquerade**, or **NAT reflection** for the
  redirected connection so replies return through the router.

| Router type | Expected route |
|---|---|
| UniFi gateway with SSH | Tested; use the `iptables` recipe in Part 4 |
| OpenWrt or a Linux router | Likely suitable; needs an equivalent `nftables` or `iptables` recipe that must be tested before publishing |
| pfSense/OPNsense | Likely suitable through LAN port-forward and outbound-NAT rules; must be tested before publishing |
| Typical ISP/consumer router | Often cannot redirect LAN-originated traffic by source and destination |

If the router cannot provide both operations, stop before Part 4. A practical
community fallback is to put both the plug and workstation temporarily behind
a separate OpenWrt/Linux travel router with a tested recipe. A DNS override is
not enough because the stock update metadata supplies a destination IP.

Do not guess at firewall rules: an incorrect rule can let the plug download
the genuine vendor update or can redirect unrelated clients. The preflight in
Part 4 must pass before sending an upgrade command.

## Words used in this guide

- **Stock firmware:** the original Sonoff/eWeLink software.
- **Tasmota:** the replacement open-source firmware.
- **Partition/slot:** one of two areas in the plug's flash memory that can hold
  an application.
- **Recovery bridge:** a small temporary application that safely receives
  Tasmota.
- **Terminal:** the Linux window where you type commands.
- **Plug IP:** the plug's address on your home network.
- **Workstation IP:** the Linux computer's address on your home network.

## The overall process

You will do this in three firmware steps:

```text
Original Sonoff
      ↓
Recovery bridge
      ↓
Trial Tasmota (for testing)
      ↓
Final Tasmota
```

The trial exists so you can test the relay, button, LED and energy meter while
the bridge remains the automatic fallback.

---

## Part 1: prepare the plug in eWeLink

1. Plug the S60 into a safe wall socket.
2. Add it to eWeLink using the normal **Add device** process.
3. Select your 2.4 GHz Wi-Fi network when asked.
4. Give it an obvious name such as `S60 OTA Test`.
5. Open **Device Settings** and turn on **LAN control**.
6. Do not install the offered Sonoff firmware update.

In your router's client list, find the device whose manufacturer is Espressif
or Sonoff. Reserve its IP so it will not change. In the successful test it was
`192.168.1.96`; yours may be different.

Write down:

```text
Plug IP: ______________________
Workstation IP: _______________
Router IP: ____________________
Stock version: ________________
```

To find the Linux workstation IP, open a terminal and run:

```sh
hostname -I
```

Use the address beginning with the same first three numbers as the plug. For
example, if the plug is `192.168.1.96`, the workstation will normally be
`192.168.1.something`.

## Part 2: open the project and run the safe checks

In a terminal:

```sh
cd "/path/to/Tasmatized Sonoff s60"
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -v
```

Replace `PLUG_IP` below with the address you wrote down, then create a
discovery file for this particular plug:

```sh
python3 tools/discover_ewelink.py \
  --timeout 8 \
  --target PLUG_IP \
  --output captures/my-s60/mdns.json
```

This first file identifies the particular plug. Every following command uses
it as an input, which prevents a second S60 on the account from being selected
by mistake.

Now obtain that plug's owner-authorized encryption key:

```sh
python3 tools/get_device_key.py \
  --mdns-capture captures/my-s60/mdns.json \
  --output captures/my-s60/device-key.json
```

Type the disposable eWeLink email and password at the local prompt. The
password is hidden while you type and is not saved.

Query the available official update without installing it:

```sh
python3 tools/query_ota.py \
  --mdns-capture captures/my-s60/mdns.json \
  --output captures/my-s60/ota-metadata.json
```

The command prints the plug's current firmware version plus the vendor OTA
host and port. Save the host and port for the router-interception rule. The
private URL path remains only in the protected output file.

Finally test read-only LAN communication. Replace the example IP:

```sh
python3 tools/lan_get_state.py PLUG_IP \
  --key-file captures/my-s60/device-key.json \
  --output captures/my-s60/lan-state.json
```

On tested stock firmware 1.1.1, the expected result is `LAN connection: PASS`
followed by `Read-only state query: NOT SUPPORTED`. The eWeLink error 400 behind
that message means this optional command is unavailable; it does not mean the
LAN check failed.

### Checkpoint

Continue only if:

- all non-network-restricted tests pass;
- the key and OTA metadata are saved under `captures/`;
- the LAN state command identifies the correct plug;
- its reported stock version is the version you expected.

## Part 3: check the firmware files

These four files must exist:

```text
artifacts/s60-ota-bridge-v3-1.2.1.ota
artifacts/s60-ota-bridge-v3-idf5.3.1.bin
artifacts/s60-tasmota-15.6.0-trial-cse7766.bin
artifacts/s60-tasmota-15.6.0-final-cse7766.bin
```

Validate the wrapped bridge:

```sh
python3 tools/analyze_vendor_ota.py \
  artifacts/s60-ota-bridge-v3-1.2.1.ota
sha256sum artifacts/s60-ota-bridge-v3-1.2.1.ota
```

The final SHA-256 line must begin with:

```text
10d79d33856bb842b26f0a1b6748751c091ec9738a72dd4de2033fbd0c329ff7
```

### Hardware-validation checkpoint

The wrapped bridge v3 file has passed all offline checks and was successfully
installed directly from stock firmware 1.1.1 on a second S60TPG. This clean
reproduction confirmed the stock-to-bridge, trial, final, metering and normal
restart stages.

## Part 4: temporarily redirect the plug's update download

This is the most advanced part. Ask someone comfortable with router
administration if these terms are unfamiliar.

Use the `Vendor OTA host` and `port` printed by `query_ota.py`. They are also
saved in `captures/my-s60/ota-metadata.json`. The successful test used
`52.57.99.135` on port `8088`, but this can change.

### What every router configuration must do

Translate the following specification into your router's terminology:

1. Give the plug and workstation fixed/reserved LAN addresses.
2. On traffic entering the router from the LAN, match only this exact flow:
   `source=PLUG_IP`, `destination=VENDOR_OTA_IP`, `protocol=TCP`, and
   `destination-port=VENDOR_OTA_PORT`.
3. Rewrite that flow's destination to
   `WORKSTATION_IP:VENDOR_OTA_PORT` (destination NAT/DNAT).
4. Rewrite its source or enable masquerade/NAT reflection so the workstation's
   reply returns through the router instead of going directly to the plug.
5. Permit that forwarded flow through any LAN firewall rule.
6. Do not redirect other clients, other destinations, or other ports.
7. Make the rules temporary and record exactly how to remove them.
8. Run the end-to-end preflight below before sending an upgrade command.

A normal **WAN port-forward** is not sufficient: this connection originates
on the LAN. Router interfaces may call the required feature LAN NAT, outbound
port forwarding, policy NAT, destination NAT, NAT reflection, or hairpin NAT.
If the interface cannot express both steps 2–4, use a capable temporary router
or stop.

The intended packet path is:

```text
S60 PLUG_IP -> router (addressed to VENDOR_OTA_IP:PORT)
             -> DNAT + masquerade
             -> WORKSTATION_IP:PORT
             -> reply through router
             -> S60
```

The following is the tested implementation of that specification.

On a UniFi gateway, first enable SSH in the UniFi console. Then connect:

```sh
ssh root@YOUR_ROUTER_IP
```

Add rules like these, replacing all capitalized values:

```sh
iptables -t nat -I PREROUTING 1 \
  -s PLUG_IP -d VENDOR_OTA_IP -p tcp --dport 8088 \
  -j DNAT --to-destination WORKSTATION_IP:8088

iptables -t nat -I POSTROUTING 1 \
  -s PLUG_IP -d WORKSTATION_IP -p tcp --dport 8088 \
  -j MASQUERADE
```

Example only:

```sh
iptables -t nat -I PREROUTING 1 \
  -s 192.168.1.96 -d 52.57.99.135 -p tcp --dport 8088 \
  -j DNAT --to-destination 192.168.1.141:8088

iptables -t nat -I POSTROUTING 1 \
  -s 192.168.1.96 -d 192.168.1.141 -p tcp --dport 8088 \
  -j MASQUERADE
```

Both rules are required. Leave the router terminal open so you can remove them
afterward.

### Check the router rules before continuing

Still on the router, repeat each rule with `-C` instead of `-I`:

```sh
iptables -t nat -C PREROUTING \
  -s PLUG_IP -d VENDOR_OTA_IP -p tcp --dport 8088 \
  -j DNAT --to-destination WORKSTATION_IP:8088 \
  && echo "DNAT rule present"

iptables -t nat -C POSTROUTING \
  -s PLUG_IP -d WORKSTATION_IP -p tcp --dport 8088 \
  -j MASQUERADE \
  && echo "MASQUERADE rule present"
```

Both confirmation messages must appear.

For an additional end-to-end routing check:

1. Stop the OTA sender if it is running.
2. Unplug the S60 so its reserved address is free.
3. On the workstation, run the following with your real values:

```sh
sudo ./tools/test_ota_firewall.sh \
  INTERFACE PLUG_IP WORKSTATION_IP VENDOR_OTA_IP 8088
```

For example only:

```sh
sudo ./tools/test_ota_firewall.sh \
  wlo1 192.168.1.96 192.168.1.141 52.57.99.135 8088
```

Continue only after both interception checks say `PASS` and the final line says
the temporary address was removed. Then plug the S60 back in and wait for it to
reconnect. This confirms source-specific interception; it does not make the
initial firmware flash risk-free.

## Part 5: send the recovery bridge through stock OTA

Back in the workstation terminal, run the following as one command. Replace
the IP addresses, email and stock version:

```sh
python3 tools/serve_tasmota_ota.py \
  --listen-ip WORKSTATION_IP \
  --listen-port 8088 \
  --device-ip PLUG_IP \
  --firmware artifacts/s60-ota-bridge-v3-1.2.1.ota \
  --email YOUR_EWELINK_EMAIL \
  --expected-current-version STOCK_VERSION \
  --mdns-capture captures/my-s60/mdns.json \
  --i-understand-stock-has-no-automatic-rollback
```

The program deliberately refuses to run while `RECOVERY_LOCK` exists. Only
rename that file after checking every value above and accepting the risk:

```sh
mv RECOVERY_LOCK RECOVERY_LOCK.owner-authorized
```

Then run the sender command again.

### What you should see

The terminal should show many lines containing:

```text
GET /user1.bin
206
Served ... bytes
```

The byte ranges should progress until the complete file has been served. The
plug may flash its blue LED three times and disappear from eWeLink. Do not
unplug it.

### Stop if

- the wrong device IP requests the file;
- the reported current version differs;
- the ranges repeatedly restart without progressing;
- validation reports a size, checksum or SHA mismatch.

## Part 6: connect to the recovery bridge

After the update, look in the computer's Wi-Fi menu for:

```text
S60-OTA-Bridge-XXXX
```

Connect using:

```text
Password: s60-ota-bridge
```

Open:

```text
http://192.168.4.1/
```

The browser Upload button has been unreliable in hardware testing. Use the
tested terminal upload while still connected to the bridge Wi-Fi:

```sh
curl --interface wlo1 --http1.1 --silent --show-error \
  --connect-timeout 5 --max-time 180 \
  -H 'Expect:' -H 'Content-Type: application/octet-stream' \
  --data-binary '@artifacts/s60-tasmota-15.6.0-trial-cse7766.bin' \
  --write-out $'\nHTTP %{http_code}; uploaded %{size_upload} bytes; exit %{exitcode}\n' \
  http://192.168.4.1/update
```

If your Wi-Fi device is not named `wlo1`, find its name with:

```sh
ip link
```

Replace `wlo1` in the command.

### Checkpoint

Do nothing until the command says all three of these:

```text
Upload verified
HTTP 200
exit 0
```

## Part 7: give trial Tasmota your Wi-Fi details

The bridge reboots automatically. Look for a Wi-Fi network whose name begins
with `tasmota-`.

Trial Tasmota may perform an automatic first-start restart while initializing
its settings. Because the trial selects the bridge as its fallback, the bridge
Wi-Fi may reappear instead. Do not upload the trial again. Power-cycle the plug
once; the bridge has already selected the intact trial image for that boot.

Connect using:

```text
Password: s60-tasmota
```

Your browser should open Tasmota's Wi-Fi setup page. If it does not, open
`http://192.168.4.1/` manually.

1. Select your normal 2.4 GHz Wi-Fi network.
2. Enter its password.
3. Save.
4. Reconnect the computer to the normal Wi-Fi network.
5. Open the plug's reserved LAN IP, for example `http://192.168.1.96/`.

## Part 8: test everything

On the Tasmota page:

1. Click **Toggle** and listen for the relay.
2. Press the plug's physical button once.
3. Check that the LED and relay respond.
4. Attach only a known-safe load.
5. Turn the relay on.
6. Wait five seconds.

You should see Voltage, Current, Active Power, Apparent Power, Reactive Power,
Power Factor and Energy. On the tested plug, voltage reads zero while the relay
is off. New energy totals also start at zero.

Do not continue unless the relay, button, LED and meter all work.

## Part 9: install final Tasmota

The trial deliberately makes the recovery bridge the next application to boot.
On the Tasmota page click **Restart**.

Reconnect to `S60-OTA-Bridge-XXXX` and upload the final image:

```sh
curl --interface wlo1 --http1.1 --silent --show-error \
  --connect-timeout 5 --max-time 180 \
  -H 'Expect:' -H 'Content-Type: application/octet-stream' \
  --data-binary '@artifacts/s60-tasmota-15.6.0-final-cse7766.bin' \
  --write-out $'\nHTTP %{http_code}; uploaded %{size_upload} bytes; exit %{exitcode}\n' \
  http://192.168.4.1/update
```

Again, wait for `Upload verified`, HTTP 200 and exit 0.

Reconnect to normal Wi-Fi and open the plug's LAN IP. Test the relay and meter
again. Finally click **Restart**, wait about 15 seconds and reload the page. It
must return directly to Tasmota rather than the bridge Wi-Fi.

## Part 10: remove the temporary router rules

In the still-open router SSH terminal, remove the exact rules you added. Use
the same substituted values:

```sh
iptables -t nat -D PREROUTING \
  -s PLUG_IP -d VENDOR_OTA_IP -p tcp --dport 8088 \
  -j DNAT --to-destination WORKSTATION_IP:8088

iptables -t nat -D POSTROUTING \
  -s PLUG_IP -d WORKSTATION_IP -p tcp --dport 8088 \
  -j MASQUERADE
```

Also remove any temporary rules created in the router's graphical interface.
Tasmota does not require the eWeLink account.

Restore the local safety lock if you renamed it in Part 5:

```sh
mv RECOVERY_LOCK.owner-authorized RECOVERY_LOCK
test -f RECOVERY_LOCK && echo "Recovery lock restored"
```

## Finished result

The plug now has:

| Application slot | Contents |
|---|---|
| Active | Final Tasmota 15.6.0 with S60TPG/CSE7766 support |
| Inactive | Recovery bridge v3 |

The original Sonoff applications are gone, but the original bootloader and
partition table remain intentionally.

## Future Tasmota upgrades

A normal Tasmota web upgrade writes over the inactive recovery bridge. To keep
the recovery arrangement:

1. Upload `s60-ota-bridge-v3-idf5.3.1.bin` from Tasmota's **Firmware Upgrade**
   page.
2. Join the bridge Wi-Fi.
3. Upload the new, validated native Tasmota `.bin` through `/update`.

Never upload a `factory.bin` to either web updater.
