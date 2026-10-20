#pragma once
/* Minimal ES8311 setup for the Hosyond 3.5" board: ADC (mic) path only. */
#include "driver/i2c_master.h"
#include "esp_err.h"
#include <stdint.h>

/* Add the codec on the shared bus and read its chip ID (0x83 0x11). */
esp_err_t es8311_min_open(i2c_master_bus_handle_t bus, uint8_t *id1, uint8_t *id2);
/* Full register setup for 16 kHz, 16-bit I2S slave, MCLK = 384 x fs from
 * the MCLK pin, analog mic on MIC1. Call while the I2S MCLK is running.
 * reg16_adc_gain: ADC scale 0..7 = 0..42 dB (ADC_SYNC is added here). */
esp_err_t es8311_min_start_adc(uint8_t reg14_pga, uint8_t reg16_adc_gain);
/* Log registers 0x00..0x4F and the chip id over the serial console. */
void es8311_min_dump(const char *why);
/* Codec into its low-power state (before deep sleep). */
esp_err_t es8311_min_power_down(void);
