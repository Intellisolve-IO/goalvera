#!/usr/bin/env python3
"""Who may use the hosted review screen: gs://<bucket>/config/review_allowlist.json.

  ./infra/review_allowlist.py list
  ./infra/review_allowlist.py add someone@example.org            # reviewer (teacher)
  ./infra/review_allowlist.py add someone@example.org --admin    # demo admin
  ./infra/review_allowlist.py add someone@example.org --rbt      # read-only
  ./infra/review_allowlist.py remove someone@example.org

Changes apply within 30 seconds (the service re-reads the file); no redeploy.
"add" also creates the person's sign-in account if they have none (email only, no password; new
sign-ups are off, so this is how a new staff member gets one - see infra/signin_accounts.py).
Skip that with --no-signin-account; it is always skipped with --local-dir.
Admins can also change a person's app role / sign-in email in the app (Teachers tab, tasks#107); those
changes are kept under people/ in the bucket and apply on top of this file (review_api/people.py).
Uses your gcloud login by default (--adc: application-default; --local-dir: a folder).
A person also needs a verified email. Everyone listed reads every classroom.
A reviewer/teacher approves only sessions of their own classrooms or students
(app/samples/demo_roster.json -> teachers.*.emails); an RBT is read-only and never
sees parent emails; a demo admin may approve any session.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/backend-pipeline"))

from review_api.auth import ALLOWLIST_OBJECT, normalize_email  # noqa: E402

PROJECT = os.environ.get("PROJECT_ID", "example-project")
BUCKET = os.environ.get("BUCKET", f"{PROJECT}-recordings")


def edit(store, action: str, email: str | None = None, admin: bool = False, rbt: bool = False) -> list[dict]:
    gen = store.generation(ALLOWLIST_OBJECT)
    doc = store.read_json(ALLOWLIST_OBJECT) or {"users": []}
    users = doc.setdefault("users", [])
    if action == "list":
        return users
    e = normalize_email(email)
    if not e:
        raise SystemExit("not a valid email address")
    users[:] = [u for u in users if normalize_email(u.get("email")) != e]
    if action == "add":
        role = "admin" if admin else "rbt" if rbt else "reviewer"
        users.append({"email": e, "role": role})
    # conditional write: fails instead of overwriting someone else's edit
    if gen is None:
        store.write_json(ALLOWLIST_OBJECT, doc, create_only=True)
    else:
        store.write_json(ALLOWLIST_OBJECT, doc, if_generation=gen)
    return users


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["list", "add", "remove"])
    ap.add_argument("email", nargs="?")
    ap.add_argument("--admin", action="store_true")
    ap.add_argument("--rbt", action="store_true", help="allowlist role rbt (read-only)")
    ap.add_argument("--no-signin-account", action="store_true",
                    help="add: do not create the Identity Platform sign-in account")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--local-dir")
    g.add_argument("--adc", action="store_true")
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
    if a.action != "list" and not a.email:
        ap.error("email required")
    if a.admin and a.rbt:
        ap.error("choose either --admin or --rbt")
    users = edit(store, a.action, a.email, a.admin, a.rbt)
    for u in users:
        print(f"  {u.get('role', 'reviewer'):9} {u.get('email')}")
    if a.action == "add" and not (a.no_signin_account or a.local_dir):
        from signin_accounts import GcloudIdentity, describe, ensure_account
        e = normalize_email(a.email)
        what, user = ensure_account(GcloudIdentity(PROJECT), e, {normalize_email(u.get("email")) for u in users})
        print(f"  sign-in account for {e}: {what} - {describe(user)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
