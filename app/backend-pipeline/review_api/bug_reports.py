"""Bug markers: a teacher reports a problem with the review site (David, Oct 7 2026; tasks#94).

A teacher (or an admin) picks the marker type "Bug" on a session, types a note and
submits it. The review service files it as an issue in your-org/your-issues-repo, labelled
`bug` and `teacher-feedback`, on the org's Team Board (Project 1) with Project =
"Goalvera".

What the issue may contain (Chief of Staff, Oct 7), and nothing else:
  * the teacher's note, with every roster student first or last name replaced by
    "[student]" (staff names by "[staff]", emails by "[email]", school ids / dates of
    birth by "[removed]", long numbers by "[number]") before it is stored or filed
  * the time in the recording, the screen / part of the page they were on
  * the app's revision (and commit), the recorder firmware version if the recording has one
  * the internal recording id and the reporter's role (no name, no email)
Never: student names, transcript text (a note that repeats 8+ words of the transcript is
refused with QUOTES_TRANSCRIPT) or audio, teacher names or emails, school ids.
check_issue() is the last line of defence: if a forbidden value is still in the title
or body, nothing is filed (BugReportBlocked).

Bug markers are not session markers: they never reach the incident list or its
numbering, the session PDF or the incident print view. The session shows them only as
a small "Bug reported" item (with the issue link once filed), to the teacher who
reported it and to admins.

Storage: the same CMEK bucket, create-only, prefix bug_reports/ (no auto-delete; removed
with the rest of the pilot data):
  bug_reports/<recording>/<report>.json            the report (already scrubbed): pending
  bug_reports/<recording>/<report>.claim-<t>.json  one instance is filing it (10-minute window)
  bug_reports/<recording>/<report>.filed.json      filed: issue number + link
A report without a .filed.json is pending.

GitHub access: a fine-grained token in Secret Manager ($GITHUB_ISSUES_SECRET, normally
github-issues-token), read at run time by the review service account. Never the PSA
token, never anyone's gh login. Until the secret has a version, reports stay pending and
the teacher still sees "Thanks, sent to our team."; pending reports are filed on the next
submission once the token is there, and on every instance start.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from common.namematch import name_pattern
from common.safelog import log

from .incident_print import _OTHER, ET, _parse as _parse_time, clock, clock_at, recording_start
from .store import Conflict

BUG_TYPE = "Bug"
REVIEW_MARKER_TYPES = (BUG_TYPE,)       # the types a teacher can add on the review site
HIDDEN_MARKER_TYPES = frozenset({BUG_TYPE})   # never in the markers line, incidents or any PDF
THANKS = "Thanks, sent to our team."
# Recorder Bug markers (David, Oct 8): the device's marker list gains "Bug"; a recording carries
# a marker of type "bug" at an offset. Handled like a review-site Bug report, with no teacher note.
DEVICE_NOTE = "Reported from recorder"
DEVICE_SCREEN = "recorder"
DEVICE_ROLE = "recorder"


def is_bug_marker(m) -> bool:
    return isinstance(m, dict) and str(m.get("type") or "").strip().lower() == "bug"
STUDENT, STAFF = "[student]", "[staff]"
MAX_NOTE = 1500
PREFIX = "bug_reports/"
SCREEN = "Session detail (review site)"
AREAS = {
    "recorder": "Recorder (marker button)",
    "transcript": "Transcript", "timeline": "Speaker timeline", "markers": "Markers",
    "iep_goals": "IEP goal evaluations", "incidents": "ABC incident analysis",
    "incident_pdf": "Incident Download PDF", "summary": "Session summary",
    "parent_email": "Draft parent email", "approve": "Approve", "session_pdf": "Session Download PDF",
    "classroom": "Classroom", "other": "Somewhere else on this screen",
}
LABELS = {"bug": ("d73a4a", "Something isn't working"),
          "teacher-feedback": ("0e8a16", "Reported by a teacher from the Goalvera review site")}
SYSTEM = SimpleNamespace(uid="system:bug_reports", role="system", is_admin=False, email=None)


_REPORT_ID = re.compile(r"^bug_\d{8}T\d{6}_[0-9a-f]{6}$")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_LONG_NUMBER = re.compile(r"(?<![\w:.])\d{6,}(?![\w:])")
_STUDENT_KEY = re.compile(r"(^|_)(first_?name|last_?name|given_?name|family_?name|surname|middle_?name|"
                          r"preferred_?name|nick_?name|full_?name)$", re.I)
_STAFF_KEY = re.compile(r"(^|_)(teacher_?name|name|full_?name|first_?name|last_?name|display_?name|preferred_?name)$", re.I)
_STOP = {"demo", "teacher", "teachers", "rbt", "staff", "admin", "the", "and", "student", "sample", "class",
         "room", "speech", "reading", "art", "gym", "mr", "mrs", "ms", "dr"}

# Generic placeholders that stand in for a person but are not a name (tasks#94, Oct 10): a value made
# only of these (plus numbers / single letters), e.g. "Student", "Student 2", "Demo Teacher", "the
# student", "Unknown", is never treated as a name. A value with any other word ("Pat Speech",
# "Student Marcus") is still a name, and the other words are still caught on their own.
_PLACEHOLDER = {"student", "students", "teacher", "teachers", "staff", "rbt", "rbts", "admin", "demo",
                "sample", "the", "a", "an", "unknown", "anonymous", "child", "kid", "peer", "parent",
                "guardian", "class", "classroom", "room", "none", "n/a", "na", "tbd", "test", "name",
                "first", "last", "sub", "substitute", "aide", "para", "paraprofessional", "learner"}


def is_placeholder(value: str) -> bool:
    parts = [p.strip(".()'\"#:-") for p in re.split(r"[\s,_]+", str(value or "")) if p.strip(".()'\"#:-")]
    return all(p.lower() in _PLACEHOLDER or p.isdigit() or len(p) == 1 for p in parts)


class BugReportBlocked(Exception):
    """A forbidden value is still in the issue: nothing is filed."""


# ---------------------------------------------------------------- what must never be in an issue

def _tokens(value: str, *, staff: bool) -> set[str]:
    v = " ".join(str(value or "").split())
    out = set()
    if is_placeholder(v):
        return out
    if v.strip(".") and re.search(r"[A-Za-z]", v):
        out.add(v)
    for part in re.split(r"[\s,]+", v):
        part = part.strip(".()'\"")
        # every name token, whatever its length ("Li" in "Shang Li"): short ones are matched only as
        # capitalized whole words (common/namematch.py), so they don't over-mask ordinary words
        if not part or not part[:1].isalpha():
            continue
        if staff and part.lower() in _STOP:
            continue
        if part.lower() in _PLACEHOLDER:
            continue
        out.add(part)
    return out


def never_values(roster: dict, *sessions: dict) -> dict:
    """{"student": names, "staff": names, "other": school ids / dates of birth}, longest first.
    Students: every name-like field in roster.class_codes / roster.students and the session.
    Staff: roster teachers', RBTs' and other staff's names (and the session's teacher name)."""
    student, staff, other = set(), set(), set()

    def walk(o, kind):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, (dict, list)):
                    walk(v, kind)
                    continue
                if not isinstance(v, str) or not v.strip():
                    if isinstance(v, int) and not isinstance(v, bool) and _OTHER.search(str(k)):
                        other.add(str(v))
                    continue
                if _OTHER.search(str(k)):
                    other.add(v.strip())
                elif str(k).lower().startswith("teacher") and _STAFF_KEY.search(str(k)):
                    staff.update(_tokens(v, staff=True))
                elif kind == "staff" and _STAFF_KEY.search(str(k)):
                    staff.update(_tokens(v, staff=True))
                elif kind == "student" and (_STUDENT_KEY.search(str(k)) or str(k).lower() == "name"
                                            or (str(k).startswith("student_") and "name" in str(k))):
                    student.update(_tokens(v, staff=False))
        elif isinstance(o, list):
            for x in o:
                walk(x, kind)

    walk((roster or {}).get("class_codes") or {}, "student")
    walk((roster or {}).get("students") or {}, "student")
    walk((roster or {}).get("teachers") or {}, "staff")
    walk((roster or {}).get("rbts") or {}, "staff")
    walk((roster or {}).get("staff") or {}, "staff")      # BCBA, therapists, office (Oct 10 roster)
    for s in sessions:
        walk(s or {}, "student")
    staff -= student
    order = lambda xs: sorted(xs, key=lambda x: (-len(x), x))  # noqa: E731
    return {"student": order(student), "staff": order(staff), "other": order(x for x in other if len(x) >= 2)}


def _word(v: str, *, cased: bool = False, possessive: bool = False) -> re.Pattern:
    """Roster name matcher (common/namematch.py): 3+ letters any case; 1-2 letters capitalized whole words."""
    return name_pattern(v, cased=cased, possessive=possessive) or re.compile(r"(?!x)x")


def scrub_note(text: str, never: dict) -> str:
    out = str(text or "")
    out = _EMAIL.sub("[email]", out)
    for v in never["other"]:
        out = re.sub(re.escape(v), "[removed]", out, flags=re.I)
    for v in never["student"]:
        out = _word(v).sub(STUDENT, out)
    for v in never["staff"]:
        out = _word(v).sub(STAFF, out)
    out = _LONG_NUMBER.sub("[number]", out)
    return out


def check_issue(title: str, body: str, never: dict) -> None:
    blob = f"{title}\n{body}"
    if _EMAIL.search(blob):
        raise BugReportBlocked("email")
    for v in never["other"]:
        if v.lower() in blob.lower():
            raise BugReportBlocked("school id / dob")
    for v in (*never["student"], *never["staff"]):
        if _word(v).search(blob):
            raise BugReportBlocked("name")


# ---------------------------------------------------------------- the issue

def _fence(text: str) -> str:
    """The note as a plain-text block: no markdown, links, images or @mentions."""
    t = text.replace("```", "'''").replace("\r", "")
    return "```text\n" + t + "\n```"


def build_issue(report: dict) -> tuple[str, str]:
    note = report["note"]
    first = " ".join(note.split())
    snippet = first if len(first) <= 70 else first[:67].rstrip() + "..."
    title = f"Teacher bug report: {snippet}".replace("@", "(at)")
    app = report.get("app_revision") or "unknown"
    if report.get("app_commit"):
        app += f" (commit {report['app_commit']})"
    device = report.get("source") == DEVICE_ROLE
    if device:
        title = f"Recorder bug report: {DEVICE_NOTE} at {clock(report.get('offset_seconds'))}"
    device_rows = [
        ("Recorder", f"`{report.get('device_id')}`" if report.get("device_id") else "not recorded"),
        ("Recording started", _started(report.get("recording_start"))),
        ("Bug pressed at", f"{report['marker_clock']} Eastern" if report.get("marker_clock") else "clock time not available"),
    ] if device else []
    rows = [
        ("Time in recording", clock(report["offset_seconds"]) if report.get("offset_seconds") is not None
         else "not given"),
        ("Screen", DEVICE_SCREEN if device else f"{SCREEN} \u2192 {AREAS.get(report.get('area'), AREAS['other'])}"),
        ("App", f"review service, revision {app}"),
        *device_rows,
        ("Recorder firmware", report.get("firmware_version") or "not recorded for this recording"),
        ("Recording", f"`{report['recording_id']}`"),
        ("Session status", "draft (not yet approved)" if report.get("draft") else "approved"),
        ("Reported by", "the recorder's Bug marker (no name or email)" if device
         else f"role: {report.get('reporter_role') or 'unknown'} (no name or email)"),
        ("Reported at", report["created_at"]),
        ("Report id", f"`{report['report_id']}`"),
    ]
    body = "\n".join([
        "**Recorder bug report**: a teacher pressed Bug on a Goalvera recorder (filed automatically)."
        if device else "**Teacher bug report** from the Goalvera review site (filed automatically).",
        "",
        "Note:" if device else "Note from the teacher:",
        "",
        _fence(note),
        "",
        "| | |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in rows],
        "",
        "_Student names in the note were replaced with [student] before filing. Bug reports never carry "
        "student or teacher names, emails, school IDs, transcript text or audio. Who reported it is in the "
        "admin activity journal (action `bug_reported`, same report id)._",
    ])
    return title, body


# ---------------------------------------------------------------- GitHub

class GitHubError(Exception):
    def __init__(self, what: str, status: int | None = None):
        super().__init__(what)
        self.what, self.status = what, status


class GitHub:
    """Issues + org Projects (v2) through the REST and GraphQL APIs, with one fine-grained
    token. Never logs the token or any response body."""
    API = "https://api.github.com"

    def __init__(self, token: str, *, repo: str, project_owner: str, project_number: int,
                 project_field: str, project_value: str, opener=None, timeout: float = 10.0):
        self._token = token
        self.repo, self.owner, self.number = repo, project_owner, int(project_number)
        self.field, self.value = project_field, project_value
        self._open = opener or urllib.request.urlopen
        self.timeout = timeout
        self._labels_ok = False
        self._project = None

    def _req(self, method: str, path: str, body=None, *, ok=(200, 201)):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.API + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "goalvera-review",
            **({"Content-Type": "application/json"} if data is not None else {})})
        try:
            with self._open(req, timeout=self.timeout) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, b""
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise GitHubError(f"{method} {path.split('?')[0]}: {type(e).__name__}") from None
        if status not in ok:
            raise GitHubError(f"{method} {path.split('?')[0]}", status)
        return status, (json.loads(raw) if raw else {})

    def _graphql(self, query: str, variables: dict) -> dict:
        _, out = self._req("POST", "/graphql", {"query": query, "variables": variables}, ok=(200,))
        if out.get("errors"):
            raise GitHubError("graphql: " + ",".join(str(e.get("type") or "error") for e in out["errors"]))
        return out.get("data") or {}

    def ensure_labels(self) -> None:
        if self._labels_ok:
            return
        for name, (color, desc) in LABELS.items():
            st, _ = self._req("GET", f"/repos/{self.repo}/labels/{name}", ok=(200, 404))
            if st == 404:
                self._req("POST", f"/repos/{self.repo}/labels",
                          {"name": name, "color": color, "description": desc}, ok=(201, 422))
        self._labels_ok = True

    def create_issue(self, title: str, body: str) -> dict:
        self.ensure_labels()
        _, out = self._req("POST", f"/repos/{self.repo}/issues",
                           {"title": title, "body": body, "labels": list(LABELS)}, ok=(201,))
        return {"number": out["number"], "url": out["html_url"], "node_id": out["node_id"]}

    def _project_ids(self) -> tuple[str, str, str]:
        if self._project is None:
            q = """query($login:String!,$n:Int!,$f:String!){organization(login:$login){projectV2(number:$n){id
                   field(name:$f){... on ProjectV2SingleSelectField{id options{id name}}}}}}"""
            p = ((self._graphql(q, {"login": self.owner, "n": self.number, "f": self.field})
                  .get("organization") or {}).get("projectV2") or {})
            f = p.get("field") or {}
            opt = [o["id"] for o in f.get("options") or [] if o.get("name") == self.value]
            if not (p.get("id") and f.get("id") and opt):
                raise GitHubError("project field / option not found")
            self._project = (p["id"], f["id"], opt[0])
        return self._project

    def add_to_project(self, node_id: str) -> None:
        pid, fid, oid = self._project_ids()
        item = self._graphql("mutation($p:ID!,$c:ID!){addProjectV2ItemById(input:{projectId:$p,contentId:$c}){item{id}}}",
                             {"p": pid, "c": node_id})["addProjectV2ItemById"]["item"]["id"]
        self._graphql("mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){updateProjectV2ItemFieldValue(input:{projectId:$p,"
                      "itemId:$i,fieldId:$f,value:{singleSelectOptionId:$o}}){projectV2Item{id}}}",
                      {"p": pid, "i": item, "f": fid, "o": oid})


def github_from_env(token: str) -> GitHub:
    return GitHub(token, repo=os.environ.get("GITHUB_ISSUES_REPO", "your-org/your-issues-repo"),
                  project_owner=os.environ.get("GITHUB_PROJECT_OWNER", "your-org"),
                  project_number=int(os.environ.get("GITHUB_PROJECT_NUMBER", "1")),
                  project_field=os.environ.get("GITHUB_PROJECT_FIELD", "Project"),
                  project_value=os.environ.get("GITHUB_PROJECT_VALUE", "Goalvera"))


class SecretToken:
    """The GitHub token from Secret Manager (latest version), read with the service's own
    identity. None while the secret or its first version doesn't exist (or isn't readable).
    Re-checked at most every `recheck` seconds while missing; cached `ttl` once found."""

    def __init__(self, project: str, secret: str, *, fetch=None, recheck: float = 60, ttl: float = 900,
                 now=time.monotonic):
        self.project, self.secret = project, secret
        self._fetch = fetch or self._fetch_secret
        self.recheck, self.ttl, self._now = recheck, ttl, now
        self._value, self._at, self._lock = None, None, threading.Lock()
        self.state = "not checked"

    def _fetch_secret(self) -> str | None:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        url = (f"https://secretmanager.googleapis.com/v1/projects/{self.project}/secrets/"
               f"{self.secret}/versions/latest:access")
        r = AuthorizedSession(creds).get(url, timeout=10)
        if r.status_code in (400, 403, 404):        # no secret / no version yet / no access yet
            self.state = f"missing ({r.status_code})"
            return None
        r.raise_for_status()
        self.state = "present"
        return base64.b64decode(r.json()["payload"]["data"]).decode().strip() or None

    def get(self) -> str | None:
        with self._lock:
            age = None if self._at is None else self._now() - self._at
            if age is None or age >= (self.ttl if self._value else self.recheck):
                try:
                    self._value = self._fetch()
                except Exception as exc:  # noqa: BLE001 - never echo (could carry the token)
                    self.state = f"error ({type(exc).__name__})"
                    self._value = None
                self._at = self._now()
            return self._value


def token_source_from_env():
    secret = os.environ.get("GITHUB_ISSUES_SECRET", "").strip()
    if not secret:
        return None                                   # laptop / tests: reports stay pending
    return SecretToken(os.environ.get("PROJECT_ID", "example-project"), secret)


def build_info() -> dict:
    """The app's version for the issue: Cloud Run's revision name + the commit deploy_review.sh
    wrote into build_info.json (if any)."""
    info = {"revision": os.environ.get("K_REVISION") or "local"}
    p = Path(__file__).with_name("build_info.json")
    try:
        commit = json.loads(p.read_text()).get("commit")
        if isinstance(commit, str) and re.match(r"^[0-9a-f]{7,40}(-dirty)?$", commit):
            info["commit"] = commit
    except (OSError, ValueError):
        pass
    return info


# ---------------------------------------------------------------- the reports

class BugReports:
    def __init__(self, notes, *, token_source="env", github_factory=None, info=None, now=None):
        self.notes = notes
        self.tokens = token_source_from_env() if token_source == "env" else token_source
        self.github_factory = github_factory or github_from_env
        self.info = info if info is not None else build_info()
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._gh, self._gh_token = None, None

    # ------------------------------------------------------------ reading
    def index(self) -> dict:
        """recording id -> its object names under bug_reports/ (one listing for the whole list)."""
        out: dict = {}
        for n in self._list(PREFIX):
            parts = n.split("/")
            if len(parts) == 3:
                out.setdefault(parts[1], []).append(n)
        return out

    def _list(self, prefix: str) -> list[str]:
        """A listing the service may not be allowed yet (bucket access for bug_reports/ comes
        with infra/setup_bug_reports.sh): the sessions still load, just without bug items."""
        try:
            return self.notes.store.list(prefix)
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ in ("Forbidden", "PermissionError", "PermissionDenied"):
                log("bug_reports_unreadable", "WARNING", step="review", error_type=type(exc).__name__)
                return []
            raise

    def _reports(self, names: list[str]) -> list[tuple[dict, dict | None]]:
        filed = {n[:-len(".filed.json")] for n in names if n.endswith(".filed.json")}
        out = []
        for n in sorted(names):
            base = n[:-len(".json")]
            if not n.endswith(".json") or not _REPORT_ID.match(base.split("/")[-1]):
                continue
            r = self.notes.store.read_json(n) or {}
            f = self.notes.store.read_json(base + ".filed.json") if base in filed else None
            out.append((r, f))
        return out

    def visible(self, rid: str, viewer, names: list[str] | None = None, *, session: dict | None = None) -> list[dict]:
        """The session's "Bug reported" items: only the reporter's own, or all for an admin
        (laptop demo: all). A recorder Bug marker has no teacher behind it: it shows to whoever
        may edit the session (its teacher, the student's teacher) and admins. Never the note,
        never who reported it."""
        from .notes import actor_id, is_read_only, may_act, signed_in
        if viewer is not None and is_read_only(viewer):
            return []
        if names is None:
            names = self._list(f"{PREFIX}{rid}/")
        admin = viewer is None or (signed_in(viewer) and viewer.is_admin)
        me = None if viewer is None else actor_id(viewer)
        out = []
        for r, f in self._reports(names):
            device = r.get("source") == DEVICE_ROLE
            if not (admin or r.get("reporter_id") == me or (device and session is not None and may_act(viewer, session))):
                continue
            out.append({"report_id": r.get("report_id"), "type": BUG_TYPE, "offset_seconds": r.get("offset_seconds"),
                        **({"source": DEVICE_ROLE} if device else {}),
                        "created_at": r.get("created_at"), "status": "filed" if f else "pending",
                        "issue_number": (f or {}).get("issue_number"), "issue_url": (f or {}).get("issue_url")})
        return out

    # ------------------------------------------------------------ writing
    def submit(self, rid: str, actor, body: dict) -> dict:
        from .notes import ApiError, actor_id, check_actor, check_rid, is_read_only, may_see
        from .planner import QuoteRefused, quoted_run

        actor = check_actor(actor)
        if is_read_only(actor):
            raise ApiError(403, "READ_ONLY_ROLE")
        check_rid(rid)
        if body.get("type") != BUG_TYPE:
            raise ApiError(400, "BAD_MARKER_TYPE")
        note = body.get("note")
        if not isinstance(note, str) or not note.strip() or len(note) > MAX_NOTE:
            raise ApiError(400, "BAD_NOTE")
        area = body.get("area") or "other"
        if area not in AREAS:
            raise ApiError(400, "BAD_SCREEN")
        d = self.notes._draft(rid)
        s = self.notes._context(rid, d)
        if not may_see(actor, s):
            raise ApiError(404, "NOT_FOUND")
        off = body.get("offset_seconds")
        if off is not None:
            dur = float(s.get("duration_seconds") or 0)
            if isinstance(off, bool) or not isinstance(off, (int, float)) or off < 0 or (dur and off > dur + 1):
                raise ApiError(400, "BAD_TIME")
            off = int(off)
        t = self.notes.store.read_json(f"transcripts/{rid}.json") or {}
        run = quoted_run(note, [t])
        if run:
            raise QuoteRefused(run)
        never = never_values(self.notes.roster(), s, t if isinstance(t, dict) else {})
        now = self._now()
        report_id = f"bug_{now.strftime('%Y%m%dT%H%M%S')}_{secrets.token_hex(3)}"
        draft = self.notes.approval(rid) is None
        report = {
            "report_id": report_id, "recording_id": rid, "type": BUG_TYPE,
            "note": scrub_note(note.strip(), never), "offset_seconds": off, "area": area,
            "app_revision": self.info.get("revision"), "app_commit": self.info.get("commit"),
            "firmware_version": _fw(s.get("firmware_version")),
            "reporter_id": actor_id(actor), "reporter_role": getattr(actor, "role", "laptop"),
            "draft": draft, "created_at": now.isoformat(timespec="seconds"),
        }
        title, text = build_issue(report)
        try:
            check_issue(title, text, never)
        except BugReportBlocked as exc:
            log("bug_report_blocked", "ERROR", step="review", recording_id=rid)
            raise ApiError(500, "BUG_REPORT_BLOCKED") from exc
        # journal first and required: a submission that can't be journaled isn't saved (503)
        self.notes.recorder.record(actor, "bug_reported", record_id=rid, student_id=s.get("student_id"),
                                   required=True, bug_report_id=report_id, issue_status="pending", draft=draft)
        self.notes.store.write_json(f"{PREFIX}{rid}/{report_id}.json", report, create_only=True)
        from .notes import log_actor
        log("bug_reported", step="review", recording_id=rid, actor_id=log_actor(actor), report_id=report_id,
            status="pending", draft=draft)
        filed = self.file_pending(actor, first=f"{PREFIX}{rid}/{report_id}.json", limit=3)
        mine = filed.get(report_id)
        return {"report_id": report_id, "type": BUG_TYPE, "offset_seconds": off, "created_at": report["created_at"],
                "status": "filed" if mine else "pending", "issue_number": (mine or {}).get("issue_number"),
                "issue_url": (mine or {}).get("issue_url"), "message": THANKS}

    def ensure_device(self, rid: str, session: dict, names: list[str] | None = None) -> list[str] | None:
        """Recorder Bug markers -> pending bug reports (once each; the id comes from the recording
        and the offset, so every instance makes the same one and create-only keeps one copy).
        Note "Reported from recorder", screen "recorder", the firmware version from the recording.
        Returns the (updated) object names for the session, or `names` unchanged."""
        marks = [m for m in (session.get("markers") or []) if is_bug_marker(m)]
        if not marks:
            return names
        if names is None:
            names = self._list(f"{PREFIX}{rid}/")
        have = set(names)
        made = []
        stamp = re.sub(r"\D", "", str(session.get("timestamp_start") or ""))[:14].ljust(14, "0")
        for m in marks:
            try:
                off = max(0, int(float(m.get("offset_seconds") or 0)))
            except (TypeError, ValueError):
                continue
            h = hashlib.sha256(f"{rid}|recorder-bug|{off}".encode()).hexdigest()[:6]
            report_id = f"bug_{stamp[:8]}T{stamp[8:14]}_{h}"
            name = f"{PREFIX}{rid}/{report_id}.json"
            if name in have:
                continue
            draft = self.notes.approval(rid) is None
            start = recording_start(session)
            wall = _parse_time(m.get("wall_time"))
            at = clock_at(wall, 0) if wall else clock_at(start, off)
            report = {
                "report_id": report_id, "recording_id": rid, "type": BUG_TYPE, "source": DEVICE_ROLE,
                "device_id": _device(session.get("device_id")),
                "recording_start": start.astimezone(ET).isoformat() if start else None,
                "marker_clock": at,
                "note": DEVICE_NOTE, "offset_seconds": off, "area": DEVICE_SCREEN,
                "app_revision": self.info.get("revision"), "app_commit": self.info.get("commit"),
                "firmware_version": _fw(session.get("firmware_version")),
                "reporter_id": f"recorder:{session.get('device_id') or 'unknown'}", "reporter_role": DEVICE_ROLE,
                "draft": draft, "created_at": self._now().isoformat(timespec="seconds"),
            }
            title, text = build_issue(report)
            try:
                check_issue(title, text, never_values(self.notes.roster(), session))
            except BugReportBlocked:
                log("bug_report_blocked", "ERROR", step="review", recording_id=rid)
                continue
            try:
                self.notes.store.write_json(name, report, create_only=True)
            except Conflict:
                have.add(name)
                continue
            except Exception as exc:  # noqa: BLE001 - no bucket access for bug_reports/ yet: try next view
                log("bug_report_unsaved", "WARNING", step="review", recording_id=rid, error_type=type(exc).__name__)
                continue
            have.add(name)
            made.append(report_id)
            self.notes.recorder.record(SYSTEM, "bug_reported", record_id=rid, student_id=session.get("student_id"),
                                       bug_report_id=report_id, issue_status="pending", draft=draft)
            log("bug_reported", step="review", recording_id=rid, actor_id="recorder", report_id=report_id,
                status="pending", draft=draft)
        if made:
            threading.Thread(target=self.file_pending, name="recorder-bug-file", daemon=True).start()
        return sorted(have)

    def _github(self):
        tok = self.tokens.get() if self.tokens is not None else None
        if not tok:
            return None
        if self._gh is None or self._gh_token != tok:
            self._gh, self._gh_token = self.github_factory(tok), tok
        return self._gh

    def pending(self) -> list[str]:
        names = self._list(PREFIX)
        filed = {n[:-len(".filed.json")] for n in names if n.endswith(".filed.json")}
        return [n for n in sorted(names) if n.endswith(".json") and _REPORT_ID.match(n[:-5].split("/")[-1])
                and n[:-5] not in filed]

    def file_pending(self, actor=SYSTEM, *, first: str | None = None, limit: int = 20) -> dict:
        """File pending reports (oldest first; `first` before the rest) if the token is there.
        Returns {report id: filed record}. Never raises: a report that can't be filed stays pending."""
        out: dict = {}
        try:
            gh = self._github()
            if gh is None:
                return out
            todo = self.pending()
            if first in todo:
                todo = [first] + [n for n in todo if n != first]
            for name in todo[:limit]:
                rec = self._file_one(gh, name, actor)
                if rec:
                    out[rec["report_id"]] = rec
        except Exception as exc:  # noqa: BLE001 - the teacher's report is saved either way
            log("bug_report_file_failed", "WARNING", step="review", error_type=type(exc).__name__,
                http_status=getattr(exc, "status", None))
        return out

    def _file_one(self, gh, name: str, actor) -> dict | None:
        base = name[:-len(".json")]
        now = self._now()
        window = now.strftime("%Y%m%dT%H") + str(now.minute // 10)
        try:
            self.notes.store.write_json(f"{base}.claim-{window}.json", {"at": now.isoformat(timespec="seconds")},
                                        create_only=True)
        except Conflict:
            return None                                # another instance is on it
        report = self.notes.store.read_json(name) or {}
        rid = report.get("recording_id")
        s = self.notes._context(rid, self.notes._draft(rid)) if rid else {}
        never = never_values(self.notes.roster(), s)
        title, body = build_issue(report)
        try:
            check_issue(title, body, never)
        except BugReportBlocked:
            log("bug_report_blocked", "ERROR", step="review", recording_id=rid)
            return None
        try:
            issue = gh.create_issue(title, body)
        except GitHubError as exc:
            log("bug_report_file_failed", "WARNING", step="review", recording_id=rid, http_status=exc.status,
                reason=re.sub(r"[^A-Za-z0-9_:/\- ]", "", exc.what)[:80].replace(" ", "_"))
            return None
        project_added = True
        try:
            gh.add_to_project(issue["node_id"])
        except GitHubError as exc:
            project_added = False
            log("bug_report_project_failed", "WARNING", step="review", recording_id=rid, http_status=exc.status)
        rec = {"report_id": report.get("report_id"), "issue_number": issue["number"], "issue_url": issue["url"],
               "filed_at": self._now().isoformat(timespec="seconds"), "project_added": project_added}
        self.notes.store.write_json(f"{base}.filed.json", rec, create_only=True)
        self.notes.recorder.record(actor, "bug_report_filed", record_id=rid, bug_report_id=report.get("report_id"),
                                   issue_number=issue["number"], issue_status="filed", draft=report.get("draft"))
        log("bug_report_filed", step="review", recording_id=rid, report_id=report.get("report_id"),
            issue_number=issue["number"], status="filed")
        return rec


def _device(v) -> str | None:
    return v if isinstance(v, str) and re.match(r"^[A-Za-z0-9._:\-]{1,64}$", v) else None


def _started(iso) -> str:
    t = _parse_time(iso)
    if not t:
        return "not available (no reliable start time)"
    t = t.astimezone(ET)
    return f"{t:%Y-%m-%d} {t.hour % 12 or 12}:{t.minute:02d}:{t.second:02d} {'AM' if t.hour < 12 else 'PM'} Eastern"


def _fw(v) -> str | None:
    return v if isinstance(v, str) and re.match(r"^[A-Za-z0-9._+\-]{1,40}$", v) else None
