#pragma once

#include "driver/gpio.h"
#include "sdkconfig.h"

#if CONFIG_RECORDER_BOARD_HOSYOND_35
#include "board_pins_hosyond35.h"
#else

/*
 * Elecrow CrowPanel Advance 4.3" HMI, hardware V1.1 / V1.2 / V1.3.
 * Pin maps copied from the official ESP-IDF lesson and the proven
 * mic_sd_test sketch. Do not change without re-checking those sources.
 *
 * Display + touch:
 *   example/V1.1_and_V1.2_and_V1.3/IDF_Code/Lesson03-LVGL_Control_LED
 *     main/waveshare_rgb_lcd_port.h
 *     main/main.c  (STC8H1K28 at 0x30, commands 250 and 0)
 *   example/V1.1_and_V1.2_and_V1.3/Arduino/lesson-03/BigInch_LVGL/LovyanGFX_Driver.h
 *
 * Microphone + microSD:
 *   firmware/mic_sd_test/mic_sd_test_main.cpp
 *   (Elecrow lesson-10 PDM mic, lesson-04 SD over SPI)
 *
 * The RGB panel is an 800x480 16-bit parallel bus. Elecrow's working
 * lessons do not bit-bang an ST7265 command channel; the timing
 * controller is driven entirely by the ESP32-S3 LCD peripheral.
 */

#define BOARD_LCD_H_RES 800
#define BOARD_LCD_V_RES 480
#define BOARD_LCD_PCLK_HZ (16 * 1000 * 1000)

#define BOARD_LCD_HSYNC GPIO_NUM_40
#define BOARD_LCD_VSYNC GPIO_NUM_41
#define BOARD_LCD_DE    GPIO_NUM_42
#define BOARD_LCD_PCLK  GPIO_NUM_39

/* DATA0..DATA15. Order is B0-B4, G0-G5, R0-R4 (RGB565, little-endian). */
#define BOARD_LCD_D0  GPIO_NUM_21
#define BOARD_LCD_D1  GPIO_NUM_47
#define BOARD_LCD_D2  GPIO_NUM_48
#define BOARD_LCD_D3  GPIO_NUM_45
#define BOARD_LCD_D4  GPIO_NUM_38
#define BOARD_LCD_D5  GPIO_NUM_9
#define BOARD_LCD_D6  GPIO_NUM_10
#define BOARD_LCD_D7  GPIO_NUM_11
#define BOARD_LCD_D8  GPIO_NUM_12
#define BOARD_LCD_D9  GPIO_NUM_13
#define BOARD_LCD_D10 GPIO_NUM_14
#define BOARD_LCD_D11 GPIO_NUM_7
#define BOARD_LCD_D12 GPIO_NUM_17
#define BOARD_LCD_D13 GPIO_NUM_18
#define BOARD_LCD_D14 GPIO_NUM_3
#define BOARD_LCD_D15 GPIO_NUM_46

#define BOARD_I2C_SDA GPIO_NUM_15
#define BOARD_I2C_SCL GPIO_NUM_16
#define BOARD_I2C_PORT I2C_NUM_0

#define BOARD_STC8_ADDR  0x30
#define BOARD_GT911_ADDR 0x5D
#define BOARD_GT911_ADDR_ALT 0x14
#define BOARD_RTC_ADDR   0x51

/* STC8H1K28 one-byte commands (Elecrow main.c + mic_sd_test). */
#define BOARD_CMD_BACKLIGHT_MAX 0     /* brightest */
#define BOARD_CMD_BACKLIGHT_OFF 245
#define BOARD_CMD_TOUCH_ON      250
#define BOARD_CMD_MIC_PATH      2
#define BOARD_CMD_MUTE_AMP      249

/* Dim level while the home screen is idle. 1..244, higher is dimmer. */
#define BOARD_BACKLIGHT_DIM 160
#define BOARD_DIM_AFTER_MS  60000

#define BOARD_MIC_CLK  GPIO_NUM_19
#define BOARD_MIC_DATA GPIO_NUM_20

#define BOARD_SD_SCK  GPIO_NUM_5
#define BOARD_SD_MISO GPIO_NUM_4
#define BOARD_SD_MOSI GPIO_NUM_6
/* Elecrow lesson-04 passes CS GPIO 0. The card CS is tied and this pin
 * is not wired to the socket; the working sketch still uses it. */
#define BOARD_SD_CS   GPIO_NUM_0

#define BOARD_SD_MISSING_MSG "No SD card: insert a FAT32 card, set DIP S1=1 S0=1, restart"

/* 16 kHz mono 16-bit = 32000 bytes/s = 1.92 MB/min = 115200000 bytes/hour. */
#define REC_SAMPLE_RATE    16000
#define REC_BITS           16
#define REC_CHANNELS       1
#define REC_SW_GAIN        8
#endif /* board */

#define REC_BYTES_PER_SEC  (REC_SAMPLE_RATE * (REC_BITS / 8) * REC_CHANNELS)
#define REC_BYTES_PER_HOUR (115200000ULL)
