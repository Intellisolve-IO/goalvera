#!/usr/bin/env python3
"""Laptop upload (demo fallback path) - the exact flow the recorder firmware will use.

  1. compute size, MD5 and CRC32C of the file while reading it
  2. POST /v1/uploads            -> one-time signed PUT URL (15 min)
  3. PUT the bytes with the required headers (Google checks MD5/CRC32C/size)
  4. POST /v1/confirm            -> what Google stored + verified flag + receipt
  5. recompute the receipt HMAC with our token; only if it matches AND
     verified is true is the local copy safe to delete.

Uploads the sidecar (.json with the same stem) too, if present.

  export GOALVERA_UPLOAD_URL=https://upload-api-....run.app
  export GOALVERA_DEVICE_ID=laptop-demo
  export GOALVERA_DEVICE_TOKEN_FILE=~/.config/goalvera/laptop-demo.token
  python upload_recording.py 20260928_101400_RM12.wav [--delete-after-verify]

Stdlib only (CRC32C implemented below), so it runs on any laptop with Python 3.9+.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request

_CRC_TABLE = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ 0x82F63B78 if _c & 1 else _c >> 1
    _CRC_TABLE.append(_c)


def crc32c_update(crc: int, data: bytes) -> int:
    try:
        import google_crc32c  # fast path if installed
        return google_crc32c.extend(crc, data)
    except ImportError:
        pass
    crc ^= 0xFFFFFFFF
    for b in data:
        crc = _CRC_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def fingerprint(path: str) -> dict:
    md5, crc, size = hashlib.md5(), 0, 0
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            md5.update(chunk)
            crc = crc32c_update(crc, chunk)
            size += len(chunk)
    return {"size": size, "md5": base64.b64encode(md5.digest()).decode(),
            "crc32c": base64.b64encode(crc.to_bytes(4, "big")).decode()}


def call(url: str, token: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:  # noqa: BLE001
            return e.code, {}


def put(url: str, headers: dict, path: str) -> int:
    with open(path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(url, data=data, method="PUT", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        code = body.split("<Code>")[1].split("</Code>")[0] if "<Code>" in body else ""
        print(f"  PUT rejected by Google: HTTP {e.code} {code}", file=sys.stderr)
        return e.code


def upload_one(base: str, device: str, token: str, path: str) -> bool:
    name = os.path.basename(path)
    fp = fingerprint(path)
    print(f"{name}: {fp['size']} bytes  md5={fp['md5']}  crc32c={fp['crc32c']}")
    st, grant = call(f"{base}/v1/uploads", token, {"device_id": device, "filename": name, **fp})
    if st != 200:
        print(f"  could not get upload URL: HTTP {st} {grant.get('error', '')}", file=sys.stderr)
        return False
    st = put(grant["upload_url"], grant["headers"], path)
    if st not in (200, 201):
        # 412 = this name was already uploaded once (one-time URL); confirm still tells us if it matches
        if st != 412:
            return False
    st, conf = call(f"{base}/v1/confirm", token, {"device_id": device, "object": grant["object"], **fp})
    stored = conf.get("stored", {})
    msg = f"{conf.get('object')}|{stored.get('generation')}|{stored.get('size')}|{stored.get('md5')}|{stored.get('crc32c')}|verified"
    expected = hmac.new(token.encode(), msg.encode(), hashlib.sha256).hexdigest()
    receipt_ok = bool(conf.get("receipt")) and hmac.compare_digest(expected, conf["receipt"])
    local_match = stored.get("size") == fp["size"] and stored.get("md5") == fp["md5"] and stored.get("crc32c") == fp["crc32c"]
    ok = bool(conf.get("verified")) and receipt_ok and local_match
    print(f"  stored as gs://…/{conf.get('object')} gen={stored.get('generation')} "
          f"cmek={'yes' if stored.get('kms_key') else 'NO'}")
    print(f"  checks={conf.get('checks')} receipt={'valid' if receipt_ok else 'INVALID'}")
    print(f"  {'VERIFIED MATCH - safe to delete local copy' if ok else 'NOT VERIFIED - keep local copy'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--delete-after-verify", action="store_true",
                    help="delete the local files only after a verified-match confirmation")
    a = ap.parse_args()
    base = os.environ["GOALVERA_UPLOAD_URL"].rstrip("/")
    device = os.environ["GOALVERA_DEVICE_ID"]
    token = os.environ.get("GOALVERA_DEVICE_TOKEN") or open(os.path.expanduser(os.environ["GOALVERA_DEVICE_TOKEN_FILE"])).read().strip()

    files = [a.wav]
    sidecar = os.path.splitext(a.wav)[0] + ".json"
    if os.path.exists(sidecar):
        files.append(sidecar)
    results = {p: upload_one(base, device, token, p) for p in files}
    if a.delete_after_verify:
        for p, ok in results.items():
            if ok:
                os.remove(p)
                print(f"deleted local {os.path.basename(p)}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
