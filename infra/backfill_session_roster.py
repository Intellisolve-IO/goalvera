#!/usr/bin/env python3
"""Re-run the pipeline's class/teacher (roster) step on an existing session,
WITHOUT touching the audio or re-running Speech-to-Text or Gemini (tasks#35).

Use it when a session came in before its class was in the roster, or when a
class needs to be assigned by hand. It uses the same resolve_session() as the
pipeline, then updates only the roster fields (class, student, teacher,
needs_class_assignment) in transcripts/<id>.json and drafts/<id>.json:
  * nothing is deleted; notes, transcript text, markers, audio are unchanged
  * refuses if the session is already approved
  * writes are conditional on the object generation it read (no lost updates)
  * each rewritten record keeps its original creation time as customTime, so
    the 30-day auto-delete is still counted from when it was first written
  * an audit record (ids + changed field names only) goes to audit/<id>/

  ./infra/backfill_session_roster.py <recording_id>                      # dry run
  ./infra/backfill_session_roster.py <recording_id> --yes                # apply
  ./infra/backfill_session_roster.py <recording_id> --class "Math 2B" --yes   # assign a class

Store: the bucket through the gcloud CLI (your normal `gcloud auth login`) by
default; --adc for the Python client with application-default credentials;
--local-dir for a local folder (tests / offline).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/backend-pipeline"))

from common.classcodes import find_class, resolve_session  # noqa: E402
from pipeline_service.records import ROSTER_FIELDS  # noqa: E402
from review_api.store import Conflict  # noqa: E402

PROJECT = os.environ.get("PROJECT_ID", "example-project")
BUCKET = os.environ.get("BUCKET", f"{PROJECT}-recordings")
RID_RE = re.compile(r"^rec_[A-Za-z0-9_]{1,120}$")
SRC_RE = re.compile(r"^rec/[a-z0-9-]+/\d{8}_\d{6}_(?P<code>[A-Za-z0-9_-]+)\.wav$")


class BackfillError(Exception):
    pass


def backfill(store, rid: str, roster: dict, *, assign_class: str | None = None, apply: bool = False,
             now: dt.datetime | None = None) -> dict:
    if not RID_RE.match(rid):
        raise BackfillError("BAD_RECORDING_ID")
    # generations first, so a change made after we read is caught at write time
    d_gen, t_gen = store.generation(f"drafts/{rid}.json"), store.generation(f"transcripts/{rid}.json")
    draft = store.read_json(f"drafts/{rid}.json")
    if draft is None:
        raise BackfillError("DRAFT_NOT_FOUND")
    if store.read_json(f"approvals/{rid}.json") is not None:
        raise BackfillError("ALREADY_APPROVED")
    transcript = store.read_json(f"transcripts/{rid}.json")
    s = dict(draft.get("session") or {})

    src = s.get("source_object") or (transcript or {}).get("source_object") or ""
    m = SRC_RE.match(src)
    filename_code = s.get("class_code_file") or (m["code"] if m else None) or s.get("class_code")
    sidecar = store.read_json(src[:-4] + ".json") if m else None   # gone after 7 days; then the file token is used
    side = dict(sidecar or {})
    if assign_class:
        display, _ = find_class(roster, assign_class)
        if not display:
            raise BackfillError("CLASS_NOT_IN_ROSTER")
        side["class_code"] = display
    ctx = resolve_session(roster, filename_code=filename_code, sidecar=side)
    new_fields = {k: ctx[k] for k in ROSTER_FIELDS}
    changes = {k: [s.get(k), v] for k, v in new_fields.items() if s.get(k) != v}
    report = {"recording_id": rid, "changes": changes, "sidecar_found": sidecar is not None,
              "applied": False, "needs_class_assignment": ctx["needs_class_assignment"]}
    if not apply or not changes:
        return report

    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = {"at": now.isoformat(timespec="seconds"), "tool": "infra/backfill_session_roster.py",
             "fields": sorted(changes), "class_assigned_by_hand": bool(assign_class)}
    if transcript is not None:
        t_created = store.created(f"transcripts/{rid}.json")
        t2 = {**transcript, **new_fields, "roster_backfill": [*transcript.get("roster_backfill", []), stamp]}
        store.write_json(f"transcripts/{rid}.json", t2, if_generation=t_gen, custom_time=t_created)
    d_created = store.created(f"drafts/{rid}.json")
    d2 = {**draft, "session": {**s, **new_fields},
          "roster_backfill": [*draft.get("roster_backfill", []), stamp]}
    store.write_json(f"drafts/{rid}.json", d2, if_generation=d_gen, custom_time=d_created)
    store.write_json(
        f"audit/{rid}/{now.strftime('%Y%m%dT%H%M%S%fZ')}_roster_backfilled_{secrets.token_hex(3)}.json",
        {"event": "roster_backfilled", "recording_id": rid, "actor_id": "ops_backfill",
         "at": stamp["at"], "fields": stamp["fields"], "class_assigned_by_hand": bool(assign_class)},
        create_only=True, custom_time=d_created)
    report["applied"] = True
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("recording_id")
    ap.add_argument("--class", dest="assign_class", help="assign this roster class (any spelling)")
    ap.add_argument("--yes", action="store_true", help="apply (default is a dry run)")
    ap.add_argument("--roster", help="roster JSON file (default: config/demo_roster.json in the store)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--local-dir")
    g.add_argument("--adc", action="store_true", help="use application-default credentials")
    a = ap.parse_args()

    if a.local_dir:
        from review_api.store import LocalStore
        store = LocalStore(a.local_dir)
    elif a.adc:
        from review_api.store import GcsStore
        store = GcsStore(BUCKET, PROJECT)
    else:
        from review_api.store import GcloudCliStore
        store = GcloudCliStore(BUCKET, PROJECT)
    roster = json.loads(Path(a.roster).read_text()) if a.roster else store.read_json("config/demo_roster.json")
    if not roster:
        print("no roster found", file=sys.stderr)
        return 2
    try:
        rep = backfill(store, a.recording_id, roster, assign_class=a.assign_class, apply=a.yes)
    except (BackfillError, Conflict) as e:
        print(f"refused: {type(e).__name__} {e}", file=sys.stderr)
        return 1
    if not rep["changes"]:
        print(f"{rep['recording_id']}: already up to date (needs_class_assignment={rep['needs_class_assignment']})")
        return 0
    for k, (old, new) in rep["changes"].items():
        print(f"  {k}: {old!r} -> {new!r}")
    print(f"{rep['recording_id']}: {'APPLIED' if rep['applied'] else 'dry run - re-run with --yes'}"
          f" (sidecar {'found' if rep['sidecar_found'] else 'not found'};"
          f" needs_class_assignment={rep['needs_class_assignment']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
