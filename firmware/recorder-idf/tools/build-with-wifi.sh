#!/usr/bin/env bash
# Local-only build with Wi-Fi credentials (used only for NTP clock sync)
# and optional local branding (local/branding.json, gitignored).
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

# Optional local branding (gitignored): local/branding.json
#   {"school_name": "Big School\nName", "badge_text": "BS"}
# or env RECORDER_BRAND_NAME / RECORDER_BRAND_BADGE (env wins). A newline
# (or the two characters \n) in the name forces a two-line header.
brand_name="${RECORDER_BRAND_NAME:-}"
brand_badge="${RECORDER_BRAND_BADGE:-}"
BRAND_FILE="$PROJ/local/branding.json"
if [ -r "$BRAND_FILE" ]; then
    if [ -z "$brand_name" ]; then
        brand_name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("school_name","").replace("\n","\\n"), end="")' "$BRAND_FILE")"
    fi
    if [ -z "$brand_badge" ]; then
        brand_badge="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("badge_text",""), end="")' "$BRAND_FILE")"
    fi
fi
brand_name="${brand_name//$'\n'/\\n}"   # real newline -> the two characters \n
if [ -n "$brand_badge" ] && ! printf '%s' "$brand_badge" | grep -Eq '^[!-~]{1,3}$'; then
    echo "error: badge_text must be 1-3 printable ASCII characters" >&2
    exit 1
fi

# Optional device upload token (normally installed from the SD card instead):
#   $RECORDER_UPLOAD_TOKEN, else ~/.config/recorder/device_token (0600).
upload_token="${RECORDER_UPLOAD_TOKEN:-}"
if [ -z "$upload_token" ] && [ -r "$HOME/.config/recorder/device_token" ]; then
    upload_token="$(tr -d '\r\n' < "$HOME/.config/recorder/device_token")"
fi
if [ -n "$upload_token" ] && ! printf '%s' "$upload_token" | grep -Eq '^[A-Za-z0-9_-]{20,128}$'; then
    echo "error: upload token must be 20-128 URL-safe characters" >&2
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
    printf 'CONFIG_RECORDER_BRAND_NAME="%s"\n' "$(esc "$brand_name")"
    printf 'CONFIG_RECORDER_BRAND_BADGE="%s"\n' "$(esc "$brand_badge")"
    printf 'CONFIG_RECORDER_UPLOAD_TOKEN="%s"\n' "$upload_token"
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
if [ -n "$brand_name$brand_badge" ]; then
    echo "local branding override applied (from env or local/branding.json)"
fi
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
