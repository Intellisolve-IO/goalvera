"""School name for the recorder's top banner.

  POST /v1/device/school   Authorization: Bearer <device token>   {"device_id": "..."}
       -> {"school_name": "Everyday Heroes Academy", "profile": "everyday-heroes", "is_default": bool}

Reuses the review site's admin setting (Settings tab -> School name), stored by
app/backend-pipeline/review_api/branding.py as create-only records under branding/: the newest
record of the active SCHOOL_PROFILE wins, else that profile's built-in name. Records written
before profiles existed belong to Demo School (same rule as branding.py). Only the name is read
here; logos stay in the review app. Cached per instance for CACHE_SECONDS.
Needs upload-api read + list on branding/ (infra/setup_media.sh: upload-read-branding).
"""
from __future__ import annotations

import json
import os
import threading
import time

from flask import Blueprint, jsonify, request

from safelog import log

PREFIX = "branding/"
# Keep in sync with review_api/branding.py PROFILES (names only).
PROFILE_NAMES = {"everyday-heroes": "Everyday Heroes Academy", "demo-school": "Demo School"}
DEFAULT_PROFILE = "everyday-heroes"
LEGACY_PROFILE = "demo-school"


def canonical_profile(value: str | None) -> str:
    """Records with no profile belong to Demo School. Any other id is used as written."""
    if not value:
        return LEGACY_PROFILE
    return value


CACHE_SECONDS = int(os.environ.get("SCHOOL_CACHE_SECONDS", "60"))
MAX_NAME = 80


def active_profile() -> str:
    p = canonical_profile(os.environ.get("SCHOOL_PROFILE") or DEFAULT_PROFILE)
    return p if p in PROFILE_NAMES else DEFAULT_PROFILE


def make_blueprint(bucket, auth, err) -> Blueprint:
    bp = Blueprint("school", __name__)
    cache = {"at": None, "val": None}
    lock = threading.Lock()

    def lookup() -> dict:
        prof = active_profile()
        default = PROFILE_NAMES[prof]
        name = None
        names = sorted((b.name for b in bucket.list_blobs(prefix=PREFIX) if b.name.endswith(".json")),
                       reverse=True)
        for n in names:
            try:
                r = json.loads(bucket.blob(n).download_as_bytes())
            except Exception:  # noqa: BLE001 - skip an unreadable record
                continue
            if isinstance(r, dict) and canonical_profile(r.get("profile")) == prof:
                name = r.get("school_name")
                break
        name = (str(name).strip()[:MAX_NAME] if name else "") or default
        return {"school_name": name, "profile": prof, "is_default": name == default}

    def current() -> dict:
        with lock:
            if cache["at"] is None or time.monotonic() - cache["at"] > CACHE_SECONDS:
                try:
                    cache["val"], cache["at"] = lookup(), time.monotonic()
                except Exception as exc:  # noqa: BLE001 - fall back to the profile name, don't cache
                    log("school_name_unavailable", "WARNING", error_type=type(exc).__name__)
                    prof = active_profile()
                    return {"school_name": PROFILE_NAMES[prof], "profile": prof, "is_default": True}
            return cache["val"]

    @bp.post("/v1/device/school")
    def school():
        body = request.get_json(silent=True) or {}
        device_id = str(body.get("device_id", ""))
        token, why = auth(device_id)
        if not token:
            return err("UNAUTHORIZED", 401, device_id, "school_name", why)
        return jsonify(current())

    return bp
