#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "$script_dir/.." && pwd)
source_dir=${1:-/tmp/Tasmota-15.6.0}
environment=tasmota32c3-s60-final
build_dir="$source_dir/.pio/build/$environment"
output_bin="$project_dir/artifacts/s60-tasmota-15.6.0-final-cse7766.bin"
output_elf="$project_dir/artifacts/s60-tasmota-15.6.0-final-cse7766.elf"
pio_core="$project_dir/.platformio-core"
pio_home="$project_dir/.pio-home"

if [[ ! -f "$source_dir/tasmota/tasmota.ino" ]]; then
  echo "Tasmota source not found at: $source_dir" >&2
  echo "Pass its directory as the first argument." >&2
  exit 1
fi

if ! grep -qF 'S60_CUSTOM_BUILD' "$source_dir/tasmota/tasmota.ino"; then
  patch --forward --directory="$source_dir" --strip=1 \
    < "$project_dir/tasmota-s60/early-fallback.patch"
fi

install -m 0644 "$project_dir/tasmota-s60/platformio_override.ini" \
  "$source_dir/platformio_override.ini"
install -m 0644 "$project_dir/tasmota-s60/user_config_override.h" \
  "$source_dir/tasmota/user_config_override.h"

echo "Building final S60 Tasmota firmware (normally 2-4 minutes)..."
set +e
PYTHONPATH="$pio_core" PLATFORMIO_CORE_DIR="$pio_home" \
  python3 -m platformio run -e "$environment" -d "$source_dir"
pio_status=$?
set -e

# Tasmota may return non-zero only because optional factory-image packaging
# cannot download safeboot. The native OTA application is independently valid.
if [[ ! -s "$build_dir/firmware.bin" || ! -s "$build_dir/firmware.elf" ]]; then
  echo "Build failed before producing native OTA artifacts (status $pio_status)." >&2
  exit "${pio_status:-1}"
fi

image_size=$(stat -c %s "$build_dir/firmware.bin")
slot_ceiling=$((0x1f0000))
if (( image_size > slot_ceiling )); then
  echo "Image is too large: $image_size > $slot_ceiling bytes." >&2
  exit 1
fi

nm_tool="$pio_home/packages/toolchain-riscv32-esp/bin/riscv32-esp-elf-nm"
nm_output=$("$nm_tool" -C "$build_dir/firmware.elf")
if ! grep -qF 'Xnrg02(unsigned int)' <<<"$nm_output"; then
  echo "Validation failed: CSE7766 driver symbol Xnrg02 is missing." >&2
  exit 1
fi

esptool_python="$pio_home/penv/bin/python"
esptool_script="$pio_home/packages/tool-esptoolpy/esptool.py"
image_info=$(
  "$esptool_python" "$esptool_script" image_info "$build_dir/firmware.bin" 2>&1
)
if ! grep -qF 'Detected image type: ESP32-C3' <<<"$image_info" ||
   ! grep -qE 'Checksum: .*\(valid\)' <<<"$image_info" ||
   ! grep -qE 'Validation hash: .*\(valid\)' <<<"$image_info"; then
  printf '%s\n' "$image_info" >&2
  echo "Validation failed: invalid ESP32-C3 application image." >&2
  exit 1
fi

install -m 0644 "$build_dir/firmware.bin" "$output_bin"
install -m 0755 "$build_dir/firmware.elf" "$output_elf"

echo
echo "FINAL BUILD READY"
echo "Image: $output_bin"
echo "Size: $image_size bytes (headroom $((slot_ceiling - image_size)) bytes)"
sha256sum "$output_bin"
