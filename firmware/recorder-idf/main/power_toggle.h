#pragma once
/*
 * On/off with the RESET-wired button (Hosyond 3.5"). Pure logic, no ESP-IDF
 * includes, so test/host/test_power_toggle.c can run it on a PC.
 *
 * The button is soldered across the board's RESET (EN/CHIP_PU), so firmware
 * never sees it held: every press is a chip reset, reported as ESP_RST_POWERON
 * (same as plugging in). 0.7.3 kept the state in RTC_NOINIT memory, but a field
 * test showed an EN reset wipes it, so the board never turned off. Since 0.7.4
 * the state lives in NVS (flash), key PT_NVS_KEY: "1" = on (armed), "0"/absent
 * = off.
 *
 *   power-on/pin reset + stored "on"  -> button pressed while on: write "off",
 *                                         deep sleep (wake only by RESET/BOOT)
 *   power-on/pin reset + stored "off" -> boot normally
 *   any other reason (deep-sleep wake, USB/JTAG/esptool reset, software
 *   restart/OTA, panic, watchdog, brownout) -> boot normally, never off
 *
 * "on" is written only after PT_ARM_MS of uptime, so a quick second press while
 * booting never turns the board off. Writes happen only when the value
 * changes. Side effect: plugging in USB/battery while "off" also powers on (the
 * stored "off" means the next power-on boots).
 */
#include <stdbool.h>
#include <stddef.h>

#define PT_ARM_MS 2000
#define PT_NVS_KEY "pwr_on"

typedef enum { PT_BOOT_PIN_OR_POWERON, PT_BOOT_DEEPSLEEP, PT_BOOT_OTHER } pt_boot_t;

/* Returns true when this boot means "turn off". */
static inline bool pt_decide(bool stored_on, pt_boot_t boot)
{
    return boot == PT_BOOT_PIN_OR_POWERON && stored_on;
}

/* Value to store right after the decision, or NULL to leave NVS as is.
 * Off -> "0". A pin/power-on boot that stays on is already "0" (nothing to
 * write), so a press before arming boots again.
 * Other reasons keep the stored value untouched (no flash write). */
static inline const char *pt_after_decide(bool stored_on, pt_boot_t boot)
{
    if (pt_decide(stored_on, boot)) {
        return "0";
    }
    return NULL;
}
