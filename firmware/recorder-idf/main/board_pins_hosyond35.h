#pragma once
/*
 * Hosyond ESP32-S3 3.5" IPS (LCDWIKI ES3C35P, Amazon ASIN B0H28X8SQ4).
 * Included only from board_pins.h when CONFIG_RECORDER_BOARD_HOSYOND_35.
 *
 * Sources (full list and discrepancies: docs/hosyond-35-pinmap.md):
 *   LCDWIKI schematic "ESP32-S3原理图.pdf" (3.5inch_ESP32-S3_board, 2026-01-30)
 *   LCDWIKI wiki page + user manual + IO allocation table
 *   vendor demo code (esp_bsp.h / spi_dev.h / ESP_Panel_Board_Custom.h)
 */

/* Panel native resolution: 320x480 portrait (ST77922, LCDWIKI spec). */
#define BOARD_LCD_H_RES 320
#define BOARD_LCD_V_RES 480
/* The UI runs in landscape (CrowPanel 800x480 layout scaled to 480x320);
 * display_st77922.c rotates each rendered strip in software. */
#define BOARD_UI_W 480
#define BOARD_UI_H 320

/* ST77922 over QSPI. LCD reset is wired to CHIP_PU (EN): no GPIO. */
#define BOARD_LCD_QSPI_HOST SPI2_HOST
#define BOARD_LCD_PCLK_HZ   (40 * 1000 * 1000)
#define BOARD_LCD_CS   GPIO_NUM_10
#define BOARD_LCD_SCK  GPIO_NUM_12
#define BOARD_LCD_D0   GPIO_NUM_11
#define BOARD_LCD_D1   GPIO_NUM_13
#define BOARD_LCD_D2   GPIO_NUM_14
#define BOARD_LCD_D3   GPIO_NUM_9
#define BOARD_LCD_TE   GPIO_NUM_42 /* not used */
#define BOARD_LCD_BL   GPIO_NUM_41 /* active high, PWM via BSS138 */

/* Shared I2C: touch (in the ST77922) + ES8311. */
#define BOARD_I2C_SDA GPIO_NUM_38
#define BOARD_I2C_SCL GPIO_NUM_39
#define BOARD_I2C_PORT I2C_NUM_0

#define BOARD_TOUCH_ADDR 0x55
#define BOARD_TOUCH_RST  GPIO_NUM_48
#define BOARD_TOUCH_INT  GPIO_NUM_47

/* ES8311 codec (7-bit 0x18, CE pin low) + analog MEMS mic on MIC1. */
#define BOARD_ES8311_ADDR 0x18
#define BOARD_I2S_PORT  I2S_NUM_1
#define BOARD_I2S_MCLK  GPIO_NUM_17
#define BOARD_I2S_BCLK  GPIO_NUM_18
#define BOARD_I2S_WS    GPIO_NUM_21
#define BOARD_I2S_DOUT  GPIO_NUM_15 /* ESP -> codec DSDIN (unused) */
#define BOARD_I2S_DIN   GPIO_NUM_16 /* codec ASDOUT -> ESP */
#define BOARD_I2S_MCLK_MULT 384      /* 16 kHz x 384 = 6.144 MHz */
/* ES8311 gain: PGA reg 0x14 low nibble (0..10 = 0..30 dB) and ADC digital
 * scale reg 0x16 (0..7 = 0..42 dB, 6 dB steps); ADC volume is 0 dB.
 * Measured on the board 2026-09-30 (boot mic self-test over USB serial):
 * PGA 30 dB + scale 12 dB left a quiet room at rms ~3 and voice near
 * -45 dBFS (sounds silent); PGA 30 dB + scale 42 dB gives a quiet-room
 * floor of rms ~110 (-50 dBFS) with room sounds peaking in the thousands.
 * Too loud / clipping: step the scale down to 0x06 (36 dB). */
#define BOARD_ES8311_PGA_REG14  0x1A /* analog MIC1 in, PGA 30 dB (max) */
#define BOARD_ES8311_ADC_GAIN16 0x07 /* ADC scale +42 dB (max) */
/* I2S slot that carries the mic (0 = left, 1 = right); both are read. */
#define BOARD_ES8311_ADC_SLOT   0

/* Speaker amp (FM8002E / SC8002B) shutdown: HIGH = amp off (pull-up). */
#define BOARD_AMP_EN GPIO_NUM_1
#define BOARD_AMP_OFF_LEVEL 1

/* microSD, SDMMC 4-bit (10 k pull-ups on board). */
#define BOARD_SD_CLK GPIO_NUM_5
#define BOARD_SD_CMD GPIO_NUM_4
#define BOARD_SD_D0  GPIO_NUM_6
#define BOARD_SD_D1  GPIO_NUM_7
#define BOARD_SD_D2  GPIO_NUM_2
#define BOARD_SD_D3  GPIO_NUM_3

#define BOARD_SD_MISSING_MSG "No SD card: insert a FAT32 microSD card, restart"

/* No RTC chip on this board; the address only keeps rtc_pcf8563.c
 * compiling (its init returns early on this board, nothing is probed). */
#define BOARD_RTC_ADDR 0x51

/* Not used by the recorder (documented only). */
#define BOARD_RGB_LED  GPIO_NUM_40

/* Battery gauge (battery.c, 0.7.0): BAT+ -> R14 100K -> GPIO8 -> R15 100K ->
 * GND, so the battery is 2 x the pin voltage. TP4054 charger, CHRG not wired
 * to a GPIO; no USB 5 V sense pin (see battery_model.h). */
#define BOARD_BAT_ADC  GPIO_NUM_8 /* ADC1_CH7, divider /2 */

/*
 * The shared code talks in the CrowPanel STC8 command numbers. On this
 * board board_ctrl_hosyond35.c maps them: 0..245 = backlight PWM
 * (0 brightest, 245 off), MUTE_AMP = amp enable pin off, the rest no-ops.
 */
#define BOARD_CMD_BACKLIGHT_MAX 0
#define BOARD_CMD_BACKLIGHT_OFF 245
#define BOARD_CMD_TOUCH_ON      250
#define BOARD_CMD_MIC_PATH      2
#define BOARD_CMD_MUTE_AMP      249

#define BOARD_BACKLIGHT_DIM 160
#define BOARD_DIM_AFTER_MS  60000

/* Same WAV format as the CrowPanel: 16 kHz mono 16-bit. */
#define REC_SAMPLE_RATE    16000
#define REC_BITS           16
#define REC_CHANNELS       1
/* ES8311 does the gain in hardware; no extra software multiply. */
#define REC_SW_GAIN        1

/* On-screen mic meter in dBFS (see main/meter_level.h). Measured on this
 * board 2026-10-07 (0.6.2, voice at ~1 ft): speech block peaks -33..-25
 * dBFS (median -30), quiet room -49..-41 dBFS, screen taps up to 0 dBFS.
 * -48..-8 dBFS over 22 bars (~1.8 dB a bar) puts speech at 1 ft around
 * 8-13 bars, a quiet room at 0-2 ("Quiet"), and the yellow top quarter
 * from about -18 dBFS. Release 2 dB per 64 ms block (~31 dB/s). */
#define BOARD_METER_DB_MIN      (-48.0f)
#define BOARD_METER_DB_MAX      (-8.0f)
#define BOARD_METER_RELEASE_DB  (2.0f)
