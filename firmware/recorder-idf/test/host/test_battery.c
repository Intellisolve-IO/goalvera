/* Host unit test for the battery gauge model (main/battery_model.h).
 *   cc -Wall -Wextra -Imain test/host/test_battery.c -o /tmp/tb && /tmp/tb */
#include "battery_model.h"
#include <assert.h>
#include <stdio.h>

static uint32_t t;

static void feed(batt_model_t *m, int mv, batt_load_t load, bool host, int n)
{
    for (int i = 0; i < n; i++) {
        t += BATT_SAMPLE_MS;
        batt_model_update(m, mv, load, host, t);
    }
}

int main(void)
{
    /* Curve end points and a few middles. */
    assert(batt_pct_from_mv(3300) == 0);
    assert(batt_pct_from_mv(3450) == 0);
    assert(batt_pct_from_mv(3550) == 5);
    assert(batt_pct_from_mv(3820) == 50);
    assert(batt_pct_from_mv(3840) == 55);
    assert(batt_pct_from_mv(4200) == 100);
    assert(batt_pct_from_mv(4300) == 100);
    for (int mv = 3400; mv < 4250; mv++) {
        assert(batt_pct_from_mv(mv) <= batt_pct_from_mv(mv + 1));
    }

    batt_model_t m;

    /* First reading on battery: battery source and % at once. */
    t = 0;
    batt_model_init(&m);
    assert(m.pct == -1 && m.src == BATT_SRC_UNKNOWN);
    feed(&m, 3820, BATT_LOAD_IDLE, false, 1);
    assert(m.src == BATT_SRC_BATTERY && m.pct == 50);

    /* Noise of +-10 mV doesn't move the shown % up, and doesn't look like a charger. */
    for (int i = 0; i < 300; i++) {
        feed(&m, 3820 + ((i & 1) ? 10 : -10), BATT_LOAD_IDLE, false, 1);
    }
    assert(m.src == BATT_SRC_BATTERY);
    assert(m.pct >= 49 && m.pct <= 50);

    /* Recording sags the reading 40 mV: compensated, so the % holds. */
    int before = m.pct;
    feed(&m, 3780, BATT_LOAD_HEAVY, false, 60);
    assert(m.src == BATT_SRC_BATTERY);
    assert(m.pct >= before - 1);
    /* Recording ends: back to idle, no false "charging". */
    feed(&m, 3820, BATT_LOAD_IDLE, false, 60);
    assert(m.src == BATT_SRC_BATTERY);

    /* Slow discharge: % goes down. */
    for (int mv = 3820; mv >= 3700; mv -= 1) {
        feed(&m, mv, BATT_LOAD_IDLE, false, 2);
    }
    assert(m.src == BATT_SRC_BATTERY);
    assert(m.pct <= 17 && m.pct >= 14);

    /* Wall charger plugged in: 60 mV step up -> Charging within ~10 s. */
    feed(&m, 3760, BATT_LOAD_IDLE, false, 5);
    assert(m.src == BATT_SRC_CHARGING);
    /* Charging, slowly rising: stays Charging. */
    for (int mv = 3760; mv <= 3900; mv++) {
        feed(&m, mv, BATT_LOAD_IDLE, false, 1);
    }
    assert(m.src == BATT_SRC_CHARGING);
    /* Unplugged: 70 mV step down -> battery again, % from the new reading. */
    feed(&m, 3830, BATT_LOAD_IDLE, false, 5);
    assert(m.src == BATT_SRC_BATTERY);
    assert(m.pct >= 50 && m.pct <= 55);

    /* A step up right after a load change is ignored (that's the load). */
    feed(&m, 3830, BATT_LOAD_HEAVY, false, 30);
    feed(&m, 3880, BATT_LOAD_IDLE, false, 5);
    assert(m.src == BATT_SRC_BATTERY);

    /* Computer connected: USB power, whatever the reading. */
    feed(&m, 4100, BATT_LOAD_IDLE, true, 3);
    assert(m.src == BATT_SRC_USB_HOST);
    /* Computer unplugged: battery, re-seeded from the new reading. */
    feed(&m, 3920, BATT_LOAD_IDLE, false, 1);
    assert(m.src == BATT_SRC_BATTERY && m.pct == 70);

    /* No battery on USB: reading impossible for a battery. */
    feed(&m, 120, BATT_LOAD_IDLE, false, 2);
    assert(m.src == BATT_SRC_USB_NO_BATT);
    feed(&m, 4400, BATT_LOAD_IDLE, false, 2);
    assert(m.src == BATT_SRC_USB_NO_BATT);

    /* Booted on a wall charger near full: the held 4.19 V catches it. */
    t = 0;
    batt_model_init(&m);
    feed(&m, 4190, BATT_LOAD_IDLE, false, 1);
    assert(m.src == BATT_SRC_BATTERY);
    feed(&m, 4190, BATT_LOAD_IDLE, false, (int)(BATT_FULL_HOLD_MS / BATT_SAMPLE_MS) + 2);
    assert(m.src == BATT_SRC_CHARGING);

    /* Booted on a wall charger mid-way: a steady rise is caught by the rise rule. */
    t = 0;
    batt_model_init(&m);
    feed(&m, 3800, BATT_LOAD_IDLE, false, 1);
    int mv = 3800;
    for (int i = 0; i < 900 && m.src == BATT_SRC_BATTERY; i++) {
        if (i % 25 == 0) mv++; /* ~1.2 mV/min, 0.1C charge */
        feed(&m, mv, BATT_LOAD_IDLE, false, 1);
    }
    assert(m.src == BATT_SRC_CHARGING);

    /* On battery under steady load, a full-looking start (4.15 V) never turns into Charging. */
    t = 0;
    batt_model_init(&m);
    for (int i = 0; i < 1800; i++) {
        feed(&m, 4150 - i / 30, BATT_LOAD_IDLE, false, 1);
    }
    assert(m.src == BATT_SRC_BATTERY);

    /* Low and critical thresholds line up with the curve. */
    assert(batt_pct_from_mv(3550) == BATT_CRIT_PCT);

    /* Gauge colours (0.7.1): green > 25, yellow 10-25, red < 10. */
    assert(batt_level(80) == BATT_LVL_GREEN);
    assert(batt_level(26) == BATT_LVL_GREEN);
    assert(batt_level(25) == BATT_LVL_YELLOW);
    assert(batt_level(20) == BATT_LVL_YELLOW);
    assert(batt_level(10) == BATT_LVL_YELLOW);
    assert(batt_level(9) == BATT_LVL_RED);
    assert(batt_level(0) == BATT_LVL_RED);

    /* Gauge view: battery = fill, no bolt; USB/charging = bolt over the
     * charging estimate; no battery = empty outline + bolt; unknown = empty. */
    batt_gauge_view_t v = batt_gauge_view(BATT_SRC_BATTERY, 80, 4050);
    assert(v.fill_pct == 80 && !v.bolt);
    v = batt_gauge_view(BATT_SRC_BATTERY, -1, 0);
    assert(v.fill_pct == 0 && !v.bolt);
    v = batt_gauge_view(BATT_SRC_CHARGING, 40, 3930);
    assert(v.bolt && v.fill_pct == batt_pct_from_mv(3930 - BATT_CHG_OFFSET_MV));
    v = batt_gauge_view(BATT_SRC_USB_HOST, -1, 4200);
    assert(v.bolt && v.fill_pct >= 90);
    v = batt_gauge_view(BATT_SRC_USB_HOST, -1, 200);
    assert(v.bolt && v.fill_pct == -1);
    v = batt_gauge_view(BATT_SRC_USB_NO_BATT, -1, 4400);
    assert(v.bolt && v.fill_pct == -1);
    v = batt_gauge_view(BATT_SRC_UNKNOWN, -1, 0);
    assert(!v.bolt && v.fill_pct == -1);

    printf("test_battery: all passed\n");
    return 0;
}
