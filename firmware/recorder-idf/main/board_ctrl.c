#include "board_ctrl.h"

#include "board_pins.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "stc8";
static i2c_master_dev_handle_t s_dev;
static bool s_present;
static int s_level = -1;

esp_err_t board_ctrl_init(i2c_master_bus_handle_t bus)
{
    if (!bus) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t probe = i2c_master_probe(bus, BOARD_STC8_ADDR, 50);
    s_present = probe == ESP_OK;
    if (!s_present) {
        ESP_LOGW(TAG, "STC8H1K28 @0x30 not found (expected on HW V1.1+)");
        return probe;
    }
    i2c_device_config_t cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = BOARD_STC8_ADDR,
        .scl_speed_hz = 100000,
    };
    esp_err_t err = i2c_master_bus_add_device(bus, &cfg, &s_dev);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "add device: %s", esp_err_to_name(err));
        s_present = false;
        return err;
    }
    /* Keep the speaker amp muted. This build never plays audio. Sent first
     * so the backlight command is the last byte the STC8 sees, as in
     * Elecrow lesson 3 (main.c: 250 then 0, nothing after). */
    board_ctrl_cmd(BOARD_CMD_MUTE_AMP);
    vTaskDelay(pdMS_TO_TICKS(20));
    /* Elecrow lesson 3: 250 enables the touch controller, 0 is backlight max. */
    board_ctrl_cmd(BOARD_CMD_TOUCH_ON);
    vTaskDelay(pdMS_TO_TICKS(100));
    board_ctrl_force_backlight(BOARD_CMD_BACKLIGHT_MAX, "init");
    ESP_LOGI(TAG, "board controller ready");
    return ESP_OK;
}

bool board_ctrl_present(void)
{
    return s_present;
}

esp_err_t board_ctrl_cmd(uint8_t cmd)
{
    if (!s_dev) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t err = i2c_master_transmit(s_dev, &cmd, 1, 100);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "cmd %u failed: %s", cmd, esp_err_to_name(err));
    }
    return err;
}

void board_ctrl_set_backlight(uint8_t level)
{
    if (level > BOARD_CMD_BACKLIGHT_OFF) {
        level = BOARD_CMD_BACKLIGHT_OFF;
    }
    if ((int)level == s_level) {
        return;
    }
    if (board_ctrl_cmd(level) == ESP_OK) {
        s_level = level;
    }
}

esp_err_t board_ctrl_force_backlight(uint8_t level, const char *why)
{
    if (level > BOARD_CMD_BACKLIGHT_OFF) {
        level = BOARD_CMD_BACKLIGHT_OFF;
    }
    esp_err_t err = ESP_ERR_INVALID_STATE;
    for (int i = 0; i < 3; i++) {
        err = board_ctrl_cmd(level);
        if (err == ESP_OK) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    if (err == ESP_OK) {
        s_level = level;
        ESP_LOGI(TAG, "backlight cmd %u sent (%s)", level, why ? why : "");
    } else {
        s_level = -1;
        ESP_LOGW(TAG, "backlight cmd %u NOT sent (%s): %s", level, why ? why : "",
                 esp_err_to_name(err));
    }
    return err;
}
