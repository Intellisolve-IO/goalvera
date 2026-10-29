#!/usr/bin/env python3
"""Write main/ota_pubkey.c from an Ed25519 public key. Refuses a path inside the repo.

The file that is written holds only the 32-byte public key. The private key
stays in Secret Manager. See docs/ota.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

ROOT = Path(__file__).resolve().parents[3]
DEST = Path(__file__).resolve().parents[1] / "main" / "ota_pubkey.c"


def public_bytes(pem: bytes) -> bytes:
    try:
        pub = load_pem_public_key(pem)
    except ValueError:
        pub = None
    if isinstance(pub, Ed25519PublicKey):
        return pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
    key = load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("key is not Ed25519")
    print("read the public half from a private key file. Delete that file after it is in Secret Manager.",
          file=sys.stderr)
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def render(pub: bytes) -> str:
    rows = []
    for i in range(0, 32, 8):
        rows.append("    " + ", ".join(f"0x{b:02x}" for b in pub[i:i + 8]) + ",")
    body = "\n".join(rows)
    return f"""/* Public half of the recorder OTA signing key (Ed25519, 32 bytes).
 *
 * The private key is not in this repo. It belongs in Secret Manager
 * (recorder-ota-signing-key). Written by tools/embed_ota_pubkey.py.
 * See docs/ota.md.
 */
#include "ota_policy.h"

const uint8_t ota_embedded_pubkey[OTA_PUB_BYTES] = {{
{body}
}};
"""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit("usage: embed_ota_pubkey.py /path/to/public.pem")
    src = Path(argv[1])
    if not src.is_file():
        raise SystemExit(f"not a file: {src}")
    try:
        src.resolve().relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise SystemExit("refusing to read a key from inside the repo")
    pub = public_bytes(src.read_bytes())
    if pub == bytes(32):
        raise SystemExit("refusing to embed an all-zero key")
    DEST.write_text(render(pub))
    print(f"wrote {DEST} (public key only)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
