#include "battery.h"

#include "board_pins.h"
#include "driver/usb_serial_jtag.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

static const char *TAG = "battery";

#define BATT_ADC_SAMPLES 32
#define BATT_LOG_EVERY_MS 30000u

static adc_oneshot_unit_handle_t s_adc;
static adc_cali_handle_t s_cali;
static adc_channel_t s_chan;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static batt_model_t s_model;
static volatile batt_load_t s_load = BATT_LOAD_IDLE;

/* Pin voltage in mV (mean of BATT_ADC_SAMPLES reads), or -1. */
static int read_pin_mv(void)
{
    int64_t sum = 0;
    int n = 0;
    for (int i = 0; i < BATT_ADC_SAMPLES; i++) {
        int raw = 0;
        if (adc_oneshot_read(s_adc, s_chan, &raw) != ESP_OK) {
            continue;
        }
        int mv = 0;
        if (s_cali && adc_cali_raw_to_voltage(s_cali, raw, &mv) == ESP_OK) {
            sum += mv;
        } else {
            sum += (int64_t)raw * 3100 / 4095; /* uncalibrated fallback, 12 dB */
        }
        n++;
    }
    return n ? (int)(sum / n) : -1;
}

static void battery_task(void *arg)
{
    (void)arg;
    uint32_t last_log = 0;
    batt_src_t last_src = BATT_SRC_UNKNOWN;
    bool first = true;
    for (;;) {
        int pin = read_pin_mv();
        if (pin >= 0) {
            int vbat = (int)((int64_t)pin * 2 * CONFIG_RECORDER_BATTERY_CAL_PERMILLE / 1000);
            bool host = usb_serial_jtag_is_connected();
            batt_load_t load = s_load;
            uint32_t now = (uint32_t)(esp_timer_get_time() / 1000);
            portENTER_CRITICAL(&s_lock);
            batt_model_update(&s_model, vbat, load, host, now);
            batt_model_t m = s_model;
            portEXIT_CRITICAL(&s_lock);
            if (first || m.src != last_src || now - last_log >= BATT_LOG_EVERY_MS) {
                ESP_LOGI(TAG, "vbat %d mV (pin %d mV, cal %d/1000)  smoothed %d mV  %d%%  source %s  load %d  usb-host %d",
                         m.vbat_mv, pin, CONFIG_RECORDER_BATTERY_CAL_PERMILLE, m.smooth_mv, m.pct,
                         batt_src_name(m.src), (int)load, (int)host);
                if (!first && m.src != last_src) {
                    ESP_LOGI(TAG, "power source: %s -> %s", batt_src_name(last_src), batt_src_name(m.src));
                }
                last_src = m.src;
                last_log = now;
                first = false;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(BATT_SAMPLE_MS));
    }
}

void battery_init(void)
{
    batt_model_init(&s_model);
    adc_unit_t unit;
    if (adc_oneshot_io_to_channel(BOARD_BAT_ADC, &unit, &s_chan) != ESP_OK) {
        ESP_LOGE(TAG, "GPIO%d is not an ADC pin", (int)BOARD_BAT_ADC);
        return;
    }
    adc_oneshot_unit_init_cfg_t ucfg = {.unit_id = unit};
    if (adc_oneshot_new_unit(&ucfg, &s_adc) != ESP_OK) {
        ESP_LOGE(TAG, "ADC unit init failed");
        return;
    }
    adc_oneshot_chan_cfg_t ccfg = {.atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT};
    if (adc_oneshot_config_channel(s_adc, s_chan, &ccfg) != ESP_OK) {
        ESP_LOGE(TAG, "ADC channel config failed");
        return;
    }
    adc_cali_curve_fitting_config_t cal = {
        .unit_id = unit, .chan = s_chan, .atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT};
    if (adc_cali_create_scheme_curve_fitting(&cal, &s_cali) != ESP_OK) {
        s_cali = NULL;
        ESP_LOGW(TAG, "no ADC calibration in eFuse: readings uncalibrated");
    }
    if (xTaskCreate(battery_task, "battery", 3072, NULL, 2, NULL) != pdPASS) {
        ESP_LOGE(TAG, "battery task create failed");
        return;
    }
    ESP_LOGI(TAG, "gauge on GPIO%d (ADC%d ch%d), x2 divider, correction %d/1000, auto save at %d%%: %s",
             (int)BOARD_BAT_ADC, (int)unit + 1, (int)s_chan, CONFIG_RECORDER_BATTERY_CAL_PERMILLE, BATT_CRIT_PCT,
#if CONFIG_RECORDER_BATTERY_AUTOSAVE
             "on"
#else
             "off"
#endif
    );
}

void battery_set_load(batt_load_t load)
{
    s_load = load;
}

void battery_get(battery_status_t *out)
{
    portENTER_CRITICAL(&s_lock);
    out->src = s_model.src;
    out->pct = s_model.pct;
    out->vbat_mv = s_model.vbat_mv;
    out->smooth_mv = s_model.smooth_mv;
    portEXIT_CRITICAL(&s_lock);
}
