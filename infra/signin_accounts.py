#!/usr/bin/env python3
"""Sign-in accounts (Identity Platform users) for people on the review site's staff list (tasks#108).

New sign-ups are OFF in Identity Platform (client.permissions.disabledUserSignup), so nobody can
create their own account, with a password or with Google. An admin creates the account ahead of time:

  ./infra/signin_accounts.py check snowwhite@staff.example      # read-only: is there an account?
  ./infra/signin_accounts.py create snowwhite@staff.example     # create it if it is missing
  ./infra/review_allowlist.py add someone@staff.example         # adds to the list AND creates it

What "create" makes: an account with the email address only - no password, no Google link yet,
emailVerified false. Nobody can sign in to it until the person uses "Sign in with Google" (once David
has enabled the Google provider): staff.example mail is hosted by Google Workspace, so Google is the
authoritative provider for these addresses, and with "one account per email" (allowDuplicateEmails
false) Firebase attaches the Google identity to this account (same user id) instead of refusing it.
emailVerified stays false on purpose: an unverified account carries nothing an impostor could keep
(Firebase strips unverified sign-in methods when the real owner signs in with Google).
For password sign-in instead, an admin sends a reset link (Authentication -> Users -> Reset password).

Rules: only addresses on the live staff list (config/review_allowlist.json) are accepted; an existing
account is never changed (reported as "exists"); nothing is ever deleted. Being on the list is still
what grants access - the app checks it on every request; the account only lets the person sign in.
Uses your gcloud login (gcloud auth print-access-token); set CLOUDSDK_CORE_ACCOUNT to choose one.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/backend-pipeline"))

from review_api.auth import ALLOWLIST_OBJECT, normalize_email  # noqa: E402

PROJECT = os.environ.get("PROJECT_ID", "example-project")
BUCKET = os.environ.get("BUCKET", f"{PROJECT}-recordings")
IDT = "https://identitytoolkit.googleapis.com/v1/projects/{project}/{path}"
# Only these fields are ever sent on create: no password, no providers, no custom claims.
CREATE_FIELDS = ("email", "emailVerified", "displayName")


class GcloudIdentity:
    """Minimal Identity Platform admin client on the caller's gcloud login."""

    def __init__(self, project: str = PROJECT):
        self.project = project
        self._token = None

    def _post(self, path: str, body: dict) -> dict:
        if self._token is None:
            self._token = subprocess.run(["gcloud", "auth", "print-access-token"], check=True,
                                         capture_output=True, text=True).stdout.strip()
        req = urllib.request.Request(IDT.format(project=self.project, path=path),
                                     data=json.dumps(body).encode(), method="POST", headers={
                                         "Authorization": f"Bearer {self._token}",
                                         "x-goog-user-project": self.project,
                                         "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise SystemExit(f"Identity Platform {path}: HTTP {e.code} {e.read().decode()[:300]}")

    def lookup(self, email: str) -> dict | None:
        users = self._post("accounts:lookup", {"email": [email]}).get("users") or []
        return users[0] if users else None

    def create(self, body: dict) -> dict:
        return self._post("accounts", body)


def describe(user: dict | None) -> str:
    if not user:
        return "no account"
    providers = [p.get("providerId") for p in user.get("providerUserInfo") or []] or ["none yet"]
    state = "disabled" if user.get("disabled") else "active"
    return f"account {user.get('localId')} ({state}; sign-in methods: {', '.join(providers)})"


def ensure_account(idp, email: str, staff: set[str], display_name: str | None = None) -> tuple[str, dict | None]:
    """('exists'|'created', user). Refuses addresses not on the staff list; never changes an account."""
    e = normalize_email(email)
    if not e:
        raise ValueError(f"not a valid email address: {email!r}")
    if e not in staff:
        raise ValueError(f"{e} is not on the staff list ({ALLOWLIST_OBJECT}); add them first")
    user = idp.lookup(e)
    if user:
        return "exists", user
    body = {"email": e, "emailVerified": False}
    if display_name:
        body["displayName"] = str(display_name)[:100]
    assert set(body) <= set(CREATE_FIELDS)
    created = idp.create(body)
    return "created", idp.lookup(e) or {"localId": created.get("localId"), "email": e}


def staff_emails(store) -> set[str]:
    doc = store.read_json(ALLOWLIST_OBJECT) or {}
    return {e for e in (normalize_email(u.get("email")) for u in doc.get("users", [])) if e}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=["check", "create"])
    ap.add_argument("emails", nargs="+")
    ap.add_argument("--name", help="display name (create, one email only)")
    a = ap.parse_args()
    if a.name and len(a.emails) != 1:
        ap.error("--name goes with exactly one email")
    idp = GcloudIdentity()
    if a.action == "check":
        for email in a.emails:
            e = normalize_email(email)
            print(f"  {e or email}: {describe(idp.lookup(e)) if e else 'not a valid email address'}")
        return 0
    from review_api.store import GcloudCliStore
    staff = staff_emails(GcloudCliStore(BUCKET, PROJECT))
    rc = 0
    for email in a.emails:
        try:
            what, user = ensure_account(idp, email, staff, a.name)
            print(f"  {normalize_email(email)}: {what} - {describe(user)}")
        except ValueError as err:
            print(f"  refused: {err}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
