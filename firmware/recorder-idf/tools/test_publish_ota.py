#!/usr/bin/env python3
"""Checks the release script's canonical bytes, signature, and public-write rule.

Does not call gcloud and does not create a key inside the repo.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import embed_ota_pubkey as embed  # noqa: E402
import publish_ota as pub  # noqa: E402

CANON = (
    "APOTA1\n"
    "0.8.1\n"
    "https://storage.googleapis.com/example-project-firmware/firmware/hosyond35/recorder-0.8.1.bin\n"
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n"
)
# Same test seed as test/host/test_ota_policy.c. Not a device key.
SEED = bytes(range(32))


class PublishTests(unittest.TestCase):
    def test_canonical_matches_firmware(self):
        url = CANON.split("\n")[2]
        sha = CANON.split("\n")[3]
        self.assertEqual(pub.canonical("0.8.1", url, sha), CANON.encode())

    def test_signature_matches_host_vector(self):
        key = Ed25519PrivateKey.from_private_bytes(SEED)
        url = CANON.split("\n")[2]
        sha = CANON.split("\n")[3]
        manifest = pub.sign_manifest(key, "0.8.1", url, sha)
        import base64
        raw = base64.b64decode(manifest["signature"])
        self.assertEqual(raw.hex(), (
            "6a9d9dad964d7e1d7fbf185c26c3b80f45e838ebbec7c34128821b55cb165c2d"
            "b7950102a4a09ea5dee68dd75e0816609e112fdc93fc40f94ba0bde696a0d708"))
        key.public_key().verify(raw, CANON.encode())

    def test_public_write_is_rejected_and_viewer_is_not(self):
        viewers = {"bindings": [{"role": "roles/storage.objectViewer", "members": ["allUsers"]}]}
        self.assertEqual(pub.public_writer_bindings(viewers), [])
        writers = {"bindings": [
            {"role": "roles/storage.objectAdmin", "members": ["allUsers"]},
            {"role": "roles/storage.objectCreator", "members": ["allAuthenticatedUsers"]},
            {"role": "roles/storage.legacyBucketWriter", "members": ["allUsers"]},
            {"role": "roles/storage.objectAdmin", "members": ["user:release@example.com"]},
        ]}
        found = pub.public_writer_bindings(writers)
        self.assertIn("allUsers roles/storage.objectAdmin", found)
        self.assertIn("allAuthenticatedUsers roles/storage.objectCreator", found)
        self.assertIn("allUsers roles/storage.legacyBucketWriter", found)
        self.assertFalse(any(x.startswith("user:") for x in found))

    def test_embedded_pubkey_is_public_and_not_a_placeholder(self):
        text = (TOOLS.parent / "main" / "ota_pubkey.c").read_text()
        self.assertNotIn("PRIVATE KEY", text)
        raw = pub.parse_embedded_pubkey(text)
        self.assertEqual(len(raw), 32)
        self.assertNotEqual(raw, bytes(32))
        self.assertNotIn("goalvera-app", text)

    def test_project_version(self):
        text = (TOOLS.parent / "CMakeLists.txt").read_text()
        self.assertEqual(pub.version_from_cmake(text), "0.8.1")

    def test_wifi_passwords_stay_empty(self):
        text = (TOOLS.parent / "sdkconfig.defaults").read_text()
        self.assertIn('CONFIG_RECORDER_WIFI_PASSWORD=""', text)
        self.assertNotRegex(text, r'CONFIG_RECORDER_WIFI_PASSWORD="[^"]+')
        self.assertNotRegex(text, r'CONFIG_RECORDER_WIFI_PASSWORD2="[^"]+')

    def test_refuses_key_inside_the_repo(self):
        env = dict(os.environ)
        env["OTA_SIGNING_KEY"] = str(TOOLS.parent / "main" / "ota_pubkey.c")
        proc = subprocess.run(
            [sys.executable, str(TOOLS / "publish_ota.py"), "--bin", str(TOOLS / "publish_ota.py"),
             "--version", "0.8.0", "--dry-run"],
            check=False, capture_output=True, text=True, env=env,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("inside the repo", proc.stderr)

    def test_dry_run_signs_without_uploading(self):
        key = Ed25519PrivateKey.from_private_bytes(SEED)
        pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        with tempfile.TemporaryDirectory(prefix="ota-test-") as tmp:
            t = Path(tmp)
            key_path = t / "key.pem"
            key_path.write_bytes(pem)
            os.chmod(key_path, 0o600)
            c_path = t / "ota_pubkey.c"
            c_path.write_text(embed.render(pub.public_raw(key)))
            blob = t / "recorder.bin"
            blob.write_bytes(b"not-a-real-image")
            env = dict(os.environ)
            env["OTA_SIGNING_KEY"] = str(key_path)
            env["OTA_BUCKET"] = "example-project-firmware"
            proc = subprocess.run(
                [sys.executable, str(TOOLS / "publish_ota.py"),
                 "--bin", str(blob), "--version", "0.8.0", "--dry-run",
                 "--embedded-pubkey", str(c_path)],
                check=False, capture_output=True, text=True, env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("dry-run: nothing uploaded", proc.stdout)
            self.assertIn(hashlib.sha256(b"not-a-real-image").hexdigest(), proc.stdout)
            self.assertNotIn("PRIVATE", proc.stdout)
            # The placeholder compiled into git must not be treated as a real key.
            proc2 = subprocess.run(
                [sys.executable, str(TOOLS / "publish_ota.py"),
                 "--bin", str(blob), "--version", "0.8.0", "--dry-run"],
                check=False, capture_output=True, text=True, env=env,
            )
            self.assertNotEqual(proc2.returncode, 0)
            self.assertIn("does not match", proc2.stderr)


if __name__ == "__main__":
    unittest.main()
