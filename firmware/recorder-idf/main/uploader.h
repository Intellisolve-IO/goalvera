#pragma once

#include <stdbool.h>
#include <stdint.h>

/* 0.7.6: every HTTPS call takes this lock, so only one TLS session exists at a
 * time. wait_ms UINT32_MAX waits forever. Recursive. */
bool uploader_net_lock(uint32_t wait_ms);
void uploader_net_unlock(void);
/* Enough internal RAM to start a TLS session without starving Wi-Fi/SD? */
bool uploader_net_mem_ok(void);

/* Wi-Fi upload of finished recordings (.wav + .json sidecar) to the
 * the Goalvera upload-api. The SD copy is deleted ONLY after the service
 * returns verified=true with a receipt whose HMAC we recompute and whose
 * size/MD5/CRC32C match what we hashed locally. Anything else keeps the
 * file and retries later. Uploads run only while no session is active. */

typedef struct {
    bool enabled;    /* Wi-Fi configured and a device token present */
    bool no_token;   /* Wi-Fi configured but no token */
    bool busy;       /* an upload is in progress */
    int pct;         /* 0-100 while busy */
    int waiting;     /* finished recordings on the SD card not yet uploaded */
    bool retry_wait; /* last attempt failed; waiting to retry */
    /* Option B pilot cap (pilot_guard.h). Updated while no session runs. */
    int64_t backlog_audio_s; /* seconds of finished audio not yet uploaded */
    int64_t oldest_age_s;    /* age of the oldest unsent recording, -1 if unknown */
    int backlog;             /* pg_level_t: 0 ok, 1 warn, 2 block new recordings */
} upload_status_t;

/* app_main, after sd_init()/config_load() and BEFORE the display starts:
 * installs /sd/config/upload_token.txt into NVS (then wipes and deletes the
 * file) and loads the token. */
void uploader_init(void);
/* app_main, after wifi_start_if_configured(). */
void uploader_start(void);
void uploader_get_status(upload_status_t *out);
/* Auto sleep: hold = no new upload attempt starts (the one running finishes
 * normally; a file is only deleted after a verified receipt either way). */
void uploader_hold(bool hold);
/* True when no upload attempt is running. */
bool uploader_idle(void);

/* Teacher Notes QR (0.7.2,). Blocking HTTPS call: never from the
 * LVGL thread. Asks the upload-api for a one-time phone upload link tied to
 * the recording local_wav (the cloud name uses class_code, as for uploads).
 * 0: url (https://.../u/<token>) and *ttl_s set. -1: err holds a short reason.
 * The url is a secret for its 5 minutes: it is shown as a QR code only. */
#include <stddef.h>
int uploader_media_token(const char *local_wav, const char *class_code, char *url, size_t url_cap,
                         int *ttl_s, char *err, size_t err_cap);

/* School display name from the review site's admin settings (POST /v1/device/school,
 * this recorder's upload key). 0: out holds the name. -1: no key, offline or error. */
int uploader_school_name(char *out, size_t cap);
