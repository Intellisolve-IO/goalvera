"""Teacher Notes photo / video upload from a phone.

  POST /v1/media/token          Authorization: Bearer <device token>
       {"device_id", "recording": "<YYYYMMDD_HHMMSS_CLASS>"}
       -> {"url": "https://<this service>/u/<token>", "expires_at", "ttl_seconds"}
          The recorder shows the url as a QR code. The token is random (192 bits),
          tied to that device's current recording, valid TOKEN_TTL_SECONDS (5 min)
          and good for ONE photo or video. Only its SHA-256 is stored
          (media_tokens/<sha>.json); the token itself is never stored or logged.
  GET  /u/<token>               the phone page (no sign-in: the token is the key)
  GET  /v1/media/<token>        {"state": "open"|"expired"|"used", "expires_in"}
  POST /v1/media/<token>/start  {"kind": "photo"|"video", "content_type", "size", "duration_s"}
       -> a V4 signed PUT URL for exactly media/<recording_id>/<media_id>.<ext>,
          create-only (x-goog-if-generation-match: 0), bound to the size and type.
          Refused after the token expires or was used.
  POST /v1/media/<token>/done   -> checks what Google stored, writes
          media/<recording_id>/<media_id>.json (the link to the session) and
          spends the token: media_tokens/<sha>.used is created once
          (create-only), so a second upload with the same QR is refused.

Demo project only (example-project); everything under media/ and
media_tokens/ is auto-deleted by the bucket lifecycle and by
infra/delete_demo_media.sh after the demo.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import secrets
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from safelog import log

TOKEN_TTL_SECONDS = int(os.environ.get("MEDIA_TOKEN_TTL_SECONDS", "300"))
PUT_URL_MINUTES = int(os.environ.get("MEDIA_PUT_URL_MINUTES", "15"))
MAX_PHOTO_BYTES = int(os.environ.get("MAX_PHOTO_BYTES", str(25 * 1024 * 1024)))
MAX_VIDEO_BYTES = int(os.environ.get("MAX_VIDEO_BYTES", str(150 * 1024 * 1024)))
MAX_VIDEO_SECONDS = float(os.environ.get("MAX_VIDEO_SECONDS", "30"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

STEM_RE = re.compile(r"^(?P<date>\d{8})_(?P<time>\d{6})_(?P<cls>[A-Za-z0-9-]{1,16})$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")
PHOTO_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
               "image/heic": "heic", "image/heif": "heif"}
VIDEO_TYPES = {"video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm"}
PAGE = (Path(__file__).with_name("upload_page.html")).read_text(encoding="utf-8")


def recording_id(device: str, stem: str) -> str:
    """Same id the pipeline gives the recording (pipeline_service/main.py)."""
    return f"rec_{device}_{stem}".replace("-", "_")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def make_blueprint(bucket, auth, signer_args, err) -> Blueprint:
    """bucket: GCS bucket; auth(device_id) -> (token|None, why); signer_args() -> kwargs; err(...)."""
    bp = Blueprint("media", __name__)

    def tok_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def read_json(name: str):
        blob = bucket.get_blob(name)
        return None if blob is None else json.loads(blob.download_as_bytes())

    def create_once(name: str, data: dict) -> bool:
        from google.api_core.exceptions import PreconditionFailed
        try:
            bucket.blob(name).upload_from_string(json.dumps(data), content_type="application/json",
                                                 if_generation_match=0)
            return True
        except PreconditionFailed:
            return False

    def load(token: str):
        """(record, sha, state) where state is open|expired|used|unknown."""
        if not TOKEN_RE.match(token or ""):
            return None, None, "unknown"
        sha = tok_hash(token)
        rec = read_json(f"media_tokens/{sha}.json")
        if rec is None:
            return None, sha, "unknown"
        if bucket.get_blob(f"media_tokens/{sha}.used") is not None:
            return rec, sha, "used"
        if _now() >= dt.datetime.fromisoformat(rec["expires_at"]):
            return rec, sha, "expired"
        return rec, sha, "open"

    def refuse(state: str, step: str):
        code = {"used": "TOKEN_USED", "expired": "TOKEN_EXPIRED"}.get(state, "TOKEN_UNKNOWN")
        log("media_rejected", "WARNING", step=step, error_code=code)
        return jsonify({"error": code}), (410 if state in ("used", "expired") else 404)

    def base_url() -> str:
        if PUBLIC_URL:
            return PUBLIC_URL
        proto = request.headers.get("X-Forwarded-Proto", request.scheme)
        return f"{proto}://{request.host}"

    @bp.post("/v1/media/token")
    def mint():
        body = request.get_json(silent=True) or {}
        device_id = str(body.get("device_id", ""))
        tok, why = auth(device_id)
        if not tok:
            return err("UNAUTHORIZED", 401, device_id, "media_token", why)
        stem = str(body.get("recording", ""))
        if not STEM_RE.match(stem):
            return err("BAD_RECORDING", 400, device_id, "media_token")
        token = secrets.token_urlsafe(24)          # 32 chars, 192 bits
        expires = _now() + dt.timedelta(seconds=TOKEN_TTL_SECONDS)
        rid = recording_id(device_id, stem)
        rec = {"device_id": device_id, "recording": stem, "recording_id": rid,
               "media_id": "m" + tok_hash(token)[:12], "created_at": _now().isoformat(),
               "expires_at": expires.isoformat()}
        if not create_once(f"media_tokens/{tok_hash(token)}.json", rec):
            return err("TOKEN_COLLISION", 500, device_id, "media_token")
        log("media_token_issued", step="media_token", device_id=device_id, recording_id=rid)
        return jsonify({"url": f"{base_url()}/u/{token}", "expires_at": expires.isoformat(),
                        "ttl_seconds": TOKEN_TTL_SECONDS})

    @bp.get("/u/<token>")
    def page(token):
        return Response(PAGE, mimetype="text/html", headers={
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex", "X-Content-Type-Options": "nosniff"})

    @bp.get("/v1/media/<token>")
    def status(token):
        rec, _, state = load(token)
        if state == "unknown":
            return jsonify({"state": "unknown"}), 404
        left = max(0, int((dt.datetime.fromisoformat(rec["expires_at"]) - _now()).total_seconds()))
        return jsonify({"state": state, "expires_in": left if state == "open" else 0,
                        "max_video_seconds": MAX_VIDEO_SECONDS})

    @bp.post("/v1/media/<token>/start")
    def start(token):
        rec, _, state = load(token)
        if state != "open":
            return refuse(state, "media_start")
        body = request.get_json(silent=True) or {}
        kind, ctype, size = body.get("kind"), str(body.get("content_type", "")).lower(), body.get("size")
        types_, cap = (PHOTO_TYPES, MAX_PHOTO_BYTES) if kind == "photo" else (VIDEO_TYPES, MAX_VIDEO_BYTES)
        if kind not in ("photo", "video") or ctype not in types_:
            return jsonify({"error": "BAD_TYPE"}), 400
        if not isinstance(size, int) or not (0 < size <= cap):
            return jsonify({"error": "TOO_LARGE" if isinstance(size, int) and size > cap else "BAD_SIZE"}), 400
        if kind == "video":
            d = body.get("duration_s")
            if not isinstance(d, (int, float)) or d <= 0 or d > MAX_VIDEO_SECONDS + 0.5:
                return jsonify({"error": "TOO_LONG"}), 400
        obj = f"media/{rec['recording_id']}/{rec['media_id']}.{types_[ctype]}"
        headers = {"Content-Type": ctype, "x-goog-if-generation-match": "0",
                   "x-goog-content-length-range": f"{size},{size}"}
        url = bucket.blob(obj).generate_signed_url(
            version="v4", method="PUT", expiration=dt.timedelta(minutes=PUT_URL_MINUTES),
            headers=headers, **signer_args())
        log("media_url_issued", step="media_start", device_id=rec["device_id"],
            recording_id=rec["recording_id"], kind=kind, size_bytes=size)
        return jsonify({"upload_url": url, "method": "PUT", "headers": headers, "object": obj})

    @bp.post("/v1/media/<token>/done")
    def done(token):
        rec, sha, state = load(token)
        if state == "used":
            return refuse(state, "media_done")
        if state == "unknown":
            return refuse(state, "media_done")
        # an upload that started before expiry may finish after it
        blob = None
        for ext in list(PHOTO_TYPES.values()) + list(VIDEO_TYPES.values()):
            blob = bucket.get_blob(f"media/{rec['recording_id']}/{rec['media_id']}.{ext}")
            if blob is not None:
                break
        if blob is None:
            return refuse(state, "media_done") if state == "expired" else (jsonify({"error": "NOT_UPLOADED"}), 409)
        if not create_once(f"media_tokens/{sha}.used", {"at": _now().isoformat()}):
            return refuse("used", "media_done")
        kind = "video" if (blob.content_type or "").startswith("video/") else "photo"
        meta = {"media_id": rec["media_id"], "recording_id": rec["recording_id"], "device_id": rec["device_id"],
                "kind": kind, "object": blob.name, "content_type": blob.content_type, "size_bytes": blob.size,
                "uploaded_at": _now().isoformat(), "source": "teacher_notes_qr", "demo_only": True}
        create_once(f"media/{rec['recording_id']}/{rec['media_id']}.json", meta)
        log("media_saved", step="media_done", device_id=rec["device_id"], recording_id=rec["recording_id"],
            kind=kind, size_bytes=blob.size)
        return jsonify({"ok": True, "kind": kind, "media_id": rec["media_id"]})

    return bp
