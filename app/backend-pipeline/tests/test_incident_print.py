"""Incident numbering + one incident's Download PDF (print view).

  * incidents are numbered in order within each recording, earliest first:
    "Incident 1 · 0:03"; the same number in the session PDF and the print view
  * the print view is only for the people who may export the session PDF
    (its teacher, the student's teacher, an admin; never an RBT or another teacher).
    Approved: the approved notes. Not approved yet: the stored draft, marked DRAFT
    (title / file name prefix, "Draft, not yet approved" line; watermark in the UI)
  * IE8-IE10: first name + last initial only; never the school id, full last name or
    date of birth, in the report or in its title (= the PDF's file name)
  * every click is logged: bucket audit trail + admin journal + Cloud Logging, with
    who / when / recording / incident number, internal ids only
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import pytest

from test_hosted_auth import ADMIN, OTHER, RID, TEACHER_EMAIL, call, hdr, token  # noqa: F401
from test_hosted_auth import authn, store  # noqa: F401  (pytest fixtures)
from test_journal import DOB, LAST_NAME, SCHOOL_ID, FailingJournal, serve
from test_roles import RBT_EMAIL, world  # noqa: F401  (pytest fixture)
from test_review_pdf import pdf_text
from review_api.incident_print import (FOOTER, PrintBlocked, build_print, check_print, clock,
                                       incident_label, incident_numbers)
from review_api.journal import MemoryJournal, Recorder
from review_api.notes import ApiError, Notes

NEVER = (SCHOOL_ID, LAST_NAME, DOB)
INCIDENTS = [   # out of order on purpose; the second one names the student in full
    {"timestamp_seconds": 75, "antecedent": "Asked to put the tablet away.",
     "behavior": "Pushed the chair back and left the table.",
     "consequence_intervention": "Offered the calm corner; returned after 2 minutes."},
    {"timestamp_seconds": 3, "antecedent": f"Marcus {LAST_NAME} (ID {SCHOOL_ID}, born {DOB}) was told to wait.",
     "behavior": f"{LAST_NAME.upper()} covered his ears.", "consequence_intervention": "Headphones offered."},
]


def tok(email):
    return token(email, uid="uid" + email.split("@")[0].replace("-", ""))


@pytest.fixture()
def school(world):
    """The roster holds what a real student record may hold one day; the approved
    session has two incidents (75 s, then 3 s)."""
    r = world.read_json("config/demo_roster.json")
    r["teachers"]["usr_demo_sarah"]["emails"] = [TEACHER_EMAIL]
    r["students"]["stu_101"].update(first_name="Marcus", last_name=LAST_NAME, dob=DOB, school_id=SCHOOL_ID)
    world.write_json("config/demo_roster.json", r)
    d = world.read_json(f"drafts/{RID}.json")
    d["analysis"]["incident_analysis"] = INCIDENTS
    world.write_json(f"drafts/{RID}.json", d)
    return world


def approve(store, authn):
    notes = Notes(store)
    t = authn.require(hdr(tok(TEACHER_EMAIL)))
    notes.approve(RID, t, store.read_json(f"drafts/{RID}.json")["analysis"])
    return notes


# ---------------------------------------------------------------- numbering

def test_clock_and_labels():
    assert [clock(x) for x in (0, 3, 59.9, 75, 600, 3599, 3600, 3725, None, "x", -4)] == [
        "0:00", "0:03", "0:59", "1:15", "10:00", "59:59", "1:00:00", "1:02:05", "0:00", "0:00", "0:00"]
    assert incident_label(1, 3) == "Incident 1 \u00b7 0:03"


def test_numbered_earliest_first_ties_keep_order():
    a, b, c = {"timestamp_seconds": 75}, {"timestamp_seconds": 3}, {"timestamp_seconds": 75}
    assert incident_numbers([a, b, c]) == [(1, b), (2, a), (3, c)]
    assert incident_numbers([]) == []


def test_session_pdf_uses_the_same_numbers(school, authn):
    notes = approve(school, authn)
    text = pdf_text(notes.pdf(RID, authn.require(hdr(tok(TEACHER_EMAIL))))[0])
    # "Incident N[: name]" and the Eastern clock time + offset (4:45:00 PM start)
    assert "Incident 1\n4:45 PM \u00b7 0:03 into recording" in text and "Incident 2\n4:46 PM \u00b7 1:15 into recording" in text
    assert text.index("Incident 1") < text.index("Incident 2")
    assert "Parent update" not in text                         # no per-session parent email any more
    assert "At 0:" not in text


# ---------------------------------------------------------------- who may print

def test_print_follows_the_export_rule(school, authn):
    notes = Notes(school)
    teacher = authn.require(hdr(tok(TEACHER_EMAIL)))
    for stage in ("draft", "approved"):
        if stage == "approved":
            approve(school, authn)
        m = notes.incident_print(RID, teacher, 1)
        assert m["label"] == "Incident 1 \u00b7 0:03" and m["draft"] is (stage == "draft")
        assert notes.incident_print(RID, authn.require(hdr(tok(ADMIN))), 2)["label"] == "Incident 2 \u00b7 1:15"
        for email, code in ((OTHER, "NOT_SESSION_TEACHER"), (RBT_EMAIL, "READ_ONLY_ROLE")):
            with pytest.raises(ApiError) as e:
                notes.incident_print(RID, authn.require(hdr(tok(email))), 1)
            assert (e.value.http, e.value.code) == (403, code), (stage, email)
    for bad, code in ((3, "NO_SUCH_INCIDENT"), (0, "BAD_INCIDENT"), ("1", "BAD_INCIDENT"), (True, "BAD_INCIDENT")):
        with pytest.raises(ApiError) as e:
            notes.incident_print(RID, teacher, bad)
        assert e.value.code == code


# ---------------------------------------------------------------- IE8-IE10: what can reach paper

@pytest.mark.parametrize("stage", ["draft", "approved"])
def test_print_view_never_has_school_id_last_name_or_dob(school, authn, stage):
    notes = approve(school, authn) if stage == "approved" else Notes(school)
    for n in (1, 2):
        m = notes.incident_print(RID, authn.require(hdr(tok(TEACHER_EMAIL))), n)
        blob = json.dumps(m, ensure_ascii=False)
        for never in NEVER:
            assert never.lower() not in blob.lower(), (n, never)
        for title in (m["title"], m["file_title"]):                       # the title is the file name
            for never in (*NEVER, "Marcus"):
                assert never not in title
        assert m["student"] == "Marcus A." and m["footer"] == FOOTER == "Confidential, contains student information"
        assert set(m) == {"recording_id", "number", "offset_seconds", "label", "of", "title", "file_title", "student",
                          "class_code", "recorded", "teacher_name", "approved_on", "fields", "footer", "sample_data",
                          "draft", "status_line", "name", "heading", "clock_time", "time_line", "time_note"}
    m1 = notes.incident_print(RID, authn.require(hdr(tok(TEACHER_EMAIL))), 1)
    pre = "DRAFT \u2013 " if stage == "draft" else ""
    assert m1["title"] == pre + "Incident 1 \u2013 RM12 \u2013 Sep 28, 2026"
    assert m1["file_title"] == ("DRAFT_" if stage == "draft" else "") + "Incident-1_4-45PM_RM12_2026-09-28"
    assert (m1["heading"], m1["time_line"]) == ("Incident 1", "4:45 PM \u00b7 0:03 into recording")
    assert m1["fields"][0]["text"] == "Marcus A. (ID [removed], born [removed]) was told to wait."
    assert m1["fields"][1]["text"] == "A. covered his ears."


def test_draft_is_marked_and_approved_is_unchanged():
    sess = {"student_first_name": "Marcus", "student_last_initial": "A.", "class_code": "RM12",
            "timestamp_start": "2026-10-07T16:01:21"}
    notes_ = {"incident_analysis": [{"timestamp_seconds": 3, "behavior": "Draft text."}]}
    d = build_print(session=sess, approval=None, draft=notes_, number=1, recording_id="rec_x")
    assert (d["draft"], d["status_line"], d["approved_on"]) == (True, "Draft, not yet approved by the teacher",
                                                                "Not yet approved")
    assert d["file_title"] == "DRAFT_Incident-1_4-01PM_RM12_2026-10-07"
    assert d["title"] == "DRAFT \u2013 Incident 1 \u2013 RM12 \u2013 Oct 7, 2026"
    assert d["fields"][1]["text"] == "Draft text." and d["footer"] == FOOTER
    a = build_print(session=sess, approval={"analysis": notes_, "approved_at": "2026-10-07T20:00:00+00:00"},
                    draft={"incident_analysis": []}, number=1, recording_id="rec_x")
    assert (a["draft"], a["status_line"], a["approved_on"]) == (False, None, "Oct 7, 2026")
    assert a["file_title"] == "Incident-1_4-01PM_RM12_2026-10-07"
    assert a["title"] == "Incident 1 \u2013 RM12 \u2013 Oct 7, 2026"


def test_student_label_is_first_name_and_last_initial_only():
    ap = {"analysis": {"incident_analysis": [{"timestamp_seconds": 3}]}, "approved_at": "2026-10-07T18:00:00+00:00"}
    for last in ("A.", "a", "Anderson", "A"):
        m = build_print(session={"student_first_name": "Marcus", "student_last_initial": last}, approval=ap,
                        number=1, recording_id="rec_x")
        assert m["student"] == "Marcus A."
    assert build_print(session={}, approval=ap, number=1, recording_id="rec_x")["student"] == "-"
    long_hour = {"analysis": {"incident_analysis": [{"timestamp_seconds": 3725}]}}
    assert build_print(session={"class_code": "Reading 3A", "timestamp_start": "2026-10-07T09:16:10"},
                       approval=long_hour, number=1, recording_id="rec_x")["file_title"] == \
        "Incident-1_10-18AM_Reading-3A_2026-10-07"
    # no start time: the offset stays in the file name and the time line says so
    m = build_print(session={"class_code": "Reading 3A"}, approval=long_hour, number=1, recording_id="rec_x")
    assert m["file_title"] == "Incident-1_1-02-05_Reading-3A" and m["time_line"] == "1:02:05 into recording"
    assert m["clock_time"] is None and m["time_note"]


def test_a_leak_that_gets_past_the_scrub_is_refused():
    for leak in (f"x {SCHOOL_ID}", f"born {DOB}", f"the {LAST_NAME} family"):
        with pytest.raises(PrintBlocked):
            check_print({"fields": [{"label": "Behavior", "text": leak}]}, [LAST_NAME], [SCHOOL_ID, DOB])
    check_print({"fields": [{"label": "Behavior", "text": "Marcus A. waited."}]}, [LAST_NAME], [SCHOOL_ID, DOB])


# ---------------------------------------------------------------- logging

def test_every_click_is_logged_with_ids_only_and_the_draft_flag(school, authn):
    jrnl = MemoryJournal()
    notes = Notes(school, recorder=Recorder(jrnl))
    teacher = authn.require(hdr(tok(TEACHER_EMAIL)))
    out = io.StringIO()
    with redirect_stdout(out):
        notes.incident_print(RID, teacher, 2)                 # draft
        approve(school, authn)
        notes.incident_print(RID, teacher, 2)                 # approved
    rows = [r for r in jrnl.rows() if r["action"] == "incident_pdf_downloaded"]
    for row in rows:
        assert (row["actor_id"], row["actor_role"], row["record_id"], row["student_id"]) == (
            "uidteacher", "reviewer", RID, "stu_101") and row["occurred_at"]
    assert [r["details"] for r in rows] == [{"incident_number": 2, "incident_offset_seconds": 75, "draft": True},
                                            {"incident_number": 2, "incident_offset_seconds": 75, "draft": False}]
    audits = [a for a in notes.audit_trail(RID) if a["event"] == "incident_pdf_downloaded"]
    assert [(a["actor_id"], a["incident_number"], a["draft"]) for a in audits] == [
        ("uidteacher", 2, True), ("uidteacher", 2, False)]
    assert all("actor_email" not in a and a["at"] for a in audits)        # ids only
    lines = [json.loads(x) for x in out.getvalue().splitlines() if "incident_pdf_downloaded" in x]
    assert [(x["recording_id"], x["incident_number"], x["draft"]) for x in lines] == [(RID, 2, True), (RID, 2, False)]
    assert all(x["actor_id"].startswith("u_") for x in lines)
    for blob in (json.dumps(rows), json.dumps(audits), out.getvalue()):
        for never in ("@", "Marcus", "Jenkins", *NEVER):
            assert never not in blob


def test_http_print_and_refusals(school, authn):
    jrnl = MemoryJournal()
    srv, base = serve(school, authn, jrnl)
    try:
        path = f"/api/sessions/{RID}/incident_print"
        st, body, _ = call(base, path, tok=tok(TEACHER_EMAIL), method="POST", body={"number": 1})
        assert st == 200 and json.loads(body)["file_title"] == "DRAFT_Incident-1_4-45PM_RM12_2026-09-28"
        for never in NEVER:
            assert never.encode() not in body
        assert call(base, path, tok=tok(RBT_EMAIL), method="POST", body={"number": 1})[0] == 403   # draft: RBT refused
        approve(school, authn)
        st, body, _ = call(base, path, tok=tok(TEACHER_EMAIL), method="POST", body={"number": 1})
        assert st == 200 and json.loads(body)["file_title"] == "Incident-1_4-45PM_RM12_2026-09-28"
        for never in NEVER:
            assert never.encode() not in body
        assert call(base, path, tok=tok(RBT_EMAIL), method="POST", body={"number": 1})[0] == 403
        assert call(base, path, tok=tok(OTHER), method="POST", body={"number": 1})[0] == 403
        assert call(base, path, method="POST", body={"number": 1})[0] == 401
        assert call(base, path, tok=tok(TEACHER_EMAIL))[0] == 405
        refused = [r for r in jrnl.rows() if r["action"] == "request_refused"]
        assert {r["details"]["attempted"] for r in refused} == {"incident_print"}
    finally:
        srv.shutdown()


def test_print_refused_if_it_cannot_be_journaled(school, authn):
    approve(school, authn)
    srv, base = serve(school, authn, FailingJournal())
    try:
        st, body, _ = call(base, f"/api/sessions/{RID}/incident_print", tok=tok(TEACHER_EMAIL), method="POST",
                           body={"number": 1})
        assert (st, json.loads(body)) == (503, {"error": "JOURNAL_UNAVAILABLE"})
        assert not [a for a in Notes(school).audit_trail(RID) if a["event"] == "incident_pdf_downloaded"]
    finally:
        srv.shutdown()
