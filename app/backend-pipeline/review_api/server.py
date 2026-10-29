"""Review service for the teacher review screen (tasks#35).

Two ways to run it:

  hosted (Cloud Run, https://review.example.org): serves the built
  review UI and the API from one container, as its own service account. Every
  API call needs an Identity Platform ID token; the server checks the token,
  that the email is verified, and that it is on the allowlist
  (config/review_allowlist.json). See review_api/auth.py.

  laptop demo (--auth off): bound to 127.0.0.1 behind the Vite dev server's /api
  proxy; the reviewer id comes from the X-Reviewer-Id header. Refused on Cloud Run.

Writes these records, all in the same bucket. The first two are on the same
30-day auto-delete rule as the drafts:
  approvals/<id>.json   who approved, when, and the approved (edited) notes
  audit/<id>/*.json     one record per approval and per PDF download
  incident_names/<id>/<key>.json  AI incident names, one per incident content (create-only; incident_names.py)
  bug_reports/...       teachers' Bug markers (tasks#94): the scrubbed report, then .filed.json once
                        it is a GitHub issue. Create-only, kept to pilot end. See review_api/bug_reports.py.
  people/state-N.json   My profile + admins' role / email changes (tasks#107): one create-only record per
                        change with the whole state (highest number wins). See review_api/people.py.
  school_setup/...      Domains + Classes (tasks#103): one create-only record per change with the whole
                        list (newest wins; seed files in the image). See review_api/school_setup.py.
  planner/...           weekly lesson planner (tasks#83): plans, per-student weekly
                        summaries and teacher notes, one create-only object per save.
                        Kept to pilot end (no auto-delete), removed with
                        infra/delete_planner.py. See review_api/planner.py.

The approved-notes PDF is rendered in memory on each download and streamed to
the browser. It is never stored. The only email the app sends is a classroom material a
teacher asked for, to that teacher's own staff address (materials.py / mailer.py, tasks#99).

  python server.py --auth off                      # laptop demo, bucket via ADC
  python server.py --auth off --local-dir /tmp/ap  # offline, local folder
  python server.py --auth identity --static ../../frontend-web/dist   # what Cloud Run runs
  python server.py --auth emulator --local-dir /tmp/ap --static ...   # tests (Auth emulator)

Endpoints
  GET  /api/auth-config        public: Identity Platform web config (not secret)
  GET  /api/me                 signed in: email, verified, approved?  (no data)
  GET  /api/health             approved users only (hosted)
  GET  /api/sessions
  GET  /api/sessions/<id>/transcript
  GET  /api/sessions/<id>/audit
  GET  /api/sessions/<id>/media/<media_id>  Teacher Notes photo / video bytes (tasks#112; same access as the session)
  POST /api/sessions/<id>/approve     {"analysis": {...}}   -> approval record
  POST /api/sessions/<id>/classroom   {"class_code": "..."} -> session (move logged in audit)
  GET  /api/sessions/<id>/pdf                               -> application/pdf (attachment)
  POST /api/sessions/<id>/incident_print  {"number": 1}     -> one incident's report for the browser's
                               print view (Save as PDF); logged in the audit trail + journal (notes.py)
  GET  /api/sessions/<id>/incident_names                  -> names the incidents that have none yet (first
                               view; review_api/incident_names.py) and returns {"incidents": [...]}
  POST /api/sessions/<id>/bug_report  {"type": "Bug", "note", "offset_seconds", "area"} -> a teacher's
                               Bug marker: filed as a GitHub issue in your-org/your-issues-repo, or saved as
                               pending until the token exists (bug_reports.py, tasks#94)
  GET  /api/planner?class_code=RM12&past=YYYY-MM-DD   weekly lesson planner: past / this / next week (tasks#83)
  POST /api/planner/plan       {"class_code","week","goals":[],"objectives":[],"plan","approve"}
  POST /api/planner/summary    {"student_id","week","summary","iep_check":[{goal_id,status,note}],"approve"}
  POST /api/planner/note       {"student_id","week","note"}
                               planner saves: 422 QUOTES_TRANSCRIPT if 8 words in a row match a transcript
  POST /api/planner/draft/summary {"student_id","week"}  AI draft of a weekly summary (returned, not stored)
  POST /api/planner/draft/plan    {"class_code","week"}  AI draft of a week's goals + objectives (not stored)
                               PLANNER_AI=offline (local stand-in) | live (Gemini) | off; see planner_ai.py
  GET  /api/sessions/<id>/materials   Teacher Notes with up to 3 suggested materials each (AI, names
                               masked; made once per session for a teacher who may act on it), the
                               materials made for the session, and where the email would go (tasks#99)
  POST /api/sessions/<id>/materials   {"note_index","type","count","topic","student_ids","due",
                               "regenerate_of"} -> makes one printable material; emails it to the
                               teacher's own staff address when the admin setting is on (materials.py)
  GET  /api/sessions/<id>/materials/<mid>/pdf[?draft=N]  -> application/pdf, all 3 drafts or one
                               (rendered on demand from the cleaned SVG, never stored; logged)
  POST /api/sessions/<id>/materials/<mid>/resend  -> email it again to the teacher's own address (logged)
  POST /api/sessions/<id>/materials/<mid>/attach  {"week"?, "lesson"?} copy it into the classroom's lesson plan
  GET  /api/planner/materials?class_code=..&weeks=w1,w2,w3  materials attached to each week's lesson plan
  POST /api/planner/materials  {"class_code","week","lesson":{"day","label"},"class_id"?, type, count, topic,
                               style, student_ids, due, regenerate_of}: make one for the lesson plan
  GET  /api/planner/materials/<mid>/pdf?class_code&week[&draft]; POST .../<mid>/(resend|continue|remove)
                               {"class_code","week"} (remove: hidden from the plan, kept in storage; logged)
  POST /api/sessions/<id>/materials/<mid>/continue  photo-style flashcards: make the next picture when the
                               pacing allows (about 2 a minute); finishes and emails the set when all are in
  GET  /api/admin/materials_instruction  the instruction per activity type (teachers read; RBT no)
  POST /api/admin/materials_instruction  admin only: {"key", "instruction": "..."} or {"key", "reset": true}
                               (journaled: settings_changed, setting=materials_instruction)
  GET  /api/admin/materials_email     admin only: email delivery for materials on/off (+ sender state)
  POST /api/admin/materials_email     admin only: {"enabled": true|false} (journaled: settings_changed)
  GET  /api/domains                   Subject / Domain list (educational methodology); everyone signed in (tasks#103)
  POST /api/domains                   admin: {"version", "domain": {name, methodology, methodology_detail, programs, book}}
  POST /api/domains/<id>              admin: edit (same body);  POST /api/domains/<id>/delete  {"version"}
  GET  /api/classes                   every class + lookups (domains, staff, students, classrooms); everyone signed in
  POST /api/classes                   admin: {"version", "class": {name, classroom, teacher_ids, domain_id, student_ids,
                               schedule: [{days: ["Mon"...], start: "HH:MM", minutes}]}}
  POST /api/classes/<id>              admin, or a teacher on that class: edit;  POST /api/classes/<id>/delete (admin)
                               every save names the version it was edited from (409 STALE_VERSION); journaled
  GET  /api/sessions/<id>/class       the session's class (picked, else suggested from its classroom + start time)
  GET  /api/sessions/<id>/speakers    speaker selector: the class's people first, then everyone (first name +
                                      last initial), then generic options; read-only, nothing stored (tasks#104)
  POST /api/sessions/<id>/class       {"class_id": "..."|null}: session teacher or admin; audit trail + journal
  GET  /api/students                  every roster student (all roles; list opens on own classrooms)
  GET  /api/students/<id>?week=Mon     IEP + one school week of daily summaries + weekly summary /
                               draft parent email (journaled: iep_viewed, every time); review_api/students.py
  GET  /api/sessions/<id>/students    students in this session (class roster suggested; tasks#106)
  POST /api/sessions/<id>/students    {"student_ids": [...]}: session teacher or admin; audit trail + journal
  GET  /api/uploads/status            recordings still processing (upload landed, no notes yet; tasks#106)
  GET  /api/students/<id>/parent_email?week=Mon   weekly parent email preview: recipients (guardians with
                                      "receives reports" on), excluded contacts, exact text (tasks#106)
  POST /api/students/<id>/parent_email/send       {"week", "preview_sha256"}: one teacher click sends it
  GET  /api/admin/parent_email        admin only: demo redirect (parent emails go to the teacher instead)
  POST /api/admin/parent_email        admin only: {"redirect": true|false} (journaled: settings_changed)
  GET  /api/teachers                  teachers, RBTs and roles (emails: admins only)
  GET  /api/admin/settings            admin only: the configuration the app runs with (read-only)
  GET  /api/branding                  school name, badge and logo info for the top bar (everyone signed in)
  GET  /api/branding/logo             the cleaned logo (image/png or image/jpeg); 404 if none
  POST /api/admin/branding            admin only: {"school_name": "..."}
  POST /api/admin/branding/logo       admin only: raw PNG / JPEG / SVG body, max 1 MB; SVG is rendered to
                               PNG server-side and discarded, PNG/JPEG re-encoded (review_api/branding.py)
  POST /api/admin/branding/logo/remove  admin only: back to the badge
  POST /api/admin/branding/reset      admin only: default name + badge (Reset demo state)
  GET  /api/admin/journal?from=&to=&actor=&action=&student=&limit=   admin only (tasks#77)
  GET  /api/admin/reports/activity.csv?by=person|student&from=&to=   admin only, download (logged)

Admin activity journal (review_api/journal.py): sign-ins, every list / session /
audit view (an RBT's view is refused if it can't be journaled), approvals, edits,
exports, classroom changes, refused attempts, journal views and report downloads.

Read-only RBTs: every POST is refused with 403 READ_ONLY_ROLE before routing (except their own My profile, tasks#107),
and the PDF export is refused too (notes.py). Parent emails are removed from
every response for anyone who is not that student's teacher or an admin.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_PIPE = Path(__file__).resolve().parents[1]
if str(_PIPE) not in sys.path:
    sys.path.insert(0, str(_PIPE))

from common.safelog import log  # noqa: E402
from review_api.journal import (JournalError, JournalReadFailed, JournalUnavailable, Query,  # noqa: E402
                                Recorder, journal_from_env)
from review_api.journal_reports import (Directory, ReportBlocked, activity_report,  # noqa: E402
                                        journal_rows_for_admin, to_csv)
from review_api.bug_reports import is_bug_marker  # noqa: E402
from review_api.branding import MAX_UPLOAD, Branding  # noqa: E402
from review_api.planner import QUOTE_WORDS, Planner, QuoteRefused  # noqa: E402
from review_api.students import Students  # noqa: E402
from review_api.materials import Materials  # noqa: E402
from review_api.school_setup import SchoolSetup  # noqa: E402
from review_api.speakers import Speakers  # noqa: E402
from review_api.session_students import SessionStudents  # noqa: E402
from review_api.uploads_status import UploadsStatus  # noqa: E402
from review_api.parent_email import ParentEmail  # noqa: E402
from review_api.people import People  # noqa: E402
from review_api.notes import (ApiError, Notes, check_rid, is_read_only, may_see,  # noqa: E402
                              may_see_parent_email, scrub_parent_email)

PROJECT = os.environ.get("PROJECT_ID", "example-project")
BUCKET = os.environ.get("BUCKET", f"{PROJECT}-recordings")
MAX_BODY = 256 * 1024
# Browsers send Origin on POST/CORS requests; only the review UI itself may call us.
LOCAL_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}
IDP_ORIGINS = "https://identitytoolkit.googleapis.com https://securetoken.googleapis.com"


GOOGLE_SCRIPT_ORIGIN = "https://apis.google.com"   # the Firebase SDK's popup helper (Sign in with Google)


def _csp(extra_connect: str = "", *, google_auth_domain: str | None = None) -> str:
    """google_auth_domain: Sign in with Google is on (tasks#108). The popup needs Google's helper script
    and the project's auth handler frame (https://<authDomain>); nothing else is loosened."""
    script = f"'self' {GOOGLE_SCRIPT_ORIGIN}" if google_auth_domain else "'self'"
    frame = f"frame-src https://{google_auth_domain}; " if google_auth_domain else ""
    return (f"default-src 'none'; script-src {script}; {frame}"
            f"connect-src 'self' {IDP_ORIGINS}{(' ' + extra_connect) if extra_connect else ''}; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
            "img-src 'self' data: blob:; media-src 'self' blob:; manifest-src 'self'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'")


def make_handler(notes: Notes, store_label: str, *, auth=None, static_dir: str | None = None,
                 auth_config: dict | None = None, origins: set[str] | None = None, materials=None):
    """auth=None: laptop demo (X-Reviewer-Id). Otherwise a review_api.auth.Authenticator."""
    rec: Recorder = notes.recorder
    planner = Planner(notes)
    students = Students(notes, planner)
    branding = Branding(notes.store, rec)
    mats = materials if materials is not None else Materials(notes)
    setup = SchoolSetup(notes)
    speakers = Speakers(notes, setup)
    if getattr(mats, "plan_classes_hook", None) is None:     # the planner's Make form lists the classroom's classes
        mats.plan_classes_hook = setup.classroom_classes
    session_students = SessionStudents(notes, setup)
    uploads = UploadsStatus(notes)
    parent_email = ParentEmail(notes, planner, sender=getattr(mats, "sender", None))
    profiles = People(notes)                # My profile + admins' role / email changes (tasks#107)
    if getattr(mats, "domain_context", None) is None:    # materials get the class's domain + methodology (tasks#103)
        mats.domain_context = setup.domain_context
    if getattr(mats, "approach_view", None) is None:
        mats.approach_view = setup.approach_view
    people: dict = {}                       # uid -> email, for admin views (never written to the journal)
    sign_ins: set = set()                   # (uid, auth_time) already journaled by this instance
    allowed_origins = set(origins or ()) | LOCAL_ORIGINS
    static_root = Path(static_dir).resolve() if static_dir else None
    emulator = (auth_config or {}).get("emulator")
    google_on = "google.com" in ((auth_config or {}).get("providers") or ())
    csp = _csp(emulator or "", google_auth_domain=(auth_config or {}).get("authDomain") if google_on else None)

    class Handler(BaseHTTPRequestHandler):
        server_version = "review-api"
        notes = None                         # set below; main() starts the pending bug-report filing
        sys_version = ""

        def _send(self, code: int, body, *, ctype="application/json", headers=None) -> None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            headers = dict(headers or {})
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", headers.pop("Cache-Control", "no-store"))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "strict-origin")   # the API key is referrer-restricted
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", csp)
            if auth is not None and not emulator:
                self.send_header("Strict-Transport-Security", "max-age=31536000")
            self.send_header("Content-Length", str(len(data)))
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):  # no request lines on the console (they'd show ids only, but keep it quiet)
            pass

        def _origin_ok(self) -> bool:
            o = self.headers.get("Origin")
            return o is None or o in allowed_origins

        def _actor(self):
            if auth is None:
                return self.headers.get("X-Reviewer-Id")
            who = auth.require(self.headers)             # 401 / 403 unless verified + allowlisted
            people[who.uid] = who.email
            self._who = who
            return who

        def _directory(self) -> Directory:
            known = dict(people)
            for n in notes.store.list("audit/"):        # uids seen on approvals / downloads / moves
                r = notes.store.read_json(n) or {}
                if r.get("actor_id") and r.get("actor_email"):
                    known.setdefault(r["actor_id"], r["actor_email"])
            allow = [{"email": e, "role": r} for e, r in profiles.allowlist().items()]   # + admins' changes
            return Directory(notes.roster(), {"users": allow}, known)

        def _admin(self, actor, params: dict):
            if not (hasattr(actor, "uid") and actor.is_admin):
                raise ApiError(403, "ADMIN_ONLY")
            try:
                return Query.parse(params)
            except JournalError as exc:
                raise ApiError(400, "BAD_QUERY") from exc

        def _static(self, path: str):
            if static_root is None:
                raise ApiError(404, "NOT_FOUND")
            rel = path.lstrip("/") or "index.html"
            f = (static_root / rel).resolve()
            if static_root not in f.parents or not f.is_file():
                f = static_root / "index.html"               # single-page app: every other path -> UI
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            cache = "public, max-age=31536000, immutable" if "/assets/" in f.as_posix() else "no-store"
            self._send(200, f.read_bytes(), ctype=ctype, headers={"Cache-Control": cache})

        def _json_body(self) -> dict:
            if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
                raise ApiError(415, "JSON_REQUIRED")
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                raise ApiError(413 if n > MAX_BODY else 400, "BAD_BODY")
            try:
                body = json.loads(self.rfile.read(n))
            except ValueError as exc:
                raise ApiError(400, "BAD_JSON") from exc
            if not isinstance(body, dict):
                raise ApiError(400, "BAD_JSON")
            return body

        def _raw_body(self, limit: int) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise ApiError(413, "LOGO_TOO_LARGE")
            if n <= 0:
                raise ApiError(400, "BAD_BODY")
            return self.rfile.read(n)

        def _branding(self, method: str, path: str, actor):
            """School name + logo (review_api/branding.py). Reading: anyone signed in. Writing: admins."""
            if method == "GET" and path == "/api/branding":
                return self._send(200, branding.view())
            if method == "GET" and path == "/api/branding/logo":
                data, ctype = branding.logo()
                return self._send(200, data, ctype=ctype, headers={
                    "Cache-Control": "private, max-age=300", "Content-Disposition": "inline"})
            if method != "POST":
                raise ApiError(405, "METHOD_NOT_ALLOWED")
            if path == "/api/admin/branding":
                return self._send(200, branding.set_name(actor, self._json_body().get("school_name")))
            if path == "/api/admin/branding/logo":
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype not in ("image/png", "image/jpeg", "image/svg+xml"):
                    raise ApiError(415, "LOGO_TYPE")
                return self._send(200, branding.set_logo(actor, self._raw_body(MAX_UPLOAD)))
            if path == "/api/admin/branding/logo/remove":
                return self._send(200, branding.remove_logo(actor))
            if path == "/api/admin/branding/reset":
                return self._send(200, branding.reset(actor))
            raise ApiError(404, "NOT_FOUND")

        def _route(self, method: str):
            if not self._origin_ok():
                raise ApiError(403, "BAD_ORIGIN")
            path = self.path.split("?", 1)[0]
            if not path.startswith("/api/"):
                if method != "GET":
                    raise ApiError(405, "METHOD_NOT_ALLOWED")
                return self._static(path)
            if method == "GET" and path == "/api/auth-config":
                if auth is None:
                    raise ApiError(404, "NOT_FOUND")
                return self._send(200, auth_config or {})
            if method == "GET" and path == "/api/me":
                if auth is None:
                    raise ApiError(404, "NOT_FOUND")
                claims, role = auth.identify(self.headers)     # 401 without a valid token
                me = {"email": claims.get("email"), "email_verified": claims.get("email_verified") is True,
                      "approved": role is not None, "role": role}
                if role is not None and me["email_verified"]:
                    who = auth.require(self.headers)
                    self._who = who
                    people[who.uid] = who.email
                    key = (who.uid, claims.get("auth_time"))
                    if key not in sign_ins:
                        sign_ins.add(key)
                        rec.record(who, "sign_in", record_id=who.uid, sign_in_provider=who.provider)
                    # the name at the top right: My profile's display name, else the staff record's name
                    me.update(display_name=profiles.me(who).get("display_name") or None)
                    me.update(read_only=is_read_only(who), home_classrooms=notes.home_classrooms(who),
                              default_classroom=notes.default_classroom(who),
                              classrooms=sorted((notes.roster().get("class_codes") or {}).keys()))
                return self._send(200, me)
            actor = self._actor() if auth is not None else None   # hosted: every data call checked here
            if path == "/api/profile/me":
                # My profile (tasks#107): everyone signed in, RBTs too (their own profile, no student data)
                if auth is None:
                    raise ApiError(404, "NOT_FOUND")
                if method == "GET":
                    return self._send(200, profiles.me(actor))
                return self._send(200, profiles.save_me(actor, self._json_body()))
            if method == "POST" and is_read_only(actor):
                raise ApiError(403, "READ_ONLY_ROLE")            # RBT: no write of any kind
            if path in ("/api/branding", "/api/branding/logo") or path.startswith("/api/admin/branding"):
                return self._branding(method, path, actor if auth is not None else self.headers.get("X-Reviewer-Id"))
            if method == "GET" and path == "/api/health":
                return self._send(200, {"ok": True, "store": store_label})
            if method == "GET" and path == "/api/uploads/status":   # processing rows (tasks#106); polled
                return self._send(200, uploads.view())
            if method == "GET" and path == "/api/sessions":
                rows = notes.list_sessions(actor)
                rec.record(actor, "sessions_listed", required=is_read_only(actor), sessions=len(rows))
                return self._send(200, {"sessions": rows})
            if method == "GET" and path == "/api/admin/journal":
                q = self._admin(actor, self._params())
                d = self._directory()
                rows = journal_rows_for_admin(rec.journal.query(q), d)
                rec.record(actor, "journal_viewed", record_id="journal", required=True, row_count=len(rows),
                           period_from=q.period_from.isoformat(), period_to=q.period_to.isoformat(),
                           filter_action=q.action, filter_actor_id=q.actor_id, filter_student_id=q.student_id)
                return self._send(200, {"entries": rows, "from": q.period_from.isoformat(),
                                        "to": q.period_to.isoformat()})
            if method == "GET" and path == "/api/admin/reports/activity.csv":
                params = self._params()
                q = self._admin(actor, {**params, "limit": str(Query.MAX_LIMIT)})
                by = params.get("by") or "person"
                if by not in ("person", "student"):
                    raise ApiError(400, "BAD_QUERY")
                d = self._directory()
                header, body = activity_report(rec.journal.query(q), by, d)
                try:
                    data = to_csv(header, body, d)
                except ReportBlocked as exc:
                    raise ApiError(500, "REPORT_BLOCKED") from exc
                digest = hashlib.sha256(data).hexdigest()
                period = {"period_from": q.period_from.isoformat(), "period_to": q.period_to.isoformat()}
                # logged like a PDF download (Ops, tasks#108): journal first (required), then the bucket
                # audit record; if either can't be written, the file isn't sent
                rec.record(actor, "report_downloaded", record_id=f"activity_by_{by}", required=True,
                           report=f"activity_by_{by}", group_by=by, row_count=len(body), report_sha256=digest,
                           report_bytes=len(data), **period)
                notes.audit_report_download(actor, f"activity_by_{by}", group_by=by, row_count=len(body),
                                            report_sha256=digest, report_bytes=len(data), **period)
                fname = f"activity-by-{by}_{q.period_from.isoformat()}_{q.period_to.isoformat()}.csv"
                return self._send(200, data, ctype="text/csv; charset=utf-8",
                                  headers={"Content-Disposition": f'attachment; filename="{fname}"'})
            pe = re.match(r"^/api/students/([^/]+)/parent_email(/send)?$", path)
            if pe:                              # weekly parent email (tasks#106): preview, then one click sends
                who = actor if auth is not None else self.headers.get("X-Reviewer-Id")
                if method == "GET" and not pe.group(2):
                    return self._send(200, parent_email.preview(who, pe.group(1), self._params().get("week")))
                if method == "POST" and pe.group(2):
                    return self._send(200, parent_email.send(who, pe.group(1), self._json_body()))
                raise ApiError(405, "METHOD_NOT_ALLOWED")
            if path == "/api/admin/parent_email":   # demo redirect setting (admin only; journaled)
                who = actor if auth is not None else self.headers.get("X-Reviewer-Id")
                if method == "GET":
                    return self._send(200, parent_email.setting(who))
                return self._send(200, parent_email.set_redirect(who, self._json_body()))
            if method == "GET" and path in ("/api/students", "/api/teachers", "/api/admin/settings") \
                    or method == "GET" and path.startswith("/api/students/"):
                who = actor if auth is not None else self.headers.get("X-Reviewer-Id")
                if path == "/api/students":
                    return self._send(200, students.list(who))
                if path == "/api/teachers":
                    return self._send(200, students.teachers(who))
                if path == "/api/admin/settings":
                    return self._send(200, students.settings(who))
                sid = path[len("/api/students/"):]
                return self._send(200, students.detail(who, sid, self._params().get("week")))
            if method == "POST" and path == "/api/admin/people/access":
                # Teachers tab (tasks#107): an admin changes a person's app role / sign-in email
                if auth is None:
                    raise ApiError(404, "NOT_FOUND")
                body = self._json_body()
                return self._send(200, profiles.change_access(actor, body.get("person"), body))
            if path == "/api/admin/materials_instruction":
                who = actor if auth is not None else self.headers.get("X-Reviewer-Id")
                if method == "GET":
                    return self._send(200, mats.instruction_setting(who))
                return self._send(200, mats.set_instruction(who, self._json_body()))
            if path == "/api/admin/materials_email":
                who = actor if auth is not None else self.headers.get("X-Reviewer-Id")
                if method == "GET":
                    return self._send(200, mats.email_setting(who))
                return self._send(200, mats.set_email(who, self._json_body()))
            if path in ("/api/domains", "/api/classes") or path.startswith(("/api/domains/", "/api/classes/")):
                return self._school_setup(method, path, actor if auth is not None
                                          else self.headers.get("X-Reviewer-Id"))
            sp = re.match(r"^/api/sessions/([^/]+)/speakers$", path)
            if sp and method == "GET":         # speaker selector choices (tasks#104); nothing is stored
                rid = check_rid(sp.group(1))
                return self._send(200, speakers.choices(rid, actor if auth is not None
                                                        else self.headers.get("X-Reviewer-Id")))
            ss = re.match(r"^/api/sessions/([^/]+)/students$", path)
            if ss and method in ("GET", "POST"):   # students in this session (tasks#106)
                rid = check_rid(ss.group(1))
                who = actor if auth is not None else self.headers.get("X-Reviewer-Id")
                if method == "GET":
                    return self._send(200, session_students.view(rid, who))
                return self._send(200, session_students.save(rid, who, self._json_body()))
            sc = re.match(r"^/api/sessions/([^/]+)/class$", path)
            if sc:
                rid = check_rid(sc.group(1))
                who = actor if auth is not None else self.headers.get("X-Reviewer-Id")
                if method == "GET":
                    return self._send(200, setup.session_class(rid, who))
                return self._send(200, setup.assign(rid, who, self._json_body()))
            mm = re.match(r"^/api/sessions/([^/]+)/materials(?:/([^/]+)/(pdf|resend|continue|attach))?$", path)
            if mm:
                rid, mid, what = check_rid(mm.group(1)), mm.group(2), mm.group(3)
                who = actor if auth is not None else self.headers.get("X-Reviewer-Id")
                if what is None and method == "GET":
                    return self._send(200, mats.view(rid, who))
                if what is None and method == "POST":
                    return self._send(200, mats.make(rid, who, self._json_body()))
                if what == "pdf" and method == "GET":
                    data, fname = mats.pdf(rid, mid, who, self._params().get("draft"))
                    return self._send(200, data, ctype="application/pdf",
                                      headers={"Content-Disposition": f'attachment; filename="{fname}"'})
                if what == "resend" and method == "POST":
                    return self._send(200, mats.resend(rid, mid, who))
                if what == "continue" and method == "POST":
                    return self._send(200, mats.continue_(rid, mid, who))
                if what == "attach" and method == "POST":
                    return self._send(200, mats.attach(rid, mid, who, self._json_body()))
                raise ApiError(405, "METHOD_NOT_ALLOWED")
            pm = re.match(r"^/api/planner/materials(?:/([^/]+)/(pdf|resend|continue|remove))?$", path)
            if pm:                                       # lesson plan materials (tasks#99): materials.py
                who = actor if auth is not None else self.headers.get("X-Reviewer-Id")
                mid, what = pm.group(1), pm.group(2)
                if what is None and method == "GET":
                    p = self._params()
                    return self._send(200, mats.plan_view(who, p.get("class_code"), p.get("weeks")))
                if what is None and method == "POST":
                    return self._send(200, mats.plan_make(who, self._json_body()))
                if what == "pdf" and method == "GET":
                    p = self._params()
                    data, fname = mats.plan_action(who, mid, "pdf", p, p.get("draft"))
                    return self._send(200, data, ctype="application/pdf",
                                      headers={"Content-Disposition": f'attachment; filename="{fname}"'})
                if what in ("resend", "continue", "remove") and method == "POST":
                    return self._send(200, mats.plan_action(who, mid, what, self._json_body()))
                raise ApiError(405, "METHOD_NOT_ALLOWED")
            if path == "/api/planner" or path.startswith("/api/planner/"):
                return self._planner(method, path, actor if auth is not None
                                     else self.headers.get("X-Reviewer-Id"))
            md = re.match(r"^/api/sessions/([^/]+)/media/(m[0-9a-f]{12})$", path)
            if md and method == "GET":   # Teacher Notes photo / video (tasks#112): whoever may see the session
                rid = check_rid(md.group(1))
                if auth is not None and not may_see(actor, notes.context(rid)):
                    raise ApiError(404, "NOT_FOUND")
                data, ctype = notes.media_bytes(rid, md.group(2))
                rec.record(actor, "media_viewed", session={"recording_id": rid}, required=is_read_only(actor))
                return self._send(200, data, ctype=ctype, headers={"Cache-Control": "private, no-store"})
            m = re.match(r"^/api/sessions/([^/]+)/(transcript|audit|approve|pdf|classroom|incident_print|bug_report|incident_names)$", path)
            if not m:
                raise ApiError(404, "NOT_FOUND")
            rid, what = check_rid(m.group(1)), m.group(2)
            ctx = None
            if auth is not None and what in ("transcript", "audit", "incident_names"):
                ctx = notes.context(rid)                         # 404 for unknown ids
                if not may_see(actor, ctx):
                    raise ApiError(404, "NOT_FOUND")
            if method == "GET" and what == "transcript":
                t = notes.store.read_json(f"transcripts/{rid}.json")
                if t is None:
                    raise ApiError(404, "NOT_FOUND")
                rec.record(actor, "session_viewed", session={"recording_id": rid, **(ctx or {})},
                           required=is_read_only(actor))
                body = {"recording_id": rid, "speaker_turns": t.get("speaker_turns", []),
                        "markers": [m for m in t.get("markers", []) if not is_bug_marker(m)],   # never shown
                        "_meta": t.get("_meta", {})}
                return self._send(200, body if ctx is None or may_see_parent_email(actor, ctx)
                                  else scrub_parent_email(body))
            if method == "GET" and what == "incident_names":
                # first view names the incidents (AI, scrubbed, observed behavior only; incident_names.py)
                return self._send(200, notes.incident_names(rid, actor))
            if method == "GET" and what == "audit":
                body = {"recording_id": rid, "events": notes.audit_trail(rid)}
                rec.record(actor, "audit_viewed", session={"recording_id": rid, **(ctx or {})},
                           required=is_read_only(actor))
                return self._send(200, body if ctx is None or may_see_parent_email(actor, ctx)
                                  else scrub_parent_email(body))
            if method == "POST" and what == "classroom":
                body = self._json_body()
                return self._send(200, notes.change_classroom(
                    rid, actor if auth is not None else self._actor(), body.get("class_code")))
            if method == "POST" and what == "approve":
                body = self._json_body()
                approval = notes.approve(rid, actor if auth is not None else self._actor(), body.get("analysis"))
                return self._send(200, {"recording_id": rid, "status": "approved",
                                        "approved_at": approval["approved_at"],
                                        "approved_by_name": approval["approved_by_name"]})
            if method == "POST" and what == "incident_print":
                body = self._json_body()
                return self._send(200, notes.incident_print(
                    rid, actor if auth is not None else self._actor(), body.get("number")))
            if method == "POST" and what == "bug_report":
                body = self._json_body()
                return self._send(200, notes.bug_report(rid, actor if auth is not None else self._actor(), body))
            if method == "GET" and what == "pdf":
                data, fname = notes.pdf(rid, actor if auth is not None else self._actor())
                return self._send(200, data, ctype="application/pdf",
                                  headers={"Content-Disposition": f'attachment; filename="{fname}"'})
            raise ApiError(405, "METHOD_NOT_ALLOWED")

        def _school_setup(self, method: str, path: str, actor):
            """Domains + Classes (tasks#103): review_api/school_setup.py. Reading: everyone signed in.
            Writing: admins (domains; add / remove classes), a teacher on their own class (edit)."""
            if method == "GET" and path == "/api/domains":
                return self._send(200, setup.domains_view(actor))
            if method == "GET" and path == "/api/classes":
                return self._send(200, setup.classes_view(actor))
            if method != "POST":
                raise ApiError(405, "METHOD_NOT_ALLOWED")
            m = re.match(r"^/api/(domains|classes)(?:/([a-z0-9][a-z0-9\-]{0,63})(/delete)?)?$", path)
            if not m:
                raise ApiError(404, "NOT_FOUND")
            kind, rid, delete = m.group(1), m.group(2), m.group(3)
            body = self._json_body()
            if kind == "domains":
                if rid is None:
                    return self._send(200, setup.add_domain(actor, body))
                return self._send(200, (setup.remove_domain if delete else setup.edit_domain)(actor, rid, body))
            if rid is None:
                return self._send(200, setup.add_class(actor, body))
            return self._send(200, (setup.remove_class if delete else setup.edit_class)(actor, rid, body))

        def _planner(self, method: str, path: str, actor):
            """Weekly lesson planner (tasks#83): review_api/planner.py."""
            if method == "GET" and path == "/api/planner":
                p = self._params()
                return self._send(200, planner.view(actor, p.get("class_code"), p.get("past")))
            save = {"/api/planner/plan": planner.save_plan, "/api/planner/summary": planner.save_summary,
                    "/api/planner/note": planner.save_note,
                    "/api/planner/draft/summary": planner.draft_summary,       # AI draft, not stored
                    "/api/planner/draft/plan": planner.draft_plan}.get(path)
            if save is None:
                raise ApiError(404, "NOT_FOUND")
            if method != "POST":
                raise ApiError(405, "METHOD_NOT_ALLOWED")
            body = self._json_body()
            try:
                return self._send(200, save(actor, body))
            except QuoteRefused as e:
                # the 8 words the person just typed, so they can find and rephrase them
                log("request_rejected", "WARNING", step="planner", error_code=e.code, http_status=e.http)
                return self._send(e.http, {"error": e.code, "words": QUOTE_WORDS, "match": e.run})

        def _params(self) -> dict:
            from urllib.parse import parse_qs, urlsplit
            return {k: v[-1] for k, v in parse_qs(urlsplit(self.path).query).items()}

        def _handle(self, method: str):
            self._who = None
            try:
                self._route(method)
            except ApiError as e:
                log("request_rejected", "WARNING", step="review", error_code=e.code, http_status=e.http)
                if e.http == 403 and self._who is not None:      # a signed-in person was refused: journal it
                    m = re.match(r"^/api/(?:sessions/([^/]+)/)?([a-z_]+)", self.path.split("?", 1)[0])
                    rid = m.group(1) if m and m.group(1) and m.group(1).startswith("rec_") else None
                    rec.record(self._who, "request_refused", record_id=rid,
                               record_type="session" if rid else None,
                               error_code=e.code, attempted=(m.group(2) if m else None))
                self._send(e.http, {"error": e.code})
            except JournalReadFailed as e:
                log("journal_read_failed", "ERROR", step="journal", error_code=e.reason,
                    reason=e.google_reason, http_status=e.http_status)
                self._send(503, {"error": "JOURNAL_READ_FAILED", "reason": e.reason})
            except JournalUnavailable:
                log("journal_unavailable", "ERROR", step="journal")
                self._send(503, {"error": "JOURNAL_UNAVAILABLE"})
            except Exception as exc:  # noqa: BLE001 - never echo messages (could carry content)
                code = getattr(exc, "code", None)
                errs = getattr(exc, "errors", None)
                log("unhandled_error", "ERROR", step="review", error_type=type(exc).__name__,
                    http_status=code if isinstance(code, int) else None,
                    reason=errs[0].get("reason") if isinstance(errs, list) and errs and isinstance(errs[0], dict) else None)
                self._send(502, {"error": "INTERNAL"})

        def do_GET(self):  # noqa: N802
            self._handle("GET")

        def do_POST(self):  # noqa: N802
            self._handle("POST")

    Handler.notes = notes
    return Handler


def build_server(store, port: int, label: str, *, host: str = "127.0.0.1", journal=None,
                 materials_options: dict | None = None, **kw) -> ThreadingHTTPServer:
    notes = Notes(store, recorder=Recorder(journal if journal is not None else journal_from_env()))
    mats = Materials(notes, **(materials_options or {}))
    return ThreadingHTTPServer((host, port), make_handler(notes, label, materials=mats, **kw))


def build_auth(store, mode: str, project: str, providers: tuple[str, ...] | None = None):
    from review_api.auth import Allowlist, Authenticator, Roster, TokenVerifier
    return Authenticator(TokenVerifier(project, emulator=(mode == "emulator")), Allowlist(store), Roster(store),
                         providers=providers)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local-dir", help="use a local folder (bucket layout) instead of the cloud bucket")
    ap.add_argument("--gcloud-cli", action="store_true",
                    help="reach the bucket through the gcloud CLI (your gcloud login) instead of the "
                         "application-default login")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT") or os.environ.get("REVIEW_API_PORT", "8787")))
    ap.add_argument("--host", default="0.0.0.0" if os.environ.get("K_SERVICE") else "127.0.0.1")
    ap.add_argument("--auth", choices=["identity", "emulator", "off"],
                    default=os.environ.get("REVIEW_AUTH") or ("identity" if os.environ.get("K_SERVICE") else "off"),
                    help="identity: Identity Platform ID tokens + allowlist (hosted); "
                         "emulator: Firebase Auth emulator (tests); off: laptop demo")
    ap.add_argument("--static", default=os.environ.get("REVIEW_STATIC_DIR"), help="built UI to serve (vite build output)")
    a = ap.parse_args()
    if a.auth == "off" and (os.environ.get("K_SERVICE") or a.host != "127.0.0.1"):
        raise SystemExit("--auth off is for the laptop demo on 127.0.0.1 only")
    if a.local_dir:
        from review_api.store import LocalStore
        store, label = LocalStore(a.local_dir), f"local:{a.local_dir}"
    elif a.gcloud_cli:
        from review_api.store import GcloudCliStore
        store, label = GcloudCliStore(BUCKET, PROJECT), f"gs://{BUCKET} (gcloud CLI)"
    else:
        from review_api.store import GcsStore
        store, label = GcsStore(BUCKET, PROJECT), f"gs://{BUCKET}"
    kw = {}
    if a.auth != "off":
        emu = os.environ.get("FIREBASE_AUTH_EMULATOR_HOST") if a.auth == "emulator" else None
        if a.auth == "emulator" and not emu:
            raise SystemExit("--auth emulator needs FIREBASE_AUTH_EMULATOR_HOST")
        from review_api.auth import sign_in_providers
        providers = sign_in_providers()       # IDP_PROVIDERS: "password" or "password,google.com" (tasks#108)
        kw = {"auth": build_auth(store, a.auth, PROJECT, providers), "static_dir": a.static,
              "origins": {o.strip() for o in os.environ.get("REVIEW_ALLOWED_ORIGINS", "").split(",") if o.strip()},
              "auth_config": {
                  "projectId": PROJECT,
                  "apiKey": os.environ.get("IDP_API_KEY", "demo-key" if emu else ""),
                  "authDomain": os.environ.get("IDP_AUTH_DOMAIN", f"{PROJECT}.firebaseapp.com"),
                  "providers": list(providers),       # the server refuses any other provider's token
                  "emulator": f"http://{emu}" if emu else None}}
    srv = build_server(store, a.port, label, host=a.host, **kw)
    # Bug markers saved while the GitHub token was missing: file them now if it's there (tasks#94)
    import threading
    threading.Thread(target=srv.RequestHandlerClass.notes.bugs.file_pending, daemon=True).start()
    print(f"review service on http://{a.host}:{a.port}  ({label}; auth={a.auth}; PDFs rendered on demand, never stored)",
          flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
