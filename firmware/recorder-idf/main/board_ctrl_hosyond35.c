/*
 * Hosyond ES3C35P board control, same API as board_ctrl.c (CrowPanel STC8).
 * There is no I/O-expander MCU on this board:
 *   backlight = GPIO41 PWM (LEDC), active high via a BSS138
 *   speaker amp = GPIO1 shutdown pin, HIGH = off (kept off: never plays audio)
 *   touch power / mic path = nothing to switch
 * The command numbers are the CrowPanel ones (board_pins_hosyond35.h):
 * 0 = brightest ... 245 = off.
 */
#include "board_ctrl.h"

#include "board_pins.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_log.h"

static const char *TAG = "board";
static bool s_ready;
static int s_level = -1;

#define BL_TIMER   LEDC_TIMER_0
#define BL_CHANNEL LEDC_CHANNEL_0
#define BL_RES     LEDC_TIMER_10_BIT
#define BL_MAX_DUTY ((1u << 10) - 1u)

static void amp_off(void)
{
    gpio_set_level(BOARD_AMP_EN, BOARD_AMP_OFF_LEVEL);
}

static esp_err_t bl_apply(uint8_t level)
{
    if (!s_ready) {
        return ESP_ERR_INVALID_STATE;
    }
    if (level > BOARD_CMD_BACKLIGHT_OFF) {
        level = BOARD_CMD_BACKLIGHT_OFF;
    }
    uint32_t duty = (uint32_t)(BOARD_CMD_BACKLIGHT_OFF - level) * BL_MAX_DUTY / BOARD_CMD_BACKLIGHT_OFF;
    esp_err_t err = ledc_set_duty(LEDC_LOW_SPEED_MODE, BL_CHANNEL, duty);
    if (err == ESP_OK) {
        err = ledc_update_duty(LEDC_LOW_SPEED_MODE, BL_CHANNEL);
    }
    return err;
}

esp_err_t board_ctrl_init(i2c_master_bus_handle_t bus)
{
    (void)bus;
    /* Amp off first (the board's pull-up already holds it off at reset). */
    gpio_config_t amp = {
        .pin_bit_mask = 1ULL << BOARD_AMP_EN,
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_set_level(BOARD_AMP_EN, BOARD_AMP_OFF_LEVEL);
    gpio_config(&amp);
    amp_off();

    ledc_timer_config_t t = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = BL_RES,
        .timer_num = BL_TIMER,
        .freq_hz = 5000, /* vendor demo: 5 kHz, 10-bit */
        .clk_cfg = LEDC_AUTO_CLK,
    };
    esp_err_t err = ledc_timer_config(&t);
    if (err == ESP_OK) {
        ledc_channel_config_t c = {
            .gpio_num = BOARD_LCD_BL,
            .speed_mode = LEDC_LOW_SPEED_MODE,
            .channel = BL_CHANNEL,
            .timer_sel = BL_TIMER,
            .duty = 0, /* dark until the first LVGL frame is on the panel */
            .hpoint = 0,
        };
        err = ledc_channel_config(&c);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "backlight PWM init failed: %s", esp_err_to_name(err));
        return err;
    }
    s_ready = true;
    s_level = BOARD_CMD_BACKLIGHT_OFF;
    ESP_LOGI(TAG, "Hosyond 3.5: amp off (GPIO%d), backlight PWM on GPIO%d", BOARD_AMP_EN, BOARD_LCD_BL);
    return ESP_OK;
}

bool board_ctrl_present(void)
{
    return s_ready;
}

esp_err_t board_ctrl_cmd(uint8_t cmd)
{
    switch (cmd) {
    case BOARD_CMD_MUTE_AMP:
        amp_off();
        return ESP_OK;
    case BOARD_CMD_TOUCH_ON:
    case BOARD_CMD_MIC_PATH:
        return ESP_OK; /* nothing to switch on this board */
    default:
        if (cmd <= BOARD_CMD_BACKLIGHT_OFF) {
            return bl_apply(cmd);
        }
        return ESP_ERR_NOT_SUPPORTED;
    }
}

void board_ctrl_set_backlight(uint8_t level)
{
    if (level > BOARD_CMD_BACKLIGHT_OFF) {
        level = BOARD_CMD_BACKLIGHT_OFF;
    }
    if ((int)level == s_level) {
        return;
    }
    if (bl_apply(level) == ESP_OK) {
        s_level = level;
    }
}

esp_err_t board_ctrl_force_backlight(uint8_t level, const char *why)
{
    if (level > BOARD_CMD_BACKLIGHT_OFF) {
        level = BOARD_CMD_BACKLIGHT_OFF;
    }
    esp_err_t err = bl_apply(level);
    if (err == ESP_OK) {
        s_level = level;
        ESP_LOGI(TAG, "backlight level %u (%s)", level, why ? why : "");
    } else {
        s_level = -1;
        ESP_LOGW(TAG, "backlight level %u NOT set (%s): %s", level, why ? why : "", esp_err_to_name(err));
    }
    return err;
}
