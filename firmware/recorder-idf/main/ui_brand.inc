/*
 * Header brand (0.8.2), shared by both layouts: the goalvera lockup (G mark +
 * "goalvera" wordmark) from the www.goalvera.app homepage logo, on every
 * screen. It is a fixed image (main/goalvera_logo.c, made by
 * tools/make_logo.py), so no school name, badge letters, device.json value
 * or admin setting can replace it. An optional muted subtitle (e.g.
 * "Classroom session") follows the lockup.
 *
 * Needs HEADER_H, BRAND_X, BRAND_GAP, BRAND_LOGO_H and F_BRAND_SUB.
 */
extern const lv_image_dsc_t goalvera_logo;

static void add_brand(lv_obj_t *parent, const char *subtitle, int right_limit)
{
    int h = BRAND_LOGO_H;
    int w = (int)goalvera_logo.header.w * h / (int)goalvera_logo.header.h;
    lv_obj_t *logo = lv_image_create(parent);
    lv_image_set_src(logo, &goalvera_logo);
    lv_image_set_inner_align(logo, LV_IMAGE_ALIGN_STRETCH); /* no-op at native size */
    lv_obj_set_size(logo, w, h);
    lv_obj_set_pos(logo, BRAND_X, (HEADER_H - h) / 2);
    lv_obj_remove_flag(logo, LV_OBJ_FLAG_CLICKABLE);

    if (subtitle && subtitle[0]) {
        int x = BRAND_X + w + BRAND_GAP;
        lv_obj_t *sub = label(parent, subtitle, F_BRAND_SUB, COL_MUTED);
        lv_obj_set_width(sub, right_limit - x > 0 ? right_limit - x : 1);
        lv_label_set_long_mode(sub, LV_LABEL_LONG_DOT);
        int lh = (int)lv_font_get_line_height(F_BRAND_SUB);
        /* sit on the wordmark's baseline area, i.e. a little below centre */
        lv_obj_set_pos(sub, x, (HEADER_H - lh) / 2 + 2);
    }
}
