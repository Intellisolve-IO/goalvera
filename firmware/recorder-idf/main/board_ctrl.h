#pragma once

#include "driver/i2c_master.h"
#include "esp_err.h"
#include <stdbool.h>
#include <stdint.h>

esp_err_t board_ctrl_init(i2c_master_bus_handle_t bus);
bool board_ctrl_present(void);
esp_err_t board_ctrl_cmd(uint8_t cmd);
void board_ctrl_set_backlight(uint8_t level);
/* Send backlight level bypassing the cache, with retries. Used at boot
 * after the first LVGL frame so the screen is lit without a touch. */
esp_err_t board_ctrl_force_backlight(uint8_t level, const char *why);
