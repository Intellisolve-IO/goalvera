"""Sign in with Google + Ops logging + 12-month parent_email/ retention ().

Ops' three checks for Google sign-in (all on the server, for every request):
  1. only email_verified Google accounts; 2. the full address matches a staff/allowlist entry exactly
  (case-insensitive; never a domain or partial match); 3. anyone whose access was removed is refused.
Ops logging: access_changed carries who (actor), which account, old / new role and, for an email change,
both addresses (that action only); each activity-report CSV download gets an audit record like a PDF's.

  python -m pytest app/backend-pipeline/tests/test_google_signin_ops.py -q
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import threading
from pathlib import Path

import pytest

from test_hosted_auth import ADMIN, TEACHER_EMAIL, call, token, verifier  # noqa: F401
from test_hosted_auth import authn, store  # noqa: F401  (pytest fixtures)
from test_journal import jrnl, site, tok  # noqa: F401  (pytest fixtures)
from test_roles import world  # noqa: F401
from test_planner import authn as pauthn, journal, pstore, who  # noqa: F401
from review_api.auth import Allowlist, Authenticator, Roster, normalize_email, sign_in_providers
from review_api.journal import JournalError, MemoryJournal, Recorder, make_entry
from review_api.notes import ApiError, Notes
from review_api.people import People
from review_api.server import _csp, build_server

ROOT = Path(__file__).resolve().parents[3]
BOTH = ("password", "google.com")


def gtoken(email, verified=True, uid="GoogleUid123", **kw):
    """An Identity Platform ID token as issued after Sign in with Google."""
    return token(email, verified, uid=uid, extra={"firebase": {"sign_in_provider": "google.com",
                                                               "identities": {"google.com": ["1098"], "email": [email]}}},
                 **kw)


def hdr(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture()
def gauth(store):  # noqa: F811
    return Authenticator(verifier(), Allowlist(store), Roster(store), providers=BOTH)


# ---------------------------------------------------------------- 1. verified email only

def test_google_verified_staff_email_signs_in(gauth):
    r = gauth.require(hdr(gtoken(ADMIN)))
    assert (r.email, r.role, r.provider) == (ADMIN, "admin", "google.com")
    t = gauth.require(hdr(gtoken(TEACHER_EMAIL)))
    assert t.role == "reviewer" and "usr_demo_sarah" in t.teacher_ids


def test_google_unverified_email_refused(gauth):
    for v in (False, None, "true", 1):
        with pytest.raises(ApiError) as e:
            gauth.require(hdr(gtoken(ADMIN, verified=v)))
        assert (e.value.http, e.value.code) == (403, "EMAIL_NOT_VERIFIED"), v
    claims, role = gauth.identify(hdr(gtoken(ADMIN, verified=False)))
    assert role is None                                     # /api/me says "not approved" too


# ---------------------------------------------------------------- 2. exact, case-insensitive match only

def test_google_email_match_is_case_insensitive(gauth):
    assert gauth.require(hdr(gtoken("Admin@Staff.EXAMPLE"))).role == "admin"


@pytest.mark.parametrize("email", [
    "xadmin@staff.example", "admin@staff.example.evil.com", "admin@mail.staff.example",
    "admn@staff.example", "admin@staff-other.example", "admin+1@staff.example",
    "someone@staff.example", "staff.example", "@staff.example", "admin@staff.example@x.io",
    "stranger@gmail.com", "", None])
def test_unknown_or_partial_email_refused(gauth, email):
    with pytest.raises(ApiError) as e:
        gauth.require(hdr(gtoken(email)))
    assert e.value.http == 403 and e.value.code in ("NOT_APPROVED", "EMAIL_NOT_VERIFIED"), email


def test_domain_or_wildcard_entries_on_the_list_match_nobody(store):  # noqa: F811
    store.write_json("config/review_allowlist.json", {"users": [
        {"email": "@staff.example", "role": "admin"}, {"email": "staff.example", "role": "admin"},
        {"email": "*@staff.example", "role": "admin"}, {"email": "a@b@staff.example", "role": "admin"}]})
    g = Authenticator(verifier(), Allowlist(store), Roster(store), providers=BOTH)
    for email in ("admin@staff.example", "a@b@staff.example"):
        with pytest.raises(ApiError) as e:
            g.require(hdr(gtoken(email)))
        assert e.value.code in ("NOT_APPROVED", "EMAIL_NOT_VERIFIED")


def test_normalize_email_is_a_full_address_only():
    assert normalize_email("  Snowwhite@Staff.EXAMPLE ") == "snowwhite@staff.example"
    for bad in ("@staff.example", "staff.example", "a@b@c.io", "a b@c.io", "a@io", "a@.io", "a@io.",
                "a@x..io", "TODO-teacher-email-pending", "", None, 5):
        assert normalize_email(bad) is None, bad


# ---------------------------------------------------------------- 3. access removed -> refused

def test_access_removed_by_an_admin_refuses_google_sign_in(pstore, pauthn, journal):  # noqa: F811
    g = Authenticator(verifier(), pauthn.allowlist, pauthn.roster, providers=BOTH)
    assert g.require(hdr(gtoken(TEACHER_EMAIL))).role == "teacher"
    notes = Notes(pstore, roster_ttl=0, recorder=Recorder(journal))
    People(notes).change_access(who(pauthn, ADMIN), "usr_demo_sarah", {"role": "none"})
    with pytest.raises(ApiError) as e:
        g.require(hdr(gtoken(TEACHER_EMAIL)))               # same still-valid Google token
    assert (e.value.http, e.value.code) == (403, "NOT_APPROVED")


def test_taken_off_the_allowlist_file_refuses_google_sign_in(store, gauth):  # noqa: F811
    store.write_json("config/review_allowlist.json", {"users": [{"email": TEACHER_EMAIL, "role": "reviewer"}]})
    gauth.allowlist._at = None
    with pytest.raises(ApiError) as e:
        gauth.require(hdr(gtoken(ADMIN)))
    assert e.value.code == "NOT_APPROVED"


# ---------------------------------------------------------------- provider switch

def test_google_token_refused_while_google_is_not_switched_on(store):  # noqa: F811
    pw_only = Authenticator(verifier(), Allowlist(store), Roster(store), providers=("password",))
    with pytest.raises(ApiError) as e:
        pw_only.require(hdr(gtoken(ADMIN)))
    assert (e.value.http, e.value.code) == (403, "PROVIDER_NOT_ALLOWED")
    assert pw_only.identify(hdr(gtoken(ADMIN)))[1] is None
    assert pw_only.require(hdr(token(ADMIN))).provider == "password"      # password sign-in unchanged
    for other in ("anonymous", "custom", "phone", None):
        t = token(ADMIN, extra={"firebase": {"sign_in_provider": other}})
        with pytest.raises(ApiError):
            Authenticator(verifier(), Allowlist(store), Roster(store), providers=BOTH).require(hdr(t))


def test_providers_setting(monkeypatch):
    monkeypatch.delenv("IDP_PROVIDERS", raising=False)
    assert sign_in_providers() == ("password",)
    assert sign_in_providers("password, Google.com") == BOTH
    assert sign_in_providers("google.com,microsoft.com") == ("google.com",)
    assert sign_in_providers("nonsense") == ("password",)


def test_csp_loosened_only_for_google():
    off = _csp()
    assert "apis.google.com" not in off and "frame-src" not in off and "script-src 'self';" in off
    on = _csp(google_auth_domain="example-project.firebaseapp.com")
    assert "script-src 'self' https://apis.google.com;" in on
    assert "frame-src https://example-project.firebaseapp.com;" in on
    assert "frame-ancestors 'none'" in on and "default-src 'none'" in on


def _serve(store, auth, providers, journal=None):  # noqa: F811
    srv = build_server(store, 0, "local", auth=auth, journal=journal or MemoryJournal(),
                       auth_config={"projectId": "example-project", "apiKey": "k",
                                    "authDomain": "example-project.firebaseapp.com",
                                    "providers": list(providers)})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_auth_config_lists_google_and_sign_in_is_logged_with_provider(store, gauth):  # noqa: F811
    j = MemoryJournal()
    srv, base = _serve(store, gauth, BOTH, j)
    try:
        st, body, h = call(base, "/api/auth-config")
        assert st == 200 and json.loads(body)["providers"] == ["password", "google.com"]
        assert "frame-src https://example-project.firebaseapp.com" in h["Content-Security-Policy"]
        st, body, _ = call(base, "/api/me", tok=gtoken(ADMIN))
        assert st == 200 and json.loads(body)["approved"] is True
        (s,) = [r for r in j.rows() if r["action"] == "sign_in"]
        assert s["details"] == {"sign_in_provider": "google.com"} and s["record_id"] == "GoogleUid123"
        # an unknown Google account: signed in at Google, but refused here (no data, not journaled as sign-in)
        st, body, _ = call(base, "/api/me", tok=gtoken("stranger@gmail.com", uid="StrangerUid9"))
        assert st == 200 and json.loads(body)["approved"] is False
        st, body, _ = call(base, "/api/sessions", tok=gtoken("stranger@gmail.com", uid="StrangerUid9"))
        assert (st, json.loads(body)) == (403, {"error": "NOT_APPROVED"})
        assert len([r for r in j.rows() if r["action"] == "sign_in"]) == 1
    finally:
        srv.shutdown()


def test_auth_config_without_google(store):  # noqa: F811
    a = Authenticator(verifier(), Allowlist(store), Roster(store), providers=("password",))
    srv, base = _serve(store, a, ("password",))
    try:
        st, body, h = call(base, "/api/auth-config")
        assert json.loads(body)["providers"] == ["password"] and "frame-src" not in h["Content-Security-Policy"]
        st, body, _ = call(base, "/api/sessions", tok=gtoken(ADMIN))
        assert (st, json.loads(body)) == (403, {"error": "PROVIDER_NOT_ALLOWED"})
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- Snow White's sign-in email

def test_snow_white_signs_in_with_snowwhite_at_staff_example(tmp_path):
    from review_api.store import LocalStore
    roster = json.loads((ROOT / "app/samples/demo_roster.json").read_text())
    assert roster["teachers"]["stf_0001"]["teacher_name"] == "Snow White"
    assert roster["teachers"]["stf_0001"]["emails"] == ["snowwhite@staff.example"]
    s = LocalStore(tmp_path)
    s.write_json("config/demo_roster.json", roster)
    s.write_json("config/review_allowlist.json", {"users": [{"email": "snowwhite@staff.example", "role": "reviewer"}]})
    g = Authenticator(verifier(), Allowlist(s), Roster(s), providers=BOTH)
    r = g.require(hdr(gtoken("SnowWhite@staff.example")))
    assert r.role == "reviewer" and r.teacher_ids == {"stf_0001"}


# ---------------------------------------------------------------- Ops logging: access changes

def _people(pstore, journal):  # noqa: F811
    return People(Notes(pstore, roster_ttl=0, recorder=Recorder(journal)))


def test_role_change_logs_who_which_account_and_old_and_new_role(pstore, pauthn, journal):  # noqa: F811
    p = _people(pstore, journal)
    admin = who(pauthn, ADMIN)
    p.change_access(admin, "usr_demo_sarah", {"role": "rbt"})
    (j,) = [r for r in journal.rows() if r["action"] == "access_changed"]
    assert j["actor_id"] == admin.uid and j["actor_role"] == "admin" and j["record_id"] == "usr_demo_sarah"
    assert j["details"] == {"fields": "role", "app_role": "rbt", "old_role": "teacher", "new_role": "rbt",
                            "account_email": TEACHER_EMAIL}
    st = pstore.read_json(pstore.list("people/")[-1])
    assert st["by"] == admin.uid and st["change"]["old_role"] == "teacher" and st["change"]["new_role"] == "rbt"


def test_email_change_logs_both_addresses(pstore, pauthn, journal):  # noqa: F811
    p = _people(pstore, journal)
    p.change_access(who(pauthn, ADMIN), "usr_demo_sarah", {"email": "Sarah.New@Example.org", "role": "admin"})
    (j,) = [r for r in journal.rows() if r["action"] == "access_changed"]
    assert j["details"]["old_email"] == TEACHER_EMAIL and j["details"]["new_email"] == "sarah.new@example.org"
    assert (j["details"]["old_role"], j["details"]["new_role"]) == ("teacher", "admin")
    assert set(j["details"]["fields"].split(".")) == {"email", "role"}
    st = pstore.read_json(pstore.list("people/")[-1])
    assert (st["change"]["old_email"], st["change"]["new_email"]) == (TEACHER_EMAIL, "sarah.new@example.org")


def test_other_profile_edits_stay_without_values(pstore, pauthn, journal):  # noqa: F811
    p = _people(pstore, journal)
    p.save_me(who(pauthn, TEACHER_EMAIL), {"phone": "404 555 0199", "display_name": "Sadie J."})
    (j,) = [r for r in journal.rows() if r["action"] == "profile_updated"]
    blob = json.dumps(j)
    assert "@" not in blob and "555" not in blob and "Sadie" not in blob


def test_addresses_allowed_on_access_changes_only():
    ok = make_entry(action="access_changed", actor_id="uid1", actor_role="admin", record_id="usr_x",
                    details={"old_email": "a@example.org", "new_email": "b@example.org"})
    assert ok["details"]["new_email"] == "b@example.org"
    for action in ("profile_updated", "sign_in", "report_downloaded", "parent_email_sent", "session_viewed"):
        with pytest.raises(JournalError):
            make_entry(action=action, actor_id="uid1", actor_role="admin", record_id="usr_x",
                       details={"account_email": "a@example.org"})
    for bad in ("Not An Email", "A@Example.org", "a@b", "x" * 300 + "@example.org"):
        with pytest.raises(JournalError):
            make_entry(action="access_changed", actor_id="uid1", actor_role="admin", record_id="usr_x",
                       details={"new_email": bad})


# ---------------------------------------------------------------- Ops logging: CSV downloads like PDFs

def test_csv_download_gets_an_audit_record_like_a_pdf(site, world, jrnl):  # noqa: F811
    st, data, _ = call(site, "/api/admin/reports/activity.csv?by=person", tok=tok(ADMIN))
    assert st == 200
    j = jrnl.rows()[-1]
    assert j["action"] == "report_downloaded" and j["details"]["report_bytes"] == len(data)
    (name,) = world.list("audit/_reports/")
    assert "_report_downloaded_" in name
    rec = world.read_json(name)
    assert rec["event"] == "report_downloaded" and rec["report"] == "activity_by_person"
    assert rec["actor_email"] == ADMIN and rec["actor_role"] == "admin" and rec["sign_in_provider"] == "password"
    assert rec["report_sha256"] == hashlib.sha256(data).hexdigest() and rec["report_bytes"] == len(data)
    assert isinstance(rec["row_count"], int) and rec["period_from"] <= rec["period_to"]
    call(site, "/api/admin/reports/activity.csv?by=student", tok=tok(ADMIN))
    assert len(world.list("audit/_reports/")) == 2                # one per download
    # the report records never look like a session to the session readers
    notes = Notes(world, roster_ttl=0)
    assert "_reports" not in notes.session_student_links() and "_reports" not in notes.classroom_changes()


# ---------------------------------------------------------------- 12-month parent_email/ rule

def _lm():
    spec = importlib.util.spec_from_file_location("lifecycle_merge", ROOT / "infra/lifecycle_merge.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


LIVE_BEFORE = {"lifecycle_config": {"rule": [
    {"action": {"type": "Delete"}, "condition": {"age": 7, "matchesPrefix": ["rec/"]}},
    {"action": {"type": "Delete"}, "condition": {"age": 30, "matchesPrefix": ["transcripts/", "drafts/", "approvals/", "audit/", "materials/"]}},
    {"action": {"type": "Delete"}, "condition": {"daysSinceCustomTime": 30, "matchesPrefix": ["transcripts/", "drafts/", "approvals/", "audit/"]}},
    {"action": {"type": "Delete"}, "condition": {"age": 1, "matchesPrefix": ["work/"]}}]}}


def test_lifecycle_merge_keeps_every_rule_and_adds_one():
    m = _lm()
    before = m.rules_of(LIVE_BEFORE)
    after = m.merge(before, "parent_email/", 365)
    assert after[:4] == before and len(after) == 5
    assert after[4] == {"action": {"type": "Delete"}, "condition": {"age": 365, "matchesPrefix": ["parent_email/"]}}
    assert m.merge(after, "parent_email/", 365) == after                 # re-running adds nothing
    assert m.merge([], "parent_email/", 365) == [after[4]]
    # a different parent_email rule (e.g. 30 days) is kept as it is; the 365-day one is added beside it
    other = [{"action": {"type": "Delete"}, "condition": {"age": 30, "matchesPrefix": ["parent_email/"]}}]
    assert m.merge(other, "parent_email/", 365)[0] == other[0]


def test_setup_scripts_carry_the_rule():
    gaps = (ROOT / "infra/setup_demo_gaps.sh").read_text()
    assert "lifecycle_merge.py\" merge" in gaps and "lifecycle_merge.py\" show" in gaps
    assert "PARENT_EMAIL_RETENTION_DAYS:=365" in (ROOT / "infra/config.sh").read_text()
    setup = (ROOT / "infra/setup.sh").read_text()
    assert '"age": ${PARENT_EMAIL_RETENTION_DAYS}, "matchesPrefix": ["parent_email/"]' in setup
    for rule in ('"age": ${AUDIO_RETENTION_DAYS}, "matchesPrefix": ["rec/"]', '"age": 1, "matchesPrefix": ["work/"]'):
        assert rule in setup
