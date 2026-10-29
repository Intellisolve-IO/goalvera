#pragma once
/*
 * On-screen "Mic level" scaling (tasks#20).
 *
 * The UI lights (level * METER_BARS / 100) bars. Up to 0.6.2 the level was
 * the block peak as a straight percentage of full scale, so one bar of 22
 * was 4.5% = -27 dBFS and everything quieter showed the 1-bar minimum.
 * That suited the CrowPanel (PDM mic with an x8 software gain, hot signal)
 * but not the Hosyond's ES8311: there normal speech at 1 ft peaks around
 * -30 dBFS (measured on David's 2026-10-07 recordings), so the meter sat
 * at 1 bar while the audio was fine.
 *
 * meter_db_level() maps the block peak in dBFS linearly onto 0..100
 * between db_min and db_max, with an instant attack and a falling hold of
 * release_db per block, so the 200 ms UI tick still sees the latest
 * syllable instead of whichever 64 ms block happened to be last.
 *
 * Pure C (no ESP-IDF headers) so test/host/test_meter.c can run it.
 */
#include <math.h>
#include <stdint.h>

#define METER_HOLD_RESET_DB (-120.0f)

static inline uint8_t meter_db_level(int16_t peak, float *hold_db, float db_min, float db_max,
                                     float release_db)
{
    int32_t a = peak < 0 ? -(int32_t)peak : peak;
    float db = a > 0 ? 20.0f * log10f((float)a / 32767.0f) : METER_HOLD_RESET_DB;
    float h = *hold_db - release_db;
    if (db > h) {
        h = db;
    }
    if (h < METER_HOLD_RESET_DB) {
        h = METER_HOLD_RESET_DB;
    }
    *hold_db = h;
    float pct = (h - db_min) * 100.0f / (db_max - db_min);
    if (pct <= 0.0f) {
        return 0;
    }
    if (pct >= 100.0f) {
        return 100;
    }
    return (uint8_t)pct;
}
