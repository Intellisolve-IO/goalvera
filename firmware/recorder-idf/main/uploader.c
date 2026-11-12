#include "uploader.h"

#include "audio_rec.h"
#include "cJSON.h"
#include "crc32c.h"
#include "device_info.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_rom_md5.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "mbedtls/base64.h"
#include "mbedtls/md.h"
#include "nvs_prefs.h"
#include "pilot_guard.h"
#include "board_pins.h"
#include "sdcard.h"
#include "sdkconfig.h"
#include "status_line.h"
#include "rtc_pcf8563.h"
#include "upload_name.h"
#include "wifi_time.h"
#include <dirent.h>
#include <stdatomic.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

/* Truncating long names/messages into fixed buffers is intended here. */
#pragma GCC diagnostic ignored "-Wformat-truncation"

static const char *TAG = "upload";

#define REC_DIR        "/sd/rec"
#define TOKEN_FILE     "/sd/config/upload_token.txt"
#define NVS_TOKEN_KEY  "up_token"
#define CHUNK          (16 * 1024)
#define RESP_CAP       (8 * 1024)
#define SIDECAR_CAP    (16 * 1024)
#define POLL_MS        5000
#define MAX_SKIP       8
#define STACK_BYTES    10240

typedef struct {
    uint64_t size;
    char md5[28];  /* base64 of 16 bytes = 24 chars */
    char crc[12];  /* base64 of 4 bytes = 8 chars */
} fp_t;

typedef struct {
    char local[168];
    char name[48];     /* remote filename */
    char object[96];   /* rec/<device>/<name> */
    char generation[24];
    fp_t fp;
} part_t;

typedef struct {
    part_t p[2];       /* 0 = .wav, 1 = .json */
    char cls[48];      /* class_code for the cloud file name */
    char room[48];     /* classroom_label as shown on screen */
    char id[56];       /* "12:58 PM recording" / "Oct 7, 2:59 PM recording" / "recording (time not set)" */
    uint64_t total, sent;
    int next_mark;     /* next progress line: 25, 50, 75, 100 */
} job_t;

typedef enum { JOB_OK, JOB_RETRY, JOB_SKIP, JOB_ABORT, JOB_WAIT } job_res_t;

/* Token, skip list and last error live in PSRAM to spare internal RAM. */
typedef struct {
    char token[132];
    char skip[MAX_SKIP][48];
    int nskip;
    char why[80];
} state_t;
static state_t *s;

/* 0.7.6: one HTTPS connection at a time. 0.7.5 let the uploader, the QR link
 * prefetch, the QR tap and the (since removed) school-name fetch each open their own TLS
 * session at once; two handshakes together ran internal RAM out (Wi-Fi
 * "mem fail", esp-aes alloc failures) and starved the SD writer. */
static SemaphoreHandle_t s_net_mu;
/* 0.7.7, retuned for software AES with mbedTLS buffers + lwIP pbufs in PSRAM:
 * a session's internal-RAM cost is the Wi-Fi driver's RX/TX buffers (up to 12
 * dynamic RX x ~1.6 KB = ~20 KB at peak) plus small mbedTLS/esp-tls structs
 * (<2 KB). 32 KB free keeps ~10 KB spare for the SD writer and LVGL; an 8 KB
 * DMA-capable block covers Wi-Fi buffers and SDMMC sector bounce buffers. */
#define NET_MIN_INTERNAL   (32 * 1024)
#define NET_MIN_DMA_BLOCK  (8 * 1024)

bool uploader_net_lock(uint32_t wait_ms)
{
    if (!s_net_mu) {
        return false;
    }
    return xSemaphoreTakeRecursive(s_net_mu, wait_ms == UINT32_MAX ? portMAX_DELAY : pdMS_TO_TICKS(wait_ms)) == pdTRUE;
}

void uploader_net_unlock(void)
{
    if (s_net_mu) {
        xSemaphoreGiveRecursive(s_net_mu);
    }
}

bool uploader_net_mem_ok(void)
{
    size_t fr = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    size_t blk = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
    if (fr < NET_MIN_INTERNAL || blk < NET_MIN_DMA_BLOCK) {
        ESP_LOGW(TAG, "network fetch deferred: internal free %u, largest DMA block %u", (unsigned)fr, (unsigned)blk);
        return false;
    }
    return true;
}
#define s_token (s->token)
#define s_skip (s->skip)
#define s_nskip (s->nskip)
#define s_why (s->why)
static char s_dev[40];
static bool s_wifi_cfg;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static upload_status_t s_st;
static uint8_t *s_buf;
static char *s_resp;
static job_t *s_job;
/* Auto sleep (0.7.0): while held, no new upload attempt starts; the one in
 * progress (if any) runs to its normal end. */
static atomic_bool s_hold;
static atomic_bool s_admin_hold;   /* 0.8.2: serial "uploads hold" / START_HELD */
static atomic_bool s_in_job;
static const char *s_token_src = "not set";

/* ------------------------------------------------------------ helpers */

static void *psram_malloc(size_t n)
{
    void *p = heap_caps_malloc(n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    return p ? p : malloc(n);
}

static void set_status(bool busy, int pct)
{
    taskENTER_CRITICAL(&s_lock);
    s_st.busy = busy;
    s_st.pct = pct;
    taskEXIT_CRITICAL(&s_lock);
}

static bool session_active(void)
{
    audio_status_t a;
    audio_get_status(&a);
    return !(a.state == AUDIO_IDLE || a.state == AUDIO_ERROR);
}

static void why(const char *fmt, const char *arg)
{
    snprintf(s_why, sizeof s_why, fmt, arg ? arg : "");
}

static bool is_skipped(const char *name)
{
    for (int i = 0; i < s_nskip; i++) {
        if (strcmp(s_skip[i], name) == 0) {
            return true;
        }
    }
    return false;
}

static void add_skip(const char *name)
{
    if (!is_skipped(name) && s_nskip < MAX_SKIP) {
        strlcpy(s_skip[s_nskip++], name, sizeof s_skip[0]);
    }
}

static bool ends_with(const char *s, const char *suf)
{
    size_t a = strlen(s), b = strlen(suf);
    return a >= b && strcasecmp(s + a - b, suf) == 0;
}

static void json_path_for(const char *wav, char *out, size_t n)
{
    strlcpy(out, wav, n);
    char *dot = strrchr(out, '.');
    if (dot) {
        strlcpy(dot, ".json", n - (size_t)(dot - out));
    }
}

/* ------------------------------------------------------------ provisioning */

void uploader_init(void)
{
    if (!s_net_mu) {
        s_net_mu = xSemaphoreCreateRecursiveMutex();
    }
    s = heap_caps_calloc(1, sizeof *s, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s) {
        ESP_LOGE(TAG, "no PSRAM for upload state; uploads off");
        return;
    }
    snprintf(s_dev, sizeof s_dev, "%s",
             CONFIG_RECORDER_UPLOAD_DEVICE_ID[0] ? CONFIG_RECORDER_UPLOAD_DEVICE_ID : device_id_str());

    /* One-time install from the SD card: token -> NVS, then wipe the file. */
    FILE *f = sd_mounted() ? fopen(TOKEN_FILE, "r+b") : NULL;
    if (f) {
        char tok[160] = {0};
        size_t n = fread(tok, 1, sizeof tok - 1, f);
        tok[n] = '\0';
        while (n > 0 && (tok[n - 1] == '\n' || tok[n - 1] == '\r' || tok[n - 1] == ' ')) {
            tok[--n] = '\0';
        }
        /* Overwrite the file's bytes before unlinking (FAT keeps data). */
        static const char zeros[160];
        fseek(f, 0, SEEK_SET);
        fwrite(zeros, 1, sizeof zeros, f);
        fflush(f);
        fsync(fileno(f));
        fclose(f);
        unlink(TOKEN_FILE);
        if (!upload_token_ok(tok)) {
            status_error("upload_token.txt was not a valid token (file removed)");
        } else if (prefs_set(NVS_TOKEN_KEY, tok) == ESP_OK) {
            status_set("Upload token installed from SD card (file removed)");
        } else {
            status_error("Could not save the upload token");
        }
        memset(tok, 0, sizeof tok);
    }

    /* 0.8.2: a token compiled into a local build wins over NVS. Such a build
     * is made for one board and one upload service, and an older NVS token
     * (installed for a different service) would otherwise shadow it. Fleet
     * builds compile no token, so for them NVS is still the only source. */
    const char *src = "none";
    if (upload_token_ok(CONFIG_RECORDER_UPLOAD_TOKEN)) {
        strlcpy(s_token, CONFIG_RECORDER_UPLOAD_TOKEN, sizeof s_token);
        src = "firmware build";
    } else if (prefs_get(NVS_TOKEN_KEY, s_token, sizeof s_token) == ESP_OK && upload_token_ok(s_token)) {
        src = "device storage";
    } else {
        s_token[0] = '\0';
    }
    s_token_src = s_token[0] ? src : "not set";
    ESP_LOGI(TAG, "device id \"%s\", upload token: %s", s_dev, s_token_src);
#if CONFIG_RECORDER_UPLOADS_START_HELD
    atomic_store(&s_admin_hold, true);
#endif
}

/* ------------------------------------------------------------ hashing */

static job_res_t fingerprint(const char *path, fp_t *out)
{
    FILE *f = fopen(path, "rb");
    if (!f) {
        why("can't open %s", strrchr(path, '/') ? strrchr(path, '/') + 1 : path);
        return JOB_RETRY;
    }
    md5_context_t md5;
    esp_rom_md5_init(&md5);
    uint32_t crc = 0;
    uint64_t size = 0;
    size_t n;
    job_res_t res = JOB_OK;
    while ((n = fread(s_buf, 1, CHUNK, f)) > 0) {
        esp_rom_md5_update(&md5, s_buf, (uint32_t)n);
        crc = crc32c_update(crc, s_buf, n);
        size += n;
        if ((size & ((1u << 20) - 1)) < CHUNK && session_active()) {
            res = JOB_ABORT;
            break;
        }
    }
    if (res == JOB_OK && ferror(f)) {
        why("SD read error", NULL);
        res = JOB_RETRY;
    }
    fclose(f);
    if (res != JOB_OK) {
        return res;
    }
    uint8_t dg[16];
    esp_rom_md5_final(dg, &md5);
    uint8_t cb[4] = {(uint8_t)(crc >> 24), (uint8_t)(crc >> 16), (uint8_t)(crc >> 8), (uint8_t)crc};
    size_t ol;
    mbedtls_base64_encode((unsigned char *)out->md5, sizeof out->md5, &ol, dg, sizeof dg);
    out->md5[ol] = '\0';
    mbedtls_base64_encode((unsigned char *)out->crc, sizeof out->crc, &ol, cb, sizeof cb);
    out->crc[ol] = '\0';
    out->size = size;
    return JOB_OK;
}

/* ------------------------------------------------------------ HTTP */

/* POST JSON to the upload-api; the reply goes to resp (cap bytes). Returns the HTTP status or -1. */
static int api_post_to(const char *path, const char *body, char *resp, int cap);

/* POST JSON to the upload-api. Returns the HTTP status or -1. */
static int api_post(const char *path, const char *body)
{
    return api_post_to(path, body, s_resp, RESP_CAP);
}

int uploader_api_post(const char *path, const char *json)
{
    char resp[384];
    if (!s || !s_token[0] || !path || !json) {
        return -1;
    }
    return api_post_to(path, json, resp, (int)sizeof resp);
}

static int api_post_to(const char *path, const char *body, char *resp, int cap)
{
    char url[192];
    snprintf(url, sizeof url, "%s%s", CONFIG_RECORDER_UPLOAD_URL, path);
    esp_http_client_config_t cfg = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .timeout_ms = 30000,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .buffer_size = 2048,
        .buffer_size_tx = 1024,
        .disable_auto_redirect = true,
    };
    if (!uploader_net_lock(30000)) {
        ESP_LOGW(TAG, "%s: network busy", path);
        return -1;
    }
    esp_http_client_handle_t c = esp_http_client_init(&cfg);
    if (!c) {
        uploader_net_unlock();
        return -1;
    }
    char auth[144];
    snprintf(auth, sizeof auth, "Bearer %s", s_token);
    esp_http_client_set_header(c, "Authorization", auth);
    esp_http_client_set_header(c, "Content-Type", "application/json");
    int status = -1;
    int len = (int)strlen(body);
    resp[0] = '\0';
    if (esp_http_client_open(c, len) == ESP_OK) {
        if (esp_http_client_write(c, body, len) == len && esp_http_client_fetch_headers(c) >= 0) {
            status = esp_http_client_get_status_code(c);
            int got = 0, r;
            while (got < cap - 1 && (r = esp_http_client_read(c, resp + got, cap - 1 - got)) > 0) {
                got += r;
            }
            resp[got] = '\0';
        }
    }
    esp_http_client_close(c);
    esp_http_client_cleanup(c);
    uploader_net_unlock();
    memset(auth, 0, sizeof auth);
    return status;
}

static const char *json_str(const cJSON *o, const char *k)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    return cJSON_IsString(v) ? v->valuestring : NULL;
}

static void progress(job_t *j, size_t n)
{
    j->sent += n;
    int pct = j->total ? (int)((j->sent * 100ULL) / j->total) : 100;
    set_status(true, pct);
    while (j->next_mark <= 100 && pct >= j->next_mark) {
        char line[32];
        snprintf(line, sizeof line, "%d%% complete", j->next_mark);
        status_set(line);
        j->next_mark += 25;
    }
}

/* PUT one file to its signed URL. */
static job_res_t put_file(job_t *j, part_t *p, const char *upload_url, const cJSON *headers)
{
    esp_http_client_config_t cfg = {
        .url = upload_url,
        .method = HTTP_METHOD_PUT,
        .timeout_ms = 60000,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .buffer_size = 2048,
        .buffer_size_tx = 4096, /* request line carries the ~1.5 KB signed query */
        .disable_auto_redirect = true,
    };
    esp_http_client_handle_t c = esp_http_client_init(&cfg);
    if (!c) {
        why("no memory for the upload connection", NULL);
        return JOB_RETRY;
    }
    const cJSON *h;
    cJSON_ArrayForEach(h, headers)
    {
        if (cJSON_IsString(h) && h->string) {
            esp_http_client_set_header(c, h->string, h->valuestring);
        }
    }
    FILE *f = fopen(p->local, "rb");
    job_res_t res = JOB_OK;
    if (!f) {
        why("SD file disappeared", NULL);
        res = JOB_RETRY;
    } else if (esp_http_client_open(c, (int)p->fp.size) != ESP_OK) {
        why("can't reach storage (connection failed)", NULL);
        res = JOB_RETRY;
    } else {
        uint64_t left = p->fp.size;
        while (left > 0) {
            size_t want = left < CHUNK ? (size_t)left : CHUNK;
            size_t n = fread(s_buf, 1, want, f);
            if (n != want) {
                why("SD read error", NULL);
                res = JOB_RETRY;
                break;
            }
            size_t off = 0;
            while (off < n) {
                int w = esp_http_client_write(c, (const char *)s_buf + off, (int)(n - off));
                if (w <= 0) {
                    break;
                }
                off += (size_t)w;
            }
            if (off != n) {
                why("connection lost during upload", NULL);
                res = JOB_RETRY;
                break;
            }
            left -= n;
            progress(j, n);
            if (session_active()) {
                res = JOB_ABORT;
                break;
            }
        }
        if (res == JOB_OK) {
            if (esp_http_client_fetch_headers(c) < 0) {
                why("no answer from storage", NULL);
                res = JOB_RETRY;
            } else {
                int st = esp_http_client_get_status_code(c);
                int got = esp_http_client_read(c, s_resp, 511);
                s_resp[got > 0 ? got : 0] = '\0';
                if (st == 412) {
                    ESP_LOGW(TAG, "%s already stored (412); confirming", p->name);
                } else if (st != 200 && st != 201) {
                    char code[48] = "";
                    char *a = strstr(s_resp, "<Code>");
                    char *b = a ? strstr(a, "</Code>") : NULL;
                    if (a && b && b - a - 6 < (int)sizeof code) {
                        memcpy(code, a + 6, (size_t)(b - a - 6));
                        code[b - a - 6] = '\0';
                    }
                    ESP_LOGE(TAG, "PUT %s: HTTP %d %s", p->name, st, code);
                    snprintf(s_why, sizeof s_why, "storage refused the file (HTTP %d %s)", st, code);
                    res = JOB_RETRY;
                }
            }
        }
    }
    if (f) {
        fclose(f);
    }
    esp_http_client_close(c);
    esp_http_client_cleanup(c);
    return res;
}

static job_res_t request_and_put(job_t *j, part_t *p)
{
    char body[320];
    snprintf(body, sizeof body,
             "{\"device_id\":\"%s\",\"filename\":\"%s\",\"size\":%llu,\"md5\":\"%s\",\"crc32c\":\"%s\"}", s_dev,
             p->name, (unsigned long long)p->fp.size, p->fp.md5, p->fp.crc);
    int st = api_post("/v1/uploads", body);
    if (st < 0) {
        why("can't reach the upload service", NULL);
        return JOB_RETRY;
    }
    cJSON *r = cJSON_Parse(s_resp);
    const char *err = json_str(r, "error");
    job_res_t res = JOB_OK;
    if (st == 401) {
        why("device not authorized. Check upload token", NULL);
        res = JOB_RETRY;
    } else if (st == 400) {
        why("service rejected the file: %s", err ? err : "bad request");
        res = JOB_SKIP;
    } else if (st != 200) {
        snprintf(s_why, sizeof s_why, "upload service error (HTTP %d)", st);
        res = JOB_RETRY;
    } else {
        const char *obj = json_str(r, "object");
        const char *url = json_str(r, "upload_url");
        const cJSON *hdrs = cJSON_GetObjectItemCaseSensitive(r, "headers");
        if (!obj || !url || !cJSON_IsObject(hdrs)) {
            why("unexpected answer from the upload service", NULL);
            res = JOB_RETRY;
        } else {
            strlcpy(p->object, obj, sizeof p->object);
            uploader_net_lock(UINT32_MAX);   /* one TLS session at a time (0.7.6) */
            res = put_file(j, p, url, hdrs);
            uploader_net_unlock();
        }
    }
    cJSON_Delete(r);
    return res;
}

/* Confirm one object; OK only if verified, receipt HMAC matches, and the
 * stored size/MD5/CRC32C equal what we hashed from the SD card. */
static job_res_t confirm(part_t *p)
{
    char body[320];
    snprintf(body, sizeof body,
             "{\"device_id\":\"%s\",\"object\":\"%s\",\"size\":%llu,\"md5\":\"%s\",\"crc32c\":\"%s\"}", s_dev,
             p->object, (unsigned long long)p->fp.size, p->fp.md5, p->fp.crc);
    int st = api_post("/v1/confirm", body);
    if (st < 0) {
        why("can't reach the upload service to confirm", NULL);
        return JOB_RETRY;
    }
    cJSON *r = cJSON_Parse(s_resp);
    job_res_t res = JOB_RETRY;
    const cJSON *verified = cJSON_GetObjectItemCaseSensitive(r, "verified");
    const cJSON *stored = cJSON_GetObjectItemCaseSensitive(r, "stored");
    const char *receipt = json_str(r, "receipt");
    const char *s_md5 = json_str(stored, "md5");
    const char *s_crc = json_str(stored, "crc32c");
    const char *s_gen = json_str(stored, "generation");
    const cJSON *s_size = cJSON_GetObjectItemCaseSensitive(stored, "size");
    if (st != 200 || !cJSON_IsTrue(verified) || !receipt || !s_md5 || !s_crc || !s_gen ||
        !cJSON_IsNumber(s_size)) {
        snprintf(s_why, sizeof s_why, "cloud copy not verified (HTTP %d)", st);
        ESP_LOGE(TAG, "confirm %s: HTTP %d, verified=%d", p->name, st, cJSON_IsTrue(verified));
    } else {
        uint64_t sz = (uint64_t)s_size->valuedouble;
        bool match = sz == p->fp.size && strcmp(s_md5, p->fp.md5) == 0 && strcmp(s_crc, p->fp.crc) == 0;
        char msg[256];
        upload_receipt_msg(msg, sizeof msg, p->object, s_gen, sz, s_md5, s_crc);
        uint8_t mac[32];
        char hex[65];
        const mbedtls_md_info_t *md = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
        bool receipt_ok = false;
        if (md && mbedtls_md_hmac(md, (const unsigned char *)s_token, strlen(s_token),
                                  (const unsigned char *)msg, strlen(msg), mac) == 0) {
            for (int i = 0; i < 32; i++) {
                snprintf(hex + i * 2, 3, "%02x", mac[i]);
            }
            receipt_ok = upload_str_eq_ct(hex, receipt);
        }
        if (match && receipt_ok) {
            strlcpy(p->generation, s_gen, sizeof p->generation);
            res = JOB_OK;
            ESP_LOGI(TAG, "confirmed %s gen %s size %llu md5 %s crc32c %s", p->object, s_gen,
                     (unsigned long long)sz, s_md5, s_crc);
        } else {
            snprintf(s_why, sizeof s_why, "%s", !match ? "cloud copy differs from SD copy" : "receipt did not match");
            ESP_LOGE(TAG, "confirm %s: match=%d receipt_ok=%d", p->name, match, receipt_ok);
            res = match ? JOB_RETRY : JOB_SKIP;
        }
    }
    cJSON_Delete(r);
    return res;
}

/* ------------------------------------------------------------ one recording */

static void read_meta(const char *json_path, char *cls, size_t cn, char *room, size_t rn)
{
    if (cls && cn) {
        cls[0] = '\0';
    }
    if (room && rn) {
        room[0] = '\0';
    }
    FILE *f = fopen(json_path, "rb");
    if (!f) {
        return;
    }
    char *buf = heap_caps_malloc(SIDECAR_CAP, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (buf) {
        size_t len = fread(buf, 1, SIDECAR_CAP - 1, f);
        buf[len] = '\0';
        cJSON *j = cJSON_Parse(buf);
        const char *c = json_str(j, "class_code");
        if (!c || !c[0]) {
            c = json_str(j, "classroom_label");
        }
        if (c && cls && cn) {
            strlcpy(cls, c, cn);
        }
        const char *r = json_str(j, "classroom_label");
        if (r && room && rn) {
            strlcpy(room, r, rn);
        }
        cJSON_Delete(j);
        heap_caps_free(buf);
    }
    fclose(f);
}

/* Month abbrevs for "Oct 7, 2:59 PM" (same names as the home-screen clock). */
static const char *UP_MON[] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun",
                               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};

/* Parse YYYYMMDD_HHMMSS at the start of a recording basename into local tm. */
static bool parse_stamp(const char *base, struct tm *out)
{
    if (!base || !out) {
        return false;
    }
    for (int i = 0; i < 15; i++) {
        char c = base[i];
        if (i == 8 ? c != '_' : (c < '0' || c > '9')) {
            return false;
        }
    }
    memset(out, 0, sizeof *out);
    int y = (base[0] - '0') * 1000 + (base[1] - '0') * 100 + (base[2] - '0') * 10 + (base[3] - '0');
    int mo = (base[4] - '0') * 10 + (base[5] - '0');
    int d = (base[6] - '0') * 10 + (base[7] - '0');
    int h = (base[9] - '0') * 10 + (base[10] - '0');
    int mi = (base[11] - '0') * 10 + (base[12] - '0');
    int s = (base[13] - '0') * 10 + (base[14] - '0');
    if (y < 2020 || mo < 1 || mo > 12 || d < 1 || d > 31 || h > 23 || mi > 59 || s > 59) {
        return false;
    }
    out->tm_year = y - 1900;
    out->tm_mon = mo - 1;
    out->tm_mday = d;
    out->tm_hour = h;
    out->tm_min = mi;
    out->tm_sec = s;
    out->tm_isdst = -1;
    return true;
}

/* Fill j->id: "12:58 PM recording", "Oct 7, 2:59 PM recording", or
 * "recording (time not set)". Prefer the file-name stamp; else sidecar start_time. */
static void fill_rec_id(job_t *j, const char *base)
{
    j->id[0] = '\0';
    if (base && strncmp(base, "NOTIME_", 7) == 0) {
        strlcpy(j->id, "recording (time not set)", sizeof j->id);
        return;
    }
    struct tm stamp;
    bool ok = parse_stamp(base, &stamp);
    if (!ok) {
        /* Sidecar: "2026-10-08T12:58:00-0400" (local America/New_York). */
        FILE *f = fopen(j->p[1].local, "rb");
        if (f) {
            char *buf = heap_caps_malloc(SIDECAR_CAP, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
            if (buf) {
                size_t len = fread(buf, 1, SIDECAR_CAP - 1, f);
                buf[len] = '\0';
                cJSON *root = cJSON_Parse(buf);
                const char *ts = json_str(root, "start_time");
                if (ts && strlen(ts) >= 19) {
                    int y, mo, d, h, mi, s;
                    if (sscanf(ts, "%d-%d-%dT%d:%d:%d", &y, &mo, &d, &h, &mi, &s) == 6 &&
                        y >= 2020 && mo >= 1 && mo <= 12 && d >= 1 && d <= 31 &&
                        h <= 23 && mi <= 59 && s <= 59) {
                        memset(&stamp, 0, sizeof stamp);
                        stamp.tm_year = y - 1900;
                        stamp.tm_mon = mo - 1;
                        stamp.tm_mday = d;
                        stamp.tm_hour = h;
                        stamp.tm_min = mi;
                        stamp.tm_sec = s;
                        stamp.tm_isdst = -1;
                        ok = true;
                    }
                }
                cJSON_Delete(root);
                heap_caps_free(buf);
            }
            fclose(f);
        }
    }
    if (!ok) {
        strlcpy(j->id, "recording (time not set)", sizeof j->id);
        return;
    }
    char when[40];
    char amp[24];
    clock_format_ampm(&stamp, false, amp, sizeof amp);
    time_t now = time(NULL);
    struct tm today;
    localtime_r(&now, &today);
    bool same_day = status_clock_valid() && today.tm_year == stamp.tm_year &&
                    today.tm_mon == stamp.tm_mon && today.tm_mday == stamp.tm_mday;
    if (same_day) {
        snprintf(when, sizeof when, "%s", amp);
    } else {
        snprintf(when, sizeof when, "%s %d, %s", UP_MON[stamp.tm_mon], stamp.tm_mday, amp);
    }
    snprintf(j->id, sizeof j->id, "%s recording", when);
}

/* NOTIME_<secs>_<code>.wav: once the clock is set, give it a real stamp so
 * its cloud name is stable across retries. */
static bool stamp_notime(char *wav, size_t n)
{
    if (!status_clock_valid()) {
        return false;
    }
    const char *base = strrchr(wav, '/') + 1;
    const char *rest = strchr(base + 7, '_');
    time_t now = time(NULL);
    struct tm t;
    localtime_r(&now, &t);
    char nw[168], oj[168], nj[168];
    snprintf(nw, sizeof nw, REC_DIR "/%04d%02d%02d_%02d%02d%02d%s", t.tm_year + 1900, t.tm_mon + 1, t.tm_mday,
             t.tm_hour, t.tm_min, t.tm_sec, rest ? rest : "_rec.wav");
    json_path_for(wav, oj, sizeof oj);
    json_path_for(nw, nj, sizeof nj);
    if (rename(wav, nw) != 0) {
        return false;
    }
    rename(oj, nj);
    ESP_LOGI(TAG, "renamed %s -> %s (clock now set)", base, strrchr(nw, '/') + 1);
    strlcpy(wav, nw, n);
    return true;
}

static job_res_t run_job(const char *wav_in)
{
    job_t *j = s_job;
    memset(j, 0, sizeof *j);
    strlcpy(j->p[0].local, wav_in, sizeof j->p[0].local);
    if (strncmp(j->p[0].local, REC_DIR "/", sizeof REC_DIR) != 0) {
        return JOB_SKIP; /* not a recording path; never dereference a missing '/' */
    }
    const char *base = strrchr(j->p[0].local, '/') + 1;
    if (strncmp(base, "NOTIME_", 7) == 0 && !stamp_notime(j->p[0].local, sizeof j->p[0].local)) {
        return JOB_WAIT;
    }
    base = strrchr(j->p[0].local, '/') + 1;
    json_path_for(j->p[0].local, j->p[1].local, sizeof j->p[1].local);
    read_meta(j->p[1].local, j->cls, sizeof j->cls, j->room, sizeof j->room);
    fill_rec_id(j, base);

    char stem[64], remote[48];
    strlcpy(stem, base, sizeof stem);
    char *dot = strrchr(stem, '.');
    if (dot) {
        *dot = '\0';
    }
    if (!upload_remote_stem(stem, j->cls, remote, sizeof remote)) {
        why("file name has no date", NULL);
        return JOB_SKIP;
    }
    snprintf(j->p[0].name, sizeof j->p[0].name, "%s.wav", remote);
    snprintf(j->p[1].name, sizeof j->p[1].name, "%s.json", remote);

    char line[STATUS_MSG_LEN];
    /* NOTIME keeps the fixed "Uploading recording (time not set)" wording. */
    if (j->room[0] && strcmp(j->id, "recording (time not set)") != 0) {
        snprintf(line, sizeof line, "Uploading %s (%s)", j->id, j->room);
    } else {
        snprintf(line, sizeof line, "Uploading %s", j->id);
    }
    status_set(line);
    ESP_LOGI(TAG, "%s -> rec/%s/%s (+ .json); internal free %u", base, s_dev, j->p[0].name,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    set_status(true, 0);

    for (int i = 0; i < 2; i++) {
        job_res_t r = fingerprint(j->p[i].local, &j->p[i].fp);
        if (r != JOB_OK) {
            return r;
        }
        j->total += j->p[i].fp.size;
    }
    ESP_LOGI(TAG, "wav %llu bytes md5 %s crc32c %s", (unsigned long long)j->p[0].fp.size, j->p[0].fp.md5,
             j->p[0].fp.crc);
    j->next_mark = 25;
    for (int i = 0; i < 2; i++) {
        job_res_t r = request_and_put(j, &j->p[i]);
        if (r != JOB_OK) {
            return r;
        }
    }
    for (int i = 0; i < 2; i++) {
        job_res_t r = confirm(&j->p[i]);
        if (r != JOB_OK) {
            return r;
        }
    }
    snprintf(line, sizeof line, "Upload receipt verified: %s", j->id);
    status_set(line);

    /* Delete only what we verified: the file must still be the same size. */
    for (int i = 0; i < 2; i++) {
        struct stat stt;
        if (stat(j->p[i].local, &stt) != 0 || (uint64_t)stt.st_size != j->p[i].fp.size) {
            why("SD file changed after upload; kept", NULL);
            return JOB_SKIP;
        }
    }
    /* Option B (plaintext SD): zero the bytes before unlinking, since FAT keeps
     * deleted clusters readable. The verified cloud copy is the only copy. */
    bool ok = pg_wipe_file(j->p[0].local) == 0;
    ok &= pg_wipe_file(j->p[1].local) == 0;
    if (!ok) {
        status_error("Uploaded, but deleting the SD copy failed");
        return JOB_SKIP;
    }
    status_set("SD copy deleted");
    ESP_LOGI(TAG, "deleted %s (+ .json); internal free %u (min ever %u)", base,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL));
    return JOB_OK;
}

/* Oldest finished recording (has a .json sidecar), and how many are waiting. */
static int64_t s_scan_audio_s, s_scan_oldest_age_s;

static int scan(char *out, size_t n)
{
    out[0] = '\0'; /* cleared first: a card with no rec folder must not leave stale bytes */
    s_scan_audio_s = 0;
    s_scan_oldest_age_s = -1;
    const int64_t byte_rate = (int64_t)REC_SAMPLE_RATE * REC_CHANNELS * REC_BITS / 8;
    time_t now = time(NULL);
    struct tm nowtm;
    localtime_r(&now, &nowtm);
    bool clock_ok = nowtm.tm_year + 1900 >= 2024;
    DIR *d = opendir(REC_DIR);
    if (!d) {
        return 0;
    }
    int count = 0;
    struct dirent *e;
    char path[168], jp[168];
    while ((e = readdir(d)) != NULL) {
        if (!ends_with(e->d_name, ".wav") || strlen(e->d_name) > 60) {
            continue;
        }
        snprintf(path, sizeof path, REC_DIR "/%s", e->d_name);
        json_path_for(path, jp, sizeof jp);
        if (access(jp, F_OK) != 0) {
            continue; /* no sidecar yet (in progress, or never finalized) */
        }
        count++;
        struct stat st;
        if (stat(path, &st) == 0 && st.st_size > 44) {
            s_scan_audio_s += ((int64_t)st.st_size - 44) / byte_rate;
        }
        struct tm stamp;
        if (clock_ok && parse_stamp(e->d_name, &stamp)) {
            stamp.tm_isdst = -1;
            int64_t age = (int64_t)difftime(now, mktime(&stamp));
            if (age > s_scan_oldest_age_s) {
                s_scan_oldest_age_s = age;
            }
        }
        if (is_skipped(e->d_name)) {
            continue;
        }
        if (!out[0] || strcmp(path, out) < 0) {
            strlcpy(out, path, n);
        }
    }
    closedir(d);
    return count;
}

static void uploader_task(void *arg)
{
    (void)arg;
    static const uint32_t backoff_min[] = {1, 2, 5, 10, 15};
    int fails = 0;
    int idle_polls = 0;
    TickType_t next_try = 0;
    char path[168] = "";
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(POLL_MS));
        if (session_active() || !sd_mounted()) {
            idle_polls = 0;
            continue;
        }
        int waiting = scan(path, sizeof path);
        taskENTER_CRITICAL(&s_lock);
        s_st.waiting = waiting;
        s_st.backlog_audio_s = s_scan_audio_s;
        s_st.oldest_age_s = s_scan_oldest_age_s;
        s_st.backlog = pg_backlog_level(s_scan_audio_s, s_scan_oldest_age_s);
        taskEXIT_CRITICAL(&s_lock);
        wifi_status_t w;
        wifi_get_status(&w);
        /* Wait 10 s after a session ends so the sidecar is fully written. */
        if (++idle_polls < 2 || !w.connected || !path[0] ||
            (next_try && (int32_t)(xTaskGetTickCount() - next_try) < 0)) {
            continue;
        }
        atomic_store(&s_in_job, true);
        if (atomic_load(&s_hold) || atomic_load(&s_admin_hold)) {
            atomic_store(&s_in_job, false);
            continue;
        }
        esp_wifi_set_ps(WIFI_PS_NONE);
        s_why[0] = '\0';
        job_res_t r = run_job(path);
        esp_wifi_set_ps(WIFI_PS_MIN_MODEM);
        atomic_store(&s_in_job, false);
        set_status(false, 0);
        char line[STATUS_MSG_LEN];
        switch (r) {
        case JOB_OK:
            fails = 0;
            next_try = 0;
            break;
        case JOB_ABORT:
            status_set("Upload paused: recording started (file kept)");
            idle_polls = 0;
            break;
        case JOB_WAIT:
            break;
        case JOB_SKIP:
            add_skip(strrchr(path, '/') + 1);
            if (s_job && s_job->id[0]) {
                snprintf(line, sizeof line, "Upload stopped: %s. Kept on SD (%s)", s_why, s_job->id);
            } else {
                snprintf(line, sizeof line, "Upload stopped: %s. Kept on SD", s_why);
            }
            status_error(line);
            break;
        case JOB_RETRY:
        default: {
            uint32_t mins = backoff_min[fails < 4 ? fails : 4];
            fails++;
            next_try = xTaskGetTickCount() + pdMS_TO_TICKS(mins * 60000UL);
            if (s_job && s_job->id[0]) {
                snprintf(line, sizeof line, "Upload failed: %s. Kept on SD, retry in %lu min (%s)",
                         s_why, (unsigned long)mins, s_job->id);
            } else {
                snprintf(line, sizeof line, "Upload failed: %s. Kept on SD, retry in %lu min", s_why,
                         (unsigned long)mins);
            }
            status_error(line);
            break;
        }
        }
        taskENTER_CRITICAL(&s_lock);
        s_st.retry_wait = r == JOB_RETRY;
        taskEXIT_CRITICAL(&s_lock);
        ESP_LOGI(TAG, "job result %d; internal free %u (min ever %u), stack free %u", (int)r,
                 (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                 (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
                 (unsigned)uxTaskGetStackHighWaterMark(NULL));
    }
}

void uploader_start(void)
{
    if (!s) {
        return;
    }
    wifi_status_t w;
    wifi_get_status(&w);
    s_wifi_cfg = w.configured;
    if (!s_wifi_cfg) {
        ESP_LOGI(TAG, "uploads off: Wi-Fi not configured");
        return;
    }
    if (!s_token[0]) {
        s_st.no_token = true;
        status_error("Uploads off: no device token (see upload_token.txt)");
        return;
    }
    /* cJSON (config + API answers) allocates from PSRAM. */
    cJSON_Hooks hooks = {.malloc_fn = psram_malloc, .free_fn = free};
    cJSON_InitHooks(&hooks);
    s_buf = heap_caps_malloc(CHUNK, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    s_resp = heap_caps_malloc(RESP_CAP, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    s_job = heap_caps_calloc(1, sizeof(job_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_buf || !s_resp || !s_job) {
        status_error("Uploads off: out of memory");
        return;
    }
    /* Stack in PSRAM: this task never writes flash (NVS is written only in
     * uploader_init at boot), so it is safe with XIP from PSRAM. */
    if (xTaskCreatePinnedToCoreWithCaps(uploader_task, "uploader", STACK_BYTES, NULL, 2, NULL, 0,
                                        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS) {
        status_error("Uploads off: task could not start");
        return;
    }
    s_st.enabled = true;
    ESP_LOGI(TAG, "uploader ready -> %s", CONFIG_RECORDER_UPLOAD_URL);
#if CONFIG_RECORDER_UPLOADS_START_HELD
    atomic_store(&s_admin_hold, true);
    ESP_LOGW(TAG, "uploads HELD at boot (local build option): send \"queue\" to list, "
                  "\"queue wipe YES\" to clear, \"uploads resume\" to start uploading");
    status_set("Uploads on hold (serial: uploads resume)");
#endif
}

void uploader_get_status(upload_status_t *out)
{
    taskENTER_CRITICAL(&s_lock);
    *out = s_st;
    taskEXIT_CRITICAL(&s_lock);
}

void uploader_hold(bool hold)
{
    atomic_store(&s_hold, hold);
    ESP_LOGI(TAG, "uploads %s", hold ? "held (going to sleep)" : "resumed");
}

bool uploader_idle(void)
{
    return !atomic_load(&s_in_job);
}

/* ------------------------------------------------------------ serial admin (0.8.2) */

void uploader_admin_hold(bool hold)
{
    atomic_store(&s_admin_hold, hold);
    ESP_LOGI(TAG, "uploads %s (serial)", hold ? "held" : "resumed");
    status_set(hold ? "Uploads on hold (serial)" : "Uploads resumed (serial)");
}

bool uploader_admin_held(void)
{
    return atomic_load(&s_admin_hold);
}

void uploader_print_status(void)
{
    upload_status_t u;
    uploader_get_status(&u);
    const char *host = CONFIG_RECORDER_UPLOAD_URL;
    if (strncmp(host, "https://", 8) == 0) {
        host += 8;
    }
    printf("upload: url host %s, device id \"%s\", token %s, %s%s%s, waiting %d (%lld min), backlog level %d%s%s\n",
           host, s_dev, s_token_src, u.enabled ? "enabled" : "off", atomic_load(&s_admin_hold) ? ", ADMIN HOLD" : "",
           atomic_load(&s_hold) ? ", sleep hold" : "", u.waiting, (long long)(u.backlog_audio_s / 60), u.backlog,
           (s && s_why[0]) ? ", last error: " : "", (s && s_why[0]) ? s_why : "");
}

static int queue_walk(bool wipe)
{
    const int64_t byte_rate = (int64_t)REC_SAMPLE_RATE * REC_CHANNELS * REC_BITS / 8;
    DIR *d = opendir(REC_DIR);
    if (!d) {
        printf("queue: %s not found (no recordings)\n", REC_DIR);
        return 0;
    }
    int files = 0, recs = 0, failed = 0;
    int64_t bytes = 0, audio_s = 0;
    char first[24] = "", last[24] = "";
    char path[300];
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        if (e->d_name[0] == '.') {
            continue;
        }
        snprintf(path, sizeof path, REC_DIR "/%s", e->d_name);
        struct stat st;
        if (stat(path, &st) != 0 || !S_ISREG(st.st_mode)) {
            continue;
        }
        files++;
        bytes += st.st_size;
        bool wav = ends_with(e->d_name, ".wav");
        int64_t secs = (wav && st.st_size > 44) ? ((int64_t)st.st_size - 44) / byte_rate : 0;
        if (wav) {
            recs++;
            audio_s += secs;
            struct tm stamp;
            if (parse_stamp(e->d_name, &stamp)) {
                char ymd[24];
                strftime(ymd, sizeof ymd, "%Y-%m-%d %H:%M", &stamp);
                if (!first[0] || strcmp(ymd, first) < 0) {
                    strlcpy(first, ymd, sizeof first);
                }
                if (!last[0] || strcmp(ymd, last) > 0) {
                    strlcpy(last, ymd, sizeof last);
                }
            }
        }
        if (wipe) {
            int rc = pg_wipe_file(path);
            if (rc != 0) {
                rc = unlink(path);
            }
            if (rc != 0) {
                failed++;
            }
            printf("queue wipe: %s %s (%lld bytes)\n", rc == 0 ? "removed" : "FAILED", e->d_name,
                   (long long)st.st_size);
        } else {
            printf("queue: %-44s %10lld bytes%s", e->d_name, (long long)st.st_size, wav ? "" : "\n");
            if (wav) {
                printf("  ~%lld min %02lld s\n", (long long)(secs / 60), (long long)(secs % 60));
            }
        }
        fflush(stdout);
    }
    closedir(d);
    printf("queue %s: %d files, %d recordings, ~%lld min audio, %lld bytes, dates %s .. %s%s\n",
           wipe ? "wipe done" : "total", files, recs, (long long)(audio_s / 60), (long long)bytes,
           first[0] ? first : "?", last[0] ? last : "?", failed ? " (SOME FILES FAILED)" : "");
    ESP_LOGW(TAG, "queue %s: %d files, %d recordings, ~%lld min", wipe ? "wiped" : "listed", files, recs,
             (long long)(audio_s / 60));
    return wipe ? files - failed : recs;
}

int uploader_queue_print(void)
{
    if (!sd_mounted()) {
        printf("queue: SD card not mounted\n");
        return -1;
    }
    return queue_walk(false);
}

int uploader_queue_wipe(void)
{
    if (!sd_mounted()) {
        printf("queue wipe: refused, SD card not mounted\n");
        return -1;
    }
    if (session_active()) {
        printf("queue wipe: refused, a recording session is active (end it first)\n");
        return -1;
    }
    atomic_store(&s_admin_hold, true);
    for (int i = 0; i < 600 && atomic_load(&s_in_job); i++) {   /* let a running upload end (<= 60 s) */
        vTaskDelay(pdMS_TO_TICKS(100));
    }
    if (atomic_load(&s_in_job)) {
        printf("queue wipe: refused, an upload is still running (uploads now held; try again)\n");
        return -1;
    }
    if (session_active()) {
        printf("queue wipe: refused, a recording started\n");
        return -1;
    }
    status_set("Clearing recordings from the SD card (serial)");
    int n = queue_walk(true);
    status_set("SD recordings cleared (serial). Uploads on hold");
    return n;
}

/* ------------------------------------------------------------ Teacher Notes QR (0.7.2) */

int uploader_media_token(const char *local_wav, const char *class_code, char *url, size_t url_cap,
                         int *ttl_s, char *err, size_t err_cap)
{
    err[0] = '\0';
    url[0] = '\0';
    if (!s || !s_token[0]) {
        strlcpy(err, "No upload key on this recorder", err_cap);
        return -1;
    }
    const char *base = strrchr(local_wav, '/');
    base = base ? base + 1 : local_wav;
    char stem[64], code[24], remote[48];
    strlcpy(stem, base, sizeof stem);
    char *dot = strrchr(stem, '.');
    if (dot) {
        *dot = '\0';
    }
    upload_class_code(code, sizeof code, class_code ? class_code : "");
    if (!upload_remote_stem(stem, code, remote, sizeof remote)) {
        strlcpy(err, "Clock not set yet. Connect Wi-Fi, then try again", err_cap);
        return -1;
    }
    char body[160];
    snprintf(body, sizeof body, "{\"device_id\":\"%s\",\"recording\":\"%s\"}", s_dev, remote);
    const int cap = 1024;
    char *resp = psram_malloc(cap);
    if (!resp) {
        strlcpy(err, "Out of memory", err_cap);
        return -1;
    }
    int status = api_post_to("/v1/media/token", body, resp, cap);
    int rc = -1;
    if (status == 200) {
        cJSON *r = cJSON_Parse(resp);
        const char *u = r ? json_str(r, "url") : NULL;
        const cJSON *t = r ? cJSON_GetObjectItemCaseSensitive(r, "ttl_seconds") : NULL;
        if (u && strncmp(u, "https://", 8) == 0 && strlen(u) < url_cap) {
            strlcpy(url, u, url_cap);
            *ttl_s = cJSON_IsNumber(t) ? t->valueint : 300;
            rc = 0;
        } else {
            strlcpy(err, "Unexpected reply from the server", err_cap);
        }
        cJSON_Delete(r);
    } else if (status == 401) {
        strlcpy(err, "Recorder not authorized (upload key)", err_cap);
    } else if (status < 0) {
        strlcpy(err, "No connection. Check Wi-Fi", err_cap);
    } else {
        snprintf(err, err_cap, "Server error %d", status);
    }
    free(resp);
    ESP_LOGI(TAG, "media token: %s (http %d)", rc == 0 ? "ok" : "failed", status);   /* never the url */
    return rc;
}

