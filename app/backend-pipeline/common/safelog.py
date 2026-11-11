"""Structured logging that cannot carry names or transcript text.

Every log line is one JSON object on stdout (Cloud Run ships it to Cloud
Logging). Only allow-listed keys are emitted, and string values must look like
identifiers, so free text (transcript lines, AI output, names spoken in audio,
teacher names from sidecars, exception messages) cannot slip in by accident.
Anything not matching is replaced with "<redacted>".
"""
from __future__ import annotations

import json
import re
import sys
import time

ALLOWED_KEYS = {
    "severity", "event", "step", "recording_id", "device_id", "object_generation",
    "ms", "error_code", "error_type", "chunks", "duration_s", "size_bytes",
    "status", "model", "http_status", "turns", "speakers", "verified", "attempt",
    "device_count",
    "operation_id", "reason", "markers", "actor_id", "pdf_sha256", "pdf_bytes",
    "incident_number", "draft", "report_id", "issue_number",
    "from_version", "to_version",
    "session_id", "part_count",
}
# ids like rec_dev01_20260924_101400_RM12, error codes like BAD_FORMAT, models like gemini-3.8-flash
_SAFE_STR = re.compile(r"^[A-Za-z0-9_.:/\-]{0,120}$")
_NAME_LIKE = re.compile(r"[A-Z][a-z]+")  # ordinary capitalized words are never ids here


def _clean(key: str, value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    s = str(value)
    if not _SAFE_STR.match(s):
        return "<redacted>"
    # error_type is a Python class name (CamelCase) - allowed; everything else must not
    # contain capitalized words (a guard against e.g. device_id="Marcus").
    if key not in ("error_type", "severity", "error_code", "event", "step", "status", "reason") and _NAME_LIKE.search(s):
        return "<redacted>"
    return s


def log(event: str, severity: str = "INFO", **fields) -> None:
    entry = {"severity": severity, "event": event}
    for k, v in fields.items():
        if k in ALLOWED_KEYS and v is not None:
            entry[k] = _clean(k, v)
    print(json.dumps(entry, separators=(",", ":")), file=sys.stdout, flush=True)


class timer:
    """with timer() as t: ...; t.ms"""

    def __enter__(self):
        self._t = time.monotonic()
        return self

    def __exit__(self, *exc):
        self.ms = int((time.monotonic() - self._t) * 1000)
        return False
