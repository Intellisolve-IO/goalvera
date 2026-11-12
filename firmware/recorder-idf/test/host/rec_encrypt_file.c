/* Encrypt a 16 kHz mono 16-bit WAV into an APENC1 file exactly as the
 * recorder writes it to the SD card (main/audio_rec.c + main/rec_crypt.c):
 * the APENC1 header, then the raw PCM (no WAV header) in AES-256-GCM chunks,
 * fed in 4 KB pieces like the recording ring buffer. For end-to-end tests
 * with MADE-UP audio only.
 *   cc -Wall -Imain -I$M/include test/host/rec_encrypt_file.c main/rec_crypt.c libmbedcrypto.a -o /tmp/rec_enc
 *   /tmp/rec_enc recording_pubkey.pem recording-wrap/1 in.wav out.wav
 */
#include "rec_crypt.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int rng(void *ctx, unsigned char *out, size_t len)
{
    return fread(out, 1, len, (FILE *)ctx) == len ? 0 : -1;
}
static int wr(void *ctx, const uint8_t *b, size_t n) { return fwrite(b, 1, n, (FILE *)ctx) == n ? 0 : -1; }
static rec_crypt_t C;

int main(int argc, char **argv)
{
    if (argc != 5) {
        fprintf(stderr, "usage: %s pub.pem key_id in.wav out.wav\n", argv[0]);
        return 2;
    }
    FILE *pf = fopen(argv[1], "rb");
    unsigned char pem[4096];
    size_t pl = pf ? fread(pem, 1, sizeof pem - 1, pf) : 0;
    if (pf) fclose(pf);
    pem[pl++] = 0;
    FILE *in = fopen(argv[3], "rb");
    if (!in) return 1;
    fseek(in, 0, SEEK_END);
    long sz = ftell(in);
    fseek(in, 0, SEEK_SET);
    unsigned char *wav = malloc((size_t)sz);
    if (fread(wav, 1, (size_t)sz, in) != (size_t)sz) return 1;
    fclose(in);
    /* find the "data" chunk; require 16 kHz mono 16-bit PCM */
    if (sz < 44 || memcmp(wav, "RIFF", 4) || memcmp(wav + 8, "WAVE", 4)) return 3;
    long off = 12, data = -1, dlen = 0;
    unsigned rate = 0, ch = 0, bits = 0;
    while (off + 8 <= sz) {
        long n = wav[off + 4] | wav[off + 5] << 8 | wav[off + 6] << 16 | (long)wav[off + 7] << 24;
        if (!memcmp(wav + off, "fmt ", 4)) {
            ch = wav[off + 10] | wav[off + 11] << 8;
            rate = wav[off + 12] | wav[off + 13] << 8 | wav[off + 14] << 16 | (unsigned)wav[off + 15] << 24;
            bits = wav[off + 22] | wav[off + 23] << 8;
        } else if (!memcmp(wav + off, "data", 4)) {
            data = off + 8;
            dlen = n < sz - data ? n : sz - data;
            break;
        }
        off += 8 + n + (n & 1);
    }
    if (data < 0 || rate != 16000 || ch != 1 || bits != 16) {
        fprintf(stderr, "need 16 kHz mono 16-bit PCM WAV (got %u Hz, %u ch, %u bit)\n", rate, ch, bits);
        return 3;
    }
    FILE *ur = fopen("/dev/urandom", "rb");
    FILE *out = fopen(argv[4], "wb");
    uint8_t hdr[REC_CRYPT_HDR_MAX];
    size_t hl = 0;
    rec_crypt_params_t p = {16000, 1, 16};
    if (rec_crypt_begin(&C, pem, pl, argv[2], &p, rng, ur, hdr, sizeof hdr, &hl) != 0) {
        fprintf(stderr, "rec_crypt_begin failed (bad public key?)\n");
        return 4;
    }
    fwrite(hdr, 1, hl, out);
    for (long done = 0; done < dlen; done += 4096) {
        size_t k = (size_t)(dlen - done < 4096 ? dlen - done : 4096);
        if (rec_crypt_update(&C, wav + data + done, k, wr, out) != 0) return 5;
    }
    if (rec_crypt_final(&C, wr, out) != 0) return 5;
    printf("%s: %ld PCM bytes -> %ld bytes APENC1 (key %s)\n", argv[4], dlen, ftell(out), argv[2]);
    fclose(out);
    fclose(ur);
    free(wav);
    return 0;
}
