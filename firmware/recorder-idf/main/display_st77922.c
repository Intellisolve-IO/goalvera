/*
 * Hosyond ES3C35P display: ST77922 over QSPI (320x480 native portrait,
 * shown as 480x320 landscape by LVGL software rotation) + the
 * touch controller built into the panel (Sitronix, I2C 0x55, same protocol
 * as espressif/esp_lcd_touch_st7123). Same public API as display.c (the
 * CrowPanel RGB driver), so the rest of the firmware does not change.
 *
 * Built only when CONFIG_RECORDER_BOARD_HOSYOND_35 (see main/CMakeLists.txt).
 */
#include "display.h"

#include "sdkconfig.h"

#include "board_ctrl.h"
#include "board_pins.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_st77922.h"
#include "esp_lcd_touch_st7123.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lvgl.h"

static const char *TAG = "display";
static esp_lcd_panel_handle_t s_panel;
static esp_lcd_panel_io_handle_t s_panel_io; /* for SLPIN before deep sleep */
static esp_lcd_touch_handle_t s_touch;
static lv_display_t *s_disp;
static SemaphoreHandle_t s_lock;
static bool s_touch_ok;

/* LVGL renders 480-px-wide landscape strips of DRAW_LINES lines (a
 * multiple of 4, see rounder_cb) into two draw buffers; flush_cb rotates
 * each strip into s_rot_buf (internal DMA RAM), which the QSPI DMA sends.
 * 2 x 19.2 KB draw + 19.2 KB rotate buffer. */
#define DRAW_LINES 20
#define DRAW_BUF_BYTES (BOARD_UI_W * DRAW_LINES * 2)
static uint8_t *s_rot_buf;

#if CONFIG_RECORDER_HOSYOND_ROT_270
#define UI_ROTATION LV_DISPLAY_ROTATION_270
#else
#define UI_ROTATION LV_DISPLAY_ROTATION_90
#endif

/*
 * Panel init table copied verbatim from the vendor LVGL demo
 * (LCDWIKI ES3C35P package, Example_08_LVGL_Demos/LVGL_Demos/esp_bsp.c).
 * It ends with CASET 0..319, RASET 0..479, INVON, SLPOUT, DISPON,
 * COLMOD 0x01, MADCTL 0x00, TEON.
 */
static const st77922_lcd_init_cmd_t s_hosyond_init_cmds[] = {
    {0xF1, (uint8_t []){0x00}, 1, 0},
    {0x60, (uint8_t []){0x00, 0x00, 0x00}, 3, 0},
    {0x65, (uint8_t []){0x80}, 1, 0},
    {0x79, (uint8_t []){0x06}, 1, 0},
    {0x7B, (uint8_t []){0x00, 0x08, 0x08}, 3, 0},
    {0x80, (uint8_t []){0x55, 0x62, 0x2F, 0x17, 0xF0, 0x52, 0x70, 0xD2, 0x52, 0x62, 0xEA}, 11, 0},
    {0x81, (uint8_t []){0x26, 0x52, 0x72, 0x27}, 4, 0},
    {0x84, (uint8_t []){0x92, 0x25}, 2, 0},
    {0x87, (uint8_t []){0x10, 0x10, 0x58, 0x00, 0x02, 0x3A}, 6, 0},
    {0x88, (uint8_t []){0x00, 0x00, 0x2C, 0x10, 0x04, 0x00, 0x00, 0x00, 0x01, 0x01, 0x01, 0x01, 0x01, 0x00, 0x06}, 15, 0},
    {0x89, (uint8_t []){0x00, 0x00, 0x00}, 3, 0},
    {0x8A, (uint8_t []){0x13, 0x00, 0x2C, 0x00, 0x00, 0x2C, 0x10, 0x10, 0x00, 0x3E, 0x19}, 11, 0},
    {0x8B, (uint8_t []){0x15, 0xB1, 0xB1, 0x44, 0x96, 0x2C, 0x10, 0x97, 0x8E}, 9, 0},
    {0x8C, (uint8_t []){0x1D, 0xB1, 0xB1, 0x44, 0x96, 0x2C, 0x10, 0x50, 0x0F, 0x01, 0xC5, 0x12, 0x09}, 13, 0},
    {0x8D, (uint8_t []){0x0C}, 1, 0},
    {0x8E, (uint8_t []){0x33, 0x01, 0x0C, 0x13, 0x01, 0x01}, 6, 0},
    {0xB3, (uint8_t []){0x00, 0x30}, 2, 0},
    {0xF1, (uint8_t []){0x00}, 1, 0},
    {0x71, (uint8_t []){0xC0}, 1, 0},
    {0x66, (uint8_t []){0x02, 0x3F}, 2,  0},
    {0xBE, (uint8_t []){0x24, 0x00, 0x9D}, 3, 0},
    {0x70, (uint8_t []){0x01, 0xA0, 0x11, 0x40, 0xE0, 0x00, 0x11, 0x69, 0x11, 0x00, 0x00, 0x1A}, 12, 0},
    {0x90, (uint8_t []){0x04, 0x04, 0x55, 0x74, 0x00, 0x40, 0x43, 0x27, 0x27}, 9, 0},
    {0x91, (uint8_t []){0x04, 0x04, 0x55, 0x75, 0x00, 0x40, 0x42, 0x27, 0x27}, 9, 0},
    {0x92, (uint8_t []){0x04, 0x44, 0x55, 0xC0, 0x06, 0x00, 0x07, 0x05, 0x90, 0x27}, 10, 0},
    {0x93, (uint8_t []){0x04, 0x43, 0x11, 0x00, 0x00, 0x00, 0x00, 0x05, 0x90, 0x27}, 10, 0},
    {0x94, (uint8_t []){0x00, 0x00, 0x00, 0x00, 0x00, 0x00}, 6, 0},
    {0x95, (uint8_t []){0x96, 0x16, 0x00, 0x00, 0xFF}, 5, 0},
    {0x96, (uint8_t []){0x44, 0x53, 0x03, 0x12, 0x23, 0x24, 0x06, 0x05, 0x94, 0x27, 0x00, 0x44}, 12, 0},
    {0x97, (uint8_t []){0x44, 0x53, 0x47, 0x56, 0x20, 0x20, 0x02, 0x01, 0x94, 0x27, 0x00, 0x44}, 12, 0},
    {0xBA, (uint8_t []){0x55, 0x94, 0x2D, 0x94, 0x27}, 5, 0},
    {0x9A, (uint8_t []){0x40, 0x00, 0x06, 0x00, 0x00, 0x00, 0x00}, 7, 0},
    {0x9B, (uint8_t []){0x00, 0x00, 0x06, 0x00, 0x00, 0x00, 0x00}, 7, 0},
    {0x9C, (uint8_t []){0x5C, 0x12, 0x00, 0x00, 0x10, 0x12, 0x00, 0x00, 0x10, 0x02, 0x00, 0x00, 0x00}, 13, 0},
    {0x9D, (uint8_t []){0x8A, 0x51, 0x00, 0x00, 0x00, 0x80, 0x1E, 0x01}, 8, 0},
    {0x9E, (uint8_t []){0x51, 0x00, 0x00, 0x00, 0x80, 0x1E, 0x01}, 7, 0},
    {0xB4, (uint8_t []){0x1D, 0x1C, 0x1E, 0x0B, 0x14, 0x02, 0x13, 0x09, 0x1E, 0x00, 0x1E, 0x10}, 12, 0},
    {0xB5, (uint8_t []){0x1D, 0x1C, 0x1E, 0x0A, 0x15, 0x03, 0x11, 0x08, 0x1E, 0x01, 0x1E, 0x12}, 12, 0},
    {0xB6, (uint8_t []){0x77, 0x77, 0x00, 0x0A, 0xFF, 0x0A, 0xFF}, 7, 0},
    {0x86, (uint8_t []){0xCD, 0x04, 0xB1, 0x02, 0x58, 0x12, 0x58, 0x0C, 0x13, 0x01, 0xA5, 0x00, 0xA5, 0xA5}, 14, 0},
    {0xB7, (uint8_t []){0x07, 0x0A, 0x0E, 0x06, 0x05, 0x03, 0x2B, 0x03, 0x03, 0x42, 0x07, 0x10, 0x10, 0x2E, 0x3F, 0x0D}, 16, 0},
    {0xB8, (uint8_t []){0x07, 0x0A, 0x0D, 0x05, 0x05, 0x02, 0x2B, 0x02, 0x03, 0x42, 0x06, 0x10, 0x0F, 0x2E, 0x3F, 0x0D}, 16, 0},
    {0xB9, (uint8_t []){0x23, 0x23}, 2, 0},
    {0xBF, (uint8_t []){0x10, 0x14, 0x14, 0x0B, 0x0B, 0x0B}, 6, 0},
    {0xF2, (uint8_t []){0x00}, 1, 0},
    {0x73, (uint8_t []){0x04, 0xDA, 0x12, 0x54, 0x47}, 5, 0},
    {0x77, (uint8_t []){0x6B, 0x5B, 0xFD, 0xC3, 0xC5}, 5, 0},
    {0x7A, (uint8_t []){0x15, 0x27}, 2, 0},
    {0x7B, (uint8_t []){0x04, 0x57}, 2, 0},
    {0x7E, (uint8_t []){0x01, 0x0E}, 2, 0},
    {0xBF, (uint8_t []){0x36}, 1, 0},
    {0xE3, (uint8_t []){0x40, 0x40}, 2, 0},
    {0xF0, (uint8_t []){0x00}, 1, 0},
    {0xD0, (uint8_t []){0x00}, 1, 0},
    {0x2A, (uint8_t []){0x00, 0x00, 0x01, 0x3F}, 4, 0},
    {0x2B, (uint8_t []){0x00, 0x00, 0x01, 0xDF}, 4, 0},
    {0x21, (uint8_t []){0x00}, 0, 0},
    {0x11, (uint8_t []){0x00}, 0, 120},
    {0x29, (uint8_t []){0x00}, 0, 0},
    {0x2C, (uint8_t []){0x00}, 0, 0},
    {0x3A, (uint8_t []){0x01}, 1, 0},
    {0x36, (uint8_t []){0x00}, 1, 0},
    {0x35, (uint8_t []){0x01}, 1, 20},
};

static uint32_t tick_cb(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000ULL);
}

static bool color_done_cb(esp_lcd_panel_io_handle_t io, esp_lcd_panel_io_event_data_t *edata, void *user_ctx)
{
    (void)io;
    (void)edata;
    (void)user_ctx;
    if (s_disp) {
        lv_display_flush_ready(s_disp);
    }
    return false;
}

static void flush_cb(lv_display_t *disp, const lv_area_t *area, uint8_t *px_map)
{
    esp_lcd_panel_handle_t panel = lv_display_get_user_data(disp);
    int32_t w = lv_area_get_width(area);
    int32_t h = lv_area_get_height(area);
    /* Landscape strip -> panel-native portrait rectangle. s_rot_buf is only
     * reused after color_done_cb (LVGL waits for flush_ready). */
    lv_draw_sw_rotate(px_map, s_rot_buf, w, h, lv_draw_buf_width_to_stride(w, LV_COLOR_FORMAT_RGB565),
                      lv_draw_buf_width_to_stride(h, LV_COLOR_FORMAT_RGB565), lv_display_get_rotation(disp),
                      LV_COLOR_FORMAT_RGB565);
    lv_area_t pa = *area;
    lv_display_rotate_area(disp, &pa);
#if CONFIG_RECORDER_HOSYOND_SWAP_RGB565_BYTES
    /* SPI sends bytes in memory order; the panel wants RGB565 high byte first. */
    lv_draw_sw_rgb565_swap(s_rot_buf, (uint32_t)w * (uint32_t)h);
#endif
    /* Asynchronous: color_done_cb calls lv_display_flush_ready(). */
    if (esp_lcd_panel_draw_bitmap(panel, pa.x1, pa.y1, pa.x2 + 1, pa.y2 + 1, s_rot_buf) != ESP_OK) {
        lv_display_flush_ready(disp);
    }
}

/* ST77922: panel x start must be 4N and x end 4N+3 (component README).
 * With 90/270 rotation the panel x axis is the UI's y axis; 320 is a
 * multiple of 4, so rounding y works for both directions. */
static void rounder_cb(lv_event_t *e)
{
    lv_area_t *a = lv_event_get_param(e);
    if (!a) {
        return;
    }
    a->y1 = (int32_t)((a->y1 >> 2) << 2);
    a->y2 = (int32_t)(((a->y2 >> 2) << 2) + 3);
    if (a->y2 > BOARD_UI_H - 1) {
        a->y2 = BOARD_UI_H - 1;
    }
}

static void touch_read_cb(lv_indev_t *indev, lv_indev_data_t *data)
{
    esp_lcd_touch_handle_t tp = lv_indev_get_user_data(indev);
    data->state = LV_INDEV_STATE_RELEASED;
    if (!tp) {
        return;
    }
    esp_lcd_touch_read_data(tp);
    esp_lcd_touch_point_data_t point;
    uint8_t points = 0;
    if (esp_lcd_touch_get_data(tp, &point, &points, 1) == ESP_OK && points > 0) {
        /* Native portrait coordinates; LVGL applies the display rotation. */
        int32_t x = point.x;
        int32_t y = point.y;
        if (x > BOARD_LCD_H_RES - 1) {
            x = BOARD_LCD_H_RES - 1;
        }
        if (y > BOARD_LCD_V_RES - 1) {
            y = BOARD_LCD_V_RES - 1;
        }
        data->point.x = x;
        data->point.y = y;
        data->state = LV_INDEV_STATE_PRESSED;
    }
}

static void lvgl_task(void *arg)
{
    (void)arg;
    int64_t start_us = esp_timer_get_time();
    int bl_stage = 0; /* 0: none, 1: after first frame, 2: +1 s re-send done */
    while (1) {
        uint32_t wait = 33;
        if (display_lock(50)) {
            uint32_t next = lv_timer_handler();
            display_unlock();
            /* Backlight stays off until the first frame is on the panel so
             * the random power-up GRAM contents are never shown. */
            if (bl_stage == 0) {
                lv_mem_monitor_t mon;
                lv_mem_monitor(&mon);
                ESP_LOGI(TAG, "first frame rendered; internal free %u (largest %u), lvgl stack free %u, "
                              "lvgl pool %u%% used (%u free)",
                         (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                         (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
                         (unsigned)uxTaskGetStackHighWaterMark(NULL), (unsigned)mon.used_pct,
                         (unsigned)mon.free_size);
                board_ctrl_force_backlight(BOARD_CMD_BACKLIGHT_MAX, "first frame");
                bl_stage = 1;
            } else if (bl_stage == 1 && esp_timer_get_time() - start_us > 1000000) {
                board_ctrl_force_backlight(BOARD_CMD_BACKLIGHT_MAX, "boot +1s");
                bl_stage = 2;
            }
            if (next > 10 && next < 33) {
                wait = next;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(wait));
    }
}

static esp_err_t panel_init(void)
{
    const spi_bus_config_t buscfg = ST77922_PANEL_BUS_QSPI_CONFIG(
        BOARD_LCD_SCK, BOARD_LCD_D0, BOARD_LCD_D1, BOARD_LCD_D2, BOARD_LCD_D3,
        DRAW_BUF_BYTES);
    esp_err_t err = spi_bus_initialize(BOARD_LCD_QSPI_HOST, &buscfg, SPI_DMA_CH_AUTO);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "QSPI bus: %s", esp_err_to_name(err));
        return err;
    }
    esp_lcd_panel_io_handle_t io = NULL;
    esp_lcd_panel_io_spi_config_t io_cfg = ST77922_PANEL_IO_QSPI_CONFIG(BOARD_LCD_CS, color_done_cb, NULL);
    io_cfg.pclk_hz = BOARD_LCD_PCLK_HZ;
    err = esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)BOARD_LCD_QSPI_HOST, &io_cfg, &io);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "panel IO: %s", esp_err_to_name(err));
        return err;
    }
    s_panel_io = io;
    const st77922_vendor_config_t vendor_cfg = {
        .init_cmds = s_hosyond_init_cmds,
        .init_cmds_size = sizeof(s_hosyond_init_cmds) / sizeof(s_hosyond_init_cmds[0]),
        .flags = {
            .use_qspi_interface = 1,
        },
    };
    const esp_lcd_panel_dev_config_t panel_cfg = {
        .reset_gpio_num = -1, /* LCD reset is CHIP_PU: software reset instead */
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16,
        .vendor_config = (void *)&vendor_cfg,
    };
    err = esp_lcd_new_panel_st77922(io, &panel_cfg, &s_panel);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "st77922 create: %s", esp_err_to_name(err));
        return err;
    }
    err = esp_lcd_panel_reset(s_panel);
    if (err == ESP_OK) {
        err = esp_lcd_panel_init(s_panel);
    }
    if (err == ESP_OK) {
        err = esp_lcd_panel_disp_on_off(s_panel, true);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "st77922 init: %s", esp_err_to_name(err));
        return err;
    }
    ESP_LOGI(TAG, "ST77922 panel ready (QSPI %u MHz, %dx%d)", (unsigned)(BOARD_LCD_PCLK_HZ / 1000000),
             BOARD_LCD_H_RES, BOARD_LCD_V_RES);
    return ESP_OK;
}

static void touch_init(i2c_master_bus_handle_t bus)
{
    if (!bus) {
        ESP_LOGW(TAG, "touch: no I2C bus");
        return;
    }
    esp_lcd_panel_io_handle_t tp_io = NULL;
    esp_lcd_panel_io_i2c_config_t io_cfg = ESP_LCD_TOUCH_IO_I2C_ST7123_CONFIG();
    io_cfg.dev_addr = BOARD_TOUCH_ADDR;
    esp_err_t err = esp_lcd_new_panel_io_i2c_v2(bus, &io_cfg, &tp_io);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "touch IO: %s", esp_err_to_name(err));
        return;
    }
    /* Reset with the vendor demo's timing (low 100 ms, then 100 ms+ to boot).
     * The st7123 component only waits 10 ms after reset, which this panel's
     * touch block does not answer in (NACK at 0x55), so it gets no RST pin. */
    gpio_config_t rst_cfg = {
        .pin_bit_mask = 1ULL << BOARD_TOUCH_RST,
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&rst_cfg);
    esp_lcd_touch_config_t tp_cfg = {
        .x_max = BOARD_LCD_H_RES,
        .y_max = BOARD_LCD_V_RES,
        .rst_gpio_num = GPIO_NUM_NC, /* reset done here, see above */
        .int_gpio_num = GPIO_NUM_NC, /* polled, like the CrowPanel build */
        .levels.reset = 0,
        .levels.interrupt = 0,
        .flags.swap_xy = 0,
#if CONFIG_RECORDER_HOSYOND_TOUCH_MIRROR_X
        .flags.mirror_x = 1,
#else
        .flags.mirror_x = 0,
#endif
#if CONFIG_RECORDER_HOSYOND_TOUCH_MIRROR_Y
        .flags.mirror_y = 1,
#else
        .flags.mirror_y = 0,
#endif
    };
    for (int attempt = 0; attempt < 3; attempt++) {
        gpio_set_level(BOARD_TOUCH_RST, 0);
        vTaskDelay(pdMS_TO_TICKS(100));
        gpio_set_level(BOARD_TOUCH_RST, 1);
        vTaskDelay(pdMS_TO_TICKS(150 + attempt * 150)); /* controller boot */
        err = esp_lcd_touch_new_i2c_st7123(tp_io, &tp_cfg, &s_touch);
        if (err == ESP_OK) {
            break;
        }
        s_touch = NULL;
    }
    if (err == ESP_OK) {
        s_touch_ok = true;
        ESP_LOGI(TAG, "touch @0x%02X ready (ST77922 built-in, st7123 protocol)", BOARD_TOUCH_ADDR);
    } else {
        ESP_LOGW(TAG, "touch init failed: %s", esp_err_to_name(err));
        /* Diagnostic only: list what answers on the bus. */
        for (int a = 0x08; a < 0x78; a++) {
            if (i2c_master_probe(bus, a, 20) == ESP_OK) {
                ESP_LOGW(TAG, "  I2C device answers at 0x%02X", a);
            }
        }
    }
}

esp_err_t display_init(i2c_master_bus_handle_t bus)
{
    s_lock = xSemaphoreCreateRecursiveMutex();
    if (!s_lock) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = panel_init();
    if (err != ESP_OK) {
        return err;
    }
    /* Vendor order: touch after the panel (the touch block is inside it). */
    touch_init(bus);

    lv_init();
    /* 0.6.7: same fix as the CrowPanel's 0.6.6 (display.c). The built-in
     * 64 KB LVGL pool can drop below one 24 KB layer buffer
     * (CONFIG_LV_DRAW_LAYER_SIMPLE_BUF_SIZE) once a dialog and an open
     * dropdown list are on screen, and LVGL then retries the layer
     * allocation forever (frozen screen, task watchdog). LVGL caps each
     * pool at the 64 KB pool size, so add four 60 KB pools in PSRAM. */
    const size_t extra_sz = 60 * 1024;
    int added = 0;
    for (int i = 0; i < 4; i++) {
        void *extra = heap_caps_malloc(extra_sz, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (extra && lv_mem_add_pool(extra, extra_sz)) {
            added++;
        } else {
            heap_caps_free(extra);
            break;
        }
    }
    if (added) {
        ESP_LOGI(TAG, "lvgl extra pools: %d x %u KB in PSRAM", added, (unsigned)(extra_sz / 1024));
    } else {
        ESP_LOGE(TAG, "lvgl extra pool not added");
    }
    lv_tick_set_cb(tick_cb);
    /* Created with the panel's native size; the rotation makes LVGL's
     * resolution 480x320 (BOARD_UI_W x BOARD_UI_H). */
    s_disp = lv_display_create(BOARD_LCD_H_RES, BOARD_LCD_V_RES);
    lv_display_set_rotation(s_disp, UI_ROTATION);
    lv_display_set_color_format(s_disp, LV_COLOR_FORMAT_RGB565);
    lv_display_set_user_data(s_disp, s_panel);
    lv_display_add_event_cb(s_disp, rounder_cb, LV_EVENT_INVALIDATE_AREA, NULL);

    /* The rotate buffer is what the SPI DMA reads: internal DMA RAM only. */
    size_t buf_sz = DRAW_BUF_BYTES;
    s_rot_buf = heap_caps_malloc(buf_sz, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA | MALLOC_CAP_8BIT);
    if (!s_rot_buf) {
        ESP_LOGE(TAG, "LCD rotate buffer (%u bytes) failed", (unsigned)buf_sz);
        return ESP_ERR_NO_MEM;
    }
    /* Draw buffers are only touched by the CPU: internal RAM, else PSRAM. */
    const char *where = "internal";
    void *b1 = heap_caps_malloc(buf_sz, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    void *b2 = heap_caps_malloc(buf_sz, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (!b1 || !b2) {
        heap_caps_free(b1);
        heap_caps_free(b2);
        where = "PSRAM (fallback)";
        b1 = heap_caps_malloc(buf_sz, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        b2 = heap_caps_malloc(buf_sz, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
    if (!b1 || !b2) {
        ESP_LOGE(TAG, "LVGL buffers failed");
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "LVGL %dx%d (rotated %d deg), draw buffers 2 x %u bytes in %s + rotate buffer",
             (int)lv_display_get_horizontal_resolution(s_disp), (int)lv_display_get_vertical_resolution(s_disp),
             UI_ROTATION == LV_DISPLAY_ROTATION_270 ? 270 : 90, (unsigned)buf_sz, where);
    lv_display_set_buffers(s_disp, b1, b2, buf_sz, LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(s_disp, flush_cb);

    lv_theme_default_init(s_disp, lv_color_hex(0x2F6BFF), lv_color_hex(0xE2231A), true,
                          &lv_font_montserrat_16);
    lv_obj_set_style_bg_color(lv_screen_active(), lv_color_hex(0x0B1220), 0);
    lv_obj_remove_flag(lv_screen_active(), LV_OBJ_FLAG_SCROLLABLE);

    if (s_touch) {
        lv_indev_t *indev = lv_indev_create();
        lv_indev_set_type(indev, LV_INDEV_TYPE_POINTER);
        lv_indev_set_read_cb(indev, touch_read_cb);
        lv_indev_set_user_data(indev, s_touch);
        lv_indev_set_display(indev, s_disp);
    }
    ESP_LOGI(TAG, "heap: internal free %u (largest %u), psram free %u",
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    return ESP_OK;
}

esp_err_t display_start_task(void)
{
    const uint32_t stack = 12288;
    BaseType_t ok = xTaskCreatePinnedToCore(lvgl_task, "lvgl", stack, NULL, 3, NULL, 1);
    const char *where = "internal";
    if (ok != pdPASS) {
        ESP_LOGW(TAG, "lvgl task: internal stack failed (largest internal block %u)",
                 (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));
        ok = xTaskCreatePinnedToCoreWithCaps(lvgl_task, "lvgl", stack, NULL, 3, NULL, 1,
                                             MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        where = "PSRAM";
    }
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "lvgl task create FAILED: UI will not run");
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "lvgl task started (%u byte stack in %s)", (unsigned)stack, where);
    return ESP_OK;
}

bool display_lock(uint32_t timeout_ms)
{
    if (!s_lock) {
        return false;
    }
    TickType_t ticks = timeout_ms == UINT32_MAX ? portMAX_DELAY : pdMS_TO_TICKS(timeout_ms);
    return xSemaphoreTakeRecursive(s_lock, ticks) == pdTRUE;
}

void display_unlock(void)
{
    if (s_lock) {
        xSemaphoreGiveRecursive(s_lock);
    }
}

bool display_touch_ok(void)
{
    return s_touch_ok;
}

/* Before deep sleep (0.7.0). Caller holds display_lock(), so LVGL can't
 * start another flush. Display off, then SLPIN (0x10) in the QSPI command
 * framing the st77922 component uses (opcode 0x02, command in bits 15..8).
 * The next boot's panel init sends SWRESET and SLPOUT again. */
void display_sleep(void)
{
    if (!s_panel || !s_panel_io) {
        return;
    }
    vTaskDelay(pdMS_TO_TICKS(50)); /* let the last strip's DMA finish */
    esp_lcd_panel_disp_on_off(s_panel, false);
    esp_err_t err = esp_lcd_panel_io_tx_param(s_panel_io, (0x02 << 24) | (0x10 << 8), NULL, 0);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "panel sleep-in: %s", esp_err_to_name(err));
    }
    vTaskDelay(pdMS_TO_TICKS(120));
}
