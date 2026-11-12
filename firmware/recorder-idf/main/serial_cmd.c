#include "serial_cmd.h"

#include "crc32c.h"
#include "device_info.h"
#include "display.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/task.h"
#include "lvgl.h"
#include "mbedtls/base64.h"
#include "uploader.h"
#include "version.h"
#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static const char *TAG = "serial";

#define LINE_MAX_LEN 80
/* 57 raw bytes -> 76 base64 characters per line. */
#define B64_RAW 57

static void cmd_help(void)
{
    printf("commands: help | version | status | queue | queue wipe YES | uploads hold | uploads resume | screenshot\n");
}

/* Dump the active screen as RGB565 (LVGL's native little-endian order):
 *   ===SNAPSHOT BEGIN w=480 h=320 cf=RGB565 stride=960 bytes=307200===
 *   S:<line index>:<76 base64 chars>
 *   ===SNAPSHOT END crc32c=<hex> lines=<n>===
 * Log output is held at WARN while it streams so lines don't interleave. */
static void cmd_screenshot(void)
{
#if LV_USE_SNAPSHOT
    lv_obj_t *scr;
    /* Logical (rotated) size, as the UI is laid out: 480x320 on the Hosyond. */
    const uint32_t w = (uint32_t)lv_display_get_horizontal_resolution(NULL);
    const uint32_t h = (uint32_t)lv_display_get_vertical_resolution(NULL);
    const uint32_t stride = lv_draw_buf_width_to_stride(w, LV_COLOR_FORMAT_RGB565);
    const size_t size = (size_t)stride * h;
    uint8_t *px = heap_caps_aligned_alloc(LV_DRAW_BUF_ALIGN, size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!px) {
        printf("screenshot: no PSRAM for %u bytes\n", (unsigned)size);
        return;
    }
    lv_draw_buf_t db;
    lv_result_t res = LV_RESULT_INVALID;
    if (display_lock(2000)) {
        scr = lv_screen_active();
        if (lv_draw_buf_init(&db, w, h, LV_COLOR_FORMAT_RGB565, stride, px, size) == LV_RESULT_OK) {
            res = lv_snapshot_take_to_draw_buf(scr, LV_COLOR_FORMAT_RGB565, &db);
        }
        display_unlock();
    } else {
        printf("screenshot: display busy\n");
    }
    if (res != LV_RESULT_OK) {
        printf("screenshot: snapshot failed\n");
        heap_caps_free(px);
        return;
    }
    esp_log_level_t prev = esp_log_level_get("*");
    esp_log_level_set("*", ESP_LOG_WARN);
    fflush(stdout);
    vTaskDelay(pdMS_TO_TICKS(50));
    printf("\n===SNAPSHOT BEGIN w=%u h=%u cf=RGB565 stride=%u bytes=%u===\n", (unsigned)w, (unsigned)h,
           (unsigned)stride, (unsigned)size);
    char b64[80];
    unsigned n = 0;
    for (size_t off = 0; off < size; off += B64_RAW, n++) {
        size_t chunk = size - off < B64_RAW ? size - off : B64_RAW;
        size_t ol = 0;
        mbedtls_base64_encode((unsigned char *)b64, sizeof b64, &ol, px + off, chunk);
        b64[ol] = '\0';
        printf("S:%u:%s\n", n, b64);
        if ((n & 63) == 63) {
            fflush(stdout);
            vTaskDelay(1);   /* let the USB FIFO drain; keeps the WDT happy */
        }
    }
    uint32_t crc = crc32c_update(0, px, size);
    printf("===SNAPSHOT END crc32c=%08lx lines=%u===\n", (unsigned long)crc, n);
    fflush(stdout);
    esp_log_level_set("*", prev);
    heap_caps_free(px);
#else
    printf("screenshot: this build has no LV_USE_SNAPSHOT\n");
#endif
}

static void handle(char *line)
{
    /* trim + lower-case the command word(s); keep "YES" exact */
    size_t n = strlen(line);
    while (n && isspace((unsigned char)line[n - 1])) {
        line[--n] = '\0';
    }
    char *p = line;
    while (*p && isspace((unsigned char)*p)) {
        p++;
    }
    if (!*p) {
        return;
    }
    ESP_LOGI(TAG, "command: %s", p);
    if (strcmp(p, "help") == 0 || strcmp(p, "?") == 0) {
        cmd_help();
    } else if (strcmp(p, "version") == 0) {
        printf("goalvera recorder %s, device %s\n", FW_VERSION, device_id_str());
    } else if (strcmp(p, "status") == 0) {
        uploader_print_status();
    } else if (strcmp(p, "queue") == 0) {
        uploader_queue_print();
    } else if (strncmp(p, "queue wipe", 10) == 0) {
        if (strcmp(p, "queue wipe YES") != 0) {
            printf("queue wipe: deletes EVERY file in /sd/rec. Type exactly: queue wipe YES\n");
        } else {
            int r = uploader_queue_wipe();
            if (r >= 0) {
                printf("queue wipe: removed %d files. Uploads stay on hold until \"uploads resume\"\n", r);
            }
        }
    } else if (strcmp(p, "uploads hold") == 0) {
        uploader_admin_hold(true);
        uploader_print_status();
    } else if (strcmp(p, "uploads resume") == 0) {
        uploader_admin_hold(false);
        uploader_print_status();
    } else if (strcmp(p, "screenshot") == 0) {
        cmd_screenshot();
    } else {
        printf("unknown command \"%s\". ", p);
        cmd_help();
    }
    fflush(stdout);
}

static void serial_task(void *arg)
{
    (void)arg;
    char line[LINE_MAX_LEN + 1];
    size_t len = 0;
    bool overflow = false;
    while (1) {
        char buf[32];
        ssize_t r = read(STDIN_FILENO, buf, sizeof buf);
        if (r <= 0) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        for (ssize_t i = 0; i < r; i++) {
            char c = buf[i];
            if (c == '\n' || c == '\r') {
                if (!overflow) {
                    line[len] = '\0';
                    handle(line);
                }
                len = 0;
                overflow = false;
            } else if (len < LINE_MAX_LEN) {
                line[len++] = c;
            } else {
                overflow = true;
            }
        }
    }
}

void serial_cmd_start(void)
{
    /* PSRAM stack: this task never writes flash (NVS is untouched here). */
    if (xTaskCreatePinnedToCoreWithCaps(serial_task, "serial_cmd", 6144, NULL, 1, NULL, 0,
                                        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS) {
        ESP_LOGE(TAG, "serial command task not started");
        return;
    }
    ESP_LOGI(TAG, "serial commands ready (type help)");
}
