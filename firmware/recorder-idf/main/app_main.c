#include "sdkconfig.h"
#include "audio_rec.h"
#if CONFIG_RECORDER_BOARD_HOSYOND_35
#include "battery.h"
#include "power_sleep.h"
#endif
#include "board_ctrl.h"
#include "board_pins.h"
#include "config_store.h"
#include "device_info.h"
#include "display.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "i2c_bus.h"
#include "nvs_prefs.h"
#include "rtc_pcf8563.h"
#include "serial_cmd.h"
#include "sdcard.h"
#include "status_line.h"
#include "ui_app.h"
#include "ota_update.h"
#include "uploader.h"
#include "version.h"
#include "wifi_time.h"
#include <stdio.h>
#if CONFIG_RECORDER_BOARD_HOSYOND_35
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lvgl.h"

/* RESET-button "off" (0.7.3, NVS since 0.7.4): nothing can be recording this early (the reset
 * already stopped it), so show a short notice and sleep. */
static void turn_off_now(void)
{
    if (i2c_bus_init() == ESP_OK) {
        board_ctrl_init(i2c_bus_get());
    }
    if (display_init(i2c_bus_get()) == ESP_OK && display_lock(1000)) {
        lv_obj_t *scr = lv_screen_active();
        lv_obj_clean(scr);
        lv_obj_set_style_bg_color(scr, lv_color_hex(0x000000), 0);
        lv_obj_t *l = lv_label_create(scr);
        lv_label_set_text(l, "Turning off\n\nPress the button again to turn on");
        lv_obj_set_style_text_color(l, lv_color_hex(0xFFFFFF), 0);
        lv_obj_set_style_text_font(l, &lv_font_montserrat_20, 0);
        lv_obj_set_style_text_align(l, LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_center(l);
        display_unlock();
        display_start_task();
        vTaskDelay(pdMS_TO_TICKS(1500));
    }
    display_lock(1000);
    power_deep_sleep(); /* does not return */
}
#endif

static const char *TAG = "recorder";

void app_main(void)
{
    ESP_LOGI(TAG, "goalvera classroom recorder %s", FW_VERSION);
#if CONFIG_RECORDER_BOARD_HOSYOND_35
    bool woke_from_sleep = power_sleep_boot();
    if (power_toggle_boot()) {
        turn_off_now();
    }
#endif
    timezone_init();
    status_init();
#if CONFIG_RECORDER_BOARD_HOSYOND_35
    if (woke_from_sleep) {
        status_set("Woke up from auto sleep (BOOT button)");
    }
#endif
    prefs_init();
    prefs_async_start();   /* 0.7.6: NVS writes from PSRAM-stack tasks go via this task */
    device_id_init();
    ESP_LOGI(TAG, "device %s  psram %u bytes", device_id_str(),
             (unsigned)heap_caps_get_total_size(MALLOC_CAP_SPIRAM));

    if (i2c_bus_init() == ESP_OK) {
        board_ctrl_init(i2c_bus_get());
        pcf8563_init(i2c_bus_get());
    } else {
        ESP_LOGE(TAG, "I2C unavailable — touch, backlight, and RTC will not work");
    }

    sd_init();
    config_load();

    /* Recent-activity list: added oldest first, shown newest first. */
    struct tm now;
    if (!rtc_now(&now)) {
        status_set("Clock not set yet");
    }
    if (config_used_sample()) {
        status_set("Using sample class lists");
    }
    /* Before the display starts: may write the upload token to NVS. */
    uploader_init();
#if CONFIG_RECORDER_BOARD_HOSYOND_35
    battery_init();
#endif
    bool audio_ok = audio_init() == ESP_OK;
    if (!audio_ok) {
        status_error("Audio buffer allocation failed");
    }
    if (!sd_mounted()) {
        status_error(BOARD_SD_MISSING_MSG);
    } else if (audio_ok) {
        status_set("Ready to record");
    }
    /* Display + UI first so their internal-RAM needs (LVGL draw buffers,
     * bounce buffers, LVGL task stack) are met before Wi-Fi allocates. */
    if (display_init(i2c_bus_get()) == ESP_OK) {
        ui_build();
        if (display_start_task() != ESP_OK) {
            ESP_LOGE(TAG, "UI task failed to start");
        }
    } else {
        ESP_LOGE(TAG, "display init failed — UI will not start");
    }

    wifi_start_if_configured();
    uploader_start();
    ota_start();
    serial_cmd_start();
    ESP_LOGI(TAG, "boot done: internal free %u (largest %u, largest DMA %u, min ever %u), psram free %u",
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
}
