# Classroom recorder (ESP-IDF)

> **Supported hardware: the Hosyond ESP32-S3 3.5" (LCDWIKI ES3C35P) only.** It is the default build. The Elecrow CrowPanel Advance 4.3" code is still in the tree but is **unsupported (community only)**: it gets no OTA update channel and no support, and it is built only with `-D RECORDER_BOARD=crowpanel43`.

Classroom recorder firmware for ESP32-S3 boards (16 MB flash, 8 MB octal PSRAM). It was first written for the CrowPanel layout. It shows the approved 800×480 LVGL screens, records 16 kHz mono 16-bit WAV from the on-board PDM mic, and writes a JSON sidecar. With Wi-Fi and a device token it uploads finished recordings to the Goalvera upload-api and deletes the SD copy only after a verified receipt.

`idf.py build` for `esp32s3` (Hosyond by default) is the check that runs in CI. A person with the board still needs to walk the checklist at the bottom.

`firmware/mic_sd_test/` is the proven Arduino mic/SD sketch and is left unchanged. Pin maps come from that sketch and from Elecrow's V1.1–V1.3 ESP-IDF lessons, not from `docs/design-spec.md` (that doc lists different pins).

## What it does

- Home, recording, paused, and end-confirm screens. Lists (classrooms, teachers, class codes) come from `/sd/config/device.json`, or from the built-in sample if that file is missing or invalid. The UI does not hard-code those names.
- Recordings: `/sd/rec/YYYYMMDD_HHMMSS_<classroomcode>.wav` plus a `.json` sidecar (start time, duration, classroom, teacher, `class_code` exactly as in the cloud file name, e.g. `Reading-3A`, plus `class_label` as shown on screen, device id from the Wi-Fi MAC, `firmware_version`, markers). Since 0.6.8 the recording screen has no Mark button and no Teacher/Class lines: the red-bordered session card runs the full width and holds three big marker buttons, **Incident**, **Teacher Notes** and **Bug** (Other and Photo are gone). The time is captured at the tap. Incident and Bug then ask OK / Cancel ("Mark an incident?" / "Acknowledge"); OK stores the marker at the tap time, Cancel stores nothing. Teacher Notes stores at once. Each stored marker shows a short toast ("Incident marked at 1:02 PM"; Bug: "Bug noted, sent to support with this recording") and a Recent activity line; a second tap on the same button within 1 s is ignored. Markers are written as `{"type", "offset_ms", "wall_time"}` with types `Incident`, `Teacher Notes` (verbatim, as before) and `bug`; the review site files `bug` for the support team and keeps it out of incidents and PDFs. Teacher and class are still saved in the sidecar exactly as before (last-used values, changeable in the End dialog). The firmware version is set once, as `PROJECT_VER` in `CMakeLists.txt`. It is printed at boot and, since 0.6.1, shown as a small muted `v0.6.2`-style caption at the right end of the home screen's **Recent activity** title line. No student names, and no code-to-student map. A `students` or `student_map` key in the JSON is ignored.
- The SD card is never formatted. If it is missing or not mountable, the screen says so and Record stays disabled. Free space under 2 hours of audio is called out as low; under 30 seconds disables Record.
- Audio capture and the WAV writer are separate FreeRTOS tasks with a PSRAM ring buffer. Pause drops samples, so paused time is not in the file. Save rewrites the WAV header and the sidecar.
- If Wi-Fi credentials were set at build time, the device connects and sets the RTC from SNTP (`America/New_York`). With no credentials it stays offline and uploads are off. See **Uploads** below.
- The backlight dims after 60 seconds of no touch on the home screen. It stays bright while a session is recording, paused, saving, or showing the end dialog. It also stays bright if the touch controller never came up, so a bench unit without touch remains readable.
- Hosyond recordings are encrypted on the card (APENC1, `docs/sd-encryption.md`). CrowPanel recordings stay plaintext. Flash and NVS encryption are an opt-in image (`sdkconfig.defaults.secure`), not the normal USB flash. The last-used teacher (and room and class) are stored in NVS.

## Uploads

- `main/uploader.c` runs only while no session is active (idle for 10 s; a new Record press aborts an upload and keeps the file). Oldest recording first.
- Flow per recording (`.wav` then its `.json`): hash on the device (size, MD5, CRC32C) → `POST /v1/uploads` (Bearer device token) → `PUT` to the one-time signed URL with exactly the returned headers → `POST /v1/confirm`.
- The SD copy is deleted only if `verified` is true, the receipt HMAC-SHA256(token, `object|generation|size|md5|crc32c|verified`) matches, and the stored size/MD5/CRC32C equal the local values. Anything else keeps the file; network and server errors retry after 1, 2, 5, 10, then every 15 min.
- Recent activity shows `Uploading 12:58 PM recording (Room B)` (today's start time in Eastern 12-hour form, plus the classroom label; another day is `Uploading Oct 7, 2:59 PM recording (Room B)`; a `NOTIME_…` file is `Uploading recording (time not set)`; no classroom drops the parentheses), then `25% complete` … `100% complete`, `Upload receipt verified: 12:58 PM recording`, `SD copy deleted`, or `Upload failed: <why>. Kept on SD, retry in N min (12:58 PM recording)`. A message too long for one row wraps onto a second row (at most two) and pushes the older rows down, so the whole text stays readable on both boards; rows that no longer fit drop off the bottom. A 401 from the upload service reads `Upload failed: device not authorized. Check upload token. Kept on SD, retry in 1 min (12:58 PM recording)`. The storage card shows `Uploading N%`, `N waiting to upload` or `All recordings uploaded`.
- Cloud object name: `rec/<device>/<YYYYMMDD_HHMMSS>_<Class code>.wav` (Class code cleaned to `[A-Za-z0-9-]`, max 16, which is what the pipeline roster is keyed by). `NOTIME_…` recordings are renamed with the current time once the clock is set.
- Device id: the Wi-Fi MAC (`device a4cb…` in the boot log) unless `CONFIG_RECORDER_UPLOAD_DEVICE_ID` is set.
- Token: create it with `infra/device_token.py create <device id> --out <SD card>/config/upload_token.txt` (pipeline branch). At the next boot the firmware stores it in NVS, overwrites and deletes the file, and logs `Upload token installed from SD card`. `tools/build-with-wifi.sh` can alternatively compile one in from `$RECORDER_UPLOAD_TOKEN` or `~/.config/recorder/device_token`. NVS and flash are not encrypted yet.
- TLS buffers are in PSRAM (`CONFIG_MBEDTLS_EXTERNAL_MEM_ALLOC`), as are the uploader's stack and buffers; the boot/after-upload logs print internal free and the minimum ever.

## Build

ESP-IDF v5.4.x (CI uses `espressif/idf:v5.4.2`). LVGL 9.2.2 and the GT911 touch component come from the IDF component manager (`main/idf_component.yml`).

```bash
cd firmware/recorder-idf
. $IDF_PATH/export.sh          # or . ~/esp/idf/export.sh
idf.py set-target esp32s3
idf.py build
```

`set-target` is only needed once. It writes `sdkconfig` from `sdkconfig.defaults` (and from `sdkconfig.defaults.local` when that file exists).

The image is large because of LVGL, Wi-Fi, and the fonts. The partition table has two 6 MB OTA slots (`ota_0`, `ota_1`) plus `otadata`; the sizes are the same as 0.7.x. The OTA client checks a signed manifest when the recorder is idle (`docs/ota.md`). The home screen shows the version (`v0.8.2`) at the right end of Recent activity. A blank `otadata` partition boots `ota_0`. Wi-Fi passwords stay out of git.

## Board selection

The supported board, and the default, is the **Hosyond ESP32-S3 3.5"** (LCDWIKI ES3C35P). It has a 320x480 ST77922 QSPI panel with built-in touch, an ES8311 codec with an analog MEMS mic, SDMMC microSD, and no RTC. The build needs a recording public key (`-D RECORDING_PUBKEY_PEM=...`, see `docs/sd-encryption.md`). Build it in its own directory:

```bash
idf.py -B build-hosyond35 -D SDKCONFIG=build-hosyond35/sdkconfig -D RECORDER_BOARD=hosyond35 build
# or build it and make a flash bundle in /workspace/recorder-flash-hosyond35:
tools/build-hosyond35.sh
# with Wi-Fi for the clock (bundle in /workspace/recorder-flash-hosyond35-wifi):
RECORDER_BOARD=hosyond35 RECORDER_WIFI_PASSWORD=... tools/build-with-wifi.sh
```

How the option works:

- `RECORDER_BOARD=hosyond35` adds `sdkconfig.defaults.hosyond35`, which selects `CONFIG_RECORDER_BOARD_HOSYOND_35`, the USB-Serial/JTAG console and DIO flash.
- Leaving `RECORDER_BOARD` out gives the Hosyond. Passing `crowpanel43` gives the **unsupported** Elecrow CrowPanel Advance 4.3" (800x480 RGB, GT911; `sdkconfig.defaults.crowpanel43`, with a warning and no update channel). Any other value stops the build with an error.
- Defaults only apply when an sdkconfig is created, so keep one build directory and one sdkconfig per board.

The pins, parts and sources are in `docs/hosyond-35-pinmap.md`. What the port changes and what is still untested is in `docs/hosyond-35-port-report.md`.

Hosyond-only menuconfig options are under Classroom recorder:

- rotation, 90 or 270 degrees
- touch mirror X and Y, in native panel axes
- RGB565 byte swap

On the Hosyond, the LCD reset is tied to EN. If the screen stays black after flashing, unplug USB for about 10 seconds.

### Hosyond battery gauge and auto sleep (0.7.0)

The Hosyond runs on a 1-cell LiPo on its 2-pin 1.25 mm battery socket (TP4054 charger, ~290 mA from USB; a 3000 mAh pack takes about 11-12 h to fill). The top-right chip on the home and session screens (the CrowPanel keeps its fixed "USB power" chip):

- **Since 0.7.1 the chip is a battery gauge, with no text or number** (`main/batt_gauge.h`; preview `docs/ui-mockups/battery-chip-0.7.1.png`): a battery outline whose fill bar spans the inside in proportion to the %, **green above 25%, yellow 10-25%, red below 10%**. A "Battery low" message shows once when it turns red (below 10%).
- **Lightning bolt** on the gauge when USB power is present: a computer is connected (USB-Serial-JTAG sees the host), or the voltage pattern says a wall charger or power bank is plugged in. The fill then shows a rough charging estimate (the reading minus ~70 mV). No battery fitted: empty outline with the bolt.
- Charging is a guess: no pin senses USB 5 V on this board and the charger's status pin isn't wired to the ESP32, so it is read from the voltage: plugging in steps the reading up within seconds, a slow rise or a reading held at 4.18 V+ also counts. Booting while already on a charger may show the gauge without the bolt for a few minutes first.

How the % is made (`main/battery_model.h`, tested by `test/host/test_battery.c`): GPIO8 (ADC1_CH7, 100K/100K divider, x2) is read every 2 s (32-sample mean, eFuse curve calibration) and smoothed over ~30 s; 40 mV is added back while recording or uploading (load sag); a 1-cell LiPo resting curve maps 4.20 V = 100% down to 3.45 V = 0% (the 3.3 V LDO drops out below ~3.4 V); on battery the % only rises again after a real 50 mV rise. The serial log (tag `battery`) prints the voltage, %, source and load every 30 s and on every change.

Settings (menuconfig, Classroom recorder, Hosyond only):

- `RECORDER_BATTERY_AUTOSAVE` (default on): at 5% on battery, a running or paused recording is saved and finished on its own, and Record stays disabled ("Battery low: plug in USB power") until it's charging. Turn it off to keep only the gauge and the 15% warning.
- `RECORDER_BATTERY_CAL_PERMILLE` (default 1000 = 1.0): per-board correction. Measure the battery with a multimeter, then set it to multimeter mV / logged `vbat` mV x 1000.
- `RECORDER_AUTO_SLEEP_MIN` (default 60, 0 = never): after this many minutes with no recording and no touch (and not plugged into a computer), a "Going to sleep" card counts down 30 s ("Tap to stay on"). Then no new upload starts, a running upload finishes (up to 15 min; a file is only ever deleted after a verified receipt), the backlight and panel turn off, the codec powers down, Wi-Fi stops and the S3 enters deep sleep. **Press BOOT to wake** (GPIO0, KEY2; RESET also works and does a normal boot). Tap-to-wake isn't possible: the touch interrupt is GPIO47, which can't wake deep sleep.

Deep sleep is not zero. Parts the firmware can't switch off stay on the battery: the WS2812B RGB LED (~0.5-1 mA idle), the SD card (~0.1-1.5 mA idle, card dependent), the panel and its built-in touch controller (panel in sleep mode; touch has no sleep command here, ~0.05-1 mA), two ME6217 LDOs (~0.1 mA), the 200K divider (~20 uA), the S3 itself (~10-20 uA with BOOT wake) and the codec, amp and charger (a few uA). Estimate: about 1-3 mA, so a full 3000 mAh pack lasts roughly 1-3 months asleep (vs. roughly 10-16 h awake). Not measured yet: put a meter in series with the battery after the screen goes dark to pin it down.

## Flash

USB cable to the panel. The port is often `/dev/ttyACM0` or `/dev/cu.usbmodem*`.

```bash
idf.py -p <port> flash monitor
```

Leave the monitor with Ctrl-].

`idf.py flash` prints `--flash_mode dio`. That is ESP-IDF 5.4's compatible image header: `sdkconfig.defaults` still selects QIO, and auto-detect switches to QIO when the flash chip supports it.

Rear DIP switch **S1=1, S0=1** ("MIC & TF Card"). Use a FAT32 microSD. Do not set any format-on-fail flag; this project has none.

## Wi-Fi without committing it

Credentials are Kconfig strings, compiled into the app image. They are empty in git.

Up to two compiled-in networks: `CONFIG_RECORDER_WIFI_SSID`/`_PASSWORD` and the optional `CONFIG_RECORDER_WIFI_SSID2`/`_PASSWORD2` (with `tools/build-with-wifi.sh`: `RECORDER_WIFI_SSID2` + `RECORDER_WIFI_PASSWORD2`). After 3 failed attempts in a row the recorder moves to the next known network and keeps going round until one connects. The boot log names the SSIDs, never the passwords.

**Network picker (0.6.3).** The status bar shows only the Wi-Fi signal icon (green bars; grey bars with a red x when not connected; climbing amber bars while switching). Tap it for the list of networks in range: the connected one first, then saved ones, then the rest by signal strength, then saved networks the scan did not see. A saved network (compiled in or joined on the device) switches in one tap; an open network joins in one tap; any other network asks for its password on the on-screen keyboard (Show/Hide, Connect, Cancel). A network joined this way is saved in NVS (up to 4, oldest replaced first; keys `wf_s<n>`/`wf_p<n>`) only after it connects, and the network last picked on screen (`wf_pref`) is tried first after a reboot. A switch that fails (2 attempts or 25 s) goes back to the network it was on, or the normal rotation, so the recorder does not stay offline. Scans and switches run on the system event task; the UI only polls, so recording and uploads carry on while the picker is open. The picker closes itself after a minute without a touch. NVS is not encrypted yet, the same as the compiled-in passwords in the app image.

Option A, a local defaults file (preferred):

```bash
cp sdkconfig.defaults.local.example sdkconfig.defaults.local
# edit the two CONFIG_RECORDER_WIFI_* lines, then:
rm -f sdkconfig sdkconfig.old
idf.py set-target esp32s3
idf.py build
```

`sdkconfig.defaults.local` is listed after `sdkconfig.defaults`, so it wins. Both `sdkconfig` and `sdkconfig.defaults.local` are gitignored.

Option B, menuconfig: `idf.py menuconfig` → Classroom recorder. Do not run `idf.py save-defconfig` afterward, or a later commit could pick up the password. Delete `sdkconfig` to go back to the committed defaults.

The password is in the `.bin` you flash. Flash encryption is not on. Do not share that binary, and do not commit `sdkconfig`. The firmware does not log the password.

## Serial console (0.8.2)

Type commands on the USB serial port (the same port as the log; any baud rate works). Nothing runs unless typed.

| Command | What it does |
|---|---|
| `help` | list the commands |
| `version` | firmware version and device id |
| `status` | upload URL host, device id, token source, waiting files / minutes, holds, last upload error |
| `queue` | list every file in `/sd/rec` with size, approx. minutes, and the date range |
| `queue wipe YES` | delete every file in `/sd/rec` (bytes zeroed, then unlinked). Refused while recording or while an upload runs. Leaves uploads on hold. |
| `uploads hold` / `uploads resume` | stop / allow uploads (separate from auto sleep) |
| `screenshot` | dump the current screen (LVGL snapshot, RGB565, base64 lines with a CRC-32C) |

`tools/screenshot.py /dev/cu.usbmodem101 out.png` sends `screenshot` and writes the PNG (needs `pyserial`; close any other serial monitor first). `--from-log saved.txt` decodes a dump from a saved log instead.

A local one-off image can start with uploads held (`RECORDER_UPLOADS_START_HELD=1 tools/build-with-wifi.sh`, Kconfig `RECORDER_UPLOADS_START_HELD`), so files already on the card do not go to that build's upload service until someone checks `queue` and types `uploads resume`.

## Config JSON

Path on the card: `/sd/config/device.json`. If it is missing, larger than 16 KB, or has no classrooms, the firmware uses `main/sample_device.json` and the status line says `sample lists`.

```json
{
  "schema": 1,
  "classrooms": [
    {"code": "room-104", "label": "Room 104"}
  ],
  "teachers": ["J. Rivera"],
  "classes": ["Reading 3A"]
}
```

- Since 0.8.2 the header is always the goalvera logo (G mark + `goalvera` wordmark from the www.goalvera.app homepage, `main/goalvera_logo.c`, regenerated with `tools/make_logo.py`). `school_name` and `badge_text` in this file are ignored, the admin-settings school name is no longer fetched, and the old `CONFIG_RECORDER_BRAND_*` / `local/branding.json` overrides are gone. No school name is shown on the device.
- `classrooms[].code` is the filename token. Anything other than letters, digits, `-`, and `_` is dropped; spaces become `-`.
- `classes` are neutral codes such as `Reading 3A`. They are not student names.
- The RBT field was removed in 0.6.0. An `rbts` list in an older `device.json` is ignored, and the sidecar has no `rbt` key.
- Do not put a `students` or `student_map` object on the card. The parser skips those keys and logs a warning.
- The sample lists are fictional staff names for the demo, not students.

Sidecar fields: `start_time` (local `America/New_York`, or `null` if the RTC was invalid), `time_valid`, `duration_seconds`, `duration_ms`, `classroom_code`, `classroom_label`, `teacher`, `class_code`, `device_id` (STA MAC, `aabbccddeeff`), `firmware_version`, `sample_rate_hz`, `channels`, `bits_per_sample`, `encryption` (`"none"`).

Hours on the home screen are `free_bytes / 115200000` (16 kHz × 16-bit × 1 channel). The numbers on the mockup are illustrative, not a real card size.

If the RTC is invalid, the home clock says **Time not set**, and a recording is still allowed. The file is named `/sd/rec/NOTIME_<boot_seconds>_<code>.wav` with `"start_time": null`.

## Hardware-verification checklist

Run this on a real board. Items marked CrowPanel apply only to the unsupported CrowPanel Advance 4.3" V1.1.

1. DIP **S1=1, S0=1**. FAT32 microSD inserted. Serial at 115200 after `idf.py -p <port> flash monitor`.
2. Home screen matches `docs/ui-mockups/home-v1.png`: school badge, school name, Wi-Fi icon (signal bars only since 0.6.3; red x when not connected), **USB power** (CrowPanel: there is no battery sensor; the Hosyond shows a battery gauge since 0.7.0), 12-hour clock and date, classroom dropdown, storage in hours, red Record button, two-line status, and the firmware version (`v0.6.1`) at the right end of the Recent activity title line.
3. Pull the card out and reboot. Record is disabled and the status line says the card is missing or not FAT32. Confirm the card was **not** reformatted (files you put on it are still there).
4. Boot with no `/sd/config/device.json`. Dropdowns show the fictional sample (Room 104, J. Rivera, Reading 3A). Then add a JSON with a different room and reboot; the dropdown follows the file.
5. With Wi-Fi set at build time, the icon shows green bars, the Recent activity list says `Wi-Fi connected: <SSID>`, and the log shows SNTP. Tap the icon: the network list opens and fills in within a few seconds; switch to the other saved network in one tap and back; join a new network with the keyboard, reboot, and confirm it reconnects to the network last picked. After a minute the RTC holds that local time across a reboot with the coin cell installed. With an unset or dead RTC and no network, the clock says **Time not set**.
6. Tap Record. The session screen matches `recording-v1.png`. Speak: the meter moves and the log shows a 2.048 MHz PDM clock. The timer counts recorded audio, not wall clock.
7. Pause. The screen matches `recording-paused-v1.png` (amber, frozen timer and meter, Resume). Wait several seconds, resume, then save. The WAV duration should skip the paused gap. Open the file on a computer and confirm it plays.
8. End opens the dialog in `recording-end-confirm-v1.png`. Change the classroom, choose **Keep recording**, and confirm the session screen shows the new room. End again, **Save & finish**. Home returns, the status line says the recording was saved, and `/sd/rec/` has the `.wav` and `.json`. The filename uses the classroom code. No student names in either file.
9. Teacher dropdown opens on the last name you saved (NVS).
10. Leave home idle for about a minute: the backlight dims. Start a recording: it returns to full brightness and does not dim. Touch brings it back on the home screen.
11. Fill the card (or use a nearly full one). Under 2 hours free, the status line says low storage. Under 30 seconds, Record disables.

## Assumptions that need a look on hardware

These were not pinned down by a line in the Elecrow lessons or the mic sketch:

1. The RTC is a standard PCF8563 at I2C `0x51` (VL bit in seconds, time registers from `0x02`, years 2000–2099). Elecrow's lessons that were checked do not show RTC code.
2. Idle backlight command `160` (1 = slightly dimmer than max, 244 = very dim, 245 = off) after 60 s. The scale direction is from Elecrow's 1-byte command description; `160` is a chosen midpoint.
3. RGB565 byte order. If red and blue are swapped on the panel, switch the LVGL format in `display.c` to `LV_COLOR_FORMAT_RGB565_SWAPPED`.
4. GT911 address `0x5D` after STC8 command `250` (Elecrow `main.c`). The code also probes `0x14`. Touch INT and RST are not wired (`GPIO_NUM_NC`), matching Elecrow.
5. SD chip-select GPIO 0 is a dummy. The sketch says the card CS is tied and unused. GPIO 0 is a strapping pin; the sketch still passes it to the SPI driver.
6. The CrowPanel has no battery-voltage ADC; its USB-power chip is a fixed label. (The Hosyond reads its battery since 0.7.0; see above.)
7. The RGB bounce buffer is 10 lines, the Espressif RGB example size. Elecrow's lesson Kconfig was not copied line for line.
8. The end-confirm dialog does not pause the recording. Duration keeps counting until Save & finish. Keep recording closes the dialog.
9. A recording is allowed when the RTC is invalid, under a `NOTIME_` name.
10. No ST7265 command channel. Elecrow's RGB lessons drive a generic 16-bit panel with the LCD peripheral only.
11. The IDF PDM high-pass filter stays at the driver default. The sketch only forced `I2S_PDM_DSR_16S` (2.048 MHz) and ×8 software gain with clipping at ±32767, and discarded ~500 ms. This port does the same.
12. NVS also remembers the last room and class, not only the teacher.
13. Touch swap/mirror flags default to off.

## CI

`.github/workflows/firmware-idf.yml` builds this project on pull requests with `espressif/idf:v5.4.2`:

```bash
idf.py set-target esp32s3 && idf.py build
```

Since 0.7.0 it also builds the Hosyond (`RECORDER_BOARD=hosyond35`) and runs the host unit tests in `test/host/` (battery model, mic meter, upload names, Wi-Fi list) with `cc`.
