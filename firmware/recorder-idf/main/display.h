#pragma once

#include "driver/i2c_master.h"
#include "esp_err.h"
#include <stdbool.h>
#include <stdint.h>

esp_err_t display_init(i2c_master_bus_handle_t bus);
esp_err_t display_start_task(void);
bool display_lock(uint32_t timeout_ms);
void display_unlock(void);
bool display_touch_ok(void);
/* Hosyond only (display_st77922.c): panel off + sleep-in before deep sleep.
 * Call with display_lock() held. */
void display_sleep(void);
