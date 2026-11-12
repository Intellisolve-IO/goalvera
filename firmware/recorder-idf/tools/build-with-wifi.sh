#!/usr/bin/env bash
# Local-only build with Wi-Fi credentials (NTP clock sync + uploads) and an
# optional upload service / device key for ONE board. (0.8.2: the header is
# always the goalvera logo; local/branding.json is no longer used.)
#
#   SSID:     $RECORDER_WIFI_SSID, else ~/.config/recorder/wifi_ssid
#   Password: $RECORDER_WIFI_PASSWORD (required)
#   Optional fallback network: $RECORDER_WIFI_SSID2 + $RECORDER_WIFI_PASSWORD2
#   (the first network is tried first; the firmware switches after 3 failed
#   attempts in a row and alternates until one connects)
#
# Credentials go only into build-wifi/ (gitignored): a 0600 defaults file
# and that build's own sdkconfig. The committed sdkconfig.defaults and the
# normal build/ stay credential-free. Nothing secret is printed.
#
#   Upload service (optional; all local-only, never commit them):
#     $RECORDER_UPLOAD_URL         https://... base URL of the upload-api
#     $RECORDER_UPLOAD_DEVICE_ID   device id registered with infra/device_token.py
#                                  (empty = the board's Wi-Fi MAC)
#     $RECORDER_UPLOAD_TOKEN_FILE  file holding that device's token (0600)
#     $RECORDER_UPLOADS_START_HELD=1  hold uploads at boot until "uploads resume"
#                                  is typed on the serial console (one-off images)
#     $RECORDER_SD_ENCRYPTION=0    plaintext SD (for a service with no recording key)
#
# Usage: tools/build-with-wifi.sh [bundle_dir]
#   bundle_dir defaults to /workspace/recorder-flash-hosyond35-wifi (a .zip is made next to it).
#
# Board: the Hosyond 3.5" by default (build-wifi-hosyond35/). RECORDER_BOARD=crowpanel43
# builds the unsupported CrowPanel 4.3 into build-wifi/ with the bundle defaulting
# to /workspace/recorder-flash-wifi.
set -euo pipefail
umask 077

cd -- "$(dirname -- "$0")/.."
PROJ="$PWD"
BOARD="${RECORDER_BOARD:-}"
case "$BOARD" in
    crowpanel43)
        echo "warning: crowpanel43 is unsupported (community only)" >&2
        OUT_DIR="${1:-/workspace/recorder-flash-wifi}"
        BUILD="$PROJ/build-wifi"
        BOARD_ARGS=(-D RECORDER_BOARD=crowpanel43)
        ;;
    ""|hosyond35)
        OUT_DIR="${1:-/workspace/recorder-flash-hosyond35-wifi}"
        BUILD="$PROJ/build-wifi-hosyond35"
        BOARD_ARGS=(-D RECORDER_BOARD=hosyond35)
        ;;
    *)
        echo "error: unknown RECORDER_BOARD '$BOARD' (hosyond35; unsupported: crowpanel43)" >&2
        exit 1
        ;;
esac

ssid="${RECORDER_WIFI_SSID:-}"
if [ -z "$ssid" ] && [ -r "$HOME/.config/recorder/wifi_ssid" ]; then
    ssid="$(tr -d '\r\n' < "$HOME/.config/recorder/wifi_ssid")"
fi
if [ -z "$ssid" ]; then
    echo "error: set RECORDER_WIFI_SSID or create ~/.config/recorder/wifi_ssid" >&2
    exit 1
fi
if [ -z "${RECORDER_WIFI_PASSWORD:-}" ]; then
    echo "error: RECORDER_WIFI_PASSWORD is not set in this process" >&2
    exit 1
fi
if [ "${#ssid}" -gt 32 ] || [ "${#RECORDER_WIFI_PASSWORD}" -gt 63 ]; then
    echo "error: SSID must be <=32 and password <=63 characters" >&2
    exit 1
fi

ssid2="${RECORDER_WIFI_SSID2:-}"
pass2="${RECORDER_WIFI_PASSWORD2:-}"
if [ -n "$ssid2" ] && [ -z "$pass2" ]; then
    echo "error: RECORDER_WIFI_SSID2 is set but RECORDER_WIFI_PASSWORD2 is not" >&2
    exit 1
fi
if [ "${#ssid2}" -gt 32 ] || [ "${#pass2}" -gt 63 ]; then
    echo "error: SSID2 must be <=32 and password2 <=63 characters" >&2
    exit 1
fi

# Kconfig string escaping: backslash and double quote.
esc() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

# Optional device upload token (normally installed from the SD card instead):
#   $RECORDER_UPLOAD_TOKEN, else ~/.config/recorder/device_token (0600).
upload_token="${RECORDER_UPLOAD_TOKEN:-}"
if [ -z "$upload_token" ] && [ -n "${RECORDER_UPLOAD_TOKEN_FILE:-}" ]; then
    upload_token="$(tr -d '\r\n' < "$RECORDER_UPLOAD_TOKEN_FILE")"
fi
if [ -z "$upload_token" ] && [ -r "$HOME/.config/recorder/device_token" ]; then
    upload_token="$(tr -d '\r\n' < "$HOME/.config/recorder/device_token")"
fi
if [ -n "$upload_token" ] && ! printf '%s' "$upload_token" | grep -Eq '^[A-Za-z0-9_-]{20,128}$'; then
    echo "error: upload token must be 20-128 URL-safe characters" >&2
    exit 1
fi

upload_url="${RECORDER_UPLOAD_URL:-}"
if [ -n "$upload_url" ] && ! printf '%s' "$upload_url" | grep -Eq '^https://[A-Za-z0-9.-]+(:[0-9]+)?$'; then
    echo "error: RECORDER_UPLOAD_URL must be https://host (no path, no query)" >&2
    exit 1
fi
upload_dev="${RECORDER_UPLOAD_DEVICE_ID:-}"
if [ -n "$upload_dev" ] && ! printf '%s' "$upload_dev" | grep -Eq '^[a-z0-9][a-z0-9-]{0,31}$'; then
    echo "error: RECORDER_UPLOAD_DEVICE_ID must be lowercase letters/digits/dashes, max 32" >&2
    exit 1
fi

mkdir -p "$BUILD"
chmod 700 "$BUILD"
CREDS="$BUILD/sdkconfig.defaults.wifi"
{
    printf 'CONFIG_RECORDER_WIFI_SSID="%s"\n' "$(esc "$ssid")"
    printf 'CONFIG_RECORDER_WIFI_PASSWORD="%s"\n' "$(esc "$RECORDER_WIFI_PASSWORD")"
    printf 'CONFIG_RECORDER_WIFI_SSID2="%s"\n' "$(esc "$ssid2")"
    printf 'CONFIG_RECORDER_WIFI_PASSWORD2="%s"\n' "$(esc "$pass2")"
    printf 'CONFIG_RECORDER_UPLOAD_TOKEN="%s"\n' "$upload_token"
    if [ -n "$upload_url" ]; then printf 'CONFIG_RECORDER_UPLOAD_URL="%s"\n' "$upload_url"; fi
    if [ -n "$upload_dev" ]; then printf 'CONFIG_RECORDER_UPLOAD_DEVICE_ID="%s"\n' "$upload_dev"; fi
    if [ "${RECORDER_UPLOADS_START_HELD:-0}" = 1 ]; then echo 'CONFIG_RECORDER_UPLOADS_START_HELD=y'; fi
    if [ "${RECORDER_SD_ENCRYPTION:-1}" = 0 ]; then echo '# CONFIG_RECORDER_SD_ENCRYPTION is not set'; fi
} > "$CREDS"
chmod 600 "$CREDS"
# Regenerate this build's sdkconfig so changed credentials always apply.
rm -f "$BUILD/sdkconfig"

if ! command -v idf.py >/dev/null 2>&1; then
    # shellcheck disable=SC1091
    . "${IDF_PATH:-$HOME/esp/esp-idf}/export.sh" >/dev/null 2>&1
fi

echo "building with Wi-Fi SSID \"$ssid\" (password not shown) into $(basename "$BUILD")/"
if [ -n "$ssid2" ]; then
    echo "fallback Wi-Fi SSID \"$ssid2\" (password not shown)"
fi
if [ -n "$upload_token" ]; then
    echo "upload token compiled in (not shown)"
else
    echo "no upload token compiled in (install one via /sd/config/upload_token.txt)"
fi
[ -n "$upload_url" ] && echo "upload service: $upload_url"
[ -n "$upload_dev" ] && echo "upload device id: $upload_dev"
[ "${RECORDER_UPLOADS_START_HELD:-0}" = 1 ] && echo "uploads HELD at boot until 'uploads resume' (serial)"
[ "${RECORDER_SD_ENCRYPTION:-1}" = 0 ] && echo "SD encryption OFF for this build"
idf.py -B "$BUILD" -D SDKCONFIG="$BUILD/sdkconfig" ${BOARD_ARGS[@]+"${BOARD_ARGS[@]}"} -D RECORDER_EXTRA_DEFAULTS="$CREDS" build

# Flash bundle (same layout as /workspace/recorder-flash).
mkdir -p "$OUT_DIR"
cp "$BUILD/bootloader/bootloader.bin" "$BUILD/partition_table/partition-table.bin" \
   "$BUILD/ota_data_initial.bin" "$BUILD/recorder.bin" "$OUT_DIR/"
sed 's#[^ ]*/##' "$BUILD/flash_args" > "$OUT_DIR/flash_args"
cat > "$OUT_DIR/flash.sh" <<'FLASH'
#!/usr/bin/env bash
set -euo pipefail
: "${1:?usage: $0 /dev/ttyACM0}"
cd -- "$(dirname -- "$0")"
python3 -m esptool --chip esp32s3 -p "$1" -b 460800 --before default_reset --after hard_reset write_flash @flash_args
FLASH
chmod 755 "$OUT_DIR/flash.sh"
chmod 644 "$OUT_DIR"/*.bin "$OUT_DIR/flash_args"
( cd "$(dirname "$OUT_DIR")" && rm -f "$(basename "$OUT_DIR").zip" && \
  python3 -c "import shutil,sys; shutil.make_archive(sys.argv[1],'zip','.',sys.argv[1])" "$(basename "$OUT_DIR")" )
echo "bundle: $OUT_DIR and $OUT_DIR.zip (contains the Wi-Fi password in recorder.bin; do not share)"
