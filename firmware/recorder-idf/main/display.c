#include "display.h"

#include "sdkconfig.h"

#include "board_ctrl.h"
#include "board_pins.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_rgb.h"
#include "esp_lcd_touch_gt911.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lvgl.h"

static const char *TAG = "display";
static esp_lcd_panel_handle_t s_panel;
static esp_lcd_touch_handle_t s_touch;
static lv_display_t *s_disp;
static SemaphoreHandle_t s_lock;
static bool s_touch_ok;

static uint32_t tick_cb(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000ULL);
}

static void flush_cb(lv_display_t *disp, const lv_area_t *area, uint8_t *px_map)
{
    esp_lcd_panel_handle_t panel = lv_display_get_user_data(disp);
    /* RGB draw_bitmap copies into the framebuffer and returns when done. */
    esp_lcd_panel_draw_bitmap(panel, area->x1, area->y1, area->x2 + 1, area->y2 + 1, px_map);
    lv_display_flush_ready(disp);
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
        data->point.x = point.x;
        data->point.y = point.y;
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
            /* The first frame is now in the framebuffer. Light the backlight
             * again here: the command sent during early boot is not always
             * honoured, which left the screen dark until the first touch. */
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

static uint8_t detect_gt911(i2c_master_bus_handle_t bus)
{
    if (!bus) {
        return 0;
    }
    if (i2c_master_probe(bus, BOARD_GT911_ADDR, 50) == ESP_OK) {
        return BOARD_GT911_ADDR;
    }
    board_ctrl_cmd(BOARD_CMD_TOUCH_ON);
    vTaskDelay(pdMS_TO_TICKS(80));
    if (i2c_master_probe(bus, BOARD_GT911_ADDR, 80) == ESP_OK) {
        return BOARD_GT911_ADDR;
    }
    if (i2c_master_probe(bus, BOARD_GT911_ADDR_ALT, 50) == ESP_OK) {
        return BOARD_GT911_ADDR_ALT;
    }
    return 0;
}

esp_err_t display_init(i2c_master_bus_handle_t bus)
{
    s_lock = xSemaphoreCreateRecursiveMutex();
    if (!s_lock) {
        return ESP_ERR_NO_MEM;
    }

    esp_lcd_rgb_panel_config_t panel_cfg = {
        .clk_src = LCD_CLK_SRC_DEFAULT,
        .timings = {
            .pclk_hz = BOARD_LCD_PCLK_HZ,
            .h_res = BOARD_LCD_H_RES,
            .v_res = BOARD_LCD_V_RES,
            .hsync_pulse_width = 4,
            .hsync_back_porch = 8,
            .hsync_front_porch = 8,
            .vsync_pulse_width = 4,
            .vsync_back_porch = 8,
            .vsync_front_porch = 8,
            .flags.pclk_active_neg = 1,
        },
        .data_width = 16,
        .bits_per_pixel = 16,
        .num_fbs = 1,
        /* Elecrow's proven 10 lines (2 x 16 KB, internal RAM). 20 lines
         * would cost 32 KB more internal RAM, which this build lacks. */
        .bounce_buffer_size_px = BOARD_LCD_H_RES * 10,
        .dma_burst_size = 64,
        .hsync_gpio_num = BOARD_LCD_HSYNC,
        .vsync_gpio_num = BOARD_LCD_VSYNC,
        .de_gpio_num = BOARD_LCD_DE,
        .pclk_gpio_num = BOARD_LCD_PCLK,
        .disp_gpio_num = -1,
        .data_gpio_nums = {
            BOARD_LCD_D0, BOARD_LCD_D1, BOARD_LCD_D2, BOARD_LCD_D3,
            BOARD_LCD_D4, BOARD_LCD_D5, BOARD_LCD_D6, BOARD_LCD_D7,
            BOARD_LCD_D8, BOARD_LCD_D9, BOARD_LCD_D10, BOARD_LCD_D11,
            BOARD_LCD_D12, BOARD_LCD_D13, BOARD_LCD_D14, BOARD_LCD_D15,
        },
        .flags.fb_in_psram = 1,
    };

    esp_err_t err = esp_lcd_new_rgb_panel(&panel_cfg, &s_panel);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "rgb panel create: %s", esp_err_to_name(err));
        return err;
    }
    ESP_ERROR_CHECK(esp_lcd_panel_reset(s_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(s_panel));

    uint8_t addr = detect_gt911(bus);
    if (addr) {
        esp_lcd_panel_io_handle_t tp_io = NULL;
        esp_lcd_panel_io_i2c_config_t io_cfg = ESP_LCD_TOUCH_IO_I2C_GT911_CONFIG();
        io_cfg.dev_addr = addr;
        io_cfg.scl_speed_hz = 400000;
        err = esp_lcd_new_panel_io_i2c_v2(bus, &io_cfg, &tp_io);
        if (err == ESP_OK) {
            esp_lcd_touch_config_t tp_cfg = {
                .x_max = BOARD_LCD_H_RES,
                .y_max = BOARD_LCD_V_RES,
                .rst_gpio_num = GPIO_NUM_NC,
                .int_gpio_num = GPIO_NUM_NC,
                .levels.reset = 0,
                .levels.interrupt = 0,
            };
            err = esp_lcd_touch_new_i2c_gt911(tp_io, &tp_cfg, &s_touch);
        }
        if (err == ESP_OK) {
            s_touch_ok = true;
            ESP_LOGI(TAG, "GT911 @0x%02X", addr);
        } else {
            ESP_LOGW(TAG, "GT911 init failed: %s", esp_err_to_name(err));
            s_touch = NULL;
        }
    } else {
        ESP_LOGW(TAG, "GT911 not found on I2C");
    }

    lv_init();
    /* 0.6.6: the 64 KB internal LVGL pool had ~24 KB free after boot, just
     * under one 24 KB layer buffer (CONFIG_LV_DRAW_LAYER_SIMPLE_BUF_SIZE).
     * Opening a dropdown on the end dialog then left LVGL retrying a layer
     * allocation forever: frozen screen, task watchdog. A second pool in
     * PSRAM gives large draw/layer buffers room; small objects still fit.
     * LVGL's allocator caps each pool at the 64 KB pool size, so add four
     * 60 KB pools rather than one big one. */
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
    s_disp = lv_display_create(BOARD_LCD_H_RES, BOARD_LCD_V_RES);
    lv_display_set_color_format(s_disp, LV_COLOR_FORMAT_RGB565);
    lv_display_set_user_data(s_disp, s_panel);

    /* LVGL renders into internal DMA-capable RAM so a full-screen redraw
     * (e.g. Home -> Session on Record) does not also hammer PSRAM. Falls
     * back to PSRAM only if internal RAM is short. */
    size_t buf_sz = BOARD_LCD_H_RES * 10 * 2; /* 2 x 16 KB */
    const char *where = "internal";
    void *b1 = heap_caps_malloc(buf_sz, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA | MALLOC_CAP_8BIT);
    void *b2 = heap_caps_malloc(buf_sz, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA | MALLOC_CAP_8BIT);
    if (!b1 || !b2) {
        heap_caps_free(b1);
        heap_caps_free(b2);
        buf_sz = BOARD_LCD_H_RES * 40 * 2;
        where = "PSRAM (fallback)";
        b1 = heap_caps_malloc(buf_sz, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        b2 = heap_caps_malloc(buf_sz, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
    if (!b1 || !b2) {
        ESP_LOGE(TAG, "LVGL buffers failed");
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "LVGL draw buffers 2 x %u bytes in %s", (unsigned)buf_sz, where);
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
    ESP_LOGI(TAG, "panel ready (bounce %u px, xip_psram=%d, rgb_iram_safe=%d, restart_vsync=%d)",
             (unsigned)(BOARD_LCD_H_RES * 10),
#if CONFIG_SPIRAM_FETCH_INSTRUCTIONS && CONFIG_SPIRAM_RODATA
             1,
#else
             0,
#endif
#if CONFIG_LCD_RGB_ISR_IRAM_SAFE
             1,
#else
             0,
#endif
#if CONFIG_LCD_RGB_RESTART_IN_VSYNC
             1);
#else
             0);
#endif
    ESP_LOGI(TAG, "heap: internal free %u (largest %u), psram free %u",
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    return ESP_OK;
}

esp_err_t display_start_task(void)
{
    /* 12 KB internal stack; if internal RAM is short, put the stack in
     * PSRAM (allowed: this task does no flash writes, and code runs from
     * PSRAM with XIP). Previously the result was unchecked and a failed
     * create left the screen black with no error. */
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
