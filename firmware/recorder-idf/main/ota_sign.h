#pragma once

#include "ota_policy.h"

#include <stddef.h>
#include <stdint.h>

/* Defined in ota_pubkey.c. All zeros until tools/embed_ota_pubkey.py runs. */
extern const uint8_t ota_embedded_pubkey[OTA_PUB_BYTES];

/* Ed25519 over the canonical manifest bytes. False for the all-zero placeholder
 * key, a bad signature, or a message that does not round-trip. */
bool ota_signature_ok(const uint8_t pub[OTA_PUB_BYTES], const uint8_t sig[OTA_SIG_BYTES],
                      const void *msg, size_t len);
