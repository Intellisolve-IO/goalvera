/*
 * Hosyond 3.5" layout: the CrowPanel 800x480 layout (see the builders in
 * ui_app.c) scaled to 480x320. Included by ui_app.c instead of those
 * builders when CONFIG_RECORDER_BOARD_HOSYOND_35.
 *
 * Every position and size is the CrowPanel number run through SX() (x 0.6)
 * or SY() (y 0.667), so the screens look the same, only smaller. Fonts step
 * down to the nearest enabled Montserrat size (48->32, 28/32->20, 24->16,
 * 20->14, captions 14/16->12). Departures from pure scaling are marked
 * "ADJ:" and exist only where text would not fit or a touch target would
 * drop under 44 px (dropdowns and dialog buttons are held at 44 px tall).
 * Same objects (every ui.* field ui_tick() uses) and same event handlers.
 */

#define SX(v) (((v) * 3 + 2) / 5) /* 800 -> 480 */
#define SY(v) (((v) * 2 + 1) / 3) /* 480 -> 320 */
#define TOUCH_MIN 44

#define F_CLOCK   (&lv_font_montserrat_32) /* 48 */
#define F_BIG     (&lv_font_montserrat_20) /* 28, 32 */
#define F_TITLE   (&lv_font_montserrat_16) /* 24 */
#define F_TEXT    (&lv_font_montserrat_14) /* 20, 16 (body text) */
#define F_CAPTION (&lv_font_montserrat_12) /* 16, 14 (muted captions, activity log) */

#define HEADER_H     SY(64)
#define BRAND_X      SX(16)
#define BRAND_GAP    SX(10)
#define BADGE_MIN    SY(40)
#define TITLE_LINES  2

static void add_brand(lv_obj_t *parent, const char *title_text, int right_limit)
{
    const lv_font_t *tfont = F_TITLE;
    int32_t lh = lv_font_get_line_height(tfont);
    int badge1 = (int)lh > BADGE_MIN ? (int)lh : BADGE_MIN;
    lv_point_t sz;
    lv_text_get_size(&sz, title_text, tfont, 0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
    bool forced = strchr(title_text, '\n') != NULL;
    int lines = (!forced && sz.x <= right_limit - (BRAND_X + badge1 + BRAND_GAP)) ? 1 : TITLE_LINES;
    int block_h = lines * (int)lh;
    int badge = block_h > BADGE_MIN ? block_h : BADGE_MIN;

    lv_obj_t *logo = lv_obj_create(parent);
    lv_obj_set_size(logo, badge, badge);
    lv_obj_set_pos(logo, BRAND_X, (HEADER_H - badge) / 2);
    style_box(logo, COL_LOGO, badge / 4);
    lv_obj_set_style_border_width(logo, 2, 0);
    lv_obj_set_style_border_color(logo, lv_color_hex(0x93C5FD), 0);

    static const lv_font_t *const bfonts[] = {
        &lv_font_montserrat_20, &lv_font_montserrat_16, &lv_font_montserrat_14, &lv_font_montserrat_12,
    };
    const char *btxt = config_badge_text();
    const lv_font_t *bf = &lv_font_montserrat_12;
    for (size_t i = 0; i < sizeof bfonts / sizeof bfonts[0]; i++) {
        lv_point_t bs;
        lv_text_get_size(&bs, btxt, bfonts[i], 0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
        if (lv_font_get_line_height(bfonts[i]) <= badge / 2 + 2 && bs.x <= badge - 6) {
            bf = bfonts[i];
            break;
        }
    }
    lv_obj_t *t = label(logo, btxt, bf, 0xFFFFFF);
    lv_obj_center(t);

    int x_text = BRAND_X + badge + BRAND_GAP;
    lv_obj_t *title = label(parent, title_text, tfont, 0xFFFFFF);
    lv_obj_set_width(title, right_limit - x_text);
    lv_label_set_long_mode(title, LV_LABEL_LONG_DOT);
    lv_obj_set_height(title, block_h);
    lv_obj_set_style_text_line_space(title, 0, 0);
    lv_obj_set_pos(title, x_text, (HEADER_H - block_h) / 2);
    ESP_LOGI("ui", "brand badge \"%s\": %d title line(s), block %d px, badge %d px, badge font %d px",
             btxt, lines, block_h, badge, (int)lv_font_get_line_height(bf));
    if (parent == ui.home) {   /* kept so a new school name can redraw it (0.7.5) */
        s_home_brand[0] = logo;
        s_home_brand[1] = title;
        s_home_brand_right = right_limit;
    }
}

static void usb_chip(lv_obj_t *parent, int x, int y)
{
    lv_obj_t *chip = lv_obj_create(parent);
    lv_obj_set_pos(chip, x, y);
    lv_obj_set_size(chip, SX(148), SY(44));
    style_box(chip, COL_CHIP, SY(12));
    lv_obj_set_style_border_width(chip, 1, 0);
    lv_obj_set_style_border_color(chip, lv_color_hex(COL_LINE), 0);
    /* 0.7.1: battery gauge drawn in the chip (main/batt_gauge.h): outline +
     * fill bar in proportion to %, green/yellow/red, bolt when on USB. No text.
     * ui_tick() -> paint_power() updates it. */
    if (s_pwr_n < PWR_CHIPS) {
        batt_gauge_create(&s_gauge[s_pwr_n], chip);
        s_pwr_n++;
    }
}

static lv_obj_t *screen_root(void)
{
    lv_obj_t *root = lv_obj_create(lv_screen_active());
    lv_obj_set_size(root, BOARD_UI_W, BOARD_UI_H);
    lv_obj_set_pos(root, 0, 0);
    style_box(root, COL_BG, 0);
    return root;
}

static void build_home(void)
{
    ui.home = screen_root();
    /* 0.6.3 ADJ: the Wi-Fi chip is only the signal icon (56 px, x 322..378,
     * 6 px left of the USB chip), so the brand may run to x 316. Up to 0.6.2
     * the chip also showed the network name, which ran into the bars. */
    add_brand(ui.home, config_school_name(), SX(640) - 6 - WIFI_CHIP_W - 6);
    ui.home_wifi = build_wifi_chip(ui.home, SX(640) - 6 - WIFI_CHIP_W, SY(12), SY(44), SY(12), ui.home_bars,
                                   &ui.home_wifi_x);
    usb_chip(ui.home, SX(640), SY(12));

    ui.clock_time = label(ui.home, "Time not set", F_CLOCK, 0xFFFFFF);
    lv_obj_set_pos(ui.clock_time, SX(16), SY(74));
    lv_obj_set_width(ui.clock_time, SX(408));
    lv_label_set_long_mode(ui.clock_time, LV_LABEL_LONG_CLIP);
    lv_obj_set_style_text_align(ui.clock_time, LV_TEXT_ALIGN_CENTER, 0);
    ui.clock_date = label(ui.home, "", F_TEXT, COL_MUTED);
    lv_obj_set_pos(ui.clock_date, SX(16), SY(140));
    lv_obj_set_width(ui.clock_date, SX(408));
    lv_label_set_long_mode(ui.clock_date, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(ui.clock_date, LV_TEXT_ALIGN_CENTER, 0);

    label(ui.home, "Classroom", F_CAPTION, COL_MUTED);
    lv_obj_t *cl = lv_obj_get_child(ui.home, -1);
    lv_obj_set_pos(cl, SX(18), SY(184));

    /* ADJ: 44 px tall (scaled 36) for touch; same top edge. */
    ui.room_dd = make_dropdown(ui.home, s_room_opts);
    lv_obj_set_pos(ui.room_dd, SX(16), SY(210));
    lv_obj_set_size(ui.room_dd, SX(408), TOUCH_MIN);
    lv_obj_add_event_cb(ui.room_dd, on_choice, LV_EVENT_VALUE_CHANGED, NULL);

    ui.record_btn = lv_button_create(ui.home);
    lv_obj_set_pos(ui.record_btn, SX(497), SY(72));
    lv_obj_set_size(ui.record_btn, SX(230), SY(216));
    style_box(ui.record_btn, COL_RED, SY(16));
    lv_obj_set_flex_flow(ui.record_btn, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(ui.record_btn, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_row(ui.record_btn, SY(8), 0);
    lv_obj_add_event_cb(ui.record_btn, on_record, LV_EVENT_CLICKED, NULL);

    lv_obj_t *ring = lv_obj_create(ui.record_btn);
    lv_obj_set_size(ring, SY(76), SY(76));
    style_box(ring, COL_RED, LV_RADIUS_CIRCLE);
    lv_obj_set_style_bg_opa(ring, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(ring, SY(10), 0);
    lv_obj_set_style_border_color(ring, lv_color_white(), 0);
    lv_obj_remove_flag(ring, LV_OBJ_FLAG_CLICKABLE);

    label(ui.record_btn, "Record", F_BIG, 0xFFFFFF);
    ui.record_hint = label(ui.record_btn, "Tap to start a session", F_CAPTION, COL_PINK);
    lv_obj_set_width(ui.record_hint, SX(206));
    lv_label_set_long_mode(ui.record_hint, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_align(ui.record_hint, LV_TEXT_ALIGN_CENTER, 0);

    lv_obj_t *card = lv_obj_create(ui.home);
    lv_obj_set_pos(card, SX(16), SY(300));
    lv_obj_set_size(card, SX(768), SY(64));
    style_box(card, COL_CARD, SY(12));
    lv_obj_t *st_lbl = label(card, "Storage", F_CAPTION, COL_MUTED);
    lv_obj_set_pos(st_lbl, SX(16), SY(10));

    lv_obj_t *row = lv_obj_create(card);
    lv_obj_set_pos(row, SX(100), SY(4));
    lv_obj_set_size(row, SX(400), SY(28));
    style_box(row, COL_CARD, 0);
    lv_obj_set_style_bg_opa(row, LV_OPA_TRANSP, 0);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_END, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(row, 0, 0);
    lv_obj_set_scrollbar_mode(row, LV_SCROLLBAR_MODE_OFF);
    ui.storage_main = label(row, "-", F_TEXT, 0xFFFFFF);
    ui.storage_of = label(row, "", F_TEXT, COL_MUTED);

    ui.upload_lbl = label(card, "", F_CAPTION, COL_MUTED);
    lv_obj_set_pos(ui.upload_lbl, SX(452), SY(10));
    lv_obj_set_width(ui.upload_lbl, SX(300));
    lv_label_set_long_mode(ui.upload_lbl, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(ui.upload_lbl, LV_TEXT_ALIGN_RIGHT, 0);

    ui.storage_bar = lv_bar_create(card);
    lv_obj_set_pos(ui.storage_bar, SX(16), SY(40));
    lv_obj_set_size(ui.storage_bar, SX(736), SY(12));
    lv_bar_set_range(ui.storage_bar, 0, 100);
    lv_obj_set_style_bg_color(ui.storage_bar, lv_color_hex(COL_LINE), LV_PART_MAIN);
    lv_obj_set_style_bg_color(ui.storage_bar, lv_color_hex(COL_BLUE), LV_PART_INDICATOR);
    lv_obj_set_style_radius(ui.storage_bar, SY(6), LV_PART_MAIN);
    lv_obj_set_style_radius(ui.storage_bar, SY(6), LV_PART_INDICATOR);

    /* Recent activity: title + 4 rows, newest first. 12 px font on 14 px
     * rows (scaled 20 -> 13, too tight for any readable font). */
    lv_obj_t *bar = lv_obj_create(ui.home);
    lv_obj_set_pos(bar, 0, SY(372));
    lv_obj_set_size(bar, BOARD_UI_W, BOARD_UI_H - SY(372));
    style_box(bar, COL_STATUS, 0);
    /* ADJ: title x 10..418 (scaled 461 wide) so the firmware version fits
     * right-aligned in x 422..470 on the same line (y 249..264), clear of
     * the activity rows below (y 263+ for the first row's text box). */
    ui.act_title = label(bar, "Recent activity", F_CAPTION, COL_MUTED);
    lv_obj_set_pos(ui.act_title, SX(16), 1);
    lv_obj_set_width(ui.act_title, SX(768) - 53);
    lv_label_set_long_mode(ui.act_title, LV_LABEL_LONG_DOT);
    version_caption(bar, F_CAPTION, 48, SX(16), 1);
    /* Rows of 14 px from y 15 (band is 72 px tall, so exactly 4 rows). A
     * long message takes two rows, wrapped at a 14 px line pitch (the 12 px
     * font's own line height is 15, so line space -1), and pushes older
     * rows down, see paint_activity(). */
    s_act_y0 = 15;
    s_act_pitch = 14;
    s_act_band_h = BOARD_UI_H - SY(372);
    s_act_msg_w = SX(680) - 4;
    for (int i = 0; i < ACT_ROWS; i++) {
        int y = s_act_y0 + i * s_act_pitch;
        ui.act_time[i] = label(bar, "", F_CAPTION, COL_MUTED);
        lv_obj_set_pos(ui.act_time[i], SX(16), y);
        lv_obj_set_width(ui.act_time[i], SX(84) + 4);
        lv_label_set_long_mode(ui.act_time[i], LV_LABEL_LONG_CLIP);
        ui.act_msg[i] = label(bar, "", F_CAPTION, 0xFFFFFF);
        lv_obj_set_pos(ui.act_msg[i], SX(104) + 4, y);
        lv_obj_set_size(ui.act_msg[i], s_act_msg_w, lv_font_get_line_height(F_CAPTION));
        lv_obj_set_style_text_line_space(ui.act_msg[i], s_act_pitch - lv_font_get_line_height(F_CAPTION), 0);
        lv_label_set_long_mode(ui.act_msg[i], LV_LABEL_LONG_DOT);
    }
}

static lv_obj_t *overlay(void)
{
    lv_obj_t *o = lv_obj_create(lv_screen_active());
    lv_obj_set_size(o, BOARD_UI_W, BOARD_UI_H);
    lv_obj_set_pos(o, 0, 0);
    style_box(o, 0x000000, 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_50, 0);
    lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
    return o;
}

static void build_msgbox(void)
{
    ui.msgbox = overlay();
    lv_obj_add_event_cb(ui.msgbox, on_msg_ok, LV_EVENT_CLICKED, NULL);

    /* ADJ: card 170 px tall (scaled 153) to keep 3-4 text lines above a
     * 44 px OK button. */
    const int cw = SX(540), ch = 170;
    lv_obj_t *card = lv_obj_create(ui.msgbox);
    lv_obj_set_size(card, cw, ch);
    lv_obj_center(card);
    style_box(card, COL_MODAL, SY(16));
    lv_obj_set_style_border_width(card, 2, 0);
    lv_obj_set_style_border_color(card, lv_color_hex(COL_END_BD), 0);

    ui.msg_title = label(card, "", F_TITLE, 0xFFFFFF);
    lv_obj_set_pos(ui.msg_title, SX(24), SY(20));
    lv_obj_set_width(ui.msg_title, SX(492));
    lv_label_set_long_mode(ui.msg_title, LV_LABEL_LONG_DOT);
    ui.msg_text = label(card, "", F_TEXT, COL_MUTED);
    lv_obj_set_pos(ui.msg_text, SX(24), SY(64));
    lv_obj_set_width(ui.msg_text, SX(492));
    lv_label_set_long_mode(ui.msg_text, LV_LABEL_LONG_WRAP);

    lv_obj_t *ok = lv_button_create(card);
    lv_obj_set_size(ok, SX(160), TOUCH_MIN);
    lv_obj_set_pos(ok, cw - SX(24) - SX(160), ch - SY(20) - TOUCH_MIN);
    style_box(ok, COL_SAVE, SY(12));
    lv_obj_add_event_cb(ok, on_msg_ok, LV_EVENT_CLICKED, NULL);
    lv_obj_t *t = label(ok, "OK", F_TEXT, 0xFFFFFF);
    lv_obj_center(t);
}

static void build_session(void)
{
    ui.session = screen_root();
    lv_obj_add_flag(ui.session, LV_OBJ_FLAG_HIDDEN);
    /* 0.6.3 ADJ: same icon-only Wi-Fi chip as home; the brand block ends 6 px
     * before it. */
    add_brand(ui.session, "Classroom Session", SX(640) - 6 - WIFI_CHIP_W - 6);
    ui.ses_wifi = build_wifi_chip(ui.session, SX(640) - 6 - WIFI_CHIP_W, SY(10), SY(42), SY(12), ui.ses_bars,
                                  &ui.ses_wifi_x);
    usb_chip(ui.session, SX(640), SY(10));

    /* 0.6.8 ADJ: the red-bordered session card runs the full width (x 7..473,
     * y 43..248). Left column (x 10..248 in the card): badge, start time,
     * timer, mic meter, classroom. Right, inside the same card: the three
     * marker buttons stacked (Incident, Teacher Notes, Bug), 196 x 58 each.
     * Teacher and Class are no longer shown; see the hidden dropdowns below. */
    ui.session_card = lv_obj_create(ui.session);
    lv_obj_set_pos(ui.session_card, SX(12), SY(64));
    lv_obj_set_size(ui.session_card, BOARD_UI_W - 2 * SX(12), SY(308));
    style_box(ui.session_card, 0x121A2B, SY(16));
    lv_obj_set_style_border_width(ui.session_card, 2, 0);
    lv_obj_set_style_border_color(ui.session_card, lv_color_hex(COL_RED), 0);

    ui.badge = lv_obj_create(ui.session_card);
    lv_obj_set_pos(ui.badge, SX(16), SY(16));
    /* ADJ: 112 px wide (scaled 101) for "Recording" with its icon in 14 px. */
    lv_obj_set_size(ui.badge, 112, SY(36));
    style_box(ui.badge, COL_RED, SY(18));
    ui.badge_lbl = label(ui.badge, "Recording", F_TEXT, 0xFFFFFF);
    lv_obj_center(ui.badge_lbl);

    ui.started = label(ui.session_card, "Starting microphone...", F_CAPTION, COL_MUTED);
    lv_obj_set_pos(ui.started, SX(16), 42);
    lv_obj_set_width(ui.started, SX(392));
    lv_label_set_long_mode(ui.started, LV_LABEL_LONG_WRAP);

    ui.timer = label(ui.session_card, "00:00:00", F_CLOCK, 0xFFFFFF);
    lv_obj_set_pos(ui.timer, SX(16), 72);

    label(ui.session_card, "Mic level", F_CAPTION, COL_MUTED);
    lv_obj_set_pos(lv_obj_get_child(ui.session_card, -1), SX(16), 114);
    ui.meter_state = label(ui.session_card, "Quiet", F_CAPTION, COL_MUTED);
    lv_obj_set_pos(ui.meter_state, SX(230), 114);

    for (int i = 0; i < METER_BARS; i++) {
        ui.meter[i] = lv_obj_create(ui.session_card);
        lv_obj_set_size(ui.meter[i], SX(12), SY(28));
        lv_obj_set_pos(ui.meter[i], SX(16) + i * 11, 132);
        style_box(ui.meter[i], COL_OFF, 2);
    }

    /* Classroom: bottom of the left column (was in the right column). */
    label(ui.session_card, "Classroom", F_CAPTION, COL_MUTED);
    lv_obj_set_pos(lv_obj_get_child(ui.session_card, -1), SX(16), 162);
    ui.room_value = label(ui.session_card, "", F_TITLE, 0xFFFFFF);
    lv_obj_set_pos(ui.room_value, SX(16), 177);
    lv_obj_set_width(ui.room_value, SX(392));
    lv_label_set_long_mode(ui.room_value, LV_LABEL_LONG_DOT);

    for (int i = 0; i < MARKER_TYPE_COUNT; i++) {
        make_mark_btn(ui.session_card, i, 256, 8 + i * (58 + 6), 196, 58, 10, F_BIG);
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

    /* Pause | End: two 229 px buttons, 8 px gap, x 7..473 (Mark removed). */
    const int bw = (BOARD_UI_W - 2 * SX(12) - 8) / 2;
    ui.pause_btn = lv_button_create(ui.session);
    lv_obj_set_pos(ui.pause_btn, SX(12), SY(386));
    lv_obj_set_size(ui.pause_btn, bw, SY(80));
    style_box(ui.pause_btn, COL_YELLOW, SY(16));
    lv_obj_add_event_cb(ui.pause_btn, on_pause, LV_EVENT_CLICKED, NULL);
    ui.pause_lbl = label(ui.pause_btn, LV_SYMBOL_PAUSE "  Pause", F_BIG, 0x1A1200);
    lv_obj_center(ui.pause_lbl);

    lv_obj_t *end = lv_button_create(ui.session);
    lv_obj_set_pos(end, SX(12) + bw + 8, SY(386));
    lv_obj_set_size(end, bw, SY(80));
    style_box(end, COL_END, SY(16));
    lv_obj_set_style_border_width(end, 2, 0);
    lv_obj_set_style_border_color(end, lv_color_hex(COL_END_BD), 0);
    lv_obj_add_event_cb(end, on_end, LV_EVENT_CLICKED, NULL);
    lv_obj_t *end_lbl = label(end, LV_SYMBOL_STOP "  End", F_BIG, 0xFFFFFF);
    lv_obj_center(end_lbl);
}

/* ADJ: caption 16 px above a 44 px dropdown (scaled: 16 px above 35 px). */
static lv_obj_t *modal_dropdown(lv_obj_t *card, const char *caption, const char *opts, int x, int y, int w)
{
    lv_obj_t *cap = label(card, caption, F_CAPTION, COL_MUTED);
    lv_obj_set_pos(cap, x, y);
    lv_obj_t *dd = make_dropdown(card, opts);
    lv_obj_set_pos(dd, x, y + 16);
    lv_obj_set_size(dd, w, TOUCH_MIN);
    lv_obj_add_event_cb(dd, on_choice, LV_EVENT_VALUE_CHANGED, NULL);
    return dd;
}

static void build_modal(void)
{
    ui.modal = overlay();

    lv_obj_t *card = lv_obj_create(ui.modal);
    lv_obj_set_size(card, SX(640), SY(400));
    lv_obj_center(card);
    style_box(card, COL_MODAL, SY(16));
    lv_obj_set_style_border_width(card, 1, 0);
    lv_obj_set_style_border_color(card, lv_color_hex(COL_LINE), 0);

    label(card, "End and save this recording?", F_BIG, 0xFFFFFF);
    lv_obj_set_pos(lv_obj_get_child(card, 0), SX(24), SY(18));
    ui.modal_dur = label(card, "Duration 00:00:00", F_CAPTION, COL_MUTED);
    lv_obj_set_pos(ui.modal_dur, SX(24), SY(62));
    /* 0.6.4 ADJ: duration + start time across the full card width (354 px),
     * the autosave countdown on its own line below it. Before, the two shared
     * a line: a long duration wrapped "PM" into the Classroom caption and ran
     * under "Autosaves in". Longest line the firmware can produce,
     * "Duration 34:57:09  -  started 12:58:58 PM" (WAV 4 GB stop = 34:57:09),
     * is 240 px. The dropdowns moved down 10 px to make room; the buttons did
     * not move (6 px gap left above them). */
    lv_obj_set_width(ui.modal_dur, SX(640) - 2 * SX(24) - 2);
    lv_label_set_long_mode(ui.modal_dur, LV_LABEL_LONG_DOT);
    ui.modal_countdown = label(card, "Autosaves in 4:00", F_TEXT, COL_AMBER);
    lv_obj_set_pos(ui.modal_countdown, SX(24), SY(62) + 17);
    lv_obj_set_width(ui.modal_countdown, SX(640) - 2 * SX(24) - 2);

    /* Classroom across the full width, Teacher | Class below (RBT removed in 0.6.0). */
    ui.modal_room = modal_dropdown(card, "Classroom", s_room_opts, SX(24), SY(104) + 10, SX(328) + SX(280) - SX(24));
    ui.modal_teacher = modal_dropdown(card, "Teacher", s_teacher_opts, SX(24), SY(196) + 10, SX(280));
    ui.modal_class = modal_dropdown(card, "Class", s_class_opts, SX(328), SY(196) + 10, SX(280));

    /* ADJ: buttons 44 px tall (scaled 43), bottom edge kept. */
    ui.keep_btn = lv_button_create(card);
    lv_obj_set_pos(ui.keep_btn, SX(24), SY(312) - 1);
    lv_obj_set_size(ui.keep_btn, SX(280), TOUCH_MIN);
    style_box(ui.keep_btn, COL_KEEP, SY(14));
    lv_obj_add_event_cb(ui.keep_btn, on_keep, LV_EVENT_CLICKED, NULL);
    lv_obj_t *keep = label(ui.keep_btn, "Keep recording", F_TEXT, 0xFFFFFF);
    lv_obj_center(keep);

    ui.save_btn = lv_button_create(card);
    lv_obj_set_pos(ui.save_btn, SX(328), SY(312) - 1);
    lv_obj_set_size(ui.save_btn, SX(288), TOUCH_MIN);
    style_box(ui.save_btn, COL_SAVE, SY(14));
    lv_obj_add_event_cb(ui.save_btn, on_save, LV_EVENT_CLICKED, NULL);
    ui.save_lbl = label(ui.save_btn, "Save & finish", F_TEXT, 0xFFFFFF);
    lv_obj_center(ui.save_lbl);
}

/* Incident / Bug confirm card (0.6.8): nearly full screen (464 x 304) so
 * the explanation reads in 16 px; big Cancel | OK (210 x 64). Text comes
 * from MARKER_TYPES (marker_types.h). */
static void build_mark_popup(void)
{
    ui.mark = overlay();
    lv_obj_set_style_bg_opa(ui.mark, LV_OPA_60, 0);

    const int cw = BOARD_UI_W - 16, ch = BOARD_UI_H - 16, pad = 14, bh = 64, gap = 12;
    lv_obj_t *card = lv_obj_create(ui.mark);
    lv_obj_set_size(card, cw, ch);
    lv_obj_center(card);
    style_box(card, COL_MODAL, SY(16));
    lv_obj_set_style_border_width(card, 2, 0);
    lv_obj_set_style_border_color(card, lv_color_hex(COL_LINE), 0);

    ui.mark_title = label(card, "", F_BIG, 0xFFFFFF);
    lv_obj_set_pos(ui.mark_title, pad, 10);
    lv_obj_set_width(ui.mark_title, cw - 2 * pad - 4);
    lv_label_set_long_mode(ui.mark_title, LV_LABEL_LONG_DOT);
    ui.mark_when = label(card, "", F_CAPTION, COL_MUTED);
    lv_obj_set_pos(ui.mark_when, pad, 38);
    lv_obj_set_width(ui.mark_when, cw - 2 * pad - 4);
    lv_label_set_long_mode(ui.mark_when, LV_LABEL_LONG_DOT);
    /* Up to 7 lines of 16 px (y 60..~210); the longest (Bug) is 4-5. */
    ui.mark_text = label(card, "", F_TITLE, 0xFFFFFF);
    lv_obj_set_pos(ui.mark_text, pad, 60);
    lv_obj_set_size(ui.mark_text, cw - 2 * pad - 4, ch - 4 - 60 - bh - 2 * 12);
    lv_label_set_long_mode(ui.mark_text, LV_LABEL_LONG_WRAP);

    const int bw = (cw - 4 - 2 * pad - gap) / 2;
    lv_obj_t *cancel = lv_button_create(card);
    lv_obj_set_pos(cancel, pad, ch - 4 - 12 - bh);
    lv_obj_set_size(cancel, bw, bh);
    style_box(cancel, COL_KEEP, SY(14));
    lv_obj_add_event_cb(cancel, on_mark_cancel, LV_EVENT_CLICKED, NULL);
    lv_obj_t *cl = label(cancel, "Cancel", F_BIG, 0xFFFFFF);
    lv_obj_center(cl);

    lv_obj_t *ok = lv_button_create(card);
    lv_obj_set_pos(ok, pad + bw + gap, ch - 4 - 12 - bh);
    lv_obj_set_size(ok, bw, bh);
    style_box(ok, COL_SAVE, SY(14));
    lv_obj_add_event_cb(ok, on_mark_ok, LV_EVENT_CLICKED, NULL);
    lv_obj_t *okl = label(ok, "OK", F_BIG, 0xFFFFFF);
    lv_obj_center(okl);
}
