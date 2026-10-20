#include "status_line.h"

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include <string.h>

static const char *TAG = "activity";

#define STATUS_KEEP 6

static SemaphoreHandle_t s_mu;
static status_entry_t s_log[STATUS_KEEP]; /* s_log[0] is newest */
static int s_count;
static uint32_t s_version;

bool status_clock_valid(void)
{
    time_t now = time(NULL);
    struct tm t;
    localtime_r(&now, &t);
    return t.tm_year + 1900 >= 2024;
}

void status_init(void)
{
    s_mu = xSemaphoreCreateMutex();
    s_count = 0;
}

static void add(const char *msg, bool error)
{
    if (!msg || !msg[0]) {
        return;
    }
    if (error) {
        ESP_LOGE(TAG, "%s", msg);
    } else {
        ESP_LOGI(TAG, "%s", msg);
    }
    if (s_mu) {
        xSemaphoreTake(s_mu, portMAX_DELAY);
    }
    status_entry_t e = {0};
    strlcpy(e.msg, msg, sizeof e.msg);
    e.error = error;
    e.boot_s = (uint32_t)(esp_timer_get_time() / 1000000ULL);
    e.wall_valid = status_clock_valid();
    e.wall = time(NULL);
    if (s_count > 0 && strcmp(s_log[0].msg, e.msg) == 0 && s_log[0].error == error) {
        s_log[0] = e; /* same message again: just refresh its time */
    } else {
        memmove(&s_log[1], &s_log[0], sizeof(status_entry_t) * (STATUS_KEEP - 1));
        s_log[0] = e;
        if (s_count < STATUS_KEEP) {
            s_count++;
        }
    }
    s_version++;
    if (s_mu) {
        xSemaphoreGive(s_mu);
    }
}

void status_set(const char *msg)
{
    add(msg, false);
}

void status_error(const char *msg)
{
    add(msg, true);
}

void status_get(char *out, size_t len)
{
    if (!out || len == 0) {
        return;
    }
    out[0] = '\0';
    if (s_mu) {
        xSemaphoreTake(s_mu, portMAX_DELAY);
    }
    if (s_count > 0) {
        strlcpy(out, s_log[0].msg, len);
    }
    if (s_mu) {
        xSemaphoreGive(s_mu);
    }
}

int status_recent(status_entry_t *out, int max)
{
    if (!out || max <= 0) {
        return 0;
    }
    if (s_mu) {
        xSemaphoreTake(s_mu, portMAX_DELAY);
    }
    int n = s_count < max ? s_count : max;
    memcpy(out, s_log, sizeof(status_entry_t) * n);
    if (s_mu) {
        xSemaphoreGive(s_mu);
    }
    return n;
}

uint32_t status_version(void)
{
    return s_version;
}
