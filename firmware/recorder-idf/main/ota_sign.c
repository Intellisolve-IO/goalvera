#include "ota_sign.h"

#include "tweetnacl.h"

#include <string.h>

/* TweetNaCl references this from crypto_sign. The recorder never signs. */
void randombytes(unsigned char *p, unsigned long long n)
{
    memset(p, 0, (size_t)n);
}

bool ota_signature_ok(const uint8_t pub[OTA_PUB_BYTES], const uint8_t sig[OTA_SIG_BYTES],
                      const void *msg, size_t len)
{
    unsigned char sm[OTA_SIG_BYTES + OTA_CANON_MAX];
    unsigned char opened[OTA_SIG_BYTES + OTA_CANON_MAX];
    unsigned long long mlen = 0;
    if (!ota_pubkey_ready(pub) || !sig || !msg || len == 0 || len > OTA_CANON_MAX) {
        return false;
    }
    memcpy(sm, sig, OTA_SIG_BYTES);
    memcpy(sm + OTA_SIG_BYTES, msg, len);
    if (crypto_sign_open(opened, &mlen, sm, (unsigned long long)(OTA_SIG_BYTES + len), pub) != 0) {
        return false;
    }
    return mlen == (unsigned long long)len && memcmp(opened, msg, len) == 0;
}
