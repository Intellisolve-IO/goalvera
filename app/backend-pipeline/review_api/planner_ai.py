"""AI drafts for the weekly lesson planner.

Two drafts, both made only when a teacher clicks for one, both returned to the
browser and NOT stored (the teacher edits and saves - that saved version is the
record, marked as AI-assisted):

  student weekly summary   from that week's teacher-approved session notes and
                           teacher notes, checked against the IEP goals: a short
                           summary plus a status and one-line reason per goal.
  next week's plan         goals and objectives from the previous week's weekly
                           summaries, teacher notes, plan and the IEP goals.

What goes to the AI (build_*_payload) - and what never does:
  sent      IEP goal ids, categories and descriptions; per approved session the
            weekday, the per-goal status and the teacher-approved analysis text,
            the session summary and the incident count; teacher notes; last
            week's plan goals and objectives; saved weekly summaries.
  masked    the student's first name -> "the student" (several students:
            "student 1", "student 2"), the teacher's names -> "the teacher",
            before sending; put back on our side afterwards.
  never     transcripts and evidence quotes, parent emails, student or recording
            ids, last initials, emails, user ids, audio.
The reply is schema-checked, the names are put back, and it goes through the
same 8-word transcript-quote check as a save: a draft that repeats a transcript
is discarded (422 AI_DRAFT_QUOTES_TRANSCRIPT).

Modes (PLANNER_AI):
  offline   default off Cloud Run: a local stand-in builds the draft from the
            same masked payload. No network call. Used for the demo and tests.
  live      Gemini via google-genai (Vertex AI with VERTEX_AI=1). Local /
            emulator testing only until the live service is configured for it.
  off       default on Cloud Run: drafts refused (404 AI_DRAFTS_OFF).
"""
from __future__ import annotations

import json
import os
import re

from common.namematch import name_pattern
import threading
import time
from typing import Any

from gemini_analyzer.prompt import STUDENT_PLACEHOLDER

PROMPT_VERSION = "planner-ai-v2-2026-10"   # v2: statuses over text, session counts, digits
IEP_CHECK = ("evidence_seen", "not_seen", "not_enough")
TEACHER_PLACEHOLDER = "the teacher"
OTHER_STUDENT_PLACEHOLDER = "another student"
MAX_NOTE_WORDS = 30
DAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def mode() -> str:
    m = os.environ.get("PLANNER_AI")
    if m in ("offline", "live", "off"):
        return m
    return "off" if os.environ.get("K_SERVICE") else "offline"


# ---------------------------------------------------------------- masking

def _word(name: str) -> re.Pattern | None:
    """Any length: 3+ letters any case; 1-2 letters capitalized whole words only
    (common/namematch.py), so "Li" is masked and "li" / "Lila" are not."""
    name = (name or "").strip()
    if not name or name.casefold() in ("student", "the student", "teacher"):
        return None
    return name_pattern(name)


class Mask:
    """Names out before the AI, back in afterwards. labels: first name -> placeholder."""

    def __init__(self, students: list[str], teacher_names: list[str], others: list[str] = ()):
        self.labels: dict[str, str] = {}
        names = [n for n in students if n]
        for i, n in enumerate(names):
            self.labels[n] = STUDENT_PLACEHOLDER if len(names) == 1 else f"student {i + 1}"
        self.out = [(p, lab) for n, lab in self.labels.items() if (p := _word(n))]
        # classmates named in a classroom session's notes: hidden, never put back
        for n in others or ():
            if n and n not in self.labels and (p := _word(n)):
                self.out.append((p, OTHER_STUDENT_PLACEHOLDER))
        for t in teacher_names:
            parts = [x for x in re.split(r"[\s.]+", t or "") if len(x) >= 2]
            if parts:   # "Ms. Jenkins" / "Mrs Sarah Jenkins" -> "the teacher", not "Ms. the teacher"
                alt = "|".join(re.escape(x) for x in parts)
                self.out.append((re.compile(rf"(?<![\w])(?:Ms|Mrs|Mr|Dr|Miss)\.?\s+(?:(?:{alt})\s+)*(?:{alt})(?![\w])",
                                            re.IGNORECASE), TEACHER_PLACEHOLDER))
            for part in re.split(r"[\s.]+", t or ""):
                p = _word(part)
                if p and part.casefold() not in ("ms", "mr", "mrs", "dr"):
                    self.out.append((p, TEACHER_PLACEHOLDER))

    def hide(self, obj: Any) -> Any:
        if isinstance(obj, str):
            for p, lab in self.out:
                obj = p.sub(lab, obj)
            return obj
        if isinstance(obj, list):
            return [self.hide(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.hide(v) for k, v in obj.items()}
        return obj

    def show(self, obj: Any) -> Any:
        if isinstance(obj, str):
            for name, lab in self.labels.items():
                if lab == STUDENT_PLACEHOLDER:
                    obj = re.sub(r"\b([Tt])he student\b(?!s)", name, obj)
                else:
                    obj = re.sub(rf"\b[Ss]tudent {lab.split()[-1]}\b(?!\d)", name, obj)
            return obj
        if isinstance(obj, list):
            return [self.show(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.show(v) for k, v in obj.items()}
        return obj


# ---------------------------------------------------------------- payloads (exactly what is sent)

def _goals(iep_goals: list[dict]) -> list[dict]:
    return [{"goal_id": g["goal_id"], "category": g.get("category") or "", "description": g.get("description") or ""}
            for g in iep_goals]


def summary_payload(*, week_label: str, iep_goals: list[dict], sessions: list[dict], notes: list[str],
                    mask: Mask) -> dict:
    """sessions: [{"date": "YYYY-MM-DD", "analysis": approved notes}] - only the fields listed here go."""
    import datetime as dt
    out = []
    for s in sessions:
        a = s.get("analysis") or {}
        out.append({
            "day": DAY[dt.date.fromisoformat(s["date"]).weekday()],
            "goals": [{"goal_id": ev.get("goal_id"), "status": ev.get("status"),
                       "teacher_approved_analysis": ev.get("analysis") or ""}
                      for ev in a.get("goal_evaluations") or []],
            "session_summary": a.get("general_session_summary") or "",
            "incidents": len(a.get("incident_analysis") or []),
        })
    return mask.hide({"week": week_label, "student": STUDENT_PLACEHOLDER, "iep_goals": _goals(iep_goals),
                      "approved_sessions": out, "teacher_notes": [n for n in notes if n]})


def plan_payload(*, week_label: str, previous_week_label: str, students: list[dict], previous_plan: dict | None,
                 mask: Mask) -> dict:
    """students: [{"first_name", "iep_goals", "summary": {"summary", "iep_check"} | None, "summary_kind", "notes"}]"""
    out = []
    for st in students:
        s = st.get("summary") or {}
        out.append({
            "student": mask.labels.get(st["first_name"], STUDENT_PLACEHOLDER),
            "iep_goals": _goals(st["iep_goals"]),
            "last_week_summary": s.get("summary") or "",
            "last_week_summary_kind": st.get("summary_kind") or "none",
            "last_week_iep_check": [{"goal_id": c.get("goal_id"), "status": c.get("status"), "note": c.get("note") or ""}
                                    for c in s.get("iep_check") or []],
            "teacher_notes": [n for n in st.get("notes") or [] if n],
        })
    prev = {"goals": (previous_plan or {}).get("goals") or [],
            "objectives": (previous_plan or {}).get("objectives") or []}
    return mask.hide({"plan_week": week_label, "previous_week": previous_week_label, "students": out,
                      "previous_plan": prev})


SUMMARY_SYSTEM = """You help a special-education teacher draft a short weekly progress summary for one student.
You receive the student's IEP goals, the teacher-approved notes from this week's sessions, and the teacher's own notes.

Rules:
- Use ONLY the information given. Do not invent events, numbers or people.
- Refer to the student only as "the student". Never guess or invent a name. Refer to staff only as "the teacher".
- Write in your own words. Do not quote anyone and never copy more than five words in a row from the input.
- Each session gives a per-goal status (Demonstrated, Struggled, Not Observed) that the teacher approved.
  The status is the result: if a session's text reads differently, follow the status.
- For EVERY IEP goal give exactly one check:
  status "evidence_seen" = the notes show the goal met or clear progress toward it;
  "not_seen" = worked on but not yet shown; "not_enough" = not enough information this week.
  note = one plain sentence, at most 25 words, saying why, with the count of sessions
  (for example "shown in 1 of 2 sessions").
- summary = 2 to 4 plain, non-jargon sentences about the week. When sessions differ, say so;
  never say "each time" or "every session" unless every status agrees. Mention what the
  teacher's notes add, if anything.
- Write numbers as digits (86%, 3 minutes).
- parent_email = a draft end-of-week email from the teacher to the student's family: warm, professional,
  non-jargon, 4 to 8 sentences. Start with "Hello," and end with "Best regards," on its own line.
  Share what went well and what the class is working on, from this week's notes only. Do not invent events,
  do not describe incidents in detail (at most say how many were noted and that they were handled),
  no IEP codes or goal ids, no diagnoses. Refer to the student as "the student" and to staff as "the teacher".
- Output JSON ONLY matching the schema. No markdown fences."""

PLAN_SYSTEM = """You help a special-education teacher draft the goals and objectives of a classroom's weekly lesson plan.
You receive each student's IEP goals, last week's weekly summary and per-goal check, the teacher's notes, and last week's plan goals and objectives.

Rules:
- Use ONLY the information given. Build on what last week's summaries show: keep working on goals not yet shown, move on where evidence was seen.
- Refer to students only as written in the input ("the student", or "student 1", "student 2"). Never guess or invent a name. Refer to staff only as "the teacher".
- goals: 2 to 5 short classroom goals for the week, each tied to an IEP goal where possible.
- objectives: 3 to 6 concrete, measurable objectives (what, how often, what counts as success).
- At most 20 words per item. Plain language. No quotes from the input. Write numbers as digits.
- Output JSON ONLY matching the schema. No markdown fences."""

SUMMARY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "iep_check", "parent_email"],
    "properties": {
        "summary": {"type": "string"},
        # the draft parent email moved here from each session: one per student per week
        "parent_email": {"type": "string"},
        "iep_check": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["goal_id", "status", "note"],
            "properties": {"goal_id": {"type": "string"}, "status": {"type": "string", "enum": list(IEP_CHECK)},
                           "note": {"type": "string"}}}},
    },
}
PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["goals", "objectives"],
    "properties": {"goals": {"type": "array", "items": {"type": "string"}},
                   "objectives": {"type": "array", "items": {"type": "string"}}},
}


class DraftError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- the model call

def _client():
    """Same client as the session analyzer (Vertex AI in our project with VERTEX_AI=1)."""
    from gemini_analyzer.analyzer import _client as c
    return c()


def _call(system: str, payload: dict, schema: dict) -> tuple[dict, str, str | None]:
    """-> (reply, source, model). The payload is already masked."""
    m = mode()
    if m == "off":
        raise DraftError("AI_DRAFTS_OFF")
    if m == "offline":
        stand_in = _offline_summary if schema is SUMMARY_SCHEMA else _offline_plan
        return stand_in(json.loads(json.dumps(payload))), "offline_stand_in", None
    from gemini_analyzer.analyzer import _use_vertex, model_name
    try:
        # Keep the client in a local: google-genai's Client closes its HTTP client in __del__,
        # so `_client().models.generate_content(...)` fails with "client has been closed".
        client = _client()
        r = client.models.generate_content(
            model=model_name(), contents=json.dumps(payload, ensure_ascii=False, indent=1),
            config={"system_instruction": system, "response_mime_type": "application/json",
                    "response_json_schema": schema, "temperature": 0.3})
        text = (r.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        reply = json.loads(text)
    except DraftError:
        raise
    except Exception as exc:  # noqa: BLE001 - never echo model output or messages
        raise DraftError("AI_UNAVAILABLE") from exc
    return reply, ("vertex_gemini_live" if _use_vertex() else "gemini_live"), model_name()


def _clip(s: Any, words: int) -> str:
    w = str(s or "").split()
    return " ".join(w[:words]) + ("…" if len(w) > words else "")


MAX_EMAIL_WORDS = 250


def _clip_lines(s: Any, words: int) -> str:
    """Like _clip, but keeps the email's line breaks (paragraphs, sign-off)."""
    lines, left, out = str(s or "").strip().splitlines(), words, []
    for ln in lines:
        w = ln.split()
        if len(w) > left:
            out.append(" ".join(w[:left]) + "…")
            break
        out.append(" ".join(w))
        left -= len(w)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def check_summary(reply: Any, goal_ids: list[str]) -> dict:
    """Schema + every IEP goal exactly once (missing ones -> not_enough)."""
    if not isinstance(reply, dict) or not isinstance(reply.get("summary"), str) or not reply["summary"].strip() \
            or not isinstance(reply.get("iep_check"), list):
        raise DraftError("AI_BAD_REPLY")
    got = {}
    for c in reply["iep_check"]:
        if isinstance(c, dict) and c.get("goal_id") in goal_ids and c.get("status") in IEP_CHECK \
                and c["goal_id"] not in got:
            got[c["goal_id"]] = {"goal_id": c["goal_id"], "status": c["status"],
                                 "note": _clip(c.get("note"), MAX_NOTE_WORDS)}
    checks = [got.get(g) or {"goal_id": g, "status": "not_enough", "note": "Not covered by the AI draft."}
              for g in goal_ids]
    email = reply.get("parent_email") if isinstance(reply.get("parent_email"), str) else ""
    return {"summary": _clip(reply["summary"], 120), "iep_check": checks,
            "parent_email": _clip_lines(email, MAX_EMAIL_WORDS)}


def check_plan(reply: Any) -> dict:
    if not isinstance(reply, dict):
        raise DraftError("AI_BAD_REPLY")
    out = {}
    for k, n in (("goals", 6), ("objectives", 8)):
        items = [_clip(x, 30) for x in (reply.get(k) or []) if isinstance(x, str) and x.strip()][:n]
        out[k] = items
    if not (out["goals"] or out["objectives"]):
        raise DraftError("AI_BAD_REPLY")
    return out


# ---------------------------------------------------------------- offline stand-in (no network)

_STATUS_RANK = {"Demonstrated": 2, "Struggled": 1}


def _offline_summary(p: dict) -> dict:
    """Deterministic draft from the masked payload only - what a model would see."""
    sessions = p.get("approved_sessions") or []
    checks, shown = [], []
    for g in p.get("iep_goals") or []:
        st = [x.get("status") for s in sessions for x in s.get("goals") or [] if x.get("goal_id") == g["goal_id"]]
        n_demo, n_str = st.count("Demonstrated"), st.count("Struggled")
        cat = (g.get("category") or "this goal").lower()
        if n_demo:
            checks.append({"goal_id": g["goal_id"], "status": "evidence_seen",
                           "note": f"Approved notes show {cat} progress in {n_demo} of {len(sessions)} sessions."})
            shown.append(cat)
        elif n_str:
            checks.append({"goal_id": g["goal_id"], "status": "not_seen",
                           "note": f"Practiced in {n_str} session{'s' * (n_str != 1)} but not yet shown independently."})
        else:
            checks.append({"goal_id": g["goal_id"], "status": "not_enough",
                           "note": "Not observed in this week's approved notes."})
    days = ", ".join(s["day"] for s in sessions) or "no days"
    inc = sum(s.get("incidents") or 0 for s in sessions)
    text = (f"This week the student had {len(sessions)} approved session{'s' * (len(sessions) != 1)} ({days}). "
            + (f"Progress showed on the {' and '.join(shown)} goal{'s' * (len(shown) != 1)}. " if shown
               else "No goal was clearly shown yet. ")
            + (f"{inc} behavior incident{'s' * (inc != 1)} were noted and handled. " if inc else "")
            + ("The teacher's notes were taken into account." if p.get("teacher_notes") else ""))
    email = ("Hello,\n\n"
             + f"Here is a short update on the student's week. We had {len(sessions)} session"
             + f"{'s' * (len(sessions) != 1)} together ({days}). "
             + (f"The student made progress on {' and '.join(shown)}. " if shown
                else "We kept practicing the skills the student is working on. ")
             + (f"{inc} behavior incident{'s' * (inc != 1)} came up and the teacher handled "
                f"{'them' if inc != 1 else 'it'} in class. " if inc else "")
             + "Next week we will keep building on this. Please reach out with any questions.\n\nBest regards,")
    return {"summary": text.strip(), "iep_check": checks, "parent_email": email}


def _offline_plan(p: dict) -> dict:
    goals, objectives = [], []
    for st in p.get("students") or []:
        who = st["student"]
        checks = {c["goal_id"]: c["status"] for c in st.get("last_week_iep_check") or []}
        for g in st.get("iep_goals") or []:
            cat = (g.get("category") or "IEP").lower()
            if checks.get(g["goal_id"]) == "evidence_seen":
                goals.append(f"Build on {who}'s {cat} progress toward independence")
                objectives.append(f"{who.capitalize()} meets the {cat} goal in 4 of 5 chances, with fewer prompts")
            else:
                goals.append(f"Keep practicing the {cat} goal with {who}")
                objectives.append(f"Daily guided practice on the {cat} goal; record each try on the chart")
    objectives.append("Friday: short check on each goal and update the weekly summary")
    return {"goals": goals[:5], "objectives": objectives[:6]}


# ---------------------------------------------------------------- a small per-person limit

class Limiter:
    """At most `n` drafts per person per `window` seconds (per instance): keeps a stuck
    button or a script from running up AI cost."""

    def __init__(self, n: int = 20, window: float = 600.0, now=time.monotonic):
        self.n, self.window, self.now = n, window, now
        self.hits: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def allow(self, who: str) -> bool:
        t = self.now()
        with self.lock:
            h = [x for x in self.hits.get(who, []) if t - x < self.window]
            ok = len(h) < self.n
            if ok:
                h.append(t)
            self.hits[who] = h
            return ok
