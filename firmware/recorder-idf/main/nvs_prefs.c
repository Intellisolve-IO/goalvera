#include "nvs_prefs.h"

#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "nvs_migrate.h"
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include <stdbool.h>
#include <string.h>

typedef struct {
    char key[16];
    char value[96];
} prefs_msg_t;

static QueueHandle_t s_q;

static const char *TAG = "prefs";
static const char *NS = "recorder";

void prefs_init(void)
{
    static bool s_done; /* 0.7.4: power_toggle_boot() calls this first */
    if (s_done) {
        return;
    }
    s_done = true;
#if CONFIG_NVS_ENCRYPTION
    esp_err_t err = nvs_migrate_init(); /* tasks#22: encrypted NVS (migrates plaintext once) */
#else
    esp_err_t err = nvs_flash_init();
#endif
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS partition needs a reset (%s)", esp_err_to_name(err));
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs init: %s", esp_err_to_name(err));
    }
#if !CONFIG_NVS_ENCRYPTION && CONFIG_RECORDER_NVS_MIGRATE_DRY_RUN
    if (err == ESP_OK) {
        nvs_migrate_dry_run();
    }
#endif
    /* Without CONFIG_NVS_ENCRYPTION (CrowPanel, plain Hosyond builds) values
     * here are plaintext. See sdkconfig.defaults.secure (tasks#22). */
}

esp_err_t prefs_get(const char *key, char *out, size_t len)
{
    if (!out || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    out[0] = '\0';
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READONLY, &h);
    if (err != ESP_OK) {
        return err;
    }
    size_t n = len;
    err = nvs_get_str(h, key, out, &n);
    nvs_close(h);
    if (err != ESP_OK) {
        out[0] = '\0';
    }
    return err;
}

esp_err_t prefs_set(const char *key, const char *value)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_set_str(h, key, value ? value : "");
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    return err;
}

static void prefs_task(void *arg)
{
    (void)arg;
    prefs_msg_t m;
    char cur[96];
    while (1) {
        if (xQueueReceive(s_q, &m, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (prefs_get(m.key, cur, sizeof cur) == ESP_OK && strcmp(cur, m.value) == 0) {
            continue;
        }
        esp_err_t err = prefs_set(m.key, m.value);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "set %s: %s", m.key, esp_err_to_name(err));
        }
    }
}

/* 0.7.6: started from prefs_init so tasks with PSRAM stacks (net_bg) never
 * end up writing flash inline. */
bool prefs_async_start(void)
{
    if (s_q) {
        return true;
    }
    s_q = xQueueCreate(16, sizeof(prefs_msg_t));
    if (!s_q) {
        return false;
    }
    if (xTaskCreatePinnedToCore(prefs_task, "prefs", 4096, NULL, 2, NULL, 0) != pdPASS) {
        vQueueDelete(s_q);
        s_q = NULL;
        return false;
    }
    return true;
}

void prefs_set_async(const char *key, const char *value)
{
    if (!key) {
        return;
    }
    if (!prefs_async_start()) {
        ESP_LOGW(TAG, "async unavailable, writing inline");
        prefs_set(key, value);
        return;
    }
    prefs_msg_t m;
    strlcpy(m.key, key, sizeof m.key);
    strlcpy(m.value, value ? value : "", sizeof m.value);
    if (xQueueSend(s_q, &m, 0) != pdTRUE) {
        ESP_LOGW(TAG, "queue full, dropped %s", key);
    }
}
