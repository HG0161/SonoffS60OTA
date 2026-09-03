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

If the page's Upload button does nothing, use the following terminal command
while still connected to the bridge Wi-Fi:

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
