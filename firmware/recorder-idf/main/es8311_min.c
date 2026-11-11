/*
 * Minimal ES8311 driver on the new i2c_master API (the registry es8311
 * component uses the legacy I2C driver, which cannot share the bus with
 * i2c_master in IDF 5.4).
 *
 * Register sequence follows Espressif's es8311 driver as shipped in the
 * LCDWIKI ES3C35P demo (Example_17_echo/echo/es8311.cpp, Apache-2.0):
 * es8311_init() + es8311_sample_frequency_config(6144000, 16000) +
 * es8311_microphone_config(analog), plus the analog bias / power-up and
 * ADC filter settings from Espressif's esp_codec_dev es8311_open() and
 * es8311_start() (regs 0x0B 0x0C 0x10 0x11 0x16 bit 5, 0x1B, 0x44).
 * The DAC is left muted: the recorder never plays audio and the speaker
 * amp is held off.
 *
 * Mic level (measured on the board 2026-09-30, see
 * docs/hosyond-35-port-report.md): with PGA 30 dB the ADC digital scale
 * must be 42 dB. At the old 12 dB a quiet room read rms ~3 of 32767 and
 * voice only ~-45 dBFS, which plays back as silence.
 */
#include "es8311_min.h"

#include "board_pins.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdio.h>

static const char *TAG = "es8311";
static i2c_master_dev_handle_t s_dev;

static esp_err_t wr(uint8_t reg, uint8_t val)
{
    uint8_t b[2] = {reg, val};
    return i2c_master_transmit(s_dev, b, 2, 100);
}

static esp_err_t rd(uint8_t reg, uint8_t *val)
{
    return i2c_master_transmit_receive(s_dev, &reg, 1, val, 1, 100);
}

esp_err_t es8311_min_open(i2c_master_bus_handle_t bus, uint8_t *id1, uint8_t *id2)
{
    if (!bus) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!s_dev) {
        esp_err_t err = i2c_master_probe(bus, BOARD_ES8311_ADDR, 50);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "ES8311 @0x%02X not found: %s", BOARD_ES8311_ADDR, esp_err_to_name(err));
            return err;
        }
        i2c_device_config_t cfg = {
            .dev_addr_length = I2C_ADDR_BIT_LEN_7,
            .device_address = BOARD_ES8311_ADDR,
            .scl_speed_hz = 100000,
        };
        err = i2c_master_bus_add_device(bus, &cfg, &s_dev);
        if (err != ESP_OK) {
            s_dev = NULL;
            return err;
        }
    }
    uint8_t a = 0, b = 0;
    esp_err_t err = rd(0xFD, &a);
    if (err == ESP_OK) {
        err = rd(0xFE, &b);
    }
    if (id1) {
        *id1 = a;
    }
    if (id2) {
        *id2 = b;
    }
    return err;
}

#define CHK(x)                         \
    do {                               \
        esp_err_t e_ = (x);            \
        if (e_ != ESP_OK) {            \
            ESP_LOGE(TAG, "%s: %s", #x, esp_err_to_name(e_)); \
            return e_;                 \
        }                              \
    } while (0)

esp_err_t es8311_min_start_adc(uint8_t reg14_pga, uint8_t reg16_adc_gain)
{
    if (!s_dev) {
        return ESP_ERR_INVALID_STATE;
    }
    uint8_t v = 0;
    /* Reset, then power on (es8311_init). */
    CHK(wr(0x00, 0x1F));
    vTaskDelay(pdMS_TO_TICKS(20));
    CHK(wr(0x00, 0x00));
    CHK(wr(0x00, 0x80));
    /* Clock: all clocks on, MCLK from the MCLK pin, not inverted. */
    CHK(wr(0x01, 0x3F));
    CHK(rd(0x06, &v));
    CHK(wr(0x06, (uint8_t)(v & ~0x20))); /* SCLK not inverted */
    /* Coefficients for MCLK 6.144 MHz, fs 16 kHz:
     * pre_div 3, pre_multi x2 (code 1), adc_div 1, dac_div 1, fs_mode 0,
     * lrck 0x00FF, bclk_div 4, adc_osr 0x10, dac_osr 0x10. */
    CHK(rd(0x02, &v));
    CHK(wr(0x02, (uint8_t)((v & 0x07) | ((3 - 1) << 5) | (1 << 3))));
    CHK(wr(0x03, (uint8_t)((0 << 6) | 0x10)));
    CHK(wr(0x04, 0x10));
    CHK(wr(0x05, (uint8_t)(((1 - 1) << 4) | (1 - 1))));
    CHK(rd(0x06, &v));
    CHK(wr(0x06, (uint8_t)((v & 0xE0) | (4 - 1))));
    CHK(rd(0x07, &v));
    CHK(wr(0x07, (uint8_t)((v & 0xC0) | 0x00)));
    CHK(wr(0x08, 0xFF));
    /* Format: slave serial port, I2S, 16-bit in and out. */
    CHK(rd(0x00, &v));
    CHK(wr(0x00, (uint8_t)(v & 0xBF)));
    CHK(wr(0x09, 0x0C));
    CHK(wr(0x0A, 0x0C)); /* bit 6 clear: ADC serial output not muted */
    /* esp_codec_dev es8311_open(): fast power-up stages, highest analog
     * bias, internal VSEL, ADC HPF stage 1, ADC data on both I2S slots. */
    CHK(wr(0x0B, 0x00));
    CHK(wr(0x0C, 0x00));
    CHK(wr(0x10, 0x1F));
    CHK(wr(0x11, 0x7F));
    CHK(wr(0x1B, 0x0A));
    CHK(wr(0x44, 0x08));
    /* Power (es8311_init). */
    CHK(wr(0x0D, 0x01)); /* analog circuitry up */
    CHK(wr(0x0E, 0x02)); /* PGA + ADC modulator on */
    CHK(wr(0x12, 0x00)); /* DAC up (as vendor; muted below) */
    CHK(wr(0x13, 0x10));
    CHK(wr(0x1C, 0x6A)); /* ADC EQ bypass, DC offset cancel */
    CHK(wr(0x37, 0x08)); /* DAC EQ bypass */
    /* Mic: ADC volume 0 dB, analog MIC1 + PGA, ADC scale (bit 5 =
     * ADC_SYNC, standard audio clock, as esp_codec_dev). */
    CHK(wr(0x17, 0xBF));
    CHK(wr(0x14, reg14_pga));
    CHK(wr(0x16, (uint8_t)(0x20 | (reg16_adc_gain & 0x07))));
    /* DAC silent: volume 0 and soft mute. */
    CHK(wr(0x32, 0x00));
    CHK(rd(0x31, &v));
    CHK(wr(0x31, (uint8_t)(v | 0x60)));
    ESP_LOGI(TAG, "ADC path on: 16 kHz, 16-bit I2S slave, analog MIC1, PGA %d dB, ADC scale +%d dB (reg14=0x%02X reg16=0x%02X)",
             (reg14_pga & 0x0F) * 3, (reg16_adc_gain & 0x07) * 6, reg14_pga, (uint8_t)(0x20 | (reg16_adc_gain & 0x07)));
    return ESP_OK;
}

void es8311_min_dump(const char *why)
{
    if (!s_dev) {
        ESP_LOGW(TAG, "register dump (%s): codec not open", why ? why : "");
        return;
    }
    /* One line per 16 registers, 0x00..0x4F, then the chip id (0xFD..0xFF). */
    for (int base = 0; base < 0x50; base += 16) {
        char line[16 * 3 + 1];
        int pos = 0;
        for (int i = 0; i < 16; i++) {
            uint8_t v = 0;
            if (rd((uint8_t)(base + i), &v) != ESP_OK) {
                pos += snprintf(line + pos, sizeof line - pos, " --");
            } else {
                pos += snprintf(line + pos, sizeof line - pos, " %02X", v);
            }
        }
        ESP_LOGI(TAG, "regs (%s) %02X:%s", why ? why : "", base, line);
    }
    uint8_t a = 0, b = 0, c = 0;
    rd(0xFD, &a);
    rd(0xFE, &b);
    rd(0xFF, &c);
    ESP_LOGI(TAG, "regs (%s) FD..FF: %02X %02X %02X", why ? why : "", a, b, c);
}

/* Low-power state before deep sleep (0.7.0): the suspend sequence from
 * Espressif's esp_codec_dev es8311_suspend(). The next boot runs
 * es8311_min_open()/start_adc() again, which reprograms everything. */
esp_err_t es8311_min_power_down(void)
{
    static const uint8_t seq[][2] = {
        {0x32, 0x00}, {0x17, 0x00}, {0x0E, 0xFF}, {0x12, 0x02}, {0x14, 0x00}, {0x0D, 0xFA},
        {0x15, 0x00}, {0x02, 0x10}, {0x00, 0x00}, {0x00, 0x1F}, {0x01, 0x30}, {0x01, 0x00},
        {0x45, 0x00}, {0x0D, 0xFC}, {0x02, 0x00},
    };
    if (!s_dev) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t err = ESP_OK;
    for (size_t i = 0; i < sizeof seq / sizeof seq[0]; i++) {
        esp_err_t e = wr(seq[i][0], seq[i][1]);
        if (e != ESP_OK) {
            err = e;
        }
    }
    return err;
}
