#!/usr/bin/env python3
"""Delete one session everywhere in the demo project (tasks#35 step 5; checklist row 36).

Removes, for one recording:
  rec/<device>/<stem>.wav and .json (sidecar)     audio + label file
  work/<id>/**                                    manifest, chunks, raw Speech-to-Text output
  transcripts/<id>.json                           transcript
  drafts/<id>.json                                Gemini draft notes
  media/<id>/*                                    Teacher Notes photos / videos + their session link
  approvals/<id>.json                             teacher approval (who, when, approved notes)
  audit/<id>/**                                   approval / PDF-download audit records

Soft delete is off on the bucket, so deleted objects are gone immediately
(no 7-day recovery copy). Logs never contain transcript text or names, so
nothing needs scrubbing there. See docs/deletion-runbook.md for the
non-bucket steps (teacher browser, downloaded PDFs, device SD card).

  ./infra/delete_session.py rec_atl_demo_01_20260928_101400_RM12          # dry run: lists what would go
  ./infra/delete_session.py rec_atl_demo_01_20260928_101400_RM12 --yes    # actually delete
  ./infra/delete_session.py --object rec/atl-demo-01/20260928_101400_RM12.wav --yes
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

PROJECT = os.environ.get("PROJECT_ID", "example-project")
BUCKET = os.environ.get("BUCKET", f"{PROJECT}-recordings")
REC_RE = re.compile(r"^rec/(?P<device>[a-z0-9-]+)/(?P<stem>\d{8}_\d{6}_[A-Za-z0-9-]+)\.(wav|json)$")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("recording_id", nargs="?")
    ap.add_argument("--object", help="the uploaded object path instead of the recording id")
    ap.add_argument("--yes", action="store_true", help="really delete (default is a dry run)")
    a = ap.parse_args()
    os.environ.setdefault("GOOGLE_CLOUD_QUOTA_PROJECT", PROJECT)
    from google.cloud import storage

    b = storage.Client(project=PROJECT).bucket(BUCKET)
    rid, source = a.recording_id, None
    if a.object:
        m = REC_RE.match(a.object)
        if not m:
            print("object must look like rec/<device>/<YYYYMMDD_HHMMSS_CLASS>.wav", file=sys.stderr)
            return 2
        source = f"rec/{m['device']}/{m['stem']}.wav"
        rid = f"rec_{m['device']}_{m['stem']}".replace("-", "_")
    if not rid or not re.match(r"^rec_[A-Za-z0-9_]+$", rid):
        print("give a recording id (rec_...) or --object", file=sys.stderr)
        return 2
    if source is None:
        mb = b.blob(f"work/{rid}/manifest.json")
        if mb.exists():
            source = json.loads(mb.download_as_bytes()).get("source_object")
        else:  # no manifest: find the audio by scanning rec/ for a matching name
            for blob in b.list_blobs(prefix="rec/"):
                m = REC_RE.match(blob.name)
                if m and f"rec_{m['device']}_{m['stem']}".replace("-", "_") == rid:
                    source = f"rec/{m['device']}/{m['stem']}.wav"
                    break

    names = []
    if source:
        names += [source, source[:-4] + ".json"]
    names += [f"transcripts/{rid}.json", f"drafts/{rid}.json", f"approvals/{rid}.json"]
    names += [bl.name for bl in b.list_blobs(prefix=f"work/{rid}/")]
    names += [bl.name for bl in b.list_blobs(prefix=f"audit/{rid}/")]
    names += [bl.name for bl in b.list_blobs(prefix=f"media/{rid}/")]   # Teacher Notes photos / videos (tasks#112)
    found = [n for n in dict.fromkeys(names) if b.blob(n).exists()]
    if not found:
        print(f"nothing found for {rid}")
        return 0
    for n in found:
        print(("deleting " if a.yes else "would delete ") + f"gs://{BUCKET}/{n}")
        if a.yes:
            b.blob(n).delete()
    if not a.yes:
        print("dry run - re-run with --yes to delete")
    else:
        left = [n for n in found if b.blob(n).exists()]
        print("verified: all deleted" if not left else f"STILL PRESENT: {left}")
        return 1 if left else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
