"""Domains (educational methodology) and Classes (David, Oct 10 2026; tasks#103): the seed is David's
document exactly and the fictional sample school's 32 classes; admins add / edit / remove (journaled,
ids only, stale saves refused); teachers read, and edit only their own classes; sessions pick a class
(suggested from classroom + recording time vs schedule; logged); materials get the class's domain name +
methodology and nothing else new.

  python -m pytest app/backend-pipeline/tests/test_school_setup.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from test_hosted_auth import ADMIN as H_ADMIN, call, token  # noqa: F401
from test_materials import (ADMIN, BELLE_T, DOC, RBT, RID, ROSTER, SNOW, SNOW_T, Spy, reviewer,  # noqa: F401
                            seed)
from test_planner import RBT_EMAIL, TEACHER_EMAIL, authn, hosted, journal, pstore  # noqa: F401
from review_api import mailer
from review_api import material_ai as mai
from review_api import material_svg as msvg
from review_api.journal import MemoryJournal, Recorder
from review_api.materials import Materials
from review_api.notes import ApiError, Notes
from review_api.school_setup import SchoolSetup, schedule_text, seed_classes, seed_domains
from review_api.server import build_server
from review_api.store import LocalStore

REPO = Path(__file__).resolve().parents[3]
DOC_NAMES = ["Reading & Phonics", "Mathematics", "Handwriting & Writing", "Social-Emotional & Self-Regulation",
             "Science & Social Studies", "Physical & Creative Arts"]
FLYNN = "stf_0010"
for _r in (ADMIN, SNOW_T, BELLE_T, RBT):      # audit records note how a signed-in person signed in
    _r.provider = "password"


@pytest.fixture()
def env(tmp_path, monkeypatch, journal):  # noqa: F811
    monkeypatch.setenv("MATERIALS_EMAIL_DEFAULT", "off")
    monkeypatch.delenv("SCHOOL_PROFILE", raising=False)
    store = LocalStore(tmp_path)
    seed(store)
    notes = Notes(store, recorder=Recorder(journal))
    return type("E", (), {"store": store, "notes": notes, "journal": journal, "setup": SchoolSetup(notes)})


def rows(env, action=None):
    return [r for r in env.journal.rows() if action is None or r["action"] == action]


def session_at(env, start: str, minutes: int = 20, rid: str = RID):
    d = env.store.read_json(f"drafts/{rid}.json")
    d["session"]["start_time"] = start
    d["session"]["duration_seconds"] = minutes * 60
    env.store.write_json(f"drafts/{rid}.json", d)


# ---------------------------------------------------------------- seed

def test_domains_seed_is_davids_document_exactly():
    d = seed_domains()
    assert [x["name"] for x in d["domains"]] == DOC_NAMES
    reading = d["domains"][0]
    assert reading == {"id": "dom-reading-phonics", "name": "Reading & Phonics",
                       "methodology": "Orton-Gillingham (OG) Approach",
                       "methodology_detail": "Direct, explicit, multisensory, and sequential phonics instruction "
                                             "tailored for dyslexic learners.",
                       "programs": "Structured Literacy, Sight Word & Decoding Practice",
                       "book": "Uncovering the Logic of English by Denise Eide (Or Overcoming Dyslexia by Dr. Sally Shaywitz)"}
    assert all(x["methodology"] and x["methodology_detail"] and x["programs"] and x["book"] for x in d["domains"])
    assert d["_meta"]["missing_fields"] == []
    assert [f["name"] for f in d["delivery_formats"]] == [
        "1:1 Personalized / Prescriptive Instruction", "Micro Small-Group Instruction", "Multisensory & Therapy Integration"]


def test_classes_seed_is_the_fictional_schools_32_classes():
    c = seed_classes()["classes"]
    doms = {d["id"]: d["name"] for d in seed_domains()["domains"]}
    assert len(c) == 32 and len({x["classroom"] for x in c}) == 8
    assert all(x["label"] == "Fictional sample data" and x["domain_id"] in doms and x["schedule"] for x in c)
    staff = set(ROSTER["teachers"]) | set(ROSTER["staff"])
    assert all(set(x["teacher_ids"]) <= staff and set(x["student_ids"]) <= set(ROSTER["students"]) for x in c)
    rdg = next(x for x in c if x["id"] == "cls-rm104-rdg")
    assert rdg["name"] == "Room 104 Reading" and rdg["teacher_ids"] == [SNOW, FLYNN]
    assert doms[rdg["domain_id"]] == "Reading & Phonics" and DOC in rdg["student_ids"] and len(rdg["student_ids"]) == 10
    assert rdg["schedule"] == [{"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "start": "08:20", "minutes": 90,
                                "period": "Reading Block"}]
    assert {doms[x["domain_id"]] for x in c if x["subject"] == "Math"} == {"Mathematics"}


def test_seed_files_are_up_to_date():
    r = subprocess.run([sys.executable, str(REPO / "app/tools/build_school_setup.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_schedule_text():
    assert schedule_text([{"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "start": "13:15", "minutes": 45},
                          {"days": ["Tue", "Thu"], "start": "08:00", "minutes": 20}]) == \
        "Mon–Fri 1:15 PM, 45 min; Tue, Thu 8:00 AM, 20 min"


# ---------------------------------------------------------------- reading

def test_everyone_reads_domains_and_classes_only_admins_edit_domains(env):
    for actor, edit in ((ADMIN, True), (SNOW_T, False), (RBT, False), (SNOW, False)):
        v = env.setup.domains_view(actor)
        assert [d["name"] for d in v["domains"]] == DOC_NAMES and v["can_edit"] is edit and v["version"] == "seed"
    c = env.setup.classes_view(SNOW_T)
    assert len(c["classes"]) == 32 and c["can_add"] is False and c["default_classroom"] == "Room 104"
    mine = {x["id"]: x["can_edit"] for x in c["classes"]}
    assert mine["cls-rm104-rdg"] is True and mine["cls-rm101-rdg"] is False
    assert all(not x["can_edit"] for x in env.setup.classes_view(RBT)["classes"])
    assert env.setup.classes_view(ADMIN)["can_add"] is True
    row = next(x for x in c["classes"] if x["id"] == "cls-rm104-rdg")
    assert {"student_id": DOC, "name": "Doc D."} in row["students"]
    assert "Dwarf" not in json.dumps(row["students"])          # first name + last initial, like the other lists
    assert row["domain_name"] == "Reading & Phonics" and row["schedule_text"] == "Mon–Fri 8:20 AM, 90 min"
    assert [t["name"] for t in row["teachers"]] == ["Alex Morgan", "Flynn Rider"]


def test_classes_seed_only_for_the_demo_school(env, monkeypatch):
    monkeypatch.setenv("SCHOOL_PROFILE", "demo-school")
    assert env.setup.classes_view(ADMIN)["classes"] == []
    assert len(env.setup.domains_view(ADMIN)["domains"]) == 6


# ---------------------------------------------------------------- domains: admin edits

def test_admin_adds_edits_and_removes_a_domain_journaled_ids_only(env):
    v = env.setup.add_domain(ADMIN, {"version": "seed", "domain": {
        "name": "  Music  ", "methodology": "Rhythm first", "methodology_detail": "Clap, then read.",
        "programs": "", "book": ""}})
    new = next(d for d in v["domains"] if d["name"] == "Music")
    assert new["id"] == "dom-music" and v["version"].startswith("domains-")
    v = env.setup.edit_domain(ADMIN, "dom-music", {"version": v["version"], "domain": {**new, "programs": "Kodaly"}})
    assert next(d for d in v["domains"] if d["id"] == "dom-music")["programs"] == "Kodaly"
    v = env.setup.remove_domain(ADMIN, "dom-music", {"version": v["version"]})
    assert [d["name"] for d in v["domains"]] == DOC_NAMES
    acts = [(r["action"], r["record_id"], r["record_type"], (r["details"] or {}).get("fields")) for r in rows(env)
            if r["action"].startswith("domain_")]
    assert acts == [("domain_added", "dom-music", "domain", "name.methodology.methodology_detail"),
                    ("domain_edited", "dom-music", "domain", "programs"),
                    ("domain_removed", "dom-music", "domain", None)]
    assert "Kodaly" not in json.dumps(rows(env)) and "Rhythm" not in json.dumps(rows(env))
    recs = env.store.list("school_setup/domains-")
    assert len(recs) == 3                                         # one create-only record per change


def test_stale_saves_and_duplicates_are_refused(env):
    v = env.setup.add_domain(ADMIN, {"version": "seed", "domain": {"name": "Music"}})
    for body, code in (({"version": "seed", "domain": {"name": "Art"}}, "STALE_VERSION"),
                       ({"version": v["version"], "domain": {"name": "mathematics"}}, "DOMAIN_EXISTS"),
                       ({"domain": {"name": "Art"}}, "VERSION_REQUIRED"),
                       ({"version": v["version"], "domain": {"name": ""}}, "BAD_NAME"),
                       ({"version": v["version"], "domain": {"name": "Doc's way"}}, "DOMAIN_HAS_NAMES"),
                       ({"version": v["version"], "domain": {"name": "Art", "methodology": "ask snowwhite@staff.example"}},
                        "DOMAIN_HAS_NAMES")):
        with pytest.raises(ApiError) as e:
            env.setup.add_domain(ADMIN, body)
        assert e.value.code == code, body


def test_teachers_and_rbts_cannot_change_domains(env):
    for actor in (SNOW_T, RBT, SNOW):
        for f, args in ((env.setup.add_domain, ({"version": "seed", "domain": {"name": "X"}},)),
                        (env.setup.edit_domain, ("dom-mathematics", {"version": "seed", "domain": {"name": "X"}})),
                        (env.setup.remove_domain, ("dom-mathematics", {"version": "seed"}))):
            with pytest.raises(ApiError) as e:
                f(actor, *args)
            assert e.value.http == 403
    assert env.store.list("school_setup/") == []


def test_a_domain_in_use_cannot_be_removed(env):
    with pytest.raises(ApiError) as e:
        env.setup.remove_domain(ADMIN, "dom-mathematics", {"version": "seed"})
    assert e.value.code == "DOMAIN_IN_USE"
    v = env.setup.remove_domain(ADMIN, "dom-physical-creative-arts", {"version": "seed"})
    assert "Physical & Creative Arts" not in [d["name"] for d in v["domains"]]


# ---------------------------------------------------------------- classes

def klass(**kw):
    return {"name": "Room 104 Science", "classroom": "Room 104", "teacher_ids": [SNOW], "domain_id": "dom-science-social-studies",
            "student_ids": [DOC], "schedule": [{"days": ["Tue", "Thu"], "start": "13:15", "minutes": 60},
                                               {"days": ["Fri"], "start": "09:50", "minutes": 45}], **kw}


def test_admin_creates_edits_and_deletes_a_class_with_several_meeting_patterns(env):
    v = env.setup.add_class(ADMIN, {"version": "seed", "class": klass()})
    c = next(x for x in v["classes"] if x["name"] == "Room 104 Science")
    assert c["schedule"] == klass()["schedule"] and c["label"] == "Fictional sample data" and len(v["classes"]) == 33
    assert c["schedule_text"] == "Tue, Thu 1:15 PM, 60 min; Fri 9:50 AM, 45 min"
    v = env.setup.edit_class(ADMIN, c["id"], {"version": v["version"], "class": klass(student_ids=[DOC, "stu_0002"])})
    v = env.setup.remove_class(ADMIN, c["id"], {"version": v["version"]})
    assert len(v["classes"]) == 32
    got = [(r["action"], r["record_id"], r["details"]) for r in rows(env) if r["action"].startswith("class_")]
    assert got[0] == ("class_added", c["id"], {"classroom": "room-104", "teachers": 1, "students": 1, "meetings": 2,
                                               "domain_id": "dom-science-social-studies"})
    assert got[1][0] == "class_edited" and got[1][2]["fields"] == "student_ids" and got[1][2]["students"] == 2
    assert got[2] == ("class_removed", c["id"], None)
    assert "Doc" not in json.dumps(rows(env))


@pytest.mark.parametrize("bad,code", [
    ({"classroom": "Room 999"}, "UNKNOWN_CLASSROOM"), ({"teacher_ids": []}, "BAD_TEACHERS"),
    ({"teacher_ids": ["stf_9999"]}, "BAD_TEACHERS"), ({"student_ids": ["stu_9999"]}, "BAD_STUDENTS"),
    ({"domain_id": "dom-nope"}, "UNKNOWN_DOMAIN"), ({"name": ""}, "BAD_CLASS_NAME"),
    ({"schedule": [{"days": ["Mon"], "start": "25:00", "minutes": 30}]}, "BAD_SCHEDULE"),
    ({"schedule": [{"days": [], "start": "08:00", "minutes": 30}]}, "BAD_SCHEDULE"),
    ({"schedule": [{"days": ["Funday"], "start": "08:00", "minutes": 30}]}, "BAD_SCHEDULE"),
    ({"schedule": [{"days": ["Mon"], "start": "08:00", "minutes": 2}]}, "BAD_SCHEDULE"),
    ({"schedule": [{"days": ["Mon"], "start": "23:30", "minutes": 60}]}, "BAD_SCHEDULE"),
])
def test_class_validation(env, bad, code):
    with pytest.raises(ApiError) as e:
        env.setup.add_class(ADMIN, {"version": "seed", "class": klass(**bad)})
    assert e.value.code == code


def test_teacher_edits_only_their_own_class_and_stays_on_it(env):
    own = next(x for x in env.setup.classes() if x["id"] == "cls-rm104-mth")
    body = {k: own[k] for k in ("name", "classroom", "teacher_ids", "domain_id", "student_ids", "schedule")}
    v = env.setup.edit_class(SNOW_T, "cls-rm104-mth", {"version": "seed", "class": {**body, "name": "Room 104 Math (CPA)"}})
    assert any(x["name"] == "Room 104 Math (CPA)" for x in v["classes"])
    ver = v["version"]
    cases = [(SNOW_T, "cls-rm104-mth", {**body, "teacher_ids": [FLYNN]}, "MUST_STAY_ON_CLASS"),
             (SNOW_T, "cls-rm104-mth", {**body, "classroom": "Room 101"}, "NOT_YOUR_CLASSROOM"),
             (BELLE_T, "cls-rm104-mth", body, "NOT_CLASS_TEACHER"),
             (RBT, "cls-rm104-mth", body, "NOT_CLASS_TEACHER")]
    for actor, cid, b, code in cases:
        with pytest.raises(ApiError) as e:
            env.setup.edit_class(actor, cid, {"version": ver, "class": b})
        assert e.value.code == code
    for f, args in ((env.setup.add_class, ({"version": ver, "class": klass()},)),
                    (env.setup.remove_class, ("cls-rm104-mth", {"version": ver}))):
        with pytest.raises(ApiError) as e:
            f(SNOW_T, *args)
        assert e.value.code == "ADMIN_ONLY"
    edited = [r for r in rows(env) if r["action"] == "class_edited"]
    assert len(edited) == 1 and edited[0]["actor_id"] == "uid_snow" and edited[0]["actor_role"] == "teacher"


# ---------------------------------------------------------------- session class picker

@pytest.mark.parametrize("start,want", [
    ("2026-10-09T08:25:00-04:00", "cls-rm104-rdg"),        # Friday, Reading Block 8:20-9:50
    ("2026-10-09T10:40:00-04:00", "cls-rm104-mth"),        # Math Block 10:35-11:45
    ("2026-10-09T12:25:00-04:00", "cls-rm104-soc"),        # 5 min early for Social Skills (grace)
    ("2026-10-09T16:20:00Z", "cls-rm104-soc"),             # 12:20 Eastern given in UTC
    ("2026-10-09T10:15:00-04:00", None),                   # Related Services: no class of Room 104
    ("2026-10-09T10:05:00-04:00", None),                   # ... even 30 min before Math
    ("2026-10-10T08:25:00-04:00", None),                   # Saturday
])
def test_class_suggested_from_classroom_and_recording_time(env, start, want):
    session_at(env, start, 5)                               # a 5-minute recording
    v = env.setup.session_class(RID, SNOW_T)
    assert v["suggested_id"] == want and v["source"] == ("suggested" if want else None)
    assert [c["id"] for c in v["choices"]] == ["cls-rm104-hr", "cls-rm104-rdg", "cls-rm104-mth", "cls-rm104-soc"]
    assert v["can_change"] is True


def test_teacher_picks_the_class_logged_in_audit_and_journal(env):
    session_at(env, "2026-10-09T10:40:00-04:00")
    v = env.setup.assign(RID, SNOW_T, {"class_id": "cls-rm104-rdg"})
    assert v["class"]["id"] == "cls-rm104-rdg" and v["source"] == "teacher" and v["suggested_id"] == "cls-rm104-mth"
    (a,) = [env.store.read_json(n) for n in env.store.list(f"audit/{RID}/") if "_session_class_assigned_" in n]
    assert a["class_id"] == "cls-rm104-rdg" and a["actor_id"] == "uid_snow" and "actor_email" not in a
    (j,) = rows(env, "session_class_assigned")
    assert j["details"] == {"class_id": "cls-rm104-rdg", "suggested": False} and j["record_id"] == RID
    v = env.setup.assign(RID, ADMIN, {"class_id": None})
    assert v["class"] is None and v["source"] == "cleared"
    for actor, body, code in ((BELLE_T, {"class_id": "cls-rm104-rdg"}, "NOT_SESSION_TEACHER"),
                              (RBT, {"class_id": "cls-rm104-rdg"}, "READ_ONLY_ROLE"),
                              (SNOW_T, {"class_id": "cls-rm101-rdg"}, "CLASS_NOT_IN_CLASSROOM"),
                              (SNOW_T, {"class_id": "cls-nope"}, "UNKNOWN_CLASS"),
                              (SNOW_T, {"class_id": "../x"}, "BAD_CLASS")):
        with pytest.raises(ApiError) as e:
            env.setup.assign(RID, actor, body)
        assert e.value.code == code
    assert env.setup.session_class(RID, BELLE_T)["can_change"] is False


# ---------------------------------------------------------------- materials get the domain (and nothing else new)

def mats_for(env):
    spy = Spy()
    return Materials(env.notes, ai=mai.MaterialAI(call=spy.call, svg=spy.svg), sender=mailer.Outbox(),
                     domain_context=env.setup.domain_context, approach_view=env.setup.approach_view), spy


def test_materials_prompt_gets_the_class_domain_and_methodology_only(env):
    session_at(env, "2026-10-09T10:40:00-04:00")            # Math Block -> Mathematics (CPA)
    mats, spy = mats_for(env)
    view = mats.view(RID, SNOW_T)
    assert view["teaching_approach"]["class_name"] == "Room 104 Math"
    assert view["teaching_approach"]["domain"] == "Mathematics"
    mats.make(RID, SNOW_T, {"note_index": 0, "type": "worksheet", "count": 4, "topic": "counting",
                            "student_ids": [DOC], "due": ""})
    (brief,) = [p for w, p in spy.calls if w == "brief"]
    assert brief["class_domain"] == (
        "Mathematics: Concrete-Pictorial-Abstract (CPA) Conceptual math instruction focusing on place-value disks, "
        "physical manipulatives, and visual models before abstract formulas.")
    assert set(brief) == {"type", "type_label", "count", "topic", "due", "teacher_request", "grades", "students",
                          "class_domain"}
    assert "Eureka" not in json.dumps(brief) and "Van de Walle" not in json.dumps(brief)   # programs / book stay home
    assert "Room 104" not in json.dumps(brief)                                              # nor the class name
    assert all(p.split("\n")[2].startswith("Teaching Approach: Mathematics: Concrete-Pictorial-Abstract")
               for _, p in spy.svgs)
    doc = env.store.read_json(next(n for n in env.store.list("materials/") if n.endswith(".json") and "/mat_" in n))
    assert doc["class_domain"].startswith("Mathematics: Concrete-Pictorial-Abstract")


def test_materials_use_the_picked_class_over_the_suggestion_and_none_without_one(env):
    session_at(env, "2026-10-09T10:15:00-04:00", 5)         # no class by time
    mats, spy = mats_for(env)
    assert mats.view(RID, SNOW_T)["teaching_approach"] is None
    env.setup.assign(RID, SNOW_T, {"class_id": "cls-rm104-rdg"})
    mats.make(RID, SNOW_T, {"note_index": 0, "type": "flashcards", "count": 4, "topic": "short a",
                            "student_ids": [], "due": ""})
    (brief,) = [p for w, p in spy.calls if w == "brief"]
    assert brief["class_domain"].startswith("Reading & Phonics: Orton-Gillingham")
    assert "Orton-Gillingham" in spy.svgs[0][1]


def test_user_prompt_keeps_davids_shape_with_the_approach_line():
    p = msvg.user_prompt("Make cards", "flashcards", 1, "thick borders", 3, "Mathematics: CPA")
    lines = p.split("\n")
    assert lines[0] == 'Teacher Request: "Make cards"' and lines[1].startswith("Activity Type: ")
    assert lines[2:] == ["Teaching Approach: Mathematics: CPA", "Draft Variation: 1 of 3 (thick borders)"]
    assert "Teaching Approach" not in msvg.user_prompt("Make cards", "flashcards", 1, "thick borders")


# ---------------------------------------------------------------- HTTP

def test_http_laptop_demo_routes(tmp_path):
    store = LocalStore(tmp_path)
    seed(store)
    srv = build_server(store, 0, "local", journal=MemoryJournal())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        h = {"X-Reviewer-Id": SNOW}
        st, body, _ = call(base, "/api/domains", headers=h)
        assert st == 200 and len(json.loads(body)["domains"]) == 6
        st, body, _ = call(base, "/api/classes", headers=h)
        c = json.loads(body)
        assert st == 200 and len(c["classes"]) == 32
        st, body, _ = call(base, "/api/domains", method="POST", headers=h,
                           body={"version": "seed", "domain": {"name": "Music"}})
        assert st == 403 and json.loads(body) == {"error": "ADMIN_ONLY"}
        own = next(x for x in c["classes"] if x["id"] == "cls-rm104-hr")
        b = {k: own[k] for k in ("name", "classroom", "teacher_ids", "domain_id", "student_ids", "schedule")}
        st, body, _ = call(base, "/api/classes/cls-rm104-hr", method="POST", headers=h,
                           body={"version": c["version"], "class": {**b, "name": "Room 104 Morning Meeting"}})
        assert st == 200 and any(x["name"] == "Room 104 Morning Meeting" for x in json.loads(body)["classes"])
        st, body, _ = call(base, "/api/classes/cls-rm104-hr/delete", method="POST", headers=h, body={"version": "x"})
        assert st == 403
        st, body, _ = call(base, f"/api/sessions/{RID}/class", headers=h)
        assert st == 200 and json.loads(body)["classroom"] == "Room 104"
        st, body, _ = call(base, f"/api/sessions/{RID}/class", method="POST", headers=h, body={"class_id": "cls-rm104-mth"})
        assert st == 200 and json.loads(body)["class"]["id"] == "cls-rm104-mth"
        st, _, _ = call(base, "/api/classes/BAD!", method="POST", headers=h, body={"version": "x"})
        assert st == 404
    finally:
        srv.shutdown()


def test_http_hosted_admin_only_and_rbt_read_only(hosted):  # noqa: F811
    st, body, _ = call(hosted, "/api/domains", tok=token(RBT_EMAIL, uid="uidrbt"))
    assert st == 200 and json.loads(body)["can_edit"] is False
    st, body, _ = call(hosted, "/api/domains", method="POST", tok=token(RBT_EMAIL, uid="uidrbt"),
                       body={"version": "seed", "domain": {"name": "Music"}})
    assert st == 403 and json.loads(body) == {"error": "READ_ONLY_ROLE"}
    st, body, _ = call(hosted, "/api/domains", method="POST", tok=token(TEACHER_EMAIL, uid="uidteacher"),
                       body={"version": "seed", "domain": {"name": "Music"}})
    assert st == 403 and json.loads(body) == {"error": "ADMIN_ONLY"}
    st, body, _ = call(hosted, "/api/domains", method="POST", tok=token(H_ADMIN, uid="uidadmin"),
                       body={"version": "seed", "domain": {"name": "Music"}})
    assert st == 200 and "Music" in [d["name"] for d in json.loads(body)["domains"]]
    st, _, _ = call(hosted, "/api/classes")
    assert st == 401
