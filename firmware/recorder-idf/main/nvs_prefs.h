#pragma once

#include "esp_err.h"
#include <stddef.h>

#include <stdbool.h>
void prefs_init(void);
bool prefs_async_start(void);
esp_err_t prefs_get(const char *key, char *out, size_t len);
esp_err_t prefs_set(const char *key, const char *value);
/* Queue a write for the background prefs task. Never touches flash on the
 * caller's thread (flash writes stall PSRAM and scramble the RGB panel).
 * Unchanged values are skipped. */
void prefs_set_async(const char *key, const char *value);
