#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Pure helpers for the uploader (no ESP-IDF deps, unit-tested on the host). */

/* Cloud object stem for a local recording stem, matching the upload-api rule
 * ^\d{8}_\d{6}_[A-Za-z0-9-]{1,16}$. The last part is the recorder's Class
 * code (what the pipeline's roster is keyed by), falling back to the code in
 * the local name. A local "_N" duplicate suffix becomes "-N".
 * Returns false if the local stem does not start with a date and time
 * (e.g. NOTIME_... recordings made before the clock was set). */
bool upload_remote_stem(const char *local_stem, const char *class_code, char *out, size_t n);

/* The Class code as it appears in cloud file names and in the sidecar's
 * "class_code": [A-Za-z0-9-] only, spaces/underscores become '-', at most
 * 16 characters (e.g. "Reading 3A" -> "Reading-3A"). Returns the length. */
size_t upload_class_code(char *dst, size_t n, const char *src);

/* "<object>|<generation>|<size>|<md5>|<crc32c>|verified" - the string the
 * upload-api HMACs with the device token to make the receipt. */
int upload_receipt_msg(char *out, size_t n, const char *object, const char *generation, uint64_t size,
                       const char *md5_b64, const char *crc32c_b64);

/* Device tokens are URL-safe base64 (secrets.token_urlsafe), 20-128 chars. */
bool upload_token_ok(const char *t);

/* Constant-time compare of two NUL-terminated strings. */
bool upload_str_eq_ct(const char *a, const char *b);
