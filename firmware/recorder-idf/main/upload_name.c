#include "upload_name.h"

#include <ctype.h>
#include <stdio.h>
#include <string.h>

#define CODE_MAX 16

static size_t clean_code(char *dst, size_t cap, const char *src)
{
    size_t j = 0;
    for (size_t i = 0; src && src[i] && j + 1 < cap; i++) {
        unsigned char c = (unsigned char)src[i];
        if (isalnum(c) || c == '-') {
            dst[j++] = (char)c;
        } else if ((c == ' ' || c == '_') && j > 0 && dst[j - 1] != '-') {
            dst[j++] = '-';
        }
    }
    while (j > 0 && dst[j - 1] == '-') {
        j--;
    }
    dst[j] = '\0';
    return j;
}

size_t upload_class_code(char *dst, size_t n, const char *src)
{
    char tmp[64];
    size_t len = clean_code(tmp, sizeof tmp, src);
    if (len > CODE_MAX) {
        len = CODE_MAX;
        while (len > 0 && tmp[len - 1] == '-') {
            len--;
        }
        tmp[len] = '\0';
    }
    if (dst && n) {
        snprintf(dst, n, "%s", tmp);
    }
    return len;
}

bool upload_remote_stem(const char *local_stem, const char *class_code, char *out, size_t n)
{
    if (!local_stem || !out || n < 17 + CODE_MAX) {
        return false;
    }
    /* YYYYMMDD_HHMMSS_ */
    for (int i = 0; i < 15; i++) {
        char c = local_stem[i];
        if (i == 8 ? c != '_' : !isdigit((unsigned char)c)) {
            return false;
        }
    }
    if (local_stem[15] != '_') {
        return false;
    }
    const char *rest = local_stem + 16;

    /* Duplicate suffix "_N" added by the recorder when the name was taken. */
    char suffix[8] = "";
    char base[64];
    strncpy(base, rest, sizeof base - 1);
    base[sizeof base - 1] = '\0';
    char *us = strrchr(base, '_');
    if (us && us[1]) {
        bool digits = true;
        for (const char *p = us + 1; *p; p++) {
            digits &= isdigit((unsigned char)*p) != 0;
        }
        if (digits && strlen(us + 1) <= 3) {
            snprintf(suffix, sizeof suffix, "-%s", us + 1);
            *us = '\0';
        }
    }

    char code[64];
    size_t len = 0;
    if (class_code && class_code[0]) {
        len = clean_code(code, sizeof code, class_code);
    }
    if (len == 0) {
        len = clean_code(code, sizeof code, base);
    }
    if (len == 0) {
        strcpy(code, "rec");
        len = 3;
    }
    size_t room = CODE_MAX - strlen(suffix);
    if (len > room) {
        code[room] = '\0';
        while (room > 0 && code[room - 1] == '-') {
            code[--room] = '\0';
        }
    }
    snprintf(out, n, "%.15s_%s%s", local_stem, code, suffix);
    return true;
}

int upload_receipt_msg(char *out, size_t n, const char *object, const char *generation, uint64_t size,
                       const char *md5_b64, const char *crc32c_b64)
{
    return snprintf(out, n, "%s|%s|%llu|%s|%s|verified", object ? object : "", generation ? generation : "",
                    (unsigned long long)size, md5_b64 ? md5_b64 : "None", crc32c_b64 ? crc32c_b64 : "None");
}

bool upload_token_ok(const char *t)
{
    size_t n = t ? strlen(t) : 0;
    if (n < 20 || n > 128) {
        return false;
    }
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)t[i];
        if (!(isalnum(c) || c == '-' || c == '_')) {
            return false;
        }
    }
    return true;
}

bool upload_str_eq_ct(const char *a, const char *b)
{
    size_t la = strlen(a), lb = strlen(b);
    unsigned char d = (unsigned char)(la != lb);
    size_t n = la < lb ? la : lb;
    for (size_t i = 0; i < n; i++) {
        d |= (unsigned char)(a[i] ^ b[i]);
    }
    return d == 0;
}
