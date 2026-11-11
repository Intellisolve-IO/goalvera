"""Prompt template from design spec §6 — versioned for the analyzer pack.

Privacy rule: the student's first name never goes to the AI. The prompt says
"the student", and the first name is also replaced with "the student" in the
transcript and the IEP goal text before sending. The name is put back into the
returned notes afterwards (reinsert_first_name), on our side.
"""
from __future__ import annotations

import json
import re
from typing import Any

from common.namematch import name_pattern

PROMPT_VERSION = "ocr-gemini-v4-2026-10"   # v4: Teacher Notes photos; v3: no per-session parent email
STUDENT_PLACEHOLDER = "the student"

SYSTEM_INSTRUCTION = """You are an expert Special Education Compliance Analyst and Behavioral Specialist. You will be provided a classroom session transcript containing speaker diarization, active IEP goals for assigned students, and flagged incident timestamps. Extract observable academic and behavioral indicators accurately and objectively without hallucinating data.

Rules:
- Use ONLY evidence present in the transcript. If a goal was not observable, status must be "Not Observed".
- Prefer short direct quotes for evidence_quote.
- Refer to the student only as "the student". Never guess or invent a name for them.
- Output JSON ONLY matching the required schema. No markdown fences."""

USER_TEMPLATE = """INPUT DATA:
Student: {student_label}
Active IEP Goals: {iep_goals_json}
Raw Transcript:
{transcript_text}
Incident Timestamps (Seconds): {incident_flags_json}

TASKS:
1. Cross-reference transcript dialogue and events against each IEP goal.
2. Determine progress status: "Demonstrated", "Struggled", or "Not Observed".
3. Extract direct supporting quotes from the transcript for each goal.
4. Analyze tagged Incident Timestamps using an Antecedent-Behavior-Consequence (ABC) framework:
   - Antecedent: What happened immediately prior to the flag?
   - Behavior: What specific observable behavior occurred?
   - Consequence/Intervention: What action did the educator take, and what was the outcome?

OUTPUT FORMAT (JSON ONLY):
{{
  "goal_evaluations": [
    {{
      "goal_id": "string",
      "status": "Demonstrated | Struggled | Not Observed",
      "evidence_quote": "string",
      "analysis": "string"
    }}
  ],
  "incident_analysis": [
    {{
      "timestamp_seconds": 0,
      "antecedent": "string",
      "behavior": "string",
      "consequence_intervention": "string"
    }}
  ],
  "general_session_summary": "string"
}}
"""


def _name_pattern(first_name: Any) -> re.Pattern | None:
    """Whole-word, case-insensitive match for the first name ("Marcus", "MARCUS", "Marcus's")."""
    if not isinstance(first_name, str):
        return None
    name = first_name.strip()
    if not name or name.casefold() in ("student", "the student"):
        return None
    if len(name) <= 2:      # short names too, as capitalized whole words (common/namematch.py)
        return name_pattern(name)
    return re.compile(rf"(?<![\w]){re.escape(name)}(?![\w])", re.IGNORECASE)


def redact_first_name(text: str, first_name: Any) -> str:
    pat = _name_pattern(first_name)
    return pat.sub(STUDENT_PLACEHOLDER, text) if (pat and isinstance(text, str)) else text


def _redact_obj(obj: Any, first_name: Any) -> Any:
    if isinstance(obj, str):
        return redact_first_name(obj, first_name)
    if isinstance(obj, list):
        return [_redact_obj(x, first_name) for x in obj]
    if isinstance(obj, dict):
        return {k: _redact_obj(v, first_name) for k, v in obj.items()}
    return obj


_PLACEHOLDER_RE = re.compile(r"\b([Tt])he student\b(?!s)")


def reinsert_first_name(obj: Any, first_name: Any) -> Any:
    """Put the first name back where the AI wrote "the student" / "The student"."""
    if _name_pattern(first_name) is None:
        return obj
    name = first_name.strip()
    if isinstance(obj, str):
        return _PLACEHOLDER_RE.sub(name, obj)
    if isinstance(obj, list):
        return [reinsert_first_name(x, first_name) for x in obj]
    if isinstance(obj, dict):
        return {k: reinsert_first_name(v, first_name) for k, v in obj.items()}
    return obj


def format_transcript_text(speaker_turns: list[dict]) -> str:
    lines = []
    for turn in speaker_turns:
        start = turn.get("start_seconds", 0)
        mm, ss = divmod(int(start), 60)
        speaker = turn.get("speaker", "Unknown")
        text = turn.get("text", "").strip()
        lines.append(f"[{mm:02d}:{ss:02d}] {speaker}: {text}")
    return "\n".join(lines)


IMAGE_TEMPLATE = """
PHOTOS (Teacher Notes): {count} photo(s) taken by the teacher during this session follow this text, in this
order, with media ids {media_ids_json}. For EACH photo add one entry to "image_insights":
  - "media_id": the photo's media id,
  - "observations": 1-3 sentences on what the photo shows that is relevant to the IEP goals or the
    session (e.g. how a worksheet was completed, colors used, accuracy, neatness), observable facts only,
  - "related_goal_ids": goal ids the photo is evidence for (may be empty).
Privacy rules for photos: never copy any name, initials, signature or other identifying text written
or printed on the photo; refer to the person only as "the student"; do not describe faces.
"""


def build_user_prompt(transcript: dict, media_ids: list[str] | None = None) -> str:
    """The text sent to the AI. Contains no student first name (see module doc).

    media_ids: the Teacher Notes photos sent alongside (as image parts, after this text)."""
    first = transcript.get("student_first_name")
    goals = _redact_obj(transcript.get("iep_goals", []), first)
    turns = _redact_obj(transcript.get("speaker_turns", []), first)
    text = USER_TEMPLATE.format(
        student_label=STUDENT_PLACEHOLDER,
        iep_goals_json=json.dumps(goals, indent=2),
        transcript_text=format_transcript_text(turns),
        incident_flags_json=json.dumps(transcript.get("incident_flags", [])),
    )
    if media_ids:
        text += IMAGE_TEMPLATE.format(count=len(media_ids), media_ids_json=json.dumps(media_ids))
    return text


def roster_first_names(roster: Any) -> list[str]:
    """Every "first_name" in the roster (any depth), for scrubbing AI photo notes."""
    out: list[str] = []
    if isinstance(roster, dict):
        for k, v in roster.items():
            if k in ("first_name", "student_first_name") and isinstance(v, str) and v.strip():
                out.append(v.strip())
            else:
                out += roster_first_names(v)
    elif isinstance(roster, list):
        for x in roster:
            out += roster_first_names(x)
    return sorted(set(out), key=len, reverse=True)


def scrub_image_insights(items: Any, names: list[str]) -> Any:
    """A name the AI read off a photo anyway becomes "a student" (our side, before review)."""
    if not isinstance(items, list):
        return items
    # case-sensitive capitalized whole words: demo names include ordinary words ("Happy", "Doc")
    pats = [re.compile(rf"(?<![\w]){re.escape(n)}(?![\w])") for n in names if len(n) > 1]
    out = []
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("observations"), str):
            text = it["observations"]
            for p in pats:
                text = p.sub("a student", text)
            it = {**it, "observations": text}
        out.append(it)
    return out
