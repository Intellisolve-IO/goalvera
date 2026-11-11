#include "power_sleep.h"

#include "board_ctrl.h"
#include "board_pins.h"
#include "display.h"
#include "driver/gpio.h"
#include "driver/rtc_io.h"
#include "es8311_min.h"
#include "esp_log.h"
#include "nvs_prefs.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "power_toggle.h"
#include "esp_sleep.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>

static const char *TAG = "sleep";

#define WAKE_GPIO GPIO_NUM_0 /* BOOT button (KEY2), active low, R10 10K pull-up */

/* RESET-button on/off state (0.7.4): NVS key PT_NVS_KEY, see power_toggle.h. */
static bool pt_stored_on(void)
{
    char v[4];
    return prefs_get(PT_NVS_KEY, v, sizeof v) == ESP_OK && v[0] == '1';
}

static void pt_store(const char *v)
{
    char cur[4];
    if (prefs_get(PT_NVS_KEY, cur, sizeof cur) == ESP_OK && strcmp(cur, v) == 0) {
        return; /* unchanged: no flash write */
    }
    esp_err_t err = prefs_set(PT_NVS_KEY, v);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "on/off state write: %s", esp_err_to_name(err));
    }
}

static void pt_arm_cb(void *arg)
{
    (void)arg;
    prefs_set_async(PT_NVS_KEY, "1"); /* background task, skipped if unchanged */
    ESP_LOGI(TAG, "on/off armed: next RESET press turns the board off");
}

bool power_toggle_boot(void)
{
    esp_reset_reason_t rr = esp_reset_reason();
    pt_boot_t boot = PT_BOOT_OTHER;
    if (rr == ESP_RST_POWERON || rr == ESP_RST_EXT) {
        boot = PT_BOOT_PIN_OR_POWERON;
    } else if (rr == ESP_RST_DEEPSLEEP) {
        boot = PT_BOOT_DEEPSLEEP;
    }
    prefs_init();
    bool was_on = pt_stored_on();
    bool off = pt_decide(was_on, boot);
    const char *nv = pt_after_decide(was_on, boot);
    if (nv) {
        pt_store(nv);
    }
    ESP_LOGI(TAG, "reset reason %d, nvs %s -> %s", (int)rr, was_on ? "on" : "off/unset", off ? "TURN OFF" : "boot");
    if (!off) {
        const esp_timer_create_args_t a = {.callback = pt_arm_cb, .name = "pt_arm"};
        esp_timer_handle_t t;
        if (esp_timer_create(&a, &t) == ESP_OK) {
            esp_timer_start_once(t, (uint64_t)PT_ARM_MS * 1000);
        }
    }
    return off;
}

bool power_sleep_boot(void)
{
    gpio_deep_sleep_hold_dis();
    gpio_hold_dis(BOARD_LCD_BL);
    gpio_hold_dis(BOARD_AMP_EN);
    esp_sleep_wakeup_cause_t why = esp_sleep_get_wakeup_cause();
    if (why == ESP_SLEEP_WAKEUP_EXT0) {
        rtc_gpio_deinit(WAKE_GPIO);
        ESP_LOGI(TAG, "woke from auto sleep: BOOT button");
        return true;
    }
    if (why != ESP_SLEEP_WAKEUP_UNDEFINED) {
        ESP_LOGI(TAG, "woke from sleep, cause %d", (int)why);
    }
    return false;
}

void power_deep_sleep(void)
{
    ESP_LOGW(TAG, "going to deep sleep; press BOOT (or RESET) to wake");
    pt_store("0"); /* the next RESET press means "on", not "off" */
    board_ctrl_force_backlight(BOARD_CMD_BACKLIGHT_OFF, "sleep");
    display_sleep();
    if (es8311_min_power_down() != ESP_OK) {
        ESP_LOGW(TAG, "codec power-down failed");
    }
    esp_wifi_stop();

    /* Backlight gate low (R32 10K also pulls it down), speaker amp held off. */
    gpio_config_t bl = {.pin_bit_mask = 1ULL << BOARD_LCD_BL, .mode = GPIO_MODE_OUTPUT};
    gpio_config(&bl);
    gpio_set_level(BOARD_LCD_BL, 0);
    gpio_set_level(BOARD_AMP_EN, BOARD_AMP_OFF_LEVEL);
    gpio_hold_en(BOARD_LCD_BL);
    gpio_hold_en(BOARD_AMP_EN);
    gpio_deep_sleep_hold_en();

    /* Don't wake at once if BOOT happens to be held right now. */
    for (int i = 0; i < 50 && gpio_get_level(WAKE_GPIO) == 0; i++) {
        vTaskDelay(pdMS_TO_TICKS(100));
    }
    rtc_gpio_pullup_en(WAKE_GPIO);
    rtc_gpio_pulldown_dis(WAKE_GPIO);
    esp_sleep_enable_ext0_wakeup(WAKE_GPIO, 0);
    vTaskDelay(pdMS_TO_TICKS(20)); /* let the log line out */
    esp_deep_sleep_start();
}
