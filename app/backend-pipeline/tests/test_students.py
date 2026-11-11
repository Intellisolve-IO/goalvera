"""Students / Teachers / Settings tabs: every role sees every
student and IEP (Oct 2 decision; the list only opens on the viewer's own classrooms), every IEP
opening is journaled (iep_viewed + the student's internal id), one school week Mon-Fri of daily
summaries, parent emails only for the student's teachers and admins, Settings admin-only.

  python -m pytest app/backend-pipeline/tests/test_students.py -q
"""
from __future__ import annotations

import json

import pytest

from test_hosted_auth import ADMIN, TEACHER_EMAIL, call, token  # noqa: F401
from test_planner import (ART_TEACHER, LAST, NEXT, RBT_EMAIL, THIS, authn, hosted, journal,  # noqa: F401
                          pinned_today, planner, pstore, who)
from review_api.notes import ApiError
from review_api.students import Students


@pytest.fixture()
def students(planner):
    return Students(planner.notes, planner)


def test_every_role_lists_every_student_and_the_list_opens_on_their_own(students, authn):
    for email, mine in ((TEACHER_EMAIL, True), (ART_TEACHER, False), (ADMIN, False), (RBT_EMAIL, True)):
        r = students.list(who(authn, email))
        row = {s["student_id"]: s for s in r["students"]}["stu_101"]
        assert row["name"] == "Marcus A." and row["class_codes"] == ["RM12"] and row["iep_goals"] >= 1
        assert row["mine"] is mine, email


def test_any_teacher_reads_the_iep_and_every_opening_is_journaled(students, authn, journal):
    art = who(authn, ART_TEACHER)                       # not Marcus's teacher: still reads the IEP
    for _ in range(2):
        d = students.detail(art, "stu_101", THIS)
    assert d["iep"]["goals"] and d["iep_hidden"] is False and d["can_edit"] is False
    views = [r for r in journal.rows() if r["action"] == "iep_viewed"]
    assert len(views) == 2                               # every time
    assert all(v["student_id"] == "stu_101" and v["actor_id"] == art.uid for v in views)
    assert all(v["occurred_at"] for v in views)
    assert "Marcus" not in str(views)                    # internal id only


def test_one_school_week_monday_to_friday_with_daily_summaries(students, authn):
    d = students.detail(who(authn, TEACHER_EMAIL), "stu_101", LAST)
    assert [x["date"] for x in d["days"]] == ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]
    assert d["week"] == LAST and d["week_end"] == "2026-10-02"
    assert d["prev_week"] == "2026-09-21" and d["next_week"] == THIS
    assert any(s["summary"] for day in d["days"] for s in day["sessions"])
    assert students.detail(who(authn, TEACHER_EMAIL), "stu_101", NEXT)["next_week"] is None
    assert students.detail(who(authn, TEACHER_EMAIL), "stu_101")["week"] == THIS


def test_parent_email_only_for_the_students_teachers_and_admins(students, planner, authn):
    t = who(authn, TEACHER_EMAIL)
    planner.save_summary(t, {"student_id": "stu_101", "week": THIS, "summary": "A calm week overall.",
                             "parent_email": "Dear family, Marcus had a calm week."})
    mine = students.detail(t, "stu_101", THIS)
    assert mine["can_edit"] and mine["summary"]["parent_email"].startswith("Dear family, Marcus")
    assert students.detail(who(authn, ADMIN), "stu_101", THIS)["summary"]["parent_email"]
    for email in (ART_TEACHER, RBT_EMAIL):
        other = students.detail(who(authn, email), "stu_101", THIS)
        assert "parent_email" not in other["summary"] and other["parent_email_hidden"] is True


def test_rbt_with_iep_access_none_gets_no_iep_and_no_iep_viewed(students, pstore, authn, journal):
    r = pstore.read_json("config/demo_roster.json")
    r["settings"]["iep_access"]["rbt"] = "none"
    pstore.write_json("config/demo_roster.json", r)
    d = students.detail(who(authn, RBT_EMAIL), "stu_101", THIS)
    assert d["iep"] is None and d["iep_hidden"] is True
    assert not [x for x in journal.rows() if x["action"] == "iep_viewed"]


def test_bad_or_unknown_student_and_week(students, authn):
    t = who(authn, TEACHER_EMAIL)
    for sid, code in (("../x", "BAD_STUDENT"), ("stu_999", "UNKNOWN_STUDENT")):
        with pytest.raises(ApiError) as e:
            students.detail(t, sid, THIS)
        assert e.value.code == code
    for wk in ("2026-10-06", "2026-10-19", "2024-01-01"):          # not a Monday / too far ahead / too far back
        with pytest.raises(ApiError):
            students.detail(t, "stu_101", wk)


def test_teachers_emails_for_admins_only(students, authn):
    t = students.teachers(who(authn, TEACHER_EMAIL))
    assert t["emails_shown"] is False and all("emails" not in r for r in t["teachers"])
    names = {r["name"]: r for r in t["teachers"]}
    assert names["Sarah Jenkins"]["classrooms"][:1] and names["Sarah Jenkins"]["role"] == "Teacher"
    assert names["Demo RBT"]["role"].startswith("RBT")
    a = students.teachers(who(authn, ADMIN))
    assert a["emails_shown"] is True
    assert TEACHER_EMAIL in {e for r in a["teachers"] for e in r.get("emails", [])}
    assert ADMIN in {e for r in a["teachers"] for e in r.get("emails", [])}       # allowlist-only admin


def test_settings_admin_only(students, authn):
    for email in (TEACHER_EMAIL, RBT_EMAIL):
        with pytest.raises(ApiError) as e:
            students.settings(who(authn, email))
        assert e.value.code == "ADMIN_ONLY"
    s = students.settings(who(authn, ADMIN))
    assert s["iep_access_rbt"] in ("read", "none") and s["roster"]["students"] >= 1
    assert {c["class_code"] for c in s["classrooms"]} >= {"RM12"}
    assert s["retention"]["audio_days"] == 7


def test_hosted_routes(hosted):
    st, body, _ = call(hosted, "/api/students", tok=token(TEACHER_EMAIL))
    assert st == 200 and json.loads(body)["students"]
    st, body, _ = call(hosted, f"/api/students/stu_101?week={THIS}", tok=token(ART_TEACHER, uid="uidArt"))
    assert st == 200 and json.loads(body)["iep"]["goals"]
    assert call(hosted, "/api/admin/settings", tok=token(TEACHER_EMAIL))[0] == 403
    assert call(hosted, "/api/admin/settings", tok=token(ADMIN))[0] == 200
    assert call(hosted, "/api/teachers", tok=token(TEACHER_EMAIL))[0] == 200
    assert call(hosted, "/api/students/stu_101")[0] == 401                 # signed out


def test_default_classroom_teacher_rbt_admin(planner, pstore, authn):
    notes = planner.notes
    assert notes.default_classroom(who(authn, TEACHER_EMAIL)) == "RM12"         # own classroom with a student
    assert notes.default_classroom(who(authn, ART_TEACHER)) == "Art 9Z"
    assert notes.default_classroom(who(authn, RBT_EMAIL)) == "RM12"
    assert notes.default_classroom(who(authn, ADMIN)) is None                   # admins: All
    r = pstore.read_json("config/demo_roster.json")
    r["teachers"]["usr_demo_sarah"]["default_class_code"] = "math-2b"           # set in the roster
    pstore.write_json("config/demo_roster.json", r)
    assert notes.default_classroom(who(authn, TEACHER_EMAIL)) == "Math 2B"
    assert planner.default_class(who(authn, TEACHER_EMAIL)) == "Math 2B"        # the planner opens there too


def test_lists_carry_the_default(students, authn):
    assert students.list(who(authn, TEACHER_EMAIL))["default_classroom"] == "RM12"
    assert students.list(who(authn, ADMIN))["default_classroom"] is None
    rows = {r["name"]: r for r in students.teachers(who(authn, TEACHER_EMAIL))["teachers"]}
    assert rows["Sarah Jenkins"]["default_classroom"] == "RM12"
    assert {d["teacher_name"]: d["default_classroom"] for d in students.settings(who(authn, ADMIN))["teacher_defaults"]}[
        "Sarah Jenkins"] == "RM12"


def test_me_carries_the_default(hosted):
    st, body, _ = call(hosted, "/api/me", tok=token(TEACHER_EMAIL))
    assert st == 200 and json.loads(body)["default_classroom"] == "RM12"
    st, body, _ = call(hosted, "/api/me", tok=token(ADMIN))
    assert json.loads(body)["default_classroom"] is None


def test_offline_validator_matches_schema_no_parent_email_required():
    """The fallback validator (no jsonschema: laptop / offline e2e) used to require
    draft_parent_email, so approving from the UI (which no longer sends it) got INVALID_NOTES."""
    from gemini_analyzer.schema import _lightweight_validate
    ok = {"goal_evaluations": [], "incident_analysis": [], "general_session_summary": "Calm morning."}
    assert _lightweight_validate(ok) == []
    assert _lightweight_validate({**ok, "draft_parent_email": ""}) == ["draft_parent_email: non-empty string required"]
    assert "general_session_summary: required" in _lightweight_validate({"goal_evaluations": [], "incident_analysis": []})
