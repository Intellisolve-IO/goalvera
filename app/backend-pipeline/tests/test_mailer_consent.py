"""infra/mailer_consent.py: the one-time sender approval stores nothing unless the right
mailbox approved exactly gmail.send (+ openid email)."""
from __future__ import annotations

import base64
import importlib.util
import json
import urllib.parse
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("mailer_consent", Path(__file__).resolve().parents[3] / "infra/mailer_consent.py")
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)
SENDER = "snowwhite@staff.example"


def idt(email, verified=True):
    p = base64.urlsafe_b64encode(json.dumps({"email": email, "email_verified": verified}).encode()).decode().rstrip("=")
    return f"h.{p}.s"


def tokens(**kw):
    t = {"refresh_token": "rt", "scope": f"openid https://www.googleapis.com/auth/userinfo.email {mc.GMAIL_SEND}",
         "id_token": idt(SENDER)}
    t.update(kw)
    return t


def test_link_asks_only_for_send_and_pins_the_mailbox():
    link, verifier, state = mc.auth_link({"client_id": "cid", "client_secret": "s"}, "http://127.0.0.1:8765/", SENDER)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(link).query))
    assert q["scope"] == f"openid email {mc.GMAIL_SEND}" and q["login_hint"] == SENDER and q["hd"] == "staff.example"
    assert q["access_type"] == "offline" and q["code_challenge_method"] == "S256" and q["state"] == state
    assert "client_secret" not in link and verifier not in link


def test_check_accepts_the_sender_with_gmail_send_only():
    mc.check(tokens(), SENDER)


@pytest.mark.parametrize("bad", [
    {"id_token": idt("admin@example.com")}, {"id_token": idt(SENDER, verified=False)},
    {"scope": "openid email https://www.googleapis.com/auth/gmail.readonly"},
    {"scope": f"openid email {mc.GMAIL_SEND} https://mail.google.com/"}, {"refresh_token": None}])
def test_check_refuses_anything_else(bad):
    with pytest.raises(SystemExit):
        mc.check(tokens(**bad), SENDER)


def test_pasted_address_must_match_the_state():
    assert mc.code_from_pasted("http://127.0.0.1:8765/?state=abc&code=4/xyz&scope=x", "abc") == "4/xyz"
    with pytest.raises(SystemExit):
        mc.code_from_pasted("http://127.0.0.1:8765/?state=other&code=4/xyz", "abc")
