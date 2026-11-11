"""Planner AI drafts: no student first name - and nothing else that
identifies anyone - reaches the AI; the reply is checked, names go back on our
side, a draft that repeats a transcript is discarded, and drafts are not stored.

The live path is exercised end to end with a fake Gemini client that records
everything it was handed (same pattern as test_prompt_privacy.py).
"""
from __future__ import annotations

import json

import pytest

from test_hosted_auth import ADMIN, TEACHER_EMAIL, call, token  # noqa: F401
from test_planner import (ART_TEACHER, LAST, NEXT, QUOTE, RBT_EMAIL, THIS, authn, hosted, journal,  # noqa: F401
                          pinned_today, planner, post, pstore, who)
from review_api import planner_ai as ai
from review_api.notes import ApiError
from review_api.planner import Planner

NAMES = ("marcus", "sarah", "jenkins")


class _FakeModels:
    def __init__(self, sent, reply):
        self.sent, self.reply = sent, reply
        self.closed = False

    def generate_content(self, **kw):
        if self.closed:   # like google-genai: the Client closes its HTTP client when it is collected
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        self.sent.append(kw)
        reply = self.reply(kw) if callable(self.reply) else self.reply

        class R:
            text = reply if isinstance(reply, str) else json.dumps(reply)
        return R()


class _FakeClient:
    def __init__(self, sent, reply):
        self.models = _FakeModels(sent, reply)

    def __del__(self):
        self.models.closed = True


SUMMARY_REPLY = {"summary": "The student had a steady week and asked for a break on their own.",
                 "iep_check": [{"goal_id": "g1", "status": "evidence_seen", "note": "The student used the break card."},
                               {"goal_id": "g2", "status": "not_seen", "note": "Regrouping still needs prompts."}]}
PLAN_REPLY = {"goals": ["Keep the student's break routine going"],
              "objectives": ["The student scores 80% on two regrouping probes", "Timer card after each break"]}


@pytest.fixture()
def live(monkeypatch):
    sent = []
    box = {"reply": SUMMARY_REPLY}
    monkeypatch.setenv("PLANNER_AI", "live")
    monkeypatch.setattr(ai, "_client", lambda: _FakeClient(sent, lambda kw: box["reply"]))
    return sent, box


def _forbidden(pstore):
    """Everything that must never reach the AI, taken from the demo data itself."""
    out = list(NAMES) + ["stu_101", "rec_laptop", "usr_demo", "@", "a."]
    for n in pstore.list("approvals/"):
        a = pstore.read_json(n)["analysis"]
        out += [ev["evidence_quote"] for ev in a["goal_evaluations"] if ev["evidence_quote"]]
        out.append(a["draft_parent_email"][:60])
    tr = pstore.read_json(pstore.list("transcripts/")[0])
    out += [t["text"] for t in tr["speaker_turns"] if len(t["text"].split()) >= 6][:5]
    return [x.casefold() for x in out if x]


def _check_sent(call_kw, pstore):
    blob = json.dumps(call_kw, default=str).casefold()
    hits = [x for x in _forbidden(pstore) if x in blob and x != "a."]
    assert hits == []
    assert " a. " not in blob and '"a."' not in blob        # no last initial
    assert "the student" in blob
    return json.loads(call_kw["contents"])


def test_summary_draft_sends_no_names_ids_or_transcripts(planner, pstore, live):
    sent, _ = live
    planner.save_note("usr_demo_sarah", {"student_id": "stu_101", "week": LAST,
                                         "note": "MARCUS did well; Ms. Jenkins moved marcus's seat. Sarah agreed."})
    out = planner.draft_summary("usr_demo_sarah", {"student_id": "stu_101", "week": LAST})
    [kw] = sent
    payload = _check_sent(kw, pstore)
    assert set(payload) == {"week", "student", "iep_goals", "approved_sessions", "teacher_notes"}
    assert payload["student"] == "the student"
    assert payload["teacher_notes"] == ["the student did well; the teacher moved the student's seat. "
                                        "the teacher agreed."]
    assert [s["day"] for s in payload["approved_sessions"]] == ["Wed", "Thu"]
    for s in payload["approved_sessions"]:
        assert set(s) == {"day", "goals", "session_summary", "incidents"}
        assert all(set(g) == {"goal_id", "status", "teacher_approved_analysis"} for g in s["goals"])
    assert '"the student"' in kw["config"]["system_instruction"]
    assert kw["config"]["system_instruction"] == ai.SUMMARY_SYSTEM
    assert kw["config"]["response_json_schema"] == ai.SUMMARY_SCHEMA
    # names back on our side, AI draft marked as such
    assert out["summary"].startswith("Marcus had a steady week")
    assert out["iep_check"][0]["note"] == "Marcus used the break card."
    assert out["ai"] == {"source": "gemini_live", "model": "gemini-3.8-flash", "prompt_version": ai.PROMPT_VERSION}


def test_plan_draft_sends_no_names_ids_or_transcripts(planner, pstore, live):
    sent, box = live
    box["reply"] = PLAN_REPLY
    planner.save_summary("usr_demo_sarah", {"student_id": "stu_101", "week": THIS,
                                            "summary": "Marcus met with Sarah Jenkins daily.",
                                            "iep_check": [{"goal_id": "g1", "status": "evidence_seen", "note": "ok"}]})
    out = planner.draft_plan("usr_demo_sarah", {"class_code": "RM12", "week": NEXT})
    [kw] = sent
    payload = _check_sent(kw, pstore)
    assert set(payload) == {"plan_week", "previous_week", "students", "previous_plan"}
    [st] = payload["students"]
    assert st["student"] == "the student" and st["last_week_summary_kind"] == "teacher_saved"
    assert st["last_week_summary"] == "the student met with the teacher the teacher daily."
    assert [g["goal_id"] for g in st["iep_goals"]] == ["g1", "g2"]
    assert payload["previous_plan"]["goals"]                 # this week's plan feeds next week's draft
    assert kw["config"]["system_instruction"] == ai.PLAN_SYSTEM
    assert out["goals"] == ["Keep Marcus's break routine going"]
    assert out["objectives"][0] == "Marcus scores 80% on two regrouping probes"
    assert out["based_on_week"] == THIS


def test_plan_draft_falls_back_to_counted_suggestion(planner, pstore, live):
    sent, box = live
    box["reply"] = PLAN_REPLY
    planner.draft_plan("usr_demo_sarah", {"class_code": "RM12", "week": THIS})   # based on last week: no saved summary
    st = json.loads(sent[0]["contents"])["students"][0]
    assert st["last_week_summary_kind"] == "counted_from_approved_notes"
    assert st["last_week_summary"].startswith("2 approved sessions this week. the student")


def test_mask_several_students_and_put_back():
    m = ai.Mask(["Marcus", "Ava"], ["Sarah Jenkins"])
    assert m.hide("Marcus and AVA with Ms. Jenkins") == "student 1 and student 2 with the teacher"
    assert m.show({"x": ["Student 1 helped student 2.", "student 10 stays"]}) == \
        {"x": ["Marcus helped Ava.", "student 10 stays"]}
    one = ai.Mask(["Marcus"], [])
    assert one.hide("Marcuses and Marcusville") == "Marcuses and Marcusville"      # whole words only
    assert one.show("The student's turn; the students lined up.") == "Marcus's turn; the students lined up."


def test_ai_reply_quoting_a_transcript_is_discarded(planner, pstore, live):
    _, box = live
    box["reply"] = {**SUMMARY_REPLY, "summary": QUOTE}
    before = sorted(pstore.list(""))
    with pytest.raises(ApiError) as e:
        planner.draft_summary("usr_demo_sarah", {"student_id": "stu_101", "week": LAST})
    assert (e.value.http, e.value.code) == (422, "AI_DRAFT_QUOTES_TRANSCRIPT")
    box["reply"] = {"goals": [QUOTE], "objectives": []}
    with pytest.raises(ApiError) as e:
        planner.draft_plan("usr_demo_sarah", {"class_code": "RM12", "week": THIS})
    assert e.value.code == "AI_DRAFT_QUOTES_TRANSCRIPT"
    assert sorted(pstore.list("")) == before


@pytest.mark.parametrize("reply,code", [("not json", "AI_UNAVAILABLE"), ({"summary": ""}, "AI_BAD_REPLY"),
                                        ([1, 2], "AI_BAD_REPLY")])
def test_bad_ai_reply(planner, live, reply, code):
    live[1]["reply"] = reply
    with pytest.raises(ApiError) as e:
        planner.draft_summary("usr_demo_sarah", {"student_id": "stu_101", "week": LAST})
    assert e.value.code == code and e.value.http == 502


def test_iep_check_covers_every_goal_once(planner, live):
    live[1]["reply"] = {"summary": "Fine week.", "iep_check": [
        {"goal_id": "g9", "status": "evidence_seen", "note": "unknown goal"},
        {"goal_id": "g2", "status": "great", "note": "bad status"},
        {"goal_id": "g1", "status": "not_seen", "note": " ".join(["word"] * 50)},
        {"goal_id": "g1", "status": "evidence_seen", "note": "duplicate"}]}
    out = planner.draft_summary("usr_demo_sarah", {"student_id": "stu_101", "week": LAST})
    assert [(c["goal_id"], c["status"]) for c in out["iep_check"]] == [("g1", "not_seen"), ("g2", "not_enough")]
    assert len(out["iep_check"][0]["note"].split()) == ai.MAX_NOTE_WORDS
    assert out["iep_check"][1]["note"] == "Not covered by the AI draft."


def test_offline_stand_in_gets_the_same_masked_payload(planner, pstore, monkeypatch):
    seen = []
    real = ai._offline_summary
    monkeypatch.setattr(ai, "_offline_summary", lambda p: (seen.append(p), real(p))[1])
    monkeypatch.delenv("PLANNER_AI", raising=False)
    out = planner.draft_summary("usr_demo_sarah", {"student_id": "stu_101", "week": LAST})
    assert out["ai"]["source"] == "offline_stand_in" and out["ai"]["model"] is None
    hits = [x for x in _forbidden(pstore) if x in json.dumps(seen).casefold() and x != "a."]
    assert hits == [] and "Marcus" in out["summary"]


def test_modes(monkeypatch, planner):
    monkeypatch.delenv("PLANNER_AI", raising=False)
    assert ai.mode() == "offline"
    monkeypatch.setenv("K_SERVICE", "review")
    assert ai.mode() == "off"                                 # Cloud Run: off unless configured
    monkeypatch.delenv("K_SERVICE")
    monkeypatch.setenv("PLANNER_AI", "off")
    with pytest.raises(ApiError) as e:
        planner.draft_summary("usr_demo_sarah", {"student_id": "stu_101", "week": LAST})
    assert (e.value.http, e.value.code) == (404, "AI_DRAFTS_OFF")
    assert planner.view(None, "RM12")["ai_drafts"] == "off"


def test_drafts_need_something_to_draft_from_and_the_right_week(planner):
    with pytest.raises(ApiError) as e:
        planner.draft_summary("usr_demo_sarah", {"student_id": "stu_101", "week": "2026-08-31"})
    assert (e.value.http, e.value.code) == (409, "NOTHING_TO_DRAFT_FROM")
    for fn, body in ((planner.draft_summary, {"student_id": "stu_101", "week": NEXT}),
                     (planner.draft_plan, {"class_code": "RM12", "week": LAST})):
        with pytest.raises(ApiError) as e:
            fn("usr_demo_sarah", body)
        assert e.value.code == "BAD_WEEK"


def test_limit_per_person(pstore):
    from review_api.notes import Notes
    p = Planner(Notes(pstore, roster_ttl=0), limiter=ai.Limiter(n=2))
    for _ in range(2):
        p.draft_summary("usr_demo_sarah", {"student_id": "stu_101", "week": LAST})
    with pytest.raises(ApiError) as e:
        p.draft_summary("usr_demo_sarah", {"student_id": "stu_101", "week": LAST})
    assert (e.value.http, e.value.code) == (429, "TOO_MANY_DRAFTS")


def test_saved_draft_is_marked_ai_assisted(planner, pstore):
    d = planner.draft_summary("usr_demo_sarah", {"student_id": "stu_101", "week": LAST})
    planner.save_summary("usr_demo_sarah", {"student_id": "stu_101", "week": LAST, "summary": d["summary"],
                                            "iep_check": d["iep_check"], "ai": d["ai"]})
    [n] = pstore.list(f"planner/students/stu_101/{LAST}/summary/")
    doc = pstore.read_json(n)
    assert doc["ai_assisted"] == {"source": "offline_stand_in", "model": None, "prompt_version": ai.PROMPT_VERSION}
    assert "Marcus" not in json.dumps(doc)
    row = planner.view(None, "RM12")["weeks"][0]["students"][0]
    assert row["summary"]["ai_assisted"]["source"] == "offline_stand_in"
    for bad in ({"source": "chatgpt", "prompt_version": ai.PROMPT_VERSION}, {"source": "gemini_live"},
                {"source": "gemini_live", "prompt_version": ai.PROMPT_VERSION, "model": "Bad Model!"}):
        with pytest.raises(ApiError) as e:
            planner.save_summary("usr_demo_sarah", {"student_id": "stu_101", "week": LAST, "summary": "x", "ai": bad})
        assert e.value.code == "BAD_AI_DRAFT"


def test_http_drafts_permissions_and_journal(hosted, journal, pstore):
    before = sorted(pstore.list("planner/"))
    st, out = post(hosted, TEACHER_EMAIL, "draft/summary", {"student_id": "stu_101", "week": LAST})
    assert st == 200 and out["ai"]["source"] == "offline_stand_in" and "Marcus" in out["summary"]
    st, out = post(hosted, TEACHER_EMAIL, "draft/plan", {"class_code": "RM12", "week": NEXT})
    assert st == 200 and out["goals"] and out["objectives"]
    assert sorted(pstore.list("planner/")) == before                # drafts are not stored
    assert post(hosted, RBT_EMAIL, "draft/summary", {"student_id": "stu_101", "week": LAST}) == \
        (403, {"error": "READ_ONLY_ROLE"})
    assert post(hosted, ART_TEACHER, "draft/plan", {"class_code": "RM12", "week": NEXT}) == \
        (403, {"error": "NOT_YOUR_CLASSROOM"})
    rows = [r for r in journal.rows() if r["action"].endswith("_drafted")]
    assert [r["action"] for r in rows] == ["planner_summary_drafted", "planner_plan_drafted"]
    d = rows[0]["details"]
    assert (json.loads(d) if isinstance(d, str) else d) == {"week": LAST, "ai_source": "offline_stand_in"}
    assert "marcus" not in json.dumps(journal.rows()).casefold()


# ---------------------------------------------------------------- the weekly draft parent email
# Moved here from each session: one draft per student per week, at the end of the weekly summary.

def test_weekly_summary_draft_has_the_parent_email_masked_then_named(planner, pstore, live):
    sent, box = live
    box["reply"] = {**SUMMARY_REPLY, "parent_email": "Hello,\n\nThe student had a calm week and used the break card.\n\nBest regards,"}
    out = planner.draft_summary("usr_demo_sarah", {"student_id": "stu_101", "week": LAST})
    [kw] = sent
    _check_sent(kw, pstore)                                         # no names, ids, transcripts, old parent emails
    assert "parent_email" in kw["config"]["response_json_schema"]["properties"]
    assert "parent_email" in kw["config"]["system_instruction"] and "Do not invent events" in kw["config"]["system_instruction"]
    assert out["parent_email"].startswith("Hello,\n\nMarcus had a calm week")
    box["reply"] = SUMMARY_REPLY                                    # a reply without one: an empty draft, not an error
    assert planner.draft_summary("usr_demo_sarah", {"student_id": "stu_101", "week": LAST})["parent_email"] == ""


def test_offline_weekly_parent_email(planner, pstore, monkeypatch):
    monkeypatch.delenv("PLANNER_AI", raising=False)
    out = planner.draft_summary("usr_demo_sarah", {"student_id": "stu_101", "week": LAST})
    assert out["parent_email"].startswith("Hello,") and "Marcus" in out["parent_email"]
    assert out["parent_email"].rstrip().endswith("Best regards,")


def test_weekly_parent_email_saved_and_seen_only_by_the_students_teachers_and_admins(planner, pstore, authn):
    text = "Hello,\n\nMarcus had a good week.\n\nBest regards,"
    planner.save_summary("usr_demo_sarah",
                         {"student_id": "stu_101", "week": LAST, "summary": "Steady week.", "parent_email": text})
    [n] = pstore.list(f"planner/students/stu_101/{LAST}/summary/")
    doc = pstore.read_json(n)
    assert doc["parent_email"] == "Hello,\n\nthe student had a good week.\n\nBest regards," and "Marcus" not in json.dumps(doc)
    row = lambda actor: planner.view(actor, "RM12")["weeks"][0]["students"][0]   # noqa: E731
    assert row("usr_demo_sarah")["summary"]["parent_email"] == text
    assert row(who(authn, ADMIN))["summary"]["parent_email"] == text
    for other in (RBT_EMAIL, ART_TEACHER):
        r = row(who(authn, other))
        assert "parent_email" not in r["summary"] and r["parent_email_hidden"] is True, other
        assert "good week" not in json.dumps(r)
    with pytest.raises(ApiError) as e:
        planner.save_summary("usr_demo_sarah", {"student_id": "stu_101", "week": LAST, "summary": "x",
                                                "parent_email": "x" * 6001})
    assert e.value.code == "BAD_PARENT_EMAIL"
    with pytest.raises(ApiError) as e:                              # same transcript-quote check as the summary
        planner.save_summary("usr_demo_sarah", {"student_id": "stu_101", "week": LAST, "summary": "x",
                                                "parent_email": QUOTE})
    assert e.value.code == "QUOTES_TRANSCRIPT"
