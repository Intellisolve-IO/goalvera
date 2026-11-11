"""Everyday Heroes Academy: the fictional sample school as the demo's roster. app/samples/demo_roster.json is built from app/samples/sis-demo by
app/tools/build_demo_roster.py.

* the roster is up to date, fictional, labelled, and carries no ids, birth dates, contacts,
  pronouns or credential numbers
* Room 104 is Alex Morgan's with the seven Dwarfs; many students per classroom work everywhere
  (Students, Teachers, Settings, planner, default classrooms)
* recordings made under the old Goalvera roster land in Room 104 when read (nothing rewritten)
* the incident-name and AI-payload safeguards still hold with the new names

  python -m pytest app/backend-pipeline/tests/test_sample_school.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_hosted_auth import ADMIN, TEACHER_EMAIL, token, verifier  # noqa: F401
from test_planner import LAST, THIS, pinned_today  # noqa: F401  (autouse fixture)
from review_api import planner_ai as ai
from review_api.auth import Allowlist, Authenticator, Roster
from review_api.bug_reports import never_values
from review_api.incident_names import Namer, capitalized_words, clean_name, transcript_window, usable
from review_api.journal import MemoryJournal, Recorder
from review_api.notes import Notes
from review_api.planner import Planner
from review_api.store import LocalStore
from review_api.students import Students
from seed_local_session import RID
from seed_planner_demo import seed_planner

REPO = Path(__file__).resolve().parents[3]
ROSTER = json.loads((REPO / "app/samples/demo_roster.json").read_text())
DWARFS = ["Doc", "Grumpy", "Happy", "Sleepy", "Bashful", "Sneezy", "Dopey"]
RBT_EMAIL = "demo-rbt@staff.example"            # the demo RBT sign-in, now Room 104's RBT
OTHER_TEACHER = "belle@example.org"


# ---------------------------------------------------------------- the roster file

def test_roster_is_built_from_the_sample_school_and_up_to_date():
    r = subprocess.run([sys.executable, str(REPO / "app/tools/build_demo_roster.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_roster_counts_and_room_104():
    assert ROSTER["_meta"]["synthetic"] is True and ROSTER["_meta"]["school_name"] == "Everyday Heroes Academy"
    assert len(ROSTER["students"]) == 87 and len(ROSTER["class_codes"]) == 8
    assert len(ROSTER["teachers"]) + len(ROSTER["staff"]) == 23 and "rbts" not in ROSTER   # one staff list
    room = ROSTER["class_codes"]["Room 104"]
    assert room["teacher_name"] == "Alex Morgan"
    kids = sorted(s["first_name"] for s in ROSTER["students"].values() if s["class_codes"] == ["Room 104"])
    assert set(DWARFS) <= set(kids) and len(kids) == 10
    assert ROSTER["teachers"][room["teacher_id"]]["default_class_code"] == "Room 104"
    assert ROSTER["staff"]["stf_0009"]["name"] == "Mary Poppins"


def test_snow_white_has_her_demo_staff_email_and_no_one_else_does():
    # Only the lead demo teacher has a staff address on file
    snow = ROSTER["teachers"][ROSTER["class_codes"]["Room 104"]["teacher_id"]]
    assert snow["teacher_name"] == "Alex Morgan" and snow["emails"] == ["snowwhite@staff.example"]
    others = [t["emails"] for tid, t in ROSTER["teachers"].items() if t is not snow]
    assert others and all(e == [] for e in others)
    snow_id = ROSTER["class_codes"]["Room 104"]["teacher_id"]
    assert Roster(LocalStoreFromRoster()).teacher_ids_for("SnowWhite@staff.example") == frozenset({snow_id})


class LocalStoreFromRoster:
    def read_json(self, name):
        return ROSTER


def test_every_person_is_labelled_fictional():
    for group in ("students", "teachers", "staff", "class_codes"):
        for k, v in ROSTER[group].items():
            assert v["label"] == "Fictional sample data", (group, k)


def test_roster_has_only_what_the_site_needs():
    """no pronouns (never sent to the AI), no staff credential numbers, no ids, birth dates,
    contacts, addresses or health flags in the roster the review site and the AI drafts read."""
    text = json.dumps(ROSTER).lower()
    for word in ("pronoun", "birth", "credential", "statestudent", "studentnumber", "guardian",
                 "example.com", "example.org", "fict-", "555-01"):
        assert word not in text, word
    keys = set()

    def walk(o):
        if isinstance(o, dict):
            keys.update(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(ROSTER["students"])
    assert not keys & {"pronouns", "school_id", "student_number", "dob", "birth_date", "state_id", "phone", "email",
                       "address", "contacts", "health_alert", "number"}


def test_legacy_codes_are_aliases_of_room_104():
    from common.classcodes import find_class
    for code in ("RM12", "Reading 3A", "reading-3a", "Math 2B", "Circle Time", "room-b", "Room B", "Hall",
                 "RM104", "room-104"):
        assert find_class(ROSTER, code)[0] == "Room 104", code
    assert find_class(ROSTER, "RM101")[0] == "Room 101"
    assert find_class(ROSTER, "Art 9Z") == (None, None)


# ---------------------------------------------------------------- the site with this roster

@pytest.fixture()
def hstore(tmp_path):
    seed_planner(str(tmp_path))                      # old RM12 recordings (Marcus A., Sarah Jenkins)
    s = LocalStore(tmp_path)
    r = json.loads(json.dumps(ROSTER))
    r["teachers"]["stf_0001"]["emails"] = [TEACHER_EMAIL]      # someone acting as Alex Morgan
    r["teachers"]["stf_0002"]["emails"] = [OTHER_TEACHER]      # Belle Beaumont, Room 101
    s.write_json("config/demo_roster.json", r)
    s.write_json("config/review_allowlist.json", {"users": [
        {"email": ADMIN, "role": "admin"}, {"email": TEACHER_EMAIL, "role": "teacher"},
        {"email": OTHER_TEACHER, "role": "teacher"}, {"email": RBT_EMAIL, "role": "rbt"}]})
    return s


@pytest.fixture()
def journal():
    return MemoryJournal()


@pytest.fixture()
def site(hstore, journal):
    notes = Notes(hstore, roster_ttl=0, recorder=Recorder(journal))
    planner = Planner(notes)
    return {"notes": notes, "planner": planner, "students": Students(notes, planner),
            "authn": Authenticator(verifier(), Allowlist(hstore), Roster(hstore))}


def who(site, email):
    return site["authn"].require({"Authorization": "Bearer " + token(email, uid="uid" + email.split("@")[0].replace("-", ""))})


def test_students_tab_lists_all_87_and_opens_on_room_104(site):
    r = site["students"].list(who(site, TEACHER_EMAIL))
    assert len(r["students"]) == 87 and r["default_classroom"] == "Room 104"
    mine = [s for s in r["students"] if s["mine"]]
    assert {s["name"].split()[0] for s in mine} >= set(DWARFS) and len(mine) == 10
    doc = next(s for s in r["students"] if s["name"] == "Doc D.")
    assert doc["class_codes"] == ["Room 104"] and doc["teacher_names"] == ["Alex Morgan"]
    assert doc["label"] == "Fictional sample data" and doc["plan_type"] == "IEP" and doc["iep_goals"] == 5
    assert sorted(r["classrooms"]) == [f"Room 10{i}" for i in range(1, 9)]


def test_a_dwarfs_iep_opens_with_its_label_and_is_journaled(site, journal):
    d = site["students"].detail(who(site, OTHER_TEACHER), "stu_0001", THIS)   # any teacher reads every IEP
    assert d["name"] == "Doc D." and d["label"] == "Fictional sample data" and d["grade"] == "5"
    iep = d["iep"]
    assert len(iep["goals"]) == 5 and iep["eligibility"] == ["Autism", "Speech-Language Impairment"]
    assert iep["case_manager"] == "Alex Morgan" and iep["present_levels"] and iep["services"] and iep["accommodations"]
    assert iep["behavior_plan"].startswith("Behavior plan targets:")
    assert d["can_edit"] is False
    assert site["students"].detail(who(site, TEACHER_EMAIL), "stu_0001", THIS)["can_edit"] is True
    views = [x for x in journal.rows() if x["action"] == "iep_viewed"]
    assert len(views) == 2 and all(v["student_id"] == "stu_0001" for v in views) and "Doc" not in str(views)


def test_teachers_tab_has_all_23_staff_with_roles(site):
    t = site["students"].teachers(who(site, ADMIN))["teachers"]
    named = [x for x in t if not x.get("allowlist_only")]
    assert len(named) == 23
    snow = next(x for x in named if x["name"] == "Alex Morgan")
    assert snow["classrooms"] == ["Room 104"] and snow["default_classroom"] == "Room 104"
    assert snow["label"] == "Fictional sample data"
    assert {x["role"] for x in named} >= {"RBT (read-only)", "BCBA", "Principal", "Case manager"}


def test_settings_counts(site):
    s = site["students"].settings(who(site, ADMIN))
    assert s["roster"]["students"] == 87 and s["roster"]["classrooms"] == 8 and s["roster"]["school"] == "Everyday Heroes Academy"
    room = next(c for c in s["classrooms"] if c["class_code"] == "Room 104")
    assert room["students"] == 10 and room["teacher_name"] == "Alex Morgan" and "RM12" in room["aliases"]


def test_default_classrooms(site):
    n = site["notes"]
    assert n.default_classroom(who(site, TEACHER_EMAIL)) == "Room 104"
    assert n.default_classroom(who(site, OTHER_TEACHER)) == "Room 101"
    assert n.default_classroom(who(site, RBT_EMAIL)) == "Room 104"
    assert n.default_classroom(who(site, ADMIN)) is None              # admins open on All
    assert site["planner"].default_class(who(site, ADMIN)) == "Room 101"


def test_planner_shows_the_whole_classroom(site):
    v = site["planner"].view(who(site, TEACHER_EMAIL), None)
    assert v["class_code"] == "Room 104" and v["can_edit_plan"] is True
    names = [s["name"] for s in v["weeks"][1]["students"]]
    assert len(names) == 10 and "Sneezy D." in names
    assert {c["class_code"]: c["students"] for c in v["classrooms"]}["Room 108"] == 9


def test_old_recordings_land_in_room_104_without_rewriting(site, hstore):
    before = (Path(hstore.root) / f"drafts/{RID}.json").read_bytes() if hasattr(hstore, "root") else None
    row = site["notes"].session(RID, who(site, ADMIN))
    assert row["class_code"] == "Room 104" and row["teacher_name"] == "Alex Morgan"
    assert row["needs_class_assignment"] is False and row["student_id"] is None   # Marcus isn't in this school
    ctx = site["notes"].context(RID)
    assert ctx["classroom_remapped_from"] == "RM12" and ctx["legacy_student_id"] == "stu_101"
    assert hstore.read_json(f"drafts/{RID}.json")["session"]["class_code"] == "RM12"   # stored record unchanged
    if before is not None:
        assert (Path(hstore.root) / f"drafts/{RID}.json").read_bytes() == before
    # Alex Morgan (by her sign-in) may now act on it; Belle (another classroom) may not
    assert site["notes"].session(RID, who(site, TEACHER_EMAIL))["viewer_may_act"] is True
    assert site["notes"].session(RID, who(site, OTHER_TEACHER))["viewer_may_act"] is False
    rows = site["notes"].list_sessions(who(site, TEACHER_EMAIL))
    assert rows and {r["class_code"] for r in rows} == {"Room 104"}


def test_room_210_and_unknown_codes(site, hstore):
    d = hstore.read_json(f"drafts/{RID}.json")
    d["session"].update(class_code="Room B", teacher_id=None, teacher_name=None, needs_class_assignment=True)
    hstore.write_json("drafts/rec_laptop_demo_20261001_090000_Room_210.json", {**d, "recording_id": "rec_laptop_demo_20261001_090000_Room_210"})
    row = site["notes"].session("rec_laptop_demo_20261001_090000_Room_210", who(site, ADMIN))
    assert (row["class_code"], row["teacher_name"], row["needs_class_assignment"]) == ("Room 104", "Alex Morgan", False)
    d["session"].update(class_code="Art 9Z")
    hstore.write_json("drafts/rec_laptop_demo_20261001_100000_Art.json", {**d, "recording_id": "rec_laptop_demo_20261001_100000_Art"})
    row = site["notes"].session("rec_laptop_demo_20261001_100000_Art", who(site, ADMIN))
    assert row["class_code"] == "Art 9Z" and row["needs_class_assignment"] is True    # nothing guessed


def test_remapped_recording_without_markers_reads_its_original_file_and_the_list_never_fails(site, hstore):
    """Live smoke, Oct 10: a remapped laptop recording with no markers looked for
    rec/<dev>/<ts>_Room 104.json (a space: bad object name) and the whole session list failed."""
    rid = "rec_laptop_demo_20260929_101500_RM12"
    d = hstore.read_json(f"drafts/{RID}.json")
    d["session"].update(class_code="RM12", device_id="laptop-demo", timestamp_start="2026-09-29T10:15:00-04:00",
                        markers=None, source_object=None)
    hstore.write_json(f"drafts/{rid}.json", {**d, "recording_id": rid})
    hstore.write_json("rec/laptop-demo/20260929_101500_RM12.json", {"markers": []})
    asked = []
    real = hstore.read_json
    hstore.read_json = lambda name: (asked.append(name), real(name))[1]
    try:
        row = site["notes"].session(rid, who(site, ADMIN))
        assert row["class_code"] == "Room 104"
        assert "rec/laptop-demo/20260929_101500_RM12.json" in asked
        assert not any(" " in a for a in asked if a.startswith("rec/"))
        assert len(site["notes"].list_sessions(who(site, ADMIN))) >= 2
    finally:
        hstore.read_json = real


def test_a_sidecar_name_the_store_refuses_is_treated_as_missing():
    from review_api.notes import _read_sidecar

    class Picky:
        def read_json(self, name):
            raise ValueError("bad object name")
    assert _read_sidecar(Picky(), "rec/x/20260928_164500_Room 104.json") is None


# ---------------------------------------------------------------- privacy safeguards with the new names

@pytest.fixture()
def never(site):
    return never_values(site["notes"].roster(), site["notes"].context(RID))


@pytest.mark.parametrize("leak", [
    "Doc left his seat during reading", "Left seat when Grumpy called out", "Covered ears near Happy",
    "Walked out as Sleepy watched", "Bashful hid under the table", "Sneezy dropped the tablet",
    "Dopey ran to the door", "Pushed a peer while Alex Morgan read", "Alex asked for quiet",
    "Left area, Ms Morgan redirected", "Refused task from Mary Poppins", "Elsa Winters came in",
    "Flynn Rider walked with the student", "Called out Cinderella's name", "Left with the Dwarf group",
])
def test_ai_incident_names_with_people_from_the_new_school_are_rejected(never, leak):
    assert usable(leak, never) is None, leak


def test_capitalized_unknown_words_are_rejected_and_plain_names_pass(never):
    assert capitalized_words("Pushed a peer while Rumpel watched") == ["Rumpel"]   # not on the roster
    assert capitalized_words("Left seat during Circle Time and Math") == []
    assert usable("Left seat during Circle Time and returned with a prompt", never) == \
        "Left seat during Circle Time and returned with a prompt"
    assert clean_name("Covered ears during the fire drill", never) == "Covered ears during the fire drill"


def test_transcript_window_masks_the_new_names(never):
    t = {"turns": [{"start": 10.0, "end": 14.0, "speaker": "Speaker 1",
                    "text": "Doc, please sit with Grumpy. Ms. White is coming. Thank you Sneezy Dwarf."}]}
    text = " ".join(transcript_window(t, 9, never, 60))
    for n in ("Doc", "Grumpy", "White", "Sneezy", "Dwarf"):
        assert n not in text, (n, text)


def test_plan_draft_for_room_104_sends_no_names_or_pronouns(site, monkeypatch):
    t = who(site, TEACHER_EMAIL)
    site["planner"].save_note(t, {"student_id": "stu_0001", "week": LAST,
                                  "note": "Doc used his break card twice; Grumpy and Happy joined the group."})
    sent = []

    def fake(system, payload, schema):
        sent.append(json.loads(json.dumps(payload)))
        return {"goals": ["Use the break card"], "objectives": ["Practice turn taking"]}, "test", None
    monkeypatch.setattr(ai, "_call", fake)
    monkeypatch.setenv("PLANNER_AI", "live")
    site["planner"].draft_plan(t, {"class_code": "Room 104", "week": THIS})
    text = json.dumps(sent)
    for n in [*DWARFS, "Dwarf", "Alex", "Morgan", "Giulia", "Alberto", "Russell"]:
        assert n not in text, n
    assert "pronoun" not in text.lower()


def test_namer_falls_back_to_incident_n_for_new_school_names(site, never):
    """End to end through the naming step: a dwarf / staff name -> scrubbed ("Incident N"); a
    capitalized unknown word -> retried once -> still there -> "Incident N"; plain names pass."""
    replies = [
        {"names": [{"index": 0, "name": "Doc left his seat"}, {"index": 1, "name": "Pushed a peer near Grumpy"},
                   {"index": 2, "name": "Covered ears as Alex Morgan read"}, {"index": 3, "name": "Waved at Rumpel"},
                   {"index": 4, "name": "Left seat and walked to the window"}]},
        {"names": [{"index": 3, "name": "Waved at Rumpel again"}]},
    ]
    namer = Namer(site["notes"], call=lambda system, payload, schema: replies.pop(0))
    out = namer._names({"incidents": [{"index": i} for i in range(5)]}, never)
    assert out[0] == (None, "scrub") and out[1] == (None, "scrub") and out[2] == (None, "scrub")
    assert out[3] == (None, "capitalized_word")
    assert out[4] == ("Left seat and walked to the window", "ok")


# ---------------------------------------------------------------- attendance

def test_sample_data_validates_including_attendance():
    r = subprocess.run([sys.executable, str(REPO / "app/tools/validate_sis_demo.py")], capture_output=True, text=True)
    assert r.returncode == 0 and "PASS" in r.stdout, r.stdout + r.stderr
    assert "attendance rows=" in r.stdout


def test_every_student_has_an_attendance_summary_and_the_page_shows_it(site):
    for sid, st in ROSTER["students"].items():
        a = st["attendance"]
        assert a["school_days"] == a["present"] + a["absent_excused"] + a["absent_unexcused"], sid
        assert a["from"] == "2026-08-03" and a["through"] == "2026-10-09" and 0 <= a["rate"] <= 100
    d = site["students"].detail(who(site, TEACHER_EMAIL), "stu_0001", THIS)
    assert d["attendance"] == ROSTER["students"]["stu_0001"]["attendance"]


def test_attendance_never_goes_to_the_ai(site, monkeypatch):
    sent = []
    monkeypatch.setattr(ai, "_call", lambda system, payload, schema: (sent.append(json.dumps(payload)) or
                                                                       ({"goals": ["x"], "objectives": ["y"]}, "test", None)))
    monkeypatch.setenv("PLANNER_AI", "live")
    t = who(site, TEACHER_EMAIL)
    site["planner"].save_note(t, {"student_id": "stu_0002", "week": LAST, "note": "Worked on reading."})
    site["planner"].draft_plan(t, {"class_code": "Room 104", "week": THIS})
    assert sent and not any(k in sent[0] for k in ("attendance", "absent", "tardy", "school_days"))
