"""Decrypt recorder SD files in the "APENC1" format.

Mirror of firmware/recorder-idf/main/rec_crypt.h. The per-file AES key is
wrapped with RSA-OAEP-3072/SHA-256; the private key lives in Cloud KMS
(asymmetric-decrypt key), so `unwrap` is normally `kms_unwrapper(...)`.
Plain WAV files (older firmware, encryption "none") pass through untouched.
"""
from __future__ import annotations

import hashlib
import struct
import wave
from dataclasses import dataclass
from typing import Callable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

MAGIC = b"APENC1\n\0"
TAG = 16


class RecordingCryptoError(Exception):
    pass


@dataclass
class Decrypted:
    pcm: bytes
    sample_rate: int
    channels: int
    bits: int
    key_id: str
    truncated: bool  # final chunk missing (power loss mid-recording)


def is_encrypted(head: bytes) -> bool:
    return head[:8] == MAGIC


def parse_header(data: bytes) -> tuple[dict, int]:
    if len(data) < 34 or data[:8] != MAGIC:
        raise RecordingCryptoError("not an APENC1 file")
    ver, alg, kid_len, _ = data[8:12]
    if ver != 1 or alg != 1:
        raise RecordingCryptoError(f"unsupported version/alg {ver}/{alg}")
    chunk, rate = struct.unpack_from("<II", data, 12)
    ch, bits = struct.unpack_from("<HH", data, 20)
    prefix = data[24:32]
    (wl,) = struct.unpack_from("<H", data, 32)
    end = 34 + kid_len + wl
    if len(data) < end or chunk == 0 or chunk > 1 << 20:
        raise RecordingCryptoError("bad header")
    key_id = data[34:34 + kid_len].decode("ascii")
    return {"chunk": chunk, "rate": rate, "channels": ch, "bits": bits, "prefix": prefix,
            "key_id": key_id, "wrapped": data[34 + kid_len:end], "hash": hashlib.sha256(data[:end]).digest()}, end


def decrypt(data: bytes, unwrap: Callable[[str, bytes], bytes], allow_truncated: bool = True) -> Decrypted:
    h, pos = parse_header(data)
    key = unwrap(h["key_id"], h["wrapped"])
    if len(key) != 32:
        raise RecordingCryptoError("unwrapped key is not 256-bit")
    aes = AESGCM(key)
    out, i, final_seen = [], 0, False
    step = h["chunk"] + TAG
    while pos < len(data):
        if final_seen:
            raise RecordingCryptoError("data after final chunk")
        blob = data[pos:pos + step]
        nonce = h["prefix"] + struct.pack(">I", i)
        aad = h["hash"] + struct.pack(">I", i)
        try:
            out.append(aes.decrypt(nonce, blob, aad + b"\x01"))
            final_seen = True
        except InvalidTag:
            if len(blob) != step:
                if allow_truncated:  # partial last write after power loss: drop it
                    break
                raise RecordingCryptoError(f"chunk {i} failed authentication")
            try:
                out.append(aes.decrypt(nonce, blob, aad + b"\x00"))
            except InvalidTag:
                raise RecordingCryptoError(f"chunk {i} failed authentication") from None
        pos += len(blob)
        i += 1
    if not final_seen and not allow_truncated:
        raise RecordingCryptoError("final chunk missing (truncated file)")
    return Decrypted(b"".join(out), h["rate"], h["channels"], h["bits"], h["key_id"], not final_seen)


def write_wav(d: Decrypted, path: str) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(d.channels)
        w.setsampwidth(d.bits // 8)
        w.setframerate(d.sample_rate)
        w.writeframes(d.pcm)


def rsa_unwrapper(private_key) -> Callable[[str, bytes], bytes]:
    """Local private key (tests / break-glass only)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    pad = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
    return lambda _kid, wrapped: private_key.decrypt(wrapped, pad)


def kms_unwrapper(key_ring_path: str) -> Callable[[str, bytes], bytes]:
    """key_id on the device is "<cryptoKey>/<version>", e.g. "recording-wrap/1";
    key_ring_path is projects/P/locations/L/keyRings/R. Only key ids under
    this ring are accepted."""
    from google.cloud import kms

    client = kms.KeyManagementServiceClient()

    def unwrap(key_id: str, wrapped: bytes) -> bytes:
        name, _, ver = key_id.partition("/")
        if not name or not ver.isdigit() or "/" in name:
            raise RecordingCryptoError(f"bad key id {key_id!r}")
        full = f"{key_ring_path}/cryptoKeys/{name}/cryptoKeyVersions/{ver}"
        return client.asymmetric_decrypt(request={"name": full, "ciphertext": wrapped}).plaintext

    return unwrap
