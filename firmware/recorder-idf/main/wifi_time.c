#include "wifi_time.h"

#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_sntp.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "nvs_prefs.h"
#include "rtc_pcf8563.h"
#include "sdkconfig.h"
#include "status_line.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/*
 * Wi-Fi for the recorder: clock sync (NTP) and uploads.
 *
 * Networks, in the order they are tried:
 *   - the one last picked on screen ("wf_pref" in NVS), if still known;
 *   - the compiled-in RECORDER_WIFI_SSID, then RECORDER_WIFI_SSID2;
 *   - up to WIFI_SAVED_MAX networks joined on the device (NVS slots
 *     "wf_s<n>"/"wf_p<n>", written by the prefs task, never logged).
 * After TRIES_PER_NET failed attempts in a row the recorder moves to the
 * next one and keeps going round until one connects.
 *
 * The network picker (ui_app.c) asks for a scan or a switch through
 * wifi_scan_request() / wifi_connect_*(). Those only post an event; all
 * Wi-Fi state changes run here, on the system event task, one at a time,
 * so the LVGL thread never waits on Wi-Fi. A switch that has not connected
 * after MANUAL_TRIES attempts or MANUAL_TIMEOUT_S goes back to the network
 * it was on (or the normal rotation), so the recorder never stays offline.
 *
 * Passwords are never logged or shown. The Wi-Fi driver keeps its config
 * in RAM only (esp_wifi_set_storage), so switching does not write flash.
 * The event task stack is small (2304 bytes): big buffers here are static.
 */

static const char *TAG = "wifi";

#define TRIES_PER_NET    3
#define MANUAL_TRIES     2
#define MANUAL_TIMEOUT_S 25
#define SCAN_RETRY_MS    300
#define SCAN_RETRIES     10
#define SCAN_RECORDS_MAX 24
#define KICK_PERIOD_S    10
#define KICK_IDLE_S      20

ESP_EVENT_DEFINE_BASE(RECORDER_WIFI_CMD);
enum { CMD_SCAN, CMD_CONNECT, CMD_TIMEOUT, CMD_KICK };

typedef struct {
    char ssid[WIFI_SSID_LEN];
    char pass[WIFI_PASS_LEN];
    bool is_new;
} wifi_cmd_t;

typedef struct {
    char ssid[WIFI_SSID_LEN];
    char pass[WIFI_PASS_LEN];
    bool builtin;
    int slot; /* NVS slot, -1 for compiled-in */
} wifi_net_t;

/* Shared with the getters (any task): guarded by s_mux. */
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static bool s_started;
static bool s_connected;
static bool s_manual;
static char s_ssid[WIFI_SSID_LEN];
static int s_bars;
static wifi_switch_t s_sw;
static wifi_seen_t s_seen[WIFI_SEEN_MAX];
static int s_seen_n;
static uint32_t s_seen_seq;
static bool s_scan_busy;
static wifi_net_t s_nets[WIFI_NETS_MAX];
static int s_net_count;

/* Event task only (and wifi_start_if_configured before the handlers run). */
static bool s_announced_down;
static bool s_sntp_started;
static int s_net_idx = -1;
static int s_fails;
static bool s_scanning;
static bool s_hold; /* reconnects paused while a scan runs */
static bool s_expect_leave;
static int s_scan_tries;
static wifi_net_t s_trial;
static bool s_trial_new;
static int s_prev_idx = -1;
static int s_manual_fails;
static bool s_manual_auth;
static uint32_t s_gen;
static int64_t s_last_try_us;
static esp_timer_handle_t s_manual_timer;
static esp_timer_handle_t s_retry_timer;
static esp_timer_handle_t s_kick_timer;
static char s_slot_ssid[WIFI_SAVED_MAX][WIFI_SSID_LEN];
static uint32_t s_rr;
static char s_msg[STATUS_MSG_LEN];
static char s_pref[WIFI_SSID_LEN]; /* NVS "wf_pref", read once at boot */
static wifi_config_t s_wc;

#define LOCK()   portENTER_CRITICAL(&s_mux)
#define UNLOCK() portEXIT_CRITICAL(&s_mux)

static void post(int32_t id, const void *data, size_t len)
{
    esp_err_t e = esp_event_post(RECORDER_WIFI_CMD, id, data, len, 0);
    if (e != ESP_OK) {
        ESP_LOGW(TAG, "event %ld not posted: %s", (long)id, esp_err_to_name(e));
    }
}

static int find_net(const char *ssid)
{
    for (int i = 0; i < s_net_count; i++) {
        if (strcmp(s_nets[i].ssid, ssid) == 0) {
            return i;
        }
    }
    return -1;
}

static void set_config(const wifi_net_t *n)
{
    memset(&s_wc, 0, sizeof s_wc);
    strlcpy((char *)s_wc.sta.ssid, n->ssid, sizeof s_wc.sta.ssid);
    if (strlen(n->pass) == 64) {
        memcpy(s_wc.sta.password, n->pass, 64); /* raw PSK (hex), no NUL */
    } else {
        strlcpy((char *)s_wc.sta.password, n->pass, sizeof s_wc.sta.password);
    }
    s_wc.sta.threshold.authmode = n->pass[0] ? WIFI_AUTH_WPA_PSK : WIFI_AUTH_OPEN;
    s_wc.sta.pmf_cfg.capable = true;
    s_wc.sta.pmf_cfg.required = false;
    esp_err_t e = esp_wifi_set_config(WIFI_IF_STA, &s_wc);
    memset(&s_wc, 0, sizeof s_wc);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_set_config failed: %s", esp_err_to_name(e));
    }
    LOCK();
    strlcpy(s_ssid, n->ssid, sizeof s_ssid);
    UNLOCK();
    ESP_LOGI(TAG, "connecting to \"%s\"", n->ssid);
}

static void connect_now(void)
{
    s_last_try_us = esp_timer_get_time();
    esp_err_t e = esp_wifi_connect();
    if (e != ESP_OK) {
        ESP_LOGW(TAG, "esp_wifi_connect: %s", esp_err_to_name(e));
    }
}

static void apply_net(int idx)
{
    s_net_idx = idx;
    set_config(&s_nets[idx]);
}

/* Leave the current network (if any) and start joining n. */
static void switch_to(const wifi_net_t *n)
{
    s_expect_leave = true;
    esp_wifi_disconnect();
    set_config(n);
    connect_now();
}

static int start_index(void)
{
    if (s_net_count == 0) {
        return -1;
    }
    int i = s_pref[0] ? find_net(s_pref) : -1;
    return i >= 0 ? i : 0;
}

/* ---- saved networks (NVS) ---- */

static void add_net_locked(const char *ssid, const char *pass, bool builtin, int slot)
{
    if (s_net_count >= WIFI_NETS_MAX) {
        return;
    }
    wifi_net_t *n = &s_nets[s_net_count++];
    memset(n, 0, sizeof *n);
    strlcpy(n->ssid, ssid, sizeof n->ssid);
    strlcpy(n->pass, pass, sizeof n->pass);
    n->builtin = builtin;
    n->slot = slot;
}

static void load_saved(void)
{
    char key[16];
    char buf[16];
    for (int s = 0; s < WIFI_SAVED_MAX; s++) {
        snprintf(key, sizeof key, "wf_s%d", s);
        prefs_get(key, s_slot_ssid[s], sizeof s_slot_ssid[s]);
        if (!s_slot_ssid[s][0] || find_net(s_slot_ssid[s]) >= 0) {
            continue; /* empty, or same name as a compiled-in network */
        }
        snprintf(key, sizeof key, "wf_p%d", s);
        prefs_get(key, s_trial.pass, sizeof s_trial.pass);
        add_net_locked(s_slot_ssid[s], s_trial.pass, false, s);
        memset(s_trial.pass, 0, sizeof s_trial.pass);
    }
    prefs_get("wf_pref", s_pref, sizeof s_pref);
    if (prefs_get("wf_n", buf, sizeof buf) == ESP_OK) {
        s_rr = (uint32_t)strtoul(buf, NULL, 10);
    }
}

/* s_trial just connected and is not compiled in: keep it on the device. */
static int save_trial(void)
{
    char key[16];
    uint32_t rr = s_rr;
    int slot = wifi_saved_slot(s_slot_ssid, WIFI_SAVED_MAX, s_trial.ssid, &rr);
    if (rr != s_rr) {
        s_rr = rr;
        snprintf(key, sizeof key, "%lu", (unsigned long)rr);
        prefs_set_async("wf_n", key);
    }
    strlcpy(s_slot_ssid[slot], s_trial.ssid, sizeof s_slot_ssid[slot]);
    snprintf(key, sizeof key, "wf_p%d", slot);
    prefs_set_async(key, s_trial.pass);
    snprintf(key, sizeof key, "wf_s%d", slot);
    prefs_set_async(key, s_trial.ssid);

    int idx = -1;
    LOCK();
    for (int i = 0; i < s_net_count; i++) {
        if (s_nets[i].slot == slot || (!s_nets[i].builtin && strcmp(s_nets[i].ssid, s_trial.ssid) == 0)) {
            idx = i;
            break;
        }
    }
    if (idx < 0) {
        add_net_locked(s_trial.ssid, s_trial.pass, false, slot);
        idx = s_net_count - 1;
    } else {
        strlcpy(s_nets[idx].ssid, s_trial.ssid, sizeof s_nets[idx].ssid);
        strlcpy(s_nets[idx].pass, s_trial.pass, sizeof s_nets[idx].pass);
        s_nets[idx].slot = slot;
    }
    UNLOCK();
    ESP_LOGI(TAG, "saved \"%s\" on the device (slot %d)", s_trial.ssid, slot);
    return idx;
}

/* ---- picked on screen ---- */

static void manual_ok(void)
{
    esp_timer_stop(s_manual_timer);
    int idx = find_net(s_trial.ssid);
    if (s_trial_new && (idx < 0 || !s_nets[idx].builtin)) {
        idx = save_trial();
    }
    if (idx >= 0) {
        s_net_idx = idx;
    }
    strlcpy(s_pref, s_trial.ssid, sizeof s_pref);
    prefs_set_async("wf_pref", s_pref);
    LOCK();
    s_manual = false;
    s_sw.state = WIFI_SW_OK;
    s_sw.seq++;
    UNLOCK();
    memset(s_trial.pass, 0, sizeof s_trial.pass);
}

static void manual_failed(bool timeout)
{
    esp_timer_stop(s_manual_timer);
    int idx = (s_prev_idx >= 0 && s_prev_idx < s_net_count) ? s_prev_idx : start_index();
    if (idx >= 0 && strcmp(s_nets[idx].ssid, s_trial.ssid) == 0 && s_net_count > 1) {
        idx = (idx + 1) % s_net_count; /* don't fall back onto the one that just failed */
    }
    LOCK();
    s_manual = false;
    s_sw.state = WIFI_SW_FAILED;
    s_sw.auth_failed = s_manual_auth;
    strlcpy(s_sw.fallback, idx >= 0 ? s_nets[idx].ssid : "", sizeof s_sw.fallback);
    s_sw.seq++;
    UNLOCK();
    snprintf(s_msg, sizeof s_msg, "Couldn't join %.32s (%s)", s_trial.ssid,
             s_manual_auth ? "wrong password?" : timeout ? "no answer" : "not found");
    status_error(s_msg);
    memset(s_trial.pass, 0, sizeof s_trial.pass);
    s_fails = 0;
    if (idx >= 0) {
        s_net_idx = idx;
        switch_to(&s_nets[idx]);
    } else {
        esp_wifi_disconnect();
    }
}

static void manual_begin(const wifi_cmd_t *c)
{
    if (!s_started) {
        return;
    }
    if (s_scanning) {
        esp_wifi_scan_stop();
        s_scanning = false;
        LOCK();
        s_scan_busy = false;
        UNLOCK();
    }
    s_hold = false;
    if (c->is_new) {
        memset(&s_trial, 0, sizeof s_trial);
        strlcpy(s_trial.ssid, c->ssid, sizeof s_trial.ssid);
        strlcpy(s_trial.pass, c->pass, sizeof s_trial.pass);
        s_trial.slot = -1;
        s_trial_new = true;
    } else {
        int i = find_net(c->ssid);
        if (i < 0) {
            ESP_LOGW(TAG, "\"%s\" is not a saved network", c->ssid);
            LOCK();
            s_manual = false;
            s_sw.state = WIFI_SW_FAILED;
            s_sw.seq++;
            UNLOCK();
            return;
        }
        s_trial = s_nets[i];
        s_trial_new = false;
    }
    s_prev_idx = s_connected ? s_net_idx : -1;
    s_manual_fails = 0;
    s_manual_auth = false;
    s_gen++;
    LOCK();
    s_manual = true;
    s_connected = false;
    s_bars = 0;
    s_sw.state = WIFI_SW_CONNECTING;
    strlcpy(s_sw.ssid, s_trial.ssid, sizeof s_sw.ssid);
    s_sw.fallback[0] = '\0';
    s_sw.auth_failed = false;
    s_sw.seq++;
    UNLOCK();
    snprintf(s_msg, sizeof s_msg, "Switching Wi-Fi to %.32s", s_trial.ssid);
    status_set(s_msg);
    switch_to(&s_trial);
    esp_timer_stop(s_manual_timer);
    esp_timer_start_once(s_manual_timer, (uint64_t)MANUAL_TIMEOUT_S * 1000000ULL);
}

/* ---- scanning ---- */

static void scan_begin(void)
{
    if (!s_started || s_manual) {
        LOCK();
        s_scan_busy = false;
        UNLOCK();
        return;
    }
    if (s_scanning) {
        return;
    }
    if (!s_connected && !s_hold) {
        /* A scan can't start while the station is busy connecting. */
        s_hold = true;
        esp_wifi_disconnect();
    }
    wifi_scan_config_t sc = {0};
    sc.show_hidden = false;
    sc.scan_type = WIFI_SCAN_TYPE_ACTIVE;
    esp_err_t e = esp_wifi_scan_start(&sc, false);
    if (e == ESP_OK) {
        s_scanning = true;
        s_scan_tries = 0;
        return;
    }
    if (e == ESP_ERR_WIFI_STATE && ++s_scan_tries <= SCAN_RETRIES) {
        esp_timer_stop(s_retry_timer);
        esp_timer_start_once(s_retry_timer, SCAN_RETRY_MS * 1000ULL);
        return;
    }
    ESP_LOGW(TAG, "scan did not start: %s", esp_err_to_name(e));
    s_scan_tries = 0;
    s_hold = false;
    LOCK();
    s_scan_busy = false;
    s_seen_seq++; /* let the picker stop showing "Looking..." */
    UNLOCK();
    if (!s_connected && s_net_count > 0) {
        connect_now();
    }
}

static wifi_seen_t s_seen_tmp[WIFI_SEEN_MAX];

static void scan_done(void)
{
    if (!s_scanning) {
        esp_wifi_clear_ap_list();
        return;
    }
    s_scanning = false;
    uint16_t num = 0;
    esp_wifi_scan_get_ap_num(&num);
    if (num > SCAN_RECORDS_MAX) {
        num = SCAN_RECORDS_MAX;
    }
    int n = 0;
    wifi_ap_record_t *recs = num ? heap_caps_calloc(num, sizeof *recs, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) : NULL;
    if (recs && esp_wifi_scan_get_ap_records(&num, recs) == ESP_OK) {
        for (int i = 0; i < num; i++) {
            n = wifi_seen_add(s_seen_tmp, n, WIFI_SEEN_MAX, (const char *)recs[i].ssid, recs[i].rssi,
                              recs[i].authmode == WIFI_AUTH_OPEN);
        }
    }
    esp_wifi_clear_ap_list();
    free(recs);
    LOCK();
    memcpy(s_seen, s_seen_tmp, sizeof s_seen);
    s_seen_n = n;
    s_seen_seq++;
    s_scan_busy = false;
    UNLOCK();
    ESP_LOGI(TAG, "scan: %d networks", n);
    s_hold = false;
    if (!s_connected && !s_manual && s_net_count > 0) {
        connect_now();
    }
}

/* ---- events ---- */

static void on_time_sync(struct timeval *tv)
{
    (void)tv;
    /* Save to the PCF8563 so the coin cell keeps Eastern time offline. */
    if (rtc_set_from_system() == ESP_OK) {
        status_set("Clock set from internet");
    } else {
        status_error("Clock set from internet, but saving to the RTC failed");
    }
}

static void start_sntp(void)
{
    if (s_sntp_started) {
        return;
    }
    s_sntp_started = true;
    esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, "pool.ntp.org");
    esp_sntp_setservername(1, "time.nist.gov");
    esp_sntp_set_time_sync_notification_cb(on_time_sync);
    esp_sntp_init();
    ESP_LOGI(TAG, "SNTP started (America/New_York)");
}

static bool auth_reason(uint8_t r)
{
    return r == WIFI_REASON_AUTH_FAIL || r == WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT ||
           r == WIFI_REASON_HANDSHAKE_TIMEOUT || r == WIFI_REASON_AUTH_EXPIRE;
}

static void on_disconnected(const wifi_event_sta_disconnected_t *ev)
{
    uint8_t reason = ev ? ev->reason : 0;
    LOCK();
    s_connected = false;
    s_bars = 0;
    UNLOCK();
    if (s_expect_leave && reason == WIFI_REASON_ASSOC_LEAVE) {
        s_expect_leave = false; /* our own switch; the next attempt is already running */
        return;
    }
    if (s_hold || s_scanning) {
        return; /* scan_done() reconnects */
    }
    if (s_manual) {
        ESP_LOGI(TAG, "\"%s\" attempt %d failed (reason %u)", s_trial.ssid, s_manual_fails + 1, reason);
        if (auth_reason(reason)) {
            s_manual_auth = true;
        }
        if (++s_manual_fails >= MANUAL_TRIES) {
            manual_failed(false);
        } else {
            connect_now();
        }
        return;
    }
    if (!s_announced_down) {
        s_announced_down = true;
        status_set("Wi-Fi disconnected, retrying");
    }
    if (s_net_count == 0) {
        return;
    }
    if (s_net_idx < 0) {
        apply_net(start_index());
    } else if (s_net_count > 1 && ++s_fails >= TRIES_PER_NET) {
        s_fails = 0;
        apply_net((s_net_idx + 1) % s_net_count);
    }
    connect_now();
}

static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        if (s_net_count > 0) {
            connect_now();
        }
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        on_disconnected(data);
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_SCAN_DONE) {
        scan_done();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        s_expect_leave = false;
        s_announced_down = false;
        s_fails = 0;
        LOCK();
        s_connected = true;
        UNLOCK();
        if (s_manual) {
            manual_ok();
        }
        ip_event_got_ip_t *ev = data;
        ESP_LOGI(TAG, "got IP " IPSTR, IP2STR(&ev->ip_info.ip));
        snprintf(s_msg, sizeof s_msg, "Wi-Fi connected: %.32s", s_ssid);
        status_set(s_msg);
        start_sntp();
    } else if (base == RECORDER_WIFI_CMD) {
        switch (id) {
        case CMD_SCAN:
            scan_begin();
            break;
        case CMD_CONNECT:
            manual_begin(data);
            /* esp_event keeps its own copy of the data: wipe the password. */
            memset(((wifi_cmd_t *)data)->pass, 0, WIFI_PASS_LEN);
            break;
        case CMD_TIMEOUT:
            if (s_manual && data && *(const uint32_t *)data == s_gen) {
                manual_failed(true);
            }
            break;
        case CMD_KICK:
            if (s_started && !s_connected && !s_scanning && !s_hold && !s_manual && s_net_count > 0 &&
                esp_timer_get_time() - s_last_try_us > (int64_t)KICK_IDLE_S * 1000000) {
                ESP_LOGW(TAG, "no Wi-Fi progress for %d s, trying again", KICK_IDLE_S);
                if (s_net_idx < 0) {
                    apply_net(start_index());
                }
                connect_now();
            }
            break;
        default:
            break;
        }
    }
}

static void manual_timer_cb(void *arg)
{
    (void)arg;
    uint32_t gen = s_gen;
    post(CMD_TIMEOUT, &gen, sizeof gen);
}

static void retry_timer_cb(void *arg)
{
    (void)arg;
    post(CMD_SCAN, NULL, 0);
}

static void kick_timer_cb(void *arg)
{
    (void)arg;
    post(CMD_KICK, NULL, 0);
}

void timezone_init(void)
{
    /* POSIX rule for America/New_York, including US DST. */
    setenv("TZ", "EST5EDT,M3.2.0,M11.1.0", 1);
    tzset();
}

/* Wi-Fi is optional. A failure must never abort the recorder. */
#define WIFI_TRY(x)                                                        \
    do {                                                                   \
        esp_err_t e_ = (x);                                                \
        if (e_ != ESP_OK) {                                                \
            ESP_LOGE(TAG, "%s failed: %s", #x, esp_err_to_name(e_));       \
            status_error("Wi-Fi could not start");                         \
            return;                                                        \
        }                                                                  \
    } while (0)

void wifi_start_if_configured(void)
{
    s_net_count = 0;
    if (CONFIG_RECORDER_WIFI_SSID[0] != '\0') {
        add_net_locked(CONFIG_RECORDER_WIFI_SSID, CONFIG_RECORDER_WIFI_PASSWORD, true, -1);
    }
    if (CONFIG_RECORDER_WIFI_SSID2[0] != '\0' && find_net(CONFIG_RECORDER_WIFI_SSID2) < 0) {
        add_net_locked(CONFIG_RECORDER_WIFI_SSID2, CONFIG_RECORDER_WIFI_PASSWORD2, true, -1);
    }
    int builtin = s_net_count;
    load_saved();
    ESP_LOGI(TAG, "%d network(s) known: %d compiled in, %d saved on this device", s_net_count, builtin,
             s_net_count - builtin);

    WIFI_TRY(esp_netif_init());
    esp_err_t lerr = esp_event_loop_create_default();
    if (lerr != ESP_OK && lerr != ESP_ERR_INVALID_STATE) {
        WIFI_TRY(lerr);
    }
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t icfg = WIFI_INIT_CONFIG_DEFAULT();
    WIFI_TRY(esp_wifi_init(&icfg));
    /* Config lives in RAM: switching networks never writes flash. */
    WIFI_TRY(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    WIFI_TRY(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL));
    WIFI_TRY(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL));
    WIFI_TRY(esp_event_handler_register(RECORDER_WIFI_CMD, ESP_EVENT_ANY_ID, wifi_event, NULL));

    const esp_timer_create_args_t mt = {.callback = manual_timer_cb, .name = "wifi_sw"};
    const esp_timer_create_args_t rt = {.callback = retry_timer_cb, .name = "wifi_scan"};
    const esp_timer_create_args_t kt = {.callback = kick_timer_cb, .name = "wifi_kick"};
    WIFI_TRY(esp_timer_create(&mt, &s_manual_timer));
    WIFI_TRY(esp_timer_create(&rt, &s_retry_timer));
    WIFI_TRY(esp_timer_create(&kt, &s_kick_timer));

    WIFI_TRY(esp_wifi_set_mode(WIFI_MODE_STA));
    int first = start_index();
    if (first >= 0) {
        if (s_net_count > 1) {
            ESP_LOGI(TAG, "trying \"%s\" first, then the other %d", s_nets[first].ssid, s_net_count - 1);
        }
        apply_net(first);
    } else {
        ESP_LOGI(TAG, "no network known yet: pick one with the Wi-Fi icon");
    }
    WIFI_TRY(esp_wifi_start());
    LOCK();
    s_started = true;
    UNLOCK();
    esp_timer_start_periodic(s_kick_timer, (uint64_t)KICK_PERIOD_S * 1000000ULL);
}

void wifi_get_status(wifi_status_t *out)
{
    if (!out) {
        return;
    }
    memset(out, 0, sizeof *out);
    LOCK();
    out->configured = s_started;
    out->connected = s_connected;
    out->connecting = s_manual;
    strlcpy(out->ssid, s_ssid, sizeof out->ssid);
    bool query = s_started && s_connected;
    UNLOCK();
    if (query) {
        wifi_ap_record_t ap;
        if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
            s_bars = wifi_bars_from_rssi(ap.rssi);
        }
        out->bars = s_bars;
    }
}

void wifi_get_switch(wifi_switch_t *out)
{
    if (!out) {
        return;
    }
    LOCK();
    *out = s_sw;
    UNLOCK();
}

bool wifi_scan_request(void)
{
    LOCK();
    bool ok = s_started && !s_scan_busy && !s_manual;
    if (ok) {
        s_scan_busy = true;
    }
    UNLOCK();
    if (ok) {
        post(CMD_SCAN, NULL, 0);
    }
    return ok;
}

bool wifi_scan_busy(void)
{
    LOCK();
    bool b = s_scan_busy;
    UNLOCK();
    return b;
}

int wifi_scan_results(wifi_seen_t *out, int max, uint32_t *seq)
{
    LOCK();
    int n = s_seen_n < max ? s_seen_n : max;
    memcpy(out, s_seen, sizeof out[0] * (size_t)n);
    if (seq) {
        *seq = s_seen_seq;
    }
    UNLOCK();
    return n;
}

int wifi_saved_list(wifi_saved_t *out, int max)
{
    LOCK();
    int n = s_net_count < max ? s_net_count : max;
    for (int i = 0; i < n; i++) {
        memcpy(out[i].ssid, s_nets[i].ssid, sizeof out[i].ssid);
        out[i].builtin = s_nets[i].builtin;
    }
    UNLOCK();
    return n;
}

static bool post_connect(const char *ssid, const char *pass, bool is_new)
{
    if (!ssid || !ssid[0] || strlen(ssid) > 32 || (pass && strlen(pass) > 64)) {
        return false;
    }
    LOCK();
    bool ok = s_started;
    if (ok) {
        /* Show "Connecting..." at once; manual_begin() confirms it. */
        s_manual = true;
        s_sw.state = WIFI_SW_CONNECTING;
        strlcpy(s_sw.ssid, ssid, sizeof s_sw.ssid);
        s_sw.fallback[0] = '\0';
        s_sw.auth_failed = false;
        s_sw.seq++;
    }
    UNLOCK();
    if (!ok) {
        return false;
    }
    wifi_cmd_t c;
    memset(&c, 0, sizeof c);
    strlcpy(c.ssid, ssid, sizeof c.ssid);
    if (pass) {
        strlcpy(c.pass, pass, sizeof c.pass);
    }
    c.is_new = is_new;
    esp_err_t e = esp_event_post(RECORDER_WIFI_CMD, CMD_CONNECT, &c, sizeof c, pdMS_TO_TICKS(100));
    memset(&c, 0, sizeof c);
    if (e != ESP_OK) {
        ESP_LOGW(TAG, "switch not posted: %s", esp_err_to_name(e));
        LOCK();
        s_manual = false;
        s_sw.state = WIFI_SW_FAILED;
        s_sw.seq++;
        UNLOCK();
        return false;
    }
    return true;
}

bool wifi_connect_saved(const char *ssid)
{
    return post_connect(ssid, NULL, false);
}

bool wifi_connect_new(const char *ssid, const char *pass)
{
    return post_connect(ssid, pass ? pass : "", true);
}
