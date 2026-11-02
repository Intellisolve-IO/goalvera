"""Phone recordings (iPhone Safari) — per-user upload, not a device token.

  POST /v1/web/uploads   Authorization: Bearer <Identity Platform ID token>
       {"session_id", "filename", "size", "md5", "crc32c"}
       -> a 15-minute create-only V4 signed PUT for exactly
          web/<session_id>/part_NNNN.m4a (or .mp4 / .webm), bound to the
          size and checksums. The first call also writes
          web/<session_id>/owner.json so only that user can add to the session.
  POST /v1/web/confirm   same token
       {"session_id", "object", "size", "md5", "crc32c"}
       -> what Google stored. safe_to_delete_local_copy is true only when the
          size, checksum and CMEK key all match. The phone then deletes that piece.
       {"session_id", "finalize": true, "class_label", "started_local",
        "offset_minutes", "parts": [{name, size, md5}], "gaps": [...]}
       -> the service writes web/<session_id>/session.json and complete.json.
          The pipeline turns the pieces into one 16 kHz WAV under rec/web-<user>/.

No device token is ever sent to a browser. The board's /v1/uploads path is
unchanged. Audio is not encrypted on the phone: the PUT is HTTPS and the
bucket key encrypts the object at rest.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

from flask import Blueprint, jsonify, request

from idp_auth import AuthError, WebAuthenticator
from safelog import log, timer

URL_MINUTES = int(os.environ.get("URL_MINUTES", "15"))
MAX_PART_BYTES = int(os.environ.get("MAX_WEB_PART_BYTES", str(8 * 1024 * 1024)))
MAX_PARTS = 400
SESSION_RE = re.compile(r"^s[a-f0-9]{16,40}$")
PART_RE = re.compile(r"^part_(?P<n>\d{4})\.(?P<ext>m4a|mp4|webm)$")
B64_MD5_RE = re.compile(r"^[A-Za-z0-9+/]{22}==$")
B64_CRC_RE = re.compile(r"^[A-Za-z0-9+/]{6}==$")
STAMP_RE = re.compile(r"^\d{8}_\d{6}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]{0,39}$")
CONTENT_TYPES = {"m4a": "audio/mp4", "mp4": "audio/mp4", "webm": "audio/webm"}


def clean_class_code(src: str) -> str:
    """The board's filename rule: spaces and underscores become '-', other
    punctuation is dropped, 16 characters, no trailing hyphen."""
    out = []
    for ch in src or "":
        if ch.isalnum() or ch == "-":
            out.append(ch)
        elif ch in " _" and out and out[-1] != "-":
            out.append("-")
    while out and out[-1] == "-":
        out.pop()
    text = "".join(out)
    if len(text) > 16:
        text = text[:16]
        while text.endswith("-"):
            text = text[:-1]
    return text


def _origins() -> set[str]:
    return {o.strip().rstrip("/") for o in os.environ.get("WEB_ALLOWED_ORIGINS", "").split(",") if o.strip()}


def _err(code: str, http: int, *, step: str, who: dict | None = None, session_id: str | None = None):
    log("request_rejected", "WARNING", step=step, error_code=code, http_status=http,
        actor_id=(who or {}).get("actor_id"),
        session_id=session_id if session_id and SESSION_RE.match(session_id) else None)
    return jsonify({"error": code}), http


def _gaps(raw) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 50:
        raise AuthError(400, "BAD_GAPS")
    out = []
    for g in raw:
        if not isinstance(g, dict):
            raise AuthError(400, "BAD_GAPS")
        off, dur = g.get("offset_seconds"), g.get("duration_seconds")
        if isinstance(off, bool) or isinstance(dur, bool):
            raise AuthError(400, "BAD_GAPS")
        if not isinstance(off, (int, float)) or not isinstance(dur, (int, float)):
            raise AuthError(400, "BAD_GAPS")
        if off < 0 or dur < 0 or off > 6 * 3600 or dur > 2 * 3600:
            raise AuthError(400, "BAD_GAPS")
        out.append({"offset_seconds": round(float(off), 1), "duration_seconds": round(float(dur), 1)})
    return out


def _stamp_ok(stamp: str, now: dt.datetime | None = None) -> bool:
    if not STAMP_RE.match(stamp or ""):
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        local = dt.datetime.strptime(stamp, "%Y%m%d_%H%M%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return False
    # The phone's wall clock, checked loosely (time zones and a delayed upload).
    delta = abs((now - local).total_seconds())
    return delta <= 8 * 24 * 3600


def make_blueprint(bucket, signer_args, auth: WebAuthenticator | None = None) -> Blueprint:
    bp = Blueprint("web_rec", __name__)
    auth = auth or WebAuthenticator(bucket)

    def who():
        return auth.require_recorder(request.headers)

    def origin_ok() -> bool:
        origin = request.headers.get("Origin")
        if not origin:
            return True
        return origin.rstrip("/") in _origins()

    def read_json(name: str):
        blob = bucket.get_blob(name)
        if blob is None:
            return None
        return json.loads(blob.download_as_bytes())

    def create_once(name: str, data: dict) -> bool:
        from google.api_core.exceptions import PreconditionFailed
        try:
            bucket.blob(name).upload_from_string(
                json.dumps(data, separators=(",", ":")),
                content_type="application/json", if_generation_match=0)
            return True
        except PreconditionFailed:
            return False

    def claim_owner(session_id: str, user: dict) -> bool:
        """True when this user owns the session. The first uploader creates owner.json."""
        name = f"web/{session_id}/owner.json"
        fresh = {"user_id": user["uid"], "device_id": user["device_id"]}
        if create_once(name, fresh):
            return True
        got = read_json(name) or {}
        return got.get("user_id") == user["uid"] and got.get("device_id") == user["device_id"]

    @bp.after_request
    def _cors(resp):
        origin = (request.headers.get("Origin") or "").rstrip("/")
        if origin and origin in _origins():
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            resp.headers["Access-Control-Max-Age"] = "600"
        return resp

    @bp.route("/v1/web/uploads", methods=["OPTIONS"])
    @bp.route("/v1/web/confirm", methods=["OPTIONS"])
    def options():
        return ("", 204)

    @bp.post("/v1/web/uploads")
    def create_upload():
        if not origin_ok():
            return _err("BAD_ORIGIN", 403, step="web_sign")
        try:
            user = who()
        except AuthError as exc:
            return _err(exc.code, exc.http, step="web_sign")
        body = request.get_json(silent=True) or {}
        session_id = str(body.get("session_id", ""))
        filename = str(body.get("filename", ""))
        if not SESSION_RE.match(session_id):
            return _err("BAD_SESSION", 400, step="web_sign", who=user)
        part = PART_RE.match(filename)
        if not part or not (1 <= int(part["n"]) <= MAX_PARTS):
            return _err("BAD_FILENAME", 400, step="web_sign", who=user, session_id=session_id)
        size = body.get("size")
        md5, crc = body.get("md5"), body.get("crc32c")
        if not isinstance(size, int) or isinstance(size, bool) or not (0 < size <= MAX_PART_BYTES):
            return _err("BAD_SIZE", 400, step="web_sign", who=user, session_id=session_id)
        if md5 is None and crc is None:
            return _err("CHECKSUM_REQUIRED", 400, step="web_sign", who=user, session_id=session_id)
        if md5 is not None and not B64_MD5_RE.match(str(md5)):
            return _err("BAD_MD5", 400, step="web_sign", who=user, session_id=session_id)
        if crc is not None and not B64_CRC_RE.match(str(crc)):
            return _err("BAD_CRC32C", 400, step="web_sign", who=user, session_id=session_id)
        try:
            if not claim_owner(session_id, user):
                return _err("SESSION_OWNED", 403, step="web_sign", who=user, session_id=session_id)
        except AuthError as exc:
            return _err(exc.code, exc.http, step="web_sign", who=user, session_id=session_id)

        obj = f"web/{session_id}/{filename}"
        headers = {
            "Content-Type": CONTENT_TYPES[part["ext"]],
            "x-goog-if-generation-match": "0",
            "x-goog-content-length-range": f"{size},{size}",
        }
        if md5:
            headers["Content-MD5"] = str(md5)
        if crc:
            headers["x-goog-hash"] = f"crc32c={crc}"
        with timer() as t:
            url = bucket.blob(obj).generate_signed_url(
                version="v4", method="PUT", expiration=dt.timedelta(minutes=URL_MINUTES),
                headers=headers, **signer_args(),
            )
        expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=URL_MINUTES)).isoformat()
        log("upload_url_issued", step="web_sign", actor_id=user["actor_id"], session_id=session_id,
            size_bytes=size, ms=t.ms)
        return jsonify({"object": obj, "method": "PUT", "upload_url": url,
                        "headers": headers, "expires_at": expires})

    def _stored(blob):
        kms = blob.kms_key_name or ""
        return {"size": blob.size, "md5": blob.md5_hash, "crc32c": blob.crc32c,
                "generation": str(blob.generation), "kms_key": kms,
                "stored_at": blob.time_created.isoformat() if getattr(blob, "time_created", None) else None}, kms

    def _checks(body, blob, kms) -> dict:
        expected = os.environ.get("EXPECTED_KMS_KEY", "")
        checks = {"size": body.get("size") == blob.size}
        if body.get("md5") is not None:
            checks["md5"] = body.get("md5") == blob.md5_hash
        if body.get("crc32c") is not None:
            checks["crc32c"] = body.get("crc32c") == blob.crc32c
        checks["checksum_provided"] = "md5" in checks or "crc32c" in checks
        checks["cmek_encrypted"] = bool(kms) and (not expected or kms.startswith(expected + "/"))
        return checks

    @bp.post("/v1/web/confirm")
    def confirm():
        if not origin_ok():
            return _err("BAD_ORIGIN", 403, step="web_confirm")
        try:
            user = who()
        except AuthError as exc:
            return _err(exc.code, exc.http, step="web_confirm")
        body = request.get_json(silent=True) or {}
        session_id = str(body.get("session_id", ""))
        if not SESSION_RE.match(session_id):
            return _err("BAD_SESSION", 400, step="web_confirm", who=user)
        try:
            if not claim_owner(session_id, user):
                return _err("SESSION_OWNED", 403, step="web_confirm", who=user, session_id=session_id)
        except AuthError as exc:
            return _err(exc.code, exc.http, step="web_confirm", who=user, session_id=session_id)
        if body.get("finalize") is True:
            return _finalize(user, session_id, body)
        filename = str(body.get("object", "")).rsplit("/", 1)[-1]
        obj = f"web/{session_id}/{filename}"
        if body.get("object") not in (None, obj) and str(body.get("object", "")) != obj:
            return _err("BAD_OBJECT", 400, step="web_confirm", who=user, session_id=session_id)
        if not PART_RE.match(filename):
            return _err("BAD_OBJECT", 400, step="web_confirm", who=user, session_id=session_id)
        blob = bucket.get_blob(obj)
        if blob is None:
            log("confirm", step="web_confirm", actor_id=user["actor_id"], session_id=session_id,
                verified=False, reason="NOT_FOUND")
            return jsonify({"verified": False, "object": obj, "reason": "NOT_FOUND",
                            "safe_to_delete_local_copy": False}), 404
        stored, kms = _stored(blob)
        checks = _checks(body, blob, kms)
        verified = all(checks.values())
        log("confirm", step="web_confirm", actor_id=user["actor_id"], session_id=session_id,
            verified=verified, size_bytes=blob.size, object_generation=str(blob.generation))
        return jsonify({"verified": verified, "object": obj, "stored": stored, "checks": checks,
                        "safe_to_delete_local_copy": verified})

    def _finalize(user: dict, session_id: str, body: dict):
        label = body.get("class_label")
        if not isinstance(label, str) or not LABEL_RE.match(label.strip()):
            return _err("BAD_CLASS", 400, step="web_finalize", who=user, session_id=session_id)
        label = label.strip()
        code = clean_class_code(label)
        if not code or clean_class_code(label) != code:
            return _err("BAD_CLASS", 400, step="web_finalize", who=user, session_id=session_id)
        stamp = str(body.get("started_local", ""))
        if not _stamp_ok(stamp):
            return _err("BAD_TIME", 400, step="web_finalize", who=user, session_id=session_id)
        off = body.get("offset_minutes", 0)
        if isinstance(off, bool) or not isinstance(off, int) or not -14 * 60 <= off <= 14 * 60:
            return _err("BAD_TIME", 400, step="web_finalize", who=user, session_id=session_id)
        try:
            gaps = _gaps(body.get("gaps"))
        except AuthError as exc:
            return _err(exc.code, exc.http, step="web_finalize", who=user, session_id=session_id)
        parts = body.get("parts")
        if not isinstance(parts, list) or not parts or len(parts) > MAX_PARTS:
            return _err("BAD_PARTS", 400, step="web_finalize", who=user, session_id=session_id)
        names = []
        for i, part in enumerate(parts, start=1):
            if not isinstance(part, dict):
                return _err("BAD_PARTS", 400, step="web_finalize", who=user, session_id=session_id)
            filename = str(part.get("name", ""))
            m = PART_RE.match(filename)
            if not m or int(m["n"]) != i:
                return _err("BAD_PARTS", 400, step="web_finalize", who=user, session_id=session_id)
            blob = bucket.get_blob(f"web/{session_id}/{filename}")
            if blob is None or part.get("size") != blob.size:
                return _err("PART_MISSING", 400, step="web_finalize", who=user, session_id=session_id)
            if part.get("md5") is not None and part.get("md5") != blob.md5_hash:
                return _err("PART_MISMATCH", 400, step="web_finalize", who=user, session_id=session_id)
            names.append(filename)

        stem = f"{stamp}_{code}"
        device = user["device_id"]
        recording_id = f"rec_{device}_{stem}".replace("-", "_")
        done = read_json(f"web/{session_id}/complete.json")
        if isinstance(done, dict):
            if done.get("user_id") != user["uid"]:
                return _err("SESSION_OWNED", 403, step="web_finalize", who=user, session_id=session_id)
            log("web_finalize", step="web_finalize", actor_id=user["actor_id"], session_id=session_id,
                part_count=len(names), status="already")
            return jsonify({"ok": True, "session_id": session_id, "recording_id": done.get("recording_id"),
                            "object": done.get("wav_object"), "status": "processing"})

        sign = "-" if off < 0 else "+"
        minutes = abs(off)
        start_time = (f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}"
                      f"{sign}{minutes // 60:02d}{minutes % 60:02d}")
        manifest = {
            "session_id": session_id, "user_id": user["uid"], "device_id": device,
            "class_code": code, "class_label": label, "started_local": stamp,
            "start_time": start_time, "parts": names, "gaps": gaps,
            "finalized_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        create_once(f"web/{session_id}/session.json", manifest)
        marker = {"session_id": session_id, "user_id": user["uid"], "device_id": device,
                  "recording_id": recording_id, "wav_object": f"rec/{device}/{stem}.wav"}
        create_once(f"web/{session_id}/complete.json", marker)
        log("web_finalize", step="web_finalize", actor_id=user["actor_id"], session_id=session_id,
            part_count=len(names), status="complete")
        return jsonify({"ok": True, "session_id": session_id, "recording_id": recording_id,
                        "object": marker["wav_object"], "status": "processing"})

    return bp
