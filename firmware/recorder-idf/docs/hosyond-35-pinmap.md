# Hosyond ESP32-S3 3.5" — pin map and sources

Board: Hosyond "ESP32-S3 3.5 inch IPS" (Amazon ASIN B0H28X8SQ4). It is the LCDWIKI **ES3C35P** board (QDtech/LCDWIKI design).
Build it with `-D RECORDER_BOARD=hosyond35`. The README section "Board selection" has the commands.

## Panel: 320x480, ST77922, QSPI (not 240x320)

The Amazon title says 240x320. That is wrong for this board. Every vendor source for this exact 3.5" model gives 320x480:

| Source | What it says |
|---|---|
| Specification V1.0 PDF (QDtech, drawing dated 2025-07-24) | "Driver IC ST77922", "Screen resolution 320xRGBx480", "INTERFACE: QSPI" |
| User manual PDF | "driving IC of ST77922, resolution of 320x480, QSPI" |
| LCDWIKI page "3.5inch ESP32-S3 Display" | ST77922, 320x480, QSPI |
| Vendor demo `display.h` | `EXAMPLE_LCD_QSPI_H_RES 320`, `EXAMPLE_LCD_QSPI_V_RES 480` |
| Vendor init table (`esp_bsp.c`) | CASET 0..0x13F (320 columns), RASET 0..0x1DF (480 rows) |
| Device on the bench (2026-09-30) | The touch controller reports "Max.X: 320, Max.Y: 480" |

The panel is 320 wide and 480 tall in its native portrait orientation. The recorder turns it to landscape (480x320) in software so it can reuse the CrowPanel layout.

## Chip

The board uses an ESP32-S3R8 (QFN56, rev v0.2) with 8 MB **octal** PSRAM (AP, 3.3 V). Flash is 16 MB quad SPI, a Zbit part (0x5E/0x4018).
All of this was read from the device with `esptool flash-id` and matches the boot log (`octal_psram: ... 64 Mbit`, `Found 8MB PSRAM device`).

The only USB port is the S3's own USB-Serial/JTAG. The board has no USB-UART bridge.
Base MAC: 14:c1:9f:d1:a7:44, which gives device id `14c19fd1a744`.

## Pins

| Function | GPIO | Notes |
|---|---|---|
| LCD QSPI CS | 10 | ST77922, SPI2, 40 MHz |
| LCD QSPI SCK | 12 | |
| LCD QSPI D0 / D1 / D2 / D3 | 11 / 13 / 14 / 9 | |
| LCD TE | 42 | wired, not used by the recorder |
| LCD reset | — | tied to CHIP_PU (EN); a software reset (0x01) is used |
| Backlight | 41 | active high through a BSS138; 5 kHz LEDC PWM |
| I2C SDA / SCL | 38 / 39 | shared by touch and codec |
| Touch (built into ST77922) | I2C 0x55 | same protocol as espressif `esp_lcd_touch_st7123` |
| Touch RST | 48 | low 100 ms, then wait at least 150 ms (vendor timing) |
| Touch INT | 47 | not used (polled, like the CrowPanel build) |
| ES8311 codec | I2C 0x18 | chip id 0x83 0x11 read on the bench |
| I2S MCLK / BCLK / WS | 17 / 18 / 21 | I2S1, 16 kHz, MCLK = 384 x fs = 6.144 MHz |
| I2S DOUT (ESP -> codec) | 15 | unused (no playback) |
| I2S DIN (codec -> ESP) | 16 | microphone data |
| Microphone | — | analog bottom-port MEMS (LMA2718B381) into ES8311 MIC1 |
| Speaker amp shutdown | 1 | HIGH = amp off; held off at all times |
| microSD CLK / CMD | 5 / 4 | SDMMC 4-bit, 10 k pull-ups on the board |
| microSD D0 / D1 / D2 / D3 | 6 / 7 / 2 / 3 | |
| RGB LED | 40 | documented only, not used |
| Battery ADC | 8 | ADC1_CH7, 100K/100K divider (x2); battery gauge since 0.7.0 (`main/battery.c`). TP4054 CHRG is not wired to a GPIO; no USB 5 V sense pin |
| BOOT (KEY2) | 0 | 10K pull-up (R10), pressed = low; RTC pin, deep-sleep wake since 0.7.0 |
| UART0 TX / RX (4-pin header) | 43 / 44 | ROM log and a fallback download path; the app logs to USB |
| BOOT key | 0 | |

The board has **no RTC chip**. The clock is set only from Wi-Fi (NTP) and is lost at power-off.

## Sources

1. LCDWIKI page "3.5inch ESP32-S3 Display" (ES3C35P): https://www.lcdwiki.com/3.5inch_ESP32-S3_Display
2. Schematic "ESP32-S3原理图.pdf", 3.5inch_ESP32-S3_board, dated 2026-01-30: https://www.lcdwiki.com/res/ES3C35P/ESP32-S3%E5%8E%9F%E7%90%86%E5%9B%BE.pdf
3. IO allocation table "ESP32-S3_IO资源分配表.xlsx": https://www.lcdwiki.com/res/ES3C35P/ESP32-S3_IO%E8%B5%84%E6%BA%90%E5%88%86%E9%85%8D%E8%A1%A8.xlsx
4. User manual: https://www.lcdwiki.com/res/ES3C35P/3.5inch_IPS_ESP32-S3_User_Manual.pdf
5. Specification V1.0: https://www.lcdwiki.com/res/ES3C35P/3.5inch_IPS_ESP32-S3_Specification_V1.0.pdf
6. Vendor demo code. The official package is a Baidu download. We used a third-party GitHub mirror of it: github.com/ydedox/st77922, commit 5eb6f7b (2026-06-23). The files are `Example_08_LVGL_Demos/LVGL_Demos/esp_bsp.{c,h}` (pins, QSPI init table, touch config and reset timing) and `display.h` (resolution). The ST77922 init table in `main/display_st77922.c` is copied verbatim from `esp_bsp.c`.
7. espressif/arduino-esp32 issue #12694, "Need Arduino example for ESP32-S3 3.5 inch ST77922 QSPI display". A comment dated 2026-09-07 confirms the same QSPI pins and ST77922.
8. The device itself, on 2026-09-30: `esptool flash-id` plus the boot log (PSRAM, flash, ES8311 id, touch firmware and max X/Y, SD mount).

## Where the sources disagree

- **Touch controller.** The user manual text names an FT6336G. The schematic, the vendor code and the device all show the touch block inside the ST77922 at 0x55. The device answers the st7123 protocol with "Firmware version 3 (1.6.1.17)". The FT6336G text is a leftover from another board.
- **Touch INT / RST.** The IO xlsx swaps them. The schematic and vendor code give RST = 48 and INT = 47. On the device, touch answered once GPIO 48 was pulsed with the vendor's reset timing.
- **RGB LED.** One document says GPIO 42. The schematic shows 42 as LCD TE and the LED on 40. The LED is not used either way.
- **Speaker amp part.** The documents call it FM8002E in one place and SC8002B in another. Both are 8002-class amps with an active-high shutdown pin, and GPIO 1 is held high (amp off).
- **Resolution.** The Amazon title says 240x320; the correct value is 320x480 (see above).
- **PSRAM.** It is octal, not quad. "Quad" in the esptool output refers to the flash.
