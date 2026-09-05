# Archive

Work that got this project to where it is, and is no longer part of converting
a plug. Nothing here is needed to run the converter; nothing here is deleted,
because the reasoning is often more useful than the result.

## `tools/`

The investigation tools. These are how the stock update mechanism was worked
out in the first place: probing the plug's endpoints, decrypting its LAN
advertisements, intercepting and replaying a genuine vendor firmware download,
and analysing a full flash dump.

They were written to answer a question once. They are not maintained, and some
assume addresses and paths from the machine they were written on.

`build_final_tasmota.sh` built the cut-down Tasmota that early conversions
needed, back when the plug's original layout left only 1,984 KiB for firmware.
Once the layout is rebuilt, the official Tasmota release fits, and no custom
build is required at all.

## `bridge/`

ESP-IDF source for the small one-shot firmware that takes over the plug from
the stock updater. The built and wrapped artifact it produces is in
`artifacts/`, pinned by SHA-256 and still very much in use — this is the source
it was built from.

## `tasmota-s60/`

The Tasmota build overlay for the custom S60 images: the GPIO template, the
feature set trimmed to fit the old layout, and the early-fallback patch. Kept
for the template, which is still the correct one for this hardware, and as the
provenance of the images in `artifacts/`.

## `tests/`

Tests for the archived tools, moved with them so the main suite stays about the
converter. Run them with `python3 -m unittest discover -s archive/tests` from
the repository root.

## `artifacts/`

`s60-ota-bridge-v2-idf5.3.1.elf` — debug symbols for bridge v2, 8 MB, useful
only for decoding a crash dump from that build. The v2 firmware itself stays in
the main `artifacts/` directory, because it was published in response to
[issue #1](https://github.com/HG0161/SonoffS60OTA/issues/1) and someone may
still be waiting on it.
