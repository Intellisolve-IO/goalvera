/* Host unit test for the dBFS mic meter (main/meter_level.h), with the
 * Hosyond 3.5" constants from main/board_pins_hosyond35.h.
 *   cc -Wall -Imain test/host/test_meter.c -lm -o /tmp/tm && /tmp/tm */
#include "meter_level.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>

#define DB_MIN (-48.0f)
#define DB_MAX (-8.0f)
#define REL    (2.0f)
#define BARS   22

static int16_t pk(float dbfs) { return (int16_t)lrintf(32767.0f * powf(10.0f, dbfs / 20.0f)); }

/* Same rule as paint_meter() in ui_app.c. */
static int bars(uint8_t level)
{
    int lit = (level * BARS) / 100;
    return (level > 0 && lit == 0) ? 1 : lit;
}

static int fresh(float dbfs)
{
    float h = METER_HOLD_RESET_DB;
    return bars(meter_db_level(pk(dbfs), &h, DB_MIN, DB_MAX, REL));
}

int main(void)
{
    float h = METER_HOLD_RESET_DB;
    assert(meter_db_level(0, &h, DB_MIN, DB_MAX, REL) == 0);  /* digital silence */
    assert(fresh(-60.0f) == 0);                                /* below the floor */
    assert(fresh(-47.0f) <= 1);                                /* quiet room: "Quiet" */
    int speech = fresh(-30.0f);                                /* voice at 1 ft (measured) */
    assert(speech >= 9 && speech <= 11);
    assert(fresh(-25.0f) >= 12 && fresh(-25.0f) <= 13);
    assert(fresh(-18.0f) >= 16);                               /* yellow quarter */
    assert(fresh(-8.0f) == BARS && fresh(0.0f) == BARS);       /* full, never past */
    assert(meter_db_level(-32767, &h, DB_MIN, DB_MAX, REL) == 100);  /* negative peak */

    /* Instant attack, 2 dB per block release, never below the floor. */
    h = METER_HOLD_RESET_DB;
    meter_db_level(pk(-20.0f), &h, DB_MIN, DB_MAX, REL);
    meter_db_level(pk(-50.0f), &h, DB_MIN, DB_MAX, REL);
    assert(fabsf(h - (-22.0f)) < 0.05f);
    for (int i = 0; i < 100; i++) {
        meter_db_level(0, &h, DB_MIN, DB_MAX, REL);
    }
    assert(h == METER_HOLD_RESET_DB);

    printf("meter ok: -30 dBFS -> %d/%d bars, -47 -> %d, -18 -> %d\n", speech, BARS, fresh(-47.0f),
           fresh(-18.0f));
    return 0;
}
