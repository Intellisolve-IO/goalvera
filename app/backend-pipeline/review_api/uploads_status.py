"""Recordings still being processed (David, Oct 10 2026; tasks#106).

A recording shows in the Recordings list as soon as its upload lands, before its notes exist:
"Uploaded, waiting", "Transcribing", "Drafting notes", or a clear failure. The browser polls this
while any row is still processing; once the draft exists the row disappears from here and the
normal session row takes its place.

Reads only what the pipeline already writes: the uploaded file names under rec/ (device, date, time,
class code; no audio is read) and work/<rid>/manifest.json (status and error code). Nothing is
written and nothing is journaled (no student data: a row has the classroom, the recording time and
the status). Needs the one-time IAM in infra/setup_demo_gaps.sh (read work/*/manifest.json, list
work/ and rec/; never read access to the audio); without it the list is simply empty.

  GET /api/uploads/status
"""
from __future__ import annotations

import datetime as dt
import re

from common.classcodes import find_class
from common.safelog import log

REC_RE = re.compile(r"^rec/(?P<device>[a-z0-9][a-z0-9-]{0,31})/(?P<stem>(?P<date>\d{8})_(?P<time>\d{6})_(?P<cls>[A-Za-z0-9-]{1,16}))\.wav$")
RECENT = dt.timedelta(days=7)          # older uploads without notes are not listed
STUCK = dt.timedelta(minutes=15)       # an upload with no pipeline record after this: didn't start
MAX_ROWS = 25

FAILURES = {
    "NOT_WAV": "The file isn't a WAV recording.",
    "BAD_FORMAT": "The audio format couldn't be read.",
    "ANALYZER_FAILED": "Drafting the notes failed.",
    "NO_START": "Processing didn't start (the file may be too large or the upload incomplete).",
}


def recording_id(device: str, stem: str) -> str:      # same as pipeline_service.main.recording_id
    return f"rec_{device}_{stem}".replace("-", "_")


def failure_text(code: str | None) -> str:
    if code and code.startswith("STT_"):
        return "Transcription failed."
    return FAILURES.get(code or "", "Processing failed.")


class UploadsStatus:
    def __init__(self, notes, now=None):
        self.notes, self.store = notes, notes.store
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))

    def _list(self, prefix: str) -> list[str]:
        try:
            return self.store.list(prefix)
        except Exception as exc:  # noqa: BLE001 - no IAM yet: show nothing rather than fail the page
            log("uploads_status_unavailable", "WARNING", step="uploads", error_type=type(exc).__name__)
            return []

    def rows(self) -> list[dict]:
        drafts = {n[len("drafts/"):-len(".json")] for n in self._list("drafts/") if n.endswith(".json")}
        now, roster, out = self._now(), self.notes.roster(), []
        cands = []
        try:
            lt = getattr(self.store, "list_times", None)
            times = lt("rec/") if lt else {n: self.store.created(n) for n in self.store.list("rec/")}
        except Exception as exc:  # noqa: BLE001 - no IAM yet: show nothing rather than fail the page
            log("uploads_status_unavailable", "WARNING", step="uploads", error_type=type(exc).__name__)
            times = {}
        for n in sorted(times):
            m = REC_RE.match(n)
            if not m:
                continue
            rid = recording_id(m["device"], m["stem"])
            up = times[n]
            if rid not in drafts and up is not None and now - up <= RECENT:
                cands.append((up, n, m, rid))
        for uploaded, n, m, rid in sorted(cands, key=lambda c: c[0], reverse=True)[:MAX_ROWS]:
            row = self._row(rid, m, uploaded, now, roster)
            if row:
                out.append(row)
        return out

    def _row(self, rid, m, uploaded, now, roster) -> dict | None:
        try:
            man = self.store.read_json(f"work/{rid}/manifest.json")
        except Exception:  # noqa: BLE001
            man = None
        status = (man or {}).get("status")
        code = None
        if man is None:
            state, label = ("failed", "Didn't start") if now - uploaded > STUCK else ("uploaded", "Uploaded, waiting to start")
            code = "NO_START" if state == "failed" else None
        elif status == "failed":
            state, label, code = "failed", "Failed", man.get("error_code") or "FAILED"
        elif status == "transcribing":
            drafting = any(x.endswith("/analysis.lock") for x in self._list(f"work/{rid}/"))
            state, label = ("drafting", "Drafting notes") if drafting else ("transcribing", "Transcribing")
        elif status == "pending_review":
            state, label = "drafting", "Finishing"
        else:
            state, label = "checking", "Checking the audio"
        display, _ = find_class(roster, m["cls"])
        d, t = m["date"], m["time"]
        return {
            "recording_id": rid, "device_id": m["device"], "class_code": display or m["cls"],
            "needs_class_assignment": display is None,
            "recorded_at_local": f"{d[:4]}-{d[4:6]}-{d[6:]}T{t[:2]}:{t[2:4]}:{t[4:]}",
            "uploaded_at": uploaded.astimezone(dt.timezone.utc).isoformat(),
            "duration_seconds": (man or {}).get("duration_seconds"),
            "state": state, "label": label, "error_code": code,
            "error_text": failure_text(code) if state == "failed" else None,
            "processing": state != "failed",
        }

    def view(self) -> dict:
        rows = self.rows()
        return {"uploads": rows, "processing": sum(r["processing"] for r in rows),
                "checked_at": self._now().astimezone(dt.timezone.utc).isoformat()}
