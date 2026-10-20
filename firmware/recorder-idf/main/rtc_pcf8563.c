#include "rtc_pcf8563.h"

#include "board_pins.h"
#include "esp_log.h"
#include <stdio.h>
#include <string.h>
#include <sys/time.h>

static const char *TAG = "rtc";
static i2c_master_dev_handle_t s_dev;
static bool s_present;

static uint8_t bcd2dec(uint8_t b)
{
    return (uint8_t)((b >> 4) * 10 + (b & 0x0F));
}

static uint8_t dec2bcd(int d)
{
    return (uint8_t)(((d / 10) << 4) | (d % 10));
}

static const char *WDAY[] = {"Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"};
static const char *MON[] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun",
                            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};

void clock_format_ampm(const struct tm *t, bool with_seconds, char *out, size_t n)
{
    if (!t || !out || n == 0) {
        return;
    }
    int h = t->tm_hour;
    const char *ampm = "AM";
    if (h >= 12) {
        ampm = "PM";
        if (h > 12) {
            h -= 12;
        }
    }
    if (h == 0) {
        h = 12;
    }
    if (with_seconds) {
        snprintf(out, n, "%d:%02d:%02d %s", h, t->tm_min, t->tm_sec, ampm);
    } else {
        snprintf(out, n, "%d:%02d %s", h, t->tm_min, ampm);
    }
}

void clock_format_date(const struct tm *t, char *out, size_t n)
{
    if (!t || !out || n == 0) {
        return;
    }
    int w = t->tm_wday;
    int m = t->tm_mon;
    if (w < 0 || w > 6) {
        w = 0;
    }
    if (m < 0 || m > 11) {
        m = 0;
    }
    snprintf(out, n, "%s, %s %d", WDAY[w], MON[m], t->tm_mday);
}

esp_err_t pcf8563_init(i2c_master_bus_handle_t bus)
{
    if (!bus) {
        return ESP_ERR_INVALID_STATE;
    }
#if CONFIG_RECORDER_BOARD_HOSYOND_35
    /* No RTC chip on this board: the ESP32 system clock (set by NTP over
     * Wi-Fi) is the only clock. It survives a soft restart, not power-off. */
    ESP_LOGW(TAG, "no RTC chip on this board; clock comes from Wi-Fi (NTP)");
    s_present = false;
    return ESP_ERR_NOT_SUPPORTED;
#endif
    if (i2c_master_probe(bus, BOARD_RTC_ADDR, 50) != ESP_OK) {
        ESP_LOGW(TAG, "PCF8563 @0x51 not found — time will show as not set");
        s_present = false;
        return ESP_ERR_NOT_FOUND;
    }
    i2c_device_config_t cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = BOARD_RTC_ADDR,
        .scl_speed_hz = 100000,
    };
    esp_err_t err = i2c_master_bus_add_device(bus, &cfg, &s_dev);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "add device: %s", esp_err_to_name(err));
        return err;
    }
    s_present = true;
    /* Start the oscillator. STOP is bit 5 of control 1. */
    uint8_t ctrl[2] = {0x00, 0x00};
    i2c_master_transmit(s_dev, ctrl, 2, 100);

    struct tm now;
    if (rtc_now(&now)) {
        struct tm tmp = now;
        tmp.tm_isdst = -1;
        time_t sec = mktime(&tmp);
        if (sec != (time_t)-1) {
            struct timeval tv = {.tv_sec = sec};
            settimeofday(&tv, NULL);
        }
        char pretty[32];
        clock_format_ampm(&now, true, pretty, sizeof pretty);
        ESP_LOGI(TAG, "clock %s", pretty);
    } else {
        ESP_LOGW(TAG, "clock not valid (VL bit or date out of range)");
    }
    return ESP_OK;
}

bool rtc_present(void)
{
    return s_present;
}

bool rtc_now(struct tm *out)
{
    if (!out) {
        return false;
    }
    memset(out, 0, sizeof *out);
    if (!s_dev) {
#if CONFIG_RECORDER_BOARD_HOSYOND_35
        /* No RTC chip: trust the system clock once NTP has set it. */
        time_t now = time(NULL);
        struct tm local;
        localtime_r(&now, &local);
        if (local.tm_year + 1900 < 2024) {
            return false;
        }
        *out = local;
        return true;
#else
        return false;
#endif
    }
    uint8_t reg = 0x02;
    uint8_t raw[7];
    if (i2c_master_transmit_receive(s_dev, &reg, 1, raw, 7, 100) != ESP_OK) {
        return false;
    }
    bool vl = (raw[0] & 0x80) != 0;
    int sec = bcd2dec(raw[0] & 0x7F);
    int min = bcd2dec(raw[1] & 0x7F);
    int hour = bcd2dec(raw[2] & 0x3F);
    int day = bcd2dec(raw[3] & 0x3F);
    int wday = raw[4] & 0x07;
    int mon = bcd2dec(raw[5] & 0x1F);
    int year = bcd2dec(raw[6]);
    if (vl || mon < 1 || mon > 12 || day < 1 || day > 31 || hour > 23 || min > 59 || sec > 59 ||
        year < 24 || year > 99) {
        return false;
    }
    out->tm_year = 2000 + year - 1900;
    out->tm_mon = mon - 1;
    out->tm_mday = day;
    out->tm_hour = hour;
    out->tm_min = min;
    out->tm_sec = sec;
    out->tm_wday = wday;
    out->tm_isdst = -1;
    struct tm tmp = *out;
    if (mktime(&tmp) != (time_t)-1) {
        *out = tmp;
    }
    return true;
}

esp_err_t rtc_set_from_system(void)
{
    if (!s_dev) {
#if CONFIG_RECORDER_BOARD_HOSYOND_35
        /* Nothing to save to: the system clock is the clock. */
        return time(NULL) > 1704067200 ? ESP_OK : ESP_ERR_INVALID_STATE; /* 2024-01-01 */
#else
        return ESP_ERR_INVALID_STATE;
#endif
    }
    time_t now = time(NULL);
    struct tm local;
    localtime_r(&now, &local);
    if (local.tm_year + 1900 < 2024) {
        return ESP_ERR_INVALID_STATE;
    }
    uint8_t ctrl[2] = {0x00, 0x00};
    i2c_master_transmit(s_dev, ctrl, 2, 100);
    uint8_t buf[8];
    buf[0] = 0x02;
    buf[1] = dec2bcd(local.tm_sec) & 0x7F;
    buf[2] = dec2bcd(local.tm_min) & 0x7F;
    buf[3] = dec2bcd(local.tm_hour) & 0x3F;
    buf[4] = dec2bcd(local.tm_mday) & 0x3F;
    buf[5] = (uint8_t)(local.tm_wday & 0x07);
    buf[6] = dec2bcd(local.tm_mon + 1) & 0x1F;
    buf[7] = dec2bcd((local.tm_year + 1900) - 2000);
    esp_err_t err = i2c_master_transmit(s_dev, buf, 8, 100);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "RTC updated from network time");
    }
    return err;
}
