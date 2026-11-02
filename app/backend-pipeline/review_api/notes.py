"""Session, approval and audit logic behind the review screen (tasks#35 PDF slice).

Rules (David's room, Sep 29, Ops agreeing):
  * A person approves. The PDF exists only for sessions a teacher approved;
    the approval is recorded server-side (approvals/<id>.json) with who and when.
  * Download only: no email, no send to any other system.
  * PDFs are generated on demand in memory and never stored. Approval and
    audit records sit in the same CMEK bucket and expire with the draft (30 days).
  * Every approval and every download is written to the audit trail
    (audit/<id>/...json): ids, times, PDF fingerprint - no names, no content.

Who may do what (David, Oct 2; DPA rev g). Enforced here, not by hiding buttons:
  * Everyone signed in and approved (admin, teacher, RBT) can read every
    classroom and session.
  * Only the session's teacher or the student's own teacher (roster), or an
    admin, can edit, approve, export (PDF) or change the classroom.
  * RBT = read-only. No edit, approve, export or classroom change, ever.
  * Parent emails (the weekly parent email draft and any parent address) are
    shown only to that student's teachers and admins. Everyone else gets the
    session without them.
  * A session belongs to its classroom and that classroom's teacher, not to the
    recorder that made it. A teacher can move a session to another classroom;
    each move is an audit record (who, when, from, to) and the latest one wins.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import secrets
import sys
import threading
import time
from pathlib import Path

_PIPE = Path(__file__).resolve().parents[1]
if str(_PIPE) not in sys.path:
    sys.path.insert(0, str(_PIPE))

from common.classcodes import class_key, find_class, resolve_session  # noqa: E402
from common.safelog import log  # noqa: E402
from common.roster import class_students, find_student, student_classrooms  # noqa: E402
from gemini_analyzer.schema import validate_output  # noqa: E402
from pipeline_service.records import ROSTER_FIELDS, markers_from_sidecar  # noqa: E402

from .incident_print import recording_start  # noqa: E402
from .journal import Recorder  # noqa: E402
from .store import Conflict  # noqa: E402

RID_RE = re.compile(r"^rec_[A-Za-z0-9_]{1,120}$")
ACTOR_RE = re.compile(r"^[a-z0-9_]{1,64}$")
ROSTER_OBJECT = "config/demo_roster.json"
ROSTER_TTL = float(os.environ.get("REVIEW_ALLOWLIST_TTL", "30"))
READ_ONLY_ROLES = frozenset({"rbt"})
HIDDEN_MARKER_TYPES = frozenset({"Bug"})   # bug reports (bug_reports.py) are never session markers
CLASSROOM_EVENT = "classroom_changed"
# Students in this session (tasks#106): latest audit record wins; a session can list many students
STUDENTS_EVENT = "session_students_set"
# any key that carries a parent email draft or a parent / guardian address
PARENT_KEY_RE = re.compile(r"(parent|guardian)_?e?_?mail", re.IGNORECASE)


def session_media(d: dict) -> list[dict]:
    """The draft's media list joined with the AI photo notes; no bucket paths go to the browser."""
    notes_by_id = {x.get("media_id"): x for x in (d.get("analysis") or {}).get("image_insights") or []}
    out = []
    for m in d.get("media") or []:
        n = notes_by_id.get(m.get("media_id")) or {}
        out.append({"media_id": m.get("media_id"), "kind": m.get("kind"), "content_type": m.get("content_type"),
                    "uploaded_at": m.get("uploaded_at"), "insights": n.get("observations"),
                    "related_goal_ids": n.get("related_goal_ids") or []})
    return out


class ApiError(Exception):
    def __init__(self, http: int, code: str):
        super().__init__(code)
        self.http, self.code = http, code


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def check_rid(rid: str) -> str:
    if not RID_RE.match(rid or ""):
        raise ApiError(400, "BAD_RECORDING_ID")
    return rid


def check_actor(actor):
    """A signed-in Reviewer (hosted: review_api.auth) or, for the local laptop
    demo only, a plain reviewer id from the X-Reviewer-Id header."""
    if hasattr(actor, "uid") and hasattr(actor, "email"):
        return actor
    if not isinstance(actor, str) or not ACTOR_RE.match(actor):
        raise ApiError(401, "REVIEWER_REQUIRED")
    return actor


def actor_id(actor) -> str:
    return actor.uid if hasattr(actor, "uid") else actor


def log_actor(actor) -> str:
    """For Cloud Logging: a short one-way reference to the signed-in user (uids are
    mixed-case and would be redacted by safelog; emails never go to logs)."""
    if hasattr(actor, "uid"):
        return "u_" + hashlib.sha256(actor.uid.encode()).hexdigest()[:16]
    return actor


def signed_in(actor) -> bool:
    return hasattr(actor, "uid")


def is_read_only(actor) -> bool:
    """RBT: reads everything, changes nothing."""
    return signed_in(actor) and actor.role in READ_ONLY_ROLES


def is_teacher(actor) -> bool:
    if signed_in(actor):
        return actor.role in ("teacher", "reviewer") and bool(actor.teacher_ids)
    return isinstance(actor, str) and bool(actor)


def _owners(session: dict) -> set:
    owners = set(session.get("owner_teacher_ids") or ())
    if session.get("teacher_id"):
        owners.add(session["teacher_id"])
    return owners


def may_act(actor, session: dict) -> bool:
    """Edit / approve / export / change classroom: the session's teacher or the
    student's own teacher (roster), or a demo admin. Never an RBT.
    Laptop demo: the X-Reviewer-Id must be one of those teacher ids."""
    owners = _owners(session)
    if signed_in(actor):
        if is_read_only(actor):
            return False
        return bool(actor.is_admin or (owners & set(actor.teacher_ids)))
    return isinstance(actor, str) and actor in owners


def may_see(actor, session: dict) -> bool:
    """Every signed-in, approved role reads every classroom and session
    (teachers, admins and read-only RBTs alike). Laptop demo: unfiltered."""
    return True


def may_see_parent_email(actor, session: dict) -> bool:
    """Parent emails: only that student's teachers (and the session's teacher) and admins."""
    if actor is None:
        return True                       # laptop demo, no signed-in user
    if is_read_only(actor):
        return False
    return may_act(actor, session)


def scrub_parent_email(obj):
    """Drop every parent-email key, at any depth."""
    if isinstance(obj, dict):
        return {k: scrub_parent_email(v) for k, v in obj.items() if not PARENT_KEY_RE.search(str(k))}
    if isinstance(obj, list):
        return [scrub_parent_email(x) for x in obj]
    return obj


def rbt_iep_access(roster: dict) -> str:
    """The school's IEP access setting for RBTs: "read" (default) or "none"."""
    v = ((roster.get("settings") or {}).get("iep_access") or {}).get("rbt", "read")
    return v if v in ("read", "none") else "read"


class Notes:
    def __init__(self, store, *, roster_ttl: float = ROSTER_TTL, now=time.monotonic, recorder=None,
                 bug_options: dict | None = None, name_options: dict | None = None):
        self.store = store
        self.recorder = recorder if recorder is not None else Recorder()
        self._roster_doc, self._roster_at, self._roster_ttl, self._now = {}, None, roster_ttl, now
        self._roster_applied = None
        self._lock = threading.Lock()
        from .bug_reports import BugReports        # Bug markers (tasks#94): never markers / incidents
        self.bugs = BugReports(self, **(bug_options or {}))
        from .incident_names import Namer          # AI incident names (David, Oct 8)
        self.names = Namer(self, **(name_options or {}))

    def roster_base(self) -> dict:
        """The roster file as deployed (config/demo_roster.json), cached."""
        with self._lock:
            if self._roster_at is None or self._now() - self._roster_at >= self._roster_ttl:
                self._roster_doc, self._roster_at = self.store.read_json(ROSTER_OBJECT) or {}, self._now()
                self._roster_applied = None
            return self._roster_doc

    def roster(self) -> dict:
        """The roster with everyone's saved My profile / sign-in email changes (review_api/people.py)."""
        from .people import apply_roster, state_for
        base = self.roster_base()
        state = state_for(self.store).get()
        key = (id(base), state.get("version"))
        with self._lock:
            if getattr(self, "_roster_applied", None) is None or self._roster_applied[0] != key:
                self._roster_applied = (key, apply_roster(base, state))
            return self._roster_applied[1]

    def forget_roster(self) -> None:
        with self._lock:
            self._roster_applied = None

    def student_teacher_ids(self, student_id) -> set:
        """A student's own teachers: teachers of every roster classroom that lists the
        student, plus roster students.<id>.teacher_ids."""
        if not student_id:
            return set()
        r = self.roster()
        out = {e.get("teacher_id") for e in (r.get("class_codes") or {}).values()
               if isinstance(e, dict) and e.get("student_id") == student_id and e.get("teacher_id")}
        st = (r.get("students") or {}).get(student_id) or {}
        out.update(t for t in (st.get("teacher_ids") or []) if isinstance(t, str))
        for d in student_classrooms(r, student_id):           # many-per-classroom roster (Oct 10)
            t = ((r.get("class_codes") or {}).get(d) or {}).get("teacher_id")
            if t:
                out.add(t)
        return out

    def home_classrooms(self, actor) -> list[str]:
        """The classrooms the list opens filtered to: a teacher's own classrooms, an
        RBT's classroom from the roster. Admin: none (opens on All)."""
        if not signed_in(actor) or actor.is_admin:
            return []
        r = self.roster()
        codes = r.get("class_codes") or {}
        if is_read_only(actor):
            return sorted(d for d in codes if class_key(d) in actor.class_keys)
        return sorted(d for d, e in codes.items() if isinstance(e, dict) and e.get("teacher_id") in actor.teacher_ids)

    def teacher_default_classroom(self, teacher_id) -> str | None:
        """A teacher's default classroom (David, Oct 8): roster teachers.<id>.default_class_code
        when it names one of the roster's classrooms, else their first classroom that has a
        student, else their first classroom. None for no classroom."""
        r = self.roster()
        codes = r.get("class_codes") or {}
        own = sorted(d for d, e in codes.items() if isinstance(e, dict) and e.get("teacher_id") == teacher_id)
        want = ((r.get("teachers") or {}).get(teacher_id) or {}).get("default_class_code")
        if isinstance(want, str):
            for d in codes:
                if class_key(d) == class_key(want):
                    return d
        with_student = [d for d in own if class_students(r, d, codes[d])]
        return (with_student or own or [None])[0]

    def default_classroom(self, actor) -> str | None:
        """Where every classroom dropdown opens (David, Oct 8): a teacher's default classroom,
        an RBT's first roster classroom; admins and the laptop demo: None (= All)."""
        if not signed_in(actor):
            return None
        if actor.is_admin or is_read_only(actor):
            # My profile (tasks#107): an admin's / RBT's own pick, if it is still one of theirs
            from .people import saved_profile
            want = saved_profile(self.store, self.roster(), actor.email).get("default_classroom")
            codes = self.roster().get("class_codes") or {}
            if actor.is_admin:
                return want if want in codes else None
            home = self.home_classrooms(actor)
            return want if want in home else (home[0] if home else None)
        for tid in sorted(actor.teacher_ids):
            d = self.teacher_default_classroom(tid)
            if d:
                return d
        return None

    # ------------------------------------------------------------ reads
    def _draft(self, rid: str) -> dict:
        d = self.store.read_json(f"drafts/{check_rid(rid)}.json")
        if d is None:
            raise ApiError(404, "NOT_FOUND")
        return d

    def approval(self, rid: str) -> dict | None:
        return self.store.read_json(f"approvals/{check_rid(rid)}.json")

    def classroom_changes(self, rid: str | None = None) -> dict:
        """recording id -> audit object names of its classroom changes, oldest first."""
        out: dict = {}
        for n in sorted(self.store.list(f"audit/{rid}/" if rid else "audit/")):
            parts = n.split("/")
            if len(parts) == 3 and f"_{CLASSROOM_EVENT}_" in parts[2]:
                out.setdefault(parts[1], []).append(n)
        return out

    def session_student_links(self, rid: str | None = None) -> dict:
        """recording id -> the student ids a teacher said were in the session (latest record wins).
        Sessions nobody has set are absent. One listing of audit/ (or audit/<rid>/)."""
        latest: dict = {}
        for n in sorted(self.store.list(f"audit/{rid}/" if rid else "audit/")):
            parts = n.split("/")
            if len(parts) == 3 and f"_{STUDENTS_EVENT}_" in parts[2]:
                latest[parts[1]] = n
        out = {}
        for r, n in latest.items():
            ids = (self.store.read_json(n) or {}).get("student_ids")
            out[r] = [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else []
        return out

    def _context(self, rid: str, draft: dict, changes: list | None = None, links: list | None = None,
                 *, _links_known: bool = False) -> dict:
        """Session facts for display: draft.session, filled in from the transcript
        (teacher name) and the recorder sidecar (markers) for older drafts, then the
        latest classroom change (the session follows its classroom and teacher)."""
        s = dict(draft.get("session") or {})
        if changes is None:
            changes = self.classroom_changes(rid).get(rid, [])
        if changes:
            last = self.store.read_json(changes[-1]) or {}
            ctx = resolve_session(self.roster(), filename_code=last.get("to_class_code"), sidecar=None)
            s.update({k: ctx[k] for k in ROSTER_FIELDS})
            if s.get("teacher_id"):
                s["teacher_source"] = "classroom_change"
            s["classroom_changed_at"] = last.get("at")
            s["classroom_changed_from"] = last.get("from_class_code")
        else:
            self._remap_legacy(s)
        # students in this session (tasks#106): set by a teacher; else the recording's own student, if any
        if not _links_known and links is None:
            links = self.session_student_links(rid).get(rid)
        s["students_set"] = links is not None
        s["student_ids"] = list(links) if links is not None else ([s["student_id"]] if s.get("student_id") else [])
        owners = set(self.student_teacher_ids(s.get("student_id")))
        for sid in s["student_ids"]:
            owners |= self.student_teacher_ids(sid)
        s["owner_teacher_ids"] = sorted(owners)
        t = None
        if not s.get("teacher_name") or s.get("markers") is None:
            t = self.store.read_json(f"transcripts/{rid}.json") or {}
            if not s.get("teacher_name"):
                s["teacher_name"] = t.get("teacher_name")
        if s.get("markers") is None:
            markers = (t or {}).get("markers")
            if markers is None:
                side = None
                src = s.get("source_object") or _source_from_session(s)
                if src:
                    side = _read_sidecar(self.store, src[:-4] + ".json")
                markers = markers_from_sidecar(side, s.get("duration_seconds"))
            s["markers"] = markers
        if "recording_gaps" not in s:
            if t is None:
                t = self.store.read_json(f"transcripts/{rid}.json") or {}
            s["recording_gaps"] = (t or {}).get("recording_gaps") or []
        return s

    def _remap_legacy(self, s: dict) -> None:
        """Recordings made before the roster changed (Oct 10: Goalvera demo -> Everyday Heroes
        Academy) follow the current roster when they are read; nothing stored is rewritten.
          * classroom: the stored code (or the file-name code) found under another spelling or as an
            alias of a roster classroom ("RM12", "Reading 3A", "room-b" -> "Room 104") shows as
            that classroom, with classroom_remapped_from = the stored code.
          * teacher: a stored teacher the roster no longer has -> the classroom's teacher.
          * student: a stored student the roster no longer has is unlinked (student_id None,
            legacy_student_id kept); the stored first name stays for display and name scrubbing.
        A recording whose code is in no roster entry keeps needs_class_assignment."""
        r = self.roster()
        old = s.get("class_code")
        display, entry = find_class(r, old)
        if display is None and not old and s.get("class_code_file"):
            display, entry = find_class(r, s.get("class_code_file"))
        teachers = r.get("teachers") or {}
        if display is not None:
            if display != old:
                s["classroom_remapped_from"] = old
                s["class_code"] = display
            s["class_key"] = class_key(display)
            s["needs_class_assignment"] = False
            if entry.get("teacher_id") and (not s.get("teacher_id") or s.get("teacher_id") not in teachers):
                s["teacher_id"], s["teacher_name"] = entry["teacher_id"], entry.get("teacher_name")
                s["teacher_source"] = "class_roster"
        sid = s.get("student_id")
        if sid and find_student(r, sid)[0] is None:
            s["legacy_student_id"], s["student_id"] = sid, None

    def list_sessions(self, viewer=None) -> list[dict]:
        out = []
        changes = self.classroom_changes()          # one listing for the whole list
        links = self.session_student_links()
        bugs = self.bugs.index()
        named = self.names.index()
        for name in self.store.list("drafts/"):
            if not name.endswith(".json"):
                continue
            rid = name[len("drafts/"):-len(".json")]
            if not RID_RE.match(rid):
                continue
            row = self.session(rid, viewer, _changes=changes.get(rid, []), _bugs=bugs.get(rid, []),
                               _names=named.get(rid, []), _links=links.get(rid), _links_known=True)
            if may_see(viewer, row):
                out.append(row)
        out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return out

    def context(self, rid: str) -> dict:
        return self._context(rid, self._draft(rid))

    def named_analysis(self, rid: str, analysis: dict, s: dict, names: list | None = None) -> dict:
        """The notes as shown and printed: incidents with their names (teacher-edited / approved,
        else the stored AI name; every name re-checked), and no per-session parent email (David,
        Oct 8: the parent email belongs to the weekly student summary; old ones stay stored, hidden)."""
        from .bug_reports import never_values
        a = self.names.merge(analysis, self.names.stored(rid, names), never_values(self.roster(), s))
        a.pop("draft_parent_email", None)
        return a

    def session(self, rid: str, viewer=None, *, _changes: list | None = None, _bugs: list | None = None,
                _names: list | None = None, _links: list | None = None, _links_known: bool = False) -> dict:
        d = self._draft(rid)
        s = self._context(rid, d, _changes, _links, _links_known=_links_known)
        ap = self.approval(rid)
        analysis = self.named_analysis(rid, (ap or {}).get("analysis") or d.get("analysis", {}), s, _names)
        needs = bool(s.get("needs_class_assignment")) or not s.get("teacher_id")
        mine = viewer is None or may_act(viewer, s)
        row = self._row(rid, d, s, ap, analysis, needs, viewer, mine)
        # Bug markers: a separate list, only the reporter's own (admins: all); never in markers/incidents
        # recorder Bug markers become pending bug reports too (once; bug_reports.ensure_device)
        _bugs = self.bugs.ensure_device(rid, s, _bugs)
        row["bug_reports"] = self.bugs.visible(rid, viewer, _bugs, session=s)
        start = recording_start(s)
        row["recording_start"] = start.isoformat() if start else None     # incidents' Eastern clock time
        if not may_see_parent_email(viewer, s):
            row = scrub_parent_email(row)
            row["parent_contact_hidden"] = True
        if is_read_only(viewer) and rbt_iep_access(self.roster()) == "none":
            row["processed_analysis"] = {**row["processed_analysis"], "goal_evaluations": []}
            row["iep_hidden"] = True
        return row

    def _row(self, rid, d, s, ap, analysis, needs, viewer, mine) -> dict:
        unassigned = bool(s.get("needs_class_assignment")) or not s.get("teacher_id")
        return {
            "recording_id": d.get("recording_id") or rid,
            "student_id": s.get("student_id"),
            "student_ids": s.get("student_ids") or [],
            "students_set": bool(s.get("students_set")),
            "teacher_id": s.get("teacher_id"),
            "teacher_name": s.get("teacher_name"),
            "teacher_source": s.get("teacher_source"),
            "timestamp_start": s.get("timestamp_start"),
            "duration_seconds": s.get("duration_seconds", 0),
            "status": "approved" if ap else d.get("status", "pending_review"),
            # unknown class / no teacher: shown in the review list; Approve is blocked
            "needs_class_assignment": needs and not ap,
            "can_approve": not ap and not needs and mine,
            "viewer_may_act": mine,
            "viewer_read_only": is_read_only(viewer),
            "viewer_may_change_classroom": (not ap and not is_read_only(viewer)
                                            and (mine or (unassigned and (viewer is None or is_teacher(viewer)
                                                                          or (signed_in(viewer) and viewer.is_admin))))),
            "classroom_changed_at": s.get("classroom_changed_at"),
            "classroom_changed_from": s.get("classroom_changed_from"),
            "approved_at": (ap or {}).get("approved_at"),
            "approved_by_name": (ap or {}).get("approved_by_name"),
            "approved_by_email": (ap or {}).get("approved_by_email"),
            "incident_flag_count": len(analysis.get("incident_analysis", [])),   # numbered 1..n by time: incident_print.py
            "iep_met": "Review" if not ap else _iep_met(analysis),
            "processed_analysis": analysis,
            "markers": [m for m in (s.get("markers") or [])
                        if str(m.get("type") or "").strip().lower() not in {t.lower() for t in HIDDEN_MARKER_TYPES}],
            "source": "pipeline",
            "model": d.get("model"),
            "prompt_version": d.get("prompt_version"),
            "device_id": s.get("device_id"),
            "class_code": s.get("class_code"),
            # Phone recordings: "Recording paused 2:14 for 40 s" (empty for a board recording).
            "recording_gaps": [g for g in (s.get("recording_gaps") or [])
                               if isinstance(g, dict) and isinstance(g.get("offset_seconds"), (int, float))
                               and isinstance(g.get("duration_seconds"), (int, float))],
            "created_at": d.get("created_at"),
            # Teacher Notes photos / videos (tasks#112): from the draft (an approval doesn't carry them);
            # shown in review only, never in the parent email
            "media": session_media(d),
        }

    def media_bytes(self, rid: str, media_id: str) -> tuple[bytes, str]:
        """(bytes, content type) of one Teacher Notes upload of this session; 404 otherwise."""
        d = self._draft(rid)
        for m in d.get("media") or []:
            obj = str(m.get("object") or "")
            if m.get("media_id") == media_id and obj.startswith(f"media/{rid}/"):
                data = self.store.read_bytes(obj) if hasattr(self.store, "read_bytes") else None
                if data is not None:
                    return data, str(m.get("content_type") or "application/octet-stream")
        raise ApiError(404, "NOT_FOUND")

    def audit_trail(self, rid: str) -> list[dict]:
        check_rid(rid)
        return [self.store.read_json(n) for n in self.store.list(f"audit/{rid}/")]

    # ------------------------------------------------------------ writes
    def _audit(self, rid: str, event: str, actor, draft_created: dt.datetime | None, *,
               session: dict | None = None, journal: dict | None = None, ids_only: bool = False,
               journal_required: bool = False, **extra) -> dict:
        """ids_only: the bucket record carries the internal user id and role only (no email).
        journal_required: refuse (JournalUnavailable -> 503) if the journal can't take the entry."""
        now = _now()
        rec = {"event": event, "recording_id": rid, "actor_id": actor_id(actor),
               "at": now.isoformat(timespec="seconds"), **extra}
        if hasattr(actor, "uid"):   # signed-in user: who it was (staff email) and how they signed in
            rec.update(actor_role=actor.role, sign_in_provider=actor.provider)
            if not ids_only:
                rec["actor_email"] = actor.email
        # the admin activity journal (tasks#77): ids only. Written first when required, so a
        # download that can't be journaled leaves no half record behind.
        if journal_required:
            self.recorder.record(actor, event, record_id=rid, student_id=(session or {}).get("student_id"),
                                 required=True, **(journal or {}))
        name = f"audit/{rid}/{now.strftime('%Y%m%dT%H%M%S%fZ')}_{event}_{secrets.token_hex(3)}.json"
        self.store.write_json(name, rec, create_only=True, custom_time=draft_created)
        log(event, step="review", recording_id=rid, actor_id=log_actor(actor),   # logs: ids only, no email
            pdf_sha256=extra.get("pdf_sha256"), pdf_bytes=extra.get("pdf_bytes"),
            incident_number=extra.get("incident_number"), draft=extra.get("draft"))
        if not journal_required:
            self.recorder.record(actor, event, record_id=rid, student_id=(session or {}).get("student_id"),
                                 **(journal or {}))
        return rec

    REPORTS_AUDIT = "audit/_reports/"     # downloads that aren't about one recording (activity-report CSV)

    def audit_report_download(self, actor, report: str, **extra) -> dict:
        """The bucket audit record for an activity-report CSV download (Ops, tasks#108): the same
        create-only record a PDF download gets (audit/<rid>/...): who (internal id, role, staff email,
        how they signed in), when, which report, row count, size and SHA-256 of the file. The journal
        entry (report_downloaded) is written first by the caller, required, like a PDF's."""
        now = _now()
        rec = {"event": "report_downloaded", "report": report, "actor_id": actor_id(actor),
               "at": now.isoformat(timespec="seconds"), **extra}
        if hasattr(actor, "uid"):
            rec.update(actor_role=actor.role, sign_in_provider=actor.provider, actor_email=actor.email)
        name = f"{self.REPORTS_AUDIT}{now.strftime('%Y%m%dT%H%M%S%fZ')}_report_downloaded_{secrets.token_hex(3)}.json"
        self.store.write_json(name, rec, create_only=True)
        log("report_downloaded", step="review", actor_id=log_actor(actor), report=report,
            report_sha256=extra.get("report_sha256"), report_bytes=extra.get("report_bytes"))
        return rec

    def approve(self, rid: str, actor, analysis: dict) -> dict:
        actor = check_actor(actor)
        if is_read_only(actor):
            raise ApiError(403, "READ_ONLY_ROLE")
        d = self._draft(rid)
        s = self._context(rid, d)
        if s.get("needs_class_assignment") or not s.get("teacher_id"):
            raise ApiError(409, "NEEDS_CLASS_ASSIGNMENT")
        if not may_act(actor, s):
            raise ApiError(403, "NOT_SESSION_TEACHER")
        if not isinstance(analysis, dict) or validate_output(analysis):
            raise ApiError(400, "INVALID_NOTES")
        analysis = self._check_names(analysis, s)
        stored_draft = d.get("analysis") or {}
        if stored_draft.get("draft_parent_email") and "draft_parent_email" not in analysis:
            # no longer shown or edited (weekly summary now); the old draft stays stored, hidden
            analysis = {**analysis, "draft_parent_email": stored_draft["draft_parent_email"]}
        created = self.store.created(f"drafts/{rid}.json")
        signed_in = hasattr(actor, "uid")
        rec = {
            "recording_id": rid,
            "approved_by_id": actor_id(actor),
            # signed in: the person who clicked Approve, not the roster teacher
            "approved_by_name": actor.email if signed_in else (s.get("teacher_name") or actor),
            "approved_by_email": actor.email if signed_in else None,
            "approved_as": ("admin" if actor.is_admin else "teacher") if signed_in else "teacher",
            "approved_at": _now().isoformat(timespec="seconds"),
            "analysis": analysis,
            "edited": _comparable(analysis) != _comparable(d.get("analysis") or {}),
            "draft_model": d.get("model"),
            "draft_created_at": d.get("created_at"),
        }
        try:
            self.store.write_json(f"approvals/{rid}.json", rec, create_only=True, custom_time=created)
        except Conflict as exc:
            raise ApiError(409, "ALREADY_APPROVED") from exc
        self._audit(rid, "session_approved", actor, created, edited=rec["edited"], session=s,
                    journal={"edited": rec["edited"]})
        if rec["edited"]:
            self.recorder.record(actor, "notes_edited", record_id=rid, student_id=s.get("student_id"))
        return rec

    def _check_names(self, analysis: dict, s: dict) -> dict:
        """Incident names in approved notes: 80 characters or fewer, no student / staff name, email
        or id, no judgment / intent / diagnosis word (same checks as the AI names)."""
        from .bug_reports import never_values
        from .incident_names import MAX_NAME, usable
        never = never_values(self.roster(), s)
        incs = []
        for inc in analysis.get("incident_analysis") or []:
            inc = dict(inc)
            if inc.get("name") is not None:
                if not isinstance(inc["name"], str) or len(inc["name"]) > MAX_NAME:
                    raise ApiError(400, "INVALID_NOTES")
                if not inc["name"].strip():
                    inc.pop("name"); inc.pop("name_source", None)
                elif usable(inc["name"], never) is None:
                    raise ApiError(422, "INCIDENT_NAME_NOT_ALLOWED")
            incs.append(inc)
        return {**analysis, "incident_analysis": incs} if "incident_analysis" in analysis else analysis

    def incident_names(self, rid: str, actor) -> dict:
        """First view: name the session's incidents that have none yet (incident_names.py), then
        return {"incidents": [{"index", "number", "name", "name_source", "title"}]}. Anyone who may
        read the session; nothing about the session changes."""
        from .incident_print import incident_numbers, incident_title
        d = self._draft(rid)
        s = self._context(rid, d)
        ap = self.approval(rid)
        raw = (ap or {}).get("analysis") or d.get("analysis", {})
        self.names.ensure(rid, raw, s, self.store.read_json(f"transcripts/{rid}.json") or {})
        a = self.named_analysis(rid, raw, s)
        incs = a.get("incident_analysis") or []
        numbers = {id(inc): n for n, inc in incident_numbers(incs)}
        return {"recording_id": rid, "incidents": [
            {"index": i, "number": numbers[id(inc)], "timestamp_seconds": inc.get("timestamp_seconds"),
             "name": inc.get("name"), "name_source": inc.get("name_source"),
             "title": incident_title(numbers[id(inc)], inc.get("name"))} for i, inc in enumerate(incs)]}

    def pdf(self, rid: str, actor) -> tuple[bytes, str]:
        from .pdf_notes import render_notes_pdf

        actor = check_actor(actor)
        if is_read_only(actor):
            raise ApiError(403, "READ_ONLY_ROLE")
        d = self._draft(rid)
        s = self._context(rid, d)
        ap = self.approval(rid)
        if not ap:
            raise ApiError(409, "NOT_APPROVED")
        if not may_act(actor, s):
            raise ApiError(403, "NOT_SESSION_TEACHER")
        goals = {}
        t = self.store.read_json(f"transcripts/{rid}.json") or {}
        for g in t.get("iep_goals", []):
            goals[g.get("goal_id")] = g
        generated = _now()
        ap = {**ap, "analysis": self.named_analysis(rid, ap.get("analysis") or {}, s)}
        data = render_notes_pdf(session=s, approval=ap, goals=goals, recording_id=rid,
                                generated_at=generated, sample_data=True)
        digest = hashlib.sha256(data).hexdigest()
        self._audit(rid, "pdf_downloaded", actor, self.store.created(f"drafts/{rid}.json"),
                    pdf_sha256=digest, pdf_bytes=len(data), approved_at=ap.get("approved_at"), session=s,
                    journal={"pdf_sha256": digest, "pdf_bytes": len(data)})
        fname = f"approved-notes_{s.get('class_code') or 'class'}_{(s.get('timestamp_start') or '')[:10]}_{rid}.pdf"
        return data, re.sub(r"[^A-Za-z0-9_.-]", "_", fname)


    def incident_print(self, rid: str, actor, number) -> dict:
        """One incident's report for the browser's print view (Download PDF on an incident).
        Same people as the approved-notes PDF: the session's teacher, the student's teacher
        or an admin, never an RBT. Approved sessions print the approved notes; a session not
        approved yet prints its stored draft, marked DRAFT (David, Oct 7). Every click is in
        the audit trail and the journal (who, when, recording, incident number, draft or not;
        ids only).
        Nothing is rendered or stored here; see incident_print.py for what may be printed."""
        from .incident_print import PrintBlocked, build_print, student_records

        actor = check_actor(actor)
        if is_read_only(actor):
            raise ApiError(403, "READ_ONLY_ROLE")
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 999:
            raise ApiError(400, "BAD_INCIDENT")
        d = self._draft(rid)
        s = self._context(rid, d)
        ap = self.approval(rid)                 # none yet: the draft prints, marked DRAFT (David, Oct 7)
        if not may_act(actor, s):
            raise ApiError(403, "NOT_SESSION_TEACHER")
        try:
            if ap:
                ap = {**ap, "analysis": self.named_analysis(rid, ap.get("analysis") or {}, s)}
            model = build_print(session=s, approval=ap, number=number, recording_id=rid,
                                draft=None if ap else self.named_analysis(rid, d.get("analysis") or {}, s),
                                roster_records=student_records(self.roster(), s.get("student_id")))
        except KeyError as exc:
            raise ApiError(404, "NO_SUCH_INCIDENT") from exc
        except PrintBlocked as exc:
            log("incident_print_blocked", "ERROR", step="review", recording_id=rid, incident_number=number)
            raise ApiError(500, "PRINT_BLOCKED") from exc
        self._audit(rid, "incident_pdf_downloaded", actor, self.store.created(f"drafts/{rid}.json"),
                    incident_number=number, incident_offset_seconds=model["offset_seconds"], draft=model["draft"],
                    session=s, ids_only=True, journal_required=True,
                    journal={"incident_number": number, "incident_offset_seconds": model["offset_seconds"],
                             "draft": model["draft"]})
        return model

    def bug_report(self, rid: str, actor, body: dict) -> dict:
        """A "Bug" marker: a teacher's report about the review site (tasks#94). See bug_reports.py."""
        return self.bugs.submit(rid, actor, body)

    def change_classroom(self, rid: str, actor, to_code) -> dict:
        """Move a session to another roster classroom. The session then belongs to that
        classroom's teacher. Logged in the audit trail: who, when, from, to."""
        actor = check_actor(actor)
        if is_read_only(actor):
            raise ApiError(403, "READ_ONLY_ROLE")
        d = self._draft(rid)
        s = self._context(rid, d)
        if self.approval(rid):
            raise ApiError(409, "ALREADY_APPROVED")
        display, entry = find_class(self.roster(), to_code if isinstance(to_code, str) else None)
        if entry is None:
            raise ApiError(400, "UNKNOWN_CLASSROOM")
        unassigned = bool(s.get("needs_class_assignment")) or not s.get("teacher_id")
        admin = signed_in(actor) and actor.is_admin
        if not (may_act(actor, s) or (unassigned and (admin or is_teacher(actor)))):
            raise ApiError(403, "NOT_SESSION_TEACHER")
        if not unassigned and class_key(display) == class_key(s.get("class_code")):
            raise ApiError(409, "SAME_CLASSROOM")
        self._audit(rid, CLASSROOM_EVENT, actor, self.store.created(f"drafts/{rid}.json"),
                    from_class_code=s.get("class_code"), to_class_code=display,
                    from_teacher_id=s.get("teacher_id"), to_teacher_id=entry.get("teacher_id"), session=s,
                    journal={"from_classroom": class_key(s.get("class_code")), "to_classroom": class_key(display),
                             "from_teacher_id": s.get("teacher_id"), "to_teacher_id": entry.get("teacher_id")})
        return self.session(rid, actor)


def _comparable(analysis: dict) -> dict:
    """For the "edited" flag: AI names and the (hidden) parent email aren't teacher edits."""
    a = {k: v for k, v in (analysis or {}).items() if k != "draft_parent_email"}
    a["incident_analysis"] = [{k: v for k, v in inc.items() if not (k in ("name", "name_source")
                                                                    and inc.get("name_source") != "teacher")}
                              for inc in a.get("incident_analysis") or []]
    return a


def _iep_met(analysis: dict) -> str:
    goals = analysis.get("goal_evaluations", [])
    return f"{sum(g.get('status') == 'Demonstrated' for g in goals)}/{len(goals)}" if goals else "-"


def _read_sidecar(store, name: str) -> dict | None:
    """The recorder sidecar sits under rec/, which the hosted review service account
    deliberately cannot read (GCS answers 403). Treat that like a missing sidecar:
    the session shows without markers instead of failing the whole list."""
    try:
        return store.read_json(name)
    except ValueError:      # a name no recorder writes (e.g. a space): no sidecar, never a failed list
        return None
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ in ("Forbidden", "PermissionError", "PermissionDenied"):
            return None
        raise


def _source_from_session(s: dict) -> str | None:
    # the code the recorder wrote into the file name, not the classroom it shows under now (a
    # remapped "RM12" recording displays as "Room 104" but its file is still ..._RM12.wav)
    cls = s.get("class_code_file") or s.get("classroom_remapped_from") or s.get("class_code")
    ts, dev = s.get("timestamp_start") or "", s.get("device_id")
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", ts)
    if not (m and dev and cls):
        return None
    return f"rec/{dev}/{''.join(m.groups()[:3])}_{''.join(m.groups()[3:])}_{cls}.wav"
