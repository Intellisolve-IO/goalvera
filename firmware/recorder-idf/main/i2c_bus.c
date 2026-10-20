#include "i2c_bus.h"

#include "board_pins.h"
#include "esp_log.h"

static const char *TAG = "i2c";
static i2c_master_bus_handle_t s_bus;

esp_err_t i2c_bus_init(void)
{
    i2c_master_bus_config_t cfg = {
        .i2c_port = BOARD_I2C_PORT,
        .sda_io_num = BOARD_I2C_SDA,
        .scl_io_num = BOARD_I2C_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    esp_err_t err = i2c_new_master_bus(&cfg, &s_bus);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "bus init failed: %s", esp_err_to_name(err));
        s_bus = NULL;
    }
    return err;
}

i2c_master_bus_handle_t i2c_bus_get(void)
{
    return s_bus;
}
