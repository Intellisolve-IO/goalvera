#pragma once

#include <stdbool.h>

void config_load(void);
bool config_used_sample(void);
/* Fixed product name "goalvera" (0.8.2). The header shows the goalvera logo;
 * no school name is shown anywhere on the device. */
const char *config_product_name(void);

int config_classroom_count(void);
const char *config_classroom_code(int index);
const char *config_classroom_label(int index);

int config_teacher_count(void);
const char *config_teacher(int index);

int config_class_count(void);
const char *config_class_code(int index);
