#pragma once
/*
 * Battery gauge model for the Hosyond 3.5" (0.7.0). Pure C, no ESP-IDF
 * headers, so test/host/test_battery.c runs it on the host. battery.c feeds
 * it one reading every BATT_SAMPLE_MS.
 *
 * Hardware (LCDWIKI ES3C35P schematic): BAT+ -> R14 100K -> GPIO8 (ADC1_CH7)
 * -> R15 100K -> GND, so the battery is 2 x the pin voltage. TP4054 charger
 * (~290 mA, stops at 4.2 V). With USB 5 V present, Q3 cuts the battery off
 * the 5 V rail and the board runs from USB. No GPIO senses USB power and the
 * charger's CHRG pin is not wired to the ESP32, so:
 *   - USB power: a computer is talking to the board (USB-Serial-JTAG SOF
 *     frames, passed in as usb_host), or the reading is impossible for a
 *     battery that is powering the board (< 3.0 V or > 4.35 V: no battery).
 *     A wall charger or power bank never shows up as usb_host.
 *   - Charging: guessed from the voltage. Plugging in takes the board's load
 *     off the battery and adds ~290 mA of charge current, so the reading steps
 *     up (expected 40-100 mV) within seconds; unplugging steps it down again.
 *     A slow rise with no load change, or a raw reading held at the charger's
 *     4.18-4.2 V, also counts. Booting while already on a wall charger is only
 *     caught by those slower rules. Thresholds are first guesses, to be tuned
 *     from the serial log on the board.
 *   - Otherwise on battery: % from a 1-cell LiPo resting curve, after ~30 s
 *     smoothing with the recording/upload sag added back. On battery the %
 *     only goes up again after a real (> 50 mV) rise, so it doesn't flicker.
 */
#include <stdbool.h>
#include <stdint.h>

#define BATT_SAMPLE_MS        2000u
#define BATT_EMA_DIV          16    /* slow average: ~32 s time constant */
#define BATT_FAST_N           3     /* fast average: last 3 samples (~6 s) */
#define BATT_SAG_HEAVY_MV     40    /* added back while recording/uploading */
#define BATT_STEP_UP_MV       40    /* fast - slow >= this: charger plugged in */
#define BATT_STEP_DOWN_MV     40    /* slow - fast >= this: charger unplugged */
#define BATT_SETTLE_MS        20000u              /* after a load change: no step decisions */
#define BATT_RISE_SETTLE_MS   (3u * 60u * 1000u)  /* after a load change: no rise decisions */
#define BATT_RISE_WINDOW_MS   (10u * 60u * 1000u)
#define BATT_RISE_MV          12    /* slow average up this much over the window: charging */
#define BATT_FULL_MV          4180  /* raw reading held at/above: charger CV or full on USB */
#define BATT_FULL_HOLD_MS     90000u
#define BATT_MIN_MV           3000  /* below: no battery powering us, so USB */
#define BATT_MAX_MV           4350  /* above: charger output with no battery, so USB */
#define BATT_UP_HYST_MV       50    /* on battery, % rises only after this much */
/* Gauge colours (0.7.1): green above 25%, yellow 10-25%, red below 10%. */
#define BATT_YELLOW_MAX_PCT   25    /* 10..25 = yellow, above = green */
#define BATT_RED_BELOW_PCT    10    /* below = red, and the one "Battery low" warning */
#define BATT_CRIT_PCT         5     /* auto Save & finish (if enabled) */
#define BATT_CHG_OFFSET_MV    70    /* charging lifts the reading ~70 mV (290 mA x pack + polarization) */

typedef enum {
    BATT_SRC_UNKNOWN = 0, /* no sample yet */
    BATT_SRC_BATTERY,     /* running on the battery */
    BATT_SRC_CHARGING,    /* external 5 V, guessed from the voltage pattern */
    BATT_SRC_USB_HOST,    /* computer connected (USB-Serial-JTAG) */
    BATT_SRC_USB_NO_BATT, /* reading impossible for a battery: USB, no battery */
} batt_src_t;

static inline const char *batt_src_name(batt_src_t s)
{
    switch (s) {
    case BATT_SRC_BATTERY: return "battery";
    case BATT_SRC_CHARGING: return "charging";
    case BATT_SRC_USB_HOST: return "usb-host";
    case BATT_SRC_USB_NO_BATT: return "usb-no-battery";
    default: return "unknown";
    }
}

/* Resting voltage (mV) -> %, 1-cell LiPo, straight lines in between. 0% is
 * 3.45 V, not the cell's ~3.0 V: the board's ME6217 3.3 V LDO drops out
 * below about 3.4 V. */
static const uint16_t BATT_CURVE_MV[] = {3450, 3550, 3650, 3700, 3730, 3760, 3790,
                                         3820, 3860, 3920, 4000, 4100, 4200};
static const uint8_t BATT_CURVE_PCT[] = {0, 5, 10, 15, 20, 30, 40, 50, 60, 70, 80, 90, 100};
#define BATT_CURVE_N ((int)(sizeof BATT_CURVE_MV / sizeof BATT_CURVE_MV[0]))

static inline int batt_pct_from_mv(int mv)
{
    if (mv <= BATT_CURVE_MV[0]) return 0;
    if (mv >= BATT_CURVE_MV[BATT_CURVE_N - 1]) return 100;
    for (int i = 1; i < BATT_CURVE_N; i++) {
        if (mv <= BATT_CURVE_MV[i]) {
            int m0 = BATT_CURVE_MV[i - 1], m1 = BATT_CURVE_MV[i];
            int p0 = BATT_CURVE_PCT[i - 1], p1 = BATT_CURVE_PCT[i];
            return p0 + ((mv - m0) * (p1 - p0) + (m1 - m0) / 2) / (m1 - m0);
        }
    }
    return 100;
}

/* Board load as the UI sees it. Only HEAVY (recording or uploading) gets sag
 * compensation; any change restarts the settle and rise windows, because a
 * load change moves the reading on its own (backlight dim included). */
typedef enum { BATT_LOAD_DIM = 0, BATT_LOAD_IDLE = 1, BATT_LOAD_HEAVY = 2 } batt_load_t;

typedef struct {
    /* outputs */
    batt_src_t src;
    int vbat_mv;   /* last raw battery reading (x2 divider, corrected) */
    int smooth_mv; /* smoothed, sag compensated */
    int pct;       /* shown %; -1 until the first battery reading */
    /* internals */
    bool seeded;
    int32_t ema_x16;
    int fast[BATT_FAST_N];
    int fast_i;
    int shown_mv;
    batt_load_t load;
    uint32_t load_changed_ms;
    uint32_t rise_ref_ms;
    int rise_ref_mv; /* -1: not started */
    uint32_t full_since_ms;
    bool full_timing;
} batt_model_t;

static inline void batt_model_init(batt_model_t *m)
{
    *m = (batt_model_t){0};
    m->src = BATT_SRC_UNKNOWN;
    m->pct = -1;
    m->load = BATT_LOAD_IDLE;
    m->rise_ref_mv = -1;
}

static inline void batt__reseed(batt_model_t *m, int comp)
{
    m->ema_x16 = comp * 16;
    for (int i = 0; i < BATT_FAST_N; i++) m->fast[i] = comp;
    m->fast_i = 0;
    m->rise_ref_mv = -1;
    m->full_timing = false;
    m->seeded = true;
    m->smooth_mv = comp;
}

static inline void batt__go_battery(batt_model_t *m, int comp)
{
    m->src = BATT_SRC_BATTERY;
    batt__reseed(m, comp);
    m->pct = batt_pct_from_mv(comp);
    m->shown_mv = comp;
}

static inline void batt__go_charging(batt_model_t *m, int comp)
{
    m->src = BATT_SRC_CHARGING;
    batt__reseed(m, comp);
}

/* Feed one sample. vbat_mv: battery voltage after the x2 divider and the
 * per-board correction. usb_host: a computer is connected. now: ms. */
static inline void batt_model_update(batt_model_t *m, int vbat_mv, batt_load_t load, bool usb_host, uint32_t now)
{
    m->vbat_mv = vbat_mv;
    if (load != m->load) {
        m->load = load;
        m->load_changed_ms = now;
        m->rise_ref_mv = -1;
    }
    int comp = vbat_mv + (load == BATT_LOAD_HEAVY ? BATT_SAG_HEAVY_MV : 0);

    if (usb_host) {
        m->src = BATT_SRC_USB_HOST;
        m->seeded = false; /* re-seed on battery when the computer goes away */
        m->smooth_mv = comp;
        return;
    }
    if (vbat_mv < BATT_MIN_MV || vbat_mv > BATT_MAX_MV) {
        m->src = BATT_SRC_USB_NO_BATT;
        m->seeded = false;
        m->smooth_mv = comp;
        return;
    }
    if (!m->seeded) {
        batt__go_battery(m, comp);
        return;
    }

    int slow_prev = (int)(m->ema_x16 / 16);
    m->fast[m->fast_i] = comp;
    m->fast_i = (m->fast_i + 1) % BATT_FAST_N;
    int fsum = 0;
    for (int i = 0; i < BATT_FAST_N; i++) fsum += m->fast[i];
    int fast = fsum / BATT_FAST_N;

    uint32_t since_load = now - m->load_changed_ms;
    if (since_load < BATT_SETTLE_MS) {
        /* The load just changed: follow the reading, decide nothing. */
        m->ema_x16 = fast * 16;
        m->smooth_mv = fast;
    } else {
        m->ema_x16 += (comp * 16 - m->ema_x16) / BATT_EMA_DIV;
        m->smooth_mv = (int)(m->ema_x16 / 16);
        if (m->src == BATT_SRC_BATTERY && fast - slow_prev >= BATT_STEP_UP_MV) {
            batt__go_charging(m, comp);
            return;
        }
        if (m->src == BATT_SRC_CHARGING && slow_prev - fast >= BATT_STEP_DOWN_MV) {
            batt__go_battery(m, comp);
            return;
        }
    }

    /* Raw reading held at the charger's full voltage: on USB. */
    if (vbat_mv >= BATT_FULL_MV) {
        if (!m->full_timing) {
            m->full_timing = true;
            m->full_since_ms = now;
        } else if (m->src == BATT_SRC_BATTERY && now - m->full_since_ms >= BATT_FULL_HOLD_MS) {
            batt__go_charging(m, comp);
            return;
        }
    } else {
        m->full_timing = false;
    }

    if (m->src == BATT_SRC_BATTERY) {
        /* Slow rise with no load change: charging. */
        if (since_load >= BATT_RISE_SETTLE_MS) {
            if (m->rise_ref_mv < 0 || m->smooth_mv < m->rise_ref_mv) {
                m->rise_ref_mv = m->smooth_mv; /* track the lowest point */
                m->rise_ref_ms = now;
            } else if (m->smooth_mv - m->rise_ref_mv >= BATT_RISE_MV &&
                       now - m->rise_ref_ms <= BATT_RISE_WINDOW_MS) {
                batt__go_charging(m, comp);
                return;
            } else if (now - m->rise_ref_ms > BATT_RISE_WINDOW_MS) {
                m->rise_ref_mv = m->smooth_mv;
                m->rise_ref_ms = now;
            }
        }
        /* Shown %: down freely, up only after a real rise. */
        int p = batt_pct_from_mv(m->smooth_mv);
        if (p < m->pct) {
            m->pct = p;
            m->shown_mv = m->smooth_mv;
        } else if (p > m->pct && m->smooth_mv >= m->shown_mv + BATT_UP_HYST_MV) {
            m->pct = p;
            m->shown_mv = m->smooth_mv;
        }
    }
}

typedef enum { BATT_LVL_RED = 0, BATT_LVL_YELLOW, BATT_LVL_GREEN } batt_level_t;

static inline batt_level_t batt_level(int pct)
{
    if (pct < BATT_RED_BELOW_PCT) return BATT_LVL_RED;
    if (pct <= BATT_YELLOW_MAX_PCT) return BATT_LVL_YELLOW;
    return BATT_LVL_GREEN;
}

/* Rough % while a charger or computer powers the board: the reading sits
 * above the resting voltage while the TP4054 charges, so take that off.
 * -1 when the reading can't be a battery (no battery fitted). */
static inline int batt_charging_pct(int vbat_mv)
{
    if (vbat_mv < BATT_MIN_MV || vbat_mv > BATT_MAX_MV) return -1;
    return batt_pct_from_mv(vbat_mv - BATT_CHG_OFFSET_MV);
}

/* What the top-right gauge shows (0.7.1): fill % (-1 = empty outline) and
 * whether the charging bolt is on. Pure, so the host test covers it. */
typedef struct {
    int fill_pct;
    bool bolt;
} batt_gauge_view_t;

static inline batt_gauge_view_t batt_gauge_view(batt_src_t src, int pct, int vbat_mv)
{
    batt_gauge_view_t v = {-1, false};
    switch (src) {
    case BATT_SRC_BATTERY:
        v.fill_pct = pct < 0 ? 0 : pct;
        break;
    case BATT_SRC_CHARGING:
    case BATT_SRC_USB_HOST:
        v.fill_pct = batt_charging_pct(vbat_mv);
        v.bolt = true;
        break;
    case BATT_SRC_USB_NO_BATT:
        v.bolt = true; /* empty outline + bolt: powered by USB, no battery */
        break;
    default:
        break;
    }
    return v;
}
