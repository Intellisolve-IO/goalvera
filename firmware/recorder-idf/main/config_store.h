#pragma once

#include <stdbool.h>

void config_load(void);
bool config_used_sample(void);
const char *config_school_name(void);
/* Any task: offer the school name from the admin settings. Saved to NVS and
 * queued for the UI when it differs. Returns true if it changed. */
bool config_school_offer(const char *name);
/* LVGL thread only: take a queued name. True if config_school_name() changed. */
bool config_school_apply_pending(void);
/* 1-3 badge letters shown in the header (default "CR"). */
const char *config_badge_text(void);

int config_classroom_count(void);
const char *config_classroom_code(int index);
const char *config_classroom_label(int index);

int config_teacher_count(void);
const char *config_teacher(int index);

int config_class_count(void);
const char *config_class_code(int index);
