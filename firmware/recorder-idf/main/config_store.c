#include "config_store.h"

#include "cJSON.h"
#include "esp_log.h"
#include "sdkconfig.h"
#include "sdcard.h"
#include "nvs_prefs.h"
#include "freertos/FreeRTOS.h"
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
/* Neutral defaults. Real branding comes from /sd/config/device.json or a
 * local build override (CONFIG_RECORDER_BRAND_*), never from the repo. */
#define DEFAULT_SCHOOL "Classroom Recorder"
#define DEFAULT_BADGE  "CR"
#define BADGE_LEN 4 /* 1-3 characters + NUL */
static char s_school[LABEL_LEN] = DEFAULT_SCHOOL;
static char s_badge[BADGE_LEN] = DEFAULT_BADGE;
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

/* School name: keeps ONE line break (a real newline or the two characters
 * backslash-n, e.g. from Kconfig) to force a two-line header; any further
 * breaks and other control characters become spaces. */
static void copy_name(char *dst, size_t n, const char *src)
{
    if (!src) {
        src = "";
    }
    size_t j = 0;
    bool broke = false;
    for (size_t i = 0; src[i] && j + 1 < n; i++) {
        char c = src[i];
        bool is_break = c == '\n' || (c == '\\' && src[i + 1] == 'n');
        if (is_break) {
            if (c == '\\') {
                i++;
            }
            c = broke ? ' ' : '\n';
            broke = true;
        } else if (c == '\r' || c == '\t') {
            c = ' ';
        }
        dst[j++] = c;
    }
    dst[j] = '\0';
}

static bool badge_ok(const char *b)
{
    size_t n = b ? strlen(b) : 0;
    if (n < 1 || n > 3) {
        return false;
    }
    for (size_t i = 0; i < n; i++) {
        if ((unsigned char)b[i] < 0x21 || (unsigned char)b[i] > 0x7E) {
            return false; /* printable ASCII only (font coverage) */
        }
    }
    return true;
}

static void set_default_branding(void)
{
    const char *name = CONFIG_RECORDER_BRAND_NAME;
    const char *badge = CONFIG_RECORDER_BRAND_BADGE;
    copy_name(s_school, sizeof s_school, (name && name[0]) ? name : DEFAULT_SCHOOL);
    strlcpy(s_badge, badge_ok(badge) ? badge : DEFAULT_BADGE, sizeof s_badge);
}

static void clear_lists(void)
{
    s_nrooms = s_nteachers = s_nclasses = 0;
    set_default_branding();
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
    /* Branding from the card wins; the built-in sample's neutral values
     * never override a local build's branding. */
    cJSON *school = cJSON_GetObjectItemCaseSensitive(root, "school_name");
    if (!sample && cJSON_IsString(school) && school->valuestring[0]) {
        copy_name(s_school, sizeof s_school, school->valuestring);
    }
    cJSON *badge = cJSON_GetObjectItemCaseSensitive(root, "badge_text");
    if (!sample && cJSON_IsString(badge)) {
        if (badge_ok(badge->valuestring)) {
            strlcpy(s_badge, badge->valuestring, sizeof s_badge);
        } else {
            ESP_LOGW(TAG, "badge_text must be 1-3 printable ASCII characters; ignored");
        }
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

/* School name from the review site's admin settings (0.7.5): fetched by the
 * background net task on Wi-Fi connect, cached in NVS so the banner shows it
 * offline. It wins over the card / build name; "Classroom Recorder" only
 * shows when no name has ever been set anywhere. */
#define NVS_SCHOOL_KEY "school_srv"
static portMUX_TYPE s_school_mux = portMUX_INITIALIZER_UNLOCKED;
static char s_school_pending[LABEL_LEN];
static volatile bool s_school_dirty;

/* Printable ASCII only (the header fonts have no other glyphs). */
static void ascii_name(char *dst, size_t n, const char *src)
{
    size_t j = 0;
    bool space = true;
    for (size_t i = 0; src && src[i] && j + 1 < n; i++) {
        unsigned char c = (unsigned char)src[i];
        if (c < 0x20 || c > 0x7E) {
            c = ' ';
        }
        if (c == ' ' && space) {
            continue;
        }
        space = c == ' ';
        dst[j++] = (char)c;
    }
    while (j && dst[j - 1] == ' ') {
        j--;
    }
    dst[j] = '\0';
}

static void load_cached_school(void)
{
    char v[LABEL_LEN];
    if (prefs_get(NVS_SCHOOL_KEY, v, sizeof v) == ESP_OK && v[0]) {
        strlcpy(s_school, v, sizeof s_school);
        ESP_LOGI(TAG, "school name from admin settings (cached)");
    }
}

void config_load(void)
{
    if (load_sd()) {
        load_cached_school();
        return;
    }
    ESP_LOGW(TAG, "no usable /sd/config/device.json — using the built-in sample");
    if (!load_sample()) {
        ESP_LOGE(TAG, "built-in sample config failed to parse");
        clear_lists();
        s_sample = true;
    }
    load_cached_school();
}

bool config_school_offer(const char *name)
{
    char v[LABEL_LEN];
    ascii_name(v, sizeof v, name);
    if (!v[0]) {
        return false;
    }
    bool changed;
    taskENTER_CRITICAL(&s_school_mux);
    changed = strcmp(v, s_school) != 0 && strcmp(v, s_school_pending) != 0;
    if (changed) {
        strlcpy(s_school_pending, v, sizeof s_school_pending);
        s_school_dirty = true;
    }
    taskEXIT_CRITICAL(&s_school_mux);
    if (changed) {
        prefs_set_async(NVS_SCHOOL_KEY, v);
    }
    return changed;
}

bool config_school_apply_pending(void)
{
    if (!s_school_dirty) {
        return false;
    }
    taskENTER_CRITICAL(&s_school_mux);
    strlcpy(s_school, s_school_pending, sizeof s_school);
    s_school_pending[0] = '\0';
    s_school_dirty = false;
    taskEXIT_CRITICAL(&s_school_mux);
    return true;
}

bool config_used_sample(void) { return s_sample; }
const char *config_school_name(void) { return s_school; }
const char *config_badge_text(void) { return s_badge; }

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
