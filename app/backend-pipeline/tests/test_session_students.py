"""Students in this session: the class roster is suggested, nothing
is linked until the teacher clicks Save; saved on the server (audit record, ids only) and journaled
(counts only); linked sessions show on each student's week; classmates are hidden from the AI.

  python -m pytest app/backend-pipeline/tests/test_session_students.py -q
"""
from __future__ import annotations

import pytest

from test_hosted_auth import call  # noqa: F401  (sets sys.path)
from test_materials import ADMIN, BELLE_T, DOC, RBT, RID, SNOW_T, seed  # noqa: F401
from test_planner import journal  # noqa: F401
from review_api.journal import Recorder
from review_api.notes import ApiError, Notes
from review_api.planner import Planner
from review_api.planner_ai import Mask
from review_api.school_setup import SchoolSetup
from review_api.session_students import SessionStudents
from review_api.store import LocalStore
from review_api.students import Students

SNEEZY, HAPPY, GIULIA = "stu_0006", "stu_0003", "stu_0041"
READING = ["stu_0001", "stu_0002", "stu_0003", "stu_0004", "stu_0005", "stu_0006", "stu_0007",
           "stu_0041", "stu_0042", "stu_0043"]
for _r in (ADMIN, SNOW_T, BELLE_T, RBT):
    _r.provider = "password"


@pytest.fixture()
def env(tmp_path, journal):  # noqa: F811
    store = LocalStore(tmp_path)
    seed(store)
    d = store.read_json(f"drafts/{RID}.json")
    d["session"]["start_time"] = "2026-10-09T08:30:00-04:00"         # Reading (cls-rm104-rdg)
    d["session"]["timestamp_start"] = "2026-10-09T08:30:00-04:00"
    store.write_json(f"drafts/{RID}.json", d)
    notes = Notes(store, recorder=Recorder(journal))
    setup = SchoolSetup(notes)
    return type("E", (), {"store": store, "notes": notes, "journal": journal, "ss": SessionStudents(notes, setup)})


def test_class_roster_is_suggested_but_nothing_is_linked_until_saved(env):
    v = env.ss.view(RID, SNOW_T)
    assert v["saved"] is False and v["student_ids"] == [DOC]          # the recording's own student only
    assert v["suggestion"]["source"] == "class" and sorted(v["suggestion"]["student_ids"]) == sorted(READING)
    assert v["can_change"] is True and {c["student_id"] for c in v["choices"]} >= set(READING)
    assert all("last_name" not in c and len(c["name"].split()) <= 2 for c in v["choices"])
    assert not [n for n in env.store.list(f"audit/{RID}/") if "_session_students_set_" in n]
    assert not [r for r in env.journal.rows() if r["action"] == "session_students_set"]
    assert RID not in [s["recording_id"] for s in Planner(env.notes)._sessions(student_id=SNEEZY)]


def test_teacher_saves_the_students_logged_ids_only_and_latest_wins(env):
    v = env.ss.save(RID, SNOW_T, {"student_ids": READING})
    assert v["saved"] is True and sorted(v["student_ids"]) == sorted(READING)
    (a,) = [env.store.read_json(n) for n in env.store.list(f"audit/{RID}/") if "_session_students_set_" in n]
    assert a["student_ids"] == READING and a["suggested"] is True and "actor_email" not in a
    (j,) = [r for r in env.journal.rows() if r["action"] == "session_students_set"]
    assert j["details"] == {"students": 10, "suggested": True} and j["record_id"] == RID
    assert "Sneezy" not in str(j) and "Doc" not in str(j)
    v = env.ss.save(RID, ADMIN, {"student_ids": [DOC, SNEEZY]})
    assert v["student_ids"] == [DOC, SNEEZY]
    assert [r["details"] for r in env.journal.rows() if r["action"] == "session_students_set"][-1] == \
        {"students": 2, "suggested": False}
    row = env.notes.session(RID, SNOW_T)
    assert row["student_ids"] == [DOC, SNEEZY] and row["students_set"] is True


def test_linked_session_shows_on_each_students_week(env):
    env.ss.save(RID, SNOW_T, {"student_ids": [DOC, SNEEZY]})
    p = Planner(env.notes)
    assert RID in [s["recording_id"] for s in p._sessions(student_id=SNEEZY)]
    assert RID not in [s["recording_id"] for s in p._sessions(student_id=HAPPY)]
    env.ss.save(RID, SNOW_T, {"student_ids": [SNEEZY]})                # Doc taken off: no longer on his week
    assert RID not in [s["recording_id"] for s in p._sessions(student_id=DOC)]
    det = Students(env.notes, p).detail(SNOW_T, SNEEZY, "2026-10-05")
    assert RID in str(det)


@pytest.mark.parametrize("actor,body,code", [
    (BELLE_T, {"student_ids": [DOC]}, "NOT_SESSION_TEACHER"),
    (RBT, {"student_ids": [DOC]}, "READ_ONLY_ROLE"),
    (SNOW_T, {"student_ids": ["stu_9999"]}, "UNKNOWN_STUDENT"),
    (SNOW_T, {"student_ids": "stu_0001"}, "BAD_STUDENTS"),
    (SNOW_T, {"student_ids": [f"stu_{i:04d}" for i in range(41)]}, "TOO_MANY_STUDENTS"),
])
def test_refusals(env, actor, body, code):
    with pytest.raises(ApiError) as e:
        env.ss.save(RID, actor, body)
    assert e.value.code == code
    assert env.ss.view(RID, RBT)["can_change"] is False


def test_classmates_are_hidden_from_the_ai_and_never_put_back():
    m = Mask(["Doc"], ["Alex Morgan"], others=["Sneezy", "Happy", "Dwarf"])
    hidden = m.hide({"t": "Doc and Sneezy shared blocks; Happy Dwarf watched. Alex Morgan helped."})
    assert hidden["t"] == ("the student and another student shared blocks; another student another student "
                           "watched. the teacher the teacher helped.")
    assert "Sneezy" not in m.show(hidden)["t"] and "Doc" in m.show(hidden)["t"]


def test_weekly_summary_and_plan_mask_every_other_student(env):
    p = Planner(env.notes)
    names = p._other_student_names([DOC])
    assert "Sneezy" in names and "Giulia" in names and "Doc" not in names
    assert "Dwarf" in names                                            # last names too


def test_http_routes(tmp_path):
    import json
    import threading
    from review_api.journal import MemoryJournal
    from review_api.server import build_server
    from test_hosted_auth import call
    store = LocalStore(tmp_path)
    seed(store)
    srv = build_server(store, 0, "local", journal=MemoryJournal())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        h = {"X-Reviewer-Id": "stf_0001"}
        st, body, _ = call(base, f"/api/sessions/{RID}/students", headers=h)
        assert st == 200 and json.loads(body)["saved"] is False
        st, body, _ = call(base, f"/api/sessions/{RID}/students", method="POST", headers=h,
                           body={"student_ids": [DOC, SNEEZY]})
        assert st == 200 and json.loads(body)["student_ids"] == [DOC, SNEEZY]
        st, body, _ = call(base, f"/api/sessions/{RID}/students", method="POST", headers={"X-Reviewer-Id": "stf_0002"},
                           body={"student_ids": [DOC]})
        assert st == 403
    finally:
        srv.shutdown()
