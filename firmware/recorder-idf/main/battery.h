#pragma once
/*
 * Battery gauge (Hosyond 3.5" only; battery.c is not built for the CrowPanel).
 * A low-priority task reads GPIO8 every 2 s and runs battery_model.h. The
 * battery voltage is logged to the serial console every 30 s and on every
 * source change (tag "battery").
 */
#include "battery_model.h"
#include <stdbool.h>

typedef struct {
    batt_src_t src;
    int pct;       /* shown %, -1 until the first battery reading */
    int vbat_mv;   /* last raw battery reading */
    int smooth_mv; /* smoothed, sag compensated */
} battery_status_t;

void battery_init(void);
/* Called by the UI (every tick) with the board's current load. */
void battery_set_load(batt_load_t load);
void battery_get(battery_status_t *out);
