#!/usr/bin/env python3
"""Sign a recorder image and publish the manifest. Does not create buckets or secrets.

The private key is read from Secret Manager (or from OTA_SIGNING_KEY, a PEM
file that must sit outside this repo). It is never printed and never passed
on the command line. See docs/ota.md for the gcloud commands that create the
bucket and the secret. Those commands are not run here.

  python3 tools/publish_ota.py --bin build-hosyond35/recorder.bin
  python3 tools/publish_ota.py --bin recorder.bin --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

ROOT = Path(__file__).resolve().parents[3]  # repo root
PUBKEY_C = Path(__file__).resolve().parents[1] / "main" / "ota_pubkey.c"
SECRET = os.environ.get("OTA_SIGNING_SECRET", "recorder-ota-signing-key")
HOST = "storage.googleapis.com"

BOARDS = {
    "hosyond35": "firmware/hosyond35",
    "crowpanel43": "firmware/crowpanel43",
}

# Public members must not hold a role that can create, change, or delete objects.
PUBLIC_MEMBERS = {"allUsers", "allAuthenticatedUsers"}
PUBLIC_WRITE_ROLES = {
    "roles/storage.admin",
    "roles/storage.objectAdmin",
    "roles/storage.objectCreator",
    "roles/storage.objectUser",
    "roles/storage.legacyBucketOwner",
    "roles/storage.legacyBucketWriter",
    "roles/storage.legacyObjectOwner",
    "roles/storage.folderAdmin",
}

VER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def canonical(version: str, url: str, sha256: str) -> bytes:
    if not VER_RE.match(version):
        raise ValueError(f"version must be X.Y.Z, got {version!r}")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    if not url.startswith(f"https://{HOST}/"):
        raise ValueError("url must be an https storage.googleapis.com object")
    return f"APOTA1\n{version}\n{url}\n{sha256}\n".encode()


def load_private_key(pem: bytes) -> Ed25519PrivateKey:
    key = load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("signing key must be Ed25519")
    return key


def public_raw(key: Ed25519PrivateKey) -> bytes:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def sign_manifest(key: Ed25519PrivateKey, version: str, url: str, sha256: str) -> dict:
    import base64
    msg = canonical(version, url, sha256)
    sig = base64.b64encode(key.sign(msg)).decode()
    return {"version": version, "url": url, "sha256": sha256, "signature": sig}


def parse_embedded_pubkey(text: str) -> bytes:
    nums = [int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{2})", text)]
    if len(nums) != 32:
        raise ValueError(f"expected 32 public-key bytes in ota_pubkey.c, found {len(nums)}")
    return bytes(nums)


def version_from_cmake(text: str) -> str:
    m = re.search(r'set\(PROJECT_VER "([^"]+)"\)', text)
    if not m or not VER_RE.match(m.group(1)):
        raise ValueError("PROJECT_VER is missing or not X.Y.Z")
    return m.group(1)


def public_writer_bindings(policy: dict) -> list[str]:
    """Bindings that let the public write. Empty means the bucket is not publicly writable."""
    found = []
    for b in policy.get("bindings") or []:
        role = b.get("role") or ""
        if role not in PUBLIC_WRITE_ROLES:
            continue
        for member in b.get("members") or []:
            if member in PUBLIC_MEMBERS:
                found.append(f"{member} {role}")
    return found


def image_url(bucket: str, board: str, version: str) -> str:
    prefix = BOARDS[board]
    return f"https://{HOST}/{bucket}/{prefix}/recorder-{version}.bin"


def object_names(board: str, version: str) -> tuple[str, str]:
    prefix = BOARDS[board]
    return f"{prefix}/recorder-{version}.bin", f"{prefix}/manifest.json"


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT)
        return True
    except ValueError:
        return False


def project_id() -> str:
    """Google Cloud project that holds the signing secret. Never a default."""
    project = os.environ.get("PROJECT_ID", "").strip()
    if not project:
        raise SystemExit("set PROJECT_ID to the project that holds the OTA signing secret")
    return project


def ota_bucket() -> str:
    bucket = os.environ.get("OTA_BUCKET", "").strip()
    if bucket:
        return bucket
    return f"{project_id()}-firmware"


def read_signing_key() -> bytes:
    """PEM bytes. From OTA_SIGNING_KEY (outside the repo) or Secret Manager."""
    given = os.environ.get("OTA_SIGNING_KEY", "")
    if given:
        path = Path(given)
        if not path.is_file():
            raise SystemExit(f"OTA_SIGNING_KEY is not a file: {path}")
        if _inside_repo(path):
            raise SystemExit("refusing to read a signing key from inside the repo")
        return path.read_bytes()
    project = project_id()
    try:
        proc = subprocess.run(
            ["gcloud", "secrets", "versions", "access", "latest",
             "--secret", SECRET, "--project", project],
            check=True, capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        err = getattr(exc, "stderr", b"") or b""
        sys.stderr.write(err.decode(errors="replace"))
        raise SystemExit(
            f"could not read secret {SECRET} in {project}. "
            "Set PROJECT_ID and OTA_SIGNING_SECRET. The key was not printed."
        ) from exc
    data = proc.stdout
    if b"PRIVATE KEY" not in data and len(data.strip()) == 0:
        raise SystemExit("signing secret was empty")
    return data


def fetch_iam_policy(bucket: str) -> dict:
    proc = subprocess.run(
        ["gcloud", "storage", "buckets", "get-iam-policy", f"gs://{bucket}", "--format=json"],
        check=False, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"could not read IAM for gs://{bucket}; not uploading")
    return json.loads(proc.stdout or "{}")


def upload(local: Path, dest: str, content_type: str, cache: str) -> None:
    proc = subprocess.run(
        ["gcloud", "storage", "cp", str(local), dest,
         f"--content-type={content_type}", f"--cache-control={cache}"],
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"upload failed: {dest}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sign and publish a recorder OTA image")
    p.add_argument("--bin", required=True, type=Path, help="recorder.bin to publish")
    p.add_argument("--version", help="X.Y.Z (default: PROJECT_VER in CMakeLists.txt)")
    p.add_argument("--board", choices=sorted(BOARDS), default="hosyond35")
    p.add_argument("--bucket", default=None, help="default: $OTA_BUCKET or ${PROJECT_ID}-firmware")
    p.add_argument("--embedded-pubkey", type=Path, default=PUBKEY_C)
    p.add_argument("--dry-run", action="store_true", help="print the manifest, do not upload")
    p.add_argument("--manifest-out", type=Path, help="also write the manifest JSON here")
    args = p.parse_args(argv)

    if not args.bin.is_file():
        raise SystemExit(f"image not found: {args.bin}")
    version = args.version or version_from_cmake(
        (Path(__file__).resolve().parents[1] / "CMakeLists.txt").read_text())
    if not VER_RE.match(version):
        raise SystemExit(f"bad version {version}")
    sha = hashlib.sha256(args.bin.read_bytes()).hexdigest()
    key = load_private_key(read_signing_key())
    embedded = parse_embedded_pubkey(args.embedded_pubkey.read_text())
    pub = public_raw(key)
    if embedded == bytes(32):
        raise SystemExit(
            "firmware public key is still the placeholder in main/ota_pubkey.c. "
            "Embed the public half (docs/ota.md) and rebuild before publishing.")
    if embedded != pub:
        raise SystemExit(
            "firmware public key does not match this signing key. "
            "Run tools/embed_ota_pubkey.py and rebuild before publishing.")
    bucket = args.bucket or ota_bucket()
    url = image_url(bucket, args.board, version)
    manifest = sign_manifest(key, version, url, sha)
    text = json.dumps(manifest, separators=(",", ":"))
    bin_obj, man_obj = object_names(args.board, version)
    print(f"version {version}")
    print(f"sha256 {sha}")
    print(f"image gs://{bucket}/{bin_obj}")
    print(f"manifest gs://{bucket}/{man_obj}")
    print(text)
    if args.manifest_out:
        args.manifest_out.write_text(text + "\n")
    if args.dry_run:
        print("dry-run: nothing uploaded")
        return 0

    writers = public_writer_bindings(fetch_iam_policy(bucket))
    if writers:
        raise SystemExit("refusing to publish; bucket is publicly writable: " + ", ".join(writers))
    with tempfile.TemporaryDirectory(prefix="ota-manifest-") as tmp:
        man = Path(tmp) / "manifest.json"
        man.write_text(text + "\n")
        upload(args.bin, f"gs://{bucket}/{bin_obj}", "application/octet-stream", "public, max-age=3600")
        upload(man, f"gs://{bucket}/{man_obj}", "application/json", "no-cache")
    print("published")
    return 0


if __name__ == "__main__":
    sys.exit(main())
