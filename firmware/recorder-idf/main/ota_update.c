#include "ota_update.h"

#include "sdkconfig.h"
#include "ota_policy.h"
#include "ota_sign.h"

#include "audio_rec.h"
#include "device_info.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/task.h"
#include "mbedtls/sha256.h"
#include "status_line.h"
#include "uploader.h"
#include "version.h"
#include "wifi_time.h"
#if CONFIG_RECORDER_BOARD_HOSYOND_35
#include "battery.h"
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *TAG = "ota";

static bool wifi_up(void)
{
    wifi_status_t w;
    wifi_get_status(&w);
    return w.connected;
}

static void collect_gate(ota_gate_in_t *g)
{
    audio_status_t a;
    upload_status_t u;
    memset(g, 0, sizeof *g);
    audio_get_status(&a);
    g->session_active = !(a.state == AUDIO_IDLE || a.state == AUDIO_ERROR);
    uploader_get_status(&u);
    g->upload_busy = u.busy;
    g->upload_waiting = u.waiting;
    g->upload_retry_wait = u.retry_wait;
#if CONFIG_RECORDER_BOARD_HOSYOND_35
    {
        battery_status_t b;
        battery_get(&b);
        g->has_battery = true;
        g->charging = b.src == BATT_SRC_CHARGING || b.src == BATT_SRC_USB_HOST ||
                      b.src == BATT_SRC_USB_NO_BATT;
        g->battery_known = b.src == BATT_SRC_USB_NO_BATT || (b.src != BATT_SRC_UNKNOWN && b.pct >= 0);
        g->battery_pct = b.pct;
    }
#else
    g->has_battery = false;
#endif
}

static bool token_ok(const char *s)
{
    if (!s) {
        return false;
    }
    for (const char *p = s; *p; p++) {
        char c = *p;
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '.' ||
              c == '_' || c == '-')) {
            return false;
        }
    }
    return true;
}

/* HTTP status, or -1 when the upload API was not reached. */
static int post_event(const char *event, const char *to_version, const char *detail)
{
    char body[320];
    const char *dev = device_id_str();
    if (!token_ok(event) || !token_ok(FW_VERSION) || !token_ok(dev) ||
        (to_version && to_version[0] && !token_ok(to_version)) ||
        (detail && detail[0] && !token_ok(detail))) {
        return -1;
    }
    snprintf(body, sizeof body,
             "{\"device_id\":\"%s\",\"event\":\"%s\",\"from_version\":\"%s\",\"to_version\":\"%s\",\"detail\":\"%s\"}",
             dev, event, FW_VERSION, to_version ? to_version : "", detail ? detail : "");
    return uploader_api_post("/v1/device/ota", body);
}

static void note(const char *msg)
{
    ESP_LOGI(TAG, "%s", msg);
    status_set(msg);
}

static bool pending_image(void)
{
    esp_ota_img_states_t st;
    const esp_partition_t *run = esp_ota_get_running_partition();
    return run && esp_ota_get_state_partition(run, &st) == ESP_OK && st == ESP_OTA_IMG_PENDING_VERIFY;
}

/* Confirm only after this boot has reached the upload API. Otherwise roll back. */
static void confirm_or_rollback(void)
{
    int64_t start = esp_timer_get_time();
    bool announced = false;
    while (pending_image()) {
        ota_rb_in_t in;
        int64_t now = esp_timer_get_time();
        upload_status_t u;
        memset(&in, 0, sizeof in);
        in.pending_verify = true;
        in.app_booted = true;
        in.wifi_up = wifi_up();
        in.timed_out = (now - start) >= (int64_t)OTA_ROLLBACK_TIMEOUT_S * 1000000LL;
        uploader_get_status(&u);
        if (in.wifi_up && !u.busy && uploader_net_mem_ok()) {
            int st = post_event("confirmed", FW_VERSION, "boot");
            in.upload_api_reached = st >= 100;
            if (in.upload_api_reached) {
                ESP_LOGI(TAG, "upload API HTTP %d", st);
            }
        }
        switch (ota_rollback_decide(&in)) {
        case OTA_RB_CONFIRM:
            if (esp_ota_mark_app_valid_cancel_rollback() == ESP_OK) {
                char msg[80];
                snprintf(msg, sizeof msg, "Firmware v%s confirmed", FW_VERSION);
                note(msg);
                return;
            }
            ESP_LOGE(TAG, "could not mark the image valid");
            break;
        case OTA_RB_ROLLBACK:
            note("Firmware update rolled back");
            post_event("rollback", FW_VERSION, "timeout");
            vTaskDelay(pdMS_TO_TICKS(200));
            esp_ota_mark_app_invalid_rollback_and_reboot();
            esp_restart();
            return;
        case OTA_RB_WAIT:
        case OTA_RB_HOLD:
        default:
            if (!announced) {
                note("Checking firmware update");
                announced = true;
            }
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}

static int http_get_open(const char *url, esp_http_client_handle_t *out)
{
    esp_http_client_config_t cfg = {
        .url = url,
        .method = HTTP_METHOD_GET,
        .timeout_ms = 60000,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .buffer_size = 4096,
        .buffer_size_tx = 1024,
        .disable_auto_redirect = true,
    };
    esp_http_client_handle_t c;
    if (!uploader_net_lock(30000)) {
        return -1;
    }
    c = esp_http_client_init(&cfg);
    if (!c || esp_http_client_open(c, 0) != ESP_OK || esp_http_client_fetch_headers(c) < 0) {
        if (c) {
            esp_http_client_close(c);
            esp_http_client_cleanup(c);
        }
        uploader_net_unlock();
        return -1;
    }
    *out = c;
    return esp_http_client_get_status_code(c);
}

static void http_get_close(esp_http_client_handle_t c)
{
    if (c) {
        esp_http_client_close(c);
        esp_http_client_cleanup(c);
    }
    uploader_net_unlock();
}

static void hex_encode(char out[OTA_SHA256_HEX + 1], const uint8_t dig[32])
{
    static const char *H = "0123456789abcdef";
    for (int i = 0; i < 32; i++) {
        out[i * 2] = H[dig[i] >> 4];
        out[i * 2 + 1] = H[dig[i] & 0xf];
    }
    out[OTA_SHA256_HEX] = '\0';
}

/* 0: manifest handled (not downloaded). 1: does not return (rebooting). -1: try again. */
static int download_and_switch(const ota_manifest_t *m)
{
    const esp_partition_t *part = esp_ota_get_next_update_partition(NULL);
    esp_http_client_handle_t c = NULL;
    esp_ota_handle_t handle = 0;
    mbedtls_sha256_context sha;
    uint8_t *buf = NULL;
    uint8_t dig[32];
    char hex[OTA_SHA256_HEX + 1];
    char msg[64];
    int status;
    int64_t cl = -1;
    size_t got = 0;
    size_t last_gate = 0;
    bool read_ok = false;
    bool hash_ok = false;
    const char *why = "http";
    ota_gate_in_t g;
    if (!part) {
        post_event("failed", m->version, "no_slot");
        return 0;
    }
    collect_gate(&g);
    if (ota_gate(&g) != OTA_ALLOW || !wifi_up()) {
        const char *code = ota_gate(&g) != OTA_ALLOW ? ota_block_code(ota_gate(&g)) : "wifi";
        post_event("blocked", m->version, code);
        return -1;
    }
    snprintf(msg, sizeof msg, "Updating firmware to v%s", m->version);
    note(msg);
    post_event("download", m->version, "start");
    buf = heap_caps_malloc(4096, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!buf) {
        return -1;
    }
    status = http_get_open(m->url, &c);
    if (status != 200 || !c) {
        ESP_LOGW(TAG, "image HTTP %d", status);
        if (c) {
            http_get_close(c);
        }
        free(buf);
        post_event("failed", m->version, "http");
        return -1;
    }
    cl = esp_http_client_get_content_length(c);
    if (cl == 0 || cl > (int64_t)part->size) {
        http_get_close(c);
        free(buf);
        post_event("rejected", m->version, "oversized");
        return 0;
    }
    if (esp_ota_begin(part, cl > 0 ? (size_t)cl : OTA_WITH_SEQUENTIAL_WRITES, &handle) != ESP_OK) {
        http_get_close(c);
        free(buf);
        post_event("failed", m->version, "ota_begin");
        return -1;
    }
    mbedtls_sha256_init(&sha);
    if (mbedtls_sha256_starts(&sha, 0) != 0) {
        mbedtls_sha256_free(&sha);
        http_get_close(c);
        free(buf);
        esp_ota_abort(handle);
        post_event("failed", m->version, "sha");
        return -1;
    }
    read_ok = true;
    while (read_ok) {
        int r = esp_http_client_read(c, (char *)buf, 4096);
        if (r < 0) {
            read_ok = false;
            why = "http";
            break;
        }
        if (r == 0) {
            break;
        }
        if (got + (size_t)r > part->size || mbedtls_sha256_update(&sha, buf, (size_t)r) != 0 ||
            esp_ota_write(handle, buf, (size_t)r) != ESP_OK) {
            read_ok = false;
            why = "write";
            break;
        }
        got += (size_t)r;
        if (got - last_gate >= 32768) {
            last_gate = got;
            collect_gate(&g);
            if (ota_gate(&g) != OTA_ALLOW || !wifi_up()) {
                read_ok = false;
                why = ota_gate(&g) == OTA_ALLOW ? "wifi" : ota_block_code(ota_gate(&g));
                ESP_LOGW(TAG, "update aborted: %s", why);
                break;
            }
        }
    }
    if (mbedtls_sha256_finish(&sha, dig) != 0) {
        read_ok = false;
        why = "sha";
    }
    mbedtls_sha256_free(&sha);
    http_get_close(c);
    free(buf);
    hex_encode(hex, dig);
    hash_ok = memcmp(hex, m->sha256, OTA_SHA256_HEX) == 0;
    collect_gate(&g);
    if (!read_ok || got == 0 || (cl > 0 && (size_t)cl != got) || !hash_ok || ota_gate(&g) != OTA_ALLOW) {
        const char *detail = "aborted";
        esp_ota_abort(handle);
        if (!read_ok) {
            detail = why;
        } else if (!hash_ok || (cl > 0 && (size_t)cl != got)) {
            detail = "bad_hash";
        } else if (ota_gate(&g) != OTA_ALLOW) {
            detail = ota_block_code(ota_gate(&g));
        }
        post_event("failed", m->version, detail);
        return -1;
    }
    if (esp_ota_end(handle) != ESP_OK) {
        /* esp_ota_end frees the handle when it fails. */
        post_event("failed", m->version, "ota_end");
        return -1;
    }
    if (esp_ota_set_boot_partition(part) != ESP_OK) {
        post_event("failed", m->version, "boot_part");
        return -1;
    }
    post_event("staged", m->version, "reboot");
    note("Firmware update staged");
    vTaskDelay(pdMS_TO_TICKS(500));
    esp_restart();
    return 1;
}

/* 0 stay on the 6 hour cadence, -1 try again sooner, 1 does not return. */
static int check_manifest(void)
{
    char *body;
    int status;
    int n = 0;
    int r;
    esp_http_client_handle_t c = NULL;
    ota_manifest_t man;
    if (!ota_pubkey_ready(ota_embedded_pubkey)) {
        static bool warned;
        if (!warned) {
            note("Firmware update off until a signing key is built in");
            warned = true;
        }
        return 0;
    }
    if (!wifi_up() || !uploader_net_mem_ok()) {
        return -1;
    }
    body = heap_caps_malloc(OTA_MANIFEST_MAX + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!body) {
        return -1;
    }
    status = http_get_open(CONFIG_RECORDER_OTA_MANIFEST_URL, &c);
    if (status != 200 || !c) {
        if (c) {
            http_get_close(c);
        }
        free(body);
        /* Not published yet: wait for the next 6 hour check. A dropped
         * connection (no HTTP status) is retried sooner. */
        return (status == 404 || status == 403) ? 0 : -1;
    }
    while (n < OTA_MANIFEST_MAX) {
        r = esp_http_client_read(c, body + n, OTA_MANIFEST_MAX - n);
        if (r < 0) {
            n = -1;
            break;
        }
        if (r == 0) {
            break;
        }
        n += r;
    }
    http_get_close(c);
    if (n <= 0) {
        free(body);
        return -1;
    }
    body[n] = '\0';
    if (ota_manifest_fields(body, CONFIG_RECORDER_OTA_BUCKET, &man) != 0) {
        free(body);
        post_event("rejected", "", "bad_manifest");
        return -1;
    }
    free(body);
    if (!ota_signature_ok(ota_embedded_pubkey, man.sig, man.canon, (size_t)man.canon_len)) {
        post_event("rejected", man.version, "bad_signature");
        note("Firmware manifest signature rejected");
        return -1;
    }
    if (!ota_is_newer(FW_VERSION, man.version)) {
        ESP_LOGI(TAG, "manifest %s is not newer than %s", man.version, FW_VERSION);
        post_event("check", man.version, "current");
        return 0;
    }
    {
        ota_gate_in_t g;
        collect_gate(&g);
        if (ota_gate(&g) != OTA_ALLOW) {
            post_event("blocked", man.version, ota_block_code(ota_gate(&g)));
            return -1;
        }
    }
    return download_and_switch(&man);
}

static void ota_task(void *arg)
{
    int64_t next = 0;
    ota_block_t last = OTA_ALLOW;
    bool logged = false;
    (void)arg;
    confirm_or_rollback();
    for (;;) {
        ota_gate_in_t g;
        ota_block_t b;
        int64_t now = esp_timer_get_time();
        if (now < next) {
            vTaskDelay(pdMS_TO_TICKS(5000));
            continue;
        }
        collect_gate(&g);
        b = ota_gate(&g);
        if (b != OTA_ALLOW) {
            if (!logged || b != last) {
                char msg[64];
                snprintf(msg, sizeof msg, "Firmware update blocked: %s", ota_block_code(b));
                note(msg);
                if (!g.session_active && !g.upload_busy && wifi_up()) {
                    post_event("blocked", "", ota_block_code(b));
                }
                logged = true;
                last = b;
            }
            /* Stay due: the boot / 6 hour check runs once the recorder is idle. */
            vTaskDelay(pdMS_TO_TICKS(15000));
            continue;
        }
        logged = false;
        if (check_manifest() == 0) {
            next = esp_timer_get_time() + (int64_t)OTA_CHECK_INTERVAL_S * 1000000LL;
        } else {
            next = esp_timer_get_time() + 15LL * 60LL * 1000000LL;
        }
    }
}

void ota_start(void)
{
    char msg[48];
    snprintf(msg, sizeof msg, "Firmware v%s", FW_VERSION);
    note(msg);
    /* Stack must be internal RAM: this task calls esp_ota_* / flash APIs, which
     * disable the cache and assert if the caller stack is in PSRAM. */
    if (xTaskCreatePinnedToCoreWithCaps(ota_task, "ota", 24576, NULL, 1, NULL, 0,
                                        MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) != pdPASS) {
        status_error("Firmware update task did not start");
    }
}
