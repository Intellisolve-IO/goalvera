#!/usr/bin/env python3
"""Check that a test run left no names or transcript text in Cloud Logging
(tasks#35 step 4). Searches every log entry from the demo project's Cloud Run
services, Eventarc and Speech/Vertex audit logs in the time window for:
  * every name in the demo roster / samples (student, teacher)
  * distinctive phrases from the synthetic script and from saved transcripts

  ./infra/check_logs.py --hours 3
Exit code 0 = clean, 1 = something matched (details printed).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT = os.environ.get("PROJECT_ID", "example-project")
ROOT = Path(__file__).resolve().parents[1]

NEEDLES = [
    "Marcus", "Sophia", "Jenkins", "Sarah",
    "headphones", "double digit", "double-digit", "calm table", "fire drill",
    "sixty-five", "37 + 28", "worksheets", "blue tray",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6)
    a = ap.parse_args()
    roster = json.loads((ROOT / "app/samples/demo_roster.json").read_text())
    for c in roster["class_codes"].values():
        NEEDLES.extend(filter(None, [c.get("student_first_name"), *(c.get("teacher_name") or "").split()]))
    for st in (roster.get("students") or {}).values():          # many-per-classroom roster (Oct 10)
        NEEDLES.extend(filter(None, [st.get("first_name"), st.get("last_name")]))
    needles = sorted({n.lower() for n in NEEDLES if n})

    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=a.hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    filt = f'timestamp>="{since}"'
    out = subprocess.run(
        ["gcloud", "logging", "read", filt, f"--project={PROJECT}", "--format=json", "--limit=20000"],
        check=True, capture_output=True, text=True,
    ).stdout
    entries = json.loads(out or "[]")
    hits = []
    for e in entries:
        blob = json.dumps(e).lower()
        for n in needles:
            if n in blob:
                hits.append((e.get("timestamp"), e.get("logName", "").rsplit("/", 1)[-1], n))
    print(f"scanned {len(entries)} log entries since {since} for {len(needles)} names/phrases")
    if hits:
        for h in hits[:50]:
            print("  HIT", *h)
        return 1
    print("clean: no names or transcript text found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
