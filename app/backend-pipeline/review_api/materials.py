"""Classroom materials from Teacher Notes.

When a teacher opens a session to assign students, each "Teacher Notes" marker whose speech looks
like a request for printable materials shows up to 3 suggested actions. The teacher picks one,
adjusts it in a small form (type, count, topic, students, due date), and the app makes 3 draft pages
(a worksheet, a coloring page, flashcards or classroom labels) as SVG with a Gemini text model under
the admin-editable system instruction (material_svg.py), checks and cleans each SVG, puts the school
header on it and renders it server-side to PNG previews and a printable PDF. It is shown on the page to
download and, when the school's admin has email delivery on, emailed to the teacher's own staff
address. Nothing is made or sent until the teacher picks.

Rules (Chief of Staff / Ops, Oct 10):
  * Email goes only to a teacher's own staff address on file (roster teachers.<id>.emails[0]):
    the teacher who clicked, or, when an admin clicks, the session's teacher. Never an address
    from a note, the form, a parent field or the browser. Domain allowlist in mailer.py.
  * The email: fixed template, the PDF, a link back to the session, student first names only,
    no IEP details, no last names, no ids (check_email()).
  * Names never go to the AI: the note's transcript window and all context are masked ("the
    student", "student 1", "the teacher"); the brief and the SVG text that come back are scrubbed again.
  * What may go to the AI: the masked note window, the teacher's choices, and per chosen student
    the grade, IEP goal ids / categories / descriptions, accommodations and up to 3 recent
    teacher-approved session summaries (all masked). Never: present levels, eligibility, ids,
    emails, parent data, other transcript text, audio.
  * Human in the loop: made only on a click; every page says "Suggestion - review before use".
  * Every step is journaled (ids, type, counts, PDF fingerprint; no content, no addresses).
  * Fictional data only (Sample Elementary School) until the DPA amendment.

Storage (CMEK bucket, create-only; materials/ expires with the drafts, 30 days):
  materials/<rec>/suggest-<key>.json        the suggestions for the session's Teacher Notes
  materials/<rec>/<material>.json           one made material: choices, brief, 3 cleaned SVG drafts
  materials/<rec>/<material>.send-<t>.json  one email attempt (status, time, recipient hash)
  materials_settings/email-<t>-<x>.json     admin: email delivery on/off (latest wins; kept)
  materials/<rec>/<material>.pic-<k>.json   photo-style flashcards: card k's picture (made in paced steps)
  materials/<rec>/<material>.done.json      photo-style flashcards: all pictures in (first writer emails)
  materials_settings/instruction-<key>-<t>-<x>.json  admin: the instruction for one activity type
                                            (latest wins; kept; {"instruction": null} = back to the default)

Renderers: one Make flow routes by activity type
(material_ai.renderer): "svg" (the text model writes 3 SVG drafts: coloring, labels, worksheets,
diagrams, line-art and color flashcards), "template" (multiplication tables, number lines, letter
tracing: the AI fills parameters, material_templates builds 3 layouts) and "photo" (photo-style
flashcards: the image model makes one picture per card, at most one call every PHOTO_GAP seconds,
driven by the page's continue calls, because the quota is about 2 pictures a minute).
PDFs and PNGs are rendered on demand from the cleaned SVG and never stored; the raw model reply is
never stored or served.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import math
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from types import SimpleNamespace

from common.roster import class_students, roster_students
from common.safelog import log

from . import material_ai as mai
from . import material_svg as msvg
from . import material_templates as mt
from . import mailer
from .bug_reports import _EMAIL, _LONG_NUMBER, _word, never_values
from .incident_names import _mask, transcript_window
from .notes import ApiError, actor_id, check_actor, is_read_only, may_act, signed_in
from .store import Conflict

PREFIX = "materials/"
PLAN_PREFIX = "planner/materials/"   # lesson plan materials: kept with the plan (not on the 30-day rule)
LESSON_DAYS = ("mon", "tue", "wed", "thu", "fri")
MAX_LESSON = 60
SETTINGS_PREFIX = "materials_settings/"
TEACHER_NOTE_TYPES = {"teacher notes", "teacher note"}
NOTE_WINDOW = 60                 # seconds after the marker (or up to the next marker)
NOTE_MIN_WINDOW = 15
MAX_STUDENTS = 3
MAX_TOPIC = 60
MAX_SUMMARIES = 3
MAX_ACCOMMODATIONS = 8
_MID = re.compile(r"^mat_\d{8}T\d{6}(?:\d{6})?_[0-9a-f]{6}$")
_ET = dt.timezone(dt.timedelta(hours=-4))   # display only; dates are stored as ISO dates
SYSTEM = SimpleNamespace(uid="system:materials", role="system", is_admin=False, email=None)


def default_instruction(key: str) -> str:
    if key == "flashcards_color":
        return msvg.COLOR_INSTRUCTION
    if key in mt.DEFAULT_PARAMS_INSTRUCTION:
        return mt.DEFAULT_PARAMS_INSTRUCTION[key]
    return msvg.DEFAULT_INSTRUCTION


def picture_prompt(style: str, subject: str) -> str:
    subject = " ".join(str(subject or "").split())
    return style.replace("{subject}", subject) if "{subject}" in style else f"{style}\nPicture: {subject}"


def email_default() -> bool:
    return (os.environ.get("MATERIALS_EMAIL_DEFAULT") or "off").lower() in ("on", "1", "true", "yes")


def review_url() -> str:
    return (os.environ.get("REVIEW_URL") or "https://review.example.org").rstrip("/")


PHOTO_GAP = float(os.environ.get("MATERIALS_PHOTO_GAP", "35"))   # seconds between image-model calls per set
PHOTO_TRIES = 3                  # a card whose picture fails this often is left as an empty frame
MAX_DOMAIN = 1500
_PHOTO_LAST: dict[str, float] = {}          # material id -> last image call (this instance)
_PHOTO_FAILS: dict[tuple, int] = {}
_PHOTO_LOCK = threading.Lock()
_PHOTO_BUSY: set[str] = set()
SVG_TRIES = 2                    # per draft: a reply that fails the SVG checks is asked for again
DRAFT_BUDGET = float(os.environ.get("MATERIALS_DRAFT_BUDGET", "95"))   # seconds; Cloud Run allows 120
PREVIEW_WIDTH = 480


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def teacher_notes(markers) -> list[dict]:
    """The session's Teacher Notes markers with their window end: [{index, start, end}]."""
    ms = sorted((m for m in markers or [] if isinstance(m, dict)
                 and isinstance(m.get("offset_seconds"), (int, float))), key=lambda m: m["offset_seconds"])
    out = []
    for i, m in enumerate(ms):
        if str(m.get("type") or "").strip().lower() not in TEACHER_NOTE_TYPES:
            continue
        start = float(m["offset_seconds"])
        nxt = next((float(x["offset_seconds"]) for x in ms[i + 1:] if float(x["offset_seconds"]) > start), None)
        end = start + NOTE_WINDOW if nxt is None else min(start + NOTE_WINDOW, max(nxt, start + NOTE_MIN_WINDOW))
        out.append({"index": len(out), "start": start, "end": end})
    return out


def _scrub_text(text: str, never: dict) -> str:
    out = _mask(text, never)
    out = _EMAIL.sub("", out)
    return " ".join(_LONG_NUMBER.sub("", out).split())


def _scrub_out(text: str, never: dict) -> str:
    """What the model wrote: capitalized roster names (as names, case-sensitive, so "a happy train"
    survives while "Happy" does not), emails, school ids and long numbers come out."""
    out = _EMAIL.sub("", str(text or ""))
    for v in never.get("other", []):
        out = re.sub(re.escape(v), "", out, flags=re.I)
    for v in never.get("student", []):
        out = _word(v, cased=True, possessive=True).sub("the student", out)
    for v in never.get("staff", []):
        out = _word(v, cased=True, possessive=True).sub("the teacher", out)
    out = re.sub(r"\b(the student|the teacher)(\s+the student|\s+the teacher)+", r"\1", out)
    return " ".join(_LONG_NUMBER.sub("", out).split())


def _scrub(obj, never: dict):
    if isinstance(obj, str):
        return _scrub_out(obj, never)
    if isinstance(obj, list):
        return [_scrub(x, never) for x in obj]
    if isinstance(obj, dict):
        return {k: _scrub(v, never) for k, v in obj.items()}
    return obj


def has_forbidden(blob: str, never: dict) -> str | None:
    """Why `blob` may not leave (an email, a school id, a roster name), or None."""
    if _EMAIL.search(blob):
        return "email"
    low = blob.lower()
    for v in never.get("other", []):
        if v.lower() in low:
            return "school_id"
    for v in (*never.get("student", []), *never.get("staff", [])):
        if _word(v).search(blob):
            return "name"
    return None


def has_forbidden_name_cased(blob: str, never: dict) -> str | None:
    """Like has_forbidden, but names only as written (capitalized), so the admin's instruction and
    the code-built prompts may say "white space" or "a happy face" while "White" / "Happy" still
    block. Used where the text is not transcript-derived (the transcript side stays case-blind)."""
    if _EMAIL.search(blob):
        return "email"
    low = blob.lower()
    for v in never.get("other", []):
        if v.lower() in low:
            return "school_id"
    for v in (*never.get("student", []), *never.get("staff", [])):
        if _word(v, cased=True).search(blob):
            return "name"
    return None


class Materials:
    def __init__(self, notes, *, ai=None, sender=None, now=None, domain_context=None, clock=None, approach_view=None):
        """domain_context(session) -> str: the class's domain / methodology text for the prompt context,
        from the Domains/Classes module (built separately); none = "". clock: seconds (tests)."""
        self.notes = notes
        self.domain_context = domain_context
        # approach_view(rid, session) -> {"class_name", "domain", "methodology"} | None: shown above the make
        # form so the teacher sees what the AI will design for (school_setup.SchoolSetup)
        self.approach_view = approach_view
        # plan_classes(class_code) -> [{"id", "name", "domain_name"}]: the school's classes in a classroom,
        # for the planner's Make form (school_setup.SchoolSetup.classroom_classes)
        self.plan_classes_hook = None
        self._ctx_session: dict = {}
        self._clock = clock or time.monotonic
        self.ai = ai or mai.MaterialAI()
        self.sender = sender if sender is not None else mailer.sender_from_env()
        self._now = now or _now

    @property
    def rec(self):
        return self.notes.recorder

    # ------------------------------------------------------------ helpers
    def _session(self, rid: str) -> tuple[dict, dict]:
        d = self.notes._draft(rid)
        return d, self.notes._context(rid, d)

    # ------------------------------------------------------------ where a material lives
    def _session_place(self, rid: str):
        d, s = self._session(rid)
        return SimpleNamespace(kind="session", rid=rid, s=s, base=f"{PREFIX}{rid}/", log_id=rid, ckey=None, week=None)

    def plan_place(self, class_code, week):
        """A lesson plan entry: one classroom's week in the planner (planner/plans/<class_key>/<week>/)."""
        from common.classcodes import class_key, find_class

        from .planner import parse_week
        display, entry = find_class(self.notes.roster(), class_code) if isinstance(class_code, str) else (None, None)
        if not display:
            raise ApiError(404, "UNKNOWN_CLASSROOM")
        wk = parse_week(week).isoformat()
        ckey = class_key(display)
        s = {"class_code": display, "class_key": ckey, "teacher_id": (entry or {}).get("teacher_id")}
        return SimpleNamespace(kind="plan", rid=None, s=s, base=f"{PLAN_PREFIX}{ckey}/{wk}/", log_id=ckey, ckey=ckey,
                               week=wk)

    def _may(self, actor, place) -> bool:
        if actor is None or is_read_only(actor):
            return False
        if place.kind == "session":
            return may_act(actor, place.s)
        if signed_in(actor):          # the planner's rule: the classroom's teacher, or an admin
            return bool(actor.is_admin or place.s.get("teacher_id") in actor.teacher_ids)
        return actor == place.s.get("teacher_id")

    def _record(self, actor, action, place, **kw):
        if place.kind == "session":
            return self.rec.record(actor, action, session={"recording_id": place.rid, **place.s}, **kw)
        return self.rec.record(actor, action, record_id=place.ckey, record_type="plan", week=place.week, **kw)

    @staticmethod
    def _place_ref(place) -> dict:
        return {"kind": "plan", "class_key": place.ckey, "week": place.week, "class_code": place.s["class_code"]}

    @staticmethod
    def _base(doc: dict) -> str:
        p = doc.get("place") or {}
        if p.get("kind") == "plan":
            return f"{PLAN_PREFIX}{p['class_key']}/{p['week']}/"
        return f"{PREFIX}{doc['recording_id']}/"

    @staticmethod
    def _lesson(v) -> dict | None:
        """Which day / lesson of the week a planner material is for: {"day": mon..fri | None, "label"}."""
        if v in (None, {}):
            return None
        if not isinstance(v, dict):
            raise ApiError(400, "BAD_LESSON")
        day = v.get("day") or None
        if day is not None and day not in LESSON_DAYS:
            raise ApiError(400, "BAD_LESSON")
        label = " ".join(str(v.get("label") or "").split())
        if len(label) > MAX_LESSON:
            raise ApiError(400, "BAD_LESSON")
        return {"day": day, "label": label} if (day or label) else None

    def _plan_classes(self, place) -> list[dict]:
        if self.plan_classes_hook is None:
            return []
        try:
            return list(self.plan_classes_hook(place.s["class_code"]) or [])
        except Exception as exc:  # noqa: BLE001 - optional
            log("materials_domain_unavailable", "WARNING", step="materials", error_type=type(exc).__name__)
            return []

    def _mark_removed(self, place, mid: str, actor, *, replaced_by: str | None = None) -> None:
        try:
            self.notes.store.write_json(f"{place.base}{mid}.removed.json",
                                        {"at": self._now().isoformat(timespec="seconds"), "by": actor_id(actor),
                                         "replaced_by": replaced_by}, create_only=True)
        except Conflict:
            return
        self._record(actor, "materials_removed", place, material_id=mid, regenerate_of=None)

    # ------------------------------------------------------------ lesson plan (planner) materials
    def _form(self, s: dict, can: bool) -> dict:
        return {"students": [{"student_id": x["student_id"], "first_name": x["first_name"],
                              "last_initial": x["last_initial"]} for x in self._choices(s)] if can else [],
                "drafts": mai.DRAFTS, "types": self._types()}

    @staticmethod
    def _types() -> list[dict]:
        return [{"type": k, "label": mai.TYPE_LABEL[k], "min": mai.COUNT[k][0], "default": mai.COUNT[k][1],
                 "max": mai.COUNT[k][2], "unit": mai.UNIT[k], "renderer": mai.renderer(k),
                 **({"styles": [{"style": st, "label": mai.STYLE_LABEL[st], "renderer": mai.renderer(k, st)}
                                for st in mai.STYLES], "photo_max": mai.PHOTO_MAX} if k == "flashcards" else {})}
                for k in mai.TYPES]

    def plan_view(self, actor, class_code, weeks) -> dict:
        """GET /api/planner/materials?class_code=..&weeks=w1,w2,w3: the materials attached to each week's
        lesson plan, plus what the planner's Make form needs."""
        actor = check_actor(actor)
        ws = [w for w in str(weeks or "").split(",") if w][:4]
        if not ws:
            raise ApiError(400, "BAD_WEEK")
        places = [self.plan_place(class_code, w) for w in ws]
        p0 = places[0]
        can = self._may(actor, p0)
        addr = self.recipient(actor, p0.s)[0] if can else None
        return {"class_code": p0.s["class_code"], "can_make": bool(can), **self._form(p0.s, can),
                "classes": [{"id": c["id"], "name": c["name"], "domain_name": c.get("domain_name")}
                            for c in self._plan_classes(p0)],
                "weeks": {p.week: [self._public(m, None) for m in self._materials(p.base)] for p in places},
                "email": {"enabled": self.email_enabled(), "to": self._mask_addr(addr) if can else None,
                          "has_address": bool(addr)}}

    def plan_make(self, actor, body: dict) -> dict:
        return self.make_at(self.plan_place(body.get("class_code"), body.get("week")), actor, body)

    def plan_action(self, actor, mid: str, what: str, body: dict, draft=None):
        place = self.plan_place(body.get("class_code"), body.get("week"))
        if what == "pdf":
            return self.pdf_at(place, mid, actor, draft)
        if what == "resend":
            return self.resend_at(place, mid, actor)
        if what == "continue":
            return self.continue_at(place, mid, actor)
        if what == "remove":
            actor = check_actor(actor)
            if not self._may(actor, place):
                raise ApiError(403, "NOT_YOUR_CLASSROOM")
            self._material(place.base, mid)
            self._mark_removed(place, mid, actor)
            return {"removed": mid}
        raise ApiError(405, "METHOD_NOT_ALLOWED")

    def attach(self, rid: str, mid: str, actor, body: dict) -> dict:
        """POST /api/sessions/<rid>/materials/<mid>/attach {"week"?, "lesson"?}: copy a material made from
        the session's Teacher Notes into its classroom's lesson plan (default: the session's week), so it
        stays with the plan after the session's materials expire."""
        from .planner import week_of
        actor = check_actor(actor)
        sp = self._session_place(rid)
        if not self._may(actor, sp):
            raise ApiError(403, "NOT_SESSION_TEACHER")
        if not sp.s.get("class_code"):
            raise ApiError(409, "NO_CLASSROOM")
        week = body.get("week")
        if week in (None, ""):
            day = self._session_day({"recording_id": rid, **sp.s})
            if day is None:
                raise ApiError(400, "BAD_WEEK")
            week = week_of(day).isoformat()
        pp = self.plan_place(sp.s["class_code"], week)
        if not self._may(actor, pp):
            raise ApiError(403, "NOT_YOUR_CLASSROOM")
        doc = self._material(sp.base, mid)
        if doc.get("renderer") == "photo" and not self._photo_state(doc)["done"]:
            raise ApiError(409, "STILL_MAKING")
        copy = {k: v for k, v in doc.items() if not k.startswith("_")}
        copy.update(recording_id=None, place=self._place_ref(pp), lesson=self._lesson(body.get("lesson")),
                    attached_from={"recording_id": rid, "note_index": doc.get("note_index")},
                    attached_by=actor_id(actor), attached_at=self._now().isoformat(timespec="seconds"))
        try:
            self.notes.store.write_json(f"{pp.base}{mid}.json", copy, create_only=True)
        except Conflict as exc:
            raise ApiError(409, "ALREADY_IN_PLAN") from exc
        for n in self._list(f"{sp.base}{mid}."):
            tail = n.rsplit("/", 1)[-1][len(mid):]
            if tail.startswith(".pic-") or tail == ".done.json":
                try:
                    self.notes.store.write_json(f"{pp.base}{mid}{tail}", self.notes.store.read_json(n) or {},
                                                create_only=True)
                except Conflict:
                    pass
        self._record(actor, "materials_attached", pp, material_id=mid, material_type=doc["type"],
                     source_recording_id=rid)
        return {"material": self._public(copy, None), "week": pp.week, "class_code": pp.s["class_code"]}

    def _in_plan(self, s: dict, mid: str) -> str | None:
        """The week of the session's lesson plan this session material was added to (or None)."""
        from common.classcodes import class_key
        wk = self._session_week(s)
        if not wk:
            return None
        base = f"{PLAN_PREFIX}{class_key(s['class_code'])}/{wk}/"
        names = set(self._list(f"{base}{mid}."))
        return wk if f"{base}{mid}.json" in names and f"{base}{mid}.removed.json" not in names else None

    def _transcript(self, rid: str) -> dict:
        return self.notes.store.read_json(f"transcripts/{rid}.json") or {}

    def _never(self, s: dict, transcript: dict) -> dict:
        return never_values(self.notes.roster(), s, transcript)

    def _list(self, prefix: str) -> list[str]:
        try:
            return self.notes.store.list(prefix)
        except Exception as exc:  # noqa: BLE001 - no bucket access to materials/ yet
            if type(exc).__name__ in ("Forbidden", "PermissionError", "PermissionDenied"):
                return []
            raise

    def _choices(self, s: dict) -> list[dict]:
        """Students the form offers: the session's classroom (and the session's own student)."""
        r = self.notes.roster()
        out = {st["student_id"]: st for st in class_students(r, s.get("class_code"))} if s.get("class_code") else {}
        if s.get("student_id") and s["student_id"] not in out:
            st = roster_students(r).get(s["student_id"])
            if st:
                out[s["student_id"]] = {"student_id": s["student_id"], "first_name": st.get("first_name") or "",
                                        "last_initial": st.get("last_initial") or ""}
        return sorted(out.values(), key=lambda x: (x["first_name"].casefold(), x["student_id"]))

    # ------------------------------------------------------------ email setting (admin)
    def email_enabled(self) -> bool:
        names = sorted(n for n in self._list(SETTINGS_PREFIX) if n.startswith(f"{SETTINGS_PREFIX}email-"))
        if not names:
            return email_default()
        return bool((self.notes.store.read_json(names[-1]) or {}).get("enabled"))

    def email_setting(self, actor) -> dict:
        self._admin(actor)
        return {"enabled": self.email_enabled(), "default": email_default(),
                "sender": mailer.MAIL_FROM, "mail_mode": mailer.mode(),
                "mail_state": getattr(self.sender, "state", "off") if self.sender is not None else "off"}

    def set_email(self, actor, body: dict) -> dict:
        self._admin(actor)
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise ApiError(400, "BAD_SETTING")
        now = self._now()
        name = f"{SETTINGS_PREFIX}email-{now.strftime('%Y%m%dT%H%M%S%f')}-{secrets.token_hex(3)}.json"
        self.notes.store.write_json(name, {"enabled": enabled, "by": actor_id(actor),
                                           "at": now.isoformat(timespec="seconds")}, create_only=True)
        self.rec.record(actor, "settings_changed", record_id="materials_email", record_type="settings",
                        required=True, setting="materials_email", enabled=enabled)
        return self.email_setting(actor)

    # ------------------------------------------------------------ per-type instructions (admin edits)
    def instruction(self, key: str = "coloring") -> tuple[str, dict | None]:
        pre = f"{SETTINGS_PREFIX}instruction-{key}-"
        names = sorted(n for n in self._list(pre) if n.startswith(pre))
        doc = (self.notes.store.read_json(names[-1]) or {}) if names else None
        text = (doc or {}).get("instruction")
        return (text if isinstance(text, str) and text.strip() else default_instruction(key)), doc

    def instruction_setting(self, actor) -> dict:
        """Anyone who may make materials reads them (teachers read-only); admins edit."""
        actor = check_actor(actor)
        if is_read_only(actor):
            raise ApiError(403, "ADMIN_ONLY")
        items = []
        for key in mai.INSTRUCTION_KEYS:
            text, doc = self.instruction(key)
            d = default_instruction(key)
            kind, _, style = key.partition("_") if key.startswith("flashcards_") else (key, "", "line")
            items.append({"key": key, "label": mai.INSTRUCTION_LABEL[key], "renderer": mai.renderer(kind, style or "line"),
                          "instruction": text, "default": d, "is_default": text == d,
                          "updated_at": (doc or {}).get("at")})
        return {"instructions": items, "can_edit": bool(signed_in(actor) and actor.is_admin),
                "max": msvg.MAX_INSTRUCTION, "model": mai.svg_model(), "image_model": mai.image_model()}

    def set_instruction(self, actor, body: dict) -> dict:
        self._admin(actor)
        key = body.get("key")
        if key not in mai.INSTRUCTION_KEYS:
            raise ApiError(400, "BAD_INSTRUCTION_KEY")
        default = default_instruction(key)
        reset = body.get("reset") is True
        text = body.get("instruction")
        if not reset:
            if not isinstance(text, str) or not text.strip():
                raise ApiError(400, "BAD_INSTRUCTION")
            text = text.replace("\r\n", "\n").strip("\n")
            if len(text) > msvg.MAX_INSTRUCTION:
                raise ApiError(400, "INSTRUCTION_TOO_LONG")
            if has_forbidden_name_cased(text, never_values(self.notes.roster(), {}, {})):
                raise ApiError(400, "INSTRUCTION_HAS_NAMES")
            if text == default:
                reset = True
        now = self._now()
        name = f"{SETTINGS_PREFIX}instruction-{key}-{now.strftime('%Y%m%dT%H%M%S%f')}-{secrets.token_hex(3)}.json"
        self.notes.store.write_json(name, {"key": key, "instruction": None if reset else text, "by": actor_id(actor),
                                           "at": now.isoformat(timespec="seconds")}, create_only=True)
        final = default if reset else text
        self.rec.record(actor, "settings_changed", record_id="materials_instruction", record_type="settings",
                        required=True, setting="materials_instruction", instruction_key=key, reset=reset,
                        chars=len(final), text_sha256=hashlib.sha256(final.encode()).hexdigest())
        return self.instruction_setting(actor)

    @staticmethod
    def _admin(actor):
        if not (signed_in(actor) and actor.is_admin):
            raise ApiError(403, "ADMIN_ONLY")

    # ------------------------------------------------------------ recipient
    def recipient(self, actor, s: dict) -> tuple[str | None, str | None]:
        """(address, teacher id) on file for this click, or (None, None). Teacher: their own address
        (a teacher id of theirs that owns the session first). Admin: the session's teacher's address.
        Laptop demo: the reviewer id is a teacher id."""
        teachers = self.notes.roster().get("teachers") or {}
        if signed_in(actor):
            if actor.is_admin:
                ids = [s.get("teacher_id")]
            else:
                owners = set(s.get("owner_teacher_ids") or []) | ({s["teacher_id"]} if s.get("teacher_id") else set())
                mine = sorted(actor.teacher_ids)
                ids = [t for t in mine if t in owners] + [t for t in mine if t not in owners]
        else:
            ids = [actor]
        for tid in ids:
            emails = [e for e in ((teachers.get(tid) or {}).get("emails") or []) if isinstance(e, str) and e.strip()]
            if emails:
                return emails[0].strip(), tid
        return None, None

    @staticmethod
    def _mask_addr(a: str | None) -> str | None:
        if not a or "@" not in a:
            return None
        local, dom = a.split("@", 1)
        return f"{local[:1]}{'*' * max(3, len(local) - 1)}@{dom}"

    # ------------------------------------------------------------ suggestions
    def view(self, rid: str, actor) -> dict:
        """GET /api/sessions/<rid>/materials: the Teacher Notes with their suggestions, the
        materials made for this session, and what the email would do."""
        d, s = self._session(rid)
        can = (not is_read_only(actor)) and may_act(actor, s) if actor is not None else True
        notes_ = teacher_notes(s.get("markers"))
        sugg = self._suggestions(rid, s, notes_, actor, generate=can) if notes_ else {}
        addr = None
        if can:
            addr, _ = self.recipient(actor, s)
        return {
            "recording_id": rid, "can_make": bool(can),
            "notes": [{"index": n["index"], "offset_seconds": n["start"],
                       **(sugg.get(n["index"]) or {"is_request": False, "suggestions": [], "pending": True})}
                      for n in notes_],
            "students": [{"student_id": x["student_id"], "first_name": x["first_name"],
                          "last_initial": x["last_initial"]} for x in self._choices(s)] if can else [],
            "default_student_ids": [s["student_id"]] if s.get("student_id") else [],
            "drafts": mai.DRAFTS,
            "types": [{"type": k, "label": mai.TYPE_LABEL[k], "min": mai.COUNT[k][0], "default": mai.COUNT[k][1],
                       "max": mai.COUNT[k][2], "unit": mai.UNIT[k], "renderer": mai.renderer(k),
                       **({"styles": [{"style": st, "label": mai.STYLE_LABEL[st], "renderer": mai.renderer(k, st)}
                                      for st in mai.STYLES], "photo_max": mai.PHOTO_MAX} if k == "flashcards" else {})}
                      for k in mai.TYPES],
            "materials": [{**self._public(m, rid), "in_plan_week": self._in_plan({"recording_id": rid, **s}, m["material_id"])}
                          for m in self._materials(f"{PREFIX}{rid}/")],
            "plan_week": self._session_week({"recording_id": rid, **s}),
            "teaching_approach": self._approach_view(rid, s) if can else None,
            "email": {"enabled": self.email_enabled(), "to": self._mask_addr(addr) if can else None,
                      "has_address": bool(addr)},
        }

    @staticmethod
    def _session_day(s: dict) -> dt.date | None:
        """The session's school date: planner.session_date (timestamp_start), else start_time, else the
        date in the recording id (rec_<device>_<YYYYMMDD>_...)."""
        from .planner import session_date
        day = session_date(s)
        if day is None and isinstance(s.get("start_time"), str):
            day = session_date({"timestamp_start": s["start_time"]})
        if day is None:
            m = re.match(r"^rec_[^_]+_(\d{8})_", str(s.get("recording_id") or ""))
            if m:
                try:
                    day = dt.datetime.strptime(m.group(1), "%Y%m%d").date()
                except ValueError:
                    day = None
        return day

    def _session_week(self, s: dict) -> str | None:
        from .planner import week_of
        day = self._session_day(s)
        return week_of(day).isoformat() if day and s.get("class_code") else None

    def _suggestions(self, rid, s, notes_, actor, *, generate: bool) -> dict:
        key = hashlib.sha256(json.dumps([mai.SUGGEST_VERSION, [(n["start"], n["end"]) for n in notes_]]).encode()).hexdigest()[:20]
        name = f"{PREFIX}{rid}/suggest-{key}.json"
        doc = self.notes.store.read_json(name) if name in self._list(f"{PREFIX}{rid}/suggest-") else None
        if doc is None and generate:
            transcript = self._transcript(rid)
            never = self._never(s, transcript)
            payload = {"notes": [{"index": n["index"], "transcript": transcript_window(
                transcript, n["start"], never, max(1, int(round(n["end"] - n["start"]))))} for n in notes_]}
            leak = has_forbidden(json.dumps(payload, ensure_ascii=False), never)
            if leak:                                         # last line of defence before the AI
                log("materials_blocked", "ERROR", step="materials", recording_id=rid, reason=leak)
                return {}
            try:
                got = mai.clean_suggestions(self.ai.suggest(payload), [n["index"] for n in notes_])
            except Exception as exc:  # noqa: BLE001 - no AI right now: no suggestions, try next open
                log("materials_suggest_unavailable", "WARNING", step="materials", recording_id=rid,
                    error_type=type(exc).__name__)
                return {}
            for v in got.values():
                for sug in v["suggestions"]:
                    sug["topic"] = _scrub_text(sug["topic"], never)[:MAX_TOPIC]
            doc = {"notes": {str(k): v for k, v in got.items()}, "prompt_version": mai.SUGGEST_VERSION,
                   "mode": mai.mode(), "at": self._now().isoformat(timespec="seconds")}
            try:
                self.notes.store.write_json(name, doc, create_only=True)
            except Conflict:
                doc = self.notes.store.read_json(name) or doc
            except Exception as exc:  # noqa: BLE001 - no bucket access to materials/ yet: show, don't cache
                log("materials_cache_failed", "WARNING", step="materials", recording_id=rid,
                    error_type=type(exc).__name__)
            requests = sum(1 for v in got.values() if v["is_request"])
            self.rec.record(actor if actor is not None else SYSTEM, "materials_suggested", session={"recording_id": rid, **s},
                            requests=requests, count=len(notes_))
        if not doc:
            return {}
        return {int(k): v for k, v in (doc.get("notes") or {}).items()}

    # ------------------------------------------------------------ make
    def make(self, rid: str, actor, body: dict) -> dict:
        """POST /api/sessions/<rid>/materials: make one material from the teacher's choice."""
        actor = check_actor(actor)
        return self.make_at(self._session_place(rid), actor, body)

    def make_at(self, place, actor, body: dict) -> dict:
        """One Make flow for a session (Teacher Notes) or a lesson plan week (planner)."""
        actor = check_actor(actor)
        rid, s = place.log_id, place.s
        if not self._may(actor, place):
            raise ApiError(403, "NOT_SESSION_TEACHER" if place.kind == "session" else "NOT_YOUR_CLASSROOM")
        kind = body.get("type")
        if kind not in mai.TYPES:
            raise ApiError(400, "BAD_MATERIAL_TYPE")
        count = body.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or not mai.COUNT[kind][0] <= count <= mai.COUNT[kind][2]:
            raise ApiError(400, "BAD_COUNT")
        topic = " ".join(str(body.get("topic") or "").split())
        if len(topic) > MAX_TOPIC:
            raise ApiError(400, "TOPIC_TOO_LONG")
        style = body.get("style") or "line"
        if kind != "flashcards":
            style = "line"
        elif style not in mai.STYLES:
            raise ApiError(400, "BAD_STYLE")
        renderer, key = mai.renderer(kind, style), mai.instruction_key(kind, style)
        if renderer == "photo" and count > mai.PHOTO_MAX:
            raise ApiError(400, "BAD_COUNT")
        due = self._due(body.get("due"))
        notes_ = teacher_notes(s.get("markers")) if place.kind == "session" else []
        ni = body.get("note_index")
        if ni is not None and (not isinstance(ni, int) or isinstance(ni, bool) or not 0 <= ni < len(notes_)):
            raise ApiError(400, "BAD_NOTE_INDEX")
        allowed = {x["student_id"] for x in self._choices(s)}
        sids = body.get("student_ids") or []
        if not isinstance(sids, list) or len(sids) > MAX_STUDENTS or any(x not in allowed for x in sids):
            raise ApiError(400, "BAD_STUDENTS")
        sids = list(dict.fromkeys(sids))
        regen = body.get("regenerate_of")
        if regen is not None and (not isinstance(regen, str) or not _MID.match(regen)
                                  or self.notes.store.read_json(f"{place.base}{regen}.json") is None):
            raise ApiError(400, "BAD_REGENERATE")
        lesson = self._lesson(body.get("lesson")) if place.kind == "plan" else None
        class_id = body.get("class_id") if place.kind == "plan" else None
        if class_id is not None and (not isinstance(class_id, str) or class_id not in
                                     {c["id"] for c in self._plan_classes(place)}):
            raise ApiError(400, "BAD_CLASS")

        now = self._now()
        mid = f"mat_{now.strftime('%Y%m%dT%H%M%S%f')}_{secrets.token_hex(3)}"
        self._record(actor, "materials_requested", place, required=True,
                        material_id=mid, material_type=kind, count=count, note_index=ni, regenerate_of=regen,
                        renderer=renderer, style=style, instruction_key=key)
        transcript = self._transcript(place.rid) if place.kind == "session" else {}
        never = self._never(s, transcript)
        self._ctx_session = {"recording_id": place.rid, **s} if place.kind == "session" else \
            {**s, "class_id": class_id}
        payload = self._plan_payload(kind, count, topic, due, sids,
                                     notes_[ni] if ni is not None else None, transcript, never, style=style)
        leak = has_forbidden(json.dumps(payload, ensure_ascii=False), never)
        if leak:
            log("materials_blocked", "ERROR", step="materials", recording_id=rid, reason=leak)
            raise ApiError(500, "MATERIALS_BLOCKED")
        system, _ = self.instruction(key)
        try:
            if renderer == "svg":
                brief, drafts, model = self._make_svg(rid, kind, count, payload, system, never)
            elif renderer == "template":
                brief, drafts, model = self._make_template(rid, kind, count, payload, system, never)
            else:
                brief, drafts, model = self._make_photo_plan(rid, count, payload, never)
        except RuntimeError as exc:
            if "off" in str(exc):
                raise ApiError(404, "MATERIALS_AI_OFF") from exc
            log("materials_plan_failed", "WARNING", step="materials", recording_id=rid, error_type=type(exc).__name__)
            raise ApiError(502, "MATERIALS_AI_FAILED") from exc
        except ApiError:
            raise
        except Exception as exc:  # noqa: BLE001 - never echo model output
            log("materials_plan_failed", "WARNING", step="materials", recording_id=rid, error_type=type(exc).__name__)
            raise ApiError(502, "MATERIALS_AI_FAILED") from exc
        doc = {"material_id": mid, "recording_id": place.rid, "type": kind, "style": style, "renderer": renderer,
               "instruction_key": key, "count": count, "topic": topic,
               "due": due, "student_ids": sids, "note_index": ni,
               "regenerate_of": regen, "teacher_id": s.get("teacher_id"), "made_by": actor_id(actor),
               "made_by_role": actor.role if signed_in(actor) else "laptop",
               "created_at": now.isoformat(timespec="seconds"), "brief": brief, "drafts": drafts,
               "instruction_sha256": hashlib.sha256(system.encode()).hexdigest(),
               "prompt_version": mai.PARAMS_VERSION if renderer != "svg" else mai.BRIEF_VERSION,
               "mode": mai.mode(), "model": model, "class_domain": payload.get("class_domain") or None,
               **({"place": self._place_ref(place), "lesson": lesson, "class_id": class_id}
                  if place.kind == "plan" else {})}
        self.notes.store.write_json(f"{place.base}{mid}.json", doc, create_only=True)
        if regen and place.kind == "plan":       # a lesson plan keeps one version: the new one replaces it
            self._mark_removed(place, regen, actor, replaced_by=mid)
        if renderer == "photo":          # pictures come in paced steps (continue_); made + emailed when all are in
            return {"material": self._public(doc, rid), "email": {"status": "pending"}}
        pdf = self.render(doc)
        self._record(actor, "materials_made", place, material_id=mid,
                        material_type=kind, count=count, pages=_pages(pdf), drafts=len(drafts), renderer=renderer,
                        style=style, pdf_sha256=hashlib.sha256(pdf).hexdigest(), pdf_bytes=len(pdf))
        email = self._deliver(place, doc, pdf, actor, resend=False) if self.email_enabled() else {"status": "email_off"}
        return {"material": self._public(doc, rid), "email": email}

    def _blocked(self, rid: str, blob: str, never: dict) -> None:
        leak = has_forbidden_name_cased(blob, never)
        if leak:
            log("materials_blocked", "ERROR", step="materials", recording_id=rid, reason=leak)
            raise ApiError(500, "MATERIALS_BLOCKED")

    def _make_svg(self, rid, kind, count, payload, system, never):
        brief = _scrub(mai.clean_brief(self.ai.brief(payload), kind, count, payload["topic"]), never)
        prompts = [msvg.user_prompt(brief["teacher_request"], kind, i + 1, v, mai.DRAFTS,
                                    approach=payload.get("class_domain"))       # Domains tab ()
                   for i, v in enumerate(brief["variations"])]
        self._blocked(rid, system + "\n" + "\n".join(prompts), never)
        drafts = self._drafts(rid, system, prompts, brief["variations"], never)
        if not drafts:
            raise ApiError(502, "MATERIALS_AI_FAILED")
        return brief, drafts, mai.svg_model()

    def _make_template(self, rid, kind, count, payload, system, never):
        """The AI fills parameters; the numbers and the layout are ours."""
        self._blocked(rid, system, never)
        params = mai.clean_params(kind, _scrub(self.ai.params(kind, system, payload), never), count, payload["topic"])
        self._blocked(rid, json.dumps(params), never)
        school = self._school()
        names = mt.LAYOUT_NAMES[kind]
        drafts = [{"draft": i + 1, "variation": names[i], "svg": msvg.set_header(mt.BUILD[kind](params, i + 1), school)}
                  for i in range(mt.LAYOUTS)]
        brief = {"title": params["title"], "teacher_request": "", "variations": names, "params": params, "goal_ids": []}
        return brief, drafts, "template"

    def _make_photo_plan(self, rid, count, payload, never):
        """The words and picture subjects now (one text call); the pictures later, paced (continue_)."""
        system = mt.PARAMS_TECH["flashcards_photo"]
        params = mai.clean_params("flashcards_photo", _scrub(self.ai.params("flashcards_photo", system, payload), never),
                                  count, payload["topic"])
        self._blocked(rid, json.dumps(params), never)
        brief = {"title": params["title"], "teacher_request": "", "variations": ["photo-style pictures"],
                 "cards": params["cards"], "goal_ids": []}
        return brief, [], mai.image_model()

    @staticmethod
    def _due(v) -> str | None:
        if v in (None, ""):
            return None
        if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise ApiError(400, "BAD_DUE_DATE")
        try:
            dt.date.fromisoformat(v)
        except ValueError as exc:
            raise ApiError(400, "BAD_DUE_DATE") from exc
        return v

    def _one_draft(self, system: str, prompt: str, never: dict) -> str:
        """Model -> cleaned SVG with our header; a reply that fails the checks is asked for again."""
        school = self._school()
        last = None
        for _ in range(SVG_TRIES):
            try:
                svg = msvg.sanitize(self.ai.svg(system, prompt))
                svg = msvg.map_text(svg, lambda t: _scrub_out(t, never) if t.strip() else t)
                svg = msvg.set_header(svg, school)
                msvg.render_png(svg, 64)                     # must render
                return svg
            except msvg.SvgInvalid as exc:
                last = exc
                log("materials_svg_invalid", "WARNING", step="materials", reason=str(exc)[:40])
        raise last or msvg.SvgInvalid("no reply")

    def _drafts(self, rid: str, system: str, prompts: list[str], variations: list[str], never: dict) -> list[dict]:
        """The drafts in parallel within DRAFT_BUDGET seconds; one that fails is left out."""
        pool = ThreadPoolExecutor(max_workers=len(prompts) or 1)
        futs = {pool.submit(self._one_draft, system, p, never): i for i, p in enumerate(prompts)}
        got = {}
        try:
            for f in as_completed(futs, timeout=DRAFT_BUDGET):
                try:
                    got[futs[f]] = f.result()
                except Exception as exc:  # noqa: BLE001 - never echo model output
                    log("materials_draft_failed", "WARNING", step="materials", recording_id=rid,
                        error_type=type(exc).__name__)
        except FuturesTimeout:
            log("materials_draft_failed", "WARNING", step="materials", recording_id=rid,
                error_type="Timeout", missing=len(futs) - len(got))
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return [{"draft": n + 1, "variation": variations[i], "svg": got[i]}
                for n, i in enumerate(sorted(got))]

    def _school(self) -> str:
        from .branding import Branding
        try:
            return Branding(self.notes.store, self.rec).view().get("school_name") or ""
        except Exception:  # noqa: BLE001
            return ""

    def _plan_payload(self, kind, count, topic, due, sids, note, transcript, never, style: str = "line") -> dict:
        r = self.notes.roster()
        studs = roster_students(r)
        label = (lambda i: "the student") if len(sids) == 1 else (lambda i: f"student {i + 1}")
        out_students = []
        for i, sid in enumerate(sids):
            st = studs.get(sid) or {}
            first = st.get("first_name") or ""
            lab = label(i)

            def m(t, first=first, lab=lab):
                t = str(t or "")
                if first:
                    t = _word(first).sub(lab, t)
                return _scrub_text(t, never)
            out_students.append({
                "label": lab, "grade": str(st.get("grade") or ""),
                "iep_goals": [{"goal_id": g.get("goal_id"), "category": g.get("category") or "",
                               "description": m(g.get("description"))} for g in st.get("iep_goals") or []
                              if isinstance(g, dict) and g.get("goal_id")],
                "accommodations": [m(a) for a in ((st.get("iep") or {}).get("accommodations") or [])[:MAX_ACCOMMODATIONS]],
                "recent_sessions": [m(x) for x in self._recent_summaries(sid)],
            })
        grades = sorted({x["grade"] for x in out_students if x["grade"]})
        request = []
        if note is not None:
            request = transcript_window(transcript, note["start"], never, max(1, int(round(note["end"] - note["start"]))))
        out = {"type": kind, "type_label": mai.TYPE_LABEL[kind], "count": count, "topic": _scrub_text(topic, never),
               "due": due or "", "teacher_request": request,
               "grades": grades, "students": out_students}
        if kind == "flashcards":         # the drafts must match the style the teacher chose (line / color / photo)
            out["style"] = style
            out["style_label"] = mai.STYLE_GUIDE[style]
        domain = self._domain(never)
        if domain:
            out["class_domain"] = domain
        return out

    def _approach_view(self, rid: str, s: dict) -> dict | None:
        if self.approach_view is None:
            return None
        try:
            return self.approach_view(rid, s)
        except Exception as exc:  # noqa: BLE001 - the note is optional
            log("materials_domain_unavailable", "WARNING", step="materials", error_type=type(exc).__name__)
            return None

    def _domain(self, never: dict) -> str:
        """Hook for the Domains/Classes module: the class's domain / methodology text, masked."""
        if self.domain_context is None:
            return ""
        try:
            text = self.domain_context(self._ctx_session) or ""
        except Exception as exc:  # noqa: BLE001 - no domain text is fine
            log("materials_domain_unavailable", "WARNING", step="materials", error_type=type(exc).__name__)
            return ""
        return _scrub_text(str(text), never)[:MAX_DOMAIN]

    def _recent_summaries(self, sid: str) -> list[str]:
        """Up to 3 teacher-approved session summaries for this student, newest first."""
        found = []
        for n in sorted(self._list("approvals/"), reverse=True)[:40]:
            if not n.endswith(".json"):
                continue
            rid = n.rsplit("/", 1)[-1][:-5]
            d = self.notes.store.read_json(f"drafts/{rid}.json") or {}
            if (d.get("session") or {}).get("student_id") != sid:
                continue
            ap = self.notes.store.read_json(n) or {}
            text = ((ap.get("analysis") or {}).get("general_session_summary") or "").strip()
            if text:
                found.append((ap.get("approved_at") or "", text[:500]))
        return [t for _, t in sorted(found, reverse=True)[:MAX_SUMMARIES]]

    # ------------------------------------------------------------ read / download / resend
    def _materials(self, base: str) -> list[dict]:
        out = []
        removed = {n.rsplit("/", 1)[-1].split(".")[0] for n in self._list(base) if n.endswith(".removed.json")}
        for n in self._list(base):
            name = n.rsplit("/", 1)[-1]
            if name.endswith(".json") and _MID.match(name[:-5]) and name[:-5] not in removed:
                doc = self.notes.store.read_json(n)
                if doc and (doc.get("drafts") or doc.get("renderer") == "photo"):   # old picture-model sets: not shown
                    doc["_sends"] = [self.notes.store.read_json(x) or {} for x in self._list(f"{base}{name[:-5]}.send-")]
                    out.append(doc)
        return sorted(out, key=lambda x: x.get("created_at") or "")

    def _material(self, base: str, mid: str) -> dict:
        if not isinstance(mid, str) or not _MID.match(mid):
            raise ApiError(404, "NOT_FOUND")
        doc = self.notes.store.read_json(f"{base}{mid}.json")
        if doc is None:
            raise ApiError(404, "NOT_FOUND")
        return doc

    def _public(self, doc: dict, rid: str) -> dict:
        """What the page gets: PNG previews rendered here from the cleaned SVG (never the SVG)."""
        studs = roster_students(self.notes.roster())
        first = [studs.get(x, {}).get("first_name") or "" for x in doc.get("student_ids") or []]
        sends = sorted(doc.get("_sends") or [], key=lambda x: x.get("at") or "")
        photo = self._photo_state(doc) if doc.get("renderer") == "photo" else None
        drafts = []
        for d in (self._photo_drafts(doc, photo["pictures"]) if photo else doc.get("drafts") or []):
            try:
                png = base64.b64encode(msvg.render_png(d["svg"], PREVIEW_WIDTH)).decode()
            except Exception:  # noqa: BLE001
                png = None
            drafts.append({"draft": d["draft"], "variation": d.get("variation") or "", "preview_png": png,
                           **({"page": d["page"], "pages": d["pages"]} if d.get("page") else {})})
        return {"material_id": doc["material_id"], "type": doc["type"], "type_label": mai.TYPE_LABEL[doc["type"]],
                "count": doc["count"], "topic": doc.get("topic") or "", "due": doc.get("due"),
                "title": (doc.get("brief") or {}).get("title") or mai.TYPE_LABEL[doc["type"]],
                "student_ids": doc.get("student_ids") or [], "students": [f for f in first if f],
                "note_index": doc.get("note_index"), "regenerate_of": doc.get("regenerate_of"),
                "created_at": doc.get("created_at"), "drafts": drafts,
                "renderer": doc.get("renderer") or "svg", "style": doc.get("style") or "line",
                "status": "making" if photo and not photo["done"] else "ready",
                **({"pictures_made": photo["made"], "pictures_total": photo["total"],
                    "eta_minutes": max(1, math.ceil((photo["total"] - photo["made"]) * PHOTO_GAP / 60))
                    if not photo["done"] else 0} if photo else {}),
                "preview_png": drafts[0]["preview_png"] if drafts else None,
                "lesson": doc.get("lesson"), "class_id": doc.get("class_id"),
                "attached_from": bool(doc.get("attached_from")),
                "emails": [{"at": x.get("at"), "status": x.get("status"), "resend": x.get("resend", False)} for x in sends]}

    def render(self, doc: dict, draft: int | None = None) -> bytes:
        from . import material_pdf
        all_ = doc.get("drafts") or []
        if doc.get("renderer") == "photo":
            st = self._photo_state(doc)
            if not st["done"]:
                raise ApiError(409, "STILL_MAKING")
            all_ = self._photo_drafts(doc, st["pictures"])
        drafts = [d for d in all_ if draft is None or d["draft"] == draft]
        if not drafts:
            raise ApiError(404, "NOT_FOUND")
        return material_pdf.render(drafts, of=len(all_))

    def pdf(self, rid: str, mid: str, actor, draft=None) -> tuple[bytes, str]:
        """One draft (?draft=N) or all drafts as a PDF."""
        actor = check_actor(actor)
        return self.pdf_at(self._session_place(rid), mid, actor, draft)

    def pdf_at(self, place, mid: str, actor, draft=None) -> tuple[bytes, str]:
        actor = check_actor(actor)
        if not self._may(actor, place):
            raise ApiError(403, "NOT_SESSION_TEACHER" if place.kind == "session" else "NOT_YOUR_CLASSROOM")
        if draft not in (None, ""):
            if not (isinstance(draft, (str, int)) and str(draft).isdigit() and 1 <= int(draft) <= mai.DRAFTS):
                raise ApiError(400, "BAD_DRAFT")
            draft = int(draft)
        else:
            draft = None
        doc = self._material(place.base, mid)
        data = self.render(doc, draft)
        self._record(actor, "materials_downloaded", place, required=True,
                        material_id=mid, material_type=doc["type"], pdf_sha256=hashlib.sha256(data).hexdigest(),
                        pdf_bytes=len(data), **({"draft_number": draft} if draft else {}))
        return data, f"{doc['type']}_{mid}{f'_draft{draft}' if draft else ''}.pdf"

    def resend(self, rid: str, mid: str, actor) -> dict:
        actor = check_actor(actor)
        return self.resend_at(self._session_place(rid), mid, actor)

    def resend_at(self, place, mid: str, actor) -> dict:
        actor = check_actor(actor)
        rid = place.log_id
        if not self._may(actor, place):
            raise ApiError(403, "NOT_SESSION_TEACHER" if place.kind == "session" else "NOT_YOUR_CLASSROOM")
        if not self.email_enabled():
            raise ApiError(409, "EMAIL_OFF")
        doc = self._material(place.base, mid)
        out = self._deliver(place, doc, self.render(doc), actor, resend=True)
        doc["_sends"] = [self.notes.store.read_json(x) or {} for x in self._list(f"{place.base}{mid}.send-")]
        return {"material": self._public(doc, rid), "email": out}

    # ------------------------------------------------------------ photo-style flashcards (paced)
    def _pictures(self, base: str, mid: str) -> dict[int, bytes | None]:
        out = {}
        for n in self._list(f"{base}{mid}.pic-"):
            m = re.search(r"\.pic-(\d{2})\.json$", n)
            if m:
                d = self.notes.store.read_json(n) or {}
                out[int(m.group(1))] = base64.b64decode(d["png"]) if d.get("png") else None
        return out

    def _photo_state(self, doc: dict) -> dict:
        base, mid = self._base(doc), doc["material_id"]
        pics = self._pictures(base, mid)
        total = len((doc.get("brief") or {}).get("cards") or [])
        done = bool(self._list(f"{base}{mid}.done"))
        return {"pictures": pics, "made": sum(1 for v in pics.values() if v), "settled": len(pics),
                "total": total, "done": done}

    def _photo_drafts(self, doc: dict, pictures: dict) -> list[dict]:
        """One draft, printed as pages of PHOTO_PER_PAGE big cards (2 x 2)."""
        b = doc.get("brief") or {}
        school = self._school()
        pages = mt.photo_pages(b.get("title") or "", b.get("cards") or [], {k: v for k, v in pictures.items() if v})
        return [{"draft": 1, "page": i + 1, "pages": len(pages), "variation": "photo-style pictures",
                 "svg": msvg.set_header(svg, school)} for i, svg in enumerate(pages)]

    def continue_(self, rid: str, mid: str, actor) -> dict:
        """POST /api/sessions/<rid>/materials/<mid>/continue: the page calls this while a photo-style set
        is being made. Each call makes at most one picture, and only when PHOTO_GAP seconds have passed
        since this set's last image call; when every card is settled the set is finished (journaled,
        emailed once). Cloud Run only runs us inside requests, so the work rides on these calls."""
        actor = check_actor(actor)
        return self.continue_at(self._session_place(rid), mid, actor)

    def continue_at(self, place, mid: str, actor) -> dict:
        actor = check_actor(actor)
        rid, s = place.log_id, place.s
        if not self._may(actor, place):
            raise ApiError(403, "NOT_SESSION_TEACHER" if place.kind == "session" else "NOT_YOUR_CLASSROOM")
        doc = self._material(place.base, mid)
        email = None
        if doc.get("renderer") == "photo":
            st = self._photo_state(doc)
            cards = (doc.get("brief") or {}).get("cards") or []
            if not st["done"] and st["settled"] < len(cards):
                self._photo_step(place, doc, cards, st["pictures"])
                st = self._photo_state(doc)
            if not st["done"] and st["settled"] >= len(cards):
                email = self._photo_finish(place, doc, actor, st)
        doc["_sends"] = [self.notes.store.read_json(x) or {} for x in self._list(f"{place.base}{mid}.send-")]
        pub = self._public(doc, rid)
        wait = 0
        if pub["status"] == "making":
            wait = max(0, round(PHOTO_GAP - (self._clock() - _PHOTO_LAST.get(mid, -1e9))))
        return {"material": pub, "email": email, "wait_seconds": wait}

    def _photo_step(self, place, doc: dict, cards: list, pics: dict) -> None:
        rid, s = place.log_id, place.s
        mid = doc["material_id"]
        with _PHOTO_LOCK:
            if mid in _PHOTO_BUSY or self._clock() - _PHOTO_LAST.get(mid, -1e9) < PHOTO_GAP:
                return
            _PHOTO_BUSY.add(mid)
            _PHOTO_LAST[mid] = self._clock()
        try:
            k = next(i for i in range(len(cards)) if i not in pics)
            style, _ = self.instruction("flashcards_photo")
            prompt = picture_prompt(style, cards[k]["picture"])
            png, why = None, None
            transcript = self._transcript(place.rid) if place.rid else {}
            if has_forbidden_name_cased(prompt, self._never(s, transcript)):
                why = "blocked"
            else:
                try:
                    png = mt.picture_png(self.ai.picture(prompt))
                except mai.PictureBusy:
                    log("materials_picture_busy", "WARNING", step="materials", recording_id=rid)
                    return
                except Exception as exc:  # noqa: BLE001
                    n = _PHOTO_FAILS[(mid, k)] = _PHOTO_FAILS.get((mid, k), 0) + 1
                    log("materials_picture_failed", "WARNING", step="materials", recording_id=rid,
                        error_type=type(exc).__name__, attempt=n)
                    if n < PHOTO_TRIES:
                        return
                    why = "failed"
            rec = {"card": k, "png": base64.b64encode(png).decode() if png else None, "skipped": why,
                   "at": self._now().isoformat(timespec="seconds")}
            try:
                self.notes.store.write_json(f"{place.base}{mid}.pic-{k:02d}.json", rec, create_only=True)
            except Conflict:
                pass
        finally:
            with _PHOTO_LOCK:
                _PHOTO_BUSY.discard(mid)

    def _photo_finish(self, place, doc: dict, actor, st: dict) -> dict | None:
        mid = doc["material_id"]
        try:
            self.notes.store.write_json(f"{place.base}{mid}.done.json",
                                        {"at": self._now().isoformat(timespec="seconds"), "by": actor_id(actor)},
                                        create_only=True)
        except Conflict:
            return None                                  # another call finished it
        pdf = self.render(doc)
        self._record(actor, "materials_made", place, material_id=mid,
                        material_type=doc["type"], count=doc["count"], pages=_pages(pdf), drafts=1,
                        renderer="photo", style="photo", pictures=st["made"],
                        pictures_missing=st["total"] - st["made"],
                        pdf_sha256=hashlib.sha256(pdf).hexdigest(), pdf_bytes=len(pdf))
        return self._deliver(place, doc, pdf, actor, resend=False) if self.email_enabled() else {"status": "email_off"}

    # ------------------------------------------------------------ email
    def email_text(self, doc: dict, s: dict) -> tuple[str, str]:
        studs = roster_students(self.notes.roster())
        first = [studs.get(x, {}).get("first_name") or "" for x in doc.get("student_ids") or []]
        first = [f for f in first if f]
        kind = doc["type"]
        unit = mai.UNIT[kind]
        n = doc["count"]
        k = 1 if doc.get("renderer") == "photo" else len(doc.get("drafts") or [])
        label = mai.TYPE_LABEL[kind] + (f" ({mai.STYLE_LABEL[doc['style']].split(' (')[0].lower()})" if kind == "flashcards" and doc.get("style") in ("color", "photo") else "")
        if kind == "multiplication_table":
            p = (doc.get("brief") or {}).get("params") or {}
            what = f"{label} {p.get('start', 1)}-{p.get('end', n)}, {k} layouts to pick from"
        elif k == 1:
            what = f"{label}, {n} {unit}{'s' if n != 1 else ''}"
        else:
            what = f"{label}, {n} {unit}{'s' if n != 1 else ''} per page, {k} draft{'s' if k != 1 else ''} to pick from"
        if doc.get("topic"):
            what += f", {doc['topic']}"
        who = f" for {', '.join(first)}" if first else ""
        due = ""
        if doc.get("due"):
            due = f" (for {dt.date.fromisoformat(doc['due']).strftime('%A, %B %-d')})"
        subject = f"Classroom materials ready: {mai.TYPE_LABEL[kind]}{who}"
        link = f"{review_url()}/#recordings/{doc['recording_id']}" if doc.get("recording_id") else f"{review_url()}/#planner"
        body = (f"Hi,\n\nHere are the classroom materials you asked for{who}{due}:\n{what}.\n\n"
                "They are suggestions made with AI from your Teacher Note. Please look them over before you use them.\n\n"
                + ("The printable PDF is attached. You can also open the " if k == 1 else
                   "The printable PDF is attached, one draft per page: print the one you like. You can also open the ") +
                f"{'session' if doc.get('recording_id') else 'lesson plan'}{' and download a single draft' if k > 1 else ''}:\n{link}\n\n"
                "This email was sent only to you.\n")
        return subject, body

    def check_email(self, subject: str, body: str, doc: dict) -> None:
        """No last names, no IEP text, no ids, no other addresses in what we send."""
        r = self.notes.roster()
        blob = f"{subject}\n{body}"
        if _EMAIL.search(blob):
            raise mailer.MailRefused("EMAIL_HAS_ADDRESS")
        for sid, st in roster_students(r).items():
            if sid in blob:
                raise mailer.MailRefused("EMAIL_HAS_ID")
            last = (st.get("last_name") or "").strip()
            if last and _word(last).search(blob):
                raise mailer.MailRefused("EMAIL_HAS_LAST_NAME")
            li = (st.get("last_initial") or "").strip()
            first = (st.get("first_name") or "").strip()
            if first and li and re.search(rf"{re.escape(first)}\s+{re.escape(li)}", blob):
                raise mailer.MailRefused("EMAIL_HAS_LAST_NAME")
            for g in st.get("iep_goals") or []:
                desc = " ".join(str((g or {}).get("description") or "").split())
                if len(desc) >= 20 and desc[:40] in blob:
                    raise mailer.MailRefused("EMAIL_HAS_IEP")
        if _LONG_NUMBER.search(blob):
            raise mailer.MailRefused("EMAIL_HAS_NUMBER")

    def _deliver(self, place, doc: dict, pdf: bytes, actor, *, resend: bool) -> dict:
        rid, s = place.log_id, place.s
        mid = doc["material_id"]
        to, tid = self.recipient(actor, s)
        status, msg_id = "emailed", None
        if self.sender is None:
            status = "email_off"
        elif not to:
            status = "no_staff_email"
        else:
            try:
                subject, body = self.email_text(doc, s)
                self.check_email(subject, body, doc)
                from_name = "Classroom materials"
                try:
                    from .branding import Branding
                    from_name = f"{Branding(self.notes.store, self.rec).view().get('school_name') or ''} - Classroom materials".strip(" -")
                except Exception:  # noqa: BLE001
                    pass
                msg = mailer.build_message(to=to, subject=subject, body=body, pdf=pdf,
                                           filename=f"{doc['type']}-{mid[-6:]}.pdf", from_name=from_name)
                msg_id = self.sender.send(msg)
            except mailer.MailNotReady as exc:
                status = exc.code.lower()
            except mailer.MailRefused as exc:
                status = exc.code.lower()
                log("materials_email_refused", "ERROR", step="materials", recording_id=rid, error_code=exc.code)
            except Exception as exc:  # noqa: BLE001 - never echo (could carry content)
                status = "send_failed"
                log("materials_email_failed", "ERROR", step="materials", recording_id=rid, error_type=type(exc).__name__)
        now = self._now()
        rec = {"material_id": mid, "status": status, "resend": resend, "at": now.isoformat(timespec="seconds"),
               "by": actor_id(actor), "teacher_id": tid,
               "to_sha256": hashlib.sha256(to.lower().encode()).hexdigest() if to else None,
               "gmail_id": msg_id if status == "emailed" else None, "from": mailer.MAIL_FROM}
        try:
            self.notes.store.write_json(f"{place.base}{mid}.send-{now.strftime('%Y%m%dT%H%M%S%f')}.json", rec, create_only=True)
        except Conflict:
            pass
        action = ("materials_resent" if resend else "materials_emailed") if status == "emailed" else "materials_send_failed"
        self._record(actor, action, place, material_id=mid,
                        material_type=doc["type"], email_status=status, pdf_sha256=hashlib.sha256(pdf).hexdigest())
        return {"status": status, "to": self._mask_addr(to)}


def _pages(pdf: bytes) -> int:
    return max(1, pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages"))
