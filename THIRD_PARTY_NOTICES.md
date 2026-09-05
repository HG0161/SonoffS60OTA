# Third-party software and corresponding source

## Tasmota-derived firmware

Tasmota is distributed under the GNU General Public License v3.0 only.

`artifacts/tasmota32c3-bluetooth.bin` is an unmodified Tasmota ESP32-C3
Bluetooth build retrieved from Tasmota's official `install` repository,
`firmware/unofficial/tasmota32c3-bluetooth.bin`. That moving branch no longer
serves these exact bytes. The image itself embeds source identifier `f5b34a2`;
the corresponding source is upstream commit
[`f5b34a26be51be2469737d2c43a62acb237c264f`](https://github.com/arendst/Tasmota/tree/f5b34a26be51be2469737d2c43a62acb237c264f).
Its build environment is `tasmota32c3-bluetooth` in
[`platformio_tasmota_cenv_sample.ini`](https://github.com/arendst/Tasmota/blob/f5b34a26be51be2469737d2c43a62acb237c264f/platformio_tasmota_cenv_sample.ini).

`artifacts/s60-recovery-safeboot-imprintable.bin` is a custom Safeboot derived
from upstream Tasmota commit
[`db3ae7e0276fdc38b7aeb241a2a8e33d8ffd6892`](https://github.com/arendst/Tasmota/tree/db3ae7e0276fdc38b7aeb241a2a8e33d8ffd6892).
The corresponding project changes and build tooling are
`repartition/recovery-safeboot/volatile-wifi-defaults.patch`,
`tools/configure_imprintable_safeboot.py`, and
`tools/build_recovery_safeboot.sh`.

The older custom `s60-tasmota-15.6.0-*.bin` builds and their corresponding
configuration, patch, documentation and helper are retained together under
`archive/`.

## OTA bridge

The OTA bridge is built using Espressif ESP-IDF through PlatformIO. Its source
is provided in `archive/bridge/`. ESP-IDF and its bundled components retain
their own copyright notices and licenses; see the corresponding upstream
distributions resolved by `archive/bridge/platformio.ini`.

Sonoff and eWeLink are trademarks of their respective owners. This project is
independent and is not an official Sonoff, eWeLink, or Tasmota installer.
