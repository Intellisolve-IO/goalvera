#pragma once
/*
 * SD-card recording encryption, format "APENC1".
 *
 * Envelope encryption, public key only on the device:
 *   - a fresh random 256-bit AES key per recording,
 *   - wrapped with RSA-OAEP-3072/SHA-256 under the recording public key
 *     (private half lives only in Cloud KMS, never on the device),
 *   - audio encrypted in chunks with AES-256-GCM (hardware AES on the S3).
 * The device cannot decrypt its own recordings, so a stolen recorder (even
 * with its flash dumped) does not expose audio. Flash/NVS encryption protects
 * the Wi-Fi password and upload key, a separate concern.
 *
 * File layout (little-endian):
 *   0  "APENC1\n\0"     8   magic
 *   8  version u8 = 1, alg u8 = 1 (RSA-OAEP-SHA256 + AES-256-GCM),
 *      key_id_len u8, reserved u8 = 0
 *   12 chunk_size u32   plaintext bytes per chunk (last chunk may be shorter)
 *   16 sample_rate u32, 20 channels u16, 22 bits u16   (PCM, no WAV header)
 *   24 nonce_prefix[8]  random per file
 *   32 wrapped_len u16, key_id[key_id_len], wrapped_key[wrapped_len]
 *   then chunks: ciphertext || 16-byte GCM tag
 * Chunk i: nonce = nonce_prefix || be32(i);
 *          AAD   = SHA-256(header) || be32(i) || u8(final)
 * Exactly one chunk (the last) has final = 1, so a truncated or reordered
 * file is detected; a file cut off by power loss can still be salvaged up to
 * its last whole chunk.
 * Pure C + mbedTLS so it builds on the host for tests.
 */
#include <stddef.h>
#include <stdint.h>
#include "mbedtls/gcm.h"

#define REC_CRYPT_MAGIC "APENC1\n"
#define REC_CRYPT_CHUNK 4096
#define REC_CRYPT_TAG 16
#define REC_CRYPT_HDR_MAX 640
#define REC_CRYPT_KEYID_MAX 64

typedef int (*rec_crypt_rng_t)(void *ctx, unsigned char *out, size_t len);
typedef int (*rec_crypt_write_t)(void *ctx, const uint8_t *buf, size_t len); /* 0 = ok */

typedef struct {
    uint32_t sample_rate;
    uint16_t channels;
    uint16_t bits;
} rec_crypt_params_t;

typedef struct {
    mbedtls_gcm_context gcm;
    uint8_t prefix[8];
    uint8_t hdr_hash[32];
    uint32_t idx;
    size_t fill;
    int active;
    /* Encrypted in place; the tag is appended. Word-aligned internal RAM so
     * SDMMC can DMA straight from it (see audio_rec.c 0.7.6 note). */
    uint8_t buf[REC_CRYPT_CHUNK + REC_CRYPT_TAG] __attribute__((aligned(4)));
} rec_crypt_t;

/* Make a key, wrap it, write the header into hdr (cap >= REC_CRYPT_HDR_MAX). 0 = ok. */
int rec_crypt_begin(rec_crypt_t *c, const unsigned char *pub_pem, size_t pem_len_with_nul,
                    const char *key_id, const rec_crypt_params_t *p, rec_crypt_rng_t rng, void *rng_ctx,
                    uint8_t *hdr, size_t cap, size_t *hdr_len);
/* Buffer plaintext; every full chunk is encrypted and passed to w. */
int rec_crypt_update(rec_crypt_t *c, const uint8_t *data, size_t n, rec_crypt_write_t w, void *wctx);
/* Encrypt the remaining bytes as the final chunk (may be empty) and wipe the key. */
int rec_crypt_final(rec_crypt_t *c, rec_crypt_write_t w, void *wctx);
/* Wipe the key without writing (error path). */
void rec_crypt_abort(rec_crypt_t *c);
/* Ciphertext bytes for n plaintext bytes (excluding header). */
uint64_t rec_crypt_body_size(uint64_t n);
