# S60 one-shot OTA bridge

This is a native ESP-IDF ESP32-C3 application, not a factory image. It is
designed to be wrapped with `tools/build_vendor_ota.py` and written by the
stock S60 updater to its inactive application slot.

Bridge v3 first confirms itself so ESP-IDF rollback cannot later discard the
recovery slot, then selects the peer application for the next boot. If Wi-Fi or
the HTTP server fails, a power cycle returns to that peer. Immediately before
an upload erases the peer, the bridge selects itself instead. It selects the
new image only after `esp_ota_end` validates it.

The remaining irreducible risk is failure before `app_main` executes. The stock
1.2.0 bootloader was built without ESP-IDF application rollback, so no software
running in an app partition can eliminate that risk.

Build with PlatformIO from a path without spaces (the ESP-IDF builder used here
does not correctly quote every generated CMake path):

```sh
cp -a bridge /tmp/s60-bridge-build
pio run -d /tmp/s60-bridge-build
```

The application image is then:

```text
/tmp/s60-bridge-build/.pio/build/s60-ota-bridge/firmware.bin
```

Wrap the application for the stock updater and validate it:

```sh
python3 tools/build_vendor_ota.py \
  /tmp/s60-bridge-build/.pio/build/s60-ota-bridge/firmware.bin \
  artifacts/s60-ota-bridge-v3-1.2.1.ota --version 1.2.1
python3 tools/analyze_vendor_ota.py artifacts/s60-ota-bridge-v3-1.2.1.ota
```

On a successful first boot it creates `S60-OTA-Bridge-XXXX`, protected with
`s60-ota-bridge`. Browse to `http://192.168.4.1/` to upload a native ESP32-C3
application image. The browser sends the selected file as a raw request body;
JavaScript must be enabled.

Do not deploy this bridge until its build artifact and wrapped OTA file pass
the offline validators and a compatible final Tasmota image has been prepared.
