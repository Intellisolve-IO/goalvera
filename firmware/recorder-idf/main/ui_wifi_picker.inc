/*
 * Wi-Fi network picker (0.6.3). Included by ui_app.c after the
 * board layout, so it uses the same helpers (label, style_box, make_bars)
 * and the board's PK_* sizes. Same screens on both boards.
 *
 * Tapping the Wi-Fi icon in the status bar opens a full-screen list:
 *   - the connected network first, then saved networks in range, then the
 *     rest by signal strength, then saved networks the scan did not see;
 *   - a saved network (compiled-in or joined on the device) switches in
 *     one tap; an open network joins in one tap; any other network opens
 *     the password screen (on-screen keyboard, Show/Hide, Connect, Cancel);
 *   - the status line shows Connecting... / Connected / Couldn't join.
 * The scan and the switch run on the Wi-Fi side (wifi_time.c); this file
 * only polls their results from an LVGL timer, so the UI never blocks and
 * recording/uploads carry on underneath. Everything here is created on
 * open and deleted on close. The LVGL pool is 64 KB and the home and
 * session screens already use about 60% of it, so the first opening adds
 * a PK_POOL_BYTES pool in PSRAM (lv_mem_add_pool) and rows stop being
 * added if free pool memory still runs low. The picker closes
 * itself after PK_IDLE_MS without a touch (the password screen gets
 * longer) so it never hides a recording session for long.
 *
 * Passwords live only in the text area while typing; they are never
 * logged, and the text area is wiped before it is deleted.
 */

#define PK_IDLE_MS     60000
#define PK_PW_IDLE_MS  180000
#define PK_NOTE_MS     5000
#define COL_TAG_SAVED  0x93C5FD
#define PK_POOL_BYTES  (48 * 1024)
#define PK_MIN_FREE    (6 * 1024)

static struct {
    lv_obj_t *root;
    lv_obj_t *status;
    lv_obj_t *list;
    lv_obj_t *rescan;
    lv_obj_t *rescan_lbl;
    lv_obj_t *pw;
    lv_obj_t *pw_ta;
    lv_obj_t *pw_show_lbl;
    lv_obj_t *pw_hint;
    lv_timer_t *timer;
    wifi_row_t rows[WIFI_ROWS_MAX];
    int n_rows;
    uint32_t seen_seq;
    uint32_t sw_seq;
    uint32_t sw_seq_at_open;
    bool conn_drawn;
    bool busy_drawn;
    char conn_ssid[WIFI_SSID_LEN];
    char pw_ssid[WIFI_SSID_LEN];
    char note[96];
    uint32_t note_ms;
    bool dirty; /* rebuild the list on the next tick (never inside a row's own event) */
} pk;

static void pk_close(void);
static void pk_refresh(bool force);

static size_t pk_pool_free(void)
{
    lv_mem_monitor_t m;
    lv_mem_monitor(&m);
    return m.free_size;
}

static void pk_ensure_pool(void)
{
    static bool added;
    if (added) {
        return;
    }
    added = true;
    void *mem = heap_caps_malloc(PK_POOL_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!mem || !lv_mem_add_pool(mem, PK_POOL_BYTES)) {
        ESP_LOGW("ui", "Wi-Fi picker: extra LVGL pool unavailable (%u bytes free)", (unsigned)pk_pool_free());
        heap_caps_free(mem);
        return;
    }
    ESP_LOGI("ui", "Wi-Fi picker: added %u-byte LVGL pool in PSRAM", (unsigned)PK_POOL_BYTES);
}

static lv_obj_t *pk_button(lv_obj_t *parent, const char *text, uint32_t bg, int w, int h)
{
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_set_size(b, w, h);
    style_box(b, bg, PK_RADIUS);
    lv_obj_set_style_bg_color(b, lv_color_hex(COL_LINE), LV_STATE_PRESSED);
    lv_obj_set_style_bg_color(b, lv_color_hex(0x232C3D), LV_STATE_DISABLED);
    lv_obj_t *l = label(b, text, PK_F_TEXT, 0xFFFFFF);
    lv_obj_set_style_text_color(l, lv_color_hex(COL_MUTED), LV_STATE_DISABLED);
    lv_obj_center(l);
    return b;
}

static void pk_note(const char *text)
{
    strlcpy(pk.note, text, sizeof pk.note);
    pk.note_ms = lv_tick_get();
    pk.dirty = true;
}

/* ---- password screen ---- */

static void pk_pw_close(void)
{
    if (!pk.pw) {
        return;
    }
    lv_textarea_set_text(pk.pw_ta, "");
    lv_obj_delete_async(pk.pw);
    pk.pw = NULL;
    pk.pw_ta = NULL;
    pk.pw_show_lbl = NULL;
    pk.pw_hint = NULL;
    pk.dirty = true; /* the list was emptied while the keyboard was up */
}

static void pk_pw_connect(lv_event_t *e)
{
    (void)e;
    if (!pk.pw) {
        return;
    }
    const char *pass = lv_textarea_get_text(pk.pw_ta);
    if (!wifi_pass_ok(pass, false)) {
        lv_label_set_text(pk.pw_hint, "Wi-Fi passwords are 8 to 63 characters.");
        lv_obj_set_style_text_color(pk.pw_hint, lv_color_hex(COL_END_BD), 0);
        return;
    }
    if (!wifi_connect_new(pk.pw_ssid, pass)) {
        lv_label_set_text(pk.pw_hint, "Wi-Fi is busy. Try again in a moment.");
        lv_obj_set_style_text_color(pk.pw_hint, lv_color_hex(COL_END_BD), 0);
        return;
    }
    pk_pw_close();
    pk.note[0] = '\0';
    pk.dirty = true;
}

#define PK_PW_HINT "Capital letters matter. Tap Connect when done."

static void pk_pw_typed(lv_event_t *e)
{
    (void)e;
    if (pk.pw_hint) {
        lv_label_set_text(pk.pw_hint, PK_PW_HINT);
        lv_obj_set_style_text_color(pk.pw_hint, lv_color_hex(COL_MUTED), 0);
    }
}

static void pk_pw_cancel(lv_event_t *e)
{
    (void)e;
    pk_pw_close();
}

static void pk_pw_show(lv_event_t *e)
{
    (void)e;
    if (!pk.pw) {
        return;
    }
    bool hidden = lv_textarea_get_password_mode(pk.pw_ta);
    lv_textarea_set_password_mode(pk.pw_ta, !hidden);
    lv_label_set_text(pk.pw_show_lbl, hidden ? "Hide" : "Show");
}

static void pk_pw_open(const char *ssid)
{
    if (pk.pw) {
        return;
    }
    strlcpy(pk.pw_ssid, ssid, sizeof pk.pw_ssid);
    /* The list is hidden under this screen: free its rows (memory), rebuilt
     * when the password screen closes. Deleted async: this runs inside a
     * row's own click event. */
    for (int i = (int)lv_obj_get_child_count(pk.list) - 1; i >= 0; i--) {
        lv_obj_delete_async(lv_obj_get_child(pk.list, i));
    }
    const int W = PK_W;
    pk.pw = lv_obj_create(pk.root);
    lv_obj_set_size(pk.pw, W, PK_H);
    lv_obj_set_pos(pk.pw, 0, 0);
    style_box(pk.pw, COL_BG, 0);

    /* Header: Cancel | Join <name> | Connect */
    lv_obj_t *cancel = pk_button(pk.pw, "Cancel", COL_KEEP, PK_BTN_W, PK_HDR_H);
    lv_obj_set_pos(cancel, PK_PAD, PK_PAD);
    lv_obj_add_event_cb(cancel, pk_pw_cancel, LV_EVENT_CLICKED, NULL);
    lv_obj_t *join = pk_button(pk.pw, "Connect", COL_SAVE, PK_BTN_W, PK_HDR_H);
    lv_obj_set_pos(join, W - PK_PAD - PK_BTN_W, PK_PAD);
    lv_obj_add_event_cb(join, pk_pw_connect, LV_EVENT_CLICKED, NULL);
    char title[48];
    snprintf(title, sizeof title, "Join %s", ssid);
    lv_obj_t *t = label(pk.pw, title, PK_F_TITLE, 0xFFFFFF);
    lv_obj_set_width(t, W - 4 * PK_PAD - 2 * PK_BTN_W);
    lv_label_set_long_mode(t, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(t, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_pos(t, 2 * PK_PAD + PK_BTN_W, PK_PAD + (PK_HDR_H - lv_font_get_line_height(PK_F_TITLE)) / 2);

    /* Password box + Show/Hide */
    int y = PK_PAD + PK_HDR_H + PK_GAP;
    pk.pw_ta = lv_textarea_create(pk.pw);
    lv_textarea_set_one_line(pk.pw_ta, true);
    lv_textarea_set_password_mode(pk.pw_ta, true);
    lv_textarea_set_max_length(pk.pw_ta, 64);
    lv_textarea_set_placeholder_text(pk.pw_ta, "Wi-Fi password");
    lv_obj_set_pos(pk.pw_ta, PK_PAD, y);
    lv_obj_set_size(pk.pw_ta, W - 3 * PK_PAD - PK_SHOW_W, PK_HDR_H);
    lv_obj_set_style_text_font(pk.pw_ta, PK_F_ROW, 0);
    lv_obj_set_style_text_color(pk.pw_ta, lv_color_white(), 0);
    lv_obj_set_style_bg_color(pk.pw_ta, lv_color_hex(COL_DROP), 0);
    lv_obj_set_style_border_color(pk.pw_ta, lv_color_hex(COL_BLUE), 0);
    lv_obj_set_style_border_width(pk.pw_ta, 2, 0);
    lv_obj_set_style_radius(pk.pw_ta, PK_RADIUS, 0);
    lv_obj_set_style_pad_ver(pk.pw_ta, (PK_HDR_H - 4 - lv_font_get_line_height(PK_F_ROW)) / 2, 0);
    lv_obj_add_state(pk.pw_ta, LV_STATE_FOCUSED); /* show the cursor */
    lv_obj_add_event_cb(pk.pw_ta, pk_pw_connect, LV_EVENT_READY, NULL); /* the keyboard's Enter key */
    lv_obj_add_event_cb(pk.pw_ta, pk_pw_typed, LV_EVENT_VALUE_CHANGED, NULL);

    lv_obj_t *show = pk_button(pk.pw, "Show", COL_KEEP, PK_SHOW_W, PK_HDR_H);
    lv_obj_set_pos(show, W - PK_PAD - PK_SHOW_W, y);
    lv_obj_add_event_cb(show, pk_pw_show, LV_EVENT_CLICKED, NULL);
    pk.pw_show_lbl = lv_obj_get_child(show, 0);

    y += PK_HDR_H + 3;
    pk.pw_hint = label(pk.pw, PK_PW_HINT, PK_F_SMALL, COL_MUTED);
    lv_obj_set_pos(pk.pw_hint, PK_PAD + 2, y);
    lv_obj_set_width(pk.pw_hint, W - 2 * PK_PAD);
    lv_label_set_long_mode(pk.pw_hint, LV_LABEL_LONG_DOT);

    lv_obj_t *kb = lv_keyboard_create(pk.pw);
    lv_obj_set_size(kb, W, PK_H - PK_KB_Y);
    lv_obj_align(kb, LV_ALIGN_BOTTOM_MID, 0, 0); /* lv_keyboard_create aligns to the bottom too */
    lv_obj_set_style_text_font(kb, PK_F_KB, 0);
    lv_obj_set_style_bg_color(kb, lv_color_hex(COL_STATUS), 0);
    lv_obj_set_style_pad_all(kb, 4, 0);
    lv_obj_set_style_pad_gap(kb, 4, 0);
    lv_keyboard_set_textarea(kb, pk.pw_ta);
    lv_obj_add_event_cb(kb, pk_pw_connect, LV_EVENT_READY, NULL);
    lv_obj_add_event_cb(kb, pk_pw_cancel, LV_EVENT_CANCEL, NULL);
}

/* ---- network list ---- */

static void pk_on_row(lv_event_t *e)
{
    int i = (int)(intptr_t)lv_event_get_user_data(e);
    if (i < 0 || i >= pk.n_rows) {
        return;
    }
    const wifi_row_t *r = &pk.rows[i];
    wifi_status_t w;
    wifi_get_status(&w);
    wifi_switch_t sw;
    wifi_get_switch(&sw);
    char msg[96];
    if (w.connecting) {
        snprintf(msg, sizeof msg, "Still connecting to %s...", sw.ssid);
        pk_note(msg);
        return;
    }
    if (r->connected && w.connected) {
        snprintf(msg, sizeof msg, "Already connected to %s", r->ssid);
        pk_note(msg);
        return;
    }
    bool wrong_pass = sw.state == WIFI_SW_FAILED && sw.auth_failed && strcmp(sw.ssid, r->ssid) == 0 &&
                      sw.seq > pk.sw_seq_at_open;
    if (r->saved && !(wrong_pass && !r->builtin)) {
        wifi_connect_saved(r->ssid);
    } else if (r->open && r->in_range) {
        wifi_connect_new(r->ssid, "");
    } else {
        pk_pw_open(r->ssid);
        return;
    }
    pk.note[0] = '\0';
    pk.dirty = true;
}

static void pk_add_row(int i, const wifi_switch_t *sw, bool connecting)
{
    const wifi_row_t *r = &pk.rows[i];
    int row_w = PK_W - 2 * PK_PAD;
    lv_obj_t *row = lv_obj_create(pk.list);
    lv_obj_set_size(row, row_w, PK_ROW_H);
    style_box(row, COL_CARD, PK_RADIUS);
    lv_obj_set_style_bg_color(row, lv_color_hex(COL_LINE), LV_STATE_PRESSED);
    if (r->connected) {
        lv_obj_set_style_border_width(row, 1, 0);
        lv_obj_set_style_border_color(row, lv_color_hex(COL_GREEN), 0);
    }
    lv_obj_add_flag(row, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(row, pk_on_row, LV_EVENT_CLICKED, (void *)(intptr_t)i);

    lv_obj_t *box = lv_obj_create(row);
    lv_obj_set_size(box, 30, 20);
    style_box(box, COL_CARD, 0);
    lv_obj_set_style_bg_opa(box, LV_OPA_TRANSP, 0);
    lv_obj_align(box, LV_ALIGN_LEFT_MID, 10, 0);
    lv_obj_t *bars[4];
    make_bars(box, bars);
    int lit = r->in_range ? wifi_bars_from_rssi(r->rssi) : 0;
    paint_bars(bars, lit > 0 ? lit : (r->in_range ? 1 : 0), r->in_range);
    lv_obj_remove_flag(box, LV_OBJ_FLAG_CLICKABLE);
    for (int b = 0; b < 4; b++) {
        lv_obj_remove_flag(bars[b], LV_OBJ_FLAG_CLICKABLE);
    }

    const char *tag = "";
    uint32_t tag_col = COL_MUTED;
    bool trying = connecting && strcmp(sw->ssid, r->ssid) == 0;
    if (trying) {
        tag = "Connecting...";
        tag_col = COL_AMBER;
    } else if (r->connected) {
        tag = LV_SYMBOL_OK " Connected";
        tag_col = COL_GREEN;
    } else if (r->saved && !r->in_range) {
        tag = "Saved, not seen";
    } else if (r->saved) {
        tag = "Saved";
        tag_col = COL_TAG_SAVED;
    } else if (r->open) {
        tag = "Open";
    }
    int tag_w = 0;
    if (tag[0]) {
        lv_obj_t *tl = label(row, tag, PK_F_SMALL, tag_col);
        lv_obj_align(tl, LV_ALIGN_RIGHT_MID, -10, 0);
        lv_point_t sz;
        lv_text_get_size(&sz, tag, PK_F_SMALL, 0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
        tag_w = sz.x + 10;
    }
    lv_obj_t *name = label(row, r->ssid, PK_F_ROW, r->in_range || r->connected ? 0xFFFFFF : COL_MUTED);
    lv_label_set_long_mode(name, LV_LABEL_LONG_DOT);
    lv_obj_set_width(name, row_w - 50 - 10 - tag_w);
    lv_obj_set_height(name, lv_font_get_line_height(PK_F_ROW));
    lv_obj_align(name, LV_ALIGN_LEFT_MID, 50, 0);
}

static void pk_rebuild(const wifi_status_t *w, const wifi_switch_t *sw)
{
    static wifi_seen_t seen[WIFI_SEEN_MAX];
    static wifi_saved_t saved[WIFI_NETS_MAX];
    uint32_t seq;
    int ns = wifi_scan_results(seen, WIFI_SEEN_MAX, &seq);
    int nv = wifi_saved_list(saved, WIFI_NETS_MAX);
    pk.dirty = false;
    pk.n_rows = wifi_rows_build(pk.rows, WIFI_ROWS_MAX, seen, ns, saved, nv, w->connected ? w->ssid : "");
    pk.seen_seq = seq;
    pk.sw_seq = sw->seq;
    pk.conn_drawn = w->connected;
    pk.busy_drawn = wifi_scan_busy();
    strlcpy(pk.conn_ssid, w->ssid, sizeof pk.conn_ssid);

    lv_obj_clean(pk.list);
    for (int i = 0; i < pk.n_rows; i++) {
        if (pk_pool_free() < PK_MIN_FREE) {
            ESP_LOGW("ui", "Wi-Fi picker: low LVGL memory, showing %d of %d networks", i, pk.n_rows);
            pk.n_rows = i;
            break;
        }
        pk_add_row(i, sw, w->connecting);
    }
    if (pk.n_rows == 0) {
        lv_obj_t *l = label(pk.list, pk.busy_drawn ? "Looking for networks..." : "No networks found. Tap Scan to look again.",
                            PK_F_TEXT, COL_MUTED);
        lv_obj_set_width(l, PK_W - 2 * PK_PAD);
        lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
    }
}

static void pk_refresh(bool force)
{
    if (!pk.root) {
        return;
    }
    wifi_status_t w;
    wifi_get_status(&w);
    wifi_switch_t sw;
    wifi_get_switch(&sw);
    bool busy = wifi_scan_busy();
    uint32_t seq;
    wifi_seen_t one;
    wifi_scan_results(&one, 0, &seq);

    if (pk.pw) {
        /* Password screen up: the list stays empty until it closes. */
    } else if (force || pk.dirty || seq != pk.seen_seq || sw.seq != pk.sw_seq || w.connected != pk.conn_drawn ||
        strcmp(w.ssid, pk.conn_ssid) != 0 || (pk.n_rows == 0 && busy != pk.busy_drawn)) {
        pk_rebuild(&w, &sw);
    }

    /* Status line. */
    char st[128];
    uint32_t col = COL_MUTED;
    bool fresh = sw.seq > pk.sw_seq_at_open;
    if (pk.note[0] && lv_tick_elaps(pk.note_ms) < PK_NOTE_MS) {
        strlcpy(st, pk.note, sizeof st);
        col = COL_AMBER;
    } else if (!w.configured) {
        strlcpy(st, "Wi-Fi is off on this recorder", sizeof st);
    } else if (w.connecting) {
        snprintf(st, sizeof st, "Connecting to %s...", sw.ssid);
        col = COL_AMBER;
    } else if (fresh && sw.state == WIFI_SW_FAILED) {
        int n = snprintf(st, sizeof st, "Couldn't join %s%s", sw.ssid,
                         sw.auth_failed ? ". Check the password." : ".");
        if (sw.fallback[0] && n > 0 && n < (int)sizeof st) {
            snprintf(st + n, sizeof st - n, w.connected ? " Back on %s." : " Trying %s.",
                     w.connected ? w.ssid : sw.fallback);
        }
        col = COL_END_BD;
    } else if (w.connected) {
        snprintf(st, sizeof st, "Connected to %s", w.ssid);
        col = COL_GREEN;
    } else if (w.ssid[0]) {
        snprintf(st, sizeof st, "Not connected. Trying %s...", w.ssid);
    } else {
        strlcpy(st, "Not connected. Pick a network.", sizeof st);
    }
    lv_label_set_text(pk.status, st);
    lv_obj_set_style_text_color(pk.status, lv_color_hex(col), 0);

    if (busy || w.connecting || !w.configured) {
        lv_obj_add_state(pk.rescan, LV_STATE_DISABLED);
    } else {
        lv_obj_remove_state(pk.rescan, LV_STATE_DISABLED);
    }
    lv_label_set_text(pk.rescan_lbl, busy ? "Scanning..." : LV_SYMBOL_REFRESH " Scan");
}

static void pk_tick(lv_timer_t *t)
{
    (void)t;
    if (lv_display_get_inactive_time(NULL) > (pk.pw ? PK_PW_IDLE_MS : PK_IDLE_MS)) {
        pk_close();
        return;
    }
    pk_refresh(false);
}

static void pk_on_close(lv_event_t *e)
{
    (void)e;
    pk_close();
}

static void pk_on_rescan(lv_event_t *e)
{
    (void)e;
    pk.note[0] = '\0';
    if (!wifi_scan_request()) {
        pk_note("Wi-Fi is busy. Try again in a moment.");
    }
    pk.dirty = true;
}

static void pk_close(void)
{
    if (!pk.root) {
        return;
    }
    if (pk.pw_ta) {
        lv_textarea_set_text(pk.pw_ta, "");
    }
    if (pk.timer) {
        lv_timer_delete(pk.timer);
    }
    lv_obj_delete_async(pk.root);
    memset(&pk, 0, sizeof pk);
}

static void wifi_picker_open(lv_event_t *e)
{
    (void)e;
    if (pk.root) {
        return;
    }
    memset(&pk, 0, sizeof pk);
    pk_ensure_pool();
    const int W = PK_W;
    pk.root = lv_obj_create(lv_screen_active());
    lv_obj_set_size(pk.root, W, PK_H);
    lv_obj_set_pos(pk.root, 0, 0);
    style_box(pk.root, COL_BG, 0);
    lv_obj_move_foreground(pk.root);

    lv_obj_t *title = label(pk.root, "Wi-Fi networks", PK_F_TITLE, 0xFFFFFF);
    lv_obj_set_pos(title, PK_PAD + 4, PK_PAD + (PK_HDR_H - lv_font_get_line_height(PK_F_TITLE)) / 2);

    lv_obj_t *close = pk_button(pk.root, LV_SYMBOL_CLOSE " Close", COL_KEEP, PK_BTN_W, PK_HDR_H);
    lv_obj_set_pos(close, W - PK_PAD - PK_BTN_W, PK_PAD);
    lv_obj_add_event_cb(close, pk_on_close, LV_EVENT_CLICKED, NULL);

    pk.rescan = pk_button(pk.root, LV_SYMBOL_REFRESH " Scan", COL_KEEP, PK_SCAN_W, PK_HDR_H);
    lv_obj_set_pos(pk.rescan, W - 2 * PK_PAD - PK_BTN_W - PK_SCAN_W, PK_PAD);
    lv_obj_add_event_cb(pk.rescan, pk_on_rescan, LV_EVENT_CLICKED, NULL);
    pk.rescan_lbl = lv_obj_get_child(pk.rescan, 0);

    int y = PK_PAD + PK_HDR_H + PK_GAP;
    pk.status = label(pk.root, "", PK_F_TEXT, COL_MUTED);
    lv_obj_set_pos(pk.status, PK_PAD + 4, y);
    lv_obj_set_width(pk.status, W - 2 * PK_PAD - 4);
    lv_label_set_long_mode(pk.status, LV_LABEL_LONG_DOT);
    y += lv_font_get_line_height(PK_F_TEXT) + PK_GAP;

    pk.list = lv_obj_create(pk.root);
    lv_obj_set_pos(pk.list, 0, y);
    lv_obj_set_size(pk.list, W, PK_H - y);
    style_box(pk.list, COL_BG, 0);
    lv_obj_add_flag(pk.list, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(pk.list, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(pk.list, LV_SCROLLBAR_MODE_AUTO);
    lv_obj_set_style_pad_hor(pk.list, PK_PAD, 0);
    lv_obj_set_style_pad_bottom(pk.list, PK_PAD, 0);
    lv_obj_set_style_pad_row(pk.list, PK_GAP, 0);
    lv_obj_set_flex_flow(pk.list, LV_FLEX_FLOW_COLUMN);

    {
        wifi_switch_t sw;
        wifi_get_switch(&sw);
        /* A failure from before this opening is old news; a switch still
         * running is not. */
        pk.sw_seq_at_open = sw.state == WIFI_SW_CONNECTING ? sw.seq - 1 : sw.seq;
    }
    wifi_scan_request();
    pk_refresh(true);
    pk.timer = lv_timer_create(pk_tick, 250, NULL);
}
