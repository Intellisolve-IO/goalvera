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
 * Goalvera upload-api. The SD copy is deleted ONLY after the service
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
    /* Option B (plaintext SD) cap (pilot_guard.h). Updated while no session runs. */
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

/* Teacher Notes QR (0.7.2). Blocking HTTPS call: never from the
 * LVGL thread. Asks the upload-api for a one-time phone upload link tied to
 * the recording local_wav (the cloud name uses class_code, as for uploads).
 * 0: url (https://.../u/<token>) and *ttl_s set. -1: err holds a short reason.
 * The url is a secret for its 5 minutes: it is shown as a QR code only. */
#include <stddef.h>
int uploader_media_token(const char *local_wav, const char *class_code, char *url, size_t url_cap,
                         int *ttl_s, char *err, size_t err_cap);

/* Serial admin (0.8.2, serial_cmd.c). An admin hold is separate from the
 * auto-sleep hold: while set, no upload starts. CONFIG_RECORDER_UPLOADS_START_HELD
 * sets it at boot (local one-off builds only); "uploads resume" clears it. */
void uploader_admin_hold(bool hold);
bool uploader_admin_held(void);
/* Print every file in /sd/rec (name, size, approx. minutes) and the totals to
 * the console. Returns the number of recordings (.wav). */
int uploader_queue_print(void);
/* Delete everything in /sd/rec (bytes zeroed, then unlinked), but only when
 * no session is active and no upload is running. Leaves the admin hold set.
 * Returns the number of files removed, or -1 if refused (reason printed). */
int uploader_queue_wipe(void);
/* One-line upload status for the console: url, device id, token source, state. */
void uploader_print_status(void);

/* POST JSON to the upload API with the device token. Returns the HTTP status,
 * or -1 if there is no token or the server was not reached. Takes the TLS lock. */
int uploader_api_post(const char *path, const char *json);
