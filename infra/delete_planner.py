#!/usr/bin/env python3
"""Delete weekly lesson planner records (tasks#83) - pilot end, or one student / classroom.

The planner's records are NOT on the bucket's 30-day auto-delete rule (they are kept
to pilot end, DPA impact D1-D3). This script removes them:
  planner/plans/<class_key>/**                   weekly plans (every version)
  planner/students/<student_id>/**               weekly summaries + teacher notes (every version)

Soft delete is off on the bucket, so deleted objects are gone immediately. Journal
entries (ids only) stay in the activity journal under its own retention.

  ./infra/delete_planner.py --student stu_101            # dry run: lists what would go
  ./infra/delete_planner.py --student stu_101 --yes      # a student leaves the program
  ./infra/delete_planner.py --classroom RM12 --yes       # one classroom's plans
  ./infra/delete_planner.py --all --yes                  # pilot end: everything under planner/
  ./infra/delete_planner.py --all --local-dir /tmp/ap    # offline / tests
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

PROJECT = os.environ.get("PROJECT_ID", "example-project")
BUCKET = os.environ.get("BUCKET", f"{PROJECT}-recordings")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/backend-pipeline"))


def prefixes(*, student: str | None = None, classroom: str | None = None, everything: bool = False) -> list[str]:
    from common.classcodes import class_key
    if everything:
        return ["planner/"]
    out = []
    if student:
        if not re.match(r"^[a-z0-9_]{1,64}$", student):
            raise SystemExit("student must be an internal id like stu_101")
        out.append(f"planner/students/{student}/")
    if classroom:
        k = class_key(classroom)
        if not k:
            raise SystemExit("unknown classroom")
        out.append(f"planner/plans/{k}/")
    if not out:
        raise SystemExit("give --student, --classroom or --all")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--student")
    ap.add_argument("--classroom")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--local-dir", help="a local folder with the bucket layout instead of the bucket")
    ap.add_argument("--yes", action="store_true", help="really delete (default is a dry run)")
    a = ap.parse_args()
    pre = prefixes(student=a.student, classroom=a.classroom, everything=a.all)
    if a.local_dir:
        root = Path(a.local_dir)
        names = sorted(str(p.relative_to(root)) for x in pre for p in (root / x).rglob("*") if p.is_file())
        delete = lambda n: (root / n).unlink()  # noqa: E731
    else:
        os.environ.setdefault("GOOGLE_CLOUD_QUOTA_PROJECT", PROJECT)
        from google.cloud import storage
        b = storage.Client(project=PROJECT).bucket(BUCKET)
        names = sorted(bl.name for x in pre for bl in b.list_blobs(prefix=x))
        delete = lambda n: b.blob(n).delete()  # noqa: E731
    for n in names:
        print(("deleting " if a.yes else "would delete ") + n)
        if a.yes:
            delete(n)
    print(f"{len(names)} object(s) {'deleted' if a.yes else 'found (dry run; add --yes)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
