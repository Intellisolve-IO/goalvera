#include "rec_crypt.h"
#include <string.h>
#include "mbedtls/pk.h"
#include "mbedtls/rsa.h"
#include "mbedtls/sha256.h"
#include "mbedtls/platform_util.h"

static void le16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void le32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i)); }
static void be32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (24 - 8 * i)); }

uint64_t rec_crypt_body_size(uint64_t n)
{
    uint64_t chunks = n / REC_CRYPT_CHUNK + 1; /* +1: final chunk, possibly empty */
    return n + chunks * REC_CRYPT_TAG;
}

int rec_crypt_begin(rec_crypt_t *c, const unsigned char *pem, size_t pem_len, const char *key_id,
                    const rec_crypt_params_t *p, rec_crypt_rng_t rng, void *rng_ctx,
                    uint8_t *hdr, size_t cap, size_t *hdr_len)
{
    memset(c, 0, sizeof *c);
    size_t kid = key_id ? strlen(key_id) : 0;
    if (kid > REC_CRYPT_KEYID_MAX || cap < REC_CRYPT_HDR_MAX) return -1;

    uint8_t key[32];
    int ret = -1;
    mbedtls_pk_context pk;
    mbedtls_pk_init(&pk);
    if (rng(rng_ctx, key, sizeof key) || rng(rng_ctx, c->prefix, sizeof c->prefix)) goto out;
    if (mbedtls_pk_parse_public_key(&pk, pem, pem_len) != 0) { ret = -2; goto out; }
    if (mbedtls_pk_get_type(&pk) != MBEDTLS_PK_RSA) { ret = -3; goto out; }
    mbedtls_rsa_context *rsa = mbedtls_pk_rsa(pk);
    size_t wl = mbedtls_rsa_get_len(rsa);
    size_t off = 34 + kid;
    if (wl < 256 || off + wl > cap) { ret = -3; goto out; }
    if (mbedtls_rsa_set_padding(rsa, MBEDTLS_RSA_PKCS_V21, MBEDTLS_MD_SHA256) != 0) goto out;
    if (mbedtls_rsa_rsaes_oaep_encrypt(rsa, rng, rng_ctx, NULL, 0, sizeof key, key, hdr + off) != 0) {
        ret = -4; goto out;
    }
    memcpy(hdr, REC_CRYPT_MAGIC, 8); /* includes the trailing NUL */
    hdr[8] = 1; hdr[9] = 1; hdr[10] = (uint8_t)kid; hdr[11] = 0;
    le32(hdr + 12, REC_CRYPT_CHUNK);
    le32(hdr + 16, p->sample_rate);
    le16(hdr + 20, p->channels);
    le16(hdr + 22, p->bits);
    memcpy(hdr + 24, c->prefix, 8);
    le16(hdr + 32, (uint16_t)wl);
    memcpy(hdr + 34, key_id, kid);
    *hdr_len = off + wl;
    if (mbedtls_sha256(hdr, *hdr_len, c->hdr_hash, 0) != 0) goto out;
    mbedtls_gcm_init(&c->gcm);
    if (mbedtls_gcm_setkey(&c->gcm, MBEDTLS_CIPHER_ID_AES, key, 256) != 0) { mbedtls_gcm_free(&c->gcm); goto out; }
    c->active = 1;
    ret = 0;
out:
    mbedtls_platform_zeroize(key, sizeof key);
    mbedtls_pk_free(&pk);
    return ret;
}

static int seal(rec_crypt_t *c, size_t n, int final, rec_crypt_write_t w, void *wctx)
{
    uint8_t iv[12], aad[37];
    memcpy(iv, c->prefix, 8);
    be32(iv + 8, c->idx);
    memcpy(aad, c->hdr_hash, 32);
    be32(aad + 32, c->idx);
    aad[36] = (uint8_t)(final ? 1 : 0);
    if (mbedtls_gcm_crypt_and_tag(&c->gcm, MBEDTLS_GCM_ENCRYPT, n, iv, sizeof iv, aad, sizeof aad,
                                  c->buf, c->buf, REC_CRYPT_TAG, c->buf + n) != 0) return -1;
    c->idx++;
    c->fill = 0;
    return w(wctx, c->buf, n + REC_CRYPT_TAG);
}

int rec_crypt_update(rec_crypt_t *c, const uint8_t *data, size_t n, rec_crypt_write_t w, void *wctx)
{
    if (!c->active) return -1;
    while (n) {
        size_t take = REC_CRYPT_CHUNK - c->fill;
        if (take > n) take = n;
        memcpy(c->buf + c->fill, data, take);
        c->fill += take; data += take; n -= take;
        if (c->fill == REC_CRYPT_CHUNK) {
            int r = seal(c, REC_CRYPT_CHUNK, 0, w, wctx);
            if (r) return r;
        }
    }
    return 0;
}

void rec_crypt_abort(rec_crypt_t *c)
{
    if (c->active) mbedtls_gcm_free(&c->gcm);
    mbedtls_platform_zeroize(c, sizeof *c);
}

int rec_crypt_final(rec_crypt_t *c, rec_crypt_write_t w, void *wctx)
{
    if (!c->active) return -1;
    int r = seal(c, c->fill, 1, w, wctx);
    rec_crypt_abort(c);
    return r;
}
