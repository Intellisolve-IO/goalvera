#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <time.h>

/* Small "Recent activity" log shown on the home screen. Thread-safe. */

#define STATUS_MSG_LEN 96

typedef struct {
    char msg[STATUS_MSG_LEN];
    bool error;
    bool wall_valid;   /* true: 'wall' is a real local time */
    time_t wall;
    uint32_t boot_s;   /* seconds since power-on when logged */
} status_entry_t;

void status_init(void);
/* Add an info entry (also ESP_LOGI). */
void status_set(const char *msg);
/* Add an error entry (also ESP_LOGE). */
void status_error(const char *msg);
/* Latest message text. */
void status_get(char *out, size_t len);
/* Copy up to max entries, newest first. Returns the count. */
int status_recent(status_entry_t *out, int max);
/* Bumped on every change so the UI only repaints when needed. */
uint32_t status_version(void);
/* True once the system clock holds a real date (RTC or network). */
bool status_clock_valid(void);
