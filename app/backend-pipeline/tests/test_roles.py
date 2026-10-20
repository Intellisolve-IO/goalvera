"""Who may do what on the hosted review screen.

  * Every approved role (admin, teacher, RBT) reads every classroom and session.
  * Edit / approve / export / classroom change: the session's teacher, the
    student's own teacher, or an admin. Enforced on the server.
  * RBT: read-only. Every write and the PDF export are refused (403).
  * Parent emails: only that student's teachers and admins.
  * A teacher can move a session to another classroom; the move is logged
    (who, when, from, to) and the session then belongs to that classroom's teacher.
"""
from __future__ import annotations

import json

import pytest

from test_hosted_auth import (ADMIN, OTHER, RID, TEACHER_EMAIL, Notes, call, hdr,  # noqa: F401
                              token)
from test_hosted_auth import authn, hosted, store  # noqa: F401  (pytest fixtures)
from review_api.notes import ApiError

READING_RID = "rec_laptop_demo_20260929_090000_Reading_3A"
ART_RID = "rec_laptop_demo_20260929_100000_Art_9Z"
LOOSE_RID = "rec_laptop_demo_20260929_110000_Gym"
ART_TEACHER = "art-teacher@example.org"
SPEECH_TEACHER = "speech-teacher@example.org"
RBT_EMAIL = "demo-rbt@example.org"
ALL = {RID, READING_RID, ART_RID, LOOSE_RID}


def _clone(store, rid, **fields):
    draft = json.loads(json.dumps(store.read_json(f"drafts/{RID}.json")))
    tr = json.loads(json.dumps(store.read_json(f"transcripts/{RID}.json")))
    draft["recording_id"] = tr["recording_id"] = rid
    draft["analysis"]["draft_parent_email"] = f"Hello family, parent email draft for {rid}."
    for rec in (draft["session"], tr):
        rec.update(student_id=None, student_first_name="Student", student_last_initial="", **fields)
    store.write_json(f"drafts/{rid}.json", draft)
    store.write_json(f"transcripts/{rid}.json", tr)


@pytest.fixture()
def world(store):
    _clone(store, READING_RID, teacher_id="usr_demo_sarah", teacher_name="Sarah Jenkins",
           class_code="Reading 3A", needs_class_assignment=False)
    _clone(store, ART_RID, teacher_id="usr_art", teacher_name="Jordan Rivera",
           class_code="Art 9Z", needs_class_assignment=False)
    _clone(store, LOOSE_RID, teacher_id=None, teacher_name=None, class_code="Gym",
           needs_class_assignment=True)
    r = store.read_json("config/demo_roster.json")
    r["class_codes"]["Art 9Z"] = {"teacher_id": "usr_art", "teacher_name": "Jordan Rivera", "iep_goals": []}
    r["teachers"]["usr_art"] = {"teacher_name": "Jordan Rivera", "emails": [ART_TEACHER]}
    r["teachers"]["usr_speech"] = {"teacher_name": "Pat Speech", "emails": [SPEECH_TEACHER]}
    r["students"] = {"stu_101": {"teacher_ids": ["usr_speech"]}}      # Marcus's other own teacher
    r["rbts"]["rbt_demo_rm12"]["emails"] = [RBT_EMAIL]
    store.write_json("config/demo_roster.json", r)
    allow = store.read_json("config/review_allowlist.json")
    allow["users"] += [{"email": ART_TEACHER, "role": "teacher"}, {"email": SPEECH_TEACHER, "role": "teacher"},
                       {"email": RBT_EMAIL, "role": "rbt"}]
    store.write_json("config/review_allowlist.json", allow)
    return store


def who(authn, email):
    return authn.require(hdr(token(email, uid="uid" + email.split("@")[0].replace("-", ""))))


def parent_texts(store):
    return [store.read_json(f"drafts/{r}.json")["analysis"]["draft_parent_email"] for r in ALL]


# ---------------------------------------------------------------- reading

def test_every_role_reads_every_classroom(world, authn):
    notes = Notes(world)
    for email in (ADMIN, TEACHER_EMAIL, ART_TEACHER, SPEECH_TEACHER, RBT_EMAIL, OTHER):
        assert {s["recording_id"] for s in notes.list_sessions(who(authn, email))} == ALL, email


def test_home_classrooms_and_read_only_flag(world, hosted):
    def me(email):
        st, body, _ = call(hosted, "/api/me", tok=token(email))
        assert st == 200
        return json.loads(body)
    assert me(TEACHER_EMAIL)["home_classrooms"] == ["Circle Time", "Math 2B", "RM12", "Reading 3A"]
    assert me(ART_TEACHER)["home_classrooms"] == ["Art 9Z"]
    assert me(ADMIN)["home_classrooms"] == [] and me(ADMIN)["read_only"] is False
    rbt = me(RBT_EMAIL)
    assert rbt["role"] == "rbt" and rbt["read_only"] is True and rbt["home_classrooms"] == ["RM12"]


# ---------------------------------------------------------------- who may edit / approve

def test_only_own_teachers_or_admin_may_act(world, authn):
    notes = Notes(world)
    rows = lambda email: {s["recording_id"]: s for s in notes.list_sessions(who(authn, email))}  # noqa: E731
    sarah, art, speech, admin, rbt = (rows(e) for e in (TEACHER_EMAIL, ART_TEACHER, SPEECH_TEACHER, ADMIN, RBT_EMAIL))
    assert {r for r, s in sarah.items() if s["viewer_may_act"]} == {RID, READING_RID}
    assert {r for r, s in art.items() if s["viewer_may_act"]} == {ART_RID}
    assert {r for r, s in speech.items() if s["viewer_may_act"]} == {RID}        # the student's own teacher
    assert all(s["viewer_may_act"] for s in admin.values())
    assert not any(s["viewer_may_act"] or s["can_approve"] or s["viewer_may_change_classroom"] for s in rbt.values())
    assert all(s["viewer_read_only"] for s in rbt.values())

    analysis = world.read_json(f"drafts/{READING_RID}.json")["analysis"]
    with pytest.raises(ApiError) as e:
        notes.approve(READING_RID, who(authn, ART_TEACHER), analysis)
    assert (e.value.http, e.value.code) == (403, "NOT_SESSION_TEACHER")
    with pytest.raises(ApiError) as e:
        notes.approve(RID, who(authn, RBT_EMAIL), world.read_json(f"drafts/{RID}.json")["analysis"])
    assert (e.value.http, e.value.code) == (403, "READ_ONLY_ROLE")
    rec = notes.approve(RID, who(authn, SPEECH_TEACHER), world.read_json(f"drafts/{RID}.json")["analysis"])
    assert rec["approved_by_email"] == SPEECH_TEACHER


# ---------------------------------------------------------------- parent emails

def test_per_session_parent_email_is_hidden_from_everyone_but_kept_in_storage(world, authn):
    """ the draft parent email is off the session screen and PDF for everyone (it belongs
    to the weekly student summary). Drafts already generated stay in storage, hidden."""
    notes = Notes(world)

    def has_email(email):
        return {s["recording_id"] for s in notes.list_sessions(who(authn, email))
                if "draft_parent_email" in s["processed_analysis"]}
    for email in (ADMIN, TEACHER_EMAIL, ART_TEACHER, SPEECH_TEACHER, RBT_EMAIL, OTHER):
        assert has_email(email) == set(), email
    assert all(parent_texts(world))                                   # still stored, untouched
    hidden = [s for s in notes.list_sessions(who(authn, ART_TEACHER)) if s["recording_id"] == RID][0]
    assert hidden["parent_contact_hidden"] is True and hidden["processed_analysis"]["general_session_summary"]
    # approving without it keeps the stored draft's text in the approval record (hidden), not "edited"
    stored_draft = world.read_json(f"drafts/{RID}.json")["analysis"]
    analysis = {k: v for k, v in stored_draft.items() if k != "draft_parent_email"}
    ap = notes.approve(RID, who(authn, TEACHER_EMAIL), analysis)
    stored = world.read_json(f"approvals/{RID}.json")
    assert stored["analysis"]["draft_parent_email"] == stored_draft["draft_parent_email"]
    assert stored["edited"] is False and ap
    assert "draft_parent_email" not in notes.session(RID, who(authn, TEACHER_EMAIL))["processed_analysis"]


def test_rbt_signed_in_sees_everything_but_no_parent_email_anywhere(world, hosted, authn):
    """Sign in as the demo RBT and walk every read endpoint."""
    tok = token(RBT_EMAIL, uid="uidRbt")
    Notes(world).approve(RID, who(authn, ADMIN), world.read_json(f"drafts/{RID}.json")["analysis"])  # PDF exists
    bodies = []
    st, body, _ = call(hosted, "/api/me", tok=tok)
    bodies.append(body)
    st, body, _ = call(hosted, "/api/sessions", tok=tok)
    assert st == 200
    bodies.append(body)
    sessions = json.loads(body)["sessions"]
    assert {s["recording_id"] for s in sessions} == ALL
    for s in sessions:
        assert s["viewer_read_only"] is True and s["can_approve"] is False and s["parent_contact_hidden"] is True
        for what in ("transcript", "audit"):
            st, body, _ = call(hosted, f"/api/sessions/{s['recording_id']}/{what}", tok=tok)
            assert st == 200, (what, body)
            bodies.append(body)
    blob = b"\n".join(bodies).decode()
    assert "parent_email" not in blob
    for text in parent_texts(world) + [world.read_json(f"approvals/{RID}.json")["analysis"]["draft_parent_email"]]:
        assert text not in blob


def test_rbt_direct_writes_and_export_are_refused(world, hosted, authn):
    Notes(world).approve(RID, who(authn, ADMIN), world.read_json(f"drafts/{RID}.json")["analysis"])
    before = sorted(world.list(""))
    tok = token(RBT_EMAIL, uid="uidRbt")
    analysis = world.read_json(f"drafts/{READING_RID}.json")["analysis"]
    edited = {**analysis, "general_session_summary": "RBT edit"}
    attempts = [
        ("POST", f"/api/sessions/{READING_RID}/approve", {"analysis": edited}),        # edit + approve
        ("POST", f"/api/sessions/{READING_RID}/classroom", {"class_code": "Art 9Z"}),  # classroom change
        ("POST", f"/api/sessions/{LOOSE_RID}/classroom", {"class_code": "RM12"}),
        ("POST", f"/api/sessions/{READING_RID}/notes", {"tag": "x"}),                  # any other write / tag
        ("POST", f"/api/sessions/{READING_RID}/suggestions", {"text": "x"}),
        ("GET", f"/api/sessions/{RID}/pdf", None),                                      # export
    ]
    for method, path, body in attempts:
        st, resp, _ = call(hosted, path, tok=tok, method=method, body=body)
        assert (st, json.loads(resp)) == (403, {"error": "READ_ONLY_ROLE"}), path
    assert sorted(world.list("")) == before                                            # nothing written


def test_rbt_iep_access_follows_the_school_setting(world, authn):
    notes = Notes(world, roster_ttl=0)
    rbt = who(authn, RBT_EMAIL)
    row = lambda: [s for s in notes.list_sessions(rbt) if s["recording_id"] == RID][0]  # noqa: E731
    assert row()["processed_analysis"]["goal_evaluations"]                       # default: read
    r = world.read_json("config/demo_roster.json")
    r["settings"] = {"iep_access": {"rbt": "none"}}
    world.write_json("config/demo_roster.json", r)
    assert row()["processed_analysis"]["goal_evaluations"] == [] and row()["iep_hidden"] is True
    sarah = [s for s in notes.list_sessions(who(authn, TEACHER_EMAIL)) if s["recording_id"] == RID][0]
    assert sarah["processed_analysis"]["goal_evaluations"]


# ---------------------------------------------------------------- classroom change

def test_teacher_moves_a_session_and_the_move_is_logged(world, authn, hosted):
    notes = Notes(world)
    sarah, art = who(authn, TEACHER_EMAIL), who(authn, ART_TEACHER)
    with pytest.raises(ApiError) as e:
        notes.change_classroom(READING_RID, art, "RM12")              # not Jordan's session
    assert e.value.http == 403
    with pytest.raises(ApiError) as e:
        notes.change_classroom(READING_RID, sarah, "Nowhere 1Z")
    assert (e.value.http, e.value.code) == (400, "UNKNOWN_CLASSROOM")
    with pytest.raises(ApiError) as e:
        notes.change_classroom(READING_RID, sarah, "reading-3a")      # same classroom, any spelling
    assert e.value.code == "SAME_CLASSROOM"

    st, body, _ = call(hosted, f"/api/sessions/{READING_RID}/classroom", tok=token(TEACHER_EMAIL, uid="uidSarah"),
                       method="POST", body={"class_code": "art 9z"})
    assert st == 200, body
    row = json.loads(body)
    assert (row["class_code"], row["teacher_id"], row["teacher_name"]) == ("Art 9Z", "usr_art", "Jordan Rivera")
    assert row["viewer_may_act"] is False and row["classroom_changed_from"] == "Reading 3A"
    [ev] = [e for e in notes.audit_trail(READING_RID) if e["event"] == "classroom_changed"]
    assert ev["actor_id"] == "uidSarah" and ev["actor_email"] == TEACHER_EMAIL and ev["at"]
    assert (ev["from_class_code"], ev["to_class_code"]) == ("Reading 3A", "Art 9Z")
    assert (ev["from_teacher_id"], ev["to_teacher_id"]) == ("usr_demo_sarah", "usr_art")

    # it now belongs to the classroom's teacher, not to whoever recorded it
    assert notes.session(READING_RID, art)["viewer_may_act"] is True
    with pytest.raises(ApiError):
        notes.change_classroom(READING_RID, sarah, "Reading 3A")
    notes.change_classroom(READING_RID, art, "Reading 3A")
    assert notes.session(READING_RID, sarah)["class_code"] == "Reading 3A"
    assert [e["to_class_code"] for e in notes.audit_trail(READING_RID)] == ["Art 9Z", "Reading 3A"]
    assert {s["recording_id"]: s["class_code"] for s in notes.list_sessions(sarah)}[READING_RID] == "Reading 3A"


def test_unassigned_session_any_teacher_may_assign(world, authn):
    notes = Notes(world)
    with pytest.raises(ApiError) as e:
        notes.change_classroom(LOOSE_RID, who(authn, OTHER), "RM12")     # allowlisted, not a roster teacher
    assert e.value.http == 403
    row = notes.change_classroom(LOOSE_RID, who(authn, ART_TEACHER), "RM12")
    assert row["needs_class_assignment"] is False and row["teacher_id"] == "usr_demo_sarah"
    assert row["student_id"] == "stu_101"                                   # the classroom's student
    assert notes.session(LOOSE_RID, who(authn, TEACHER_EMAIL))["can_approve"] is True


def test_no_classroom_change_after_approval(world, authn):
    notes = Notes(world)
    sarah = who(authn, TEACHER_EMAIL)
    notes.approve(READING_RID, sarah, world.read_json(f"drafts/{READING_RID}.json")["analysis"])
    with pytest.raises(ApiError) as e:
        notes.change_classroom(READING_RID, sarah, "Math 2B")
    assert (e.value.http, e.value.code) == (409, "ALREADY_APPROVED")
