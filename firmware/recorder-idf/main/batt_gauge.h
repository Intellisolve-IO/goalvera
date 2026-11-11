#pragma once
/*
 * Hosyond top-right battery gauge (0.7.1). LVGL only, no ESP-IDF,
 * so a host LVGL render can draw exactly this code (previews for the PR).
 *
 * Inside the status chip: a battery outline with a nub, a fill bar that
 * spans the whole inside of the outline in proportion to the %, coloured
 * green (> 25%), yellow (10-25%) or red (< 10%). No number. A lightning
 * bolt (dark-edged so it reads on any fill) is drawn on top when USB power
 * is present: a computer is connected or a charger was detected. No battery
 * fitted: empty outline + bolt.
 */
#include "battery_model.h"
#include "lvgl.h"

#define BG_OUTLINE  0xC8D0DC
#define BG_GREEN    0x3DDC6A
#define BG_YELLOW   0xF5B301
#define BG_RED      0xE2231A
#define BG_BOLT     0xFFFFFF
#define BG_BOLT_EDGE 0x0B1220

#define BG_BODY_W   62
#define BG_BODY_H   19
#define BG_BORDER   2
#define BG_GAP      1
#define BG_NUB_W    4
#define BG_NUB_H    9
#define BG_FILL_MAX_W (BG_BODY_W - 2 * (BG_BORDER + BG_GAP))
#define BG_FILL_H     (BG_BODY_H - 2 * (BG_BORDER + BG_GAP))

typedef struct {
    lv_obj_t *body;
    lv_obj_t *fill;
    lv_obj_t *bolt_edge[4];
    lv_obj_t *bolt;
} batt_gauge_t;

static inline lv_obj_t *bg__box(lv_obj_t *parent)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_CLICKABLE);
    return o;
}

static inline lv_obj_t *bg__bolt(lv_obj_t *parent, lv_obj_t *over, uint32_t col, int dx, int dy)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_label_set_text(l, LV_SYMBOL_CHARGE);
    lv_obj_set_style_text_font(l, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(l, lv_color_hex(col), 0);
    /* A little top padding keeps the glyph's top edge inside the label box
     * (no clipping); the -1 centres the glyph itself on the outline. */
    lv_obj_set_style_pad_top(l, 4, 0);
    lv_obj_align_to(l, over, LV_ALIGN_CENTER, dx, dy - 1);
    lv_obj_add_flag(l, LV_OBJ_FLAG_HIDDEN);
    return l;
}

/* Builds the gauge centred in `chip` (the 89 x 29 status chip). */
static inline void batt_gauge_create(batt_gauge_t *g, lv_obj_t *chip)
{
    g->body = bg__box(chip);
    lv_obj_set_size(g->body, BG_BODY_W, BG_BODY_H);
    lv_obj_align(g->body, LV_ALIGN_CENTER, -(BG_NUB_W + 1) / 2, 0);
    lv_obj_set_style_border_width(g->body, BG_BORDER, 0);
    lv_obj_set_style_border_color(g->body, lv_color_hex(BG_OUTLINE), 0);
    lv_obj_set_style_radius(g->body, 4, 0);

    lv_obj_t *nub = bg__box(chip);
    lv_obj_set_size(nub, BG_NUB_W, BG_NUB_H);
    lv_obj_align_to(nub, g->body, LV_ALIGN_OUT_RIGHT_MID, 1, 0);
    lv_obj_set_style_bg_opa(nub, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(nub, lv_color_hex(BG_OUTLINE), 0);
    lv_obj_set_style_radius(nub, 1, 0);

    g->fill = bg__box(g->body);
    lv_obj_set_pos(g->fill, BG_GAP, BG_GAP); /* children start inside the border */
    lv_obj_set_size(g->fill, 0, BG_FILL_H);
    lv_obj_set_style_bg_opa(g->fill, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(g->fill, 2, 0);

    static const int8_t d[4][2] = {{-1, 0}, {1, 0}, {0, -1}, {0, 1}};
    for (int i = 0; i < 4; i++) {
        g->bolt_edge[i] = bg__bolt(chip, g->body, BG_BOLT_EDGE, d[i][0], d[i][1]);
    }
    g->bolt = bg__bolt(chip, g->body, BG_BOLT, 0, 0); /* on the chip, so it isn't clipped by the outline */
}

static inline void batt_gauge_paint(batt_gauge_t *g, batt_gauge_view_t v)
{
    if (v.fill_pct < 0) {
        lv_obj_add_flag(g->fill, LV_OBJ_FLAG_HIDDEN);
    } else {
        int p = v.fill_pct > 100 ? 100 : v.fill_pct;
        int w = (p * BG_FILL_MAX_W + 50) / 100;
        if (p > 0 && w < 2) {
            w = 2; /* a sliver stays visible down to 1% */
        }
        static const uint32_t col[] = {BG_RED, BG_YELLOW, BG_GREEN};
        lv_obj_set_width(g->fill, w);
        lv_obj_set_style_bg_color(g->fill, lv_color_hex(col[batt_level(p)]), 0);
        if (w > 0) {
            lv_obj_remove_flag(g->fill, LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(g->fill, LV_OBJ_FLAG_HIDDEN);
        }
    }
    for (int i = 0; i < 4; i++) {
        if (v.bolt) {
            lv_obj_remove_flag(g->bolt_edge[i], LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(g->bolt_edge[i], LV_OBJ_FLAG_HIDDEN);
        }
    }
    if (v.bolt) {
        lv_obj_remove_flag(g->bolt, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(g->bolt, LV_OBJ_FLAG_HIDDEN);
    }
}
