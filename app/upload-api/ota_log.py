"""Recorder OTA events (firmware 0.8.0).

  POST /v1/device/ota   Authorization: Bearer <device token>
       {"device_id", "event", "from_version", "to_version", "detail"}
       -> {"ok": true}

event is one of: check, blocked, rejected, download, staged, confirmed,
rollback, failed. Versions are X.Y.Z. detail is a short code (battery,
recording, upload, bad_signature, ...). Nothing here is a student name or
audio. The object is create-only under control/ota-log/<device>/<stamp>.json.

The upload-api service account needs objectCreator on that prefix. The grant
is documented in firmware/recorder-idf/docs/ota.md and is not applied here.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading

from flask import Blueprint, jsonify, request

from safelog import log

EVENTS = {"check", "blocked", "rejected", "download", "staged", "confirmed", "rollback", "failed"}
VER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
DETAIL_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_seq = 0
_seq_lock = threading.Lock()


def _version(value, required: bool) -> str | None:
    if value is None or value == "":
        return "" if not required else None
    text = str(value)
    return text if VER_RE.match(text) else None


def make_blueprint(bucket, auth, err) -> Blueprint:
    bp = Blueprint("ota_log", __name__)

    @bp.post("/v1/device/ota")
    def ota_event():
        global _seq
        body = request.get_json(silent=True) or {}
        device_id = str(body.get("device_id", ""))
        token, why = auth(device_id)
        if not token:
            return err("UNAUTHORIZED", 401, device_id, "ota", why)
        event = str(body.get("event", ""))
        if event not in EVENTS:
            return err("BAD_EVENT", 400, device_id, "ota")
        from_v = _version(body.get("from_version"), required=True)
        to_v = _version(body.get("to_version", ""), required=False)
        if from_v is None or to_v is None:
            return err("BAD_VERSION", 400, device_id, "ota")
        detail = body.get("detail") or ""
        detail = str(detail)
        if detail and not DETAIL_RE.match(detail):
            return err("BAD_DETAIL", 400, device_id, "ota")
        with _seq_lock:
            _seq += 1
            n = _seq
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
        name = f"control/ota-log/{device_id}/{stamp}Z_{event}_{n}.json"
        record = {"device_id": device_id, "event": event, "from_version": from_v,
                  "to_version": to_v, "detail": detail, "stored_at": stamp + "Z"}
        try:
            bucket.blob(name).upload_from_string(
                json.dumps(record, separators=(",", ":")),
                content_type="application/json", if_generation_match=0)
        except Exception as exc:  # noqa: BLE001 - the device retries; don't cache a failure
            log("ota", "ERROR", step=event, device_id=device_id, error_type=type(exc).__name__)
            return err("STORE_FAILED", 503, device_id, "ota")
        log("ota", step=event, device_id=device_id, from_version=from_v,
            to_version=to_v or None, reason=detail or None)
        return jsonify({"ok": True})

    return bp
