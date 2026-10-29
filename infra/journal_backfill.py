#!/usr/bin/env python3
"""Copy the existing audit/<recording>/*.json records (approvals, PDF downloads,
classroom moves) into the admin activity journal (tasks#77). Run once, after
./infra/setup_journal.sh. Event ids come from the audit object names, so a
re-run adds nothing twice (and the reports query drops any doubled insert).

  ./infra/journal_backfill.py                 # dry run: counts only
  ./infra/journal_backfill.py --yes           # append to BigQuery as you (gcloud ADC)
  ./infra/journal_backfill.py --local-dir D   # read a local store (tests / offline)

Writes ids only: actor uid, recording id, internal student id. No names, emails.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/backend-pipeline"))

from review_api.journal import BigQueryJournal, MemoryJournal, backfill_from_audit  # noqa: E402

PROJECT = os.environ.get("PROJECT_ID", "example-project")
BUCKET = os.environ.get("BUCKET", f"{PROJECT}-recordings")


def student_lookup(store):
    def student_of(rid: str):
        d = store.read_json(f"drafts/{rid}.json") or {}
        return (d.get("session") or {}).get("student_id")
    return student_of


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="append to BigQuery (default: dry run)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--local-dir")
    g.add_argument("--adc", action="store_true", help="read the bucket with application-default credentials")
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
    if a.yes:
        journal = BigQueryJournal(PROJECT, os.environ.get("JOURNAL_DATASET", "goalvera_journal"),
                                  os.environ.get("JOURNAL_TABLE", "activity"),
                                  os.environ.get("JOURNAL_REPORTS_DATASET", "goalvera_journal_reports"),
                                  location=os.environ.get("REGION", "us-east1"))
    else:
        journal = MemoryJournal()
    n = backfill_from_audit(store, journal, student_of=student_lookup(store))
    print(f"{n} audit record(s) {'appended to ' + journal.table_id if a.yes else 'found (dry run - re-run with --yes)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
