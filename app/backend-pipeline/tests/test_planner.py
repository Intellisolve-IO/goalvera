"""Weekly lesson planner: weeks, the anchored current week, who may
save, the 8-word transcript-quote check, "the student" storage and the journal.

  python -m pytest app/backend-pipeline/tests/test_planner.py -q
"""
from __future__ import annotations

import datetime as dt
import json
import threading

import pytest

from test_hosted_auth import ADMIN, TEACHER_EMAIL, call, token, verifier  # noqa: F401
from review_api.auth import Allowlist, Authenticator, Roster
from review_api.journal import MemoryJournal, Recorder
from review_api.notes import ApiError, Notes
from review_api.planner import (Planner, QuoteRefused, quoted_run, suggest, three_weeks, today,
                                week_of)
from review_api.server import build_server
from review_api.store import LocalStore
from seed_planner_demo import seed_planner

TODAY = "2026-10-07"                  # a Wednesday: this week = Mon 5 Oct
THIS, LAST, NEXT = "2026-10-05", "2026-09-28", "2026-10-12"
RBT_EMAIL, ART_TEACHER = "demo-rbt@example.org", "art-teacher@example.org"
QUOTE = "Good morning, everyone. Marcus, let's start with our math warm-up."   # transcript, 10 words


@pytest.fixture(autouse=True)
def pinned_today(monkeypatch):
    monkeypatch.setenv("PLANNER_TODAY", TODAY)
    monkeypatch.delenv("K_SERVICE", raising=False)


@pytest.fixture()
def pstore(tmp_path):
    seed_planner(str(tmp_path))
    s = LocalStore(tmp_path)
    r = s.read_json("config/demo_roster.json")
    r["teachers"]["usr_demo_sarah"]["emails"] = [TEACHER_EMAIL]
    r["class_codes"]["Art 9Z"] = {"teacher_id": "usr_art", "teacher_name": "Jordan Rivera", "iep_goals": []}
    r["teachers"]["usr_art"] = {"teacher_name": "Jordan Rivera", "emails": [ART_TEACHER]}
    r["rbts"]["rbt_demo_rm12"]["emails"] = [RBT_EMAIL]
    s.write_json("config/demo_roster.json", r)
    s.write_json("config/review_allowlist.json", {"users": [
        {"email": ADMIN, "role": "admin"}, {"email": TEACHER_EMAIL, "role": "teacher"},
        {"email": ART_TEACHER, "role": "teacher"}, {"email": RBT_EMAIL, "role": "rbt"}]})
    return s


@pytest.fixture()
def authn(pstore):
    return Authenticator(verifier(), Allowlist(pstore), Roster(pstore))


@pytest.fixture()
def journal():
    return MemoryJournal()


@pytest.fixture()
def planner(pstore, journal):
    return Planner(Notes(pstore, roster_ttl=0, recorder=Recorder(journal)))


@pytest.fixture()
def hosted(pstore, authn, journal, tmp_path_factory):
    static = tmp_path_factory.mktemp("dist")
    (static / "index.html").write_text("<!doctype html><title>review</title>")
    srv = build_server(pstore, 0, "local", auth=authn, static_dir=str(static), journal=journal,
                       auth_config={"projectId": "example-project", "apiKey": "k"})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def who(authn, email):
    return authn.require({"Authorization": "Bearer " + token(email, uid="uid" + email.split("@")[0].replace("-", ""))})


def get(base, email, query="class_code=RM12"):
    st, body, _ = call(base, f"/api/planner?{query}", tok=token(email, uid="uid" + email.split("@")[0].replace("-", "")))
    return st, json.loads(body)


def post(base, email, what, body):
    st, out, _ = call(base, f"/api/planner/{what}", method="POST", body=body,
                      tok=token(email, uid="uid" + email.split("@")[0].replace("-", "")))
    return st, json.loads(out)


# ---------------------------------------------------------------- weeks

def test_week_of_is_monday():
    assert week_of(dt.date(2026, 10, 7)) == dt.date(2026, 10, 5)
    assert week_of(dt.date(2026, 10, 5)) == dt.date(2026, 10, 5)
    assert week_of(dt.date(2026, 10, 11)) == dt.date(2026, 10, 5)      # Sunday belongs to the week before


def test_this_week_is_anchored_and_past_defaults_to_last_week():
    w = three_weeks()
    assert {k: v.isoformat() for k, v in w.items()} == {"past": LAST, "current": THIS, "next": NEXT}
    for past in ("2026-09-21", "2026-09-14", "2026-01-05"):
        w = three_weeks(past)
        assert (w["past"].isoformat(), w["current"].isoformat(), w["next"].isoformat()) == (past, THIS, NEXT)


@pytest.mark.parametrize("past", [THIS, NEXT, "2026-10-06", "2026-09-29", "28-09-2026", "2025-09-29", "x"])
def test_past_slot_never_reaches_this_week_or_beyond_a_year(past):
    with pytest.raises(ApiError) as e:
        three_weeks(past)
    assert e.value.code == "BAD_WEEK"


def test_today_uses_school_timezone_and_ignores_pin_on_cloud_run(monkeypatch):
    assert today() == dt.date(2026, 10, 7)
    monkeypatch.setenv("K_SERVICE", "review")
    from review_api.planner import SCHOOL_TZ
    assert today() == dt.datetime.now(SCHOOL_TZ).date()


# ---------------------------------------------------------------- the screen

def test_view_three_weeks_with_plan_first_and_students(planner):
    v = planner.view("usr_demo_sarah", "RM12")
    assert (v["this_week"], v["last_week"]) == (THIS, LAST)
    assert [(w["slot"], w["week"]) for w in v["weeks"]] == [("past", LAST), ("current", THIS), ("next", NEXT)]
    past, cur, nxt = v["weeks"]
    assert past["plan"]["status"] == "approved" and past["plan"]["goals"] and past["plan"]["objectives"]
    assert "Marcus leading the warm-up" in cur["plan"]["plan"]          # name put back on read
    row = past["students"][0]
    assert row["name"] == "Marcus A." and [g["goal_id"] for g in row["iep_goals"]] == ["g1", "g2"]
    assert len(row["sessions"]) == 3 and sum(s["approved"] for s in row["sessions"]) == 2
    assert row["suggestion"]["iep_check"][1] == {"goal_id": "g2", "status": "evidence_seen",
                                                 "note": "Demonstrated in 2 of 2 approved sessions."}
    assert row["note"]["note"].startswith("Marcus asked for headphones")
    assert nxt["students"][0]["sessions"] == [] and nxt["students"][0]["suggestion"] is None
    assert v["can_edit_plan"] and row["can_edit"]


def test_arrows_move_only_the_past_slot(planner):
    a = planner.view(None, "RM12")
    b = planner.view(None, "RM12", "2026-09-14")
    assert [w["week"] for w in b["weeks"]] == ["2026-09-14", THIS, NEXT]
    assert a["weeks"][1:] == b["weeks"][1:]                            # this week + next week unchanged
    sug = b["weeks"][0]["students"][0]["suggestion"]
    assert [r["status"] for r in sug["iep_check"]] == ["not_seen", "not_seen"]


def test_suggestion_has_no_transcript_text(planner, pstore):
    v = planner.view(None, "RM12")
    blob = json.dumps([w["students"] for w in v["weeks"]])
    assert "headphones, please" not in blob and "six out of seven" not in blob.lower()
    evidence = [ev["evidence_quote"] for n in pstore.list("approvals/")
                for ev in pstore.read_json(n)["analysis"]["goal_evaluations"] if ev["evidence_quote"]]
    for sug in (r["suggestion"] for w in v["weeks"] for r in w["students"] if r["suggestion"]):
        assert not any(q in json.dumps(sug) for q in evidence)


def test_suggestion_counts():
    st = {"first_name": "Marcus", "iep_goals": [{"goal_id": "g1"}, {"goal_id": "g2"}]}
    a = lambda g1, g2: {"analysis": {"goal_evaluations": [{"goal_id": "g1", "status": g1},  # noqa: E731
                                                          {"goal_id": "g2", "status": g2}]}}
    s = suggest(st, [a("Struggled", "Not Observed"), a("Demonstrated", "Not Observed")])
    assert [(r["status"], r["note"]) for r in s["iep_check"]] == [
        ("evidence_seen", "Demonstrated in 1 of 2 approved sessions."),
        ("not_enough", "Not observed in 2 approved sessions.")]
    assert s["summary"].startswith("2 approved sessions this week. Marcus showed progress on 1 of 2")


# ---------------------------------------------------------------- saving

def test_teacher_saves_summary_note_and_plan_stored_as_the_student(planner, pstore, authn):
    sarah = who(authn, TEACHER_EMAIL)
    out = planner.save_summary(sarah, {
        "student_id": "stu_101", "week": LAST, "approve": True,
        "summary": "Marcus used the break card twice and finished both probes.",
        "iep_check": [{"goal_id": "g1", "status": "evidence_seen", "note": "Marcus asked for a break"},
                      {"goal_id": "g2", "status": "evidence_seen", "note": "86% on the probe"}]})
    assert out["summary"].startswith("Marcus used") and out["status"] == "approved"
    names = pstore.list(f"planner/students/stu_101/{LAST}/summary/")
    assert len(names) == 1
    raw = json.dumps(pstore.read_json(names[0]))
    assert "Marcus" not in raw and "the student used the break card" in raw
    assert pstore.read_json(names[0])["source_sessions"] == [
        "rec_laptop_demo_20260930_093000_RM12", "rec_laptop_demo_20261001_113000_RM12"]
    planner.save_note(sarah, {"student_id": "stu_101", "week": THIS, "note": "Probe on Thursday."})
    planner.save_plan(sarah, {"class_code": "rm12", "week": THIS, "goals": ["New goal"], "plan": "Updated"})
    v = planner.view(sarah, "RM12")
    assert v["weeks"][0]["students"][0]["summary"]["summary"].startswith("Marcus used")
    assert v["weeks"][1]["students"][0]["note"]["note"] == "Probe on Thursday."
    assert v["weeks"][1]["plan"]["goals"] == ["New goal"] and v["weeks"][1]["plan_versions"] == 2


def test_saves_never_overwrite(planner, pstore):
    for i in range(3):
        planner.save_note("usr_demo_sarah", {"student_id": "stu_101", "week": THIS, "note": f"v{i}"})
    names = pstore.list(f"planner/students/stu_101/{THIS}/note/")
    assert [pstore.read_json(n)["note"] for n in names] == ["v0", "v1", "v2"]
    assert planner.view(None, "RM12")["weeks"][1]["students"][0]["note_versions"] == 3


def test_eight_word_transcript_quote_refused(planner, pstore):
    tr = [pstore.read_json(n) for n in pstore.list("transcripts/")]
    assert quoted_run(QUOTE, tr) is not None
    assert quoted_run("Good morning everyone, Marcus did his warm-up today.", tr) is None   # 7 words max
    for what, body in (("note", {"note": f"He said: {QUOTE}"}),
                       ("summary", {"summary": QUOTE.upper()}),
                       ("summary", {"summary": "ok", "iep_check": [{"goal_id": "g1", "status": "not_seen",
                                                                    "note": QUOTE}]})):
        with pytest.raises(QuoteRefused) as e:
            getattr(planner, f"save_{what}")("usr_demo_sarah", {"student_id": "stu_101", "week": LAST, **body})
        assert e.value.code == "QUOTES_TRANSCRIPT" and len(e.value.run.split()) == 8
    with pytest.raises(QuoteRefused):
        planner.save_plan("usr_demo_sarah", {"class_code": "RM12", "week": LAST, "plan": QUOTE})
    # a week with no sessions has no transcripts to quote
    planner.save_plan("usr_demo_sarah", {"class_code": "RM12", "week": NEXT, "plan": QUOTE})
    assert len(pstore.list(f"planner/students/stu_101/{LAST}/note/")) == 1     # only the seeded note


def test_quote_check_lapses_once_the_transcript_is_gone(planner, pstore, tmp_path):
    for n in pstore.list("transcripts/"):
        (tmp_path / n).unlink()                                        # 30-day auto-delete
    planner.save_note("usr_demo_sarah", {"student_id": "stu_101", "week": LAST, "note": QUOTE})


@pytest.mark.parametrize("body,code", [
    ({"week": "2026-10-06"}, "BAD_WEEK"),
    ({"week": "2026-10-19"}, "BAD_WEEK"),                              # beyond next week
    ({"week": LAST, "iep_check": [{"goal_id": "g9", "status": "evidence_seen"}]}, "BAD_IEP_CHECK"),
    ({"week": LAST, "iep_check": [{"goal_id": "g1", "status": "great"}]}, "BAD_IEP_CHECK"),
    ({"week": LAST, "summary": ""}, "BAD_SUMMARY"),
    ({"week": LAST, "summary": "x" * 6001}, "BAD_SUMMARY"),
    ({"week": LAST, "student_id": "Marcus"}, "BAD_STUDENT"),
])
def test_bad_summary_input(planner, body, code):
    with pytest.raises(ApiError) as e:
        planner.save_summary("usr_demo_sarah", {"student_id": "stu_101", "summary": "fine", **body})
    assert e.value.code == code


def test_only_own_teacher_or_admin_may_save(planner, authn):
    art, admin, rbt = (who(authn, e) for e in (ART_TEACHER, ADMIN, RBT_EMAIL))
    for actor, code in ((art, "NOT_YOUR_STUDENT"), (rbt, "NOT_YOUR_STUDENT"), ("usr_art", "NOT_YOUR_STUDENT")):
        with pytest.raises(ApiError) as e:
            planner.save_note(actor, {"student_id": "stu_101", "week": THIS, "note": "x"})
        assert e.value.code == code
    with pytest.raises(ApiError) as e:
        planner.save_plan(art, {"class_code": "RM12", "week": THIS, "plan": "x"})
    assert e.value.code == "NOT_YOUR_CLASSROOM"
    planner.save_note(admin, {"student_id": "stu_101", "week": THIS, "note": "admin note"})
    assert planner.view(art, "RM12")["can_edit_plan"] is False
    assert planner.view(rbt, "RM12")["weeks"][1]["students"][0]["can_edit"] is False


# ---------------------------------------------------------------- over HTTP (hosted)

def test_http_view_and_rbt_read_only(hosted, journal):
    st, v = get(hosted, RBT_EMAIL)
    assert st == 200 and [w["week"] for w in v["weeks"]] == [LAST, THIS, NEXT]
    assert v["can_edit_plan"] is False and v["weeks"][0]["students"][0]["iep_goals"]
    st, out = post(hosted, RBT_EMAIL, "note", {"student_id": "stu_101", "week": THIS, "note": "x"})
    assert (st, out["error"]) == (403, "READ_ONLY_ROLE")
    acts = [r["action"] for r in journal.rows()]
    assert "planner_viewed" in acts and "iep_viewed" in acts and "request_refused" in acts


def test_http_past_navigation_and_bad_week(hosted):
    st, v = get(hosted, TEACHER_EMAIL, "class_code=RM12&past=2026-09-21")
    assert st == 200 and [w["week"] for w in v["weeks"]] == ["2026-09-21", THIS, NEXT]
    assert get(hosted, TEACHER_EMAIL, f"class_code=RM12&past={THIS}") == (400, {"error": "BAD_WEEK"})
    assert get(hosted, TEACHER_EMAIL, "class_code=Nope")[0] == 404


def test_http_teacher_saves_and_quote_refused(hosted, journal):
    st, out = post(hosted, TEACHER_EMAIL, "summary", {
        "student_id": "stu_101", "week": THIS, "summary": "Marcus had a steady week.",
        "iep_check": [{"goal_id": "g1", "status": "not_enough", "note": ""}]})
    assert st == 200 and out["summary"] == "Marcus had a steady week."
    st, out = post(hosted, TEACHER_EMAIL, "note", {"student_id": "stu_101", "week": THIS, "note": QUOTE})
    assert st == 422 and out["error"] == "QUOTES_TRANSCRIPT" and out["words"] == 8
    rows = [r for r in journal.rows() if r["action"].startswith("planner_") and r["action"] != "planner_viewed"]
    assert [r["action"] for r in rows] == ["planner_summary_saved"]
    assert rows[0]["student_id"] == "stu_101" and "Marcus" not in json.dumps(rows)
    details = rows[0]["details"]
    assert (json.loads(details) if isinstance(details, str) else details) == {"week": THIS, "approved": False}


def test_laptop_mode_uses_reviewer_header(pstore):
    srv = build_server(pstore, 0, "local")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        st, body, _ = call(base, "/api/planner?class_code=RM12", headers={"X-Reviewer-Id": "usr_demo_sarah"})
        assert st == 200 and json.loads(body)["can_edit_plan"] is True
        st, body, _ = call(base, "/api/planner?class_code=RM12")
        assert json.loads(body)["can_edit_plan"] is False
        st, _, _ = call(base, "/api/planner/note", method="POST", body={"student_id": "stu_101", "week": THIS,
                                                                       "note": "x"})
        assert st == 401
    finally:
        srv.shutdown()


def test_default_classroom_is_own_with_a_student(planner, authn):
    assert planner.view(who(authn, TEACHER_EMAIL), None)["class_code"] == "RM12"
    assert planner.view("usr_demo_sarah", "")["class_code"] == "RM12"
    # every classroom list opens on the teacher's own (default) classroom, even one
    # with no student yet (Art 9Z); before, the planner skipped to the first classroom with a student
    v = planner.view(who(authn, ART_TEACHER), None)
    assert v["class_code"] == "Art 9Z"
    assert planner.view(who(authn, ADMIN), None)["class_code"] == "RM12"     # admins: first with a student
    assert {"class_code": "Art 9Z", "students": 0} in v["classrooms"]
    assert planner.view(who(authn, ART_TEACHER), "Art 9Z")["weeks"][1]["students"] == []
