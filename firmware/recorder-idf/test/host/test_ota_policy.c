/* Host tests for OTA rollback and the rules that block an update.
 *   cc -Wall -Wextra -Werror -Imain test/host/test_ota_policy.c main/ota_policy.c \
 *      main/ota_sign.c main/tweetnacl.c -o /tmp/to && /tmp/to
 * TweetNaCl itself is not -Werror-clean; compile it without -Werror if needed.
 */
#include "ota_policy.h"
#include "ota_sign.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

static const char *URL =
    "https://storage.googleapis.com/example-project-firmware/firmware/hosyond35/recorder-0.8.1.bin";
static const char *SHA = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
static const char *CANON =
    "APOTA1\n"
    "0.8.1\n"
    "https://storage.googleapis.com/example-project-firmware/firmware/hosyond35/recorder-0.8.1.bin\n"
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n";
/* Test key only (seed 0..31). Not the firmware key. Private half is not on the device. */
static const uint8_t TEST_PUB[32] = {
    0x03, 0xa1, 0x07, 0xbf, 0xf3, 0xce, 0x10, 0xbe, 0x1d, 0x70, 0xdd, 0x18, 0xe7, 0x4b, 0xc0, 0x99,
    0x67, 0xe4, 0xd6, 0x30, 0x9b, 0xa5, 0x0d, 0x5f, 0x1d, 0xdc, 0x86, 0x64, 0x12, 0x55, 0x31, 0xb8};
static const uint8_t TEST_SIG[64] = {
    0x6a, 0x9d, 0x9d, 0xad, 0x96, 0x4d, 0x7e, 0x1d, 0x7f, 0xbf, 0x18, 0x5c, 0x26, 0xc3, 0xb8, 0x0f,
    0x45, 0xe8, 0x38, 0xeb, 0xbe, 0xc7, 0xc3, 0x41, 0x28, 0x82, 0x1b, 0x55, 0xcb, 0x16, 0x5c, 0x2d,
    0xb7, 0x95, 0x01, 0x02, 0xa4, 0xa0, 0x9e, 0xa5, 0xde, 0xe6, 0x8d, 0xd7, 0x5e, 0x08, 0x16, 0x60,
    0x9e, 0x11, 0x2f, 0xdc, 0x93, 0xfc, 0x40, 0xf9, 0x4b, 0xa0, 0xbd, 0xe6, 0x96, 0xa0, 0xd7, 0x08};
static const char *SIG_B64 =
    "ap2drZZNfh1/vxhcJsO4D0XoOOu+x8NBKIIbVcsWXC23lQECpKCepd7mjddeCBZgnhEv3JP8QPlLoL3mlqDXCA==";

static ota_gate_in_t idle_full(void)
{
    ota_gate_in_t g;
    memset(&g, 0, sizeof g);
    g.has_battery = true;
    g.battery_known = true;
    g.battery_pct = 80;
    return g;
}

static void test_gate(void)
{
    ota_gate_in_t g = idle_full();
    assert(ota_gate(NULL) == OTA_BLOCK_RECORDING);
    assert(ota_gate(&g) == OTA_ALLOW);

    g.session_active = true;
    assert(ota_gate(&g) == OTA_BLOCK_RECORDING);
    g.upload_busy = true;
    g.battery_pct = 5;
    assert(ota_gate(&g) == OTA_BLOCK_RECORDING); /* recording wins */

    g = idle_full();
    g.upload_busy = true;
    assert(ota_gate(&g) == OTA_BLOCK_UPLOAD);
    g.upload_busy = false;
    g.upload_waiting = 1;
    assert(ota_gate(&g) == OTA_BLOCK_UPLOAD);
    g.upload_waiting = 0;
    g.upload_retry_wait = true;
    assert(ota_gate(&g) == OTA_BLOCK_UPLOAD);
    assert(strcmp(ota_block_code(OTA_BLOCK_UPLOAD), "upload") == 0);

    g = idle_full();
    g.battery_pct = 24;
    assert(ota_gate(&g) == OTA_BLOCK_BATTERY);
    g.battery_pct = 25;
    assert(ota_gate(&g) == OTA_ALLOW);
    g.battery_pct = 0;
    g.charging = true;
    assert(ota_gate(&g) == OTA_ALLOW); /* plugged in */
    g.charging = false;
    g.battery_known = false;
    g.battery_pct = -1;
    assert(ota_gate(&g) == OTA_BLOCK_BATTERY); /* Hosyond, reading not in yet */

    g = idle_full();
    g.has_battery = false; /* CrowPanel */
    g.battery_known = false;
    g.battery_pct = -1;
    assert(ota_gate(&g) == OTA_ALLOW);

    assert(OTA_BATTERY_MIN_PCT == 25);
    assert(OTA_CHECK_INTERVAL_S == 6 * 60 * 60);
    assert(OTA_ROLLBACK_TIMEOUT_S == 10 * 60);
}

static void test_rollback(void)
{
    ota_rb_in_t r;
    memset(&r, 0, sizeof r);
    assert(ota_rollback_decide(NULL) == OTA_RB_HOLD);
    assert(ota_rollback_decide(&r) == OTA_RB_HOLD);
    r.timed_out = true;
    r.wifi_up = true;
    assert(ota_rollback_decide(&r) == OTA_RB_HOLD); /* not an OTA boot */

    r.pending_verify = true;
    r.timed_out = false;
    r.app_booted = true;
    r.wifi_up = false;
    r.upload_api_reached = false;
    assert(ota_rollback_decide(&r) == OTA_RB_WAIT);

    r.wifi_up = true;
    assert(ota_rollback_decide(&r) == OTA_RB_WAIT); /* API not reached */

    r.upload_api_reached = true;
    r.app_booted = false;
    assert(ota_rollback_decide(&r) == OTA_RB_WAIT);

    r.app_booted = true;
    r.timed_out = true;
    assert(ota_rollback_decide(&r) == OTA_RB_CONFIRM); /* reached beats the timeout */

    r.upload_api_reached = false;
    r.wifi_up = true;
    assert(ota_rollback_decide(&r) == OTA_RB_ROLLBACK);

    r.wifi_up = false;
    assert(ota_rollback_decide(&r) == OTA_RB_ROLLBACK);

    r.timed_out = false;
    r.wifi_up = true;
    r.upload_api_reached = true;
    r.app_booted = true;
    assert(ota_rollback_decide(&r) == OTA_RB_CONFIRM);
}

static void test_version_and_url(void)
{
    char canon[OTA_CANON_MAX];
    int n;
    assert(ota_version_ok("0.8.0"));
    assert(ota_version_ok("10.0.1"));
    assert(!ota_version_ok("0.8"));
    assert(!ota_version_ok("0.8.0-pilot"));
    assert(!ota_version_ok("v0.8.0"));
    assert(!ota_is_newer("0.8.0", "0.8.0"));
    assert(!ota_is_newer("0.8.0", "0.7.9"));
    assert(ota_is_newer("0.8.0", "0.8.1"));
    assert(ota_is_newer("0.7.8", "0.8.0"));
    assert(ota_is_newer("0.9.9", "1.0.0"));
    assert(!ota_is_newer("0.8.0", "nope"));
    assert(!ota_is_newer("nope", "0.8.1"));

    assert(ota_url_ok(URL, "example-project-firmware"));
    assert(!ota_url_ok(URL, "example-project")); /* prefix of the bucket name is not the bucket */
    assert(!ota_url_ok("http://storage.googleapis.com/example-project-firmware/a.bin",
                       "example-project-firmware"));
    assert(!ota_url_ok("https://storage.googleapis.com/other-bucket/a.bin",
                       "example-project-firmware"));
    assert(!ota_url_ok("https://evil.example/example-project-firmware/a.bin",
                       "example-project-firmware"));
    assert(!ota_url_ok("https://storage.googleapis.com/example-project-firmware/../a.bin",
                       "example-project-firmware"));
    assert(!ota_url_ok("https://user:pw@storage.googleapis.com/example-project-firmware/a.bin",
                       "example-project-firmware"));

    n = ota_canonical(canon, sizeof canon, "0.8.1", URL, SHA);
    assert(n == (int)strlen(CANON));
    assert(strcmp(canon, CANON) == 0);
    assert(ota_canonical(canon, sizeof canon, "0.8.1", "https://example.com/a.bin", SHA) < 0);
}

static void test_signature(void)
{
    uint8_t raw[64];
    uint8_t zeros[32];
    uint8_t bad[64];
    char json[800];
    ota_manifest_t m;
    assert(ota_b64_decode(SIG_B64, raw, sizeof raw) == 64);
    assert(memcmp(raw, TEST_SIG, 64) == 0);
    assert(ota_signature_ok(TEST_PUB, TEST_SIG, CANON, strlen(CANON)));
    memset(zeros, 0, sizeof zeros);
    assert(!ota_pubkey_ready(zeros));
    assert(ota_pubkey_ready(TEST_PUB));
    assert(!ota_signature_ok(zeros, TEST_SIG, CANON, strlen(CANON)));
    memcpy(bad, TEST_SIG, 64);
    bad[0] ^= 0x01;
    assert(!ota_signature_ok(TEST_PUB, bad, CANON, strlen(CANON)));
    assert(!ota_signature_ok(TEST_PUB, TEST_SIG, "APOTA1\n0.8.0\n", 14));

    snprintf(json, sizeof json,
             "{\"version\":\"0.8.1\",\"url\":\"%s\",\"sha256\":\"%s\",\"signature\":\"%s\"}", URL, SHA, SIG_B64);
    assert(ota_manifest_fields(json, "example-project-firmware", &m) == 0);
    assert(strcmp(m.canon, CANON) == 0);
    assert(ota_signature_ok(TEST_PUB, m.sig, m.canon, (size_t)m.canon_len));
    assert(ota_is_newer("0.8.0", m.version));
    assert(!ota_is_newer("0.8.1", m.version));

    {
        char bad_b64[96];
        snprintf(bad_b64, sizeof bad_b64, "AAAA%s", SIG_B64 + 4);
        snprintf(json, sizeof json,
                 "{\"version\":\"0.8.1\",\"url\":\"%s\",\"sha256\":\"%s\",\"signature\":\"%s\"}",
                 URL, SHA, bad_b64);
    }
    assert(ota_manifest_fields(json, "example-project-firmware", &m) == 0);
    assert(!ota_signature_ok(TEST_PUB, m.sig, m.canon, (size_t)m.canon_len));

    assert(ota_manifest_fields("{\"version\":\"0.8.1\"}", "example-project-firmware", &m) != 0);
    assert(ota_manifest_fields("{\"version\":\"0.8.1\",\"url\":\"https://storage.googleapis.com/other/a.bin\","
                               "\"sha256\":\"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\","
                               "\"signature\":\"aaaa\"}",
                               "example-project-firmware", &m) != 0);
}

int main(void)
{
    test_gate();
    test_rollback();
    test_version_and_url();
    test_signature();
    puts("ota policy OK");
    return 0;
}
