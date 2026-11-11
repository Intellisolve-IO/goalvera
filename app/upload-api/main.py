"""Upload service (Cloud Run, public HTTPS, device-token auth) -.

The recorder never holds a Google credential. It holds one per-device token.

  POST /v1/uploads   Authorization: Bearer <device token>
       {"device_id", "filename", "size", "md5": <base64>, "crc32c": <base64>}
       -> a V4 signed PUT URL for exactly rec/<device_id>/<filename>, valid
          15 minutes, usable once (x-goog-if-generation-match: 0 means the
          object can only be created, never overwritten), bound to the
          declared size, content type and MD5/CRC32C, so Google itself rejects
          a damaged or different file.
  POST /v1/confirm   Authorization: Bearer <device token>
       {"device_id", "object", "size", "md5", "crc32c"}
       -> what Google actually stored (size, MD5, CRC32C, generation, KMS key)
          plus "verified": true only if every declared value matches and the
          object is encrypted with our CMEK key. Also a "receipt":
          HMAC-SHA256(device token, "<object>|<generation>|<size>|<md5>|<crc32c>|verified")
          which the device can recompute to check the answer came from us.
          The device deletes its SD copy ONLY when verified is true and the
          receipt matches.

Tokens live as SHA-256 hashes in gs://<bucket>/control/devices.json
(infra/device_token.py create|revoke). Revoking takes effect within
TOKEN_CACHE_SECONDS. Logs hold only device id, step, timing, error codes.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import threading
import time

import google.auth
from flask import Flask, jsonify, request
from google.auth.transport.requests import Request as AuthRequest
from google.cloud import storage

from safelog import log, timer

BUCKET = os.environ["BUCKET"]
EXPECTED_KMS_KEY = os.environ.get("EXPECTED_KMS_KEY", "")
URL_MINUTES = int(os.environ.get("URL_MINUTES", "15"))
MAX_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(400 * 1024 * 1024)))  # ~3.5 h at 16 kHz/16-bit
TOKEN_CACHE_SECONDS = int(os.environ.get("TOKEN_CACHE_SECONDS", "30"))
REGISTRY_OBJECT = "control/devices.json"

DEVICE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
FILE_RE = re.compile(r"^\d{8}_\d{6}_[A-Za-z0-9-]{1,16}\.(wav|json)$")
B64_MD5_RE = re.compile(r"^[A-Za-z0-9+/]{22}==$")
B64_CRC_RE = re.compile(r"^[A-Za-z0-9+/]{6}==$")
CONTENT_TYPES = {"wav": "audio/wav", "json": "application/json"}

app = Flask(__name__)
_gcs = storage.Client()
_bucket = _gcs.bucket(BUCKET)
# "at" is None until the registry has been read once. It must not start at 0.0:
# time.monotonic() counts from when the instance's sandbox booted, so on a cold
# start it is only a few seconds, "now - 0.0 > TOKEN_CACHE_SECONDS" stayed false
# for the first ~30 s, the registry was never read, and every device got a 401
# until the instance was 30 s old (seen on every cold start Oct 1-7).
_registry = {"at": None, "devices": {}}
_lock = threading.Lock()
_creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])


def _devices() -> dict:
    """The device registry, re-read at most every TOKEN_CACHE_SECONDS.

    Read on the first request of every instance. If the read fails, fail closed
    for this request but don't cache the empty answer: the next request tries
    again, so one storage hiccup can't lock every device out for 30 s.
    """
    with _lock:
        at = _registry["at"]
        if at is None or time.monotonic() - at > TOKEN_CACHE_SECONDS:
            try:
                data = json.loads(_bucket.blob(REGISTRY_OBJECT).download_as_bytes())
                _registry["devices"] = data.get("devices", {})
                _registry["at"] = time.monotonic()
                if at is None:
                    log("registry_loaded", device_count=len(_registry["devices"]))
            except Exception as exc:  # noqa: BLE001 - fail closed
                log("registry_unavailable", "ERROR", error_type=type(exc).__name__)
                _registry["devices"] = {}
                _registry["at"] = None
        return _registry["devices"]


def _auth(device_id: str) -> tuple[str | None, str]:
    """(token, "") if the presented token is valid for device_id, else (None, why).

    why is a fixed code for the log only (never the token or its hash):
    NO_BEARER, BAD_DEVICE_ID, UNKNOWN_DEVICE, REVOKED or TOKEN_MISMATCH.
    """
    hdr = request.headers.get("Authorization", "")
    if not hdr.startswith("Bearer "):
        return None, "NO_BEARER"
    if not DEVICE_RE.match(device_id or ""):
        return None, "BAD_DEVICE_ID"
    token = hdr[7:].strip()
    rec = _devices().get(device_id)
    if not rec:
        return None, "UNKNOWN_DEVICE"
    if rec.get("revoked"):
        return None, "REVOKED"
    digest = hashlib.sha256(token.encode()).hexdigest()
    if not hmac.compare_digest(digest, rec.get("token_sha256", "")):
        return None, "TOKEN_MISMATCH"
    return token, ""


def _err(code: str, http: int, device_id: str | None = None, step: str = "", reason: str | None = None):
    log("request_rejected", "WARNING", step=step, error_code=code, http_status=http, reason=reason,
        device_id=device_id if device_id and DEVICE_RE.match(device_id) else None)
    return jsonify({"error": code}), http


def _signer_args() -> dict:
    """Sign through the IAM signBlob API as the service's own account (no key file)."""
    if not _creds.valid:
        _creds.refresh(AuthRequest())
    email = getattr(_creds, "service_account_email", None)
    if email in (None, "default"):
        _creds.refresh(AuthRequest())
        email = _creds.service_account_email
    return {"service_account_email": email, "access_token": _creds.token}


@app.post("/v1/uploads")
def create_upload():
    body = request.get_json(silent=True) or {}
    device_id = str(body.get("device_id", ""))
    token, why = _auth(device_id)
    if not token:
        return _err("UNAUTHORIZED", 401, device_id, "sign", why)
    filename = str(body.get("filename", ""))
    size = body.get("size")
    md5 = body.get("md5")
    crc = body.get("crc32c")
    if not FILE_RE.match(filename):
        return _err("BAD_FILENAME", 400, device_id, "sign")
    if not isinstance(size, int) or not (0 < size <= MAX_BYTES):
        return _err("BAD_SIZE", 400, device_id, "sign")
    if md5 is None and crc is None:
        return _err("CHECKSUM_REQUIRED", 400, device_id, "sign")
    if md5 is not None and not B64_MD5_RE.match(str(md5)):
        return _err("BAD_MD5", 400, device_id, "sign")
    if crc is not None and not B64_CRC_RE.match(str(crc)):
        return _err("BAD_CRC32C", 400, device_id, "sign")

    obj = f"rec/{device_id}/{filename}"
    ctype = CONTENT_TYPES[filename.rsplit(".", 1)[1]]
    headers = {
        "Content-Type": ctype,
        "x-goog-if-generation-match": "0",               # create-only: one successful upload per name
        "x-goog-content-length-range": f"{size},{size}",  # exact declared size
    }
    if md5:
        headers["Content-MD5"] = md5                       # Google rejects the PUT if bytes differ
    if crc:
        headers["x-goog-hash"] = f"crc32c={crc}"
    with timer() as t:
        url = _bucket.blob(obj).generate_signed_url(
            version="v4", method="PUT", expiration=dt.timedelta(minutes=URL_MINUTES),
            headers=headers, **_signer_args(),
        )
    expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=URL_MINUTES)).isoformat()
    log("upload_url_issued", step="sign", device_id=device_id, size_bytes=size, ms=t.ms)
    return jsonify({"object": obj, "method": "PUT", "upload_url": url,
                    "headers": headers, "expires_at": expires})


@app.post("/v1/confirm")
def confirm():
    body = request.get_json(silent=True) or {}
    device_id = str(body.get("device_id", ""))
    token, why = _auth(device_id)
    if not token:
        return _err("UNAUTHORIZED", 401, device_id, "confirm", why)
    obj = str(body.get("object", ""))
    if not obj.startswith(f"rec/{device_id}/") or not FILE_RE.match(obj.rsplit("/", 1)[-1]):
        return _err("BAD_OBJECT", 400, device_id, "confirm")
    blob = _bucket.get_blob(obj)
    if blob is None:
        log("confirm", step="confirm", device_id=device_id, verified=False, reason="NOT_FOUND")
        return jsonify({"verified": False, "object": obj, "reason": "NOT_FOUND"}), 404

    kms = blob.kms_key_name or ""
    stored = {"size": blob.size, "md5": blob.md5_hash, "crc32c": blob.crc32c,
              "generation": str(blob.generation), "kms_key": kms,
              "stored_at": blob.time_created.isoformat() if blob.time_created else None}
    checks = {"size": body.get("size") == blob.size}
    if body.get("md5") is not None:
        checks["md5"] = body.get("md5") == blob.md5_hash
    if body.get("crc32c") is not None:
        checks["crc32c"] = body.get("crc32c") == blob.crc32c
    checks["checksum_provided"] = "md5" in checks or "crc32c" in checks
    checks["cmek_encrypted"] = bool(kms) and (not EXPECTED_KMS_KEY or kms.startswith(EXPECTED_KMS_KEY + "/"))
    verified = all(checks.values())
    receipt = None
    if verified:
        msg = f"{obj}|{blob.generation}|{blob.size}|{blob.md5_hash}|{blob.crc32c}|verified"
        receipt = hmac.new(token.encode(), msg.encode(), hashlib.sha256).hexdigest()
    log("confirm", step="confirm", device_id=device_id, verified=verified, size_bytes=blob.size,
        object_generation=str(blob.generation))
    return jsonify({"verified": verified, "object": obj, "stored": stored, "checks": checks,
                    "receipt": receipt,
                    "safe_to_delete_local_copy": verified})


# Teacher Notes photo / video from a phone: media.py
from media import make_blueprint  # noqa: E402

app.register_blueprint(make_blueprint(_bucket, _auth, _signer_args, _err))

# School name for the recorder's top banner: school.py
import school  # noqa: E402

app.register_blueprint(school.make_blueprint(_bucket, _auth, _err))

# OTA confirm + update log: ota_log.py
import ota_log  # noqa: E402

app.register_blueprint(ota_log.make_blueprint(_bucket, _auth, _err))

# iPhone Safari recorder: per-user signed URLs under web/<session>/ (web_rec.py).
import web_rec  # noqa: E402

app.register_blueprint(web_rec.make_blueprint(_bucket, _signer_args))


@app.get("/healthz")
def healthz():
    return {"ok": True}
