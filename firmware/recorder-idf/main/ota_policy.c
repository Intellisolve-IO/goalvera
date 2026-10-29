#include "ota_policy.h"

#include <stdio.h>
#include <string.h>

ota_block_t ota_gate(const ota_gate_in_t *in)
{
    if (!in || in->session_active) {
        return OTA_BLOCK_RECORDING;
    }
    if (in->upload_busy || in->upload_waiting > 0 || in->upload_retry_wait) {
        return OTA_BLOCK_UPLOAD;
    }
    if (in->has_battery && !in->charging) {
        if (!in->battery_known || in->battery_pct < OTA_BATTERY_MIN_PCT) {
            return OTA_BLOCK_BATTERY;
        }
    }
    return OTA_ALLOW;
}

const char *ota_block_code(ota_block_t b)
{
    switch (b) {
    case OTA_BLOCK_RECORDING:
        return "recording";
    case OTA_BLOCK_UPLOAD:
        return "upload";
    case OTA_BLOCK_BATTERY:
        return "battery";
    case OTA_ALLOW:
    default:
        return "ok";
    }
}

ota_rb_t ota_rollback_decide(const ota_rb_in_t *in)
{
    if (!in || !in->pending_verify) {
        return OTA_RB_HOLD;
    }
    /* A reached API confirms the image even if the clock then says we timed out. */
    if (in->app_booted && in->wifi_up && in->upload_api_reached) {
        return OTA_RB_CONFIRM;
    }
    if (in->timed_out) {
        return OTA_RB_ROLLBACK;
    }
    return OTA_RB_WAIT;
}

static bool parse_ver(const char *v, int out[3])
{
    int n = 0;
    int acc = -1;
    if (!v || !v[0]) {
        return false;
    }
    for (const char *p = v;; p++) {
        if (*p >= '0' && *p <= '9') {
            if (acc < 0) {
                acc = 0;
            }
            if (acc > 100000000) {
                return false;
            }
            acc = acc * 10 + (*p - '0');
        } else if (*p == '.' || *p == '\0') {
            if (acc < 0 || n >= 3) {
                return false;
            }
            out[n++] = acc;
            acc = -1;
            if (*p == '\0') {
                break;
            }
        } else {
            return false;
        }
    }
    return n == 3;
}

bool ota_version_ok(const char *v)
{
    int x[3];
    return parse_ver(v, x);
}

bool ota_is_newer(const char *running, const char *manifest)
{
    int a[3], b[3];
    if (!parse_ver(running, a) || !parse_ver(manifest, b)) {
        return false;
    }
    for (int i = 0; i < 3; i++) {
        if (b[i] != a[i]) {
            return b[i] > a[i];
        }
    }
    return false;
}

bool ota_sha256_hex_ok(const char *hex)
{
    if (!hex) {
        return false;
    }
    for (int i = 0; i < OTA_SHA256_HEX; i++) {
        char c = hex[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) {
            return false;
        }
    }
    return hex[OTA_SHA256_HEX] == '\0';
}

static bool bucket_ok(const char *bucket)
{
    size_t n;
    if (!bucket) {
        return false;
    }
    n = strlen(bucket);
    if (n < 3 || n > 63) {
        return false;
    }
    if (!((bucket[0] >= 'a' && bucket[0] <= 'z') || (bucket[0] >= '0' && bucket[0] <= '9'))) {
        return false;
    }
    if (!((bucket[n - 1] >= 'a' && bucket[n - 1] <= 'z') ||
          (bucket[n - 1] >= '0' && bucket[n - 1] <= '9'))) {
        return false;
    }
    for (size_t i = 0; i < n; i++) {
        char c = bucket[i];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.')) {
            return false;
        }
    }
    return true;
}

bool ota_url_ok(const char *url, const char *bucket)
{
    static const char prefix[] = "https://storage.googleapis.com/";
    const char *rest;
    const char *path;
    size_t bl;
    if (!url || !bucket_ok(bucket)) {
        return false;
    }
    if (strlen(url) > OTA_URL_MAX || strncmp(url, prefix, sizeof prefix - 1) != 0) {
        return false;
    }
    rest = url + (sizeof prefix - 1);
    bl = strlen(bucket);
    if (strncmp(rest, bucket, bl) != 0 || rest[bl] != '/') {
        return false;
    }
    path = rest + bl + 1;
    if (!path[0] || strstr(path, "..") || strstr(path, "//")) {
        return false;
    }
    for (const char *p = path; *p; p++) {
        unsigned char c = (unsigned char)*p;
        if (c <= 32 || c >= 127 || c == '?' || c == '#' || c == '\\' || c == '@') {
            return false;
        }
    }
    return true;
}

int ota_canonical(char *out, size_t cap, const char *version, const char *url, const char *sha256)
{
    int n;
    if (!out || !ota_version_ok(version) || !ota_sha256_hex_ok(sha256) || !url) {
        return -1;
    }
    if (strncmp(url, "https://storage.googleapis.com/", 31) != 0 || strlen(url) > OTA_URL_MAX) {
        return -1;
    }
    n = snprintf(out, cap, "APOTA1\n%s\n%s\n%s\n", version, url, sha256);
    if (n < 0 || (size_t)n >= cap) {
        return -1;
    }
    return n;
}

bool ota_pubkey_ready(const uint8_t pub[OTA_PUB_BYTES])
{
    if (!pub) {
        return false;
    }
    for (int i = 0; i < OTA_PUB_BYTES; i++) {
        if (pub[i] != 0) {
            return true;
        }
    }
    return false;
}

static int b64_val(char c)
{
    if (c >= 'A' && c <= 'Z') {
        return c - 'A';
    }
    if (c >= 'a' && c <= 'z') {
        return c - 'a' + 26;
    }
    if (c >= '0' && c <= '9') {
        return c - '0' + 52;
    }
    if (c == '+') {
        return 62;
    }
    if (c == '/') {
        return 63;
    }
    return -1;
}

int ota_b64_decode(const char *in, uint8_t *out, size_t cap)
{
    int val[4];
    size_t n = 0;
    int k = 0;
    if (!in || !out) {
        return -1;
    }
    for (const char *p = in; *p; p++) {
        if (*p == '=') {
            break;
        }
        val[k++] = b64_val(*p);
        if (val[k - 1] < 0) {
            return -1;
        }
        if (k < 4) {
            continue;
        }
        if (n + 3 > cap) {
            return -1;
        }
        out[n++] = (uint8_t)((val[0] << 2) | (val[1] >> 4));
        out[n++] = (uint8_t)((val[1] << 4) | (val[2] >> 2));
        out[n++] = (uint8_t)((val[2] << 6) | val[3]);
        k = 0;
    }
    if (k == 0) {
        return (int)n;
    }
    if (k == 1 || n >= cap) {
        return -1;
    }
    out[n++] = (uint8_t)((val[0] << 2) | (val[1] >> 4));
    if (k == 2) {
        return (int)n;
    }
    if (k != 3 || n >= cap) {
        return -1;
    }
    out[n++] = (uint8_t)((val[1] << 4) | (val[2] >> 2));
    return (int)n;
}

static const char *skip_ws(const char *p)
{
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') {
        p++;
    }
    return p;
}

static bool extract_string(const char *json, const char *key, char *out, size_t cap)
{
    char pat[40];
    const char *p = json;
    int pn = snprintf(pat, sizeof pat, "\"%s\"", key);
    if (pn < 0 || (size_t)pn >= sizeof pat) {
        return false;
    }
    while ((p = strstr(p, pat)) != NULL) {
        const char *q = skip_ws(p + (size_t)pn);
        size_t n = 0;
        if (*q != ':') {
            p += pn;
            continue;
        }
        q = skip_ws(q + 1);
        if (*q != '"') {
            return false;
        }
        q++;
        while (*q && *q != '"') {
            if (*q == '\\' || n + 1 >= cap) {
                return false;
            }
            out[n++] = *q++;
        }
        if (*q != '"') {
            return false;
        }
        out[n] = '\0';
        return true;
    }
    return false;
}

int ota_manifest_fields(const char *json, const char *bucket, ota_manifest_t *out)
{
    char sig_b64[128];
    int n;
    if (!json || !out || strlen(json) > OTA_MANIFEST_MAX) {
        return -1;
    }
    memset(out, 0, sizeof *out);
    if (!extract_string(json, "version", out->version, sizeof out->version) ||
        !extract_string(json, "url", out->url, sizeof out->url) ||
        !extract_string(json, "sha256", out->sha256, sizeof out->sha256) ||
        !extract_string(json, "signature", sig_b64, sizeof sig_b64)) {
        return -1;
    }
    if (!ota_version_ok(out->version) || !ota_sha256_hex_ok(out->sha256) || !ota_url_ok(out->url, bucket)) {
        return -1;
    }
    if (ota_b64_decode(sig_b64, out->sig, sizeof out->sig) != OTA_SIG_BYTES) {
        return -1;
    }
    n = ota_canonical(out->canon, sizeof out->canon, out->version, out->url, out->sha256);
    if (n < 0) {
        return -1;
    }
    out->canon_len = n;
    return 0;
}
