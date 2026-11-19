#include "ui_app.h"

#include "audio_rec.h"
#include "board_ctrl.h"
#include "board_pins.h"
#include "config_store.h"
#include "display.h"
#include "esp_attr.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "lvgl.h"
#include "marker_types.h"
#include "nvs_prefs.h"
#include "rtc_pcf8563.h"
#include "sdcard.h"
#include "status_line.h"
#include "uploader.h"
#include "version.h"
#include "wifi_time.h"
#include <stdio.h>
#include <string.h>

#define COL_BG      0x0B1220
#define COL_CARD    0x151E30
#define COL_CHIP    0x172033
#define COL_LINE    0x2C3A50
#define COL_MUTED   0x9AA6B8
#define COL_BLUE    0x3B82F6
#define COL_RED     0xE2231A
#define COL_YELLOW  0xF5B301
#define COL_GREEN   0x3DDC6A
#define COL_AMBER   0xE6A100
#define COL_DROP    0x10182A
#define COL_STATUS  0x0A101C
#define COL_MODAL   0x1A2336
#define COL_PINK    0xF6C9C6
#define COL_OFF     0x2C3648
#define COL_SAVE    0x2F6FE0
#define COL_KEEP    0x2C3548
#define COL_END     0x9A3030
#define COL_END_BD  0xE08A82
#define COL_LOGO    0x2F6BFF

#ifndef LV_RADIUS_CIRCLE
#define LV_RADIUS_CIRCLE 0x7FFF
#endif

#define METER_BARS 22
#define ACT_ROWS 4

/* Board differences used by the shared UI logic. The CrowPanel values are
 * exactly the ones this file always used. The Hosyond 3.5" (480x320 after
 * rotation) scales the 800x480 layout: 0.6 across, 0.667 down. */
#if CONFIG_RECORDER_BOARD_HOSYOND_35
/* No RTC chip on that board: time only comes from Wi-Fi (NTP). */
#define UI_NO_TIME_HINT "Needs Wi-Fi to set the clock"
#define UI_FONT_CLOCK        (&lv_font_montserrat_32)
#define UI_FONT_CLOCK_NOTIME (&lv_font_montserrat_20)
#define UI_FONT_DD           (&lv_font_montserrat_14)
#define UI_FONT_LIST         (&lv_font_montserrat_16)
#define UI_LIST_MAX_H        200
/* Open-list rows padded to at least 44 px so each choice is a real touch target. */
#define UI_LIST_ROW_MIN      44
/* Short "when" line in the Incident/Bug confirm card. */
#define UI_MARK_WHEN_FMT     "Marked at %s  (%s in)"
/* Status-bar Wi-Fi icon and the network picker (ui_wifi_picker.inc). */
#define WIFI_CHIP_W          56
#define WIFI_X_FONT          (&lv_font_montserrat_12)
#define WIFI_X_SHIFT         7
#define PK_F_TITLE           (&lv_font_montserrat_16)
#define PK_F_ROW             (&lv_font_montserrat_16)
#define PK_F_TEXT            (&lv_font_montserrat_14)
#define PK_F_SMALL           (&lv_font_montserrat_12)
#define PK_F_KB              (&lv_font_montserrat_16)
#define PK_PAD               6
#define PK_GAP               4
#define PK_HDR_H             40
#define PK_ROW_H             44
#define PK_RADIUS            8
#define PK_BTN_W             96
#define PK_SCAN_W            110
#define PK_SHOW_W            72
#define PK_KB_Y              120
#define PK_W                 BOARD_UI_W
#define PK_H                 BOARD_UI_H
#define UI_DD_RADIUS         8
#define UI_DD_PAD_H          8
#define UI_DD_PAD_V          8
#else
#define UI_NO_TIME_HINT "RTC has no valid time"
#define UI_FONT_CLOCK        (&lv_font_montserrat_48)
#define UI_FONT_CLOCK_NOTIME (&lv_font_montserrat_32)
#define UI_FONT_DD           (&lv_font_montserrat_20)
#define UI_FONT_LIST         (&lv_font_montserrat_20)
#define UI_LIST_MAX_H        240
#define UI_DD_RADIUS         12
#define UI_DD_PAD_H          14
#define UI_DD_PAD_V          10
#define UI_MARK_WHEN_FMT     "Marked at %s  (%s into the recording)"
#define WIFI_CHIP_W          84
#define WIFI_X_FONT          (&lv_font_montserrat_16)
#define WIFI_X_SHIFT         8
#define PK_F_TITLE           (&lv_font_montserrat_24)
#define PK_F_ROW             (&lv_font_montserrat_20)
#define PK_F_TEXT            (&lv_font_montserrat_20)
#define PK_F_SMALL           (&lv_font_montserrat_16)
#define PK_F_KB              (&lv_font_montserrat_24)
#define PK_PAD               12
#define PK_GAP               8
#define PK_HDR_H             56
#define PK_ROW_H             60
#define PK_RADIUS            12
#define PK_BTN_W             150
#define PK_SCAN_W            160
#define PK_SHOW_W            110
#define PK_KB_Y              180
#define PK_W                 800
#define PK_H                 480
#endif

typedef struct {
    lv_obj_t *home;
    lv_obj_t *session;
    lv_obj_t *modal;
    lv_obj_t *clock_time;
    lv_obj_t *clock_date;
    lv_obj_t *room_dd;
    lv_obj_t *storage_main;
    lv_obj_t *storage_of;
    lv_obj_t *storage_bar;
    lv_obj_t *upload_lbl;
    lv_obj_t *record_btn;
    lv_obj_t *record_hint;
    lv_obj_t *act_title;
    lv_obj_t *act_time[ACT_ROWS];
    lv_obj_t *act_msg[ACT_ROWS];
    lv_obj_t *msgbox;
    lv_obj_t *msg_title;
    lv_obj_t *msg_text;
    lv_obj_t *home_wifi;
    lv_obj_t *home_bars[4];
    lv_obj_t *home_wifi_x;
    lv_obj_t *ses_wifi;
    lv_obj_t *ses_bars[4];
    lv_obj_t *ses_wifi_x;
    lv_obj_t *badge;
    lv_obj_t *badge_lbl;
    lv_obj_t *session_card;
    lv_obj_t *started;
    lv_obj_t *timer;
    lv_obj_t *meter_state;
    lv_obj_t *meter[METER_BARS];
    lv_obj_t *room_value;
    lv_obj_t *teacher_dd;
    lv_obj_t *class_dd;
    lv_obj_t *pause_btn;
    lv_obj_t *pause_lbl;
    lv_obj_t *modal_dur;
    lv_obj_t *modal_countdown;
    lv_obj_t *mark_btns[MARKER_TYPE_COUNT];
    lv_obj_t *mark;
    lv_obj_t *mark_title;
    lv_obj_t *mark_text;
    lv_obj_t *mark_when;
    lv_obj_t *modal_room;
    lv_obj_t *modal_teacher;
    lv_obj_t *modal_class;
    lv_obj_t *save_btn;
    lv_obj_t *save_lbl;
    lv_obj_t *keep_btn;
} ui_t;

static ui_t ui;
static void qr_prefetch_arm(const char *class_code);   /* ui_qr.inc: link prefetch (0.7.5) */
static bool s_sync;
static bool s_save_pending;
static bool s_error_latched;
static int s_space_ticks;
/* 0.7.7: PSRAM (LVGL copies these into its own heap). */
static EXT_RAM_BSS_ATTR char s_room_opts[2048];
static EXT_RAM_BSS_ATTR char s_teacher_opts[2048];
static EXT_RAM_BSS_ATTR char s_class_opts[2048];
static uint32_t s_act_version = UINT32_MAX;
/* Recent activity rows: first row y, row pitch and band height inside the
 * band, set by the board's home builder. A message that does not fit on one
 * row wraps onto a second one and pushes the older rows down; rows that no
 * longer fit in the band are hidden (paint_activity). */
#define ACT_MAX_LINES 2
static int s_act_y0, s_act_pitch, s_act_band_h, s_act_msg_w;
static uint32_t s_err_seen;
static lv_timer_t *s_msg_timer;
static bool s_low_announced;
static bool s_start_announced;
/* End dialog: capture is held while it is open; autosave after 4 min. */
#define END_TIMEOUT_MS (4UL * 60UL * 1000UL)
static bool s_end_was_recording;
static uint32_t s_end_opened_ms;
/* Marker buttons (0.6.8): one per MARKER_TYPES entry on the recording
 * screen. The time is captured at the tap. Types with a confirm text
 * (Incident, Bug) show an OK / Cancel card and store that tap time only on
 * OK; the others store it at once. A second tap on the same button within
 * MARK_DEBOUNCE_MS is ignored so a double tap can't log twice. */
#define MARK_DEBOUNCE_MS 1000
static audio_mark_time_t s_mark_time;
static int s_mark_pending = -1;
static uint32_t s_mark_last_tap[MARKER_TYPE_COUNT];
/* Toast: one small pill on the top layer, created on first use and reused,
 * so it costs one object + one label in the LVGL pools. Not clickable, so
 * touches pass through to the screen under it. */
#define TOAST_MS 3500
/* End-dialog autosave reason for the activity log ("Autosaving recording X <why>"). */
static const char *s_autosave_why = "after no response";
#if CONFIG_RECORDER_BOARD_HOSYOND_35
/* Battery gauge chip (home + session top bars) and auto sleep (0.7.0). */
#include "battery.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "power_sleep.h"
#include "batt_gauge.h"
#define PWR_CHIPS 2
static batt_gauge_t s_gauge[PWR_CHIPS];
static int s_pwr_n;
static bool s_batt_low_warned;
static bool s_batt_crit_done;
#define SLEEP_WARN_MS        30000UL
#define SLEEP_UPLOAD_WAIT_MS (15UL * 60UL * 1000UL)
typedef enum { SLEEP_NONE = 0, SLEEP_WARN, SLEEP_GOING } sleep_state_t;
static sleep_state_t s_sleep_state;
static lv_obj_t *s_sleep_ov;
static lv_obj_t *s_sleep_text;
static volatile bool s_sleep_cancel;
static volatile bool s_sleep_task_running;
#endif
static lv_obj_t *s_toast;
static lv_obj_t *s_toast_lbl;
static lv_timer_t *s_toast_timer;

static void style_box(lv_obj_t *o, uint32_t bg, int radius)
{
    lv_obj_set_style_bg_color(o, lv_color_hex(bg), 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(o, radius, 0);
    lv_obj_set_style_border_width(o, 0, 0);
    lv_obj_set_style_pad_all(o, 0, 0);
    lv_obj_set_style_shadow_width(o, 0, 0);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
}

static lv_obj_t *label(lv_obj_t *parent, const char *text, const lv_font_t *font, uint32_t color)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_label_set_text(l, text);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_color(l, lv_color_hex(color), 0);
    return l;
}

static void fmt_commas(char *dst, size_t n, uint32_t v)
{
    char tmp[16];
    snprintf(tmp, sizeof tmp, "%lu", (unsigned long)v);
    int len = (int)strlen(tmp);
    int commas = (len - 1) / 3;
    if ((size_t)(len + commas + 1) > n) {
        strlcpy(dst, tmp, n);
        return;
    }
    int di = len + commas;
    dst[di] = '\0';
    int group = 0;
    for (int i = len - 1; i >= 0; i--) {
        dst[--di] = tmp[i];
        if (++group == 3 && i != 0) {
            dst[--di] = ',';
            group = 0;
        }
    }
}

static void fmt_hms(char *dst, size_t n, uint32_t ms)
{
    uint32_t sec = ms / 1000;
    snprintf(dst, n, "%02lu:%02lu:%02lu", (unsigned long)(sec / 3600),
             (unsigned long)((sec / 60) % 60), (unsigned long)(sec % 60));
}

static void join_opts(char *dst, size_t n, int count, const char *(*get)(int))
{
    dst[0] = '\0';
    if (count <= 0) {
        strlcpy(dst, "(none)", n);
        return;
    }
    size_t used = 0;
    for (int i = 0; i < count; i++) {
        const char *s = get(i);
        size_t len = strlen(s);
        if (used && used + 1 < n) {
            dst[used++] = '\n';
        }
        if (used + len + 1 > n) {
            break;
        }
        memcpy(dst + used, s, len);
        used += len;
        dst[used] = '\0';
    }
}

static int find_str(int count, const char *(*get)(int), const char *want)
{
    if (!want || !want[0]) {
        return 0;
    }
    for (int i = 0; i < count; i++) {
        if (strcmp(get(i), want) == 0) {
            return i;
        }
    }
    return 0;
}

static void style_list(lv_event_t *e)
{
    lv_obj_t *list = lv_dropdown_get_list(lv_event_get_target(e));
    if (!list) {
        return;
    }
    lv_obj_set_style_bg_color(list, lv_color_hex(COL_DROP), 0);
    lv_obj_set_style_text_color(list, lv_color_white(), 0);
    lv_obj_set_style_text_font(list, UI_FONT_LIST, 0);
    lv_obj_set_style_max_height(list, UI_LIST_MAX_H, 0);
#ifdef UI_LIST_ROW_MIN
    lv_obj_set_style_text_line_space(list, UI_LIST_ROW_MIN - lv_font_get_line_height(UI_FONT_LIST), 0);
#endif
}

static lv_obj_t *make_dropdown(lv_obj_t *parent, const char *opts)
{
    lv_obj_t *dd = lv_dropdown_create(parent);
    lv_dropdown_set_options(dd, opts);
    lv_dropdown_set_symbol(dd, LV_SYMBOL_DOWN);
    lv_obj_set_style_bg_color(dd, lv_color_hex(COL_DROP), 0);
    lv_obj_set_style_border_color(dd, lv_color_hex(COL_BLUE), 0);
    lv_obj_set_style_border_width(dd, 2, 0);
    lv_obj_set_style_radius(dd, UI_DD_RADIUS, 0);
    lv_obj_set_style_text_color(dd, lv_color_white(), 0);
    lv_obj_set_style_text_font(dd, UI_FONT_DD, 0);
    lv_obj_set_style_pad_hor(dd, UI_DD_PAD_H, 0);
    lv_obj_set_style_pad_ver(dd, UI_DD_PAD_V, 0);
    lv_obj_add_event_cb(dd, style_list, LV_EVENT_READY, NULL);
    return dd;
}

static void make_bars(lv_obj_t *parent, lv_obj_t **bars)
{
    for (int i = 0; i < 4; i++) {
        bars[i] = lv_obj_create(parent);
        lv_obj_set_size(bars[i], 5, 8 + i * 4);
        style_box(bars[i], COL_OFF, 2);
        lv_obj_align(bars[i], LV_ALIGN_BOTTOM_LEFT, i * 8, 0);
    }
}

static void paint_bars_col(lv_obj_t **bars, int lit, uint32_t on_col)
{
    for (int i = 0; i < 4; i++) {
        lv_obj_set_style_bg_color(bars[i], lv_color_hex(i < lit ? on_col : COL_OFF), 0);
    }
}

static void paint_bars(lv_obj_t **bars, int lit, bool on)
{
    paint_bars_col(bars, on ? lit : 0, COL_GREEN);
}

static void wifi_picker_open(lv_event_t *e); /* ui_wifi_picker.inc */

/* Status-bar Wi-Fi icon (0.6.3): signal bars only, no network name (it
 * collided with the bars on the 3.5" screen). A red x next to grey bars
 * means not connected; amber bars that climb mean a switch is running.
 * Tapping it opens the network picker. The touch area reaches past the
 * small icon (ext click area) so it is easy to hit. */
static lv_obj_t *build_wifi_chip(lv_obj_t *parent, int x, int y, int h, int radius, lv_obj_t **bars,
                                 lv_obj_t **xl)
{
    lv_obj_t *chip = lv_obj_create(parent);
    lv_obj_set_pos(chip, x, y);
    lv_obj_set_size(chip, WIFI_CHIP_W, h);
    style_box(chip, COL_CHIP, radius);
    lv_obj_set_style_border_width(chip, 1, 0);
    lv_obj_set_style_border_color(chip, lv_color_hex(COL_LINE), 0);
    lv_obj_set_style_bg_color(chip, lv_color_hex(COL_LINE), LV_STATE_PRESSED);
    lv_obj_add_flag(chip, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_ext_click_area(chip, 10);
    lv_obj_add_event_cb(chip, wifi_picker_open, LV_EVENT_CLICKED, NULL);
    lv_obj_t *box = lv_obj_create(chip);
    lv_obj_set_size(box, 30, 20);
    style_box(box, COL_CHIP, 0);
    lv_obj_set_style_bg_opa(box, LV_OPA_TRANSP, 0);
    lv_obj_align(box, LV_ALIGN_CENTER, -WIFI_X_SHIFT, 0);
    lv_obj_remove_flag(box, LV_OBJ_FLAG_CLICKABLE);
    make_bars(box, bars);
    for (int i = 0; i < 4; i++) {
        lv_obj_remove_flag(bars[i], LV_OBJ_FLAG_CLICKABLE);
    }
    *xl = label(chip, LV_SYMBOL_CLOSE, WIFI_X_FONT, COL_RED);
    lv_obj_align(*xl, LV_ALIGN_CENTER, 2 * WIFI_X_SHIFT + 3, 1);
    lv_obj_remove_flag(*xl, LV_OBJ_FLAG_CLICKABLE);
    return chip;
}

/* Home screen firmware version, e.g. "v0.6.1", from FW_VERSION (PROJECT_VER
 * in CMakeLists.txt). Small muted caption, right-aligned in a fixed-width
 * box at the top-right of the Recent activity band, on the same line as
 * the "Recent activity" title, whose width the caller trims so the two
 * never meet. A longer version string gets "..." instead of spilling. */
static lv_obj_t *version_caption(lv_obj_t *band, const lv_font_t *font, int w, int right_pad, int y)
{
    char txt[40];
    snprintf(txt, sizeof txt, "v%s", FW_VERSION);
    lv_obj_t *l = label(band, txt, font, COL_MUTED);
    lv_obj_set_width(l, w);
    lv_label_set_long_mode(l, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(l, LV_TEXT_ALIGN_RIGHT, 0);
    lv_obj_align(l, LV_ALIGN_TOP_RIGHT, -right_pad, y);
    return l;
}

/* 800x480 layout helpers (CrowPanel). The same layout scaled to 480x320
 * (Hosyond 3.5") lives in ui_layout_480x320.inc. */
#if !CONFIG_RECORDER_BOARD_HOSYOND_35
#define HEADER_H     64  /* header band y 0..64; clock starts at y 74 */
#define BRAND_X      16
#define BRAND_GAP    10

#define BRAND_LOGO_H 44  /* goalvera lockup height in the 64 px header */
#define F_BRAND_SUB  (&lv_font_montserrat_16)
#include "ui_brand.inc"

static void usb_chip(lv_obj_t *parent, int x, int y)
{
    lv_obj_t *chip = lv_obj_create(parent);
    lv_obj_set_pos(chip, x, y);
    lv_obj_set_size(chip, 148, 44);
    style_box(chip, COL_CHIP, 12);
    lv_obj_set_style_border_width(chip, 1, 0);
    lv_obj_set_style_border_color(chip, lv_color_hex(COL_LINE), 0);
    lv_obj_t *icon = label(chip, LV_SYMBOL_USB, &lv_font_montserrat_20, COL_YELLOW);
    lv_obj_align(icon, LV_ALIGN_LEFT_MID, 12, 0);
    lv_obj_t *t = label(chip, "USB power", &lv_font_montserrat_16, 0xFFFFFF);
    lv_obj_align(t, LV_ALIGN_LEFT_MID, 40, 0);
}
#endif /* !CONFIG_RECORDER_BOARD_HOSYOND_35 */

static void read_labels(lv_obj_t *room, lv_obj_t *teacher, lv_obj_t *cls, rec_labels_t *out)
{
    memset(out, 0, sizeof *out);
    int ri = (int)lv_dropdown_get_selected(room);
    int ti = (int)lv_dropdown_get_selected(teacher);
    int ci = (int)lv_dropdown_get_selected(cls);
    strlcpy(out->classroom_code, config_classroom_code(ri), sizeof out->classroom_code);
    strlcpy(out->classroom_label, config_classroom_label(ri), sizeof out->classroom_label);
    strlcpy(out->teacher, config_teacher(ti), sizeof out->teacher);
    strlcpy(out->class_code, config_class_code(ci), sizeof out->class_code);
}

static void persist_labels(const rec_labels_t *m)
{
    /* Deferred to the prefs task: no flash writes on the LVGL thread. */
    prefs_set_async("room", m->classroom_code);
    prefs_set_async("teacher", m->teacher);
    prefs_set_async("class", m->class_code);
}

static void apply_room_label(void)
{
    int i = (int)lv_dropdown_get_selected(ui.room_dd);
    lv_label_set_text(ui.room_value, config_classroom_label(i));
}

static void mark_hide(void);

static void show_home(void)
{
    lv_obj_add_flag(ui.modal, LV_OBJ_FLAG_HIDDEN);
    if (ui.mark) {
        mark_hide();
    }
    lv_obj_add_flag(ui.session, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(ui.home, LV_OBJ_FLAG_HIDDEN);
}

static void show_session(void)
{
    apply_room_label();
    lv_obj_add_flag(ui.modal, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(ui.home, LV_OBJ_FLAG_HIDDEN);
    lv_obj_remove_flag(ui.session, LV_OBJ_FLAG_HIDDEN);
}

static void sync_modal_from_session(void)
{
    s_sync = true;
    lv_dropdown_set_selected(ui.modal_room, lv_dropdown_get_selected(ui.room_dd));
    lv_dropdown_set_selected(ui.modal_teacher, lv_dropdown_get_selected(ui.teacher_dd));
    lv_dropdown_set_selected(ui.modal_class, lv_dropdown_get_selected(ui.class_dd));
    s_sync = false;
}

static void sync_session_from_modal(void)
{
    s_sync = true;
    lv_dropdown_set_selected(ui.room_dd, lv_dropdown_get_selected(ui.modal_room));
    lv_dropdown_set_selected(ui.teacher_dd, lv_dropdown_get_selected(ui.modal_teacher));
    lv_dropdown_set_selected(ui.class_dd, lv_dropdown_get_selected(ui.modal_class));
    s_sync = false;
    apply_room_label();
}

static void hide_message(void)
{
    if (ui.msgbox) {
        lv_obj_add_flag(ui.msgbox, LV_OBJ_FLAG_HIDDEN);
    }
    if (s_msg_timer) {
        lv_timer_pause(s_msg_timer);
    }
}

static void msg_timeout(lv_timer_t *t)
{
    (void)t;
    hide_message();
}

static void on_msg_ok(lv_event_t *e)
{
    (void)e;
    hide_message();
}

/* Short on-screen notice (auto-hides after 15 s). */
static void show_message(const char *title, const char *text)
{
    lv_label_set_text(ui.msg_title, title);
    lv_label_set_text(ui.msg_text, text ? text : "");
    lv_obj_remove_flag(ui.msgbox, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_to_index(ui.msgbox, -1);
    if (!s_msg_timer) {
        s_msg_timer = lv_timer_create(msg_timeout, 15000, NULL);
    }
    lv_timer_reset(s_msg_timer);
    lv_timer_resume(s_msg_timer);
}

static void start_failed(const char *reason)
{
    char line[176];
    snprintf(line, sizeof line, "Couldn't start recording: %s", reason);
    status_error(line); /* also ESP_LOGE */
    show_message("Couldn't start recording", reason);
}

static void on_record(lv_event_t *e)
{
    (void)e;
    char why[64];
    if (config_classroom_count() <= 0) {
        start_failed("no classrooms in the config");
        return;
    }
    if (!sd_can_record(why, sizeof why)) {
        start_failed(why);
        return;
    }
    {
        /* Option B (plaintext SD): no new recording while too much plaintext audio
         * is still on the card (pilot_guard.h). Connect Wi-Fi to upload. */
        upload_status_t ub;
        uploader_get_status(&ub);
        if (ub.backlog >= 2) {
            start_failed("too much audio waiting to upload; connect Wi-Fi first");
            return;
        }
    }
    rec_labels_t labels;
    read_labels(ui.room_dd, ui.teacher_dd, ui.class_dd, &labels);
    persist_labels(&labels);
    /* Ignore any error left over from an earlier attempt. */
    audio_status_t st;
    audio_get_status(&st);
    s_err_seen = st.err_seq;
    s_error_latched = false;
    s_start_announced = false;
    esp_err_t err = audio_rec_start(&labels);
    if (err == ESP_OK) {
#if !CONFIG_RECORDER_PILOT_NO_MEDIA
        qr_prefetch_arm(labels.class_code);
#endif
        show_session();
    } else {
        char r[64];
        snprintf(r, sizeof r, "recorder busy (%s)", esp_err_to_name(err));
        start_failed(r);
    }
}

static void on_pause(lv_event_t *e)
{
    (void)e;
    audio_status_t st;
    audio_get_status(&st);
    if (st.state == AUDIO_PAUSED) {
        audio_rec_resume();
    } else if (st.state == AUDIO_RECORDING) {
        audio_rec_pause();
    }
}

static void fmt_offset(char *dst, size_t n, uint32_t ms)
{
    fmt_hms(dst, n, ms);
}

static void toast_timeout(lv_timer_t *t)
{
    (void)t;
    if (s_toast) {
        lv_obj_add_flag(s_toast, LV_OBJ_FLAG_HIDDEN);
    }
    lv_timer_pause(s_toast_timer);
}

/* Brief notice near the bottom of the screen; hides itself after TOAST_MS. */
static void show_toast(const char *text)
{
    if (!s_toast) {
        int32_t w = lv_display_get_horizontal_resolution(NULL);
        s_toast = lv_obj_create(lv_layer_top());
        style_box(s_toast, COL_MODAL, 14);
        lv_obj_set_style_border_width(s_toast, 1, 0);
        lv_obj_set_style_border_color(s_toast, lv_color_hex(COL_AMBER), 0);
        lv_obj_set_style_pad_hor(s_toast, 16, 0);
        lv_obj_set_style_pad_ver(s_toast, 10, 0);
        lv_obj_remove_flag(s_toast, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_size(s_toast, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
        lv_obj_set_style_max_width(s_toast, w - 24, 0);
        s_toast_lbl = label(s_toast, "", UI_FONT_DD, 0xFFFFFF);
        lv_label_set_long_mode(s_toast_lbl, LV_LABEL_LONG_WRAP);
        lv_obj_set_style_max_width(s_toast_lbl, w - 24 - 32, 0);
        lv_obj_set_width(s_toast_lbl, LV_SIZE_CONTENT);
        lv_obj_align(s_toast, LV_ALIGN_BOTTOM_MID, 0, -16);
        s_toast_timer = lv_timer_create(toast_timeout, TOAST_MS, NULL);
    }
    lv_label_set_text(s_toast_lbl, text);
    lv_obj_remove_flag(s_toast, LV_OBJ_FLAG_HIDDEN);
    lv_obj_align(s_toast, LV_ALIGN_BOTTOM_MID, 0, -16);
    lv_timer_reset(s_toast_timer);
    lv_timer_resume(s_toast_timer);
}

static void mark_hide(void)
{
    s_mark_pending = -1;
    lv_obj_add_flag(ui.mark, LV_OBJ_FLAG_HIDDEN);
}

static void qr_open(void);   /* ui_qr.inc: Teacher Notes QR (0.7.2) */

/* Store marker i at the captured tap time and say so: activity line plus a
 * toast ("Incident marked at 1:02 PM"; Bug keeps its own toast). */
static void mark_store(int i, const audio_mark_time_t *t)
{
    const char *type = MARKER_TYPES[i].type;
    const char *shown = MARKER_TYPES[i].label;
    if (audio_rec_add_marker(type, t) != ESP_OK) {
        char line[176];
        snprintf(line, sizeof line, "Marker not saved: %s", shown);
        status_error(line);
        show_toast(line);
        return;
    }
    char line[176], at[24];
    bool wall = t->wall_valid;
    if (wall) {
        struct tm tm;
        localtime_r(&t->wall, &tm);
        clock_format_ampm(&tm, false, at, sizeof at);
    } else {
        fmt_offset(at, sizeof at, t->offset_ms);
    }
    snprintf(line, sizeof line, "Marker added: %s at %s", shown, at);
    status_set(line);
    if (strcmp(type, MARKER_TYPE_TEACHER_NOTES) == 0) {
#if CONFIG_RECORDER_PILOT_NO_MEDIA
        show_toast("Teacher Notes marked");   /* audio-only build, no photo/video QR */
#else
        qr_open();   /* phone photo / video for this note */
#endif
    } else if (strcmp(type, MARKER_TYPE_BUG) == 0) {
        show_toast(MARKER_BUG_TOAST);
    } else {
        snprintf(line, sizeof line, wall ? "%s marked at %s" : "%s marked %s into the recording", shown, at);
        show_toast(line);
    }
}

static void on_mark_btn(lv_event_t *e)
{
    int i = (int)(intptr_t)lv_event_get_user_data(e);
    if (i < 0 || i >= MARKER_TYPE_COUNT) {
        return;
    }
    uint32_t now = lv_tick_get();
    if (s_mark_last_tap[i] && lv_tick_elaps(s_mark_last_tap[i]) < MARK_DEBOUNCE_MS) {
        ESP_LOGI("ui", "%s tap ignored (debounce)", MARKER_TYPES[i].label);
        return;
    }
    s_mark_last_tap[i] = now ? now : 1;
    /* Timestamp NOW (the tap), not when OK is tapped. Recording keeps
     * running while the confirm card is open. */
    audio_mark_time_t t;
    if (!audio_rec_mark_now(&t)) {
        return;
    }
    ESP_LOGI("ui", "%s tapped at %lu ms", MARKER_TYPES[i].label, (unsigned long)t.offset_ms);
    if (!MARKER_TYPES[i].confirm_text) {
        mark_store(i, &t);
        return;
    }
    s_mark_time = t;
    s_mark_pending = i;
    char off[16], when[80];
    fmt_offset(off, sizeof off, t.offset_ms);
    if (t.wall_valid) {
        struct tm tm;
        char clk[24];
        localtime_r(&t.wall, &tm);
        clock_format_ampm(&tm, true, clk, sizeof clk);
        snprintf(when, sizeof when, UI_MARK_WHEN_FMT, clk, off);
    } else {
        snprintf(when, sizeof when, "Marked %s into the recording", off);
    }
    lv_label_set_text(ui.mark_title, MARKER_TYPES[i].confirm_title);
    lv_label_set_text(ui.mark_text, MARKER_TYPES[i].confirm_text);
    lv_label_set_text(ui.mark_when, when);
    lv_obj_remove_flag(ui.mark, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_to_index(ui.mark, -1);
}

static void on_mark_ok(lv_event_t *e)
{
    (void)e;
    int i = s_mark_pending;
    mark_hide();
    if (i < 0 || i >= MARKER_TYPE_COUNT) {
        return;
    }
    mark_store(i, &s_mark_time);
}

static void on_mark_cancel(lv_event_t *e)
{
    (void)e;
    int i = s_mark_pending;
    mark_hide();
    ESP_LOGI("ui", "%s cancelled, no marker stored", (i >= 0 && i < MARKER_TYPE_COUNT) ? MARKER_TYPES[i].label : "marker");
}

/* One big marker button (icon + label) on the recording screen; the
 * layout files place them. Disabled (grey) while the mic is starting or
 * the recording is saving. */
static lv_obj_t *make_mark_btn(lv_obj_t *parent, int i, int x, int y, int w, int h, int radius,
                               const lv_font_t *font)
{
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_set_pos(b, x, y);
    lv_obj_set_size(b, w, h);
    style_box(b, MARKER_TYPES[i].color, radius);
    lv_obj_set_style_border_width(b, 2, 0);
    lv_obj_set_style_border_color(b, lv_color_white(), 0);
    lv_obj_set_style_border_opa(b, LV_OPA_40, 0);
    lv_obj_set_style_bg_color(b, lv_color_hex(0x3A4458), LV_STATE_DISABLED);
    lv_obj_set_style_bg_color(b, lv_color_white(), LV_STATE_PRESSED);
    lv_obj_set_style_bg_opa(b, LV_OPA_COVER, LV_STATE_PRESSED);
    lv_obj_set_style_text_color(b, lv_color_hex(MARKER_TYPES[i].color), LV_STATE_PRESSED);
    lv_obj_add_event_cb(b, on_mark_btn, LV_EVENT_CLICKED, (void *)(intptr_t)i);
    char txt[48];
    snprintf(txt, sizeof txt, "%s  %s", MARKER_TYPES[i].icon, MARKER_TYPES[i].label);
    lv_obj_t *l = lv_label_create(b);
    lv_label_set_text(l, txt);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_center(l);
    ui.mark_btns[i] = b;
    return b;
}

static void on_end(lv_event_t *e)
{
    (void)e;
    /* Stop capturing the instant End is pressed; nothing is recorded
     * while the dialog waits. Keep recording resumes if it was running. */
    s_end_was_recording = audio_rec_hold();
    s_end_opened_ms = lv_tick_get();
    sync_modal_from_session();
    lv_label_set_text(ui.modal_countdown, "Autosaves in 4:00");
    lv_obj_remove_flag(ui.modal, LV_OBJ_FLAG_HIDDEN);
    /* Last child is painted on top. LVGL 9 has no lv_obj_move_foreground. */
    lv_obj_move_to_index(ui.modal, -1);
}

static void on_keep(lv_event_t *e)
{
    (void)e;
    if (s_save_pending) {
        return;
    }
    sync_session_from_modal();
    rec_labels_t labels;
    read_labels(ui.room_dd, ui.teacher_dd, ui.class_dd, &labels);
    persist_labels(&labels);
    lv_obj_add_flag(ui.modal, LV_OBJ_FLAG_HIDDEN);
    if (s_end_was_recording) {
        audio_rec_resume();
        s_end_was_recording = false;
    }
}

static void do_save(bool autosave)
{
    if (s_save_pending) {
        return;
    }
    sync_session_from_modal();
    rec_labels_t labels;
    read_labels(ui.modal_room, ui.modal_teacher, ui.modal_class, &labels);
    persist_labels(&labels);
    if (autosave) {
        char line[176];
        snprintf(line, sizeof line, "Autosaving recording %s %s",
                 labels.class_code[0] ? labels.class_code : labels.classroom_label, s_autosave_why);
        status_set(line);
    }
    if (audio_rec_save(&labels) == ESP_OK) {
        s_save_pending = true;
        s_end_was_recording = false;
        lv_label_set_text(ui.save_lbl, "Saving...");
        lv_label_set_text(ui.modal_countdown, autosave ? "Autosaving..." : "");
        lv_obj_add_state(ui.save_btn, LV_STATE_DISABLED);
        lv_obj_add_state(ui.keep_btn, LV_STATE_DISABLED);
    }
}

static void on_save(lv_event_t *e)
{
    (void)e;
    do_save(false);
}

static void on_choice(lv_event_t *e)
{
    if (s_sync) {
        return;
    }
    lv_obj_t *dd = lv_event_get_target(e);
    if (dd == ui.modal_room || dd == ui.modal_teacher || dd == ui.modal_class) {
        sync_session_from_modal();
    }
    rec_labels_t labels;
    read_labels(ui.room_dd, ui.teacher_dd, ui.class_dd, &labels);
    persist_labels(&labels);
    apply_room_label();
}

static void paint_wifi(lv_obj_t **bars, lv_obj_t *xl, const wifi_status_t *w)
{
    lv_obj_t *box = lv_obj_get_parent(bars[0]);
    bool down = !w->connected && !w->connecting;
    if (w->connecting) {
        paint_bars_col(bars, 1 + (int)((lv_tick_get() / 400) % 4), COL_AMBER);
    } else if (w->connected) {
        paint_bars_col(bars, w->bars > 0 ? w->bars : 1, COL_GREEN);
    } else {
        paint_bars_col(bars, 0, COL_GREEN);
    }
    if (down) {
        lv_obj_remove_flag(xl, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(xl, LV_OBJ_FLAG_HIDDEN);
    }
    lv_obj_align(box, LV_ALIGN_CENTER, down ? -WIFI_X_SHIFT : 0, 0);
}

static void paint_meter(uint8_t level, bool paused)
{
    int lit = (level * METER_BARS) / 100;
    if (level > 0 && lit == 0) {
        lit = 1;
    }
    for (int i = 0; i < METER_BARS; i++) {
        uint32_t col = COL_OFF;
        if (i < lit) {
            if (paused) {
                col = 0x5C6778;
            } else if (i >= (METER_BARS * 3) / 4) {
                col = COL_YELLOW;
            } else {
                col = COL_GREEN;
            }
        }
        lv_obj_set_style_bg_color(ui.meter[i], lv_color_hex(col), 0);
    }
    if (paused) {
        lv_label_set_text(ui.meter_state, "Paused");
        lv_obj_set_style_text_color(ui.meter_state, lv_color_hex(COL_MUTED), 0);
    } else if (lit >= 3) {
        lv_label_set_text(ui.meter_state, "Picking up sound");
        lv_obj_set_style_text_color(ui.meter_state, lv_color_hex(COL_GREEN), 0);
    } else {
        lv_label_set_text(ui.meter_state, "Quiet");
        lv_obj_set_style_text_color(ui.meter_state, lv_color_hex(COL_MUTED), 0);
    }
}

/* Returns true while the backlight is dimmed (battery gauge load hint). */
static bool update_backlight(bool session_active)
{
    /* 0xFF is never a valid level, so the first call always sends. */
    static uint16_t applied = 0xFF;
    static uint32_t ui_start_ms;
    if (!ui_start_ms) {
        ui_start_ms = lv_tick_get() ? lv_tick_get() : 1;
    }
    uint8_t want = BOARD_CMD_BACKLIGHT_MAX;
    if (!session_active && display_touch_ok()) {
        /* Dim only after 60 s of real inactivity counted from UI start,
         * never because of time spent booting. */
        uint32_t idle = lv_display_get_inactive_time(NULL);
        uint32_t since_ui = lv_tick_elaps(ui_start_ms);
        if (idle > since_ui) {
            idle = since_ui;
        }
        if (idle >= BOARD_DIM_AFTER_MS) {
            want = BOARD_BACKLIGHT_DIM;
        }
    }
    if (want != applied) {
        applied = want;
        board_ctrl_force_backlight(want, want == BOARD_CMD_BACKLIGHT_MAX ? "ui full" : "ui dim");
    }
    return want != BOARD_CMD_BACKLIGHT_MAX;
}

static void fmt_entry_time(const status_entry_t *e, char *out, size_t n)
{
    if (e->wall_valid) {
        struct tm t;
        localtime_r(&e->wall, &t);
        clock_format_ampm(&t, false, out, n);
    } else {
        /* Clock not set: minutes:seconds since power-on. */
        uint32_t s = e->boot_s;
        if (s < 3600) {
            snprintf(out, n, "+%lu:%02lu", (unsigned long)(s / 60), (unsigned long)(s % 60));
        } else {
            snprintf(out, n, "+%luh%02lu", (unsigned long)(s / 3600), (unsigned long)((s / 60) % 60));
        }
    }
}

static void paint_activity(void)
{
    uint32_t v = status_version();
    if (v == s_act_version) {
        return;
    }
    s_act_version = v;
    status_entry_t e[ACT_ROWS];
    int n = status_recent(e, ACT_ROWS);
    bool any_boot_time = false;
    int y = s_act_y0;
    for (int i = 0; i < ACT_ROWS; i++) {
        lv_obj_t *tl = ui.act_time[i], *ml = ui.act_msg[i];
        if (i >= n || y + s_act_pitch > s_act_band_h) {
            lv_label_set_text(tl, "");
            lv_label_set_text(ml, "");
            lv_obj_add_flag(tl, LV_OBJ_FLAG_HIDDEN);
            lv_obj_add_flag(ml, LV_OBJ_FLAG_HIDDEN);
            continue;
        }
        char tbuf[16];
        fmt_entry_time(&e[i], tbuf, sizeof tbuf);
        any_boot_time |= !e[i].wall_valid;
        /* Lines this message needs at the row width (at most ACT_MAX_LINES,
         * and never more than the band has left); the last line ends in
         * "..." only if even that is not enough. */
        const lv_font_t *f = lv_obj_get_style_text_font(ml, 0);
        int32_t lh = lv_font_get_line_height(f);
        lv_point_t sz;
        lv_text_get_size(&sz, e[i].msg, f, 0, s_act_pitch - lh, s_act_msg_w, LV_TEXT_FLAG_NONE);
        int lines = (int)((sz.y + s_act_pitch - lh + s_act_pitch - 1) / s_act_pitch);
        int room = (s_act_band_h - y) / s_act_pitch;
        if (lines < 1) {
            lines = 1;
        }
        if (lines > ACT_MAX_LINES) {
            lines = ACT_MAX_LINES;
        }
        if (lines > room) {
            lines = room;
        }
        lv_obj_set_y(tl, y);
        lv_obj_set_y(ml, y);
        /* n lines at a pitch of s_act_pitch: the last line is one font line tall */
        lv_obj_set_height(ml, (lines - 1) * s_act_pitch + lh);
        lv_label_set_text(tl, tbuf);
        lv_label_set_text(ml, e[i].msg);
        lv_obj_set_style_text_color(ml, lv_color_hex(e[i].error ? 0xFF8A80 : 0xFFFFFF), 0);
        lv_obj_remove_flag(tl, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(ml, LV_OBJ_FLAG_HIDDEN);
        y += lines * s_act_pitch;
    }
    lv_label_set_text(ui.act_title, any_boot_time ? "Recent activity  (+m:ss = time since power-on, clock not set)"
                                                  : "Recent activity");
}

static void paint_upload(void)
{
    upload_status_t u;
    uploader_get_status(&u);
    char t[48];
    uint32_t col = COL_MUTED;
    if (u.busy) {
        snprintf(t, sizeof t, LV_SYMBOL_UPLOAD "  Uploading %d%%", u.pct);
        col = COL_BLUE;
    } else if (u.no_token) {
        snprintf(t, sizeof t, "Uploads off: no device token");
    } else if (!u.enabled) {
        snprintf(t, sizeof t, "Uploads off");
    } else if (u.waiting > 0 && u.backlog >= 1) {
        snprintf(t, sizeof t, LV_SYMBOL_WARNING " %d min unsent: %s", (int)(u.backlog_audio_s / 60) % 10000,
                 u.backlog >= 2 ? "rec paused" : "connect Wi-Fi");
        col = u.backlog >= 2 ? COL_RED : COL_AMBER;
    } else if (u.waiting > 0) {
        snprintf(t, sizeof t, "%d waiting to upload%s", u.waiting, u.retry_wait ? " (retrying)" : "");
        col = COL_AMBER;
    } else {
        snprintf(t, sizeof t, LV_SYMBOL_OK "  All recordings uploaded");
        col = COL_GREEN;
    }
    lv_label_set_text(ui.upload_lbl, t);
    lv_obj_set_style_text_color(ui.upload_lbl, lv_color_hex(col), 0);
}

#if CONFIG_RECORDER_BOARD_HOSYOND_35
/* ---- Battery gauge chip (0.7.1) ----------------------------------------
 * Top-right chip on home and session: battery outline with a fill bar
 * (green > 25%, yellow 10-25%, red < 10%), no number; a lightning bolt on
 * top when on USB power (computer connected or charging detected); empty
 * outline + bolt when no battery is fitted. See main/batt_gauge.h. */
static void set_text_if(lv_obj_t *l, const char *t)
{
    if (strcmp(lv_label_get_text(l), t) != 0) {
        lv_label_set_text(l, t);
    }
}

static void paint_power(const battery_status_t *b)
{
    batt_gauge_view_t v = batt_gauge_view(b->src, b->pct, b->vbat_mv);
    for (int i = 0; i < s_pwr_n; i++) {
        batt_gauge_paint(&s_gauge[i], v);
    }
}

static bool batt_critical(const battery_status_t *b)
{
    return b->src == BATT_SRC_BATTERY && b->pct >= 0 && b->pct <= BATT_CRIT_PCT;
}

/* Warning below 10% (when the gauge turns red; once per low spell) and, if CONFIG_RECORDER_BATTERY_AUTOSAVE,
 * Save & finish at 5%. */
static void battery_tick(const battery_status_t *b, const audio_status_t *audio)
{
    bool low = b->src == BATT_SRC_BATTERY && b->pct >= 0 && b->pct < BATT_RED_BELOW_PCT;
    if (low && !s_batt_low_warned) {
        s_batt_low_warned = true;
        char line[64], text[160];
        snprintf(line, sizeof line, "Battery low: %d%%", b->pct);
        status_error(line);
#if CONFIG_RECORDER_BATTERY_AUTOSAVE
        snprintf(text, sizeof text, "%d%% left. Plug in USB power soon. At %d%% a recording is saved and finished "
                 "automatically.", b->pct, BATT_CRIT_PCT);
#else
        snprintf(text, sizeof text, "%d%% left. Plug in USB power soon.", b->pct);
#endif
        show_message("Battery low", text);
    } else if (b->src != BATT_SRC_BATTERY || b->pct >= BATT_RED_BELOW_PCT + 5) {
        s_batt_low_warned = false;
    }
#if CONFIG_RECORDER_BATTERY_AUTOSAVE
    if (batt_critical(b) && !s_batt_crit_done && !s_save_pending &&
        (audio->state == AUDIO_RECORDING || audio->state == AUDIO_PAUSED)) {
        s_batt_crit_done = true;
        ESP_LOGW("ui", "battery at %d%% (%d mV): auto Save & finish", b->pct, b->smooth_mv);
        mark_hide();
        if (lv_obj_has_flag(ui.modal, LV_OBJ_FLAG_HIDDEN)) {
            on_end(NULL);
        }
        s_autosave_why = "because the battery is at 5%";
        do_save(true);
        show_message("Battery at 5%", "The recording was saved and finished. Plug in USB power to record again.");
    } else if (b->src != BATT_SRC_BATTERY || b->pct > BATT_CRIT_PCT + 5) {
        s_batt_crit_done = false;
    }
#else
    (void)audio;
#endif
}

/* ---- Auto sleep (0.7.0) -------------------------------------------------- */
static void sleep_cancel(const char *why)
{
    if (s_sleep_state == SLEEP_NONE) {
        return;
    }
    ESP_LOGI("ui", "auto sleep cancelled: %s", why);
    if (s_sleep_state == SLEEP_GOING) {
        s_sleep_cancel = true;
        uploader_hold(false);
        status_set("Staying on");
    }
    s_sleep_state = SLEEP_NONE;
    lv_obj_add_flag(s_sleep_ov, LV_OBJ_FLAG_HIDDEN);
}

static void on_sleep_tap(lv_event_t *e)
{
    (void)e;
    sleep_cancel("tap");
}

static void sleep_task(void *arg)
{
    (void)arg;
    uint32_t waited = 0;
    while (!s_sleep_cancel && !uploader_idle() && waited < SLEEP_UPLOAD_WAIT_MS) {
        vTaskDelay(pdMS_TO_TICKS(500));
        waited += 500;
    }
    if (!uploader_idle()) {
        ESP_LOGW("ui", "upload still running after %lu min: sleeping anyway (file stays on SD)",
                 (unsigned long)(SLEEP_UPLOAD_WAIT_MS / 60000UL));
    }
    if (!s_sleep_cancel && display_lock(2000)) {
        if (!s_sleep_cancel) {
            power_deep_sleep(); /* does not return */
        }
        display_unlock();
    }
    s_sleep_task_running = false;
    vTaskDelete(NULL);
}

static void build_sleep_overlay(void)
{
    s_sleep_ov = lv_obj_create(lv_layer_top());
    lv_obj_set_size(s_sleep_ov, BOARD_UI_W, BOARD_UI_H);
    lv_obj_set_pos(s_sleep_ov, 0, 0);
    style_box(s_sleep_ov, 0x000000, 0);
    lv_obj_set_style_bg_opa(s_sleep_ov, LV_OPA_80, 0);
    lv_obj_add_flag(s_sleep_ov, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(s_sleep_ov, on_sleep_tap, LV_EVENT_CLICKED, NULL);
    lv_obj_t *card = lv_obj_create(s_sleep_ov);
    lv_obj_set_size(card, 360, 150);
    lv_obj_center(card);
    style_box(card, COL_MODAL, 12);
    lv_obj_set_style_border_width(card, 1, 0);
    lv_obj_set_style_border_color(card, lv_color_hex(COL_LINE), 0);
    lv_obj_remove_flag(card, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_t *t = label(card, "Going to sleep", &lv_font_montserrat_20, 0xFFFFFF);
    lv_obj_align(t, LV_ALIGN_TOP_MID, 0, 18);
    s_sleep_text = label(card, "", &lv_font_montserrat_16, COL_MUTED);
    lv_obj_set_width(s_sleep_text, 330);
    lv_obj_set_style_text_align(s_sleep_text, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(s_sleep_text, LV_ALIGN_TOP_MID, 0, 58);
    lv_obj_add_flag(s_sleep_ov, LV_OBJ_FLAG_HIDDEN);
}

/* eligible: no session, not saving, no dialog, not plugged into a computer. */
static void sleep_tick(bool eligible)
{
#if CONFIG_RECORDER_AUTO_SLEEP_MIN > 0
    const uint32_t timeout = (uint32_t)CONFIG_RECORDER_AUTO_SLEEP_MIN * 60000UL;
    uint32_t idle = lv_display_get_inactive_time(NULL);
    char t[96];
    switch (s_sleep_state) {
    case SLEEP_NONE:
        if (eligible && !s_sleep_task_running && idle + SLEEP_WARN_MS >= timeout) {
            ESP_LOGI("ui", "idle %lu min: sleep warning", (unsigned long)(idle / 60000UL));
            s_sleep_state = SLEEP_WARN;
            lv_obj_remove_flag(s_sleep_ov, LV_OBJ_FLAG_HIDDEN);
        }
        break;
    case SLEEP_WARN:
        if (!eligible) {
            sleep_cancel("recording or computer");
            break;
        }
        if (idle + SLEEP_WARN_MS < timeout) {
            sleep_cancel("touch");
            break;
        }
        if (idle >= timeout) {
            s_sleep_state = SLEEP_GOING;
            s_sleep_cancel = false;
            s_sleep_task_running = true;
            uploader_hold(true);
            snprintf(t, sizeof t, "Going to sleep after %d min idle", CONFIG_RECORDER_AUTO_SLEEP_MIN);
            status_set(t);
            if (xTaskCreate(sleep_task, "sleep", 4096, NULL, 2, NULL) != pdPASS) {
                s_sleep_task_running = false;
                sleep_cancel("task create failed");
                break;
            }
            set_text_if(s_sleep_text, "Turning off...\nPress BOOT to wake it up.");
        } else {
            uint32_t left = (timeout - idle + 999) / 1000;
            snprintf(t, sizeof t, "In %lu s.  Tap to stay on.\nPress BOOT to wake it up later.", (unsigned long)left);
            set_text_if(s_sleep_text, t);
        }
        break;
    case SLEEP_GOING:
        if (idle < timeout) {
            sleep_cancel("touch");
        } else if (!uploader_idle()) {
            set_text_if(s_sleep_text, "Finishing an upload first...\nTap to stay on.");
        }
        break;
    }
#else
    (void)eligible;
#endif
}
#endif /* CONFIG_RECORDER_BOARD_HOSYOND_35 */

static void ui_tick(lv_timer_t *timer)
{
    (void)timer;
    struct tm now;
    bool time_ok = rtc_now(&now);
    if (time_ok) {
        char tbuf[24], dbuf[24];
        clock_format_ampm(&now, false, tbuf, sizeof tbuf);
        clock_format_date(&now, dbuf, sizeof dbuf);
        lv_obj_set_style_text_font(ui.clock_time, UI_FONT_CLOCK, 0);
        lv_label_set_text(ui.clock_time, tbuf);
        lv_label_set_text(ui.clock_date, dbuf);
    } else {
        lv_obj_set_style_text_font(ui.clock_time, UI_FONT_CLOCK_NOTIME, 0);
        lv_label_set_text(ui.clock_time, "Time not set");
        lv_label_set_text(ui.clock_date, UI_NO_TIME_HINT);
    }

    if (++s_space_ticks >= 25) {
        s_space_ticks = 0;
        sd_refresh_async();
    }
    uint32_t free_h = 0, total_h = 0;
    sd_space(&free_h, &total_h, NULL);
    char num[16], line[48];
    fmt_commas(num, sizeof num, free_h);
    snprintf(line, sizeof line, "%s hours free", num);
    lv_label_set_text(ui.storage_main, sd_mounted() ? line : "Not available");
    fmt_commas(num, sizeof num, total_h);
    snprintf(line, sizeof line, " of %s", num);
    lv_label_set_text(ui.storage_of, sd_mounted() ? line : "");
    int pct = (sd_mounted() && total_h > 0) ? (int)((free_h * 100ULL) / (total_h ? total_h : 1)) : 0;
    if (pct > 100) {
        pct = 100;
    }
    lv_bar_set_value(ui.storage_bar, sd_mounted() ? pct : 0, LV_ANIM_OFF);

    paint_upload();

    char why[64];
    bool can = sd_can_record(why, sizeof why) && config_classroom_count() > 0;
#if CONFIG_RECORDER_BOARD_HOSYOND_35
    battery_status_t batt;
    battery_get(&batt);
    paint_power(&batt);
#if CONFIG_RECORDER_BATTERY_AUTOSAVE
    if (can && batt_critical(&batt)) {
        can = false;
        snprintf(why, sizeof why, "Battery low: plug in USB power");
    }
#endif
#endif
    if (can) {
        lv_obj_remove_state(ui.record_btn, LV_STATE_DISABLED);
        lv_obj_set_style_bg_color(ui.record_btn, lv_color_hex(COL_RED), 0);
        lv_label_set_text(ui.record_hint, "Tap to start a session");
    } else {
        lv_obj_add_state(ui.record_btn, LV_STATE_DISABLED);
        lv_obj_set_style_bg_color(ui.record_btn, lv_color_hex(0x6E2A28), LV_STATE_DISABLED);
        lv_label_set_text(ui.record_hint, why[0] ? why : "No classrooms in config");
    }

    if (sd_mounted() && sd_space_low()) {
        if (!s_low_announced) {
            s_low_announced = true;
            status_error("SD card is almost full");
        }
    } else {
        s_low_announced = false;
    }

    wifi_status_t wifi;
    wifi_get_status(&wifi);
    paint_wifi(ui.home_bars, ui.home_wifi_x, &wifi);
    paint_wifi(ui.ses_bars, ui.ses_wifi_x, &wifi);

    audio_status_t audio;
    audio_get_status(&audio);
    bool session_active = audio.state == AUDIO_STARTING || audio.state == AUDIO_RECORDING ||
                          audio.state == AUDIO_PAUSED || audio.state == AUDIO_SAVING ||
                          !lv_obj_has_flag(ui.modal, LV_OBJ_FLAG_HIDDEN) ||
                          !lv_obj_has_flag(ui.mark, LV_OBJ_FLAG_HIDDEN);
#if CONFIG_RECORDER_BOARD_HOSYOND_35
    bool dimmed = update_backlight(session_active || s_sleep_state != SLEEP_NONE);
    upload_status_t up;
    uploader_get_status(&up);
    bool heavy = audio.state == AUDIO_STARTING || audio.state == AUDIO_RECORDING || audio.state == AUDIO_SAVING ||
                 up.busy;
    battery_set_load(heavy ? BATT_LOAD_HEAVY : (dimmed ? BATT_LOAD_DIM : BATT_LOAD_IDLE));
    battery_tick(&batt, &audio);
    sleep_tick(!session_active && audio.state != AUDIO_SAVING && !s_save_pending &&
               batt.src != BATT_SRC_USB_HOST);
#else
    update_backlight(session_active);
#endif
    if (audio.state == AUDIO_RECORDING && !s_start_announced) {
        s_start_announced = true;
        char line[176];
        snprintf(line, sizeof line, "Recording started: %s",
                 config_classroom_label((int)lv_dropdown_get_selected(ui.room_dd)));
        status_set(line);
    }
    paint_activity();

    bool on_session = !lv_obj_has_flag(ui.session, LV_OBJ_FLAG_HIDDEN);
    if (audio.state == AUDIO_ERROR && !s_error_latched && audio.err_seq != s_err_seen) {
        s_error_latched = true;
        s_err_seen = audio.err_seq;
        const char *why = audio.err[0] ? audio.err : "unknown error";
        if (s_save_pending) {
            char line[176];
            snprintf(line, sizeof line, "Saving failed: %s", why);
            status_error(line);
            show_message("Couldn't save the recording", why);
        } else if (audio.recorded_ms == 0) {
            start_failed(why);
        } else {
            char line[176];
            snprintf(line, sizeof line, "Recording stopped: %s", why);
            status_error(line);
            show_message("Recording stopped", why);
        }
        s_save_pending = false;
        lv_label_set_text(ui.save_lbl, "Save & finish");
        lv_obj_remove_state(ui.save_btn, LV_STATE_DISABLED);
        lv_obj_remove_state(ui.keep_btn, LV_STATE_DISABLED);
        show_home();
        on_session = false;
    } else if (audio.state != AUDIO_ERROR) {
        s_error_latched = false;
    }

    if (s_save_pending && audio.state == AUDIO_IDLE) {
        s_save_pending = false;
        char when[24] = "unknown time";
        if (audio.time_valid) {
            clock_format_ampm(&audio.start_local, false, when, sizeof when);
        }
        unsigned long mins = audio.recorded_ms / 60000UL;
        char saved[80];
        char dur[24];
        if (audio.recorded_ms > 0 && mins == 0) {
            snprintf(saved, sizeof saved, "Last recording %s (<1 min)", when);
            snprintf(dur, sizeof dur, "under 1 min");
        } else {
            snprintf(saved, sizeof saved, "Last recording %s (%lu min)", when, mins);
            snprintf(dur, sizeof dur, "%lu min", mins);
        }
        prefs_set_async("last_rec", saved);
        char line[176];
        snprintf(line, sizeof line, "Recording saved: %s, %s",
                 config_classroom_label((int)lv_dropdown_get_selected(ui.room_dd)), dur);
        status_set(line);
        lv_label_set_text(ui.save_lbl, "Save & finish");
        lv_obj_remove_state(ui.save_btn, LV_STATE_DISABLED);
        lv_obj_remove_state(ui.keep_btn, LV_STATE_DISABLED);
        show_home();
        on_session = false;
    }

    if (!on_session) {
        return;
    }

    bool paused = audio.state == AUDIO_PAUSED;
    bool starting = audio.state == AUDIO_STARTING;
    lv_obj_set_style_border_color(ui.session_card, lv_color_hex(paused ? COL_AMBER : COL_RED), 0);
    lv_obj_set_style_bg_color(ui.badge, lv_color_hex(paused ? 0x3A2E0A : COL_RED), 0);
    lv_label_set_text(ui.badge_lbl, paused ? (LV_SYMBOL_PAUSE "  Paused") : (LV_SYMBOL_AUDIO "  Recording"));
    lv_obj_set_style_text_color(ui.badge_lbl, lv_color_hex(paused ? COL_AMBER : 0xFFFFFF), 0);

    if (starting || !audio.time_valid) {
        lv_label_set_text(ui.started, starting ? "Starting microphone..." :
                          (paused ? "Time was not set - currently paused" : "Time was not set"));
    } else {
        char started[64];
        char clock[32];
        clock_format_ampm(&audio.start_local, true, clock, sizeof clock);
        snprintf(started, sizeof started, paused ? "Recording started at %s - currently paused"
                                                 : "Recording started at %s", clock);
        lv_label_set_text(ui.started, started);
    }
    char hms[16];
    fmt_hms(hms, sizeof hms, audio.recorded_ms);
    lv_label_set_text(ui.timer, hms);
    lv_obj_set_style_text_color(ui.timer, lv_color_hex(paused ? 0x8E99AB : 0xFFFFFF), 0);
    paint_meter(audio.level, paused || starting);

    if (paused) {
        lv_label_set_text(ui.pause_lbl, LV_SYMBOL_PLAY "  Resume");
    } else {
        lv_label_set_text(ui.pause_lbl, LV_SYMBOL_PAUSE "  Pause");
    }
    if (starting || audio.state == AUDIO_SAVING) {
        lv_obj_add_state(ui.pause_btn, LV_STATE_DISABLED);
    } else {
        lv_obj_remove_state(ui.pause_btn, LV_STATE_DISABLED);
    }
    for (int i = 0; i < MARKER_TYPE_COUNT; i++) {
        if (starting || audio.state == AUDIO_SAVING) {
            lv_obj_add_state(ui.mark_btns[i], LV_STATE_DISABLED);
        } else {
            lv_obj_remove_state(ui.mark_btns[i], LV_STATE_DISABLED);
        }
    }

    if (!lv_obj_has_flag(ui.modal, LV_OBJ_FLAG_HIDDEN)) {
        char dur[96];
        char clock[32] = "time not set";
        if (audio.time_valid) {
            clock_format_ampm(&audio.start_local, true, clock, sizeof clock);
        }
        fmt_hms(hms, sizeof hms, audio.recorded_ms);
        snprintf(dur, sizeof dur, "Duration %s  -  started %s", hms, clock);
        lv_label_set_text(ui.modal_dur, dur);
        if (!s_save_pending) {
            uint32_t open_ms = lv_tick_elaps(s_end_opened_ms);
            if (open_ms >= END_TIMEOUT_MS) {
                ESP_LOGW("ui", "end dialog timed out after %lu s: autosaving", (unsigned long)(open_ms / 1000));
                s_autosave_why = "after no response";
                do_save(true);
            } else {
                uint32_t left = (END_TIMEOUT_MS - open_ms + 999) / 1000;
                char cd[40];
                snprintf(cd, sizeof cd, "Autosaves in %lu:%02lu", (unsigned long)(left / 60),
                         (unsigned long)(left % 60));
                lv_label_set_text(ui.modal_countdown, cd);
            }
        }
    }
}

#if !CONFIG_RECORDER_BOARD_HOSYOND_35
static lv_obj_t *screen_root(void)
{
    lv_obj_t *root = lv_obj_create(lv_screen_active());
    lv_obj_set_size(root, 800, 480);
    lv_obj_set_pos(root, 0, 0);
    style_box(root, COL_BG, 0);
    return root;
}

static void build_home(void)
{
    ui.home = screen_root();
    /* 0.6.3: the Wi-Fi chip is just the signal icon (x 546..630), so the
     * brand block may run to x 538 (it ended at 312 when the chip showed
     * the network name). */
    add_brand(ui.home, NULL, 640 - 10 - WIFI_CHIP_W - 8);
    ui.home_wifi = build_wifi_chip(ui.home, 640 - 10 - WIFI_CHIP_W, 12, 44, 12, ui.home_bars, &ui.home_wifi_x);
    usb_chip(ui.home, 640, 12);

    ui.clock_time = label(ui.home, "Time not set", &lv_font_montserrat_48, 0xFFFFFF);
    /* Time and date centred in the left column (x 16..424, the dropdown's width). */
    lv_obj_set_pos(ui.clock_time, 16, 74);
    lv_obj_set_width(ui.clock_time, 408);
    lv_label_set_long_mode(ui.clock_time, LV_LABEL_LONG_CLIP);
    lv_obj_set_style_text_align(ui.clock_time, LV_TEXT_ALIGN_CENTER, 0);
    ui.clock_date = label(ui.home, "", &lv_font_montserrat_20, COL_MUTED);
    lv_obj_set_pos(ui.clock_date, 16, 140);
    lv_obj_set_width(ui.clock_date, 408);
    lv_label_set_long_mode(ui.clock_date, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(ui.clock_date, LV_TEXT_ALIGN_CENTER, 0);

    label(ui.home, "Classroom", &lv_font_montserrat_16, COL_MUTED);
    lv_obj_t *cl = lv_obj_get_child(ui.home, -1);
    lv_obj_set_pos(cl, 18, 184);

    ui.room_dd = make_dropdown(ui.home, s_room_opts);
    lv_obj_set_pos(ui.room_dd, 16, 210);
    lv_obj_set_size(ui.room_dd, 408, 54);
    lv_obj_add_event_cb(ui.room_dd, on_choice, LV_EVENT_VALUE_CHANGED, NULL);

    /* Record button: two-thirds of the old 344 x 324, centred in the old
     * right column (x 440..784), top-aligned with the clock. y 72..288. */
    ui.record_btn = lv_button_create(ui.home);
    lv_obj_set_pos(ui.record_btn, 497, 72);
    lv_obj_set_size(ui.record_btn, 230, 216);
    style_box(ui.record_btn, COL_RED, 16);
    lv_obj_set_flex_flow(ui.record_btn, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(ui.record_btn, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_row(ui.record_btn, 8, 0);
    lv_obj_add_event_cb(ui.record_btn, on_record, LV_EVENT_CLICKED, NULL);

    lv_obj_t *ring = lv_obj_create(ui.record_btn);
    lv_obj_set_size(ring, 76, 76);
    style_box(ring, COL_RED, LV_RADIUS_CIRCLE);
    lv_obj_set_style_bg_opa(ring, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(ring, 10, 0);
    lv_obj_set_style_border_color(ring, lv_color_white(), 0);
    lv_obj_remove_flag(ring, LV_OBJ_FLAG_CLICKABLE);

    label(ui.record_btn, "Record", &lv_font_montserrat_28, 0xFFFFFF);
    ui.record_hint = label(ui.record_btn, "Tap to start a session", &lv_font_montserrat_16, COL_PINK);
    lv_obj_set_width(ui.record_hint, 206);
    lv_label_set_long_mode(ui.record_hint, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_align(ui.record_hint, LV_TEXT_ALIGN_CENTER, 0);

    /* Storage: full width (16..784), y 300..364, below dropdown and button. */
    lv_obj_t *card = lv_obj_create(ui.home);
    lv_obj_set_pos(card, 16, 300);
    lv_obj_set_size(card, 768, 64);
    style_box(card, COL_CARD, 12);
    lv_obj_t *st_lbl = label(card, "Storage", &lv_font_montserrat_16, COL_MUTED);
    lv_obj_set_pos(st_lbl, 16, 10);

    lv_obj_t *row = lv_obj_create(card);
    lv_obj_set_pos(row, 100, 4);
    lv_obj_set_size(row, 400, 28);
    style_box(row, COL_CARD, 0);
    lv_obj_set_style_bg_opa(row, LV_OPA_TRANSP, 0);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_END, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(row, 0, 0);
    lv_obj_set_scrollbar_mode(row, LV_SCROLLBAR_MODE_OFF);
    ui.storage_main = label(row, "-", &lv_font_montserrat_20, 0xFFFFFF);
    ui.storage_of = label(row, "", &lv_font_montserrat_20, COL_MUTED);

    /* Upload state, top-right of the storage card. */
    ui.upload_lbl = label(card, "", &lv_font_montserrat_16, COL_MUTED);
    lv_obj_set_pos(ui.upload_lbl, 452, 10);
    lv_obj_set_width(ui.upload_lbl, 300);
    lv_label_set_long_mode(ui.upload_lbl, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(ui.upload_lbl, LV_TEXT_ALIGN_RIGHT, 0);

    ui.storage_bar = lv_bar_create(card);
    lv_obj_set_pos(ui.storage_bar, 16, 40);
    lv_obj_set_size(ui.storage_bar, 736, 12);
    lv_bar_set_range(ui.storage_bar, 0, 100);
    lv_obj_set_style_bg_color(ui.storage_bar, lv_color_hex(COL_LINE), LV_PART_MAIN);
    lv_obj_set_style_bg_color(ui.storage_bar, lv_color_hex(COL_BLUE), LV_PART_INDICATOR);
    lv_obj_set_style_radius(ui.storage_bar, 6, LV_PART_MAIN);
    lv_obj_set_style_radius(ui.storage_bar, 6, LV_PART_INDICATOR);

    /* Recent activity: y 372..480. Title + 4 rows of 20 px, newest first
     * (a long message wraps onto 2 rows and the oldest row drops off). */
    lv_obj_t *bar = lv_obj_create(ui.home);
    lv_obj_set_pos(bar, 0, 372);
    lv_obj_set_size(bar, 800, 108);
    style_box(bar, COL_STATUS, 0);
    /* Title x 16..712; firmware version right-aligned in x 720..784 on the
     * same line (y 377..393), clear of the activity rows below (y 397+). */
    ui.act_title = label(bar, "Recent activity", &lv_font_montserrat_14, COL_MUTED);
    lv_obj_set_pos(ui.act_title, 16, 5);
    lv_obj_set_width(ui.act_title, 768 - 72);
    lv_label_set_long_mode(ui.act_title, LV_LABEL_LONG_DOT);
    version_caption(bar, &lv_font_montserrat_14, 64, 16, 5);
    /* Rows of 20 px from y 25; a long message takes two rows (wrapped, 20 px
     * line pitch) and pushes older ones down, see paint_activity(). */
    s_act_y0 = 25;
    s_act_pitch = 20;
    s_act_band_h = 108;
    s_act_msg_w = 680;
    for (int i = 0; i < ACT_ROWS; i++) {
        int y = s_act_y0 + i * s_act_pitch;
        ui.act_time[i] = label(bar, "", &lv_font_montserrat_16, COL_MUTED);
        lv_obj_set_pos(ui.act_time[i], 16, y);
        lv_obj_set_width(ui.act_time[i], 84);
        lv_label_set_long_mode(ui.act_time[i], LV_LABEL_LONG_CLIP);
        ui.act_msg[i] = label(bar, "", &lv_font_montserrat_16, 0xFFFFFF);
        lv_obj_set_pos(ui.act_msg[i], 104, y);
        lv_obj_set_size(ui.act_msg[i], s_act_msg_w, lv_font_get_line_height(&lv_font_montserrat_16));
        lv_obj_set_style_text_line_space(ui.act_msg[i], s_act_pitch - lv_font_get_line_height(&lv_font_montserrat_16), 0);
        lv_label_set_long_mode(ui.act_msg[i], LV_LABEL_LONG_DOT);
    }
}

static void build_msgbox(void)
{
    ui.msgbox = lv_obj_create(lv_screen_active());
    lv_obj_set_size(ui.msgbox, 800, 480);
    lv_obj_set_pos(ui.msgbox, 0, 0);
    style_box(ui.msgbox, 0x000000, 0);
    lv_obj_set_style_bg_opa(ui.msgbox, LV_OPA_50, 0);
    lv_obj_add_flag(ui.msgbox, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_event_cb(ui.msgbox, on_msg_ok, LV_EVENT_CLICKED, NULL);

    lv_obj_t *card = lv_obj_create(ui.msgbox);
    lv_obj_set_size(card, 540, 230);
    lv_obj_center(card);
    style_box(card, COL_MODAL, 16);
    lv_obj_set_style_border_width(card, 2, 0);
    lv_obj_set_style_border_color(card, lv_color_hex(COL_END_BD), 0);

    ui.msg_title = label(card, "", &lv_font_montserrat_24, 0xFFFFFF);
    lv_obj_set_pos(ui.msg_title, 24, 20);
    lv_obj_set_width(ui.msg_title, 492);
    lv_label_set_long_mode(ui.msg_title, LV_LABEL_LONG_DOT);
    ui.msg_text = label(card, "", &lv_font_montserrat_20, COL_MUTED);
    lv_obj_set_pos(ui.msg_text, 24, 64);
    lv_obj_set_width(ui.msg_text, 492);
    lv_label_set_long_mode(ui.msg_text, LV_LABEL_LONG_WRAP);

    lv_obj_t *ok = lv_button_create(card);
    lv_obj_set_size(ok, 160, 52);
    lv_obj_set_pos(ok, 540 - 24 - 160, 230 - 20 - 52);
    style_box(ok, COL_SAVE, 12);
    lv_obj_add_event_cb(ok, on_msg_ok, LV_EVENT_CLICKED, NULL);
    lv_obj_t *t = label(ok, "OK", &lv_font_montserrat_20, 0xFFFFFF);
    lv_obj_center(t);
}

static void build_session(void)
{
    ui.session = screen_root();
    lv_obj_add_flag(ui.session, LV_OBJ_FLAG_HIDDEN);
    /* Brand block ends 8 px before the Wi-Fi icon (same place as on home). */
    add_brand(ui.session, "Classroom session", 640 - 10 - WIFI_CHIP_W - 8);
    ui.ses_wifi = build_wifi_chip(ui.session, 640 - 10 - WIFI_CHIP_W, 10, 42, 12, ui.ses_bars, &ui.ses_wifi_x);
    usb_chip(ui.session, 640, 10);

    /* 0.6.8: the red-bordered session card runs the full width (x 12..788,
     * y 64..372). Left: badge, start time, timer, mic meter, classroom.
     * Right, inside the same card: the three marker buttons stacked
     * (Incident, Teacher Notes, Bug), 336 x 82 each. Teacher and Class are
     * no longer shown here; see the hidden dropdowns below. */
    ui.session_card = lv_obj_create(ui.session);
    lv_obj_set_pos(ui.session_card, 12, 64);
    lv_obj_set_size(ui.session_card, 776, 308);
    style_box(ui.session_card, 0x121A2B, 16);
    lv_obj_set_style_border_width(ui.session_card, 2, 0);
    lv_obj_set_style_border_color(ui.session_card, lv_color_hex(COL_RED), 0);

    ui.badge = lv_obj_create(ui.session_card);
    lv_obj_set_pos(ui.badge, 16, 16);
    lv_obj_set_size(ui.badge, 168, 36);
    style_box(ui.badge, COL_RED, 18);
    ui.badge_lbl = label(ui.badge, "Recording", &lv_font_montserrat_16, 0xFFFFFF);
    lv_obj_center(ui.badge_lbl);

    ui.started = label(ui.session_card, "Starting microphone...", &lv_font_montserrat_16, COL_MUTED);
    lv_obj_set_pos(ui.started, 16, 66);
    lv_obj_set_width(ui.started, 392);
    lv_label_set_long_mode(ui.started, LV_LABEL_LONG_WRAP);

    ui.timer = label(ui.session_card, "00:00:00", &lv_font_montserrat_48, 0xFFFFFF);
    lv_obj_set_pos(ui.timer, 16, 112);

    label(ui.session_card, "Mic level", &lv_font_montserrat_16, COL_MUTED);
    lv_obj_set_pos(lv_obj_get_child(ui.session_card, -1), 16, 184);
    ui.meter_state = label(ui.session_card, "Quiet", &lv_font_montserrat_16, COL_MUTED);
    lv_obj_set_pos(ui.meter_state, 230, 184);

    for (int i = 0; i < METER_BARS; i++) {
        ui.meter[i] = lv_obj_create(ui.session_card);
        lv_obj_set_size(ui.meter[i], 12, 28);
        lv_obj_set_pos(ui.meter[i], 16 + i * 18, 212);
        style_box(ui.meter[i], COL_OFF, 3);
    }

    /* Classroom: bottom of the left column (was in the right column). */
    label(ui.session_card, "Classroom", &lv_font_montserrat_16, COL_MUTED);
    lv_obj_set_pos(lv_obj_get_child(ui.session_card, -1), 16, 266);
    ui.room_value = label(ui.session_card, "", &lv_font_montserrat_20, 0xFFFFFF);
    lv_obj_set_pos(ui.room_value, 112, 263);
    lv_obj_set_width(ui.room_value, 296);
    lv_label_set_long_mode(ui.room_value, LV_LABEL_LONG_DOT);

    for (int i = 0; i < MARKER_TYPE_COUNT; i++) {
        make_mark_btn(ui.session_card, i, 424, 16 + i * (82 + 12), 336, 82, 14, &lv_font_montserrat_28);
    }

    /* Teacher and Class are hidden from this screen (0.6.8) but still hold
     * the saved choice: read_labels() reads them at start, and the End
     * dialog's Teacher/Class dropdowns sync to and from them, so the
     * recording's metadata is exactly as before. */
    ui.teacher_dd = make_dropdown(ui.session, s_teacher_opts);
    lv_obj_add_flag(ui.teacher_dd, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_event_cb(ui.teacher_dd, on_choice, LV_EVENT_VALUE_CHANGED, NULL);
    ui.class_dd = make_dropdown(ui.session, s_class_opts);
    lv_obj_add_flag(ui.class_dd, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_event_cb(ui.class_dd, on_choice, LV_EVENT_VALUE_CHANGED, NULL);

    /* Pause | End: two 382 px buttons, 12 px gap, x 12..788 (Mark removed). */
    ui.pause_btn = lv_button_create(ui.session);
    lv_obj_set_pos(ui.pause_btn, 12, 386);
    lv_obj_set_size(ui.pause_btn, 382, 80);
    style_box(ui.pause_btn, COL_YELLOW, 16);
    lv_obj_add_event_cb(ui.pause_btn, on_pause, LV_EVENT_CLICKED, NULL);
    ui.pause_lbl = label(ui.pause_btn, LV_SYMBOL_PAUSE "  Pause", &lv_font_montserrat_28, 0x1A1200);
    lv_obj_center(ui.pause_lbl);

    lv_obj_t *end = lv_button_create(ui.session);
    lv_obj_set_pos(end, 406, 386);
    lv_obj_set_size(end, 382, 80);
    style_box(end, COL_END, 16);
    lv_obj_set_style_border_width(end, 2, 0);
    lv_obj_set_style_border_color(end, lv_color_hex(COL_END_BD), 0);
    lv_obj_add_event_cb(end, on_end, LV_EVENT_CLICKED, NULL);
    lv_obj_t *end_lbl = label(end, LV_SYMBOL_STOP "  End", &lv_font_montserrat_28, 0xFFFFFF);
    lv_obj_center(end_lbl);
}

static lv_obj_t *modal_dropdown(lv_obj_t *card, const char *caption, const char *opts, int x, int y, int w)
{
    lv_obj_t *cap = label(card, caption, &lv_font_montserrat_14, COL_MUTED);
    lv_obj_set_pos(cap, x, y);
    lv_obj_t *dd = make_dropdown(card, opts);
    lv_obj_set_pos(dd, x, y + 24);
    lv_obj_set_size(dd, w, 52);
    lv_obj_add_event_cb(dd, on_choice, LV_EVENT_VALUE_CHANGED, NULL);
    return dd;
}

static void build_modal(void)
{
    ui.modal = lv_obj_create(lv_screen_active());
    lv_obj_set_size(ui.modal, 800, 480);
    lv_obj_set_pos(ui.modal, 0, 0);
    style_box(ui.modal, 0x000000, 0);
    lv_obj_set_style_bg_opa(ui.modal, LV_OPA_50, 0);
    lv_obj_add_flag(ui.modal, LV_OBJ_FLAG_HIDDEN);

    lv_obj_t *card = lv_obj_create(ui.modal);
    lv_obj_set_size(card, 640, 400);
    lv_obj_center(card);
    style_box(card, COL_MODAL, 16);
    lv_obj_set_style_border_width(card, 1, 0);
    lv_obj_set_style_border_color(card, lv_color_hex(COL_LINE), 0);

    label(card, "End and save this recording?", &lv_font_montserrat_28, 0xFFFFFF);
    lv_obj_set_pos(lv_obj_get_child(card, 0), 24, 18);
    /* 0.6.4: duration + start time across the full card width, and the
     * autosave countdown on its own line below it (it used to sit on the same
     * line and overlapped a long duration). The longest line the firmware can
     * produce, "Duration 34:57:09  -  started 12:58:58 PM" (the WAV 4 GB stop
     * is 34:57:09), is ~320 px of the 590. The dropdowns moved down 16 px
     * to make room; the buttons did not move. */
    ui.modal_dur = label(card, "Duration 00:00:00", &lv_font_montserrat_16, COL_MUTED);
    lv_obj_set_pos(ui.modal_dur, 24, 62);
    lv_obj_set_width(ui.modal_dur, 640 - 2 * 24 - 2);
    lv_label_set_long_mode(ui.modal_dur, LV_LABEL_LONG_DOT);
    ui.modal_countdown = label(card, "Autosaves in 4:00", &lv_font_montserrat_20, COL_AMBER);
    lv_obj_set_pos(ui.modal_countdown, 24, 86);
    lv_obj_set_width(ui.modal_countdown, 640 - 2 * 24 - 2);

    /* Classroom across the full width (long room names), Teacher | Class below. */
    ui.modal_room = modal_dropdown(card, "Classroom", s_room_opts, 24, 104 + 16, 328 + 280 - 24);
    ui.modal_teacher = modal_dropdown(card, "Teacher", s_teacher_opts, 24, 196 + 16, 280);
    ui.modal_class = modal_dropdown(card, "Class", s_class_opts, 328, 196 + 16, 280);

    ui.keep_btn = lv_button_create(card);
    lv_obj_set_pos(ui.keep_btn, 24, 312);
    lv_obj_set_size(ui.keep_btn, 280, 64);
    style_box(ui.keep_btn, COL_KEEP, 14);
    lv_obj_add_event_cb(ui.keep_btn, on_keep, LV_EVENT_CLICKED, NULL);
    lv_obj_t *keep = label(ui.keep_btn, "Keep recording", &lv_font_montserrat_20, 0xFFFFFF);
    lv_obj_center(keep);

    ui.save_btn = lv_button_create(card);
    lv_obj_set_pos(ui.save_btn, 328, 312);
    lv_obj_set_size(ui.save_btn, 288, 64);
    style_box(ui.save_btn, COL_SAVE, 14);
    lv_obj_add_event_cb(ui.save_btn, on_save, LV_EVENT_CLICKED, NULL);
    ui.save_lbl = label(ui.save_btn, "Save & finish", &lv_font_montserrat_20, 0xFFFFFF);
    lv_obj_center(ui.save_lbl);
}

/* Incident / Bug confirm card (0.6.8): title, when line, explanation,
 * then big Cancel | OK. Text comes from MARKER_TYPES (marker_types.h). */
static void build_mark_popup(void)
{
    ui.mark = lv_obj_create(lv_screen_active());
    lv_obj_set_size(ui.mark, 800, 480);
    lv_obj_set_pos(ui.mark, 0, 0);
    style_box(ui.mark, 0x000000, 0);
    lv_obj_set_style_bg_opa(ui.mark, LV_OPA_60, 0);
    lv_obj_add_flag(ui.mark, LV_OBJ_FLAG_HIDDEN);

    const int cw = 640, ch = 360, pad = 28, bh = 76, gap = 20;
    lv_obj_t *card = lv_obj_create(ui.mark);
    lv_obj_set_size(card, cw, ch);
    lv_obj_center(card);
    style_box(card, COL_MODAL, 16);
    lv_obj_set_style_border_width(card, 2, 0);
    lv_obj_set_style_border_color(card, lv_color_hex(COL_LINE), 0);

    ui.mark_title = label(card, "", &lv_font_montserrat_28, 0xFFFFFF);
    lv_obj_set_pos(ui.mark_title, pad, 20);
    lv_obj_set_width(ui.mark_title, cw - 2 * pad - 4);
    lv_label_set_long_mode(ui.mark_title, LV_LABEL_LONG_DOT);
    ui.mark_when = label(card, "", &lv_font_montserrat_16, COL_MUTED);
    lv_obj_set_pos(ui.mark_when, pad, 60);
    lv_obj_set_width(ui.mark_when, cw - 2 * pad - 4);
    lv_label_set_long_mode(ui.mark_when, LV_LABEL_LONG_DOT);
    /* Up to 5 lines of 20 px text (y 92..~210); the longest (Bug) is 4. */
    ui.mark_text = label(card, "", &lv_font_montserrat_20, 0xFFFFFF);
    lv_obj_set_pos(ui.mark_text, pad, 92);
    lv_obj_set_size(ui.mark_text, cw - 2 * pad - 4, ch - 92 - bh - 2 * 20);
    lv_label_set_long_mode(ui.mark_text, LV_LABEL_LONG_WRAP);

    int bw = (cw - 4 - 2 * pad - gap) / 2;
    lv_obj_t *cancel = lv_button_create(card);
    lv_obj_set_pos(cancel, pad, ch - 4 - 20 - bh);
    lv_obj_set_size(cancel, bw, bh);
    style_box(cancel, COL_KEEP, 14);
    lv_obj_add_event_cb(cancel, on_mark_cancel, LV_EVENT_CLICKED, NULL);
    lv_obj_t *cl = label(cancel, "Cancel", &lv_font_montserrat_28, 0xFFFFFF);
    lv_obj_center(cl);

    lv_obj_t *ok = lv_button_create(card);
    lv_obj_set_pos(ok, pad + bw + gap, ch - 4 - 20 - bh);
    lv_obj_set_size(ok, bw, bh);
    style_box(ok, COL_SAVE, 14);
    lv_obj_add_event_cb(ok, on_mark_ok, LV_EVENT_CLICKED, NULL);
    lv_obj_t *okl = label(ok, "OK", &lv_font_montserrat_28, 0xFFFFFF);
    lv_obj_center(okl);
}
#else
#include "ui_layout_480x320.inc"
#endif /* !CONFIG_RECORDER_BOARD_HOSYOND_35 */

#include "ui_wifi_picker.inc"
#include "ui_qr.inc"

void ui_build(void)
{
    join_opts(s_room_opts, sizeof s_room_opts, config_classroom_count(), config_classroom_label);
    join_opts(s_teacher_opts, sizeof s_teacher_opts, config_teacher_count(), config_teacher);
    join_opts(s_class_opts, sizeof s_class_opts, config_class_count(), config_class_code);

    build_home();
    build_session();
    build_modal();
    build_mark_popup();
    build_qr_popup();
    build_msgbox();
#if CONFIG_RECORDER_BOARD_HOSYOND_35
    build_sleep_overlay();
#endif

    char saved[48];
    prefs_get("room", saved, sizeof saved);
    int room = 0;
    for (int i = 0; i < config_classroom_count(); i++) {
        if (strcmp(config_classroom_code(i), saved) == 0) {
            room = i;
            break;
        }
    }
    prefs_get("teacher", saved, sizeof saved);
    int teacher = find_str(config_teacher_count(), config_teacher, saved);
    prefs_get("class", saved, sizeof saved);
    int cls = find_str(config_class_count(), config_class_code, saved);

    lv_dropdown_set_selected(ui.room_dd, room);
    lv_dropdown_set_selected(ui.teacher_dd, teacher);
    lv_dropdown_set_selected(ui.class_dd, cls);
    apply_room_label();

    if (!lv_timer_create(ui_tick, 200, NULL)) {
        ESP_LOGE("ui", "ui_tick timer create failed");
    }
}
