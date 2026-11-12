#include "config_store.h"

#include "cJSON.h"
#include "esp_log.h"
#include "sdkconfig.h"
#include "sdcard.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *TAG = "config";

#define MAX_ROOMS 24
#define MAX_PEOPLE 32
#define MAX_CLASSES 32
#define LABEL_LEN 48
#define CODE_LEN 32

extern const uint8_t sample_device_json_start[] asm("_binary_sample_device_json_start");
extern const uint8_t sample_device_json_end[] asm("_binary_sample_device_json_end");

typedef struct {
    char code[CODE_LEN];
    char label[LABEL_LEN];
} room_t;

static room_t s_rooms[MAX_ROOMS];
static int s_nrooms;
static char s_teachers[MAX_PEOPLE][LABEL_LEN];
static int s_nteachers;
static char s_classes[MAX_CLASSES][LABEL_LEN];
static int s_nclasses;
/* 0.8.2: the header is always the goalvera logo (ui_brand.inc). The old
 * school_name / badge_text keys in device.json, the CONFIG_RECORDER_BRAND_*
 * build overrides and the admin-settings school name are all ignored. */
#define PRODUCT_NAME "goalvera"
static bool s_sample;

static void copy_str(char *dst, size_t n, const char *src)
{
    if (!src) {
        src = "";
    }
    size_t j = 0;
    for (size_t i = 0; src[i] && j + 1 < n; i++) {
        char c = src[i];
        if (c == '\n' || c == '\r' || c == '\t') {
            c = ' ';
        }
        dst[j++] = c;
    }
    dst[j] = '\0';
}

static void clear_lists(void)
{
    s_nrooms = s_nteachers = s_nclasses = 0;
}

static bool parse_doc(const char *text, bool sample)
{
    cJSON *root = cJSON_Parse(text);
    if (!root) {
        ESP_LOGW(TAG, "JSON parse failed");
        return false;
    }
    if (cJSON_GetObjectItemCaseSensitive(root, "students") ||
        cJSON_GetObjectItemCaseSensitive(root, "student_map")) {
        ESP_LOGW(TAG, "ignoring student mapping — it must not live on the device");
    }
    clear_lists();
    if (!sample && (cJSON_GetObjectItemCaseSensitive(root, "school_name") ||
                    cJSON_GetObjectItemCaseSensitive(root, "badge_text"))) {
        ESP_LOGI(TAG, "device.json school_name/badge_text ignored: the header is always goalvera");
    }
    cJSON *rooms = cJSON_GetObjectItemCaseSensitive(root, "classrooms");
    cJSON *room;
    if (cJSON_IsArray(rooms))
    cJSON_ArrayForEach(room, rooms) {
        if (s_nrooms >= MAX_ROOMS || !cJSON_IsObject(room)) {
            continue;
        }
        cJSON *code = cJSON_GetObjectItemCaseSensitive(room, "code");
        cJSON *label = cJSON_GetObjectItemCaseSensitive(room, "label");
        if (!cJSON_IsString(code) || code->valuestring[0] == '\0') {
            continue;
        }
        copy_str(s_rooms[s_nrooms].code, CODE_LEN, code->valuestring);
        if (cJSON_IsString(label) && label->valuestring[0]) {
            copy_str(s_rooms[s_nrooms].label, LABEL_LEN, label->valuestring);
        } else {
            copy_str(s_rooms[s_nrooms].label, LABEL_LEN, code->valuestring);
        }
        s_nrooms++;
    }
    cJSON *teachers = cJSON_GetObjectItemCaseSensitive(root, "teachers");
    cJSON *item = NULL;
    if (cJSON_IsArray(teachers))
    cJSON_ArrayForEach(item, teachers) {
        if (s_nteachers >= MAX_PEOPLE || !cJSON_IsString(item) || item->valuestring[0] == '\0') {
            continue;
        }
        copy_str(s_teachers[s_nteachers], LABEL_LEN, item->valuestring);
        s_nteachers++;
    }
    /* An "rbts" list from an older device.json is ignored (field removed in 0.6.0). */
    cJSON *classes = cJSON_GetObjectItemCaseSensitive(root, "classes");
    if (cJSON_IsArray(classes))
    cJSON_ArrayForEach(item, classes) {
        if (s_nclasses >= MAX_CLASSES || !cJSON_IsString(item) || item->valuestring[0] == '\0') {
            continue;
        }
        copy_str(s_classes[s_nclasses], LABEL_LEN, item->valuestring);
        s_nclasses++;
    }
    cJSON_Delete(root);
    return s_nrooms > 0;
}

static bool load_text(const char *text, bool sample)
{
    if (!parse_doc(text, sample)) {
        return false;
    }
    s_sample = sample;
    ESP_LOGI(TAG, "%s: %d rooms, %d teachers, %d classes",
             sample ? "built-in sample" : "/sd/config/device.json",
             s_nrooms, s_nteachers, s_nclasses);
    return true;
}

static bool load_sample(void)
{
    size_t n = (size_t)(sample_device_json_end - sample_device_json_start);
    char *buf = malloc(n + 1);
    if (!buf) {
        return false;
    }
    memcpy(buf, sample_device_json_start, n);
    buf[n] = '\0';
    bool ok = load_text(buf, true);
    free(buf);
    return ok;
}

static bool load_sd(void)
{
    if (!sd_mounted()) {
        return false;
    }
    FILE *f = fopen("/sd/config/device.json", "rb");
    if (!f) {
        return false;
    }
    char *buf = malloc(16384);
    if (!buf) {
        fclose(f);
        return false;
    }
    size_t n = fread(buf, 1, 16383, f);
    fclose(f);
    buf[n] = '\0';
    bool ok = load_text(buf, false);
    free(buf);
    return ok;
}

void config_load(void)
{
    if (load_sd()) {
        return;
    }
    ESP_LOGW(TAG, "no usable /sd/config/device.json — using the built-in sample");
    if (!load_sample()) {
        ESP_LOGE(TAG, "built-in sample config failed to parse");
        clear_lists();
        s_sample = true;
    }
}

bool config_used_sample(void) { return s_sample; }
const char *config_product_name(void) { return PRODUCT_NAME; }

int config_classroom_count(void) { return s_nrooms; }
const char *config_classroom_code(int i)
{
    return (i >= 0 && i < s_nrooms) ? s_rooms[i].code : "";
}
const char *config_classroom_label(int i)
{
    return (i >= 0 && i < s_nrooms) ? s_rooms[i].label : "";
}

int config_teacher_count(void) { return s_nteachers; }
const char *config_teacher(int i)
{
    return (i >= 0 && i < s_nteachers) ? s_teachers[i] : "";
}

int config_class_count(void) { return s_nclasses; }
const char *config_class_code(int i)
{
    return (i >= 0 && i < s_nclasses) ? s_classes[i] : "";
}
