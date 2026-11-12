/* Teacher Notes QR (0.7.2; intro panel + fetch on tap 0.7.8). Included by ui_app.c (both boards).
 *
 * Tapping Teacher Notes stores the marker as before, then shows a QR code a
 * phone can scan to add ONE photo or video (up to 30 s) to this recording.
 * The link comes from the upload-api (POST /v1/media/token, this recorder's
 * upload key), is tied to the current recording and expires after ~5 min.
 * The HTTPS call runs on its own task; this file only touches LVGL from the
 * LVGL thread (qr_poll timer). The link itself is never logged. */

#include "uploader.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/idf_additions.h"
#include "esp_heap_caps.h"
#include <time.h>

#ifndef BOARD_UI_W
#define BOARD_UI_W 800
#define BOARD_UI_H 480
#endif

typedef enum { QR_IDLE = 0, QR_FETCHING, QR_READY, QR_FAILED, QR_SHOWN } qr_state_t;

static struct {
    lv_obj_t *ov, *qr, *title, *text, *timer_lbl, *wait;
    lv_obj_t *intro;    /* 0.7.8: intro panel over the QR card */
    lv_timer_t *poll;
    uint32_t deadline;  /* lv_tick when the link expires */
    bool in_intro;      /* intro shown; the QR stays hidden until "Add photos/videos" */
    bool no_wifi;       /* tap happened with Wi-Fi down */
} q;
static volatile bool s_qr_inflight;   /* 0.7.8: a qr_token task is running (never two) */

static portMUX_TYPE s_qr_mux = portMUX_INITIALIZER_UNLOCKED;
static volatile qr_state_t s_qr_state;
static char s_qr_wav[160], s_qr_cls[48];
static char s_qr_url[200];
static char s_qr_err[80];
static int s_qr_ttl;


/* ---------------------------------------------------------------- prefetch (0.7.5)
 * the QR took >10 s (cold server + a fresh TLS handshake on
 * the tap). The net task now fetches a link as soon as a recording is running
 * and refreshes it before its ~5 min expiry, so tapping Teacher Notes shows the
 * QR at once. A link is single-use, so one taken by a tap is dropped and the
 * task fetches the next. With no valid prefetched link the tap fetches on demand
 * as before. (0.8.2: the school-name fetch for the banner is gone; the header
 * is always the goalvera logo.) */
#include "esp_timer.h"

#define PF_REFRESH_S   60   /* refetch when less than this is left */
#define PF_MIN_LEFT_S  45   /* a tap only uses a link with at least this left */
#define PF_RETRY_S     15
#define PF_SETTLE_S    5           /* after a recording starts, let the WAV file open first */

static portMUX_TYPE s_pf_mux = portMUX_INITIALIZER_UNLOCKED;
static volatile bool s_pf_armed;          /* a session was started from this UI */
static char s_pf_cls[48];                 /* class code captured at start (LVGL thread) */
static char s_pf_wav[160], s_pf_url[200];
static int64_t s_pf_exp_us;               /* esp_timer time the cached link expires */

static int64_t now_us(void) { return esp_timer_get_time(); }

static void pf_clear_locked(void)
{
    memset(s_pf_url, 0, sizeof s_pf_url);
    s_pf_wav[0] = '\0';
    s_pf_exp_us = 0;
}

/* LVGL thread, right after audio_rec_start succeeded. */
static void qr_prefetch_arm(const char *class_code)
{
    taskENTER_CRITICAL(&s_pf_mux);
    strlcpy(s_pf_cls, class_code ? class_code : "", sizeof s_pf_cls);
    pf_clear_locked();
    s_pf_armed = true;
    taskEXIT_CRITICAL(&s_pf_mux);
}

/* LVGL thread: take a still-valid prefetched link for wav. Single use: the cache
 * is cleared so the net task fetches a fresh one for the next note. */
static bool qr_prefetch_take(const char *wav, char *url, size_t cap, int *ttl_s)
{
    bool ok = false;
    taskENTER_CRITICAL(&s_pf_mux);
    int64_t left = (s_pf_exp_us - now_us()) / 1000000;
    if (s_pf_url[0] && strcmp(s_pf_wav, wav) == 0 && left >= PF_MIN_LEFT_S) {
        strlcpy(url, s_pf_url, cap);
        *ttl_s = (int)left;
        ok = true;
    }
    pf_clear_locked();
    taskEXIT_CRITICAL(&s_pf_mux);
    return ok;
}

/* 0.7.6: every fetch here is opportunistic. It runs only when no other HTTPS
 * call holds the network lock and internal RAM has room for a TLS session, so
 * it can never collide with an upload, a QR tap, or starve the SD writer. */
static bool net_try_begin(void)
{
    if (!uploader_net_lock(0)) {
        return false;
    }
    if (!uploader_net_mem_ok()) {
        uploader_net_unlock();
        return false;
    }
    return true;
}

static void net_bg_task(void *arg)
{
    (void)arg;
    bool was_up = false;
    int64_t pf_next = 0, live_since = 0;
    static char url[200], err[80], wav[160], cls[48];
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(2000));
        wifi_status_t ws;
        wifi_get_status(&ws);
        int64_t t = now_us();
        if (!ws.connected) {
            was_up = false;
            continue;
        }
        if (!was_up) {
            was_up = true;
            ESP_LOGI("ui", "wifi up: internal free %u (largest %u, largest DMA %u)",
                     (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                     (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
                     (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA));
        }

        audio_status_t st;
        audio_get_status(&st);
        bool live = st.state == AUDIO_RECORDING || st.state == AUDIO_PAUSED;
        if (!live) {
            live_since = 0;
        } else if (!live_since) {
            live_since = t;
        }
        if (!live && st.state != AUDIO_STARTING && s_pf_armed) {
            taskENTER_CRITICAL(&s_pf_mux);
            s_pf_armed = false;             /* session over: drop any link */
            pf_clear_locked();
            taskEXIT_CRITICAL(&s_pf_mux);
        }
        if (live && s_pf_armed && st.path[0] && t >= pf_next &&
            t - live_since >= (int64_t)PF_SETTLE_S * 1000000) {
            bool need;
            taskENTER_CRITICAL(&s_pf_mux);
            need = !s_pf_url[0] || strcmp(s_pf_wav, st.path) != 0 ||
                   (s_pf_exp_us - t) / 1000000 < PF_REFRESH_S;
            strlcpy(cls, s_pf_cls, sizeof cls);
            taskEXIT_CRITICAL(&s_pf_mux);
            if (need && !net_try_begin()) {
                pf_next = now_us() + 5 * 1000000;   /* busy or low RAM: look again soon */
            } else if (need) {
                strlcpy(wav, st.path, sizeof wav);
                int ttl = 0;
                int64_t asked = now_us();
                int rc = uploader_media_token(wav, cls, url, sizeof url, &ttl, err, sizeof err);
                uploader_net_unlock();
                taskENTER_CRITICAL(&s_pf_mux);
                if (rc == 0 && s_pf_armed) {
                    strlcpy(s_pf_url, url, sizeof s_pf_url);
                    strlcpy(s_pf_wav, wav, sizeof s_pf_wav);
                    s_pf_exp_us = asked + (int64_t)(ttl > 0 ? ttl : 300) * 1000000;   /* from when we asked */
                }
                taskEXIT_CRITICAL(&s_pf_mux);
                memset(url, 0, sizeof url);
                ESP_LOGI("ui", "Teacher Notes link prefetch: %s", rc == 0 ? "ready" : "failed");
                pf_next = now_us() + (int64_t)(rc == 0 ? 2 : PF_RETRY_S) * 1000000;
            }
        }

    }
}

static void net_bg_start(void)
{
    /* 0.7.6: stack in PSRAM (10 KB of internal RAM back for Wi-Fi/TLS/SD). The
     * task never writes flash. */
    if (xTaskCreatePinnedToCoreWithCaps(net_bg_task, "net_bg", 10240, NULL, 3, NULL, 0,
                                        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS) {
        ESP_LOGE("ui", "net_bg task not started (no QR prefetch)");
    }
}

static void qr_task(void *arg)
{
    (void)arg;
    char url[200], err[80];
    int ttl = 0;
    int rc = -1;
    url[0] = '\0';
    /* Tap: wait for any background fetch to finish (one TLS session at a time). */
    if (uploader_net_lock(20000)) {
        rc = uploader_media_token(s_qr_wav, s_qr_cls, url, sizeof url, &ttl, err, sizeof err);
        uploader_net_unlock();
    } else {
        strlcpy(err, "Recorder busy uploading. Try again in a moment", sizeof err);
    }
    taskENTER_CRITICAL(&s_qr_mux);
    if (s_qr_state == QR_FETCHING) {      /* not closed meanwhile */
        strlcpy(s_qr_url, url, sizeof s_qr_url);
        strlcpy(s_qr_err, err, sizeof s_qr_err);
        s_qr_ttl = ttl;
        s_qr_state = rc == 0 ? QR_READY : QR_FAILED;
    } else {
        s_qr_state = QR_IDLE;
    }
    s_qr_inflight = false;
    taskEXIT_CRITICAL(&s_qr_mux);
    memset(url, 0, sizeof url);
    vTaskDeleteWithCaps(NULL);
}

static void qr_close(void)
{
    taskENTER_CRITICAL(&s_qr_mux);
    s_qr_state = QR_IDLE;   /* a fetch still running then finishes into QR_IDLE, not shown */
    memset(s_qr_url, 0, sizeof s_qr_url);
    taskEXIT_CRITICAL(&s_qr_mux);
    if (q.qr) {
        lv_qrcode_update(q.qr, " ", 1);   /* don't leave a live link drawn in memory */
    }
    q.in_intro = false;
    lv_obj_add_flag(q.ov, LV_OBJ_FLAG_HIDDEN);
}

static void on_qr_close(lv_event_t *e)
{
    (void)e;
    qr_close();
}

static void qr_poll(lv_timer_t *t)
{
    (void)t;
    qr_state_t st = s_qr_state;
    if (st == QR_READY) {
        taskENTER_CRITICAL(&s_qr_mux);
        char url[200];
        strlcpy(url, s_qr_url, sizeof url);
        int ttl = s_qr_ttl;
        s_qr_state = QR_SHOWN;
        taskEXIT_CRITICAL(&s_qr_mux);
        if (lv_obj_has_flag(q.ov, LV_OBJ_FLAG_HIDDEN)) {   /* closed meanwhile: drop it */
            memset(url, 0, sizeof url);
            taskENTER_CRITICAL(&s_qr_mux);
            s_qr_state = QR_IDLE;
            memset(s_qr_url, 0, sizeof s_qr_url);
            taskEXIT_CRITICAL(&s_qr_mux);
            return;
        }
        lv_qrcode_update(q.qr, url, (uint32_t)strlen(url));
        memset(url, 0, sizeof url);
        lv_obj_remove_flag(q.qr, LV_OBJ_FLAG_HIDDEN);   /* hidden by the intro panel until asked for */
        lv_obj_add_flag(q.wait, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(q.title, "Add a photo or video");
        lv_label_set_text(q.text, "Scan with your phone camera. One photo, or a video up to 30 seconds, "
                                  "is added to this recording.");
        q.deadline = lv_tick_get() + (uint32_t)(ttl > 0 ? ttl : 300) * 1000u;
        ESP_LOGI("ui", "Teacher Notes QR shown (%d s)", ttl);
    } else if (st == QR_FAILED) {
        if (q.in_intro) {
            return;   /* keep the error until the teacher asks for the QR */
        }
        taskENTER_CRITICAL(&s_qr_mux);
        s_qr_state = QR_IDLE;
        taskEXIT_CRITICAL(&s_qr_mux);
        lv_label_set_text(q.wait, "");
        lv_label_set_text(q.title, "No QR code");
        lv_label_set_text(q.text, s_qr_err[0] ? s_qr_err : "Could not get a link. Try again.");
        lv_label_set_text(q.timer_lbl, "The Teacher Notes marker was saved.");
        status_error(s_qr_err);
    }
    if (st == QR_SHOWN || s_qr_state == QR_SHOWN) {
        int32_t left = (int32_t)(q.deadline - lv_tick_get());
        if (left <= 0 && q.in_intro) {   /* expired unseen: fetch a new one if asked */
            taskENTER_CRITICAL(&s_qr_mux);
            s_qr_state = QR_IDLE;
            memset(s_qr_url, 0, sizeof s_qr_url);
            taskEXIT_CRITICAL(&s_qr_mux);
            lv_qrcode_update(q.qr, " ", 1);
            return;
        }
        if (left <= 0) {
            qr_close();
            show_toast("QR code expired. Tap Teacher Notes for a new one");
            return;
        }
        char b[48];
        int s_left = (int)((left + 999) / 1000);
        snprintf(b, sizeof b, "Expires in %d:%02d", s_left / 60, s_left % 60);
        lv_label_set_text(q.timer_lbl, b);
    }
}

/* Start (or adopt) the link fetch for the current recording. LVGL thread.
 * Never spawns a second qr_token task: one still running from an earlier tap
 * is adopted if it is for this recording. */
static void qr_fetch_start(void)
{
    if (s_qr_state == QR_FETCHING || s_qr_state == QR_READY || s_qr_state == QR_SHOWN) {
        return;
    }
    audio_status_t st;
    audio_get_status(&st);
    wifi_status_t ws;
    wifi_get_status(&ws);
    q.no_wifi = !ws.connected;
    if (!ws.connected) {
        return;
    }
    rec_labels_t labels;
    read_labels(ui.room_dd, ui.teacher_dd, ui.class_dd, &labels);
    taskENTER_CRITICAL(&s_qr_mux);
    if (s_qr_inflight) {
        if (strcmp(s_qr_wav, st.path) == 0) {
            s_qr_state = QR_FETCHING;   /* adopt the running fetch */
        } else {
            strlcpy(s_qr_err, "Recorder busy. Try again in a moment.", sizeof s_qr_err);
            s_qr_state = QR_FAILED;
        }
        taskEXIT_CRITICAL(&s_qr_mux);
        return;
    }
    strlcpy(s_qr_wav, st.path, sizeof s_qr_wav);
    strlcpy(s_qr_cls, labels.class_code, sizeof s_qr_cls);
    s_qr_err[0] = '\0';
    s_qr_state = QR_FETCHING;
    taskEXIT_CRITICAL(&s_qr_mux);
    {
        char url[200];
        int ttl = 0;
        if (qr_prefetch_take(st.path, url, sizeof url, &ttl)) {   /* instant: already fetched */
            taskENTER_CRITICAL(&s_qr_mux);
            strlcpy(s_qr_url, url, sizeof s_qr_url);
            s_qr_ttl = ttl;
            s_qr_state = QR_READY;
            taskEXIT_CRITICAL(&s_qr_mux);
            memset(url, 0, sizeof url);
            qr_poll(NULL);
            return;
        }
    }
    s_qr_inflight = true;
    if (xTaskCreatePinnedToCoreWithCaps(qr_task, "qr_token", 10240, NULL, 4, NULL, 0,
                                        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS) {
        taskENTER_CRITICAL(&s_qr_mux);
        s_qr_inflight = false;
        strlcpy(s_qr_err, "Recorder busy. Try again in a moment.", sizeof s_qr_err);
        s_qr_state = QR_FAILED;
        taskEXIT_CRITICAL(&s_qr_mux);
    }
}

/* QR view: what the card shows once the intro is dismissed. */
static void qr_view_show(void)
{
    q.in_intro = false;
    lv_obj_add_flag(q.intro, LV_OBJ_FLAG_HIDDEN);
    if (q.no_wifi) {
        lv_obj_add_flag(q.qr, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(q.wait, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(q.title, "No QR code");
        lv_label_set_text(q.wait, "");
        lv_label_set_text(q.text, "Wi-Fi is not connected, so a phone can't add a photo right now.");
        lv_label_set_text(q.timer_lbl, "The Teacher Notes marker was saved.");
        return;
    }
    if (s_qr_state == QR_IDLE) {   /* an unseen link expired: get a new one */
        qr_fetch_start();
    }
    qr_poll(NULL);   /* shows the QR, the error, or leaves the loading state */
}

static void on_qr_add(lv_event_t *e)
{
    (void)e;
    qr_view_show();
}

/* Teacher Notes tapped (marker already stored). 0.7.8: show the intro panel
 * and fetch the link in the background right away. */
static void qr_open(void)
{
    if (!lv_obj_has_flag(q.ov, LV_OBJ_FLAG_HIDDEN)) {
        return;   /* already open */
    }
    lv_obj_add_flag(q.qr, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(q.wait, LV_OBJ_FLAG_HIDDEN);
    lv_label_set_text(q.timer_lbl, "");
    lv_label_set_text(q.title, "Teacher Notes");
    lv_label_set_text(q.wait, LV_SYMBOL_REFRESH);
    lv_label_set_text(q.text, "Getting a QR code for your phone...");
    q.in_intro = true;
    lv_obj_remove_flag(q.intro, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(q.ov, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_to_index(q.ov, -1);
    qr_fetch_start();
}

static void build_qr_popup(void)
{
    const int W = BOARD_UI_W, H = BOARD_UI_H;
    const bool small = W < 600;
    const int pad = small ? 12 : 24, qs = small ? 196 : 300, bh = small ? 56 : 72;
    q.ov = lv_obj_create(lv_screen_active());
    lv_obj_set_size(q.ov, W, H);
    lv_obj_set_pos(q.ov, 0, 0);
    style_box(q.ov, 0x000000, 0);
    lv_obj_set_style_bg_opa(q.ov, LV_OPA_60, 0);
    lv_obj_add_flag(q.ov, LV_OBJ_FLAG_HIDDEN);

    const int cw = W - 2 * (small ? 8 : 40), ch = H - 2 * (small ? 8 : 30);
    lv_obj_t *card = lv_obj_create(q.ov);
    lv_obj_set_size(card, cw, ch);
    lv_obj_center(card);
    style_box(card, COL_MODAL, 16);
    lv_obj_set_style_border_width(card, 2, 0);
    lv_obj_set_style_border_color(card, lv_color_hex(COL_LINE), 0);
    lv_obj_remove_flag(card, LV_OBJ_FLAG_SCROLLABLE);

    /* white quiet zone around the code so phones read it on the dark card */
    lv_obj_t *qbg = lv_obj_create(card);
    lv_obj_set_size(qbg, qs + 16, qs + 16);
    lv_obj_set_pos(qbg, pad - 4, (ch - 4 - qs - 16) / 2);
    style_box(qbg, 0xFFFFFF, 12);
    lv_obj_remove_flag(qbg, LV_OBJ_FLAG_SCROLLABLE);
    q.qr = lv_qrcode_create(qbg);
    lv_qrcode_set_size(q.qr, qs);
    lv_qrcode_set_dark_color(q.qr, lv_color_hex(0x000000));
    lv_qrcode_set_light_color(q.qr, lv_color_hex(0xFFFFFF));
    lv_obj_center(q.qr);
    lv_obj_add_flag(q.qr, LV_OBJ_FLAG_HIDDEN);
    q.wait = label(qbg, LV_SYMBOL_REFRESH, &lv_font_montserrat_48, COL_SAVE);
    lv_obj_center(q.wait);

    const int tx = pad + qs + 16 + pad, tw = cw - 4 - tx - pad;
    q.title = label(card, "Teacher Notes", small ? &lv_font_montserrat_20 : &lv_font_montserrat_28, 0xFFFFFF);
    lv_obj_set_pos(q.title, tx, pad);
    lv_obj_set_width(q.title, tw);
    lv_label_set_long_mode(q.title, LV_LABEL_LONG_WRAP);
    q.text = label(card, "", small ? &lv_font_montserrat_16 : &lv_font_montserrat_20, 0xFFFFFF);
    lv_obj_set_pos(q.text, tx, pad + (small ? 54 : 48));
    lv_obj_set_width(q.text, tw);
    lv_label_set_long_mode(q.text, LV_LABEL_LONG_WRAP);
    q.timer_lbl = label(card, "", small ? &lv_font_montserrat_14 : &lv_font_montserrat_20, COL_MUTED);
    lv_obj_set_pos(q.timer_lbl, tx, ch - 4 - pad - bh - (small ? 40 : 40));
    lv_obj_set_width(q.timer_lbl, tw);
    lv_label_set_long_mode(q.timer_lbl, LV_LABEL_LONG_WRAP);

    lv_obj_t *close = lv_button_create(card);
    lv_obj_set_pos(close, tx, ch - 4 - pad - bh);
    lv_obj_set_size(close, tw, bh);
    style_box(close, COL_KEEP, bh / 2);   /* round pill, like the other buttons */
    lv_obj_add_event_cb(close, on_qr_close, LV_EVENT_CLICKED, NULL);
    lv_obj_t *cl = label(close, "Close", small ? &lv_font_montserrat_20 : &lv_font_montserrat_28, 0xFFFFFF);
    lv_obj_center(cl);

    /* 0.7.8 intro panel: covers the whole card until "Add photos/videos". */
    q.intro = lv_obj_create(card);
    lv_obj_set_size(q.intro, cw - 4, ch - 4);
    lv_obj_set_pos(q.intro, 0, 0);
    style_box(q.intro, COL_MODAL, 16);
    lv_obj_t *it = label(q.intro, "Teacher Notes", small ? &lv_font_montserrat_20 : &lv_font_montserrat_28, 0xFFFFFF);
    lv_obj_set_pos(it, pad, pad);
    lv_obj_t *ib = label(q.intro, "Just say it out loud: ask for a worksheet, add to the lesson plan, "
                                  "or note anything about today.\n\nWant to add photos or videos?",
                         small ? &lv_font_montserrat_16 : &lv_font_montserrat_20, 0xFFFFFF);
    lv_obj_set_pos(ib, pad, pad + (small ? 36 : 48));
    lv_obj_set_width(ib, cw - 4 - 2 * pad);
    lv_label_set_long_mode(ib, LV_LABEL_LONG_WRAP);
    const int gap = small ? 10 : 20, bw2 = (cw - 4 - 2 * pad - gap) / 2, by = ch - 4 - pad - bh;
    lv_obj_t *done = lv_button_create(q.intro);
    lv_obj_set_pos(done, pad, by);
    lv_obj_set_size(done, bw2, bh);
    style_box(done, COL_KEEP, bh / 2);
    lv_obj_add_event_cb(done, on_qr_close, LV_EVENT_CLICKED, NULL);
    lv_obj_center(label(done, "Done", small ? &lv_font_montserrat_20 : &lv_font_montserrat_28, 0xFFFFFF));
    lv_obj_t *add = lv_button_create(q.intro);
    lv_obj_set_pos(add, pad + bw2 + gap, by);
    lv_obj_set_size(add, bw2, bh);
    style_box(add, COL_SAVE, bh / 2);
    lv_obj_add_event_cb(add, on_qr_add, LV_EVENT_CLICKED, NULL);
    lv_obj_t *al = label(add, "Add photos/videos", small ? &lv_font_montserrat_16 : &lv_font_montserrat_24, 0xFFFFFF);
    lv_obj_set_width(al, bw2 - 8);
    lv_obj_set_style_text_align(al, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(al, LV_LABEL_LONG_WRAP);
    lv_obj_center(al);

    q.poll = lv_timer_create(qr_poll, 250, NULL);
    net_bg_start();
}
