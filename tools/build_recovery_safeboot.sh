#!/usr/bin/env bash
set -euo pipefail
umask 077

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "$script_dir/.." && pwd)
source_dir=${1:-/tmp/s60-tasmota-recovery-src}
private_header=${2:-$project_dir/captures/safeboot-recovery/user_config_override.h}
output_dir=${3:-$project_dir/captures/safeboot-recovery}
environment=tasmota32c3-safeboot
expected_commit=db3ae7e0276fdc38b7aeb241a2a8e33d8ffd6892
settings_file="$source_dir/tasmota/tasmota_support/settings.ino"
source_header="$source_dir/tasmota/user_config_override.h"
patch_file="$project_dir/repartition/recovery-safeboot/volatile-wifi-defaults.patch"
build_dir="$source_dir/.pio/build/$environment"
variant_copy="$source_dir/variants/tasmota/tasmota32c3-safeboot.bin"
output_image="$output_dir/tasmota32c3-safeboot-recovery.bin"
output_report="$output_dir/recovery-safeboot-validation.json"
pio_core="$project_dir/.platformio-core"
pio_home="$project_dir/.pio-home"
patch_applied=0
source_touched=0

cleanup() {
  set +e
  if (( source_touched )); then
    rm -f -- "$source_header"
    if (( patch_applied )); then
      git -C "$source_dir" apply --reverse "$patch_file"
    fi
    PYTHONPATH="$pio_core" PLATFORMIO_CORE_DIR="$pio_home" \
      python3 -m platformio run -e "$environment" -d "$source_dir" -t clean >/dev/null 2>&1
    rm -f -- "$variant_copy"
  fi
}
trap cleanup EXIT

if [[ ! -f "$settings_file" ]]; then
  echo "Tasmota source not found at: $source_dir" >&2
  exit 1
fi
if [[ ! -f "$private_header" ]]; then
  echo "Private Wi-Fi header not found: $private_header" >&2
  echo "Run tools/configure_recovery_safeboot.py locally first." >&2
  exit 1
fi
if [[ $(git -C "$source_dir" rev-parse HEAD) != "$expected_commit" ]]; then
  echo "Tasmota source is not the reviewed commit $expected_commit" >&2
  exit 1
fi
if ! git -C "$source_dir" diff --quiet --exit-code ||
   ! git -C "$source_dir" diff --cached --quiet --exit-code; then
  echo "Tasmota source has tracked modifications; use a clean pinned tree." >&2
  exit 1
fi
if [[ -e "$source_header" ]] &&
   ! cmp -s "$source_header" "$source_dir/tasmota/user_config_override_sample.h"; then
  echo "Refusing to overwrite a non-sample $source_header" >&2
  exit 1
fi

chmod 0700 "$source_dir"
mkdir -p "$output_dir"
chmod 0700 "$output_dir"
PYTHONPATH="$pio_core" PLATFORMIO_CORE_DIR="$pio_home" \
  python3 -m platformio run -e "$environment" -d "$source_dir" -t clean
source_touched=1
git -C "$source_dir" apply --check "$patch_file"
git -C "$source_dir" apply "$patch_file"
patch_applied=1
install -m 0600 "$private_header" "$source_header"
rm -f -- "$variant_copy"

PYTHONPATH="$pio_core" PLATFORMIO_CORE_DIR="$pio_home" \
  python3 -m platformio run -e "$environment" -d "$source_dir"

if [[ ! -s "$build_dir/firmware.bin" ]]; then
  echo "Build did not produce $build_dir/firmware.bin" >&2
  exit 1
fi
install -m 0600 "$build_dir/firmware.bin" "$output_image"
python3 "$project_dir/tools/validate_recovery_safeboot.py" \
  --image "$output_image" \
  --private-header "$private_header" \
  --output "$output_report"

echo
echo "PRIVATE RECOVERY SAFEBOOT READY"
echo "Image: $output_image"
echo "Report: $output_report"
echo "The image contains Wi-Fi credentials; do not publish or commit it."
