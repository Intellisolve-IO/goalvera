/* Host test for rec_crypt (APENC1). Needs host mbedTLS (ESP-IDF's copy):
 *   M=$IDF_PATH/components/mbedtls/mbedtls
 *   (build libmbedcrypto.a from all of $M/library, see .github/workflows/firmware-idf.yml)
 *   cc -Wall -Imain -I$M/include test/host/test_rec_crypt.c main/rec_crypt.c libmbedcrypto.a -o /tmp/trc
 *   /tmp/trc pub.pem out_dir     (then app/backend-pipeline/tests/test_recording_crypto.py decrypts)
 * Writes vectors: a.enc (10000 B, uneven), b.enc (exactly 2 chunks), c.enc (empty),
 * d.enc (a.enc cut mid-chunk), plus a.pcm/b.pcm with the plaintext. */
#include "rec_crypt.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int rng(void *ctx, unsigned char *out, size_t len)
{
    FILE *f = (FILE *)ctx;
    return fread(out, 1, len, f) == len ? 0 : -1;
}
static int wr(void *ctx, const uint8_t *b, size_t n) { return fwrite(b, 1, n, (FILE *)ctx) == n ? 0 : -1; }

static rec_crypt_t C;

static long make(const char *dir, const char *name, const unsigned char *pem, size_t pl, FILE *ur, size_t n, int odd)
{
    char path[512];
    snprintf(path, sizeof path, "%s/%s.enc", dir, name);
    FILE *f = fopen(path, "wb");
    assert(f);
    uint8_t hdr[REC_CRYPT_HDR_MAX];
    size_t hl = 0;
    rec_crypt_params_t p = {16000, 1, 16};
    assert(rec_crypt_begin(&C, pem, pl, "recordings/1", &p, rng, ur, hdr, sizeof hdr, &hl) == 0);
    assert(memcmp(hdr, "APENC1\n", 8) == 0 && hl == 34 + 12 + 384);
    fwrite(hdr, 1, hl, f);
    uint8_t *pcm = malloc(n + 1);
    for (size_t i = 0; i < n; i++) pcm[i] = (uint8_t)(i * 7 + 3);
    /* feed in uneven pieces like the ring buffer does */
    size_t done = 0, step = odd ? 1000 : 4096;
    while (done < n) {
        size_t k = n - done < step ? n - done : step;
        assert(rec_crypt_update(&C, pcm + done, k, wr, f) == 0);
        done += k;
    }
    assert(rec_crypt_final(&C, wr, f) == 0);
    assert(C.active == 0); /* key wiped */
    long sz = ftell(f);
    fclose(f);
    assert((uint64_t)sz == hl + rec_crypt_body_size(n));
    snprintf(path, sizeof path, "%s/%s.pcm", dir, name);
    f = fopen(path, "wb");
    fwrite(pcm, 1, n, f);
    fclose(f);
    free(pcm);
    return sz;
}

int main(int argc, char **argv)
{
    assert(argc == 3);
    FILE *pf = fopen(argv[1], "rb");
    assert(pf);
    unsigned char pem[4096];
    size_t pl = fread(pem, 1, sizeof pem - 1, pf);
    fclose(pf);
    pem[pl++] = 0;
    FILE *ur = fopen("/dev/urandom", "rb");

    /* bad key is refused */
    uint8_t hdr[REC_CRYPT_HDR_MAX];
    size_t hl;
    rec_crypt_params_t p = {16000, 1, 16};
    assert(rec_crypt_begin(&C, (const unsigned char *)"junk", 5, "k/1", &p, rng, ur, hdr, sizeof hdr, &hl) != 0);

    make(argv[2], "a", pem, pl, ur, 10000, 1);
    make(argv[2], "b", pem, pl, ur, 8192, 0);
    make(argv[2], "c", pem, pl, ur, 0, 0);
    /* d = a cut in the middle of chunk 2 (power loss) */
    char src[512], dst[512];
    snprintf(src, sizeof src, "%s/a.enc", argv[2]);
    snprintf(dst, sizeof dst, "%s/d.enc", argv[2]);
    FILE *a = fopen(src, "rb"), *d = fopen(dst, "wb");
    uint8_t buf[430 + 4112 + 2000];
    size_t got = fread(buf, 1, sizeof buf, a);
    fwrite(buf, 1, got, d);
    fclose(a);
    fclose(d);
    fclose(ur);
    printf("rec_crypt host test OK\n");
    return 0;
}
