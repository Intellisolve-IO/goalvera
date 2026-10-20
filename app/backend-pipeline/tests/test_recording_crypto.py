"""APENC1 decrypt tests. Cross-checks the C encoder when REC_CRYPT_VECTORS
points at the output of firmware test/host/test_rec_crypt.c (CI does this)."""
import hashlib, os, struct, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common import recording_crypto as rc

PAD = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


def encode(pub, pcm: bytes, chunk=4096, kid=b"recordings/1") -> bytes:
    key, prefix = os.urandom(32), os.urandom(8)
    wrapped = pub.encrypt(key, PAD)
    hdr = rc.MAGIC + bytes([1, 1, len(kid), 0]) + struct.pack("<IIHH", chunk, 16000, 1, 16) + prefix
    hdr += struct.pack("<H", len(wrapped)) + kid + wrapped
    hh, aes, out = hashlib.sha256(hdr).digest(), AESGCM(key), [hdr]
    parts = [pcm[i:i + chunk] for i in range(0, len(pcm) - len(pcm) % chunk, chunk)] + [pcm[len(pcm) - len(pcm) % chunk:]]
    for i, p in enumerate(parts):
        fin = i == len(parts) - 1
        out.append(aes.encrypt(prefix + struct.pack(">I", i), p, hh + struct.pack(">I", i) + bytes([fin])))
    return b"".join(out)


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        vec = os.environ.get("REC_CRYPT_VECTORS")
        if vec:
            cls.priv = serialization.load_pem_private_key(Path(vec, "priv.pem").read_bytes(), None)
        else:
            cls.priv = rsa.generate_private_key(65537, 3072)
        cls.un = staticmethod(rc.rsa_unwrapper(cls.priv))

    def test_roundtrip_sizes(self):
        for n in (0, 1, 4095, 4096, 8192, 10000):
            pcm = os.urandom(n)
            d = rc.decrypt(encode(self.priv.public_key(), pcm), self.un)
            self.assertEqual(d.pcm, pcm)
            self.assertFalse(d.truncated)
            self.assertEqual((d.sample_rate, d.channels, d.bits, d.key_id), (16000, 1, 16, "recordings/1"))

    def test_tamper_and_reorder_rejected(self):
        blob = bytearray(encode(self.priv.public_key(), os.urandom(12000)))
        bad = bytearray(blob); bad[-5] ^= 1
        with self.assertRaises(rc.RecordingCryptoError):
            rc.decrypt(bytes(bad), self.un, allow_truncated=False)
        hdr_bad = bytearray(blob); hdr_bad[16] ^= 1  # sample rate is authenticated via AAD
        with self.assertRaises(rc.RecordingCryptoError):
            rc.decrypt(bytes(hdr_bad), self.un)

    def test_truncation(self):
        pcm = os.urandom(10000)
        blob = encode(self.priv.public_key(), pcm)
        h, end = rc.parse_header(blob)
        cut = blob[:end + 4112 + 100]
        d = rc.decrypt(cut, self.un)
        self.assertTrue(d.truncated)
        self.assertEqual(d.pcm, pcm[:4096])
        with self.assertRaises(rc.RecordingCryptoError):
            rc.decrypt(cut, self.un, allow_truncated=False)
        # dropping whole trailing chunks is also flagged
        self.assertTrue(rc.decrypt(blob[:end + 4112], self.un).truncated)

    def test_plain_wav_not_encrypted(self):
        self.assertFalse(rc.is_encrypted(b"RIFF\x00\x00\x00\x00WAVE"))

    @unittest.skipUnless(os.environ.get("REC_CRYPT_VECTORS"), "C vectors not built")
    def test_c_vectors(self):
        v = Path(os.environ["REC_CRYPT_VECTORS"])
        for name in "abc":
            d = rc.decrypt(Path(v, f"{name}.enc").read_bytes(), self.un, allow_truncated=False)
            self.assertEqual(d.pcm, Path(v, f"{name}.pcm").read_bytes())
        d = rc.decrypt(Path(v, "d.enc").read_bytes(), self.un)
        self.assertTrue(d.truncated)
        self.assertEqual(d.pcm, Path(v, "a.pcm").read_bytes()[:4096])


if __name__ == "__main__":
    unittest.main()
