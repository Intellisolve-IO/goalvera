#pragma once
/*
 * Wi-Fi network picker model (, 0.6.3). Pure C, no ESP-IDF headers,
 * so test/host/test_wifi_list.c can run it on the host.
 *
 * - wifi_seen_*: scan results, one entry per network name (strongest radio
 *   wins), strongest first, hidden (empty-name) networks dropped.
 * - wifi_rows_build: what the picker lists. The connected network first,
 *   then saved networks in range, then other networks in range (strongest
 *   first), then saved networks that the scan did not see ("not in range",
 *   still tappable in case the scan missed a phone hotspot).
 * - wifi_saved_slot: which of the on-device slots a newly joined network
 *   goes into (same name = same slot, then a free slot, then round robin).
 * - wifi_pass_ok: WPA rules (8-63 printable characters or 64 hex digits);
 *   open networks take an empty password.
 *
 * Passwords never pass through this file.
 */
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#define WIFI_SSID_LEN   33 /* 32 + NUL */
#define WIFI_PASS_LEN   65 /* 64 + NUL */
#define WIFI_SAVED_MAX  4  /* networks joined on the device, kept in NVS */
#define WIFI_NETS_MAX   (2 + WIFI_SAVED_MAX) /* + the two compiled in */
#define WIFI_SEEN_MAX   12
#define WIFI_ROWS_MAX   (WIFI_SEEN_MAX + WIFI_NETS_MAX)

typedef struct {
    char ssid[WIFI_SSID_LEN];
    int8_t rssi;
    bool open;
} wifi_seen_t;

typedef struct {
    char ssid[WIFI_SSID_LEN];
    bool builtin; /* compiled into this firmware image */
} wifi_saved_t;

typedef struct {
    char ssid[WIFI_SSID_LEN];
    int8_t rssi;     /* valid when in_range */
    bool in_range;
    bool open;
    bool saved;
    bool builtin;
    bool connected;
} wifi_row_t;

static inline int wifi_bars_from_rssi(int rssi)
{
    if (rssi >= -55) return 4;
    if (rssi >= -65) return 3;
    if (rssi >= -75) return 2;
    if (rssi >= -85) return 1;
    return 0;
}

/* Add one scan record. Keeps the list sorted strongest first and at most
 * max long (the weakest falls off). Returns the new count. */
static inline int wifi_seen_add(wifi_seen_t *list, int count, int max, const char *ssid, int rssi, bool open)
{
    if (!ssid || !ssid[0] || max <= 0) {
        return count;
    }
    for (int i = 0; i < count; i++) {
        if (strncmp(list[i].ssid, ssid, WIFI_SSID_LEN - 1) == 0) {
            if (rssi <= list[i].rssi) {
                return count;
            }
            /* Stronger radio for the same name: remove, re-insert below. */
            memmove(&list[i], &list[i + 1], sizeof list[0] * (size_t)(count - i - 1));
            count--;
            break;
        }
    }
    int pos = count;
    while (pos > 0 && list[pos - 1].rssi < rssi) {
        pos--;
    }
    if (pos >= max) {
        return count;
    }
    int keep = count < max ? count : max - 1;
    memmove(&list[pos + 1], &list[pos], sizeof list[0] * (size_t)(keep - pos));
    memset(&list[pos], 0, sizeof list[pos]);
    strncpy(list[pos].ssid, ssid, WIFI_SSID_LEN - 1);
    list[pos].rssi = (int8_t)(rssi < -127 ? -127 : rssi > 0 ? 0 : rssi);
    list[pos].open = open;
    return keep + 1;
}

static inline int wifi_saved_find(const wifi_saved_t *saved, int n, const char *ssid)
{
    for (int i = 0; i < n; i++) {
        if (ssid && strcmp(saved[i].ssid, ssid) == 0) {
            return i;
        }
    }
    return -1;
}

/* connected_ssid: the network the recorder is on now (NULL/"" if none). */
static inline int wifi_rows_build(wifi_row_t *rows, int max, const wifi_seen_t *seen, int n_seen,
                                  const wifi_saved_t *saved, int n_saved, const char *connected_ssid)
{
    int n = 0;
    bool have_conn = connected_ssid && connected_ssid[0];
    /* pass 0: connected, 1: saved in range, 2: other in range, 3: saved not in range */
    for (int pass = 0; pass < 4; pass++) {
        if (pass < 3) {
            for (int i = 0; i < n_seen && n < max; i++) {
                bool conn = have_conn && strcmp(seen[i].ssid, connected_ssid) == 0;
                int si = wifi_saved_find(saved, n_saved, seen[i].ssid);
                if ((pass == 0) != conn || (pass == 1 && si < 0) || (pass == 2 && si >= 0)) {
                    continue;
                }
                wifi_row_t *r = &rows[n++];
                memset(r, 0, sizeof *r);
                strncpy(r->ssid, seen[i].ssid, WIFI_SSID_LEN - 1);
                r->rssi = seen[i].rssi;
                r->in_range = true;
                r->open = seen[i].open;
                r->saved = si >= 0;
                r->builtin = si >= 0 && saved[si].builtin;
                r->connected = conn;
            }
            if (pass == 0 && n == 0 && have_conn && n < max) {
                /* Connected but not in this scan (scan ran before joining). */
                int si = wifi_saved_find(saved, n_saved, connected_ssid);
                wifi_row_t *r = &rows[n++];
                memset(r, 0, sizeof *r);
                strncpy(r->ssid, connected_ssid, WIFI_SSID_LEN - 1);
                r->saved = si >= 0;
                r->builtin = si >= 0 && saved[si].builtin;
                r->connected = true;
            }
        } else {
            for (int i = 0; i < n_saved && n < max; i++) {
                bool listed = false;
                for (int j = 0; j < n; j++) {
                    if (strcmp(rows[j].ssid, saved[i].ssid) == 0) {
                        listed = true;
                        break;
                    }
                }
                if (listed || !saved[i].ssid[0]) {
                    continue;
                }
                wifi_row_t *r = &rows[n++];
                memset(r, 0, sizeof *r);
                strncpy(r->ssid, saved[i].ssid, WIFI_SSID_LEN - 1);
                r->saved = true;
                r->builtin = saved[i].builtin;
            }
        }
    }
    return n;
}

/* slot_ssids: the WIFI_SAVED_MAX on-device slots ("" = free). *rr is the
 * round-robin counter (persisted by the caller) and advances only when a
 * slot has to be reused. */
static inline int wifi_saved_slot(char slot_ssids[][WIFI_SSID_LEN], int slots, const char *ssid, uint32_t *rr)
{
    for (int i = 0; i < slots; i++) {
        if (strcmp(slot_ssids[i], ssid) == 0) {
            return i;
        }
    }
    for (int i = 0; i < slots; i++) {
        if (!slot_ssids[i][0]) {
            return i;
        }
    }
    int s = (int)(*rr % (uint32_t)slots);
    (*rr)++;
    return s;
}

static inline bool wifi_pass_ok(const char *pass, bool open)
{
    size_t n = pass ? strlen(pass) : 0;
    if (open) {
        return n == 0;
    }
    if (n == 64) {
        for (size_t i = 0; i < n; i++) {
            char c = pass[i];
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'))) {
                return false;
            }
        }
        return true;
    }
    if (n < 8 || n > 63) {
        return false;
    }
    for (size_t i = 0; i < n; i++) {
        if (pass[i] < 0x20 || pass[i] > 0x7E) {
            return false;
        }
    }
    return true;
}
