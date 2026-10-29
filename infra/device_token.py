#!/usr/bin/env python3
"""Manage per-device upload tokens (tasks#35 step 6; checklist row 40).

Tokens are random 32-byte secrets. Only their SHA-256 hash is stored, in
gs://<bucket>/control/devices.json (CMEK-encrypted, readable only by the
upload service and project owners). The plain token is printed ONCE.

  ./infra/device_token.py create atl-demo-01 [--out token.txt]
  ./infra/device_token.py revoke atl-demo-01      # lost device -> uploads blocked within ~30 s
  ./infra/device_token.py list

Runs with your gcloud login (ADC). Uses a generation-match write so two
admins can't clobber each other.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys

PROJECT = os.environ.get("PROJECT_ID", "example-project")
BUCKET = os.environ.get("BUCKET", f"{PROJECT}-recordings")
OBJECT = "control/devices.json"
DEVICE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["create", "revoke", "list"])
    ap.add_argument("device_id", nargs="?")
    ap.add_argument("--out", help="write the token to this file (chmod 600) instead of stdout")
    a = ap.parse_args()

    os.environ.setdefault("GOOGLE_CLOUD_QUOTA_PROJECT", PROJECT)
    from google.api_core.exceptions import NotFound
    from google.cloud import storage

    blob = storage.Client(project=PROJECT).bucket(BUCKET).blob(OBJECT)
    try:
        blob.reload()
        data, gen = json.loads(blob.download_as_bytes(if_generation_match=blob.generation)), blob.generation
    except NotFound:
        data, gen = {"devices": {}}, 0
    devices = data.setdefault("devices", {})
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    if a.action == "list":
        for d, r in sorted(devices.items()):
            print(f"{d:24} {'REVOKED' if r.get('revoked') else 'active ':8} created {r.get('created_at')}")
        return 0
    if not a.device_id or not DEVICE_RE.match(a.device_id):
        print("device_id must be lowercase letters/digits/dashes, max 32 chars", file=sys.stderr)
        return 2

    token = None
    if a.action == "create":
        token = secrets.token_urlsafe(32)
        devices[a.device_id] = {"token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                                "created_at": now, "revoked": False}
    else:
        if a.device_id not in devices:
            print("unknown device", file=sys.stderr)
            return 1
        devices[a.device_id].update(revoked=True, revoked_at=now)

    blob.upload_from_string(json.dumps(data, indent=2), content_type="application/json",
                            if_generation_match=gen)
    if token:
        if a.out:
            fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(token + "\n")
            print(f"created token for {a.device_id} -> {a.out} (shown only once; store it safely)")
        else:
            print(token)
    else:
        print(f"revoked {a.device_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
