#pragma once
/*
 * OTA decisions that do not touch the network or the flash. Host-tested
 * (test/host/test_ota_policy.c). The device (ota_update.c) only applies an
 * image when ota_gate() allows it, and only marks that image valid when
 * ota_rollback_decide() says so.
 *
 * Signed bytes are the canonical manifest (ota_canonical): version, the
 * https://storage.googleapis.com object URL, and the image sha256. The
 * Ed25519 signature is carried beside those fields, not inside them.
 */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define OTA_VERSION_MAX 32
#define OTA_URL_MAX 512
#define OTA_SHA256_HEX 64
#define OTA_SIG_BYTES 64
#define OTA_PUB_BYTES 32
#define OTA_CANON_MAX 640
#define OTA_MANIFEST_MAX 4096

/* Below this, and not on external power, the recorder does not download. */
#define OTA_BATTERY_MIN_PCT 25
/* Idle check cadence after a successful manifest read. */
#define OTA_CHECK_INTERVAL_S (6 * 60 * 60)
/* Pending image: reach Wi-Fi and the upload API within this long, or roll back. */
#define OTA_ROLLBACK_TIMEOUT_S (10 * 60)

typedef enum {
    OTA_ALLOW = 0,
    OTA_BLOCK_RECORDING, /* session starting, recording, paused, or saving */
    OTA_BLOCK_UPLOAD,    /* an upload is running or a finished file is still unsent */
    OTA_BLOCK_BATTERY,   /* gauge present, not on external power, unknown or < 25% */
} ota_block_t;

typedef struct {
    bool session_active;
    bool upload_busy;
    int upload_waiting; /* finished recordings not yet uploaded */
    bool upload_retry_wait;
    bool has_battery; /* Hosyond gauge. CrowPanel has none and is not blocked. */
    bool battery_known;
    int battery_pct; /* 0-100 once known; ignored while charging */
    bool charging;   /* charger, USB host, or running from USB with no pack */
} ota_gate_in_t;

/* Recording wins over upload, upload wins over battery. NULL fails closed. */
ota_block_t ota_gate(const ota_gate_in_t *in);
const char *ota_block_code(ota_block_t b);

typedef enum {
    OTA_RB_HOLD = 0, /* this boot is not a pending OTA image */
    OTA_RB_WAIT,     /* pending; Wi-Fi or the upload API is not there yet */
    OTA_RB_CONFIRM,  /* booted, Wi-Fi up, and the upload API answered */
    OTA_RB_ROLLBACK, /* pending and the window closed without a confirm */
} ota_rb_t;

typedef struct {
    bool pending_verify;
    bool app_booted;
    bool wifi_up;
    bool upload_api_reached; /* TLS completed and an HTTP status came back */
    bool timed_out;
} ota_rb_in_t;

ota_rb_t ota_rollback_decide(const ota_rb_in_t *in);

bool ota_version_ok(const char *v);
/* True only when both sides are X.Y.Z and manifest is strictly newer. */
bool ota_is_newer(const char *running, const char *manifest);

bool ota_sha256_hex_ok(const char *hex);
/* https://storage.googleapis.com/<bucket>/<object> with no query, userinfo, or .. */
bool ota_url_ok(const char *url, const char *bucket);

/* "APOTA1\n<version>\n<url>\n<sha256>\n". Returns the length, or -1. */
int ota_canonical(char *out, size_t cap, const char *version, const char *url, const char *sha256);

/* All-zero key is the placeholder shipped in git. OTA must refuse it. */
bool ota_pubkey_ready(const uint8_t pub[OTA_PUB_BYTES]);

/* Standard base64 (padding optional). Returns the byte count, or -1. */
int ota_b64_decode(const char *in, uint8_t *out, size_t cap);

typedef struct {
    char version[OTA_VERSION_MAX + 1];
    char url[OTA_URL_MAX + 1];
    char sha256[OTA_SHA256_HEX + 1];
    uint8_t sig[OTA_SIG_BYTES];
    char canon[OTA_CANON_MAX];
    int canon_len;
} ota_manifest_t;

/* Extract and check the four manifest fields. Does not verify the signature.
 * json is a NUL-terminated document of at most OTA_MANIFEST_MAX bytes.
 * 0 on a well-formed manifest, -1 otherwise. */
int ota_manifest_fields(const char *json, const char *bucket, ota_manifest_t *out);
