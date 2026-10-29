"""Speaker selector from the roster + RBTs as staff records (David, Oct 10 2026; tasks#104).

Choices: the session's class first (its teachers / RBTs and students), else the classroom's, then
everyone else, then generic options; first name + last initial only; nothing stored. RBTs are staff
records with their role and supervisor (person schema); their sign-in scope is unchanged.
"""
from __future__ import annotations

import copy
import json
import threading

PRID = "rec_laptop_demo_20260928_164500_RM12"     # seed_local_session.RID (the planner seed's base session)
from test_hosted_auth import call, token  # noqa: F401
from test_materials import DOC, RID, ROSTER, SNOW, SNOW_T, seed
from test_planner import RBT_EMAIL, TEACHER_EMAIL, authn, hosted, journal, pstore  # noqa: F401
from common.classcodes import class_key
from common.roster import rbt_records, short_name, staff_directory
from review_api.auth import Roster
from review_api.bug_reports import never_values
from review_api.journal import MemoryJournal
from review_api.notes import Notes
from review_api.school_setup import SchoolSetup
from review_api.server import build_server
from review_api.speakers import GENERIC, Speakers
from review_api.store import LocalStore
from review_api.students import Students

FLYNN, POPPINS = "stf_0010", "stf_0009"
SNOW_T.provider = "password"           # audit records note how a signed-in person signed in


def env(tmp_path, roster=None, start=None):
    store = LocalStore(tmp_path)
    seed(store, roster=roster)
    if start:
        d = store.read_json(f"drafts/{RID}.json")
        d["session"]["start_time"], d["session"]["duration_seconds"] = start, 300
        store.write_json(f"drafts/{RID}.json", d)
    notes = Notes(store)
    setup = SchoolSetup(notes)
    return store, notes, setup, Speakers(notes, setup)


class _R:
    def __init__(self, doc):
        self.doc = doc

    def read_json(self, name):
        return self.doc


# ---------------------------------------------------------------- RBTs are staff records

def test_rbts_are_staff_records_with_role_and_supervisor():
    assert "rbts" not in ROSTER
    rbts = {k: v for k, v in ROSTER["staff"].items() if v.get("role") == "rbt"}
    assert len(rbts) == 5 and set(rbts) == set(rbt_records(ROSTER))
    assert all(v["supervisor_id"] == POPPINS and v["label"] == "Fictional sample data" for v in rbts.values())
    assert ROSTER["staff"][POPPINS]["name"] == "Mary Poppins" and ROSTER["staff"][POPPINS]["role"] == "bcba"
    assert all(t["role"] == "teacher" for t in ROSTER["teachers"].values())
    assert all(v.get("role") for v in ROSTER["staff"].values())


def test_one_staff_directory_with_the_role_from_each_record():
    d = staff_directory(ROSTER)
    assert len(d) == 23 and len({p["id"] for p in d}) == 23
    flynn = next(p for p in d if p["id"] == FLYNN)
    assert flynn["role"] == "rbt" and flynn["class_codes"] == ["Room 104"] and flynn["supervisor_name"] == "Mary Poppins"
    assert flynn["short_name"] == "Flynn R." and short_name("Alex Morgan") == "Alex M." and short_name("Cher") == "Cher"
    assert sum(p["role"] == "rbt" for p in d) == 5 and sum(p["role"] == "teacher" for p in d) == 8


def test_older_rosters_with_a_separate_rbt_list_still_work():
    old = {"teachers": {"t1": {"teacher_name": "Sarah Jenkins"}},
           "rbts": {"r1": {"name": "Demo RBT", "class_codes": ["RM12"], "emails": ["rbt@x.org"]}}}
    d = staff_directory(old)
    assert [(p["id"], p["role"], p["title"]) for p in d] == [("t1", "teacher", "Teacher"), ("r1", "rbt", "RBT")]
    assert Roster(_R(old)).class_keys_for("rbt@x.org") == frozenset({class_key("RM12")})


def test_rbt_sign_in_scope_is_unchanged():
    # Flynn Rider's demo sign-in still maps to Room 104 only, and to no teacher ids (read-only role)
    r = Roster(_R(ROSTER))
    assert r.class_keys_for("demo-rbt@staff.example") == frozenset({class_key("Room 104")})
    assert r.teacher_ids_for("demo-rbt@staff.example") == frozenset()
    legacy = copy.deepcopy(ROSTER)
    legacy["rbts"] = {k: legacy["staff"].pop(k) for k in list(legacy["staff"]) if legacy["staff"][k]["role"] == "rbt"}
    assert Roster(_R(legacy)).class_keys_for("demo-rbt@staff.example") == r.class_keys_for("demo-rbt@staff.example")


def test_teachers_tab_lists_rbts_with_their_supervisor(tmp_path):
    _, notes, _, _ = env(tmp_path)
    rows = Students(notes, None).teachers(SNOW_T)["teachers"]
    rbts = [x for x in rows if x["role"] == "RBT (read-only)"]
    assert len(rbts) == 5 and all(x["supervisor"] == "Mary Poppins" for x in rbts)
    assert next(x for x in rows if x["id"] == FLYNN)["classrooms"] == ["Room 104"]
    assert len(rows) == 23


def test_ai_masking_still_covers_every_rbt():
    staff = set(never_values(ROSTER)["staff"])
    for v in rbt_records(ROSTER).values():       # every token, "Li" in "Shang Li" too (test_short_names.py)
        assert set(v["name"].split()) <= staff, v["name"]


# ---------------------------------------------------------------- speaker choices

def test_class_first_when_the_recording_matches_a_class(tmp_path):
    _, _, _, sp = env(tmp_path, start="2026-10-05T08:25:00-04:00")    # Monday 8:25 AM -> Room 104 Reading
    c = sp.choices(RID, SNOW_T)
    assert c["basis"] == {"kind": "class", "name": "Room 104 Reading"}
    labels = [p["label"] for p in c["in_class"]]
    assert labels[:3] == ["Alex M.", "Flynn R.", "Doc D."]                # teacher, RBT, the session's student
    assert "Sneezy D." in labels and len(labels) == 12
    assert c["in_class"][2]["session_student"] is True and c["in_class"][2]["id"] == DOC
    assert c["generic"] == GENERIC and "Unknown speaker" in GENERIC
    assert c["label"] == "Fictional sample data"


def test_classroom_when_no_class_matches_then_everyone_else(tmp_path):
    _, _, _, sp = env(tmp_path, start="2026-10-10T10:15:00-04:00")    # Saturday: no class
    c = sp.choices(RID, SNOW_T)
    assert c["basis"] == {"kind": "classroom", "name": "Room 104"}
    inside = {p["id"] for p in c["in_class"]}
    assert {SNOW, FLYNN, DOC} <= inside and len(inside) == 12
    others = c["others"]
    assert len(inside) + len(others) == 87 + 23
    assert any(p["label"] == "Mary P." and p["role"] == "BCBA" for p in others)
    assert [p["kind"] for p in others] == sorted((p["kind"] for p in others), key=lambda k: k == "student")


def test_only_first_name_and_last_initial(tmp_path):
    _, _, _, sp = env(tmp_path)
    blob = json.dumps(sp.choices(RID, SNOW_T))
    last_names = {s["last_name"] for s in ROSTER["students"].values()} | \
        {t["teacher_name"].split()[-1] for t in ROSTER["teachers"].values()} | \
        {t["name"].split()[-1] for t in ROSTER["staff"].values()}
    for ln in last_names:
        assert f'{ln}"' not in blob and f"{ln} " not in blob, ln


def test_same_label_twice_gets_the_classroom(tmp_path):
    r = copy.deepcopy(ROSTER)
    twin = next(k for k, v in r["students"].items() if v["class_codes"] == ["Room 101"])
    r["students"][twin].update(first_name="Doc", last_initial="D.", last_name="Dee")
    _, _, _, sp = env(tmp_path, roster=r)
    c = sp.choices(RID, SNOW_T)
    assert any(p["label"] == "Doc D. (Room 104)" for p in c["in_class"])
    assert any(p["label"] == "Doc D. (Room 101)" for p in c["others"])


def test_picked_class_changes_the_first_group(tmp_path):
    _, _, setup, sp = env(tmp_path, start="2026-10-05T08:25:00-04:00")
    setup.assign(RID, SNOW_T, {"class_id": "cls-rm104-mth"})
    assert sp.choices(RID, SNOW_T)["basis"]["name"] == "Room 104 Math"


def test_http_laptop_get_only_and_nothing_stored(tmp_path):
    store = LocalStore(tmp_path)
    seed(store)
    before = sorted(store.list(""))
    srv = build_server(store, 0, "local", journal=MemoryJournal())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        st, body, _ = call(base, f"/api/sessions/{RID}/speakers", headers={"X-Reviewer-Id": SNOW})
        assert st == 200 and json.loads(body)["in_class"]
        st, _, _ = call(base, f"/api/sessions/{RID}/speakers", method="POST", headers={"X-Reviewer-Id": SNOW}, body={})
        assert st in (404, 405)
        st, _, _ = call(base, "/api/sessions/BAD!/speakers", headers={"X-Reviewer-Id": SNOW})
        assert st in (400, 404)
    finally:
        srv.shutdown()
    assert sorted(store.list("")) == before


def test_http_hosted_signed_in_roles_read_and_anonymous_is_refused(hosted):  # noqa: F811
    for email, uid in ((RBT_EMAIL, "uidrbt"), (TEACHER_EMAIL, "uidteacher")):
        st, body, _ = call(hosted, f"/api/sessions/{PRID}/speakers", tok=token(email, uid=uid))
        assert st == 200, (email, body)
        c = json.loads(body)
        assert c["in_class"] and c["generic"] == GENERIC
    st, _, _ = call(hosted, f"/api/sessions/{PRID}/speakers")
    assert st == 401
