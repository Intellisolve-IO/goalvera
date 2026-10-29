"""Short AI-written incident names (David, Oct 8 2026; Ops and Chief of Staff guardrails; tasks#85).

Each incident gets a name of 80 characters or fewer that summarizes what was seen or heard,
shown with its number: "Incident 1: Left seat and refused redirection during reading".
Since Oct 8 (David) the name comes from the transcript starting at the incident's marker: the
speaker turns in the next WINDOW_SECONDS (75 s by default, 60-90 via
INCIDENT_NAME_WINDOW_SECONDS); the incident's own notes go along as context, and alone when there
is no transcript.

  * Generated with the same Gemini client as the session analyzer and the planner
    (gemini_analyzer.analyzer._client: Vertex AI in our project), one small extra call per
    session for the incidents that have no name yet. INCIDENT_NAMES_AI = live | offline |
    off; default: the review service's PLANNER_AI setting.
  * What goes to the model: that transcript window and the incident notes, with roster student
    names replaced by "the student", staff names by "the teacher", emails / school ids / dates of
    birth / long numbers removed. Never the whole transcript, never word timings or audio.
  * Ops: observed behavior only (what was seen or heard). No diagnoses, no intent, no
    judgment words. That is in the prompt, and BLOCKED is checked on every name: a blocked
    word -> one regeneration -> still blocked -> no name ("Incident 1").
  * The same name scrub as the rest of the report (bug_reports.never_values: student first /
    last names, staff names, emails, school ids, dates of birth, long numbers): a name that
    contains any of them is not used ("Incident 1").
  * Stored create-only per incident, keyed by the incident's content:
    incident_names/<recording>/<key>.json. A teacher-edited name (name_source "teacher") is
    part of the notes and is never overwritten; an edited incident gets a fresh AI name only
    if it has none.
  * Existing sessions: named on first view (GET /api/sessions/<id>/incident_names), no backfill job.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re

from common.safelog import log

from .bug_reports import _EMAIL, _LONG_NUMBER, _word, never_values
from .store import Conflict

MAX_NAME = 80
PREFIX = "incident_names/"
PROMPT_VERSION = "incident-name-v2-2026-10"   # v2: named from the transcript window
WINDOW_DEFAULT, WINDOW_MIN, WINDOW_MAX = 75, 60, 90
WINDOW_CHARS = 2500                             # cap on the window text sent per incident
TEXT_KEYS = ("antecedent", "behavior", "consequence_intervention")
# Ops, Oct 8: no judgment, intent or diagnosis words. Stems match their forms (defiant, defiance...).
BLOCKED = re.compile(
    r"\b(defian\w*|aggress\w*|angr\w*|anger\w*|disrupt\w*|manipulat\w*|violen\w*|hostil\w*|rude\w*|"
    r"lazy|laziness|naughty|bad|oppositional|non-?complian\w*|uncooperative|attention[- ]seeking|"
    r"tantrum\w*|meltdown\w*|outburst\w*|explosive|out of control|misbehav\w*|bully\w*|threaten\w*|"
    r"intentional\w*|deliberate\w*|on purpose|purposeful\w*|wanted to|tried to|refused to learn|"
    r"adhd|autis\w*|anxious|anxiety|depress\w*|bipolar|trauma\w*|disorder\w*|"
    r"diagnos\w*|frustrat\w*|upset|stubborn|spoiled|lying|liar|sneaky)\b", re.I)
BLOCKED_ACRONYMS = re.compile(r"\b(ADHD|ADD|ODD|ASD|OCD|PTSD|EBD)\b")   # diagnoses; case-sensitive ("add 3 + 4" is fine)

NAME_SYSTEM = """You write short titles for incidents in a special-education classroom.
Each incident has "transcript": what was said in the classroom starting at the moment the incident was
marked (about the next minute, one line per speaker turn), and "notes": the teacher's draft notes.
For each incident, write one title of at most 80 characters that summarizes what was seen or heard,
based on the transcript (use the notes only as context, or when there is no transcript).

Rules:
- Describe ONLY behavior that was observed: what was seen or heard (for example "Left seat and refused redirection during reading").
- No diagnoses, no guesses about intent, feelings or motives, and no judgment words (never words like defiant, aggressive, angry, disruptive or manipulative).
- Never include any person's name, not even one heard in the transcript. Say "the student" for the student,
  "the teacher" for staff and "a peer" for any other child. Say nothing about who the student is.
- Do not capitalize any word except the first one (class and subject names like Circle Time or Math are fine).
- No quotes, no numbers or IDs, no ending period. Plain words, sentence case, start with a verb when you can.
- Use only what is in the incident's transcript and notes. The student is always called "the student".
Return JSON only: {"names": [{"index": <the incident's index>, "name": "<title>"}]}"""
RETRY_NOTE = """
Your previous titles for these incidents used a word that is not allowed (a judgment, intent or diagnosis word,
or a capitalized word that may be a person's name). Write them again describing only what was seen or heard,
with no names: "the student", "the teacher", "a peer"."""

# Chief of Staff, Oct 8: an AI name with a capitalized word after the first word may carry a name the
# roster doesn't know ("The student pushed Jayden at recess") -> retry once -> "Incident N".
# Allowed capitalized words (a fixed list, Oct 8): "I", weekdays, months, school activity / subject words,
# all-caps acronyms of 2-4 letters (ABC, IEP) and words with a digit
# (class codes like RM12). Only AI names are checked; a teacher's own name text is never checked here.
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
SCHOOL_WORDS = ("Circle", "Time", "Math", "Reading", "ELA", "Science", "PE", "Art", "Music", "Lunch", "Recess",
                "Writing", "Phonics", "Spelling", "Social", "Studies", "History", "Library", "Gym", "Snack",
                "Centers", "Morning", "Meeting", "Calendar", "Speech", "OT", "Specials", "Arrival", "Dismissal")
CAP_ALLOW = frozenset(("I", *WEEKDAYS, *MONTHS, *SCHOOL_WORDS))
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\u2019-]*")
_ACRONYM = re.compile(r"[A-Z]{2,4}")
NAME_SCHEMA = {"type": "object", "required": ["names"], "properties": {"names": {"type": "array", "items": {
    "type": "object", "required": ["index", "name"],
    "properties": {"index": {"type": "integer"}, "name": {"type": "string"}}}}}}


def mode() -> str:
    m = os.environ.get("INCIDENT_NAMES_AI")
    if m in ("live", "offline", "off"):
        return m
    from . import planner_ai
    return planner_ai.mode()


def window_seconds() -> int:
    try:
        w = int(os.environ.get("INCIDENT_NAME_WINDOW_SECONDS") or WINDOW_DEFAULT)
    except ValueError:
        w = WINDOW_DEFAULT
    return max(WINDOW_MIN, min(WINDOW_MAX, w))


def transcript_window(transcript: dict, start, never: dict, seconds: int | None = None) -> list[str]:
    """The speaker turns from the incident's marker for `seconds`, masked ("the student"), as
    "Speaker 2: ..." lines. A turn that started a little before the marker but runs past it counts."""
    try:
        start = float(start)
    except (TypeError, ValueError):
        return []
    end = start + (seconds or window_seconds())
    out, size = [], 0
    for turn in (transcript or {}).get("speaker_turns") or []:
        try:
            s, e = float(turn.get("start_seconds") or 0), float(turn.get("end_seconds") or turn.get("start_seconds") or 0)
        except (TypeError, ValueError):
            continue
        if e <= start or s >= end:
            continue
        text = " ".join(_mask(turn.get("text"), never).split())
        if not text:
            continue
        line = f"{turn.get('speaker') or 'Speaker'}: {text}"
        if size + len(line) > WINDOW_CHARS:
            break
        out.append(line)
        size += len(line)
    return out


def incident_key(inc: dict) -> str:
    """Storage key: the incident's content + the prompt version (a new prompt names again; a
    teacher-edited name is in the notes and is never replaced)."""
    raw = json.dumps([PROMPT_VERSION, inc.get("timestamp_seconds"), *(str(inc.get(k) or "") for k in TEXT_KEYS)],
                     ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def blocked(name: str) -> list[str]:
    hits = {m.group(0).lower() for m in BLOCKED.finditer(name or "")}
    hits |= {m.group(0) for m in BLOCKED_ACRONYMS.finditer(name or "")}
    return sorted(hits)


def clean_name(raw, never: dict) -> str | None:
    """The name as shown, or None (-> "Incident N") if it is empty or fails the scrub:
    a student / staff name, an email, a school id / date of birth, a long number, a placeholder."""
    if not isinstance(raw, str):
        return None
    s = " ".join(raw.split()).strip(" .\"'`\u201c\u201d")
    s = re.sub(r"^(incident\s*\d*\s*[:\-\u2013\u00b7]\s*)", "", s, flags=re.I).strip()
    if not s:
        return None
    if len(s) > MAX_NAME:
        cut = s[:MAX_NAME + 1].rsplit(" ", 1)[0] if " " in s[:MAX_NAME + 1] else s[:MAX_NAME]
        s = cut[:MAX_NAME].rstrip(" ,;:-\u2013")
    if "[" in s or "]" in s or _EMAIL.search(s) or _LONG_NUMBER.search(s):
        return None
    for v in never.get("other", []):
        if v.lower() in s.lower():
            return None
    for v in (*never.get("student", []), *never.get("staff", [])):
        if _word(v).search(s):
            return None
    return s[:1].upper() + s[1:]


def capitalized_words(name: str) -> list[str]:
    """Capitalized words after the first word that are not on the allowlist (possible names)."""
    words = _WORD_RE.findall(name or "")
    bad = []
    for w in words[1:]:
        base = re.sub(r"['\u2019]s$", "", w)
        if not base[:1].isupper() or base in CAP_ALLOW or any(c.isdigit() for c in base):
            continue
        if _ACRONYM.fullmatch(base):
            continue
        if "-" in base and all(p in CAP_ALLOW or not p[:1].isupper() for p in base.split("-")):
            continue
        bad.append(base)
    return bad


def usable(raw, never: dict) -> str | None:
    """A name that passes the scrub AND has no blocked word (teacher-edited names too)."""
    s = clean_name(raw, never)
    return s if s and not blocked(s) else None


def _mask(text: str, never: dict) -> str:
    out = _EMAIL.sub("", str(text or ""))
    for v in never["other"]:
        out = re.sub(re.escape(v), "", out, flags=re.I)
    for v in never["student"]:
        out = _word(v).sub("the student", out)
    for v in never["staff"]:
        out = _word(v).sub("the teacher", out)
    return _LONG_NUMBER.sub("", out)


def _offline(payload: dict) -> dict:
    """Local stand-in (tests, laptop demo): the first clause of the behavior note, else of the
    first transcript line in the window."""
    out = []
    for inc in payload["incidents"]:
        notes = inc.get("notes") or {}
        first = (inc.get("transcript") or [""])[0].split(": ", 1)[-1]
        b = re.split(r"(?<=[.;!?])\s|,\s(?:and\s)?(?:then\s)?", notes.get("behavior") or first)[0]
        b = re.sub(r"^(the student|he|she|they)\s+", "", b.strip(), flags=re.I).rstrip(".;!? ")
        out.append({"index": inc["index"], "name": b})
    return {"names": out}


def _gemini(system: str, payload: dict, schema: dict) -> dict:
    from gemini_analyzer.analyzer import _client, model_name
    client = _client()          # keep a reference: the client closes its HTTP session when collected
    r = client.models.generate_content(
        model=model_name(), contents=json.dumps(payload, ensure_ascii=False, indent=1),
        config={"system_instruction": system, "response_mime_type": "application/json",
                "response_json_schema": schema, "temperature": 0.2})
    text = (r.text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(text)


class Namer:
    def __init__(self, notes, *, call=None, now=None):
        self.notes = notes
        self._call = call
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))

    def call(self, system: str, payload: dict) -> dict:
        if self._call is not None:
            return self._call(system, payload, NAME_SCHEMA)
        m = mode()
        if m == "offline":
            return _offline(payload)
        if m == "live":
            return _gemini(system, payload, NAME_SCHEMA)
        raise RuntimeError("AI names off")

    # ------------------------------------------------------------ storage
    def stored(self, rid: str, names: list[str] | None = None) -> dict:
        """incident key -> stored doc (name may be None: the AI couldn't give a usable one)."""
        if names is None:
            try:
                names = self.notes.store.list(f"{PREFIX}{rid}/")
            except Exception as exc:  # noqa: BLE001 - no bucket access yet: no names, the session still loads
                if type(exc).__name__ in ("Forbidden", "PermissionError", "PermissionDenied"):
                    return {}
                raise
        out = {}
        for n in names:
            if n.endswith(".json"):
                out[n.rsplit("/", 1)[-1][:-5]] = self.notes.store.read_json(n) or {}
        return out

    def index(self) -> dict:
        out: dict = {}
        try:
            for n in self.notes.store.list(PREFIX):
                parts = n.split("/")
                if len(parts) == 3:
                    out.setdefault(parts[1], []).append(n)
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ not in ("Forbidden", "PermissionError", "PermissionDenied"):
                raise
        return out

    # ------------------------------------------------------------ merge into the notes
    def merge(self, analysis: dict, stored: dict, never: dict) -> dict:
        """The incidents with their names: a name already in the notes (teacher-edited, or approved)
        wins; else the stored AI name. Every name is re-checked (scrub + blocked words); one that
        fails is dropped, so the incident shows as "Incident N"."""
        out = dict(analysis or {})
        incs = []
        for inc in out.get("incident_analysis") or []:
            inc = dict(inc)
            if inc.get("name"):
                ok = usable(inc["name"], never)
                if ok:
                    inc["name"] = ok
                else:
                    inc.pop("name", None)
                    inc.pop("name_source", None)
            if not inc.get("name"):
                doc = stored.get(incident_key(inc)) or {}
                ok = usable(doc.get("name"), never)
                if ok and capitalized_words(ok):
                    ok = None
                if ok:
                    inc.update(name=ok, name_source="ai")
            incs.append(inc)
        if "incident_analysis" in out:
            out["incident_analysis"] = incs
        return out

    # ------------------------------------------------------------ generate (first view)
    def ensure(self, rid: str, analysis: dict, session: dict, transcript: dict | None = None) -> dict:
        """Name every incident that has no name and no stored AI name yet. Returns the stored docs."""
        never = never_values(self.notes.roster(), session, transcript or {})
        stored = self.stored(rid)
        todo = []
        for i, inc in enumerate((analysis or {}).get("incident_analysis") or []):
            if inc.get("name") or incident_key(inc) in stored:
                continue
            todo.append((i, inc))
        if not todo:
            return stored
        secs = window_seconds()
        payload = {"window_seconds": secs, "incidents": [
            {"index": i, "transcript": transcript_window(transcript or {}, inc.get("timestamp_seconds"), never, secs),
             "notes": {k: _mask(inc.get(k), never) for k in TEXT_KEYS}} for i, inc in todo]}
        try:
            got = self._names(payload, never)
        except Exception as exc:  # noqa: BLE001 - no AI right now: "Incident N" for now, try again next view
            log("incident_names_unavailable", "WARNING", step="review", recording_id=rid, error_type=type(exc).__name__)
            return stored
        for i, inc in todo:
            name, reason = got.get(i, (None, "missing"))
            doc = {"name": name, "source": "ai" if name else "fallback", "reason": reason,
                   "prompt_version": PROMPT_VERSION, "mode": mode() if self._call is None else "test",
                   "at": self._now().isoformat(timespec="seconds")}
            key = incident_key(inc)
            try:
                self.notes.store.write_json(f"{PREFIX}{rid}/{key}.json", doc, create_only=True)
            except Conflict:
                doc = self.notes.store.read_json(f"{PREFIX}{rid}/{key}.json") or doc
            stored[key] = doc
        log("incident_names_generated", step="review", recording_id=rid, status=f"{len(todo)}_incidents")
        return stored

    def _names(self, payload: dict, never: dict) -> dict:
        """index -> (name or None, reason). Blocked word -> regenerate once -> fallback."""
        def run(system, p):
            reply = self.call(system, p)
            out = {}
            for r in (reply or {}).get("names") or []:
                if isinstance(r, dict) and isinstance(r.get("index"), int):
                    out[r["index"]] = r.get("name")
            return out

        first = run(NAME_SYSTEM, payload)
        result, retry = {}, []
        for inc in payload["incidents"]:
            i = inc["index"]
            name = clean_name(first.get(i), never)
            if name is None:
                result[i] = (None, "scrub")
            elif blocked(name) or capitalized_words(name):
                retry.append(inc)
            else:
                result[i] = (name, "ok")
        if retry:
            second = run(NAME_SYSTEM + RETRY_NOTE, {**payload, "incidents": retry})
            for inc in retry:
                i = inc["index"]
                name = clean_name(second.get(i), never)
                if name is None:
                    result[i] = (None, "scrub")
                elif blocked(name):
                    result[i] = (None, "blocked_word")
                elif capitalized_words(name):
                    result[i] = (None, "capitalized_word")
                else:
                    result[i] = (name, "ok_after_retry")
        return result
