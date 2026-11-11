/*: move a plaintext NVS partition into encrypted NVS in place.
 * Called once, on the first boot after flash encryption is enabled, when the
 * nvs_keys partition is still blank. Values are copied in RAM only and are
 * never logged (only counts). If any entry cannot be read, nothing is erased
 * and the caller keeps running on the plaintext partition. */
#include "nvs_migrate.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "nvs.h"
#include "nvs_flash.h"
#include <stdlib.h>
#include <string.h>

static const char *TAG = "nvs_mig";

typedef struct {
    char ns[NVS_NS_NAME_MAX_SIZE];
    char key[NVS_KEY_NAME_MAX_SIZE];
    nvs_type_t type;
    size_t len;
    void *data;
} ent_t;

typedef struct { ent_t *e; int n, cap; } snap_t;

static void snap_free(snap_t *s)
{
    for (int i = 0; i < s->n; i++) {
        if (s->e[i].data) { memset(s->e[i].data, 0, s->e[i].len); free(s->e[i].data); }
    }
    free(s->e);
    memset(s, 0, sizeof *s);
}

static size_t int_len(nvs_type_t t)
{
    switch (t) {
    case NVS_TYPE_U8: case NVS_TYPE_I8: return 1;
    case NVS_TYPE_U16: case NVS_TYPE_I16: return 2;
    case NVS_TYPE_U32: case NVS_TYPE_I32: return 4;
    case NVS_TYPE_U64: case NVS_TYPE_I64: return 8;
    default: return 0;
    }
}

static esp_err_t read_one(nvs_handle_t h, ent_t *e)
{
    esp_err_t err;
    size_t il = int_len(e->type);
    if (il) {
        e->len = il;
        e->data = calloc(1, 8);
        if (!e->data) return ESP_ERR_NO_MEM;
        switch (e->type) {
        case NVS_TYPE_U8:  return nvs_get_u8(h, e->key, e->data);
        case NVS_TYPE_I8:  return nvs_get_i8(h, e->key, e->data);
        case NVS_TYPE_U16: return nvs_get_u16(h, e->key, e->data);
        case NVS_TYPE_I16: return nvs_get_i16(h, e->key, e->data);
        case NVS_TYPE_U32: return nvs_get_u32(h, e->key, e->data);
        case NVS_TYPE_I32: return nvs_get_i32(h, e->key, e->data);
        case NVS_TYPE_U64: return nvs_get_u64(h, e->key, e->data);
        case NVS_TYPE_I64: return nvs_get_i64(h, e->key, e->data);
        default: return ESP_ERR_INVALID_ARG;
        }
    }
    size_t n = 0;
    if (e->type == NVS_TYPE_STR) err = nvs_get_str(h, e->key, NULL, &n);
    else if (e->type == NVS_TYPE_BLOB) err = nvs_get_blob(h, e->key, NULL, &n);
    else return ESP_ERR_NOT_SUPPORTED;
    if (err != ESP_OK) return err;
    e->len = n;
    e->data = malloc(n ? n : 1);
    if (!e->data) return ESP_ERR_NO_MEM;
    return e->type == NVS_TYPE_STR ? nvs_get_str(h, e->key, e->data, &n)
                                   : nvs_get_blob(h, e->key, e->data, &n);
}

static esp_err_t write_one(nvs_handle_t h, const ent_t *e)
{
    switch (e->type) {
    case NVS_TYPE_U8:  return nvs_set_u8(h, e->key, *(uint8_t *)e->data);
    case NVS_TYPE_I8:  return nvs_set_i8(h, e->key, *(int8_t *)e->data);
    case NVS_TYPE_U16: return nvs_set_u16(h, e->key, *(uint16_t *)e->data);
    case NVS_TYPE_I16: return nvs_set_i16(h, e->key, *(int16_t *)e->data);
    case NVS_TYPE_U32: return nvs_set_u32(h, e->key, *(uint32_t *)e->data);
    case NVS_TYPE_I32: return nvs_set_i32(h, e->key, *(int32_t *)e->data);
    case NVS_TYPE_U64: return nvs_set_u64(h, e->key, *(uint64_t *)e->data);
    case NVS_TYPE_I64: return nvs_set_i64(h, e->key, *(int64_t *)e->data);
    case NVS_TYPE_STR: return nvs_set_str(h, e->key, e->data);
    case NVS_TYPE_BLOB: return nvs_set_blob(h, e->key, e->data, e->len);
    default: return ESP_ERR_NOT_SUPPORTED;
    }
}

/* Read every entry of an initialised partition into RAM. */
static esp_err_t snapshot(const char *part, snap_t *s)
{
    nvs_iterator_t it = NULL;
    esp_err_t err = nvs_entry_find(part, NULL, NVS_TYPE_ANY, &it);
    while (err == ESP_OK) {
        nvs_entry_info_t info;
        nvs_entry_info(it, &info);
        if (s->n == s->cap) {
            int cap = s->cap ? s->cap * 2 : 32;
            ent_t *ne = realloc(s->e, cap * sizeof *ne);
            if (!ne) { nvs_release_iterator(it); return ESP_ERR_NO_MEM; }
            s->e = ne; s->cap = cap;
        }
        ent_t *e = &s->e[s->n];
        memset(e, 0, sizeof *e);
        strlcpy(e->ns, info.namespace_name, sizeof e->ns);
        strlcpy(e->key, info.key, sizeof e->key);
        e->type = info.type;
        s->n++;
        err = nvs_entry_next(&it);
    }
    nvs_release_iterator(it);
    if (err != ESP_ERR_NVS_NOT_FOUND) return err;
    for (int i = 0; i < s->n; i++) {
        nvs_handle_t h;
        err = nvs_open_from_partition(part, s->e[i].ns, NVS_READONLY, &h);
        if (err == ESP_OK) { err = read_one(h, &s->e[i]); nvs_close(h); }
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "read entry %d (type 0x%02x) failed: %s", i, s->e[i].type, esp_err_to_name(err));
            return err;
        }
    }
    return ESP_OK;
}

static int count_namespaces(const snap_t *s)
{
    int n = 0;
    for (int i = 0; i < s->n; i++) {
        int seen = 0;
        for (int j = 0; j < i; j++) if (!strcmp(s->e[i].ns, s->e[j].ns)) { seen = 1; break; }
        n += !seen;
    }
    return n;
}

esp_err_t nvs_migrate_dry_run(void)
{
    snap_t s = {0};
    esp_err_t err = snapshot(NVS_DEFAULT_PART_NAME, &s);
    ESP_LOGW(TAG, "dry run: %d entries in %d namespaces read: %s", s.n, count_namespaces(&s), esp_err_to_name(err));
    snap_free(&s);
    return err;
}

#if CONFIG_NVS_ENCRYPTION
esp_err_t nvs_migrate_init(void)
{
    const nvs_sec_scheme_t *scheme = nvs_flash_get_default_security_scheme();
    if (!scheme) {
        ESP_LOGE(TAG, "no NVS security scheme registered");
        return ESP_ERR_INVALID_STATE;
    }
    nvs_sec_cfg_t cfg = {0};
    esp_err_t err = nvs_flash_read_security_cfg_v2(scheme, &cfg);
    if (err == ESP_OK) {
        err = nvs_flash_secure_init_partition(NVS_DEFAULT_PART_NAME, &cfg);
        memset(&cfg, 0, sizeof cfg);
        return err;
    }
    if (err != ESP_ERR_NVS_KEYS_NOT_INITIALIZED) {
        ESP_LOGE(TAG, "nvs_keys unreadable (%s): not touching NVS", esp_err_to_name(err));
        return err;
    }

    /* First boot with encryption: nvs_keys blank, NVS still plaintext. */
    snap_t s = {0};
    err = nvs_flash_init_partition(NVS_DEFAULT_PART_NAME);
    if (err == ESP_OK) {
        err = snapshot(NVS_DEFAULT_PART_NAME, &s);
        nvs_flash_deinit_partition(NVS_DEFAULT_PART_NAME);
    } else if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "plaintext NVS not readable (%s): starting empty", esp_err_to_name(err));
        err = ESP_OK;
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "snapshot failed (%s): keeping plaintext NVS, keys NOT generated", esp_err_to_name(err));
        snap_free(&s);
        return nvs_flash_init_partition(NVS_DEFAULT_PART_NAME) == ESP_OK ? ESP_ERR_INVALID_STATE : err;
    }
    ESP_LOGW(TAG, "migrating %d entries in %d namespaces to encrypted NVS", s.n, count_namespaces(&s));

    err = nvs_flash_generate_keys_v2(scheme, &cfg);
    if (err == ESP_OK) err = nvs_flash_erase_partition(NVS_DEFAULT_PART_NAME);
    if (err == ESP_OK) err = nvs_flash_secure_init_partition(NVS_DEFAULT_PART_NAME, &cfg);
    memset(&cfg, 0, sizeof cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "encrypted NVS setup failed: %s", esp_err_to_name(err));
        snap_free(&s);
        return err;
    }
    int ok = 0;
    for (int i = 0; i < s.n; i++) {
        nvs_handle_t h;
        esp_err_t e = nvs_open(s.e[i].ns, NVS_READWRITE, &h);
        if (e == ESP_OK) {
            e = write_one(h, &s.e[i]);
            if (e == ESP_OK) e = nvs_commit(h);
            nvs_close(h);
        }
        if (e == ESP_OK) ok++;
        else ESP_LOGE(TAG, "write entry %d (type 0x%02x): %s", i, s.e[i].type, esp_err_to_name(e));
    }
    /* Verify by re-reading everything from the encrypted partition. */
    snap_t v = {0};
    esp_err_t ve = snapshot(NVS_DEFAULT_PART_NAME, &v);
    int match = 0;
    for (int i = 0; ve == ESP_OK && i < s.n; i++) {
        for (int j = 0; j < v.n; j++) {
            if (!strcmp(s.e[i].ns, v.e[j].ns) && !strcmp(s.e[i].key, v.e[j].key) && s.e[i].type == v.e[j].type
                && s.e[i].len == v.e[j].len && !memcmp(s.e[i].data, v.e[j].data, s.e[i].len)) { match++; break; }
        }
    }
    ESP_LOGW(TAG, "migration done: %d/%d written, %d/%d verified", ok, s.n, match, s.n);
    snap_free(&v);
    snap_free(&s);
    return ESP_OK;
}
#endif
