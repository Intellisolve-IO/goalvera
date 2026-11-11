#pragma once

#include "wifi_list.h"
#include <stdbool.h>
#include <stdint.h>

typedef struct {
    bool configured;  /* Wi-Fi is running (the network picker works) */
    bool connected;
    bool connecting;  /* a network picked on screen is being joined */
    char ssid[33];    /* network the recorder is on, or trying */
    int bars;
} wifi_status_t;

/* Result of the last network picked on screen. */
typedef enum {
    WIFI_SW_NONE = 0,
    WIFI_SW_CONNECTING,
    WIFI_SW_OK,
    WIFI_SW_FAILED,
} wifi_sw_state_t;

typedef struct {
    wifi_sw_state_t state;
    char ssid[33];      /* the network that was picked */
    char fallback[33];  /* WIFI_SW_FAILED: the network it went back to ("" = none) */
    bool auth_failed;   /* WIFI_SW_FAILED: the password looked wrong */
    uint32_t seq;       /* bumps on every change */
} wifi_switch_t;

void timezone_init(void);
/* Starts Wi-Fi (compiled-in networks + networks saved on the device). */
void wifi_start_if_configured(void);
void wifi_get_status(wifi_status_t *out);
void wifi_get_switch(wifi_switch_t *out);

/* Network picker. All of these return at once; the work runs on the
 * system event task, never on the caller's (LVGL) thread. */
bool wifi_scan_request(void);
bool wifi_scan_busy(void);
/* Copies the latest scan (strongest first); *seq bumps on each new scan. */
int wifi_scan_results(wifi_seen_t *out, int max, uint32_t *seq);
/* Compiled-in and on-device saved networks (names only, no passwords). */
int wifi_saved_list(wifi_saved_t *out, int max);
/* One-tap switch to a saved network. */
bool wifi_connect_saved(const char *ssid);
/* Join a network that is not saved yet ("" password for an open one).
 * Saved on the device only once it connects. The password is never
 * logged. */
bool wifi_connect_new(const char *ssid, const char *pass);
