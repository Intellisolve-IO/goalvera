"""Incident names + Eastern clock time (privacy guardrails),
and recorder Bug markers (firmware 0.6.7 writes type "bug").

  * each incident gets a short AI name (80 characters or fewer) from its own notes; the same name
    scrub as the rest of the report; failing it -> plain "Incident N"
  * observed behavior only; a judgment / intent / diagnosis word -> regenerate once -> "Incident N"
  * teachers can edit the name before approval; an edited name is never overwritten
  * clock time = recorder start time + offset, in Eastern ("1:02 PM"), DST-correct; offset only
    without a trustworthy start time
  * the file name carries the clock time, never the AI name
  * a recorder "bug" marker (any case) never shows as a marker or in a PDF; it becomes a pending
    bug report (note "Reported from recorder", screen "recorder", device id, firmware, start time,
    clock time of the marker)
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from test_hosted_auth import ADMIN, OTHER, RID, TEACHER_EMAIL, call, hdr, token  # noqa: F401
from test_hosted_auth import authn, store  # noqa: F401  (pytest fixtures)
from test_journal import DOB, LAST_NAME, SCHOOL_ID, serve
from test_roles import RBT_EMAIL, world  # noqa: F401  (pytest fixture)
from test_review_pdf import pdf_text
from pipeline_service.records import markers_from_sidecar, recorder_facts
from review_api.bug_reports import build_issue, never_values
from review_api.incident_names import (MAX_NAME, NAME_SYSTEM, Namer, blocked, clean_name, incident_key,
                                       usable)
from review_api.incident_print import build_print, clock_at, recording_start, time_line
from review_api.journal import MemoryJournal, Recorder
from review_api.notes import ApiError, Notes

NEVER = ("Marcus", LAST_NAME, SCHOOL_ID, DOB, "Jenkins", "Sarah", "@")
INCIDENTS = [
    {"timestamp_seconds": 754, "antecedent": f"Marcus {LAST_NAME} was asked by Ms Jenkins to start reading.",
     "behavior": "Marcus left his seat and walked to the window; did not return when redirected twice.",
     "consequence_intervention": "Sarah Jenkins offered a break card; returned after 2 minutes."},
    {"timestamp_seconds": 3, "antecedent": "Fire drill tone.", "behavior": "Covered his ears and put his head down.",
     "consequence_intervention": "Headphones offered."},
]


def tok(email):
    return token(email, uid="uid" + email.split("@")[0].replace("-", ""))


@pytest.fixture()
def school(world):
    r = world.read_json("config/demo_roster.json")
    r["teachers"]["usr_demo_sarah"]["emails"] = [TEACHER_EMAIL]
    r["students"]["stu_101"].update(first_name="Marcus", last_name=LAST_NAME, dob=DOB, school_id=SCHOOL_ID)
    world.write_json("config/demo_roster.json", r)
    d = world.read_json(f"drafts/{RID}.json")
    d["analysis"]["incident_analysis"] = INCIDENTS
    d["session"]["timestamp_start"] = "2026-10-08T12:49:26"          # 12:49:26 PM Eastern (file name)
    d["session"]["start_time"] = None                                # older drafts: no sidecar start time
    world.write_json(f"drafts/{RID}.json", d)
    return world


class FakeAI:
    """Records what it was sent; answers from a script (one reply per call)."""
    def __init__(self, *replies):
        self.replies, self.sent = list(replies), []

    def __call__(self, system, payload, schema):
        self.sent.append((system, json.loads(json.dumps(payload))))
        r = self.replies.pop(0)
        return r(payload) if callable(r) else r


def named(*names):
    return lambda p: {"names": [{"index": inc["index"], "name": n} for inc, n in zip(p["incidents"], names)]}


# ---------------------------------------------------------------- the name scrub and the rules

def never_for(store):
    return never_values(store.read_json("config/demo_roster.json"), store.read_json(f"drafts/{RID}.json")["session"])


def test_name_is_scrubbed_short_and_falls_back(school):
    never = never_for(school)
    assert clean_name("Left seat and refused redirection during reading.", never) == \
        "Left seat and refused redirection during reading"
    for leak in ("Marcus left his seat", f"{LAST_NAME} left the room", f"Left seat ({SCHOOL_ID})",
                 f"Born {DOB}", "Left seat when Ms Jenkins asked", "Emailed sarah@x.org", "Student 4482913 left",
                 "[student] left seat", "", "   ", None, 42):
        assert clean_name(leak, never) is None, leak
    long = "Walked to the window and stood there without answering when redirected by staff during the reading block"
    out = clean_name(long, never)
    assert len(out) <= MAX_NAME and long.startswith(out) and not out.endswith(" ")
    assert clean_name("Incident 1: covered ears", never) == "Covered ears"


def test_ops_blocklist_no_judgment_intent_or_diagnosis():
    for bad in ("Defiant during reading", "Aggressive outburst at lunch", "Angry about the timer",
                "Disruptive in group", "Manipulative bargaining", "Deliberately knocked over cup",
                "ADHD behavior during math", "Tried to avoid work", "Non-compliant with directions"):
        assert blocked(bad), bad
    for ok in ("Left seat and refused redirection", "Covered ears and put head down",
               "Asked to add 3 + 4 and pushed paper away", "Odd numbers worksheet pushed away"):
        assert not blocked(ok), ok
    # the rule is in the prompt itself
    assert "ONLY behavior that was observed" in NAME_SYSTEM and "defiant" in NAME_SYSTEM and "No diagnoses" in NAME_SYSTEM


def test_blocked_word_regenerates_once_then_falls_back(school):
    ai = FakeAI(named("Defiant at the window", "Covered ears and put head down"),
                named("Aggressive again"))
    notes = Notes(school, name_options={"call": ai})
    out = notes.incident_names(RID, None)
    assert len(ai.sent) == 2                                   # one regeneration, only for the blocked one
    assert [i["index"] for i in ai.sent[1][1]["incidents"]] == [0]
    assert "not allowed" in ai.sent[1][0]
    by_n = {i["number"]: i for i in out["incidents"]}
    assert by_n[2]["title"] == "Incident 2"                    # 754 s is incident 2; still blocked -> fallback
    assert by_n[1]["title"] == "Incident 1: Covered ears and put head down"


def test_blocked_word_then_a_clean_regeneration_is_used(school):
    ai = FakeAI(named("Defiant at the window", "Covered ears"), named("Walked to the window and did not return when redirected"))
    out = Notes(school, name_options={"call": ai}).incident_names(RID, None)
    assert {i["number"]: i["title"] for i in out["incidents"]}[2] == \
        "Incident 2: Walked to the window and did not return when redirected"


def test_what_goes_to_the_ai_is_masked_and_names_are_stored_once(school):
    ai = FakeAI(named("Left seat and walked to the window", "Covered ears and put head down"))
    notes = Notes(school, name_options={"call": ai})
    notes.incident_names(RID, None)
    sent = json.dumps(ai.sent[0][1])
    for never in NEVER:
        assert never.lower() not in sent.lower(), never
    assert "the student" in sent and "the teacher" in sent
    stored = [school.read_json(n) for n in school.list(f"incident_names/{RID}/")]
    assert sorted(d["name"] for d in stored) == ["Covered ears and put head down", "Left seat and walked to the window"]
    notes.incident_names(RID, None)                            # second view: no new call
    assert len(ai.sent) == 1
    s = notes.session(RID, None)
    names = {i["timestamp_seconds"]: (i.get("name"), i.get("name_source")) for i in s["processed_analysis"]["incident_analysis"]}
    assert names == {754: ("Left seat and walked to the window", "ai"), 3: ("Covered ears and put head down", "ai")}


def test_a_name_that_fails_the_scrub_falls_back(school):
    ai = FakeAI(named("Marcus left his seat", f"Covered ears, {SCHOOL_ID}"))
    out = Notes(school, name_options={"call": ai}).incident_names(RID, None)
    assert [i["title"] for i in out["incidents"]] == ["Incident 2", "Incident 1"]
    for d in (school.read_json(n) for n in school.list(f"incident_names/{RID}/")):
        assert d["name"] is None and d["reason"] == "scrub"


def test_no_ai_means_plain_numbers_and_a_retry_later(school):
    def down(*a):
        raise RuntimeError("no model")
    out = Notes(school, name_options={"call": down}).incident_names(RID, None)
    assert [i["title"] for i in out["incidents"]] == ["Incident 2", "Incident 1"]
    assert school.list(f"incident_names/{RID}/") == []          # nothing stored: next view tries again


def test_teacher_edit_wins_and_is_checked(school, authn):
    ai = FakeAI(named("Left seat and walked to the window", "Covered ears and put head down"))
    notes = Notes(school, name_options={"call": ai})
    notes.incident_names(RID, None)
    teacher = authn.require(hdr(tok(TEACHER_EMAIL)))
    shown = notes.session(RID, teacher)["processed_analysis"]
    # approving the AI names unchanged is not an edit
    edited = json.loads(json.dumps(shown))
    edited["incident_analysis"][1].update(name="Covered ears during the fire drill tone", name_source="teacher")
    for bad, code in (("Defiant at the window", "INCIDENT_NAME_NOT_ALLOWED"), ("Marcus at the window", "INCIDENT_NAME_NOT_ALLOWED"),
                      ("x" * 81, "INVALID_NOTES")):
        trial = json.loads(json.dumps(edited))
        trial["incident_analysis"][0].update(name=bad, name_source="teacher")
        with pytest.raises(ApiError) as e:
            notes.approve(RID, teacher, trial)
        assert e.value.code == code, bad
    ap = notes.approve(RID, teacher, edited)
    assert ap["edited"] is True
    stored = school.read_json(f"approvals/{RID}.json")["analysis"]["incident_analysis"]
    assert stored[1]["name"] == "Covered ears during the fire drill tone" and stored[1]["name_source"] == "teacher"
    assert stored[0]["name"] == "Left seat and walked to the window"
    # a later AI pass never overwrites the teacher's name
    notes.incident_names(RID, None)
    after = notes.session(RID, teacher)["processed_analysis"]["incident_analysis"]
    assert after[1]["name"] == "Covered ears during the fire drill tone"


def test_ai_names_alone_do_not_mark_the_notes_edited(school, authn):
    notes = Notes(school, name_options={"call": FakeAI(named("Left seat", "Covered ears"))})
    notes.incident_names(RID, None)
    teacher = authn.require(hdr(tok(TEACHER_EMAIL)))
    ap = notes.approve(RID, teacher, notes.session(RID, teacher)["processed_analysis"])
    assert ap["edited"] is False


# ---------------------------------------------------------------- clock time

def test_eastern_clock_time_and_dst_edges():
    s = recording_start({"timestamp_start": "2026-10-08T12:49:26"})
    assert s.utcoffset() == dt.timedelta(hours=-4)
    assert clock_at(s, 754) == "1:02 PM" and time_line(s, 754) == "1:02 PM \u00b7 12:34 into recording"
    w = recording_start({"timestamp_start": "2026-12-08T12:49:26"})
    assert w.utcoffset() == dt.timedelta(hours=-5) and clock_at(w, 754) == "1:02 PM"
    # spring forward: 1:59 AM EST + 90 s -> 3:00 AM EDT; a start inside the gap is not trusted
    assert clock_at(recording_start({"timestamp_start": "2026-03-08T01:59:00"}), 90) == "3:00 AM"
    assert recording_start({"timestamp_start": "2026-03-08T02:30:00"}) is None
    # fall back: 1:59 AM EDT + 90 s -> 1:00 AM EST (the second 1 AM)
    assert clock_at(recording_start({"timestamp_start": "2026-11-01T01:59:00"}), 90) == "1:00 AM"
    # the sidecar's own start time (with its offset) wins; time_valid false -> no clock time
    assert clock_at(recording_start({"start_time": "2026-10-08T16:49:26Z", "timestamp_start": "2026-10-08T01:00:00"}), 754) == "1:02 PM"
    assert clock_at(recording_start({"start_time": "2026-10-08T12:49:26-0400"}), 0) == "12:49 PM"
    assert recording_start({"timestamp_start": "2026-10-08T12:49:26", "start_time_valid": False}) is None
    for bad in ({}, {"timestamp_start": "1970-01-01T00:00:10"}, {"timestamp_start": "garbage"}):
        assert recording_start(bad) is None
    assert time_line(None, 754) == "12:34 into recording"


def test_print_view_title_time_and_file_name(school, authn):
    notes = Notes(school, name_options={"call": FakeAI(named("Left seat and walked to the window", "Covered ears"))})
    notes.incident_names(RID, None)
    teacher = authn.require(hdr(tok(TEACHER_EMAIL)))
    for stage in ("draft", "approved"):
        if stage == "approved":
            notes.approve(RID, teacher, notes.session(RID, teacher)["processed_analysis"])
        m = notes.incident_print(RID, teacher, 2)
        assert m["heading"] == "Incident 2: Left seat and walked to the window"
        assert m["time_line"] == "1:02 PM \u00b7 12:34 into recording" and m["clock_time"] == "1:02 PM"
        assert m["file_title"] == ("DRAFT_" if stage == "draft" else "") + "Incident-2_1-02PM_RM12_2026-10-08"
        assert "Left" not in m["file_title"] and "window" not in m["file_title"]
        blob = json.dumps(m)
        for never in (SCHOOL_ID, LAST_NAME, DOB, "@"):
            assert never not in blob
        for never in NEVER:
            assert never not in m["title"] and never not in m["file_title"] and never not in m["heading"]
    text = pdf_text(notes.pdf(RID, teacher)[0])
    assert "Incident 1: Covered ears\n12:49 PM \u00b7 0:03 into recording" in text
    assert "Incident 2: Left seat and walked to the window\n1:02 PM \u00b7 12:34 into recording" in text
    heads = [ln for ln in text.splitlines() if ln.startswith("Incident ") and ":" in ln or "into recording" in ln]
    assert len(heads) == 4
    for never in NEVER:                                      # the titles / times carry no name or id
        assert all(never not in ln for ln in heads), never
    assert "Parent update" not in text


def test_a_name_with_forbidden_data_never_reaches_paper():
    sess = {"student_first_name": "Marcus", "student_last_initial": "A.", "student_last_name": LAST_NAME,
            "class_code": "RM12", "timestamp_start": "2026-10-08T12:49:26", "school_id": SCHOOL_ID}
    m = build_print(session=sess, approval=None, number=1, recording_id="rec_x",
                    draft={"incident_analysis": [{"timestamp_seconds": 3, "name": f"{LAST_NAME} left the room"}]})
    assert m["heading"] == "Incident 1" and m["name"] is None


def test_http_incident_names_for_anyone_who_may_read(school, authn):
    jrnl = MemoryJournal()
    srv, base = serve(school, authn, jrnl)
    try:
        st, body, _ = call(base, f"/api/sessions/{RID}/incident_names", tok=tok(RBT_EMAIL))
        assert st == 200
        rows = json.loads(body)["incidents"]
        assert [r["number"] for r in rows] == [2, 1] and all(r["title"].startswith("Incident ") for r in rows)
        for never in NEVER:
            assert never.encode() not in body
        st, _, _ = call(base, f"/api/sessions/{RID}/incident_names", tok=tok(OTHER))
        assert st in (200, 404)
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- recorder Bug markers (firmware 0.6.7)

def test_pipeline_keeps_recorder_bug_markers_and_facts():
    sc = {"start_time": "2026-10-08T12:49:26-0400", "time_valid": True, "firmware_version": "0.6.7",
          "markers": [{"type": "bug", "offset_ms": 83120, "wall_time": "2026-10-08T13:02:11-0400"},
                      {"type": "bug", "offset_ms": 5000, "wall_time": None},
                      {"type": "Incident", "offset_ms": 3000, "wall_time": "2026-10-08T12:49:29-0400"}]}
    assert markers_from_sidecar(sc) == [{"type": "Incident", "offset_seconds": 3.0},
                                        {"type": "bug", "offset_seconds": 5.0},
                                        {"type": "bug", "offset_seconds": 83.1, "wall_time": "2026-10-08T13:02:11-0400"}]
    assert recorder_facts(sc) == {"start_time": "2026-10-08T12:49:26-0400", "start_time_valid": True,
                                  "firmware_version": "0.6.7"}
    assert recorder_facts({"start_time": "x", "time_valid": "yes", "firmware_version": "rm -rf /"}) == \
        {"start_time": None, "start_time_valid": None, "firmware_version": None}


def _with_device_bugs(store):
    d = store.read_json(f"drafts/{RID}.json")
    d["session"].update(device_id="rec-a1b2c3", firmware_version="0.6.7", start_time="2026-10-08T12:49:26-0400",
                        markers=[{"type": "Incident", "offset_seconds": 3.0},
                                 {"type": "bug", "offset_seconds": 83.1, "wall_time": "2026-10-08T13:02:11-0400"},
                                 {"type": "BUG", "offset_seconds": 200.0}])
    store.write_json(f"drafts/{RID}.json", d)


def test_recorder_bug_markers_become_pending_reports_and_stay_hidden(school, authn):
    _with_device_bugs(school)
    jrnl = MemoryJournal()
    notes = Notes(school, recorder=Recorder(jrnl), bug_options={"token_source": None,
                                                                "info": {"revision": "review-00021-xyz"}})
    teacher = authn.require(hdr(tok(TEACHER_EMAIL)))
    s = notes.session(RID, teacher)
    assert [m["type"] for m in s["markers"]] == ["Incident"]              # "bug" / "BUG" never shown
    assert [(b["source"], b["offset_seconds"], b["status"]) for b in s["bug_reports"]] == [
        ("recorder", 83, "pending"), ("recorder", 200, "pending")]
    reports = [school.read_json(n) for n in sorted(school.list(f"bug_reports/{RID}/"))]
    assert len(reports) == 2
    r = reports[0]
    assert (r["note"], r["area"], r["firmware_version"], r["device_id"], r["marker_clock"]) == (
        "Reported from recorder", "recorder", "0.6.7", "rec-a1b2c3", "1:02 PM")
    title, body = build_issue(r)
    assert title == "Recorder bug report: Reported from recorder at 1:23"
    for row in ("| Screen | recorder |", "| Recorder | `rec-a1b2c3` |", "| Recorder firmware | 0.6.7 |",
                "| Recording started | 2026-10-08 12:49:26 PM Eastern |", "| Bug pressed at | 1:02 PM Eastern |",
                "| Time in recording | 1:23 |", f"| Recording | `{RID}` |"):
        assert row in body, row
    assert reports[1]["marker_clock"] == "12:52 PM"                         # no wall_time: start + offset
    for never in NEVER:
        assert never not in title + body
    # once each, whoever looks
    notes.list_sessions(authn.require(hdr(tok(ADMIN))))
    notes.session(RID, authn.require(hdr(tok(RBT_EMAIL))))
    assert len([n for n in school.list(f"bug_reports/{RID}/") if n.endswith(".json") and ".claim" not in n]) == 2
    assert [r["action"] for r in jrnl.rows()].count("bug_reported") == 2
    # visible to the session's teacher and admins; not to an RBT or another teacher
    assert len(notes.session(RID, authn.require(hdr(tok(ADMIN))))["bug_reports"]) == 2
    assert notes.session(RID, authn.require(hdr(tok(RBT_EMAIL))))["bug_reports"] == []
    try:
        assert notes.session(RID, authn.require(hdr(tok(OTHER))))["bug_reports"] == []
    except ApiError as e:                                    # or the session isn't theirs to read at all
        assert e.http in (403, 404)
    # never an incident, never in a PDF
    assert len(s["processed_analysis"]["incident_analysis"]) == 2
    notes.approve(RID, teacher, notes.session(RID, teacher)["processed_analysis"])
    text = pdf_text(notes.pdf(RID, teacher)[0])
    assert "bug" not in text.lower()
    m = notes.incident_print(RID, teacher, 1)
    assert "bug" not in json.dumps(m).lower()


# ---------------------------------------------------------------- named from the transcript

def test_name_comes_from_the_transcript_window_after_the_marker(school, monkeypatch):
    from review_api.incident_names import PROMPT_VERSION, transcript_window, window_seconds
    t = {"student_first_name": "Marcus", "speaker_turns": [
        {"speaker": "Speaker 1", "start_seconds": 0, "end_seconds": 2, "text": "Morning, Marcus."},
        {"speaker": "Speaker 1", "start_seconds": 750, "end_seconds": 756, "text": "Marcus, open your book please."},
        {"speaker": "Speaker 2", "start_seconds": 760, "end_seconds": 765, "text": "No. I'm going to the window."},
        {"speaker": "Speaker 1", "start_seconds": 800, "end_seconds": 806, "text": f"Ms Jenkins here, {SCHOOL_ID}."},
        {"speaker": "Speaker 1", "start_seconds": 840, "end_seconds": 845, "text": "Too late: past the window."}]}
    school.write_json(f"transcripts/{RID}.json", t)
    ai = FakeAI(named("Left seat and went to the window during reading", "Covered ears"))
    Notes(school, name_options={"call": ai}).incident_names(RID, None)
    system, payload = ai.sent[0]
    assert "transcript" in system and payload["window_seconds"] == 75 and PROMPT_VERSION.startswith("incident-name-v2")
    inc = next(i for i in payload["incidents"] if i["index"] == 0)          # the incident at 754 s
    assert inc["transcript"] == ["Speaker 1: the student, open your book please.",   # began before, runs past the marker
                                 "Speaker 2: No. I'm going to the window.",
                                 "Speaker 1: Ms the teacher here, ."]           # 829 s ends the window: 840 s is out
    sent = json.dumps(payload)
    for never in NEVER:
        assert never.lower() not in sent.lower(), never
    assert "Morning" not in sent                                         # never the whole transcript
    monkeypatch.setenv("INCIDENT_NAME_WINDOW_SECONDS", "200")
    assert window_seconds() == 90
    monkeypatch.setenv("INCIDENT_NAME_WINDOW_SECONDS", "10")
    assert window_seconds() == 60
    assert transcript_window(t, None, never_for(school)) == []


def test_no_transcript_names_from_the_notes(school):
    ai = FakeAI(named("Left seat", "Covered ears"))
    Notes(school, name_options={"call": ai}).incident_names(RID, None)
    inc = ai.sent[0][1]["incidents"][0]
    assert set(inc) == {"index", "transcript", "notes"} and inc["notes"]["behavior"]


# ---------------------------------------------------------------- capitalized words

def test_prompt_says_never_a_name_and_capitalized_words_are_caught():
    from review_api.incident_names import CAP_ALLOW, capitalized_words
    assert "Never include any person's name" in NAME_SYSTEM and '"a peer"' in NAME_SYSTEM and '"the teacher"' in NAME_SYSTEM
    assert capitalized_words("The student pushed Jayden at recess") == ["Jayden"]       # not on any roster
    assert capitalized_words("Student left seat during Circle Time") == []
    for ok in ("Covered ears during Math on Tuesday", "Said I need a break in October", "Left the ABC table during ELA",
               "Pushed tray during Lunch near RM12", "Asked for IEP break card in PE", "Ran to the door after Recess",
               "Left Reading 3A group", "Students lined up for Art and Music"):
        assert capitalized_words(ok) == [], ok
    for bad, word in (("Hit Ms Lopez during group", "Ms"), ("Threw a cup at Aiden's desk", "Aiden"),
                      ("Followed Coach Dee outside", "Coach"), ("Yelled at a peer named Sam", "Sam")):
        assert word in capitalized_words(bad), bad
    assert {"I", "Monday", "May", "Circle", "Time", "ELA", "PE", "Recess"} <= CAP_ALLOW


def test_capitalized_word_retries_once_then_falls_back(school):
    ai = FakeAI(named("The student pushed Jayden at recess", "Covered ears during Circle Time"),
                named("Pushed Jayden again"))
    out = Notes(school, name_options={"call": ai}).incident_names(RID, None)
    assert len(ai.sent) == 2 and [i["index"] for i in ai.sent[1][1]["incidents"]] == [0]
    assert "capitalized word" in ai.sent[1][0]
    by_n = {i["number"]: i["title"] for i in out["incidents"]}
    assert by_n == {2: "Incident 2", 1: "Incident 1: Covered ears during Circle Time"}
    stored = {d["reason"] for d in (school.read_json(n) for n in school.list(f"incident_names/{RID}/"))}
    assert stored == {"capitalized_word", "ok"}


def test_capitalized_word_then_a_clean_retry_is_used(school):
    ai = FakeAI(named("The student pushed Jayden at recess", "Covered ears"), named("Pushed a peer at recess"))
    out = Notes(school, name_options={"call": ai}).incident_names(RID, None)
    assert {i["number"]: i["title"] for i in out["incidents"]}[2] == "Incident 2: Pushed a peer at recess"


def test_teacher_edited_names_are_not_checked_for_capitals(school, authn):
    notes = Notes(school, name_options={"call": FakeAI(named("Left seat", "Covered ears"))})
    d = school.read_json(f"drafts/{RID}.json")
    d["analysis"]["incident_analysis"][0].update(name="Pushed Jayden at recess", name_source="teacher")
    school.write_json(f"drafts/{RID}.json", d)
    s = notes.session(RID, None)
    names = {i["timestamp_seconds"]: (i.get("name"), i.get("name_source")) for i in s["processed_analysis"]["incident_analysis"]}
    assert names[754] == ("Pushed Jayden at recess", "teacher")


def test_person_named_classroom_words_are_not_allowed(school):
    """Fixed allowlist only: a classroom named after a person doesn't open a hole."""
    from review_api.incident_names import capitalized_words
    r = school.read_json("config/demo_roster.json")
    r["class_codes"]["Mrs Lee's Room"] = {"teacher_id": "usr_demo_sarah"}
    school.write_json("config/demo_roster.json", r)
    assert capitalized_words("Student left seat in Lee room") == ["Lee"]
    ai = FakeAI(named("Student left seat in Lee room", "Covered ears"), named("Left seat in Lee room again"))
    out = Notes(school, name_options={"call": ai}).incident_names(RID, None)
    assert {i["number"]: i["title"] for i in out["incidents"]}[2] == "Incident 2"
