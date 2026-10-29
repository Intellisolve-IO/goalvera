"""Weekly parent email (David + Ops, Oct 10 2026; tasks#106).

  * recipients = only that student's guardians with "receives reports" on (never all contacts);
    non-custodial contacts in the sample data are excluded and shown as excluded;
  * manual only: one teacher click per email, the exact previewed text; nothing sends by itself;
  * HARD RULE: with the demo redirect on (the default) every email goes to the teacher's own
    staff.example address with a "Demo: would have gone to ..." banner; no other address, ever;
  * approved report text only, first names only; every send is logged (record + journal).

  python -m pytest app/backend-pipeline/tests/test_parent_email.py -q
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from test_hosted_auth import call  # noqa: F401  (sets sys.path)
from test_materials import ADMIN, BELLE_T, DOC, RBT, ROSTER, SNOW, SNOW_T, seed  # noqa: F401
from test_planner import journal  # noqa: F401
from review_api import mailer
from review_api.journal import MemoryJournal, Recorder
from review_api.notes import ApiError, Notes
from review_api.parent_email import REDIRECT_DOMAIN, ParentEmail, mask_addr
from review_api.planner import Planner, today, week_of
from review_api.store import LocalStore

REPO = Path(__file__).resolve().parents[3]
GUARDIANS = json.loads((REPO / "app/samples/contacts/demo_guardians.json").read_text())
SNEEZY, HAPPY = "stu_0006", "stu_0003"
ROOM104 = ["stu_0001", "stu_0002", "stu_0003", "stu_0004", "stu_0005", "stu_0006", "stu_0007",
           "stu_0041", "stu_0042", "stu_0043"]
WEEK = week_of(today()).isoformat()
TEXT = "Hello! This week {fn} practiced reading short stories and asked great questions."
for _r in (ADMIN, SNOW_T, BELLE_T, RBT):
    _r.provider = "password"


@pytest.fixture()
def env(tmp_path, journal, monkeypatch):  # noqa: F811
    monkeypatch.delenv("PARENT_EMAIL_REDIRECT_DEFAULT", raising=False)
    monkeypatch.delenv("MATERIALS_MAIL_DOMAINS", raising=False)
    store = LocalStore(tmp_path)
    seed(store)
    notes = Notes(store, recorder=Recorder(journal))
    planner = Planner(notes)
    out = mailer.Outbox()
    return type("E", (), {"store": store, "notes": notes, "journal": journal, "planner": planner, "outbox": out,
                          "pe": ParentEmail(notes, planner, sender=out)})


def approve(env, sid=DOC, text=None, actor=SNOW_T, approve_=True):
    fn = ROSTER["students"][sid]["first_name"]
    env.planner.save_summary(actor, {"student_id": sid, "week": WEEK, "summary": f"{fn} had a good week.",
                                     "parent_email": (text or TEXT).format(fn=fn), "approve": approve_})


def send(env, sid=DOC, actor=SNOW_T):
    p = env.pe.preview(actor, sid, WEEK)
    return env.pe.send(actor, sid, {"week": WEEK, "preview_sha256": p["preview_sha256"]})


def all_addresses(msg) -> list[str]:
    return [a.lower() for h in ("To", "Cc", "Bcc", "Reply-To", "From") for a in re.findall(r"[\w.+-]+@[\w.-]+", str(msg[h] or ""))]


# ---------------------------------------------------------------- sample contacts

def test_guardians_file_is_built_fictional_and_separate_from_the_roster():
    r = subprocess.run([sys.executable, str(REPO / "app/tools/build_demo_roster.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    emails = [c["email"] for cs in GUARDIANS["students"].values() for c in cs if c["email"]]
    assert emails and all(e.rsplit("@", 1)[1] in ("example.com", "example.org") for e in emails)
    assert "guardian" not in json.dumps(ROSTER).lower() and "example.org" not in json.dumps(ROSTER)
    allowed = {"contact_id", "first_name", "relationship", "email", "receives_reports"}
    assert all(set(c) == allowed for cs in GUARDIANS["students"].values() for c in cs)       # no last names etc.
    nc = [c for cs in GUARDIANS["students"].values() for c in cs if c["relationship"] == "non-custodial parent"]
    assert len(nc) == 2 and all(c["email"] and c["receives_reports"] is False for c in nc)


# ---------------------------------------------------------------- recipients (Ops rule 1)

def test_recipients_are_only_flagged_guardians_and_excluded_are_shown(env):
    approve(env, SNEEZY)
    p = env.pe.preview(SNOW_T, SNEEZY, WEEK)
    assert [e["guardian_first_name"] for e in p["emails"]] == ["Wren"]
    assert {(x["first_name"], x["reason"]) for x in p["excluded"]} == {
        ("Rory", "not set to receive reports"), ("Jamal", "no email on file")}
    assert p["can_send"] is True and p["manual_only"] is True
    approve(env, HAPPY)
    p = env.pe.preview(SNOW_T, HAPPY, WEEK)
    assert sorted(e["guardian_first_name"] for e in p["emails"]) == ["June", "Oren"]
    assert "Linden" in [x["first_name"] for x in p["excluded"]]


def test_never_all_contacts(env):
    for sid in ROOM104:
        approve(env, sid)
        p = env.pe.preview(SNOW_T, sid, WEEK)
        flagged = {c["contact_id"] for c in GUARDIANS["students"][sid] if c["receives_reports"]}
        assert {e["contact_id"] for e in p["emails"]} == flagged
        assert len(p["emails"]) + len(p["excluded"]) == len(GUARDIANS["students"][sid])


# ---------------------------------------------------------------- redirect (HARD RULE)

def test_redirect_is_on_by_default_and_sends_to_the_teacher_with_a_banner(env):
    approve(env)
    p = env.pe.preview(SNOW_T, DOC, WEEK)
    assert p["redirect"] is True and p["redirect_to"] == "snowwhite@staff.example"
    assert "not to parents" in p["redirect_banner"]
    r = send(env)
    assert r["status"] == "sent" and r["sent"] == 2 and r["redirected"] is True
    assert len(env.outbox.sent) == 2
    for m, who in zip(env.outbox.sent, ("Marigold", "Elliot")):
        assert str(m["To"]) == "snowwhite@staff.example"
        assert "notifications@staff.example" in str(m["From"])
        body = m.get_content()
        assert body.startswith(f"Demo: would have gone to {who} at {who[0].lower()}")
        assert "@example.org" in body.splitlines()[0] and ".dwarf" not in body                     # masked
        assert TEXT.format(fn="Doc") in body and "Dwarf" not in body and DOC not in body
        assert not m.is_multipart()                                                          # no attachment


def test_no_external_address_is_ever_used_with_redirect_on(env, monkeypatch):
    # even if the domain allowlist were widened to the guardians' domains
    monkeypatch.setenv("MATERIALS_MAIL_DOMAINS", "staff.example,example.org,example.com")
    for sid in ROOM104:
        approve(env, sid)
        send(env, sid, SNOW_T)
        send(env, sid, ADMIN)
    assert len(env.outbox.sent) >= 20
    for m in env.outbox.sent:
        for a in all_addresses(m):
            assert a.endswith("@" + REDIRECT_DOMAIN), a
    # a teacher address on file outside staff.example is refused; nothing leaves
    roster = json.loads(json.dumps(ROSTER))
    roster["teachers"][SNOW]["emails"] = ["snow@example.org"]
    env.store.write_json("config/demo_roster.json", roster)
    notes = Notes(env.store, recorder=env.notes.recorder)                   # a fresh read of the roster
    env.pe = ParentEmail(notes, Planner(notes), sender=env.outbox)
    before = len(env.outbox.sent)
    r = send(env, DOC, SNOW_T)
    assert r["status"] == "redirect_not_internal" and r["sent"] == 0 and len(env.outbox.sent) == before


def test_redirect_off_still_cannot_reach_fictional_parents(env):
    env.pe.set_redirect(ADMIN, {"redirect": False})
    approve(env)
    p = env.pe.preview(SNOW_T, DOC, WEEK)
    assert p["redirect"] is False and p["emails"][0]["to"].endswith("@example.org")
    r = send(env)
    assert r["status"] == "recipient_domain_not_allowed" and env.outbox.sent == []
    (j,) = [x for x in env.journal.rows() if x["action"] == "parent_email_send_failed"]
    assert j["details"]["redirected"] is False and j["details"]["recipients"] == 0


def test_redirect_setting_is_admin_only_and_logged(env):
    for who in (SNOW_T, RBT, "stf_0001"):
        with pytest.raises(ApiError) as e:
            env.pe.set_redirect(who, {"redirect": False})
        assert e.value.code == "ADMIN_ONLY"
    v = env.pe.set_redirect(ADMIN, {"redirect": False})
    assert v["redirect"] is False
    assert env.pe.set_redirect(ADMIN, {"redirect": True})["redirect"] is True
    rows = [x for x in env.journal.rows() if x["action"] == "settings_changed"]
    assert [x["details"] for x in rows] == [{"setting": "parent_email_redirect", "enabled": False},
                                            {"setting": "parent_email_redirect", "enabled": True}]


# ---------------------------------------------------------------- manual only (Ops rule 2) + content

def test_needs_an_approved_report_and_the_exact_preview(env):
    p = env.pe.preview(SNOW_T, DOC, WEEK)
    assert p["problem"] == "NOT_APPROVED" and p["can_send"] is False
    approve(env, approve_=False)                                               # a draft is not enough
    assert env.pe.preview(SNOW_T, DOC, WEEK)["problem"] == "NOT_APPROVED"
    approve(env)
    p = env.pe.preview(SNOW_T, DOC, WEEK)
    with pytest.raises(ApiError) as e:
        env.pe.send(SNOW_T, DOC, {"week": WEEK})                               # no click on a preview
    assert e.value.code == "PREVIEW_CHANGED"
    approve(env, text="Hi! {fn} built a tall tower this week.")                # text changed after the preview
    with pytest.raises(ApiError) as e:
        env.pe.send(SNOW_T, DOC, {"week": WEEK, "preview_sha256": p["preview_sha256"]})
    assert e.value.code == "PREVIEW_CHANGED" and env.outbox.sent == []


@pytest.mark.parametrize("actor,code", [(BELLE_T, "NOT_YOUR_STUDENT"), (RBT, "READ_ONLY_ROLE")])
def test_only_the_students_teachers_and_admins(env, actor, code):
    approve(env)
    with pytest.raises(ApiError) as e:
        env.pe.send(actor, DOC, {"week": WEEK, "preview_sha256": "x"})
    assert e.value.code == code


def test_last_names_are_refused(env):
    approve(env, text="{fn} Dwarf had a great week.")
    p = env.pe.preview(SNOW_T, DOC, WEEK)
    assert p["problem"] == "EMAIL_HAS_LAST_NAME" and p["can_send"] is False


def test_every_send_is_logged_ids_only(env):
    approve(env)
    send(env)
    (j,) = [x for x in env.journal.rows() if x["action"] == "parent_email_sent"]
    assert j["details"]["recipients"] == 2 and j["details"]["redirected"] is True and j["details"]["excluded"] == 1
    assert "Marigold" not in json.dumps(j) and "@" not in json.dumps(j["details"])
    (n,) = env.store.list(f"parent_email/sends/{DOC}/{WEEK}/")
    rec = env.store.read_json(n)
    assert rec["status"] == "sent" and rec["contact_ids"] == ["con_0001", "con_0002"] and "@" not in json.dumps(rec).replace(
        "notifications@staff.example", "")
    assert env.pe.preview(SNOW_T, DOC, WEEK)["last_sent"]["recipients"] == 2


def test_manual_only_no_automatic_sending_anywhere():
    """The only caller of ParentEmail.send is the POST route a teacher's click hits; nothing else in the
    service, the pipeline or infra (no scheduler, cron or job) sends a parent email."""
    hits = []
    for p in list((REPO / "app").rglob("*.py")) + list((REPO / "infra").rglob("*")):
        if not p.is_file() or "tests" in p.parts or p.suffix not in (".py", ".sh", ".yaml", ".yml", ".json", ""):
            continue
        t = p.read_text(encoding="utf-8", errors="ignore")
        hits += [(p.name, m.group(0)) for m in re.finditer(r"parent_email\.send\(|\.send\(who, pe\.group", t)]
        assert not re.search(r"(scheduler|cron).*parent_email|parent_email.*(scheduler|cron)", t, re.I), p
    assert hits == [("server.py", "parent_email.send(")]
    src = (REPO / "app/backend-pipeline/review_api/server.py").read_text()
    assert re.search(r'if method == "POST" and pe\.group\(2\):\s+return self\._send\(200, parent_email\.send\(', src)


def test_mask_addr():
    assert mask_addr("marigold.dwarf40@example.org") == "m***************@example.org"


def test_http_laptop_demo(tmp_path, monkeypatch):
    monkeypatch.setenv("MATERIALS_MAIL", "outbox")
    store = LocalStore(tmp_path)
    seed(store)
    srv = build = None
    from review_api.server import build_server
    srv = build_server(store, 0, "local", journal=MemoryJournal())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        h = {"X-Reviewer-Id": SNOW}
        st, body, _ = call(base, "/api/planner/summary", method="POST", headers=h,
                           body={"student_id": DOC, "week": WEEK, "summary": "Doc had a good week.",
                                 "parent_email": TEXT.format(fn="Doc"), "approve": True})
        assert st == 200, body
        st, body, _ = call(base, f"/api/students/{DOC}/parent_email?week={WEEK}", headers=h)
        p = json.loads(body)
        assert st == 200 and p["redirect"] is True and len(p["emails"]) == 2
        st, body, _ = call(base, f"/api/students/{DOC}/parent_email/send", method="POST", headers=h,
                           body={"week": WEEK, "preview_sha256": p["preview_sha256"]})
        assert st == 200 and json.loads(body)["status"] == "sent"
        st, body, _ = call(base, "/api/admin/parent_email", headers=h)
        assert st == 403
    finally:
        srv.shutdown()
    del build
