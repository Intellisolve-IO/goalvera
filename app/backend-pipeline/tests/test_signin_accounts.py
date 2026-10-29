"""Pre-created sign-in accounts (new sign-ups stay off; tasks#108): infra/signin_accounts.py.

  python -m pytest app/backend-pipeline/tests/test_signin_accounts.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "infra"))
spec = importlib.util.spec_from_file_location("signin_accounts", ROOT / "infra/signin_accounts.py")
sa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sa)

STAFF = {"snowwhite@staff.example", "demo-teacher@staff.example"}


class FakeIdp:
    def __init__(self, users=None):
        self.users = {u["email"]: u for u in users or []}
        self.created = []

    def lookup(self, email):
        return self.users.get(email)

    def create(self, body):
        self.created.append(dict(body))
        u = {"localId": f"uid{len(self.created)}", **body}
        self.users[body["email"]] = u
        return {"localId": u["localId"]}


def test_creates_email_only_account_no_password():
    idp = FakeIdp()
    what, user = sa.ensure_account(idp, "SnowWhite@Staff.Example", STAFF, "Alex Morgan")
    assert what == "created" and user["email"] == "snowwhite@staff.example"
    assert idp.created == [{"email": "snowwhite@staff.example", "emailVerified": False, "displayName": "Alex Morgan"}]
    body = idp.created[0]
    assert not {"password", "passwordHash", "providerUserInfo", "customAttributes"} & set(body)


def test_existing_account_is_never_changed():
    existing = {"localId": "T1", "email": "demo-teacher@staff.example", "emailVerified": True,
                "providerUserInfo": [{"providerId": "password"}]}
    idp = FakeIdp([existing])
    what, user = sa.ensure_account(idp, "demo-teacher@staff.example", STAFF)
    assert what == "exists" and user is existing and idp.created == []


@pytest.mark.parametrize("email", ["stranger@gmail.com", "snowwhite@staff.example.evil.com", "white@staff.example",
                                   "staff.example", "not-an-email"])
def test_only_exact_staff_addresses(email):
    idp = FakeIdp()
    with pytest.raises(ValueError):
        sa.ensure_account(idp, email, STAFF)
    assert idp.created == []


def test_describe():
    assert sa.describe(None) == "no account"
    assert "none yet" in sa.describe({"localId": "u", "email": "x@y.io"})
    assert "password" in sa.describe({"localId": "u", "providerUserInfo": [{"providerId": "password"}]})


def test_allowlist_add_creates_account_unless_local_or_opted_out():
    src = (ROOT / "infra/review_allowlist.py").read_text()
    assert "ensure_account(" in src and "--no-signin-account" in src
    assert "not (a.no_signin_account or a.local_dir)" in src


def test_no_password_or_delete_paths_in_tool():
    src = (ROOT / "infra/signin_accounts.py").read_text()
    assert sa.CREATE_FIELDS == ("email", "emailVerified", "displayName")
    for bad in ("accounts:delete", "accounts:update", '"password"', "disabledUserSignup\":"):
        assert bad not in src
