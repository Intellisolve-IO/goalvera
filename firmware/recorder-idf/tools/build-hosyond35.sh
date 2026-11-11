#!/usr/bin/env bash
# Build the Hosyond ESP32-S3 3.5" (ST77922 QSPI, 320x480) variant with no
# Wi-Fi credentials and make a flash bundle. The CrowPanel build (build/,
# build-wifi/, /workspace/recorder-flash-wifi) is not touched.
#
# Usage: tools/build-hosyond35.sh [bundle_dir]
#   bundle_dir defaults to /workspace/recorder-flash-hosyond35
# For a Wi-Fi (NTP clock) build use:
#   RECORDER_BOARD=hosyond35 tools/build-with-wifi.sh
set -euo pipefail

cd -- "$(dirname -- "$0")/.."
PROJ="$PWD"
OUT_DIR="${1:-/workspace/recorder-flash-hosyond35}"
BUILD="$PROJ/build-hosyond35"

if ! command -v idf.py >/dev/null 2>&1; then
    # shellcheck disable=SC1091
    . "${IDF_PATH:-$HOME/esp/esp-idf}/export.sh" >/dev/null 2>&1
fi

idf.py -B "$BUILD" -D SDKCONFIG="$BUILD/sdkconfig" -D RECORDER_BOARD=hosyond35 build

mkdir -p "$OUT_DIR"
cp "$BUILD/bootloader/bootloader.bin" "$BUILD/partition_table/partition-table.bin" \
   "$BUILD/ota_data_initial.bin" "$BUILD/recorder.bin" "$OUT_DIR/"
sed 's#[^ ]*/##' "$BUILD/flash_args" > "$OUT_DIR/flash_args"
cat > "$OUT_DIR/flash.sh" <<'FLASH'
#!/usr/bin/env bash
# Hosyond 3.5" only. Its LCD reset is tied to EN: if the screen stays black
# after flashing, unplug USB for ~10 s and plug it back in.
set -euo pipefail
: "${1:?usage: $0 /dev/cu.usbmodem1101}"
cd -- "$(dirname -- "$0")"
python3 -m esptool --chip esp32s3 -p "$1" -b 460800 --before default_reset --after hard_reset write_flash @flash_args
FLASH
chmod 755 "$OUT_DIR/flash.sh"
chmod 644 "$OUT_DIR"/*.bin "$OUT_DIR/flash_args"
( cd "$(dirname "$OUT_DIR")" && rm -f "$(basename "$OUT_DIR").zip" && \
  python3 -c "import shutil,sys; shutil.make_archive(sys.argv[1],'zip','.',sys.argv[1])" "$(basename "$OUT_DIR")" )
echo "bundle: $OUT_DIR and $OUT_DIR.zip (Hosyond 3.5\" only; no Wi-Fi password inside)"
