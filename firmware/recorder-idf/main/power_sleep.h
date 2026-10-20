#pragma once
/*
 * Auto sleep (Hosyond 3.5" only, 0.7.0). The UI decides when (no recording,
 * no touch for CONFIG_RECORDER_AUTO_SLEEP_MIN minutes, not plugged into a
 * computer); this file does the hardware part.
 *
 * Wake: the BOOT button. On this board KEY2 pulls GPIO0 to GND with a 10K
 * pull-up (R10) to 3.3 V, and GPIO0 is an RTC pin, so ext0 wake on a low
 * level works. Strapping pins are not re-sampled on a deep-sleep wake, so
 * pressing BOOT wakes the app, not the download mode. The RESET button also
 * works (full boot). Tap-to-wake is not possible: the touch interrupt is
 * GPIO47, which is not an RTC pin.
 */
#include <stdbool.h>

/* app_main, first thing: release the pin holds from the last sleep and log
 * why we woke. Returns true when the BOOT button woke us from auto sleep. */
bool power_sleep_boot(void);
/* Backlight off, panel sleep-in, codec power-down, Wi-Fi off, pin holds,
 * BOOT-button wake, deep sleep. Never returns. Call with display_lock() held. */
void power_deep_sleep(void);
/* RESET-button on/off (0.7.3, NVS since 0.7.4, see power_toggle.h). Call first in app_main.
 * Returns true when this reset was the button pressed while the board was on:
 * the caller shows "Turning off" and calls power_deep_sleep(). Otherwise it
 * arms "on" PT_ARM_MS later and the board boots normally. */
bool power_toggle_boot(void);
