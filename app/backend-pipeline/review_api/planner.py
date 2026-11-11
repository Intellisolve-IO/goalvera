"""Weekly lesson planner - first working version, demo data only.

What the screen shows (product spec): a weekly calendar with last week, this
week and next week. This week stays anchored; the arrows step the "last week"
slot back through earlier weeks. Each week opens with its lesson plan summary
(goals and objectives as bullets), then the week's plan, then one row per
student: a weekly summary and a teacher note, checked against the student's
IEP goals (fictional goals from the class roster sample until lands).

Nothing here calls the AI. The per-student "suggestion" is counted from the
week's APPROVED session notes (Demonstrated / Struggled / Not Observed per
goal) - no transcript text and no quotes are copied into it. An AI-drafted
weekly summary is the next slice and will go through the same "the student"
de-identification as the session analyzer (gemini_analyzer.prompt).

Storage (same bucket, prefix planner/; NOT on the 30-day auto-delete rule -
kept until the school data is deleted, then removed with infra/delete_planner.py):
  planner/plans/<class_key>/<week>/<ts>_<rand>.json             one per save
  planner/students/<student_id>/<week>/summary/<ts>_<rand>.json  one per save
  planner/students/<student_id>/<week>/note/<ts>_<rand>.json     one per save
  planner/materials/<class_key>/<week>/<material>.json         worksheets / materials on that week's
                                    lesson plan (materials.py; removed = <material>.removed.json)
Every save is a NEW create-only object (the review service never overwrites
or deletes); the newest one is the current version, older ones are history.
The student's first name is stored as "the student" and put back on read.

Saves are refused (422 QUOTES_TRANSCRIPT) if any 8 words in a row match a
transcript of that week's sessions (the same check as the speaker-timeline
save), so planner text can't carry transcript quotes while transcripts exist.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import secrets
from zoneinfo import ZoneInfo

from common.classcodes import class_key, find_class
from common.roster import class_students, classrooms_with_students, find_student
from gemini_analyzer.prompt import STUDENT_PLACEHOLDER, redact_first_name, reinsert_first_name

from . import planner_ai as ai
from .notes import (RID_RE, ApiError, actor_id, check_actor, is_read_only, rbt_iep_access,
                    signed_in)

SCHOOL_TZ = ZoneInfo(os.environ.get("PLANNER_TZ", "America/New_York"))
QUOTE_WORDS = 8
MAX_WEEKS_BACK = 52
MAX_ITEMS, MAX_ITEM, MAX_TEXT, MAX_NOTE = 12, 300, 6000, 300
IEP_CHECK = ("evidence_seen", "not_seen", "not_enough")
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_WEEK = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SID = re.compile(r"^[a-z0-9_]{1,64}$")


# ---------------------------------------------------------------- weeks

def today() -> dt.date:
    """The school's date (America/New_York). PLANNER_TODAY=YYYY-MM-DD pins it for
    tests and screenshots; ignored on Cloud Run."""
    pin = os.environ.get("PLANNER_TODAY")
    if pin and not os.environ.get("K_SERVICE"):
        return dt.date.fromisoformat(pin)
    return dt.datetime.now(SCHOOL_TZ).date()


def week_of(d: dt.date) -> dt.date:
    """Monday of d's week."""
    return d - dt.timedelta(days=d.weekday())


def parse_week(s) -> dt.date:
    if not isinstance(s, str) or not _WEEK.match(s):
        raise ApiError(400, "BAD_WEEK")
    try:
        d = dt.date.fromisoformat(s)
    except ValueError as exc:
        raise ApiError(400, "BAD_WEEK") from exc
    if d.weekday() != 0:
        raise ApiError(400, "BAD_WEEK")
    return d


def three_weeks(past: str | None = None, *, now: dt.date | None = None) -> dict:
    """{"past", "current", "next"} Mondays. current = this week, always (anchored).
    past = last week unless the arrows moved it to an earlier week; never later."""
    current = week_of(now or today())
    last = current - dt.timedelta(days=7)
    p = last if past in (None, "") else parse_week(past)
    if p > last or p < current - dt.timedelta(days=7 * MAX_WEEKS_BACK):
        raise ApiError(400, "BAD_WEEK")
    return {"past": p, "current": current, "next": current + dt.timedelta(days=7)}


def session_date(s: dict) -> dt.date | None:
    ts = s.get("timestamp_start")
    if not isinstance(ts, str):
        return None
    try:
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is not None:                   # device times are local; a zoned one is converted
        t = t.astimezone(SCHOOL_TZ)
    return t.date()


# ---------------------------------------------------------------- the 8-word check

def words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower().replace("\u2019", "'"))


def quoted_run(text: str, transcripts: list[dict], n: int = QUOTE_WORDS) -> str | None:
    """The first n-word run of `text` that also appears in a transcript turn, or None."""
    mine = words(text)
    if len(mine) < n:
        return None
    runs = set()
    for t in transcripts:
        for turn in (t or {}).get("speaker_turns") or []:
            w = words(turn.get("text") or "")
            runs.update(tuple(w[i:i + n]) for i in range(len(w) - n + 1))
    for i in range(len(mine) - n + 1):
        if tuple(mine[i:i + n]) in runs:
            return " ".join(mine[i:i + n])
    return None


# ---------------------------------------------------------------- input checks

def _text(v, limit: int, field: str, *, required=False) -> str:
    if v is None:
        v = ""
    if not isinstance(v, str) or len(v) > limit:
        raise ApiError(400, f"BAD_{field.upper()}")
    v = v.strip()
    if required and not v:
        raise ApiError(400, f"BAD_{field.upper()}")
    return v


def _items(v, field: str) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > MAX_ITEMS:
        raise ApiError(400, f"BAD_{field.upper()}")
    return [x for x in (_text(i, MAX_ITEM, field) for i in v) if x]


def _stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + secrets.token_hex(3)


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


_DRAFT_HTTP = {"AI_DRAFTS_OFF": 404, "AI_UNAVAILABLE": 502, "AI_BAD_REPLY": 502,
               "AI_DRAFT_QUOTES_TRANSCRIPT": 422, "TOO_MANY_DRAFTS": 429, "NOTHING_TO_DRAFT_FROM": 409}
_AI_SOURCES = ("offline_stand_in", "gemini_live", "vertex_gemini_live")
_MODEL = re.compile(r"^[a-z0-9][a-z0-9.\-]{0,63}$")


def _ai_assisted(v) -> dict | None:
    """The teacher saved an AI draft (edited or not): which draft source made it."""
    if v in (None, {}):
        return None
    if not isinstance(v, dict) or v.get("source") not in _AI_SOURCES \
            or v.get("prompt_version") != ai.PROMPT_VERSION \
            or not (v.get("model") is None or (isinstance(v.get("model"), str) and _MODEL.match(v["model"]))):
        raise ApiError(400, "BAD_AI_DRAFT")
    return {"source": v["source"], "model": v.get("model"), "prompt_version": v["prompt_version"]}


class Planner:
    def __init__(self, notes, *, limiter: ai.Limiter | None = None):
        self.notes = notes
        self.store = notes.store
        self.rec = notes.recorder
        self.limiter = limiter or ai.Limiter()

    # ------------------------------------------------------------ roster
    def _class(self, code):
        display, entry = find_class(self.notes.roster(), code)
        if not display:
            raise ApiError(404, "UNKNOWN_CLASSROOM")
        return display, entry, class_key(display)

    def _students(self, entry: dict, display: str | None = None) -> list[dict]:
        """The classroom's students: roster.students listing the classroom (many per classroom,
        Oct 10) plus an old-style class entry's own student (common/roster.py)."""
        return class_students(self.notes.roster(), display, entry)

    def _student(self, sid) -> tuple[dict, str]:
        if not isinstance(sid, str) or not _SID.match(sid):
            raise ApiError(400, "BAD_STUDENT")
        st, display = find_student(self.notes.roster(), sid)
        if st is None:
            raise ApiError(404, "UNKNOWN_STUDENT")
        return st, display

    # ------------------------------------------------------------ who may change what
    def may_edit_plan(self, actor, entry: dict) -> bool:
        if actor is None or is_read_only(actor):
            return False
        if signed_in(actor):
            return bool(actor.is_admin or entry.get("teacher_id") in actor.teacher_ids)
        return actor == entry.get("teacher_id")

    def may_edit_student(self, actor, sid: str) -> bool:
        if actor is None or is_read_only(actor):
            return False
        owners = self.notes.student_teacher_ids(sid)
        if signed_in(actor):
            return bool(actor.is_admin or (owners & set(actor.teacher_ids)))
        return actor in owners

    def _iep_hidden(self, actor) -> bool:
        return is_read_only(actor) and rbt_iep_access(self.notes.roster()) == "none"

    # ------------------------------------------------------------ sessions
    def _sessions(self, ckey: str | None = None, student_id: str | None = None) -> list[dict]:
        """Every session of a classroom (or a student), with its week, approved notes and
        whether its transcript still exists (it goes after 30 days)."""
        out = []
        changes = self.notes.classroom_changes()
        links = self.notes.session_student_links()
        for name in self.store.list("drafts/"):
            if not name.endswith(".json"):
                continue
            rid = name[len("drafts/"):-len(".json")]
            if not RID_RE.match(rid):
                continue
            d = self.store.read_json(name) or {}
            s = self.notes._context(rid, d, changes.get(rid, []), links.get(rid), _links_known=True)
            if ckey and s.get("class_key") != ckey:
                continue
            if student_id and student_id not in (s.get("student_ids") or []):   # students in the session
                continue
            day = session_date(s)
            if day is None:
                continue
            ap = self.notes.approval(rid)
            out.append({"recording_id": rid, "date": day.isoformat(), "week": week_of(day),
                        "student_id": s.get("student_id"), "student_ids": s.get("student_ids") or [],
                        "approved": ap is not None,
                        "analysis": (ap or {}).get("analysis") if ap else None})
        out.sort(key=lambda x: (x["date"], x["recording_id"]))
        return out

    def _transcripts(self, sessions: list[dict]) -> list[dict]:
        return [t for t in (self.store.read_json(f"transcripts/{s['recording_id']}.json") for s in sessions) if t]

    # ------------------------------------------------------------ versions
    def _latest(self, prefix: str) -> tuple[dict | None, int]:
        names = [n for n in self.store.list(prefix) if n.endswith(".json")]
        if not names:
            return None, 0
        return self.store.read_json(sorted(names)[-1]), len(names)

    def _save(self, prefix: str, doc: dict) -> dict:
        self.store.write_json(f"{prefix}{_stamp()}.json", doc, create_only=True)
        return doc

    # ------------------------------------------------------------ the screen
    def default_class(self, actor) -> str | None:
        """No class asked for: the viewer's default classroom (notes.default_classroom), else their own
        classroom that has a student, else the first such. The planner shows one class, so admins (no
        default) get the first classroom with a student."""
        codes = self.notes.roster().get("class_codes") or {}
        with_students = classrooms_with_students(self.notes.roster())
        if signed_in(actor):
            d = self.notes.default_classroom(actor)          # the teacher's default classroom
            if d in codes:
                return d
        home = self.notes.home_classrooms(actor) if signed_in(actor) else \
            [d for d, e in codes.items() if isinstance(e, dict) and e.get("teacher_id") == actor]
        mine = [d for d in home if d in with_students]
        return (mine or with_students or list(codes) or [None])[0]

    def view(self, actor, class_code, past: str | None = None) -> dict:
        if class_code in (None, ""):
            class_code = self.default_class(actor)
        display, entry, ckey = self._class(class_code)
        weeks = three_weeks(past)
        students = self._students(entry, display)
        hide_iep = self._iep_hidden(actor)
        sessions = self._sessions(ckey)
        out_weeks = []
        for slot in ("past", "current", "next"):
            wk = weeks[slot]
            plan, versions = self._latest(f"planner/plans/{ckey}/{wk.isoformat()}/")
            if plan and len(students) == 1:
                plan = reinsert_first_name(plan, students[0]["first_name"])
            rows = []
            for st in students:
                mine = [s for s in sessions if s["week"] == wk and st["student_id"] in s["student_ids"]]
                base = f"planner/students/{st['student_id']}/{wk.isoformat()}/"
                summary, sv = self._latest(base + "summary/")
                note, nv = self._latest(base + "note/")
                approved = [s for s in mine if s["approved"]]
                row = {
                    "student_id": st["student_id"],
                    "name": f"{st['first_name']} {st['last_initial']}".strip(),
                    "sessions": [{"recording_id": s["recording_id"], "date": s["date"],
                                  "approved": s["approved"]} for s in mine],
                    "transcripts_available": len(self._transcripts(mine)) if mine else 0,
                    "summary": reinsert_first_name(_public(summary), st["first_name"]) if summary else None,
                    "summary_versions": sv,
                    "note": reinsert_first_name(_public(note), st["first_name"]) if note else None,
                    "note_versions": nv,
                    "suggestion": suggest(st, approved) if approved else None,
                    "can_edit": self.may_edit_student(actor, st["student_id"]),
                }
                if row["summary"] and "parent_email" in row["summary"] and not row["can_edit"]:
                    # the weekly parent email: the student's teachers and admins only (not RBTs or
                    # other teachers), the same people who saw it on sessions before
                    row["summary"] = {k: v for k, v in row["summary"].items() if k != "parent_email"}
                    row["parent_email_hidden"] = True
                if hide_iep:
                    row.update(iep_goals=[], iep_hidden=True)
                    if row["summary"]:
                        row["summary"] = {**row["summary"], "iep_check": []}
                    if row["suggestion"]:
                        row["suggestion"] = {**row["suggestion"], "iep_check": []}
                else:
                    row["iep_goals"] = st["iep_goals"]
                rows.append(row)
            out_weeks.append({"slot": slot, "week": wk.isoformat(),
                              "week_end": (wk + dt.timedelta(days=4)).isoformat(),
                              "plan": _public(plan) if plan else None, "plan_versions": versions,
                              "students": rows})
        if students and not hide_iep:
            for st in students:
                self.rec.record(actor, "iep_viewed", student_id=st["student_id"], required=is_read_only(actor))
        self.rec.record(actor, "planner_viewed", record_id=ckey, required=is_read_only(actor),
                        week=weeks["current"].isoformat(), past_week=weeks["past"].isoformat())
        codes = self.notes.roster().get("class_codes") or {}
        return {"class_code": display, "class_key": ckey, "today": today().isoformat(),
                "classrooms": [{"class_code": d, "students": len(class_students(self.notes.roster(), d, e))}
                               for d, e in sorted(codes.items()) if isinstance(e, dict)],
                "this_week": weeks["current"].isoformat(),
                "last_week": (weeks["current"] - dt.timedelta(days=7)).isoformat(),
                "earliest_week": (weeks["current"] - dt.timedelta(days=7 * MAX_WEEKS_BACK)).isoformat(),
                "can_edit_plan": self.may_edit_plan(actor, entry),
                "ai_drafts": ai.mode(),
                "weeks": out_weeks}

    # ------------------------------------------------------------ saves
    def _check_week(self, s) -> dt.date:
        wk = parse_week(s)
        w = three_weeks()
        if wk > w["next"] or wk < w["current"] - dt.timedelta(days=7 * MAX_WEEKS_BACK):
            raise ApiError(400, "BAD_WEEK")
        return wk

    def _no_quotes(self, texts: list[str], sessions: list[dict]):
        hit = quoted_run("\n".join(t for t in texts if t), self._transcripts(sessions))
        if hit:
            raise QuoteRefused(hit)

    def save_plan(self, actor, body: dict) -> dict:
        actor = check_actor(actor)
        display, entry, ckey = self._class(body.get("class_code"))
        if not self.may_edit_plan(actor, entry):
            raise ApiError(403, "NOT_YOUR_CLASSROOM")
        wk = self._check_week(body.get("week"))
        goals, objectives = _items(body.get("goals"), "goals"), _items(body.get("objectives"), "objectives")
        plan = _text(body.get("plan"), MAX_TEXT, "plan")
        if not (goals or objectives or plan):
            raise ApiError(400, "EMPTY_PLAN")
        approve = body.get("approve") is True
        week_sessions = [s for s in self._sessions(ckey) if s["week"] == wk]
        self._no_quotes([*goals, *objectives, plan], week_sessions)
        names = [s["first_name"] for s in self._students(entry, display)]
        red = lambda t: _redact_all(t, names)  # noqa: E731
        doc = {"kind": "plan", "class_key": ckey, "week": wk.isoformat(),
               "goals": [red(g) for g in goals], "objectives": [red(o) for o in objectives],
               "plan": red(plan), "status": "approved" if approve else "draft",
               **({"ai_assisted": a} if (a := _ai_assisted(body.get("ai"))) else {}),
               "saved_by": actor_id(actor), "saved_at": _now_iso(),
               **({"approved_by": actor_id(actor), "approved_at": _now_iso()} if approve else {})}
        self._save(f"planner/plans/{ckey}/{wk.isoformat()}/", doc)
        self.rec.record(actor, "planner_plan_saved", record_id=ckey, week=wk.isoformat(), approved=approve)
        return reinsert_first_name(_public(doc), names[0]) if len(names) == 1 else _public(doc)

    def save_summary(self, actor, body: dict) -> dict:
        actor = check_actor(actor)
        st, _ = self._student(body.get("student_id"))
        if not self.may_edit_student(actor, st["student_id"]):
            raise ApiError(403, "NOT_YOUR_STUDENT")
        wk = self._check_week(body.get("week"))
        text = _text(body.get("summary"), MAX_TEXT, "summary", required=True)
        goal_ids = [g["goal_id"] for g in st["iep_goals"]]
        check = body.get("iep_check") or []
        if not isinstance(check, list) or len(check) > len(goal_ids):
            raise ApiError(400, "BAD_IEP_CHECK")
        rows, seen = [], set()
        for c in check:
            if not isinstance(c, dict) or c.get("goal_id") not in goal_ids or c.get("goal_id") in seen \
                    or c.get("status") not in IEP_CHECK:
                raise ApiError(400, "BAD_IEP_CHECK")
            seen.add(c["goal_id"])
            rows.append({"goal_id": c["goal_id"], "status": c["status"],
                         "note": _text(c.get("note"), MAX_NOTE, "iep_check")})
        # the week's draft parent email (Oct 8: moved here from each session). Never sent.
        email = _text(body.get("parent_email"), MAX_TEXT, "parent_email")
        approve = body.get("approve") is True
        week_sessions = [s for s in self._sessions(student_id=st["student_id"]) if s["week"] == wk]
        self._no_quotes([text, *(r["note"] for r in rows), email], week_sessions)
        fn = st["first_name"]
        doc = {"kind": "summary", "student_id": st["student_id"], "week": wk.isoformat(),
               "summary": redact_first_name(text, fn),
               "iep_check": [{**r, "note": redact_first_name(r["note"], fn)} for r in rows],
               **({"parent_email": redact_first_name(email, fn)} if email else {}),
               "source_sessions": [s["recording_id"] for s in week_sessions if s["approved"]],
               "status": "approved" if approve else "draft",
               **({"ai_assisted": a} if (a := _ai_assisted(body.get("ai"))) else {}),
               "saved_by": actor_id(actor), "saved_at": _now_iso(),
               **({"approved_by": actor_id(actor), "approved_at": _now_iso()} if approve else {})}
        self._save(f"planner/students/{st['student_id']}/{wk.isoformat()}/summary/", doc)
        self.rec.record(actor, "planner_summary_saved", student_id=st["student_id"], record_id=st["student_id"],
                        week=wk.isoformat(), approved=approve)
        return reinsert_first_name(_public(doc), fn)

    def save_note(self, actor, body: dict) -> dict:
        actor = check_actor(actor)
        st, _ = self._student(body.get("student_id"))
        if not self.may_edit_student(actor, st["student_id"]):
            raise ApiError(403, "NOT_YOUR_STUDENT")
        wk = self._check_week(body.get("week"))
        text = _text(body.get("note"), MAX_TEXT, "note")
        week_sessions = [s for s in self._sessions(student_id=st["student_id"]) if s["week"] == wk]
        self._no_quotes([text], week_sessions)
        fn = st["first_name"]
        doc = {"kind": "note", "student_id": st["student_id"], "week": wk.isoformat(),
               "note": redact_first_name(text, fn), "saved_by": actor_id(actor), "saved_at": _now_iso()}
        self._save(f"planner/students/{st['student_id']}/{wk.isoformat()}/note/", doc)
        self.rec.record(actor, "planner_note_saved", student_id=st["student_id"], record_id=st["student_id"],
                        week=wk.isoformat())
        return reinsert_first_name(_public(doc), fn)


    # ------------------------------------------------------------ AI drafts (planner_ai.py; not stored)
    def _other_student_names(self, keep: list[str]) -> list[str]:
        """First and last names of every roster student not in `keep` (hidden as "another student")."""
        from common.roster import roster_students
        out = set()
        for sid, st in roster_students(self.notes.roster()).items():
            if sid in keep:
                continue
            for k in ("first_name", "last_name"):
                v = st.get(k)
                if isinstance(v, str) and v.strip():
                    out.add(v.strip())
        return sorted(out, key=lambda n: (-len(n), n))

    def _teacher_names(self) -> list[str]:
        r = self.notes.roster()
        names = [t.get("teacher_name") for t in (r.get("teachers") or {}).values() if isinstance(t, dict)]
        names += [t.get("name") for k in ("rbts", "staff") for t in (r.get(k) or {}).values() if isinstance(t, dict)]
        # My profile: a display / preferred name is a name too
        names += [t.get(f) for k in ("teachers", "rbts", "staff") for t in (r.get(k) or {}).values()
                  if isinstance(t, dict) for f in ("display_name", "preferred_name")]
        names += [e.get("teacher_name") for e in (r.get("class_codes") or {}).values() if isinstance(e, dict)]
        return sorted({n for n in names if isinstance(n, str)})

    def _draft(self, actor, system, payload, schema, transcripts):
        if not self.limiter.allow(actor_id(actor)):
            raise ai.DraftError("TOO_MANY_DRAFTS")
        reply, source, model = ai._call(system, payload, schema)
        return reply, {"source": source, "model": model, "prompt_version": ai.PROMPT_VERSION}

    def draft_summary(self, actor, body: dict) -> dict:
        actor = check_actor(actor)
        st, _ = self._student(body.get("student_id"))
        if not self.may_edit_student(actor, st["student_id"]):
            raise ApiError(403, "NOT_YOUR_STUDENT")
        wk = self._check_week(body.get("week"))
        if wk > week_of(today()):
            raise ApiError(400, "BAD_WEEK")
        week_sessions = [s for s in self._sessions(student_id=st["student_id"]) if s["week"] == wk]
        approved = [s for s in week_sessions if s["approved"]]
        note, _ = self._latest(f"planner/students/{st['student_id']}/{wk.isoformat()}/note/")
        notes = [note["note"]] if note and note.get("note") else []
        try:
            if not (approved or notes):
                raise ai.DraftError("NOTHING_TO_DRAFT_FROM")
            # a classroom session can mention classmates: every other student's name is hidden too
            mask = ai.Mask([st["first_name"]], self._teacher_names(), others=self._other_student_names([st["student_id"]]))
            payload = ai.summary_payload(week_label=_week_label(wk), iep_goals=st["iep_goals"],
                                         sessions=approved, notes=notes, mask=mask)
            reply, meta = self._draft(actor, ai.SUMMARY_SYSTEM, payload, ai.SUMMARY_SCHEMA, None)
            draft = mask.show(ai.check_summary(reply, [g["goal_id"] for g in st["iep_goals"]]))
            if quoted_run("\n".join([draft["summary"], *(c["note"] for c in draft["iep_check"]),
                                     draft.get("parent_email") or ""]),
                          self._transcripts(week_sessions)):
                raise ai.DraftError("AI_DRAFT_QUOTES_TRANSCRIPT")
        except ai.DraftError as e:
            raise ApiError(_DRAFT_HTTP.get(e.code, 502), e.code) from e
        self.rec.record(actor, "planner_summary_drafted", student_id=st["student_id"], record_id=st["student_id"],
                        week=wk.isoformat(), ai_source=meta["source"])
        return {**draft, "student_id": st["student_id"], "week": wk.isoformat(), "ai": meta,
                "based_on": {"approved_sessions": len(approved), "teacher_notes": len(notes)}}

    def draft_plan(self, actor, body: dict) -> dict:
        actor = check_actor(actor)
        display, entry, ckey = self._class(body.get("class_code"))
        if not self.may_edit_plan(actor, entry):
            raise ApiError(403, "NOT_YOUR_CLASSROOM")
        wk = self._check_week(body.get("week"))
        if wk < week_of(today()):
            raise ApiError(400, "BAD_WEEK")
        prev = wk - dt.timedelta(days=7)
        sessions = self._sessions(ckey)
        students, summaries = self._students(entry, display), 0
        rows = []
        for st in students:
            base = f"planner/students/{st['student_id']}/{prev.isoformat()}/"
            summary, _ = self._latest(base + "summary/")
            kind = "teacher_saved" if summary else None
            if not summary:
                approved = [s for s in sessions if s["week"] == prev and st["student_id"] in s["student_ids"]
                            and s["approved"]]
                summary = suggest(st, approved) if approved else None
                kind = "counted_from_approved_notes" if summary else "none"
            note, _ = self._latest(base + "note/")
            summaries += bool(summary)
            rows.append({"first_name": st["first_name"], "iep_goals": st["iep_goals"], "summary": summary,
                         "summary_kind": kind, "notes": [note["note"]] if note and note.get("note") else []})
        prev_plan, _ = self._latest(f"planner/plans/{ckey}/{prev.isoformat()}/")
        try:
            if not (summaries or prev_plan or any(r["notes"] for r in rows)):
                raise ai.DraftError("NOTHING_TO_DRAFT_FROM")
            mask = ai.Mask([st["first_name"] for st in students], self._teacher_names(),
                           others=self._other_student_names([st["student_id"] for st in students]))
            payload = ai.plan_payload(week_label=_week_label(wk), previous_week_label=_week_label(prev),
                                      students=rows, previous_plan=prev_plan, mask=mask)
            reply, meta = self._draft(actor, ai.PLAN_SYSTEM, payload, ai.PLAN_SCHEMA, None)
            draft = mask.show(ai.check_plan(reply))
            near = [s for s in sessions if s["week"] in (prev, wk)]
            if quoted_run("\n".join(draft["goals"] + draft["objectives"]), self._transcripts(near)):
                raise ai.DraftError("AI_DRAFT_QUOTES_TRANSCRIPT")
        except ai.DraftError as e:
            raise ApiError(_DRAFT_HTTP.get(e.code, 502), e.code) from e
        self.rec.record(actor, "planner_plan_drafted", record_id=ckey, week=wk.isoformat(), ai_source=meta["source"])
        return {**draft, "class_code": display, "week": wk.isoformat(), "based_on_week": prev.isoformat(), "ai": meta,
                "based_on": {"summaries": summaries, "previous_plan": bool(prev_plan),
                             "teacher_notes": sum(len(r["notes"]) for r in rows)}}


def _week_label(wk: dt.date) -> str:
    return f"{wk.isoformat()} to {(wk + dt.timedelta(days=4)).isoformat()}"


class QuoteRefused(ApiError):
    def __init__(self, run: str):
        super().__init__(422, "QUOTES_TRANSCRIPT")
        self.run = run


def _redact_all(text: str, names: list[str]) -> str:
    for n in names:
        text = redact_first_name(text, n)
    return text


def _public(doc: dict | None) -> dict | None:
    """What the screen gets: the saved fields minus internal bookkeeping."""
    if not doc:
        return doc
    return {k: v for k, v in doc.items() if k not in ("kind",)}


# ---------------------------------------------------------------- the no-AI suggestion

_RANK = {"Demonstrated": "evidence_seen", "Struggled": "not_seen"}


def suggest(student: dict, approved: list[dict]) -> dict:
    """A starting point for the teacher's weekly summary, counted from the week's
    approved session notes. Statuses and counts only - no transcript text, no quotes,
    no AI. Uses "the student" so it reads the same as AI drafts will."""
    n = len(approved)
    rows = []
    for g in student["iep_goals"]:
        st = [ev.get("status") for s in approved
              for ev in ((s.get("analysis") or {}).get("goal_evaluations") or []) if ev.get("goal_id") == g["goal_id"]]
        shown, tried = st.count("Demonstrated"), st.count("Struggled")
        if shown:
            status, note = "evidence_seen", f"Demonstrated in {shown} of {n} approved session{'s' * (n != 1)}."
        elif tried:
            status, note = "not_seen", f"Worked on but not yet shown ({tried} of {n} approved session{'s' * (n != 1)})."
        else:
            status, note = "not_enough", f"Not observed in {n} approved session{'s' * (n != 1)}."
        rows.append({"goal_id": g["goal_id"], "status": status, "note": note})
    incidents = sum(len((s.get("analysis") or {}).get("incident_analysis") or []) for s in approved)
    shown_goals = sum(r["status"] == "evidence_seen" for r in rows)
    text = (f"{n} approved session{'s' * (n != 1)} this week. {STUDENT_PLACEHOLDER.capitalize()} showed progress "
            f"on {shown_goals} of {len(rows)} IEP goal{'s' * (len(rows) != 1)}"
            + (f"; {incidents} behavior incident{'s' * (incidents != 1)} noted." if incidents else ".")
            if rows else f"{n} approved session{'s' * (n != 1)} this week.")
    return {"summary": reinsert_first_name(text, student["first_name"]), "iep_check": rows,
            "source": "approved_session_notes"}
