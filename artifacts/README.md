# Published firmware artifacts

Every file here is pinned in [`../SHA256SUMS`](../SHA256SUMS). Per-device
credentials and flash captures belong under the Git-ignored `captures/`
directory, never here.

| File | Purpose | SHA-256 |
|---|---|---|
| `s60-ota-bridge-v3-1.2.1.ota` | Wrapped one-shot bridge accepted by the stock S60 updater | `10d79d33856bb842b26f0a1b6748751c091ec9738a72dd4de2033fbd0c329ff7` |
| `tasmota32c3-bluetooth.bin` | Berry-capable Tasmota used temporarily on the original partition layout | `a92cfadaff5a3c824f790471ba31464acd2076a24ff1fcad2de28574c5641708` |
| `s60-recovery-safeboot-imprintable.bin` | Credential-free recovery Safeboot template | `7a4f719402d9e86ef03d13d36701efb83ec751d7c694c9d7723e5219721dc803` |

The Bluetooth image is the exact file used by the successful migrations. It
was retrieved from Tasmota's official moving `install/firmware` branch at
`firmware/unofficial/tasmota32c3-bluetooth.bin`. Its embedded source identifier
is `f5b34a2`; corresponding source is
[`arendst/Tasmota@f5b34a26be51be2469737d2c43a62acb237c264f`](https://github.com/arendst/Tasmota/tree/f5b34a26be51be2469737d2c43a62acb237c264f).

The imprintable Safeboot contains reserved placeholder bytes instead of real
Wi-Fi credentials. It **must not be flashed as published**: the private
bundle-preparation path must imprint and validate a per-device copy first.
