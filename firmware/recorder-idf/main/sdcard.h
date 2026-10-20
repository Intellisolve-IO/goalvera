#pragma once

#include "esp_err.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

esp_err_t sd_init(void);
bool sd_mounted(void);
void sd_refresh(void);
/* Ask a background task to refresh free space (never blocks the UI). */
void sd_refresh_async(void);
void sd_space(uint32_t *hours_free, uint32_t *hours_total, uint64_t *free_bytes);
bool sd_space_low(void);
bool sd_can_record(char *reason, size_t len);
