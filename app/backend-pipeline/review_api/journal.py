"""Admin activity journal (tasks#77): one add-only record of who did what.

Every entry: who (internal user id + role), what they did, which record, when.
Internal IDs only: no names, no emails, no free text. One exception (Ops, tasks#108):
an access change (access_changed) carries the staff sign-in address(es) it applied to, and
both the old and the new address when a sign-in email changes; no other action may. A student appears only by
their random internal ID (e.g. stu_101); names are looked up when an admin
views or downloads a report (journal_reports.py), never copied into the journal.

Storage: BigQuery, this school's own dataset in example-project,
us-east1 (infra/setup_journal.sh). The review service account can only append
rows (insertAll; custom role journalAppender on the table, no BigQuery job
rights anywhere), so it can't update, delete or truncate. Reports are read
through an authorized view by a separate reader account.

Kept for as long as the school is a customer; removed with the dataset at the
end of the contract (decided on tasks#77).

Writers:
  MemoryJournal    tests and the laptop demo (no update / delete methods)
  BigQueryJournal  hosted (JOURNAL_MODE=bigquery)
  NullJournal      journal off
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass

SCHOOL_ID = os.environ.get("JOURNAL_SCHOOL_ID", "demo-school")

# action -> what kind of record it is about
ACTIONS = {
    "sign_in": "account",
    "sessions_listed": None,
    "session_viewed": "session",          # opened a session (its transcript)
    "media_viewed": "session",            # opened a Teacher Notes photo / video (tasks#112)
    "audit_viewed": "session",
    "notes_edited": "session",            # edited notes, saved with the approval
    "session_approved": "session",
    "pdf_downloaded": "session",          # export
    "incident_pdf_downloaded": "session", # export: one incident's print view / Save as PDF (number in details)
    "classroom_changed": "session",
    "bug_reported": "session",            # a teacher's Bug marker (tasks#94): report id, pending/filed, draft
    "bug_report_filed": "session",        # that report became a GitHub issue (issue number in details)
    "request_refused": None,              # a refused attempt (e.g. an RBT trying to approve)
    "journal_viewed": "report",
    "report_downloaded": "report",        # export
    "iep_viewed": "student",
    "iep_edited": "student",
    "roster_imported": "roster",
    "settings_changed": "settings",
    "planner_viewed": "plan",             # weekly lesson planner opened (tasks#83)
    "planner_plan_saved": "plan",         # a new version of a classroom's weekly plan
    "planner_summary_saved": "student",   # a new version of a student's weekly summary
    "planner_note_saved": "student",      # a new version of a teacher note for the week
    "planner_summary_drafted": "student", # an AI draft of a weekly summary was made (not stored)
    "planner_plan_drafted": "plan",       # an AI draft of a week's goals/objectives was made (not stored)
    # classroom materials from Teacher Notes (tasks#99; materials.py)
    "materials_suggested": "session",     # AI looked at the session's Teacher Notes (requests found, notes)
    "materials_requested": "session",     # a teacher picked a suggestion / filled the form (type, count)
    "materials_made": "session",          # the material was made (pages, pictures, PDF fingerprint)
    "materials_downloaded": "session",    # export: its PDF was downloaded
    "materials_emailed": "session",       # emailed to the teacher's own staff address (no address logged)
    "materials_resent": "session",        # emailed again (Resend)
    "materials_send_failed": "session",   # not emailed: email_status says why (setting off, no address...)
    "materials_attached": "plan",         # a session's material was added to its classroom's lesson plan
    "materials_removed": "plan",          # a material was taken off a lesson plan (or replaced by a new version)
    # Domains (educational methodology) and Classes (tasks#103; school_setup.py)
    "domain_added": "domain",             # admin: a Subject / Domain (changed field names only, no text)
    "domain_edited": "domain",
    "domain_removed": "domain",
    "class_added": "class",               # admin: a class (classroom, counts of teachers / students / meetings)
    "class_edited": "class",              # admin, or a teacher on their own class
    "class_removed": "class",
    "session_class_assigned": "session",  # a session's class picked (class id; was it the suggestion)
    "session_students_set": "session",    # students in this session (tasks#106): count; was it the suggestion
    # weekly parent email (tasks#106; parent_email.py): one teacher click per email, never automatic
    "parent_email_sent": "student",       # sent (recipients count; redirected to the teacher in demo mode)
    "parent_email_send_failed": "student",
    # My profile + admin access changes (tasks#107; people.py): field names only, never values
    "profile_updated": "person",          # someone edited their own profile (which fields)
    "access_changed": "person",           # an admin changed a person's app role and/or sign-in email
}
ROLES = frozenset({"admin", "teacher", "reviewer", "rbt", "laptop", "system"})
RECORD_TYPES = frozenset({"session", "student", "report", "roster", "settings", "account", "plan", "class", "domain", "person"})

_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")
_RID = re.compile(r"^rec_[A-Za-z0-9_\-]{1,120}$")
_NAME_LIKE = re.compile(r"[A-Z][a-z]{2,}")         # "Marcus", "Jenkins": never an id here
_HEX = re.compile(r"^[0-9a-f]{16,64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# details: a closed list of keys, each with the only shape it may take
DETAIL_KEYS = {
    "edited": bool, "sessions": int, "row_count": int, "pdf_bytes": int,
    "pdf_sha256": "hex", "report_sha256": "hex",
    "from_classroom": "id", "to_classroom": "id", "from_teacher_id": "id", "to_teacher_id": "id",
    "error_code": "id", "attempted": "id", "report": "id", "group_by": "id",
    "period_from": "date", "period_to": "date", "filter_action": "id", "filter_actor_id": "id",
    "filter_student_id": "id", "audit_object_sha": "hex",
    "week": "date", "past_week": "date", "approved": bool, "ai_source": "id",
    "incident_number": int, "incident_offset_seconds": int, "draft": bool,
    "bug_report_id": "id", "issue_number": int, "issue_status": "id",
    "setting": "id", "image_format": "id",
    "material_id": "id", "material_type": "id", "count": int, "requests": int, "note_index": int,
    "regenerate_of": "id", "pages": int, "pictures": int, "email_status": "id", "enabled": bool,
    "source_recording_id": "rid",
    "renderer": "id", "style": "id", "instruction_key": "id", "pictures_missing": int,
    "drafts": int, "draft_number": int, "reset": bool, "chars": int, "text_sha256": "hex",     # settings_changed: which field (school_name, school_logo, reset)
    # Domains / Classes (tasks#103): ids, field names and counts only, never names or text
    "fields": "id", "classroom": "id", "teachers": int, "students": int, "meetings": int,
    "domain_id": "id", "class_id": "id", "suggested": bool,
    "recipients": int, "redirected": bool, "excluded": int,
    "app_role": "id",                     # access_changed: the new app role (admin / teacher / rbt / none)
    "sign_in_provider": "id",             # sign_in: "password" or "google.com" (tasks#108)
    # access_changed (Ops, tasks#108): old and new role, and the staff sign-in addresses involved.
    # "email" values are allowed on access_changed ONLY (make_entry refuses them anywhere else).
    "old_role": "id", "new_role": "id",
    "account_email": "email", "old_email": "email", "new_email": "email",
    "report_bytes": int,                  # report_downloaded: size of the CSV, like pdf_bytes
}
EMAIL_DETAIL_ACTIONS = frozenset({"access_changed"})

SCHEMA = [  # BigQuery table schema (infra/setup_journal.sh writes it out)
    {"name": "event_id", "type": "STRING", "mode": "REQUIRED"},
    {"name": "occurred_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
    {"name": "school_id", "type": "STRING", "mode": "REQUIRED"},
    {"name": "actor_id", "type": "STRING", "mode": "REQUIRED"},
    {"name": "actor_role", "type": "STRING", "mode": "REQUIRED"},
    {"name": "action", "type": "STRING", "mode": "REQUIRED"},
    {"name": "record_type", "type": "STRING", "mode": "NULLABLE"},
    {"name": "record_id", "type": "STRING", "mode": "NULLABLE"},
    {"name": "student_id", "type": "STRING", "mode": "NULLABLE"},
    {"name": "details", "type": "JSON", "mode": "NULLABLE"},
    {"name": "source", "type": "STRING", "mode": "REQUIRED"},
]
COLUMNS = [c["name"] for c in SCHEMA]


class JournalError(ValueError):
    pass


def _check_id(field: str, v, *, required: bool = False, no_words: bool = False):
    """An internal id: no spaces, no "@". no_words: also no capitalized words (for
    student ids and detail values; uids and recording ids are mixed-case by nature)."""
    if v is None:
        if required:
            raise JournalError(f"{field} required")
        return None
    if not isinstance(v, str) or not _ID.match(v) or "@" in v or (no_words and _NAME_LIKE.search(v)):
        raise JournalError(f"{field} must be an internal id")
    return v


_EMAIL = re.compile(r"^[^@\sA-Z]{1,64}@[a-z0-9.\-]{1,190}\.[a-z]{2,24}$")   # = people._EMAIL, lower-cased


def _check_details(details: dict | None, action: str | None = None) -> dict | None:
    if not details:
        return None
    out = {}
    for k, v in details.items():
        kind = DETAIL_KEYS.get(k)
        if kind is None:
            raise JournalError(f"details.{k} not allowed")
        if v is None:
            continue
        if kind is bool and isinstance(v, bool):
            out[k] = v
        elif kind is int and isinstance(v, int) and not isinstance(v, bool):
            out[k] = v
        elif kind == "hex" and isinstance(v, str) and _HEX.match(v):
            out[k] = v
        elif kind == "date" and isinstance(v, str) and _DATE.match(v):
            out[k] = v
        elif kind == "rid" and isinstance(v, str) and _RID.match(v):      # a recording id (as in record_id)
            out[k] = v
        elif kind == "id":
            out[k] = _check_id(f"details.{k}", v, no_words=True)
        elif kind == "email":
            # a staff sign-in address, on an access change only (Ops, tasks#108); never anywhere else
            if action not in EMAIL_DETAIL_ACTIONS:
                raise JournalError(f"details.{k}: an address is allowed on access changes only")
            if not isinstance(v, str) or not _EMAIL.match(v):
                raise JournalError(f"details.{k} must be a lower-case email address")
            out[k] = v
        else:
            raise JournalError(f"details.{k} has the wrong shape")
    return out or None


def make_entry(*, action: str, actor_id: str, actor_role: str, record_id: str | None = None,
               student_id: str | None = None, details: dict | None = None, record_type: str | None = None,
               occurred_at: dt.datetime | None = None, source: str = "review_api",
               event_id: str | None = None) -> dict:
    """One journal row, validated. Raises JournalError on anything that isn't an id."""
    if action not in ACTIONS:
        raise JournalError(f"unknown action {action!r}")
    if actor_role not in ROLES:
        raise JournalError("unknown role")
    rtype = record_type or ACTIONS[action]
    if rtype is not None and rtype not in RECORD_TYPES:
        raise JournalError("unknown record type")
    when = (occurred_at or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    return {
        "event_id": _check_id("event_id", event_id or uuid.uuid4().hex, required=True),
        "occurred_at": when.isoformat(timespec="microseconds"),
        "school_id": _check_id("school_id", SCHOOL_ID, required=True),
        "actor_id": _check_id("actor_id", actor_id, required=True),
        "actor_role": actor_role,
        "action": action,
        "record_type": rtype if (record_id or rtype in ("report", "account")) else None,
        "record_id": _check_id("record_id", record_id),
        "student_id": _check_id("student_id", student_id, no_words=True),
        "details": _check_details(details, action),
        "source": _check_id("source", source, required=True),
    }


# ---------------------------------------------------------------- filters (shared by every reader)

@dataclass(frozen=True)
class Query:
    period_from: dt.date
    period_to: dt.date                    # inclusive
    actor_id: str | None = None
    action: str | None = None
    student_id: str | None = None
    limit: int = 500

    MAX_DAYS = 400
    MAX_LIMIT = 5000

    @classmethod
    def parse(cls, params: dict, today: dt.date | None = None) -> "Query":
        today = today or dt.datetime.now(dt.timezone.utc).date()
        try:
            to = dt.date.fromisoformat(params.get("to") or today.isoformat())
            frm = dt.date.fromisoformat(params.get("from") or (to - dt.timedelta(days=30)).isoformat())
            limit = int(params.get("limit") or 500)
        except ValueError as exc:
            raise JournalError("bad date or limit") from exc
        if frm > to or (to - frm).days > cls.MAX_DAYS:
            raise JournalError("date range too long")             # cost cap, with maximum_bytes_billed
        action = params.get("action") or None
        if action is not None and action not in ACTIONS:
            raise JournalError("unknown action")
        return cls(frm, to, _check_id("actor", params.get("actor") or None), action,
                   _check_id("student", params.get("student") or None, no_words=True), max(1, min(limit, cls.MAX_LIMIT)))

    def matches(self, row: dict) -> bool:
        day = dt.datetime.fromisoformat(row["occurred_at"]).date()
        return (self.period_from <= day <= self.period_to
                and (self.actor_id is None or row["actor_id"] == self.actor_id)
                and (self.action is None or row["action"] == self.action)
                and (self.student_id is None or row["student_id"] == self.student_id))


# ---------------------------------------------------------------- writers

class NullJournal:
    kind = "off"

    def append(self, entries: list[dict]) -> None:
        pass

    def query(self, q: Query) -> list[dict]:
        return []


class MemoryJournal:
    """Add-only, in memory. There is deliberately no update or delete."""
    kind = "memory"

    def __init__(self):
        self._rows: list[dict] = []
        self._ids: set = set()
        self._lock = threading.Lock()

    def append(self, entries: list[dict]) -> None:
        with self._lock:
            for e in entries:
                if set(e) != set(COLUMNS):
                    raise JournalError("not a journal row")
                if e["event_id"] in self._ids:            # same as BigQuery insertId: retries don't double up
                    continue
                self._ids.add(e["event_id"])
                self._rows.append(json.loads(json.dumps(e)))

    def rows(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._rows]

    def query(self, q: Query) -> list[dict]:
        rows = sorted((r for r in self.rows() if q.matches(r)), key=lambda r: r["occurred_at"], reverse=True)
        return rows[: q.limit]


# The reader token's OAuth scope. Not bigquery.readonly: client.query() calls jobs.insert,
# which refuses that scope with 403 "insufficient authentication scopes" (the cause of the
# Oct 7 2026 "error 502, INTERNAL" on the admin Journal). What the reader may do is still
# set by IAM alone: bigquery.jobUser on the project + READER on the reports dataset only.
READER_SCOPES = ["https://www.googleapis.com/auth/bigquery"]


class JournalReadFailed(Exception):
    """Reading the journal failed. reason: our short code for the admin's error message;
    google_reason: Google's own error reason (an identifier, never a message)."""

    def __init__(self, reason: str, google_reason: str | None = None, http_status: int | None = None):
        super().__init__(reason)
        self.reason, self.google_reason, self.http_status = reason, google_reason, http_status


def read_failure(exc: Exception) -> JournalReadFailed:
    """Turn a BigQuery / token error into a JournalReadFailed with codes only."""
    status = getattr(exc, "code", None)
    status = status if isinstance(status, int) else None
    greason = None
    errs = getattr(exc, "errors", None)
    if isinstance(errs, list) and errs and isinstance(errs[0], dict):
        greason = errs[0].get("reason")
    if greason is None:
        greason = getattr(exc, "reason", None) if isinstance(getattr(exc, "reason", None), str) else None
    name = type(exc).__name__
    if name in ("RefreshError", "TransportError"):
        reason = "READER_TOKEN_FAILED"            # review-web couldn't get the reader account's token
    elif status == 403:
        reason = "ACCESS_DENIED"
    elif status == 404:
        reason = "NOT_FOUND"
    elif status == 400:
        reason = "QUERY_REJECTED"                 # includes the bytes cap (bytesBilledLimitExceeded)
    else:
        reason = "BACKEND_ERROR"
    return JournalReadFailed(reason, greason, status)


class BigQueryJournal:
    """Hosted. Appends with insertAll (insertId = event_id) as the review service
    account, which has only bigquery.tables.updateData + get on the table.
    Reads go to the reports view as the reader account, with a bytes cap."""
    kind = "bigquery"

    def __init__(self, project: str, dataset: str, table: str, reports_dataset: str, *,
                 writer=None, reader=None, reader_sa: str | None = None,
                 max_bytes: int = int(os.environ.get("JOURNAL_MAX_BYTES", str(1 << 30))),
                 location: str = "us-east1"):
        self.project, self.dataset, self.table, self.reports = project, dataset, table, reports_dataset
        self.max_bytes, self.location, self.reader_sa = max_bytes, location, reader_sa
        self._writer, self._reader = writer, reader

    @property
    def table_id(self) -> str:
        return f"{self.project}.{self.dataset}.{self.table}"

    @property
    def view_id(self) -> str:
        return f"{self.project}.{self.reports}.{self.table}"

    def _writer_client(self):
        if self._writer is None:
            from google.cloud import bigquery  # type: ignore
            self._writer = bigquery.Client(project=self.project, location=self.location)
        return self._writer

    def _reader_client(self):
        if self._reader is None:
            import google.auth  # type: ignore
            from google.auth import impersonated_credentials  # type: ignore
            from google.cloud import bigquery  # type: ignore
            src, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            creds = impersonated_credentials.Credentials(
                source_credentials=src, target_principal=self.reader_sa,
                target_scopes=READER_SCOPES, lifetime=600)
            self._reader = bigquery.Client(project=self.project, credentials=creds, location=self.location)
        return self._reader

    def append(self, entries: list[dict]) -> None:
        if not entries:
            return
        rows = [{**e, "details": json.dumps(e["details"]) if e["details"] is not None else None} for e in entries]
        errors = self._writer_client().insert_rows_json(self.table_id, rows, row_ids=[e["event_id"] for e in entries])
        if errors:
            raise JournalError("journal insert failed")

    def sql(self, q: Query) -> tuple[str, list]:
        where = ["occurred_at >= TIMESTAMP(@frm)", "occurred_at < TIMESTAMP(DATE_ADD(@upto, INTERVAL 1 DAY))"]
        params = [("frm", "DATE", q.period_from.isoformat()), ("upto", "DATE", q.period_to.isoformat())]
        for col, val in (("actor_id", q.actor_id), ("action", q.action), ("student_id", q.student_id)):
            if val is not None:
                where.append(f"{col} = @{col}")
                params.append((col, "STRING", val))
        params.append(("lim", "INT64", q.limit))
        cols = ", ".join(c if c != "details" else "TO_JSON_STRING(details) AS details" for c in COLUMNS)
        # WHERE first (partition pruning keeps the scan to the asked-for days), then drop
        # rows a retried insert may have doubled (insertId dedup is best effort).
        sql = (f"SELECT {cols} FROM `{self.view_id}` WHERE {' AND '.join(where)} "
               f"QUALIFY ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY occurred_at) = 1 "
               f"ORDER BY occurred_at DESC LIMIT @lim")
        return sql, params

    def query(self, q: Query) -> list[dict]:
        from google.cloud import bigquery  # type: ignore
        sql, params = self.sql(q)
        cfg = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter(n, t, v) for n, t, v in params],
            maximum_bytes_billed=self.max_bytes, use_query_cache=True)
        try:
            result = list(self._reader_client().query(sql, job_config=cfg).result())
        except Exception as exc:  # noqa: BLE001 - mapped to codes; the message is never shown or logged
            raise read_failure(exc) from exc
        out = []
        for r in result:
            row = dict(r.items())
            row["occurred_at"] = row["occurred_at"].isoformat() if hasattr(row["occurred_at"], "isoformat") else row["occurred_at"]
            row["details"] = json.loads(row["details"]) if row.get("details") else None
            out.append(row)
        return out


def journal_from_env() -> object:
    mode = os.environ.get("JOURNAL_MODE", "off")
    if mode == "bigquery":
        project = os.environ.get("PROJECT_ID", "example-project")
        return BigQueryJournal(project, os.environ.get("JOURNAL_DATASET", "goalvera_journal"),
                               os.environ.get("JOURNAL_TABLE", "activity"),
                               os.environ.get("JOURNAL_REPORTS_DATASET", "goalvera_journal_reports"),
                               reader_sa=os.environ.get("JOURNAL_READER_SA"),
                               location=os.environ.get("JOURNAL_LOCATION", "us-east1"))
    if mode == "memory":
        return MemoryJournal()
    return NullJournal()


# ---------------------------------------------------------------- the one way the app writes

def actor_ref(actor) -> tuple[str, str]:
    """(internal id, role). Signed in: the Identity Platform uid. Laptop demo: the reviewer id."""
    if hasattr(actor, "uid"):
        return actor.uid, actor.role
    if isinstance(actor, str) and actor:
        return f"laptop:{actor}", "laptop"
    return "laptop:anonymous", "laptop"


class Recorder:
    """Writes journal entries; a failed write is logged (ids only) and never blocks
    the person's action. Every log point in the review service goes through here."""

    def __init__(self, journal=None):
        self.journal = journal if journal is not None else NullJournal()

    @property
    def on(self) -> bool:
        return getattr(self.journal, "kind", "off") != "off"

    def record(self, actor, action: str, *, session: dict | None = None, record_id: str | None = None,
               student_id: str | None = None, record_type: str | None = None, required: bool = False,
               **details) -> dict | None:
        """required=True: the caller must not go ahead without the entry (downloads,
        RBT views); raises JournalUnavailable if the journal can't take it."""
        from common.safelog import log
        aid, role = actor_ref(actor)
        if session is not None:
            record_id = record_id or session.get("recording_id")
            student_id = student_id or session.get("student_id")
        try:
            e = make_entry(action=action, actor_id=aid, actor_role=role, record_id=record_id,
                           student_id=student_id, record_type=record_type, details=details or None)
            self.journal.append([e])
            return e
        except Exception as exc:  # noqa: BLE001 - journal trouble must not take the review screen down
            log("journal_write_failed", "ERROR", step="journal", error_type=type(exc).__name__,
                reason=action)
            if required and self.on:
                raise JournalUnavailable(action) from exc
            return None


class JournalUnavailable(Exception):
    pass


def audit_object_event_id(name: str) -> str:
    """Deterministic id for a bucket audit record, so the backfill can be re-run safely."""
    return "audit_" + hashlib.sha256(name.encode()).hexdigest()[:32]


# ---------------------------------------------------------------- copy the existing bucket audit trail in

_AUDIT_ACTIONS = {"session_approved", "pdf_downloaded", "classroom_changed", "incident_pdf_downloaded"}


def backfill_from_audit(store, journal, *, student_of=None) -> int:
    """Copy audit/<recording>/*.json (approvals, PDF downloads, classroom moves) into the
    journal. Ids are derived from the object name, so re-running adds nothing twice.
    student_of(recording_id) -> internal student id, best effort."""
    from common.classcodes import class_key

    rows = []
    for name in sorted(store.list("audit/")):
        r = store.read_json(name) or {}
        action, rid = r.get("event"), r.get("recording_id")
        if action not in _AUDIT_ACTIONS or not rid:
            continue
        signed_in = bool(r.get("actor_email"))
        aid = r.get("actor_id") if signed_in else f"laptop:{r.get('actor_id')}"
        role = r.get("actor_role") if signed_in else "laptop"
        details = {}
        if action == "session_approved":
            details["edited"] = bool(r.get("edited"))
        elif action == "pdf_downloaded":
            details.update(pdf_sha256=r.get("pdf_sha256"), pdf_bytes=r.get("pdf_bytes"))
        elif action == "incident_pdf_downloaded":
            details.update(incident_number=r.get("incident_number"),
                           incident_offset_seconds=r.get("incident_offset_seconds"),
                           draft=r.get("draft") if isinstance(r.get("draft"), bool) else None)
        else:
            details.update(from_classroom=class_key(r.get("from_class_code")), to_classroom=class_key(r.get("to_class_code")),
                           from_teacher_id=r.get("from_teacher_id"), to_teacher_id=r.get("to_teacher_id"))
        details["audit_object_sha"] = hashlib.sha256(name.encode()).hexdigest()
        try:
            when = dt.datetime.fromisoformat(r["at"])
        except (KeyError, ValueError):
            continue
        rows.append(make_entry(action=action, actor_id=aid, actor_role=role if role in ROLES else "reviewer",
                               record_id=rid, student_id=student_of(rid) if student_of else None,
                               details=details, occurred_at=when, source="backfill",
                               event_id=audit_object_event_id(name)))
    for i in range(0, len(rows), 500):
        journal.append(rows[i:i + 500])
    return len(rows)
