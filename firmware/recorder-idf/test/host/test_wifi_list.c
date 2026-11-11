/* Host unit test for the Wi-Fi picker model (main/wifi_list.h).
 *   cc -Wall -Imain test/host/test_wifi_list.c -o /tmp/tw && /tmp/tw */
#include "wifi_list.h"
#include <assert.h>
#include <stdio.h>

int main(void)
{
    /* Scan list: dedupe by name keeping the strongest, strongest first,
     * hidden networks dropped, capped. */
    wifi_seen_t s[4];
    int n = 0;
    n = wifi_seen_add(s, n, 4, "HomeNet", -70, false);
    n = wifi_seen_add(s, n, 4, "", -30, false);
    n = wifi_seen_add(s, n, 4, "PhoneHotspot", -50, false);
    n = wifi_seen_add(s, n, 4, "HomeNet", -60, false); /* second radio, stronger */
    n = wifi_seen_add(s, n, 4, "HomeNet", -80, false); /* weaker: ignored */
    n = wifi_seen_add(s, n, 4, "Cafe", -90, true);
    n = wifi_seen_add(s, n, 4, "Neighbor", -75, false);
    assert(n == 4);
    assert(!strcmp(s[0].ssid, "PhoneHotspot") && s[0].rssi == -50);
    assert(!strcmp(s[1].ssid, "HomeNet") && s[1].rssi == -60);
    assert(!strcmp(s[2].ssid, "Neighbor"));
    assert(!strcmp(s[3].ssid, "Cafe") && s[3].open);
    n = wifi_seen_add(s, n, 4, "Strong", -40, false); /* full: weakest falls off */
    assert(n == 4 && !strcmp(s[0].ssid, "Strong") && !strcmp(s[3].ssid, "Neighbor"));
    n = wifi_seen_add(s, n, 4, "Weak", -95, false);   /* full and weakest: dropped */
    assert(n == 4 && !strcmp(s[3].ssid, "Neighbor"));
    /* A 32-character name is kept whole. */
    wifi_seen_t l[1];
    assert(wifi_seen_add(l, 0, 1, "abcdefghijklmnopqrstuvwxyz012345", -50, false) == 1);
    assert(strlen(l[0].ssid) == 32);

    /* Bars. */
    assert(wifi_bars_from_rssi(-50) == 4 && wifi_bars_from_rssi(-60) == 3);
    assert(wifi_bars_from_rssi(-70) == 2 && wifi_bars_from_rssi(-80) == 1 && wifi_bars_from_rssi(-90) == 0);

    /* Rows: connected, saved in range, others by strength, saved not in range. */
    wifi_seen_t seen[5];
    int ns = 0;
    ns = wifi_seen_add(seen, ns, 5, "Neighbor", -48, false);
    ns = wifi_seen_add(seen, ns, 5, "HomeNet", -62, false);
    ns = wifi_seen_add(seen, ns, 5, "Library", -71, false);
    ns = wifi_seen_add(seen, ns, 5, "Cafe", -80, true);
    wifi_saved_t saved[3] = {{"PhoneHotspot", true}, {"HomeNet", true}, {"Library", false}};
    wifi_row_t rows[WIFI_ROWS_MAX];
    int nr = wifi_rows_build(rows, WIFI_ROWS_MAX, seen, ns, saved, 3, "HomeNet");
    assert(nr == 5);
    assert(!strcmp(rows[0].ssid, "HomeNet") && rows[0].connected && rows[0].saved && rows[0].builtin);
    assert(!strcmp(rows[1].ssid, "Library") && rows[1].saved && !rows[1].builtin && rows[1].in_range);
    assert(!strcmp(rows[2].ssid, "Neighbor") && !rows[2].saved);
    assert(!strcmp(rows[3].ssid, "Cafe") && rows[3].open);
    assert(!strcmp(rows[4].ssid, "PhoneHotspot") && rows[4].saved && !rows[4].in_range);
    /* Connected network missing from the scan still comes first. */
    nr = wifi_rows_build(rows, WIFI_ROWS_MAX, seen, ns, saved, 3, "PhoneHotspot");
    assert(nr == 5 && !strcmp(rows[0].ssid, "PhoneHotspot") && rows[0].connected && !rows[0].in_range);
    assert(!strcmp(rows[1].ssid, "HomeNet") && !rows[1].connected);
    /* Not connected, nothing scanned yet: saved networks only. */
    nr = wifi_rows_build(rows, WIFI_ROWS_MAX, seen, 0, saved, 3, "");
    assert(nr == 3 && !rows[0].connected && !strcmp(rows[2].ssid, "Library"));
    /* Cap. */
    nr = wifi_rows_build(rows, 2, seen, ns, saved, 3, "HomeNet");
    assert(nr == 2);

    /* Saved slots: same name, then free, then round robin. */
    char slots[WIFI_SAVED_MAX][WIFI_SSID_LEN] = {"Library", "", "Cafe", ""};
    uint32_t rr = 0;
    assert(wifi_saved_slot(slots, WIFI_SAVED_MAX, "Cafe", &rr) == 2 && rr == 0);
    assert(wifi_saved_slot(slots, WIFI_SAVED_MAX, "New", &rr) == 1 && rr == 0);
    strcpy(slots[1], "New");
    strcpy(slots[3], "Other");
    assert(wifi_saved_slot(slots, WIFI_SAVED_MAX, "Fifth", &rr) == 0 && rr == 1);
    assert(wifi_saved_slot(slots, WIFI_SAVED_MAX, "Sixth", &rr) == 1 && rr == 2);

    /* Passwords. */
    assert(wifi_pass_ok("", true) && !wifi_pass_ok("x", true));
    assert(!wifi_pass_ok("", false) && !wifi_pass_ok("1234567", false));
    assert(wifi_pass_ok("12345678", false));
    assert(wifi_pass_ok("abcdefghijklmnopqrstuvwxyz0123456789abcdefghijklmnopqrstuvwxy12", false)); /* 63 */
    assert(!wifi_pass_ok("abcdefghijklmnopqrstuvwxyz0123456789abcdefghijklmnopqrstuvwxy12z", false)); /* 64 non-hex */
    assert(wifi_pass_ok("0123456789abcdef0123456789ABCDEF0123456789abcdef0123456789abcdef", false)); /* 64 hex */
    assert(!wifi_pass_ok("pass\tword", false));

    printf("wifi picker model: all tests passed\n");
    return 0;
}
