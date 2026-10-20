#include "sdkconfig.h"
#if CONFIG_RECORDER_SD_ENCRYPTION
#include "rec_crypt.h"
#include "esp_random.h"
#endif
#include "audio_rec.h"

#include "board_ctrl.h"
#include "board_pins.h"
#include "device_info.h"
#if CONFIG_RECORDER_BOARD_HOSYOND_35
#include "driver/i2s_std.h"
#include "es8311_min.h"
#include "i2c_bus.h"
#else
#include "driver/i2s_pdm.h"
#endif
#include "esp_attr.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "rtc_pcf8563.h"
#include "upload_name.h"
#include "version.h"
#include "meter_level.h"
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <errno.h>
#include <unistd.h>
#include <math.h>

static const char *TAG = "audio";

/* The ring stays 256 KB (8 s of audio) in PSRAM. Its traffic is 64 KB/s
 * (write + read), ~0.2% of the 32 MB/s RGB scan-out, so it is not what
 * disturbs the panel, and a smaller internal ring (the build has only
 * ~179 KB DIRAM left before heap) would risk dropped samples on SD
 * write stalls. */
#define RING_CAP_PSRAM (256 * 1024)
static size_t s_ring_cap = RING_CAP_PSRAM;
#define CMD_START  1
#define CMD_PAUSE  2
#define CMD_RESUME 3
#define CMD_SAVE   4

typedef struct {
    int op;
    rec_labels_t labels;
} audio_cmd_t;

static uint8_t *s_ring;
static _Atomic uint32_t s_prod;
static _Atomic uint32_t s_cons;

static SemaphoreHandle_t s_mu;
static QueueHandle_t s_queue;
static TaskHandle_t s_writer_task;

static _Atomic int s_state;
static i2s_chan_handle_t s_rx;
static bool s_i2s_ready;
static bool s_rx_on;

static void rx_stop(void)
{
    if (s_rx_on && s_rx) {
        i2s_channel_disable(s_rx);
        s_rx_on = false;
    }
}

static char s_path[160];
static bool s_open_req;
static rec_labels_t s_labels;
static rec_labels_t s_save_labels;
static bool s_have_save_labels;
static struct tm s_start;
static bool s_time_valid;
static uint64_t s_bytes;
/* Audio bytes accepted into the file stream (ring) for this session. */
static _Atomic uint32_t s_captured;

typedef struct {
    char type[32];
    audio_mark_time_t t;
} marker_t;
/* Both marker tables live in PSRAM (allocated in audio_init) to keep internal RAM free. */
static marker_t *s_markers;
static marker_t *s_marks_copy;
static int s_nmarkers;
static uint8_t s_level;
#ifdef BOARD_METER_DB_MIN
/* Falling peak hold for the dBFS meter (capture task only). */
static float s_meter_hold_db = METER_HOLD_RESET_DB;
#endif
static char s_err[80];
static uint32_t s_err_seq;
static uint32_t s_overruns;
static bool s_overrun_logged;

static size_t ring_used(void)
{
    uint32_t p = atomic_load_explicit(&s_prod, memory_order_acquire);
    uint32_t c = atomic_load_explicit(&s_cons, memory_order_acquire);
    return (size_t)(p - c);
}

static bool ring_write(const uint8_t *src, size_t n)
{
    uint32_t p = atomic_load_explicit(&s_prod, memory_order_relaxed);
    uint32_t c = atomic_load_explicit(&s_cons, memory_order_acquire);
    if ((size_t)(p - c) + n > s_ring_cap) {
        return false;
    }
    size_t idx = p % s_ring_cap;
    size_t first = s_ring_cap - idx;
    if (first > n) {
        first = n;
    }
    memcpy(s_ring + idx, src, first);
    if (n > first) {
        memcpy(s_ring, src + first, n - first);
    }
    atomic_store_explicit(&s_prod, p + (uint32_t)n, memory_order_release);
    return true;
}

static size_t ring_read(uint8_t *dst, size_t maxn)
{
    uint32_t p = atomic_load_explicit(&s_prod, memory_order_acquire);
    uint32_t c = atomic_load_explicit(&s_cons, memory_order_relaxed);
    size_t used = (size_t)(p - c);
    if (used == 0 || maxn == 0) {
        return 0;
    }
    size_t n = used < maxn ? used : maxn;
    size_t idx = c % s_ring_cap;
    size_t first = s_ring_cap - idx;
    if (first > n) {
        first = n;
    }
    memcpy(dst, s_ring + idx, first);
    if (n > first) {
        memcpy(dst + first, s_ring, n - first);
    }
    atomic_store_explicit(&s_cons, c + (uint32_t)n, memory_order_release);
    return n;
}

static void set_err(const char *msg)
{
    xSemaphoreTake(s_mu, portMAX_DELAY);
    strlcpy(s_err, msg, sizeof s_err);
    s_err_seq++;
    xSemaphoreGive(s_mu);
    ESP_LOGE(TAG, "%s", msg);
}

static void write_le16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v & 0xFF);
    p[1] = (uint8_t)(v >> 8);
}

static void write_le32(uint8_t *p, uint32_t v)
{
    for (int i = 0; i < 4; i++) {
        p[i] = (uint8_t)((v >> (8 * i)) & 0xFF);
    }
}

static __attribute__((unused)) void build_wav_header(uint8_t *h, uint32_t data_bytes)
{
    const uint32_t byte_rate = REC_SAMPLE_RATE * REC_CHANNELS * REC_BITS / 8;
    const uint16_t align = REC_CHANNELS * REC_BITS / 8;
    memcpy(h, "RIFF", 4);
    write_le32(h + 4, 36 + data_bytes);
    memcpy(h + 8, "WAVE", 4);
    memcpy(h + 12, "fmt ", 4);
    write_le32(h + 16, 16);
    write_le16(h + 20, 1);
    write_le16(h + 22, REC_CHANNELS);
    write_le32(h + 24, REC_SAMPLE_RATE);
    write_le32(h + 28, byte_rate);
    write_le16(h + 32, align);
    write_le16(h + 34, REC_BITS);
    memcpy(h + 36, "data", 4);
    write_le32(h + 40, data_bytes);
}

#if CONFIG_RECORDER_SD_ENCRYPTION
/* / G5: recordings are APENC1 on the SD card (see rec_crypt.h).
 * Only the public key is on the device; the cloud unwraps with Cloud KMS. */
extern const unsigned char rec_pubkey_pem_start[] asm("_binary_recording_pubkey_pem_start");
extern const unsigned char rec_pubkey_pem_end[] asm("_binary_recording_pubkey_pem_end");
static rec_crypt_t s_crypt; /* internal RAM, 4 KB + tag, word aligned */

static int rec_rng(void *ctx, unsigned char *out, size_t len)
{
    (void)ctx;
    esp_fill_random(out, len); /* hardware RNG; true random while Wi-Fi/RF is on */
    return 0;
}

static size_t write_all(FILE *fp, const uint8_t *buf, size_t n);
static int crypt_write(void *ctx, const uint8_t *buf, size_t n)
{
    return write_all((FILE *)ctx, buf, n) == n ? 0 : -1;
}
#endif

static void json_escape(FILE *f, const char *s)
{
    fputc('"', f);
    for (; s && *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') {
            fputc('\\', f);
            fputc(c, f);
        } else if (c < 0x20) {
            fprintf(f, "\\u%04x", c);
        } else {
            fputc(c, f);
        }
    }
    fputc('"', f);
}

static void sanitize_code(char *dst, size_t n, const char *src)
{
    size_t j = 0;
    for (size_t i = 0; src && src[i] && j + 1 < n; i++) {
        char c = src[i];
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
            (c >= '0' && c <= '9') || c == '-' || c == '_') {
            dst[j++] = c;
        } else if (c == ' ') {
            dst[j++] = '-';
        }
    }
    if (j == 0) {
        strlcpy(dst, "room", n);
    } else {
        dst[j] = '\0';
    }
}

static bool path_exists(const char *path)
{
    return access(path, F_OK) == 0;
}

static void unique_path(char *dst, size_t n, const char *dir_and_stem)
{
    snprintf(dst, n, "%s.wav", dir_and_stem);
    if (!path_exists(dst)) {
        return;
    }
    for (int i = 2; i < 100; i++) {
        snprintf(dst, n, "%s_%d.wav", dir_and_stem, i);
        if (!path_exists(dst)) {
            return;
        }
    }
}

static void build_recording_path(char *dst, size_t n, const char *code, bool time_ok, const struct tm *t)
{
    char safe[32];
    sanitize_code(safe, sizeof safe, code);
    char stem[140];
    if (time_ok && t) {
        snprintf(stem, sizeof stem, "/sd/rec/%04d%02d%02d_%02d%02d%02d_%s",
                 t->tm_year + 1900, t->tm_mon + 1, t->tm_mday,
                 t->tm_hour, t->tm_min, t->tm_sec, safe);
    } else {
        unsigned long secs = (unsigned long)(esp_timer_get_time() / 1000000ULL);
        snprintf(stem, sizeof stem, "/sd/rec/NOTIME_%lu_%s", secs, safe);
    }
    unique_path(dst, n, stem);
}

static void write_sidecar(const char *wav_path, uint64_t data_bytes)
{
    char json_path[176];
    strlcpy(json_path, wav_path, sizeof json_path);
    char *dot = strrchr(json_path, '.');
    if (!dot || strlen(dot) + 1 > sizeof json_path - (size_t)(dot - json_path)) {
        return;
    }
    strlcpy(dot, ".json", sizeof json_path - (size_t)(dot - json_path));

    xSemaphoreTake(s_mu, portMAX_DELAY);
    rec_labels_t labels = s_have_save_labels ? s_save_labels : s_labels;
    struct tm start = s_start;
    bool time_ok = s_time_valid;
    xSemaphoreGive(s_mu);

    FILE *f = fopen(json_path, "wb");
    if (!f) {
        ESP_LOGE(TAG, "sidecar open failed: %s", json_path);
        return;
    }
    uint32_t ms = (uint32_t)((data_bytes * 1000ULL) / REC_BYTES_PER_SEC);
    fprintf(f, "{\n");
    if (time_ok) {
        char ts[40];
        strftime(ts, sizeof ts, "%Y-%m-%dT%H:%M:%S%z", &start);
        fprintf(f, "  \"start_time\": \"%s\",\n", ts);
    } else {
        fprintf(f, "  \"start_time\": null,\n");
    }
    fprintf(f, "  \"time_valid\": %s,\n", time_ok ? "true" : "false");
    fprintf(f, "  \"duration_seconds\": %lu,\n", (unsigned long)(ms / 1000));
    fprintf(f, "  \"duration_ms\": %lu,\n", (unsigned long)ms);
    fprintf(f, "  \"classroom_code\": ");
    json_escape(f, labels.classroom_code);
    fprintf(f, ",\n  \"classroom_label\": ");
    json_escape(f, labels.classroom_label);
    fprintf(f, ",\n  \"teacher\": ");
    json_escape(f, labels.teacher);
    /* class_code matches the cloud file name ("Reading-3A"); class_label
     * is the name as shown on screen ("Reading 3A"). */
    char code[24];
    upload_class_code(code, sizeof code, labels.class_code);
    fprintf(f, ",\n  \"class_code\": ");
    json_escape(f, code);
    fprintf(f, ",\n  \"class_label\": ");
    json_escape(f, labels.class_code);
    fprintf(f, ",\n  \"device_id\": \"%s\",\n", device_id_str());
    fprintf(f, "  \"firmware_version\": \"%s\",\n", FW_VERSION);
    fprintf(f, "  \"sample_rate_hz\": %d,\n", REC_SAMPLE_RATE);
    fprintf(f, "  \"channels\": %d,\n", REC_CHANNELS);
    fprintf(f, "  \"bits_per_sample\": %d,\n", REC_BITS);
    fprintf(f, "  \"markers\": [");
    xSemaphoreTake(s_mu, portMAX_DELAY);
    int nm = s_nmarkers;
    marker_t *marks = s_marks_copy;
    if (!marks || !s_markers) nm = 0;
    if (nm) memcpy(marks, s_markers, sizeof(marker_t) * nm);
    xSemaphoreGive(s_mu);
    for (int i = 0; i < nm; i++) {
        fprintf(f, "%s\n    {\"type\": ", i ? "," : "");
        json_escape(f, marks[i].type);
        fprintf(f, ", \"offset_ms\": %lu, \"wall_time\": ", (unsigned long)marks[i].t.offset_ms);
        if (marks[i].t.wall_valid) {
            struct tm wt;
            char ts[40];
            localtime_r(&marks[i].t.wall, &wt);
            strftime(ts, sizeof ts, "%Y-%m-%dT%H:%M:%S%z", &wt);
            fprintf(f, "\"%s\"}", ts);
        } else {
            fprintf(f, "null}");
        }
    }
    fprintf(f, "%s],\n", nm ? "\n  " : "");
#if CONFIG_RECORDER_SD_ENCRYPTION
    fprintf(f, "  \"encryption\": \"apenc1\",\n");
    fprintf(f, "  \"encryption_key_id\": \"%s\"\n}\n", CONFIG_RECORDER_REC_KEY_ID);
#else
    fprintf(f, "  \"encryption\": \"none\"\n}\n");
#endif
    fflush(f);
    fsync(fileno(f));
    fclose(f);
    ESP_LOGI(TAG, "sidecar %s", json_path);
}

static void apply_gain(uint8_t *buf, size_t n, int16_t *peak)
{
    int16_t *s = (int16_t *)buf;
    int16_t local = 0;
    for (size_t i = 0; i < n / 2; i++) {
        int32_t v = (int32_t)s[i] * REC_SW_GAIN;
        if (v > 32767) {
            v = 32767;
        } else if (v < -32767) {
            v = -32767;
        }
        s[i] = (int16_t)v;
        int16_t a = s[i] < 0 ? (int16_t)-s[i] : s[i];
        if (a > local) {
            local = a;
        }
    }
    if (peak) {
        *peak = local;
    }
}

#if !CONFIG_RECORDER_BOARD_HOSYOND_35
static esp_err_t ensure_i2s(void)
{
    if (s_i2s_ready) {
        return ESP_OK;
    }
    i2s_chan_config_t chan = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan.dma_desc_num = 8;
    chan.dma_frame_num = 512;
    chan.auto_clear = true;
    esp_err_t err = i2s_new_channel(&chan, NULL, &s_rx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S channel: %s", esp_err_to_name(err));
        s_rx = NULL;
        return err;
    }
    i2s_pdm_rx_config_t pdm = {
        .clk_cfg = I2S_PDM_RX_CLK_DEFAULT_CONFIG(REC_SAMPLE_RATE),
        .slot_cfg = I2S_PDM_RX_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .clk = BOARD_MIC_CLK,
            .din = BOARD_MIC_DATA,
        },
    };
    /* Same down-sample mode as mic_sd_test: DSR_16S -> 128x -> 2.048 MHz. */
    pdm.clk_cfg.dn_sample_mode = I2S_PDM_DSR_16S;
    err = i2s_channel_init_pdm_rx_mode(s_rx, &pdm);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "PDM init: %s", esp_err_to_name(err));
        i2s_del_channel(s_rx);
        s_rx = NULL;
        return err;
    }
    s_i2s_ready = true;
    ESP_LOGI(TAG, "PDM clock %u Hz (128x), sw gain x%d",
             (unsigned)(REC_SAMPLE_RATE * 128), REC_SW_GAIN);
    return ESP_OK;
}
#else /* CONFIG_RECORDER_BOARD_HOSYOND_35: ES8311 over I2S standard mode */
static esp_err_t ensure_i2s(void)
{
    if (s_i2s_ready) {
        return ESP_OK;
    }
    i2s_chan_config_t chan = I2S_CHANNEL_DEFAULT_CONFIG(BOARD_I2S_PORT, I2S_ROLE_MASTER);
    chan.dma_desc_num = 8;
    chan.dma_frame_num = 512;
    chan.auto_clear = true;
    esp_err_t err = i2s_new_channel(&chan, NULL, &s_rx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S channel: %s", esp_err_to_name(err));
        s_rx = NULL;
        return err;
    }
    /* ESP32 is the I2S master (MCLK/BCLK/LRCK), codec is slave. Both
     * slots are read (16 kHz 16-bit stereo, as the vendor demo does) and
     * capture_task keeps BOARD_ES8311_ADC_SLOT, so the WAV stays 16 kHz
     * mono like the CrowPanel. */
    i2s_std_config_t std = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(REC_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = BOARD_I2S_MCLK,
            .bclk = BOARD_I2S_BCLK,
            .ws = BOARD_I2S_WS,
            .dout = I2S_GPIO_UNUSED,
            .din = BOARD_I2S_DIN,
        },
    };
    std.clk_cfg.mclk_multiple = I2S_MCLK_MULTIPLE_384;
    std.slot_cfg.slot_mask = I2S_STD_SLOT_BOTH;
    err = i2s_channel_init_std_mode(s_rx, &std);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S std init: %s", esp_err_to_name(err));
        i2s_del_channel(s_rx);
        s_rx = NULL;
        return err;
    }
    s_i2s_ready = true;
    ESP_LOGI(TAG, "I2S%d std RX %u Hz stereo (keep %s), MCLK %u Hz (384x), ES8311 codec, sw gain x%d",
             (int)BOARD_I2S_PORT, (unsigned)REC_SAMPLE_RATE, BOARD_ES8311_ADC_SLOT ? "right" : "left",
             (unsigned)(REC_SAMPLE_RATE * BOARD_I2S_MCLK_MULT), REC_SW_GAIN);
    return ESP_OK;
}

/* Codec registers need MCLK running: call after i2s_channel_enable(). */
static esp_err_t codec_start(const char *why)
{
    uint8_t id1 = 0, id2 = 0;
    esp_err_t err = es8311_min_open(i2c_bus_get(), &id1, &id2);
    if (err == ESP_OK) {
        err = es8311_min_start_adc(BOARD_ES8311_PGA_REG14, BOARD_ES8311_ADC_GAIN16);
    }
    if (err == ESP_OK) {
        es8311_min_dump(why);
    }
    return err;
}
#endif

/* I2S bytes per second as read from the DMA (Hosyond reads both slots). */
#if CONFIG_RECORDER_BOARD_HOSYOND_35
#define I2S_BYTES_PER_SEC (REC_BYTES_PER_SEC * 2)
#else
#define I2S_BYTES_PER_SEC REC_BYTES_PER_SEC
#endif

#if CONFIG_RECORDER_BOARD_HOSYOND_35
/*
 * Mic level diagnostics: once a second while recording, log the level of
 * each I2S slot over the USB serial console (rms with DC removed, peak,
 * DC offset, min..max, first raw samples). Full scale is 32767; a dead
 * input reads rms 0 (or a constant), a quiet room a few tens to a few
 * hundred, speech at table distance well above that.
 */
typedef struct {
    uint64_t sq[2];
    int64_t sum[2];
    int32_t mn[2];
    int32_t mx[2];
    uint32_t n;
    int16_t raw[4];
    uint8_t nraw;
} mic_stats_t;

static mic_stats_t s_stats;
static int64_t s_stats_t0;

static void stats_reset(mic_stats_t *m)
{
    memset(m, 0, sizeof *m);
    for (int c = 0; c < 2; c++) {
        m->mn[c] = 32767;
        m->mx[c] = -32768;
    }
}

/* samples: interleaved when channels == 2, plain mono when 1. */
static void stats_add(mic_stats_t *m, const int16_t *samples, size_t frames, int channels)
{
    for (size_t i = 0; i < frames; i++) {
        for (int c = 0; c < channels; c++) {
            int32_t v = samples[i * channels + c];
            m->sq[c] += (uint64_t)((int64_t)v * v);
            m->sum[c] += v;
            if (v < m->mn[c]) {
                m->mn[c] = v;
            }
            if (v > m->mx[c]) {
                m->mx[c] = v;
            }
            if (m->nraw < 4) {
                m->raw[m->nraw++] = (int16_t)v;
            }
        }
    }
    m->n += (uint32_t)frames;
}

static void stats_log(const char *tag, const mic_stats_t *m, int channels)
{
    if (m->n == 0) {
        ESP_LOGW(TAG, "mic %s: no samples this second", tag);
        return;
    }
    char part[2][96];
    for (int c = 0; c < 2; c++) {
        if (c >= channels) {
            part[c][0] = '\0';
            continue;
        }
        double mean = (double)m->sum[c] / m->n;
        double var = (double)m->sq[c] / m->n - mean * mean;
        unsigned rms = var > 0 ? (unsigned)sqrt(var) : 0;
        int32_t peak = m->mx[c] - (int32_t)mean;
        int32_t neg = (int32_t)mean - m->mn[c];
        if (neg > peak) {
            peak = neg;
        }
        snprintf(part[c], sizeof part[c], "%s rms %5u peak %5ld dc %6d range %6ld..%6ld",
                 channels == 1 ? "M" : (c ? "| R" : "L"), rms, (long)peak, (int)mean, (long)m->mn[c],
                 (long)m->mx[c]);
    }
    ESP_LOGI(TAG, "mic %s: %s %s | raw %04X %04X %04X %04X", tag, part[0], part[1], (uint16_t)m->raw[0],
             (uint16_t)m->raw[1], (uint16_t)m->raw[2], (uint16_t)m->raw[3]);
}

static void stats_tick(const char *tag, int channels)
{
    int64_t now = esp_timer_get_time();
    if (now - s_stats_t0 >= 1000000) {
        stats_log(tag, &s_stats, channels);
        stats_reset(&s_stats);
        s_stats_t0 = now;
    }
}
#endif /* CONFIG_RECORDER_BOARD_HOSYOND_35: mic level diagnostics */

/* Large buffers are static, not on task stacks. The old capture task had
 * a 4 KB buffer in capture_task() plus another 4 KB in discard_settle()
 * called via begin_recording() on an 8 KB stack: pressing Record
 * overflowed the stack and rebooted the board. */
/* 0.7.7: PSRAM. i2s_channel_read copies out of the driver's own internal DMA
 * buffers, so these only need CPU access. */
static EXT_RAM_BSS_ATTR uint8_t s_settle_buf[4096];
static EXT_RAM_BSS_ATTR uint8_t s_cap_buf[4096];
/* 0.7.6: word-aligned internal RAM so SDMMC DMAs straight from it (an unaligned
 * buffer forced a per-sector bounce allocation from internal DMA RAM, which
 * failed while a TLS handshake held it: "SD card write failed"). */
static uint8_t s_chunk[4096] __attribute__((aligned(4)));

/* 0.7.6: write all of buf, retrying short writes briefly. A transient
 * internal-RAM shortage (Wi-Fi/TLS) makes the SD driver return ENOMEM once;
 * waiting a moment and writing the rest keeps the WAV intact. */
static size_t write_all(FILE *fp, const uint8_t *buf, size_t n)
{
    size_t done = 0;
    for (int tries = 0; done < n && tries < 10; tries++) {
        errno = 0;
        size_t w = fwrite(buf + done, 1, n - done, fp);
        done += w;
        if (done < n) {
            ESP_LOGW(TAG, "SD short write %u/%u (errno %d %s, internal free %u, largest DMA %u), retrying",
                     (unsigned)done, (unsigned)n, errno, strerror(errno),
                     (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                     (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA));
            clearerr(fp);
            vTaskDelay(pdMS_TO_TICKS(50));
        }
    }
    return done;
}

static void discard_settle(void)
{
    uint8_t *buf = s_settle_buf;
    uint32_t left = I2S_BYTES_PER_SEC / 2; /* ~500 ms, matching mic_sd_test */
    while (left) {
        size_t got = 0;
        size_t want = left > sizeof s_settle_buf ? sizeof s_settle_buf : left;
        if (i2s_channel_read(s_rx, buf, want, &got, pdMS_TO_TICKS(200)) != ESP_OK || got == 0) {
            break;
        }
        left -= got;
    }
}

static void writer_task(void *arg)
{
    (void)arg;
    uint8_t *chunk = s_chunk;
    const size_t chunk_sz = sizeof s_chunk;
    FILE *fp = NULL;
    char opened[160];
    uint64_t written = 0;

    while (1) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(40));
        int st = atomic_load(&s_state);

        if (!fp) {
            char path[160];
            bool open_now = false;
            xSemaphoreTake(s_mu, portMAX_DELAY);
            if (s_open_req && (st == AUDIO_RECORDING || st == AUDIO_PAUSED || st == AUDIO_SAVING)) {
                strlcpy(path, s_path, sizeof path);
                s_open_req = false;
                open_now = true;
            }
            xSemaphoreGive(s_mu);
            if (open_now) {
                fp = fopen(path, "wb+");
                if (!fp) {
                    int e = errno;
                    ESP_LOGE(TAG, "fopen %s: errno %d (%s)", path, e, strerror(e));
                    char msg[80];
                    snprintf(msg, sizeof msg, "can't create file on SD (%s)", strerror(e));
                    set_err(msg);
                    atomic_store(&s_state, AUDIO_ERROR);
                } else {
                    /* 0.7.6: no newlib stdio buffer. It was malloc'd lazily on
                     * the first fwrite (the 44-byte header) and failing that
                     * allocation under RAM pressure failed the header write.
                     * FATFS already buffers per file; data goes in 4 KB blocks. */
                    setvbuf(fp, NULL, _IONBF, 0);
#if CONFIG_RECORDER_SD_ENCRYPTION
                    uint8_t hdr[REC_CRYPT_HDR_MAX];
                    size_t hlen = 0;
                    rec_crypt_params_t cp = {REC_SAMPLE_RATE, REC_CHANNELS, REC_BITS};
                    int cr = rec_crypt_begin(&s_crypt, rec_pubkey_pem_start,
                                             (size_t)(rec_pubkey_pem_end - rec_pubkey_pem_start),
                                             CONFIG_RECORDER_REC_KEY_ID, &cp, rec_rng, NULL, hdr, sizeof hdr, &hlen);
                    if (cr != 0) {
                        ESP_LOGE(TAG, "encryption setup failed (%d)", cr);
                        fclose(fp);
                        fp = NULL;
                        unlink(path);
                        set_err("Encryption setup failed");
                        atomic_store(&s_state, AUDIO_ERROR);
                    } else if (write_all(fp, hdr, hlen) != hlen) {
                        rec_crypt_abort(&s_crypt);
#else
                    uint8_t hdr[44];
                    build_wav_header(hdr, 0);
                    if (write_all(fp, hdr, 44) != 44) {
#endif
                        ESP_LOGE(TAG, "WAV header write failed: %s", path);
                        fclose(fp);
                        fp = NULL;
                        set_err("SD card write failed");
                        atomic_store(&s_state, AUDIO_ERROR);
                    } else {
                        written = 0;
                        strlcpy(opened, path, sizeof opened);
                        ESP_LOGI(TAG, "recording started: %s", opened);
                    }
                }
            }
        }

        if (fp) {
            size_t n;
            while ((n = ring_read(chunk, chunk_sz)) > 0) {
                if (written > 0xF0000000ULL) {
                    set_err("Recording reached the WAV 4 GB limit");
                    atomic_store(&s_state, AUDIO_ERROR);
                    while (ring_read(chunk, chunk_sz)) {
                    }
                    break;
                }
#if CONFIG_RECORDER_SD_ENCRYPTION
                size_t w = rec_crypt_update(&s_crypt, chunk, n, crypt_write, fp) == 0 ? n : 0;
#else
                size_t w = write_all(fp, chunk, n);
#endif
                if (w != n) {
                    ESP_LOGE(TAG, "fwrite %u/%u bytes: errno %d (%s)", (unsigned)w, (unsigned)n, errno,
                             strerror(errno));
                    set_err("SD card write failed");
                    atomic_store(&s_state, AUDIO_ERROR);
                    while (ring_read(chunk, chunk_sz)) {
                    }
                    break;
                }
                written += w;
                xSemaphoreTake(s_mu, portMAX_DELAY);
                s_bytes = written;
                xSemaphoreGive(s_mu);
                if ((written & ~((64 * 1024) - 1)) != ((written - w) & ~((64 * 1024) - 1))) {
                    fflush(fp);
                }
            }
        }

        st = atomic_load(&s_state);
        if ((st == AUDIO_SAVING || st == AUDIO_ERROR) && ring_used() == 0) {
            if (fp) {
                uint32_t data_bytes = written > 0xFFFFFFFFULL ? 0xFFFFFFFFUL : (uint32_t)written;
#if CONFIG_RECORDER_SD_ENCRYPTION
                /* Final chunk seals the file (also on the error path, so the
                 * audio written so far stays authenticated); the key is wiped. */
                if (rec_crypt_final(&s_crypt, crypt_write, fp) != 0) {
                    ESP_LOGE(TAG, "final encrypted chunk write failed");
                }
#else
                uint8_t hdr[44];
                build_wav_header(hdr, data_bytes);
                if (fseek(fp, 0, SEEK_SET) == 0) {
                    fwrite(hdr, 1, 44, fp);
                }
#endif
                fflush(fp);
                fsync(fileno(fp));
                fclose(fp);
                fp = NULL;
                if (written > 0) {
                    xSemaphoreTake(s_mu, portMAX_DELAY);
                    rec_labels_t started = s_labels;
                    rec_labels_t labels = s_have_save_labels ? s_save_labels : s_labels;
                    bool time_ok = s_time_valid;
                    struct tm start = s_start;
                    xSemaphoreGive(s_mu);
                    char final_path[160];
                    strlcpy(final_path, opened, sizeof final_path);
                    char code_started[32], code_final[32];
                    sanitize_code(code_started, sizeof code_started, started.classroom_code);
                    sanitize_code(code_final, sizeof code_final, labels.classroom_code);
                    if (strcmp(code_started, code_final) != 0) {
                        char rebuilt[160];
                        build_recording_path(rebuilt, sizeof rebuilt, labels.classroom_code, time_ok, &start);
                        if (strcmp(rebuilt, opened) != 0 && rename(opened, rebuilt) == 0) {
                            strlcpy(final_path, rebuilt, sizeof final_path);
                        }
                    }
                    xSemaphoreTake(s_mu, portMAX_DELAY);
                    strlcpy(s_path, final_path, sizeof s_path);
                    xSemaphoreGive(s_mu);
                    write_sidecar(final_path, written);
                    ESP_LOGI(TAG, "finalized %s (%lu bytes, ring overruns %lu, writer stack free %u)",
                             final_path, (unsigned long)data_bytes, (unsigned long)s_overruns,
                             (unsigned)uxTaskGetStackHighWaterMark(NULL));
                }
                written = 0;
            }
            if (st == AUDIO_SAVING) {
                atomic_store(&s_state, AUDIO_IDLE);
            }
        }
    }
}

static void begin_recording(const rec_labels_t *labels)
{
    atomic_store(&s_state, AUDIO_STARTING);
    xSemaphoreTake(s_mu, portMAX_DELAY);
    s_labels = *labels;
    s_have_save_labels = false;
    s_bytes = 0;
    s_nmarkers = 0;
    atomic_store(&s_captured, 0);
    s_level = 0;
#ifdef BOARD_METER_DB_MIN
    s_meter_hold_db = METER_HOLD_RESET_DB;
#endif
    s_err[0] = '\0';
    s_time_valid = false;
    s_overruns = 0;
    s_overrun_logged = false;
    memset(&s_start, 0, sizeof s_start);
    xSemaphoreGive(s_mu);

    board_ctrl_cmd(BOARD_CMD_MIC_PATH);
    board_ctrl_cmd(BOARD_CMD_MUTE_AMP);

    ESP_LOGI(TAG, "start requested: room %s", labels->classroom_code);
    esp_err_t err = ensure_i2s();
    if (err == ESP_OK) {
        err = i2s_channel_enable(s_rx);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "microphone start: %s", esp_err_to_name(err));
        set_err("microphone didn't start");
        atomic_store(&s_state, AUDIO_ERROR);
        return;
    }
    s_rx_on = true;
#if CONFIG_RECORDER_BOARD_HOSYOND_35
    if (codec_start("record start") != ESP_OK) {
        set_err("microphone didn't start");
        rx_stop();
        atomic_store(&s_state, AUDIO_ERROR);
        return;
    }
#endif
    discard_settle();
#if CONFIG_RECORDER_BOARD_HOSYOND_35
    stats_reset(&s_stats);
    s_stats_t0 = esp_timer_get_time();
#endif

    struct tm now;
    bool time_ok = rtc_now(&now);
    struct stat st;
    if (stat("/sd", &st) != 0) {
        ESP_LOGE(TAG, "/sd not mounted: errno %d (%s)", errno, strerror(errno));
        set_err("SD card not available");
        rx_stop();
        atomic_store(&s_state, AUDIO_ERROR);
        return;
    }
    if (mkdir("/sd/rec", 0755) != 0 && errno != EEXIST) {
        int e = errno;
        ESP_LOGE(TAG, "mkdir /sd/rec: errno %d (%s)", e, strerror(e));
        char msg[80];
        snprintf(msg, sizeof msg, "can't create /sd/rec folder (%s)", strerror(e));
        set_err(msg);
        rx_stop();
        atomic_store(&s_state, AUDIO_ERROR);
        return;
    }

    char path[160];
    build_recording_path(path, sizeof path, labels->classroom_code, time_ok, time_ok ? &now : NULL);
    ESP_LOGI(TAG, "file %s (clock %s, pdm_cap stack free %u)", path, time_ok ? "valid" : "not set",
             (unsigned)uxTaskGetStackHighWaterMark(NULL));

    xSemaphoreTake(s_mu, portMAX_DELAY);
    s_time_valid = time_ok;
    if (time_ok) {
        s_start = now;
    }
    strlcpy(s_path, path, sizeof s_path);
    s_open_req = true;
    xSemaphoreGive(s_mu);

    atomic_store(&s_prod, 0);
    atomic_store(&s_cons, 0);
    atomic_store(&s_state, AUDIO_RECORDING);
    xTaskNotifyGive(s_writer_task);
}

#if CONFIG_RECORDER_BOARD_HOSYOND_35
/* Boot mic check: a few seconds after boot, read the mic for up to
 * MIC_SELFTEST_S seconds and log the level once a second over USB serial.
 * Nothing is kept or written. Any UI command (Record) ends it at once. */
#define MIC_SELFTEST_S 10
static void mic_selftest(void)
{
    vTaskDelay(pdMS_TO_TICKS(3000));
    if (uxQueueMessagesWaiting(s_queue) || atomic_load(&s_state) != AUDIO_IDLE) {
        return;
    }
    if (ensure_i2s() != ESP_OK || i2s_channel_enable(s_rx) != ESP_OK) {
        ESP_LOGE(TAG, "mic self-test: I2S did not start");
        return;
    }
    s_rx_on = true;
    if (codec_start("self-test") != ESP_OK) {
        ESP_LOGE(TAG, "mic self-test: codec did not start");
        rx_stop();
        return;
    }
    ESP_LOGI(TAG, "mic self-test: %d s of level readings, nothing is saved (full scale 32767)", MIC_SELFTEST_S);
    discard_settle();
    stats_reset(&s_stats);
    s_stats_t0 = esp_timer_get_time();
    int64_t end = s_stats_t0 + (int64_t)MIC_SELFTEST_S * 1000000;
    while (esp_timer_get_time() < end && !uxQueueMessagesWaiting(s_queue)) {
        size_t got = 0;
        if (i2s_channel_read(s_rx, s_cap_buf, sizeof s_cap_buf, &got, pdMS_TO_TICKS(200)) != ESP_OK || got < 4) {
            continue;
        }
        stats_add(&s_stats, (const int16_t *)s_cap_buf, got / 4, 2);
        stats_tick("self-test", 2);
    }
    rx_stop();
    ESP_LOGI(TAG, "mic self-test done");
}
#endif

static void capture_task(void *arg)
{
    (void)arg;
    uint8_t *buf = s_cap_buf;
#if CONFIG_RECORDER_BOARD_HOSYOND_35
    mic_selftest();
#endif
    while (1) {
        int st = atomic_load(&s_state);
        audio_cmd_t cmd;
        TickType_t wait = (st == AUDIO_RECORDING || st == AUDIO_PAUSED) ? 0 : portMAX_DELAY;
        if (xQueueReceive(s_queue, &cmd, wait) == pdTRUE) {
            st = atomic_load(&s_state);
            if (cmd.op == CMD_START && st != AUDIO_RECORDING && st != AUDIO_PAUSED && st != AUDIO_SAVING &&
                st != AUDIO_STARTING) {
                begin_recording(&cmd.labels);
            } else if (cmd.op == CMD_PAUSE && st == AUDIO_RECORDING) {
                atomic_store(&s_state, AUDIO_PAUSED);
            } else if (cmd.op == CMD_RESUME && st == AUDIO_PAUSED) {
                atomic_store(&s_state, AUDIO_RECORDING);
            } else if (cmd.op == CMD_SAVE && (st == AUDIO_RECORDING || st == AUDIO_PAUSED)) {
                xSemaphoreTake(s_mu, portMAX_DELAY);
                s_save_labels = cmd.labels;
                s_have_save_labels = true;
                xSemaphoreGive(s_mu);
                rx_stop();
                atomic_store(&s_state, AUDIO_SAVING);
                xTaskNotifyGive(s_writer_task);
                for (int i = 0; i < 200; i++) {
                    int now = atomic_load(&s_state);
                    if (now == AUDIO_IDLE || now == AUDIO_ERROR) {
                        break;
                    }
                    vTaskDelay(pdMS_TO_TICKS(50));
                }
                if (atomic_load(&s_state) == AUDIO_SAVING) {
                    set_err("Saving the recording timed out");
                    atomic_store(&s_state, AUDIO_ERROR);
                }
            }
        }

        st = atomic_load(&s_state);
        if (st != AUDIO_RECORDING && st != AUDIO_PAUSED) {
            if (st == AUDIO_ERROR || st == AUDIO_IDLE) {
                rx_stop();
            }
            continue;
        }
        size_t got = 0;
        esp_err_t err = i2s_channel_read(s_rx, buf, sizeof s_cap_buf, &got, pdMS_TO_TICKS(200));
        st = atomic_load(&s_state);
        if (err != ESP_OK || got < 2 || (st != AUDIO_RECORDING && st != AUDIO_PAUSED)) {
            continue;
        }
        if (st == AUDIO_PAUSED) {
            continue;
        }
#if CONFIG_RECORDER_BOARD_HOSYOND_35
        {
            /* Stereo frames in, keep one slot: mono 16-bit out, in place. */
            int16_t *sm = (int16_t *)buf;
            size_t frames = got / 4;
            stats_add(&s_stats, sm, frames, 2);
            stats_tick("rec", 2);
            for (size_t i = 0; i < frames; i++) {
                sm[i] = sm[i * 2 + BOARD_ES8311_ADC_SLOT];
            }
            got = frames * 2;
            if (got < 2) {
                continue;
            }
        }
#endif
        int16_t peak = 0;
        apply_gain(buf, got & ~1u, &peak);
        if (!ring_write(buf, got & ~1u)) {
            s_overruns++;
            if (!s_overrun_logged) {
                s_overrun_logged = true;
                ESP_LOGW(TAG, "ring overrun: SD writes are falling behind (samples dropped)");
            }
        } else {
            atomic_fetch_add(&s_captured, (uint32_t)(got & ~1u));
            if (s_writer_task) {
                xTaskNotifyGive(s_writer_task);
            }
        }
#ifdef BOARD_METER_DB_MIN
        uint8_t level = meter_db_level(peak, &s_meter_hold_db, BOARD_METER_DB_MIN, BOARD_METER_DB_MAX,
                                       BOARD_METER_RELEASE_DB);
#else
        /* CrowPanel: linear peak percentage, unchanged. */
        uint8_t level = (uint8_t)((peak * 100) / 32767);
#endif
        xSemaphoreTake(s_mu, portMAX_DELAY);
        s_level = level;
        xSemaphoreGive(s_mu);
    }
}

static esp_err_t enqueue(const audio_cmd_t *cmd)
{
    if (!s_queue) {
        return ESP_ERR_INVALID_STATE;
    }
    if (xQueueSend(s_queue, cmd, pdMS_TO_TICKS(100)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

esp_err_t audio_init(void)
{
    s_mu = xSemaphoreCreateMutex();
    s_queue = xQueueCreate(4, sizeof(audio_cmd_t));
    const char *where = "PSRAM";
    s_ring = heap_caps_aligned_alloc(64, s_ring_cap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    s_markers = heap_caps_calloc(AUDIO_MAX_MARKERS, sizeof(marker_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    s_marks_copy = heap_caps_calloc(AUDIO_MAX_MARKERS, sizeof(marker_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_markers || !s_marks_copy) {
        ESP_LOGW(TAG, "marker tables unavailable; markers will not be stored");
    }
    if (!s_mu || !s_queue || !s_ring) {
        ESP_LOGE(TAG, "audio buffers failed (mutex %p queue %p ring %p)", s_mu, s_queue, s_ring);
        return ESP_ERR_NO_MEM;
    }
    atomic_store(&s_state, AUDIO_IDLE);
#if CONFIG_RECORDER_BOARD_HOSYOND_35
    /* Boot check of the codec only (no audio is read or kept): start the
     * I2S clocks, read the ES8311 chip ID, program it, stop the clocks. */
    {
        esp_err_t cerr = ensure_i2s();
        if (cerr == ESP_OK) {
            cerr = i2s_channel_enable(s_rx);
        }
        if (cerr == ESP_OK) {
            uint8_t id1 = 0, id2 = 0;
            cerr = es8311_min_open(i2c_bus_get(), &id1, &id2);
            ESP_LOGI(TAG, "ES8311 chip id %02X %02X (expect 83 11)", id1, id2);
            if (cerr == ESP_OK) {
                cerr = es8311_min_start_adc(BOARD_ES8311_PGA_REG14, BOARD_ES8311_ADC_GAIN16);
            }
            if (cerr == ESP_OK) {
                es8311_min_dump("boot");
            }
            i2s_channel_disable(s_rx);
        }
        if (cerr == ESP_OK) {
            ESP_LOGI(TAG, "codec init OK");
        } else {
            ESP_LOGE(TAG, "codec init FAILED: %s (recording will report a mic error)", esp_err_to_name(cerr));
        }
    }
#endif
    if (xTaskCreatePinnedToCore(writer_task, "wav_writer", 10240, NULL, 5, &s_writer_task, 0) != pdPASS) {
        ESP_LOGE(TAG, "wav_writer task create failed");
        return ESP_ERR_NO_MEM;
    }
#if CONFIG_RECORDER_BOARD_HOSYOND_35
    const uint32_t cap_stack = 8192; /* + level log / self-test */
#else
    const uint32_t cap_stack = 7168;
#endif
    if (xTaskCreatePinnedToCore(capture_task, "pdm_cap", cap_stack, NULL, 6, NULL, 0) != pdPASS) {
        ESP_LOGE(TAG, "pdm_cap task create failed");
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "audio tasks ready, ring %u bytes in %s", (unsigned)s_ring_cap, where);
    return ESP_OK;
}

esp_err_t audio_rec_start(const rec_labels_t *labels)
{
    if (!labels || !s_ring) {
        return ESP_ERR_INVALID_STATE;
    }
    audio_cmd_t cmd = {.op = CMD_START, .labels = *labels};
    return enqueue(&cmd);
}

/* Pause/resume flip the state directly: the capture task re-reads the
 * state after every I2S read and drops audio while PAUSED, so this takes
 * effect on the very next buffer (no queue latency). */
bool audio_rec_hold(void)
{
    int expect = AUDIO_RECORDING;
    bool was = atomic_compare_exchange_strong(&s_state, &expect, AUDIO_PAUSED);
    if (was) {
        ESP_LOGI(TAG, "capture held at %lu ms", (unsigned long)((uint64_t)atomic_load(&s_captured) * 1000ULL /
                                                                 REC_BYTES_PER_SEC));
    }
    return was;
}

esp_err_t audio_rec_pause(void)
{
    audio_rec_hold();
    return ESP_OK;
}

esp_err_t audio_rec_resume(void)
{
    int expect = AUDIO_PAUSED;
    atomic_compare_exchange_strong(&s_state, &expect, AUDIO_RECORDING);
    return ESP_OK;
}

bool audio_rec_mark_now(audio_mark_time_t *out)
{
    int st = atomic_load(&s_state);
    if (!out || (st != AUDIO_RECORDING && st != AUDIO_PAUSED)) {
        return false;
    }
    out->offset_ms = (uint32_t)(((uint64_t)atomic_load(&s_captured) * 1000ULL) / REC_BYTES_PER_SEC);
    out->wall = time(NULL);
    struct tm t;
    localtime_r(&out->wall, &t);
    out->wall_valid = t.tm_year + 1900 >= 2024;
    return true;
}

esp_err_t audio_rec_add_marker(const char *type, const audio_mark_time_t *t)
{
    int st = atomic_load(&s_state);
    if (!type || !t || (st != AUDIO_RECORDING && st != AUDIO_PAUSED)) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t err = ESP_OK;
    xSemaphoreTake(s_mu, portMAX_DELAY);
    if (s_markers && s_nmarkers < AUDIO_MAX_MARKERS) {
        strlcpy(s_markers[s_nmarkers].type, type, sizeof s_markers[0].type);
        s_markers[s_nmarkers].t = *t;
        s_nmarkers++;
    } else {
        err = ESP_ERR_NO_MEM;
    }
    int n = s_nmarkers;
    xSemaphoreGive(s_mu);
    ESP_LOGI(TAG, "marker %d: %s at %lu ms", n, type, (unsigned long)t->offset_ms);
    return err;
}

esp_err_t audio_rec_save(const rec_labels_t *labels)
{
    if (!labels) {
        return ESP_ERR_INVALID_ARG;
    }
    audio_cmd_t cmd = {.op = CMD_SAVE, .labels = *labels};
    return enqueue(&cmd);
}

void audio_get_status(audio_status_t *out)
{
    if (!out) {
        return;
    }
    memset(out, 0, sizeof *out);
    out->state = (audio_state_t)atomic_load(&s_state);
    if (!s_mu) {
        return;
    }
    xSemaphoreTake(s_mu, portMAX_DELAY);
    out->time_valid = s_time_valid;
    out->start_local = s_start;
    out->recorded_ms = (uint32_t)((s_bytes * 1000ULL) / REC_BYTES_PER_SEC);
    out->level = (out->state == AUDIO_PAUSED) ? s_level : s_level;
    strlcpy(out->err, s_err, sizeof out->err);
    out->err_seq = s_err_seq;
    strlcpy(out->path, s_path, sizeof out->path);
    xSemaphoreGive(s_mu);
}
