#include "sdcard.h"

#include "board_pins.h"
#include "esp_log.h"
#include "esp_vfs_fat.h"
#if CONFIG_RECORDER_BOARD_HOSYOND_35
#include "driver/sdmmc_host.h"
#else
#include "driver/sdspi_host.h"
#include "driver/spi_master.h"
#endif
#include "sdmmc_cmd.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdio.h>

static const char *TAG = "sd";
static sdmmc_card_t *s_card;
static bool s_mounted;
#if !CONFIG_RECORDER_BOARD_HOSYOND_35
static bool s_bus_ready;
#endif
static uint64_t s_total;
static uint64_t s_free;

#if !CONFIG_RECORDER_BOARD_HOSYOND_35
static esp_err_t mount_at(int freq_khz)
{
    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.max_freq_khz = freq_khz;
    sdspi_device_config_t dev = SDSPI_DEVICE_CONFIG_DEFAULT();
    dev.host_id = host.slot;
    dev.gpio_cs = BOARD_SD_CS;
    esp_vfs_fat_sdmmc_mount_config_t cfg = {
        .format_if_mount_failed = false, /* NEVER format the card. */
        .max_files = 6,
        .allocation_unit_size = 16 * 1024,
    };
    return esp_vfs_fat_sdspi_mount("/sd", &host, &dev, &cfg, &s_card);
}

esp_err_t sd_init(void)
{
    s_mounted = false;
    s_card = NULL;
    spi_bus_config_t bus = {
        .mosi_io_num = BOARD_SD_MOSI,
        .miso_io_num = BOARD_SD_MISO,
        .sclk_io_num = BOARD_SD_SCK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 4096,
    };
    esp_err_t err = spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "SPI bus: %s", esp_err_to_name(err));
        return err;
    }
    s_bus_ready = true;

    const int freqs[] = {20000, 4000};
    for (size_t i = 0; i < sizeof freqs / sizeof freqs[0]; i++) {
        ESP_LOGI(TAG, "mounting SPI %d kHz", freqs[i]);
        err = mount_at(freqs[i]);
        if (err == ESP_OK) {
            s_mounted = true;
            sdmmc_card_print_info(stdout, s_card);
            sd_refresh();
            ESP_LOGI(TAG, "mounted, %u h free of %u h",
                     (unsigned)(s_free / REC_BYTES_PER_HOUR),
                     (unsigned)(s_total / REC_BYTES_PER_HOUR));
            return ESP_OK;
        }
        ESP_LOGW(TAG, "mount %d kHz failed: %s", freqs[i], esp_err_to_name(err));
        s_card = NULL;
    }
    ESP_LOGE(TAG, "SD mount failed. Insert a FAT32 card and set DIP S1=1, S0=1. Card was not formatted.");
    return err;
}
#else /* CONFIG_RECORDER_BOARD_HOSYOND_35: SDMMC host, 4-bit */

static esp_err_t mount_sdmmc(int width, int freq_khz)
{
    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    host.max_freq_khz = freq_khz;
    sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
    slot.width = width;
    slot.clk = BOARD_SD_CLK;
    slot.cmd = BOARD_SD_CMD;
    slot.d0 = BOARD_SD_D0;
    if (width == 4) {
        slot.d1 = BOARD_SD_D1;
        slot.d2 = BOARD_SD_D2;
        slot.d3 = BOARD_SD_D3;
    }
    /* 10 k pull-ups are on the board; the internal ones do no harm. */
    slot.flags |= SDMMC_SLOT_FLAG_INTERNAL_PULLUP;
    esp_vfs_fat_sdmmc_mount_config_t cfg = {
        .format_if_mount_failed = false, /* NEVER format the card. */
        .max_files = 6,
        .allocation_unit_size = 16 * 1024,
    };
    return esp_vfs_fat_sdmmc_mount("/sd", &host, &slot, &cfg, &s_card);
}

esp_err_t sd_init(void)
{
    s_mounted = false;
    s_card = NULL;
    static const struct {
        int width;
        int freq_khz;
    } tries[] = {{4, SDMMC_FREQ_DEFAULT}, {4, 10000}, {1, SDMMC_FREQ_PROBING * 10}};
    esp_err_t err = ESP_FAIL;
    for (size_t i = 0; i < sizeof tries / sizeof tries[0]; i++) {
        ESP_LOGI(TAG, "mounting SDMMC %d-bit %d kHz", tries[i].width, tries[i].freq_khz);
        err = mount_sdmmc(tries[i].width, tries[i].freq_khz);
        if (err == ESP_OK) {
            s_mounted = true;
            sdmmc_card_print_info(stdout, s_card);
            sd_refresh();
            ESP_LOGI(TAG, "mounted, %u h free of %u h",
                     (unsigned)(s_free / REC_BYTES_PER_HOUR),
                     (unsigned)(s_total / REC_BYTES_PER_HOUR));
            return ESP_OK;
        }
        ESP_LOGW(TAG, "mount %d-bit %d kHz failed: %s", tries[i].width, tries[i].freq_khz, esp_err_to_name(err));
        s_card = NULL;
    }
    ESP_LOGE(TAG, "SD mount failed. Insert a FAT32 microSD card. Card was not formatted.");
    return err;
}
#endif

bool sd_mounted(void)
{
    return s_mounted;
}

void sd_refresh(void)
{
    if (!s_mounted) {
        s_total = 0;
        s_free = 0;
        return;
    }
    uint64_t total = 0, freeb = 0;
    if (esp_vfs_fat_info("/sd", &total, &freeb) == ESP_OK) {
        s_total = total;
        s_free = freeb;
    }
}

void sd_space(uint32_t *hours_free, uint32_t *hours_total, uint64_t *free_bytes)
{
    if (hours_free) {
        *hours_free = (uint32_t)(s_free / REC_BYTES_PER_HOUR);
    }
    if (hours_total) {
        *hours_total = (uint32_t)(s_total / REC_BYTES_PER_HOUR);
    }
    if (free_bytes) {
        *free_bytes = s_free;
    }
}

bool sd_space_low(void)
{
    if (!s_mounted) {
        return false;
    }
    return (s_free / REC_BYTES_PER_HOUR) < 2;
}

bool sd_can_record(char *reason, size_t len)
{
    if (reason && len) {
        reason[0] = '\0';
    }
    if (!s_mounted) {
        if (reason && len) {
            snprintf(reason, len, "Insert a FAT32 microSD card");
        }
        return false;
    }
    if (s_free < (uint64_t)REC_BYTES_PER_SEC * 30) {
        if (reason && len) {
            snprintf(reason, len, "SD card is full");
        }
        return false;
    }
    return true;
}

static TaskHandle_t s_refresh_task;

static void refresh_task(void *arg)
{
    (void)arg;
    while (1) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        sd_refresh();
    }
}

void sd_refresh_async(void)
{
    if (!s_refresh_task) {
        if (xTaskCreatePinnedToCore(refresh_task, "sd_space", 4096, NULL, 2, &s_refresh_task, 0) != pdPASS) {
            s_refresh_task = NULL;
            return;
        }
    }
    xTaskNotifyGive(s_refresh_task);
}
