/* Host unit test for the RESET-button on/off logic (main/power_toggle.h).
 *   cc -Wall -Wextra -Imain test/host/test_power_toggle.c -o /tmp/tp && /tmp/tp */
#include "power_toggle.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

/* Simulated NVS + device: returns true if this boot turned off. */
static bool nv_on;
static int writes;
static void store(const char *v) { bool on = v[0] == '1'; if (on != nv_on) { nv_on = on; writes++; } }
static bool boot(pt_boot_t b, bool armed)
{
    bool off = pt_decide(nv_on, b);
    const char *v = pt_after_decide(nv_on, b);
    if (v) store(v);
    if (off) { store("0"); return true; } /* power_deep_sleep */
    if (armed) store("1");
    return false;
}

int main(void)
{
    /* Fresh board (no key) + plug in -> on. */
    nv_on = false;
    assert(!boot(PT_BOOT_PIN_OR_POWERON, true));
    /* Running (armed) + button -> off; next press -> on; next -> off. */
    assert(boot(PT_BOOT_PIN_OR_POWERON, false));
    assert(!nv_on);
    assert(!boot(PT_BOOT_PIN_OR_POWERON, true));
    assert(boot(PT_BOOT_PIN_OR_POWERON, false));
    /* Pressed again before arming (still booting) -> stays on. */
    assert(!boot(PT_BOOT_PIN_OR_POWERON, false));
    assert(!boot(PT_BOOT_PIN_OR_POWERON, false));
    /* Running + USB/JTAG reset, software/OTA restart, panic, brownout -> never off,
     * and no flash write. */
    nv_on = true; writes = 0;
    assert(!pt_decide(true, PT_BOOT_OTHER) && pt_after_decide(true, PT_BOOT_OTHER) == NULL);
    assert(!boot(PT_BOOT_OTHER, true));
    assert(writes == 0 && nv_on);
    /* Woken from deep sleep by BOOT -> on. */
    assert(!pt_decide(true, PT_BOOT_DEEPSLEEP));
    /* Armed boot with value already "1" writes nothing. */
    writes = 0; nv_on = true;
    assert(!boot(PT_BOOT_DEEPSLEEP, true));
    assert(writes == 0);
    puts("power_toggle: all tests passed");
    return 0;
}
