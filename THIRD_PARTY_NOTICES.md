# Third-party software and corresponding source

The files named `s60-tasmota-15.6.0-*.bin` are custom builds derived from
[Tasmota 15.6.0](https://github.com/arendst/Tasmota/tree/v15.6.0). Tasmota is
distributed under the GNU General Public License v3.0 only.

The exact project-specific configuration and source changes used for these
builds are provided in `tasmota-s60/`, with the reproducible build helper at
`tools/build_final_tasmota.sh`. Obtain the unmodified upstream source from the
tag linked above, then apply the included overlay and patch as documented in
`tasmota-s60/README.md`.

The OTA bridge is built using Espressif ESP-IDF through PlatformIO. Its source
is provided in `bridge/`. ESP-IDF and its bundled components retain their own
copyright notices and licenses; see the corresponding upstream distributions
resolved by `bridge/platformio.ini`.

Sonoff and eWeLink are trademarks of their respective owners. This project is
independent and is not an official Sonoff, eWeLink, or Tasmota installer.

