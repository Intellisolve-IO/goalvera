"""Hosted review screen: server-side sign-in checks (Identity Platform ID tokens,
verified email, allowlist) and who may approve. Offline: tokens are signed with
a throwaway RSA key standing in for Google's securetoken keys.

  python -m pytest app/backend-pipeline/tests -q
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from google.auth import crypt, jwt as gjwt

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE.parents[2] / "tools"))

from review_api.auth import (Allowlist, Authenticator, CertCache, Roster, TokenVerifier,  # noqa: E402
                             bearer_token)
from review_api.notes import ApiError, Notes  # noqa: E402
from review_api.server import build_server  # noqa: E402
from review_api.store import LocalStore  # noqa: E402
from seed_local_session import RID, seed  # noqa: E402

PROJECT = "example-project"
ADMIN, TEACHER_EMAIL, OTHER = "admin@staff.example", "teacher@example.org", "reviewer@example.org"


def _key_and_cert():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-securetoken")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256()))
    pem_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    return pem_key, cert.public_bytes(serialization.Encoding.PEM).decode()


KEY_PEM, CERT_PEM = _key_and_cert()
OTHER_KEY_PEM, _ = _key_and_cert()
KID = "kid-1"


def token(email=ADMIN, verified=True, *, uid="UidAbc123XyZ", key=KEY_PEM, kid=KID, aud=PROJECT,
          iss=f"https://securetoken.google.com/{PROJECT}", exp_in=3600, iat_off=0, extra=None):
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": uid, "user_id": uid, "auth_time": now - 10,
              "iat": now + iat_off, "exp": now + exp_in, "email": email, "email_verified": verified,
              "firebase": {"sign_in_provider": "password", "identities": {"email": [email]}}}
    claims.update(extra or {})
    signer = crypt.RSASigner.from_string(key, key_id=kid)
    return gjwt.encode(signer, claims, header={"kid": kid}).decode()


def unsigned(claims):
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{b({'alg': 'none', 'typ': 'JWT'})}.{b(claims)}."


def verifier():
    return TokenVerifier(PROJECT, CertCache(fetch=lambda url: ({KID: CERT_PEM}, 3600)))


def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture()
def store(tmp_path):
    seed(str(tmp_path))
    s = LocalStore(tmp_path)
    s.write_json("config/review_allowlist.json", {"users": [
        {"email": ADMIN, "role": "admin"},
        {"email": TEACHER_EMAIL, "role": "reviewer"},
        {"email": OTHER, "role": "reviewer"},
        {"email": "TODO-teacher-email-pending", "role": "reviewer"}]})
    roster = json.loads((HERE.parents[2] / "samples/legacy_demo_roster.json").read_text())
    roster["teachers"]["usr_demo_sarah"]["emails"] = [TEACHER_EMAIL.upper()]   # case must not matter
    s.write_json("config/demo_roster.json", roster)
    return s


@pytest.fixture()
def authn(store):
    return Authenticator(verifier(), Allowlist(store), Roster(store))


# ---------------------------------------------------------------- the five required cases

def test_no_token(authn):
    for h in ({}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "}, {"Authorization": "Bearer notajwt"}):
        with pytest.raises(ApiError) as e:
            authn.require(h)
        assert (e.value.http, e.value.code) == (401, "SIGN_IN_REQUIRED")


@pytest.mark.parametrize("bad", [
    dict(key=OTHER_KEY_PEM),                                  # forged signature
    dict(kid="unknown-kid"),
    dict(aud="some-other-project"),
    dict(iss="https://securetoken.google.com/some-other-project"),
    dict(iss="https://accounts.google.com"),
    dict(exp_in=-3600),                                       # expired
    dict(iat_off=3600, exp_in=7200),                          # issued in the future
    dict(uid=""),
    dict(extra={"firebase": {"tenant": "t1", "sign_in_provider": "password"}}),
])
def test_bad_token(authn, bad):
    with pytest.raises(ApiError) as e:
        authn.require(hdr(token(**bad)))
    assert (e.value.http, e.value.code) == (401, "BAD_TOKEN")


def test_unsigned_token_rejected_outside_emulator(authn):
    now = int(time.time())
    t = unsigned({"iss": f"https://securetoken.google.com/{PROJECT}", "aud": PROJECT, "sub": "u1",
                  "iat": now, "exp": now + 600, "email": ADMIN, "email_verified": True})
    with pytest.raises(ApiError) as e:
        authn.require(hdr(t))
    assert e.value.code == "BAD_TOKEN"


def test_unverified_email(authn):
    for v in (False, None, "true"):
        with pytest.raises(ApiError) as e:
            authn.require(hdr(token(ADMIN, verified=v)))
        assert (e.value.http, e.value.code) == (403, "EMAIL_NOT_VERIFIED")


def test_not_on_allowlist(authn):
    for email in ("stranger@example.org", "todo-teacher-email-pending@example.org", "admin@staff.example.evil.com"):
        with pytest.raises(ApiError) as e:
            authn.require(hdr(token(email)))
        assert (e.value.http, e.value.code) == (403, "NOT_APPROVED")


def test_allowed(authn):
    r = authn.require(hdr(token(ADMIN.upper())))
    assert (r.email, r.role, r.is_admin, r.uid, r.provider) == (ADMIN, "admin", True, "UidAbc123XyZ", "password")
    r = authn.require(hdr(token(TEACHER_EMAIL)))
    assert r.role == "reviewer" and r.teacher_ids == {"usr_demo_sarah"}
    assert authn.require(hdr(token(OTHER))).teacher_ids == frozenset()


def test_allowlist_changes_apply_after_ttl(store):
    clock = [0.0]
    al = Allowlist(store, ttl=30, now=lambda: clock[0])
    assert al.role("new@example.org") is None
    store.write_json("config/review_allowlist.json", {"users": [{"email": "New@Example.org"}]})
    assert al.role("new@example.org") is None          # cached
    clock[0] = 31
    assert al.role("new@example.org") == "reviewer"


def test_emulator_mode_refused_on_cloud_run(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "review")
    with pytest.raises(RuntimeError):
        TokenVerifier(PROJECT, emulator=True)


def test_bearer_token_parsing():
    assert bearer_token({"Authorization": "Bearer a.b.c"}) == "a.b.c"
    assert bearer_token({"Authorization": "bearer a.b.c"}) is None
    assert bearer_token({"Authorization": "Bearer " + "a." * 5000 + "b"}) is None


# ---------------------------------------------------------------- who may approve

def test_approval_rules_and_identity(store, authn):
    notes = Notes(store)
    analysis = store.read_json(f"drafts/{RID}.json")["analysis"]
    other = authn.require(hdr(token(OTHER, uid="uidOther")))
    s = notes.session(RID, other)
    assert s["can_approve"] is False and s["viewer_may_act"] is False
    with pytest.raises(ApiError) as e:
        notes.approve(RID, other, analysis)
    assert (e.value.http, e.value.code) == (403, "NOT_SESSION_TEACHER")

    teacher = authn.require(hdr(token(TEACHER_EMAIL, uid="uidTeacher")))
    assert notes.session(RID, teacher)["can_approve"] is True
    rec = notes.approve(RID, teacher, analysis)
    assert rec["approved_by_id"] == "uidTeacher" and rec["approved_by_email"] == TEACHER_EMAIL
    assert rec["approved_by_name"] == TEACHER_EMAIL and rec["approved_as"] == "teacher"
    [audit] = notes.audit_trail(RID)
    assert audit["actor_id"] == "uidTeacher" and audit["actor_email"] == TEACHER_EMAIL
    assert audit["sign_in_provider"] == "password"

    with pytest.raises(ApiError) as e:
        notes.pdf(RID, other)
    assert e.value.code == "NOT_SESSION_TEACHER"
    admin = authn.require(hdr(token(ADMIN, uid="uidAdmin")))
    data, _ = notes.pdf(RID, admin)
    assert data[:5] == b"%PDF-"
    assert notes.audit_trail(RID)[-1]["actor_email"] == ADMIN


def test_admin_may_approve_any_session(store, authn):
    notes = Notes(store)
    admin = authn.require(hdr(token(ADMIN, uid="uidAdmin")))
    rec = notes.approve(RID, admin, store.read_json(f"drafts/{RID}.json")["analysis"])
    assert rec["approved_as"] == "admin" and rec["approved_by_email"] == ADMIN


# ---------------------------------------------------------------- over HTTP

@pytest.fixture()
def hosted(store, authn, tmp_path_factory):
    static = tmp_path_factory.mktemp("dist")
    (static / "index.html").write_text("<!doctype html><title>review</title>")
    (static / "assets").mkdir()
    (static / "assets" / "app.js").write_text("console.log(1)")
    srv = build_server(store, 0, "local", auth=authn, static_dir=str(static),
                       auth_config={"projectId": PROJECT, "apiKey": "k", "providers": ["password"]},
                       origins={"https://review.example.org"})
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def call(base, path, *, tok=None, method="GET", body=None, headers=None):
    h = dict(headers or {})
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


DATA_PATHS = ["/api/sessions", f"/api/sessions/{RID}/transcript", f"/api/sessions/{RID}/audit",
              f"/api/sessions/{RID}/pdf", "/api/health", "/api/anything"]


def test_http_every_data_endpoint_checks_the_token(hosted):
    cases = [(None, 401, "SIGN_IN_REQUIRED"), (token(key=OTHER_KEY_PEM), 401, "BAD_TOKEN"),
             (token(ADMIN, verified=False), 403, "EMAIL_NOT_VERIFIED"),
             (token("stranger@example.org"), 403, "NOT_APPROVED")]
    for tok, code, err in cases:
        for p in DATA_PATHS:
            st, body, _ = call(hosted, p, tok=tok)
            assert (st, json.loads(body)) == (code, {"error": err}), p
        st, body, _ = call(hosted, f"/api/sessions/{RID}/approve", tok=tok, method="POST", body={"analysis": {}})
        assert (st, json.loads(body)) == (code, {"error": err})
    # the demo-laptop header does nothing when hosted
    st, _, _ = call(hosted, "/api/sessions", headers={"X-Reviewer-Id": "usr_demo_sarah"})
    assert st == 401


def test_http_waiting_user_gets_no_data(hosted):
    st, body, _ = call(hosted, "/api/me", tok=token("stranger@example.org"))
    assert st == 200 and json.loads(body) == {"email": "stranger@example.org", "email_verified": True,
                                               "approved": False, "role": None}
    st, _, _ = call(hosted, "/api/me")
    assert st == 401


def test_http_allowed_user_full_flow(hosted, store):
    st, body, _ = call(hosted, "/api/me", tok=token(TEACHER_EMAIL))
    assert json.loads(body)["approved"] is True
    st, body, _ = call(hosted, "/api/sessions", tok=token(TEACHER_EMAIL))
    [s] = json.loads(body)["sessions"]
    assert st == 200 and s["recording_id"] == RID and s["can_approve"] is True
    analysis = s["processed_analysis"]
    st, body, _ = call(hosted, f"/api/sessions/{RID}/approve", tok=token(OTHER, uid="uidOther"),
                       method="POST", body={"analysis": analysis})
    assert (st, json.loads(body)["error"]) == (403, "NOT_SESSION_TEACHER")
    buf = io.StringIO()
    with redirect_stdout(buf):
        st, body, _ = call(hosted, f"/api/sessions/{RID}/approve", tok=token(TEACHER_EMAIL, uid="uidTeacher"),
                           method="POST", body={"analysis": analysis},
                           headers={"Origin": "https://review.example.org"})
        assert st == 200 and json.loads(body)["approved_by_name"] == TEACHER_EMAIL
        st, pdf, h = call(hosted, f"/api/sessions/{RID}/pdf", tok=token(TEACHER_EMAIL, uid="uidTeacher"))
    assert st == 200 and pdf[:5] == b"%PDF-" and h["Content-Type"] == "application/pdf"
    assert not store.list("pdf") and not [n for n in store.list("") if n.endswith(".pdf")]
    logs = buf.getvalue()
    assert "example.org" not in logs and "uidTeacher" not in logs and "u_" in logs   # logs: no emails, no raw uid
    events = [e["event"] for e in Notes(store).audit_trail(RID)]
    assert events == ["session_approved", "pdf_downloaded"]


def test_http_origin_and_static(hosted):
    st, body, _ = call(hosted, "/api/sessions", tok=token(ADMIN), headers={"Origin": "https://evil.example"})
    assert (st, json.loads(body)) == (403, {"error": "BAD_ORIGIN"})
    st, body, h = call(hosted, "/")
    assert st == 200 and b"<title>review</title>" in body
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"] and h["X-Frame-Options"] == "DENY"
    assert "securetoken.googleapis.com" in h["Content-Security-Policy"]
    st, body, _ = call(hosted, "/some/deep/link")
    assert st == 200 and b"<title>review</title>" in body          # SPA fallback
    st, body, _ = call(hosted, "/../../etc/passwd")
    assert b"root:" not in body
    st, body, h = call(hosted, "/assets/app.js")
    assert body == b"console.log(1)" and "immutable" in h["Cache-Control"]
    st, body, _ = call(hosted, "/api/auth-config")
    assert st == 200 and json.loads(body)["providers"] == ["password"]
