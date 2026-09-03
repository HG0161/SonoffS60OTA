# Development device baseline

Initial values were recorded before any firmware update or active OTA
experiment. The development-unit status has since changed as noted below.

| Field | Value |
| --- | --- |
| Product | Sonoff S60TPG |
| Plug standard | BS 1363 / Type G (UK) |
| Firmware family | SN-ESP32C3-S60-01 |
| Initially installed firmware | 1.1.1 |
| Current installed firmware | Custom Tasmota 15.6.0 (final) |
| Initially offered firmware | 1.2.0 |
| LAN control | Enabled |
| LAN discovery | `_ewelink._tcp.local` |
| LAN endpoint | TCP 8081 |
| LAN API version | 1 |
| LAN payload | AES-encrypted (`encrypt=true`) |
| Vendor update installed | Yes, during the controlled capture workflow |

Device ID, MAC address, account details and network credentials are deliberately
excluded from the repository.

## Independent reproduction

A second UK S60TPG starting on stock 1.1.1 was converted on 2026-09-03. This
run installed the consolidated wrapped bridge v3 directly through the stock
updater, followed by the reviewed trial and final Tasmota images. Relay, button,
LED, CSE7766 readings, return through the bridge fallback, and final normal
restart all passed. Private identifiers and captures remain excluded.

## Update outcome

The 1.1.1 to 1.2.0 vendor update completed and supplied a genuine wrapped image
for offline analysis. Stock 1.2.0 was subsequently replaced during the
successful controlled OTA conversion. The active application slot now contains
final Tasmota 15.6.0 and the inactive slot contains recovery bridge v3. The
original stock applications are gone; the original bootloader and partition
table remain intentionally.

## First LAN probe

The device responds to ICMP and advertises an eWeLink DNS-SD service. Port 80
rejects connections. Port 8081 is advertised for LAN control, but an unencrypted
DIY-style `/zeroconf/info` request receives no response. The mDNS TXT payload is
encrypted and supplies a 16-byte IV, API version 1 and device type `plug`.

This establishes that the device implements normal encrypted eWeLink LAN mode,
not an openly accessible DIY Mode endpoint. A device-specific `devicekey` is
needed to decrypt the advertisement and construct a valid read-only request.

The owner-authorized device key was retrieved through eWeLink without retaining
the account password or access token. The encrypted mDNS state then decrypted
successfully and confirmed firmware 1.1.1 plus the expected power-monitoring
fields. A correctly encrypted `/zeroconf/getState` request received HTTP 200 and
a structured reply with error 400, indicating that this firmware does not
implement that optional query command; state delivery through mDNS still works.
