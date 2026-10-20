#pragma once

#include "driver/i2c_master.h"
#include "esp_err.h"
#include <stdbool.h>
#include <stddef.h>
#include <time.h>

esp_err_t pcf8563_init(i2c_master_bus_handle_t bus);
bool rtc_present(void);
bool rtc_now(struct tm *out);
esp_err_t rtc_set_from_system(void);

void clock_format_ampm(const struct tm *t, bool with_seconds, char *out, size_t n);
void clock_format_date(const struct tm *t, char *out, size_t n);
