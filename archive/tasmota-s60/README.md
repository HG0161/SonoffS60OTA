# S60 reduced Tasmota trial image

This overlay targets Tasmota 15.6.0 and produces a native ESP32-C3 application
image small enough for the stock S60's `0x1f0000` OTA slots. The build uses
Tasmota's normal partition CSV only as a linker/factory-packaging aid; only the
native app image is used, and its size is separately checked against the stock
slot. It retains the web
UI, MQTT, relay/button handling, and CSE7766-compatible energy metering while
removing unrelated ESP32 features.

The trial build selects the peer OTA slot (the one-shot bridge) at the very
start of `setup()`. A power cycle after the trial therefore enters the bridge,
which in turn selects the trial image as its fallback. Do not remove this early
fallback for the first hardware run.

Build from a clean Tasmota 15.6.0 source tree:

```sh
cp tasmota-s60/platformio_override.ini /tmp/Tasmota-15.6.0/platformio_override.ini
cp tasmota-s60/user_config_override.h /tmp/Tasmota-15.6.0/tasmota/user_config_override.h
patch -d /tmp/Tasmota-15.6.0 -p1 < tasmota-s60/early-fallback.patch
PLATFORMIO_CORE_DIR=/tmp/s60-pio-home-real .platformio-core/bin/pio run \
  -d /tmp/Tasmota-15.6.0 -e tasmota32c3-s60-trial
```

The image delivered through the bridge must be the app-only `.bin`, never a
`.factory.bin`. On first boot, join the normal Tasmota setup AP using password
`s60-tasmota`, configure Wi-Fi, and verify relay, button, LED, and metering.
