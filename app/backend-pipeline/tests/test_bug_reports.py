"""Bug markers (privacy guardrails).

  * a teacher's "Bug" marker becomes an issue in your-org/your-issues-repo (labels bug +
    teacher-feedback, Team Board with Project = Goalvera)
  * the issue holds only: the note (student names -> [student]), the time in the
    recording, the screen, the app revision / firmware, the internal recording id and the
    reporter's role. Never student names, transcript text, teacher names or emails,
    school ids, in the title or the body
  * no token yet: saved as pending, the teacher still sees "Thanks, sent to our team.",
    filed on the next submission or on start once the token is there
  * bug markers stay out of the incident list / numbering, the session PDF and the
    incident print view; the session shows them only to the reporter and admins
  * every submission is in the journal: bug_reported (+ bug_report_filed with the issue number)
"""
from __future__ import annotations

import io
import json
import urllib.error
from contextlib import redirect_stdout

import pytest

from test_hosted_auth import ADMIN, OTHER, RID, TEACHER_EMAIL, call, hdr, token  # noqa: F401
from test_hosted_auth import authn, store  # noqa: F401  (pytest fixtures)
from test_journal import DOB, LAST_NAME, SCHOOL_ID, FailingJournal, serve
from test_roles import RBT_EMAIL, world  # noqa: F401  (pytest fixture)
from test_review_pdf import pdf_text
from review_api.bug_reports import (STUDENT, SYSTEM, THANKS, BugReportBlocked, GitHub, SecretToken,
                                    build_issue, check_issue, is_placeholder, never_values, scrub_note)
from review_api.journal import MemoryJournal, Recorder
from review_api.notes import ApiError, Notes

TRANSCRIPT_RUN = "Good morning, everyone. Marcus, let's start with our math warm-up. Double digit addition on page 12."
NOTE = (f"Marcus {LAST_NAME} (ID {SCHOOL_ID}, born {DOB}) - when I clicked Approve for marcus the page froze. "
        f"Ms Jenkins and sarah.jenkins@school.org saw it too; Sarah says MARCUS's mom called. Student no. 4482913.")
NEVER = ("Marcus", LAST_NAME, SCHOOL_ID, DOB, "Jenkins", "Sarah", "@", TEACHER_EMAIL, "4482913",
         "Good morning", "Pat Speech", "Jordan Rivera")


def tok(email):
    return token(email, uid="uid" + email.split("@")[0].replace("-", ""))


class FakeGitHub:
    def __init__(self):
        self.issues, self.project = [], []

    def create_issue(self, title, body):
        n = 500 + len(self.issues)
        self.issues.append((title, body))
        return {"number": n, "url": f"https://github.com/your-org/your-issues-repo/issues/{n}", "node_id": f"I_{n}"}

    def add_to_project(self, node_id):
        self.project.append(node_id)


class Tokens:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value


@pytest.fixture()
def school(world):
    r = world.read_json("config/demo_roster.json")
    r["teachers"]["usr_demo_sarah"]["emails"] = [TEACHER_EMAIL]
    r["students"]["stu_101"].update(first_name="Marcus", last_name=LAST_NAME, dob=DOB, school_id=SCHOOL_ID)
    world.write_json("config/demo_roster.json", r)
    d = world.read_json(f"drafts/{RID}.json")
    d["analysis"]["incident_analysis"] = [{"timestamp_seconds": 49, "antecedent": "a", "behavior": "b",
                                           "consequence_intervention": "c"}]
    world.write_json(f"drafts/{RID}.json", d)
    return world


def make(store, tokens=None, journal=None, gh=None):
    gh = gh or FakeGitHub()
    notes = Notes(store, recorder=Recorder(journal or MemoryJournal()),
                  bug_options={"token_source": tokens or Tokens(), "github_factory": lambda t: gh,
                               "info": {"revision": "review-00018-abc", "commit": "1234abc"}})
    return notes, gh


def bug(note=NOTE, **kw):
    return {"type": "Bug", "note": note, "offset_seconds": 83, "area": "approve", **kw}


# ---------------------------------------------------------------- what may reach the issue

def test_note_is_scrubbed_before_it_is_stored_or_filed(school, authn):
    notes, gh = make(school, Tokens("ghp_test"))
    out = notes.bug_report(RID, authn.require(hdr(tok(TEACHER_EMAIL))), bug())
    assert out["status"] == "filed" and out["message"] == THANKS == "Thanks, sent to our team."
    title, body = gh.issues[0]
    stored = [json.dumps(school.read_json(n)) for n in school.list("bug_reports/")]
    for blob in (title, body, *stored):
        for never in NEVER:
            assert never.lower() not in blob.lower(), never
    assert body.count(STUDENT) >= 4 and "[removed]" in body and "[email]" in body and "[number]" in body
    assert "[staff]" in body
    assert "| Time in recording | 1:23 |" in body
    assert "| Screen | Session detail (review site) \u2192 Approve |" in body
    assert "review-00018-abc (commit 1234abc)" in body and f"`{RID}`" in body
    assert "| Recorder firmware | sample |" in body              # from the recording's sidecar
    assert "| Reported by | role: reviewer (no name or email) |" in body
    assert title.startswith("Teacher bug report: [student]")


def test_issue_body_holds_only_the_allowed_fields(school, authn):
    notes, gh = make(school, Tokens("ghp_test"))
    notes.bug_report(RID, authn.require(hdr(tok(TEACHER_EMAIL))), bug("The Approve button spins forever."))
    _, body = gh.issues[0]
    rows = [line.split("|")[1].strip() for line in body.splitlines() if line.startswith("| ") and "---" not in line]
    assert rows == ["", "Time in recording", "Screen", "App", "Recorder firmware", "Recording", "Session status",
                    "Reported by", "Reported at", "Report id"]
    for leak in ("uidteacher", "usr_demo_sarah", "stu_101", "Sarah Jenkins", TEACHER_EMAIL, "RM12 \u00b7"):
        assert leak not in body
    assert "```text\nThe Approve button spins forever.\n```" in body


@pytest.mark.parametrize("note", [
    "marcus cried", "MARCUS", "Marcus's chair", f"{LAST_NAME}", f"{LAST_NAME.lower()}-family", "ms. jenkins",
    "sarah", "write to teacher@example.org", f"id={SCHOOL_ID}", f"dob {DOB}", "Pat Speech said", "jordan",
    "ID 0042317 again"])
def test_no_variant_of_a_forbidden_value_survives(school, authn, note):
    notes, gh = make(school, Tokens("ghp_test"))
    notes.bug_report(RID, authn.require(hdr(tok(ADMIN))), bug(f"Problem: {note} on the screen"))
    title, body = gh.issues[0]
    for never in (*NEVER, "Pat", "Speech", "Jordan", "Rivera", "0042317"):
        if never in ("@",):
            assert "@" not in title + body
            continue
        assert never.lower() not in (title + body).lower(), (note, never)


def test_transcript_text_is_refused_and_nothing_is_saved(school, authn):
    notes, gh = make(school, Tokens("ghp_test"))
    with pytest.raises(ApiError) as e:
        notes.bug_report(RID, authn.require(hdr(tok(TEACHER_EMAIL))), bug(f"It said: {TRANSCRIPT_RUN}"))
    assert (e.value.http, e.value.code) == (422, "QUOTES_TRANSCRIPT")
    assert school.list("bug_reports/") == [] and gh.issues == []


def test_a_leak_past_the_scrub_is_never_filed():
    never = {"student": ["Marcus", LAST_NAME], "staff": ["Jenkins"], "other": [SCHOOL_ID]}
    for leak in ("marcus", f"x {SCHOOL_ID}", "a@b.org", "the jenkins note", f"{LAST_NAME.upper()}"):
        with pytest.raises(BugReportBlocked):
            check_issue("Teacher bug report", leak, never)
    check_issue("Teacher bug report", "[student] waited; Marcy is not a roster name", never)


def test_scrub_and_never_values_from_the_roster(school):
    r = school.read_json("config/demo_roster.json")
    never = never_values(r, school.read_json(f"drafts/{RID}.json")["session"])
    assert {"Marcus", LAST_NAME} <= set(never["student"])
    assert {"Sarah", "Jenkins", "Jordan", "Rivera", "Pat"} <= set(never["staff"])
    assert "Demo" not in never["staff"] and "Speech" not in never["staff"] and "Pat Speech" in never["staff"]
    assert {SCHOOL_ID, DOB} <= set(never["other"])
    assert scrub_note("Marcus and marcus's sister; Marcy stays", never) == "[student] and [student]'s sister; Marcy stays"
    title, body = build_issue({"note": "x" * 200, "offset_seconds": None, "recording_id": RID, "report_id": "bug_1",
                               "created_at": "t", "reporter_role": "teacher"})
    assert len(title) < 100 and "| Time in recording | not given |" in body


# ---------------------------------------------------------------- pending until the token exists

def test_no_token_saves_pending_and_files_later(school, authn):
    jrnl = MemoryJournal()
    tokens = Tokens(None)
    notes, gh = make(school, tokens, jrnl)
    teacher = authn.require(hdr(tok(TEACHER_EMAIL)))
    out = notes.bug_report(RID, teacher, bug("First report."))
    assert (out["status"], out["issue_url"], out["message"]) == ("pending", None, THANKS)
    assert gh.issues == [] and len(notes.bugs.pending()) == 1
    assert [i["status"] for i in notes.session(RID, teacher)["bug_reports"]] == ["pending"]
    tokens.value = "github_pat_test"                         # an admin adds the secret version
    out2 = notes.bug_report(RID, teacher, bug("Second report."))
    assert out2["status"] == "filed" and len(gh.issues) == 2 and gh.project == ["I_500", "I_501"]
    assert "Second report." in gh.issues[0][1] and "First report." in gh.issues[1][1]   # the new one first
    assert notes.bugs.pending() == []
    items = notes.session(RID, teacher)["bug_reports"]
    assert sorted(i["issue_number"] for i in items) == [500, 501] and all(i["status"] == "filed" for i in items)
    rows = jrnl.rows()
    rep = [r for r in rows if r["action"] == "bug_reported"]
    filed = [r for r in rows if r["action"] == "bug_report_filed"]
    assert [r["details"]["issue_status"] for r in rep] == ["pending", "pending"]
    assert all(r["details"]["draft"] is True and r["record_id"] == RID and r["student_id"] == "stu_101"
               and r["actor_id"] == "uidteacher" for r in rep)
    assert sorted(r["details"]["issue_number"] for r in filed) == [500, 501]
    assert {r["details"]["bug_report_id"] for r in rep} == {r["details"]["bug_report_id"] for r in filed}


def test_pending_reports_are_filed_on_start_once_only(school, authn):
    notes, gh = make(school, Tokens(None))
    notes.bug_report(RID, authn.require(hdr(tok(TEACHER_EMAIL))), bug("Pending one."))
    notes.bugs.tokens = Tokens("github_pat_test")
    first = notes.bugs.file_pending()                            # what server start runs
    again = notes.bugs.file_pending()
    assert len(first) == 1 and again == {} and len(gh.issues) == 1
    other, gh2 = make(school, Tokens("github_pat_test"))         # a second instance finds nothing to do
    assert other.bugs.file_pending() == {} and gh2.issues == []


def test_a_claimed_report_is_not_filed_twice(school, authn):
    notes, gh = make(school, Tokens(None))
    notes.bug_report(RID, authn.require(hdr(tok(TEACHER_EMAIL))), bug("Claimed."))
    a, gh_a = make(school, Tokens("t"))
    b, gh_b = make(school, Tokens("t"))
    name = a.bugs.pending()[0]
    school.write_json(name[:-5] + ".claim-" + a.bugs._now().strftime("%Y%m%dT%H") + str(a.bugs._now().minute // 10)
                      + ".json", {"at": "x"}, create_only=True)  # another instance is on it right now
    assert b.bugs.file_pending() == {} and gh_b.issues == []


def test_github_trouble_keeps_it_pending(school, authn):
    class Down(FakeGitHub):
        def create_issue(self, title, body):
            from review_api.bug_reports import GitHubError
            raise GitHubError("POST /repos/x/issues", 403)
    notes, _ = make(school, Tokens("t"), gh=Down())
    out = notes.bug_report(RID, authn.require(hdr(tok(TEACHER_EMAIL))), bug("GitHub is down."))
    assert out["status"] == "pending" and len(notes.bugs.pending()) == 1


# ---------------------------------------------------------------- who, and where it shows

def test_who_may_report_and_who_sees_it(school, authn):
    notes, _ = make(school)
    teacher, other, admin, rbt = (authn.require(hdr(tok(e))) for e in (TEACHER_EMAIL, OTHER, ADMIN, RBT_EMAIL))
    with pytest.raises(ApiError) as e:
        notes.bug_report(RID, rbt, bug("x"))
    assert (e.value.http, e.value.code) == (403, "READ_ONLY_ROLE")
    for body, code in ((bug(type="Incident"), "BAD_MARKER_TYPE"), (bug(note=" "), "BAD_NOTE"),
                       (bug(note="x" * 1501), "BAD_NOTE"), (bug(offset_seconds=99999), "BAD_TIME"),
                       (bug(offset_seconds=-1), "BAD_TIME"), (bug(area="admin"), "BAD_SCREEN")):
        with pytest.raises(ApiError) as e:
            notes.bug_report(RID, teacher, body)
        assert e.value.code == code
    notes.bug_report(RID, teacher, bug("Teacher's report."))
    notes.bug_report(RID, other, bug("Other teacher's report."))
    seen = {who: len(notes.session(RID, u)["bug_reports"]) for who, u in
            (("teacher", teacher), ("other", other), ("admin", admin), ("rbt", rbt))}
    assert seen == {"teacher": 1, "other": 1, "admin": 2, "rbt": 0}
    item = notes.session(RID, teacher)["bug_reports"][0]
    assert set(item) == {"report_id", "type", "offset_seconds", "created_at", "status", "issue_number", "issue_url"}
    listed = {r["recording_id"]: r for r in notes.list_sessions(admin)}
    assert len(listed[RID]["bug_reports"]) == 2


def test_bug_markers_stay_out_of_incidents_and_pdfs(school, authn):
    notes, _ = make(school)
    teacher = authn.require(hdr(tok(TEACHER_EMAIL)))
    before = notes.session(RID, teacher)
    p_before = notes.incident_print(RID, teacher, 1)
    notes.bug_report(RID, teacher, bug("Bug at 1:23.", offset_seconds=10))
    after = notes.session(RID, teacher)
    for k in ("markers", "processed_analysis", "incident_flag_count"):
        assert after[k] == before[k], k
    assert all(m["type"] != "Bug" for m in after["markers"])
    p_after = notes.incident_print(RID, teacher, 1)
    assert p_after["label"] == p_before["label"] == "Incident 1 \u00b7 0:49" and p_after["of"] == 1
    with pytest.raises(ApiError):
        notes.incident_print(RID, teacher, 2)
    notes.approve(RID, teacher, school.read_json(f"drafts/{RID}.json")["analysis"])
    text = pdf_text(notes.pdf(RID, teacher)[0])
    assert "Bug" not in text and "Incident 1\n" in text and "0:49 into recording" in text
    # even a "Bug" marker that reached the session record is never shown as a marker or in the PDF
    d = school.read_json(f"drafts/{RID}.json")
    d["session"]["markers"] = [*d["session"]["markers"], {"type": "Bug", "offset_seconds": 5}]
    school.write_json(f"drafts/{RID}.json", d)
    assert all(m["type"] != "Bug" for m in notes.session(RID, teacher)["markers"])
    assert "Bug" not in pdf_text(notes.pdf(RID, teacher)[0])


# ---------------------------------------------------------------- HTTP, journal, logs

def test_http_bug_report(school, authn):
    jrnl = MemoryJournal()
    srv, base = serve(school, authn, jrnl)
    try:
        path = f"/api/sessions/{RID}/bug_report"
        out = io.StringIO()
        with redirect_stdout(out):
            st, body, _ = call(base, path, tok=tok(TEACHER_EMAIL), method="POST", body=bug())
        assert st == 200 and json.loads(body)["status"] == "pending" and json.loads(body)["message"] == THANKS
        for never in NEVER:
            assert never.lower().encode() not in body.lower()
        assert call(base, path, tok=tok(RBT_EMAIL), method="POST", body=bug())[0] == 403
        assert call(base, path, method="POST", body=bug())[0] == 401
        assert call(base, path, tok=tok(TEACHER_EMAIL))[0] == 405
        st, body, _ = call(base, path, tok=tok(TEACHER_EMAIL), method="POST", body=bug(f"x {TRANSCRIPT_RUN}"))
        assert (st, json.loads(body)) == (422, {"error": "QUOTES_TRANSCRIPT"})
        rows = [r for r in jrnl.rows() if r["action"] == "bug_reported"]
        assert len(rows) == 1 and rows[0]["details"]["issue_status"] == "pending"
        logs = [json.loads(x) for x in out.getvalue().splitlines() if '"bug_reported"' in x]
        assert logs and logs[0]["recording_id"] == RID and logs[0]["actor_id"].startswith("u_")
        for blob in (json.dumps(jrnl.rows()), out.getvalue()):
            for never in NEVER:
                assert never.lower() not in blob.lower(), never
    finally:
        srv.shutdown()


def test_refused_if_it_cannot_be_journaled(school, authn):
    srv, base = serve(school, authn, FailingJournal())
    try:
        st, body, _ = call(base, f"/api/sessions/{RID}/bug_report", tok=tok(TEACHER_EMAIL), method="POST", body=bug())
        assert (st, json.loads(body)) == (503, {"error": "JOURNAL_UNAVAILABLE"})
        assert school.list("bug_reports/") == []
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- GitHub + Secret Manager clients

class FakeResp:
    def __init__(self, status, body):
        self.status, self._b = status, json.dumps(body).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_github_client_labels_issue_and_project():
    calls = []

    def opener(req, timeout):
        body = json.loads(req.data) if req.data else None
        calls.append((req.get_method(), req.full_url.replace(GitHub.API, ""), body, req.get_header("Authorization")))
        url = req.full_url
        if url.endswith("/labels/bug"):
            return FakeResp(200, {"name": "bug"})
        if url.endswith("/labels/teacher-feedback"):
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        if url.endswith("/labels"):
            return FakeResp(201, {})
        if url.endswith("/issues"):
            return FakeResp(201, {"number": 77, "html_url": "https://github.com/o/r/issues/77", "node_id": "I_77"})
        q = body["query"]
        if "organization" in q:
            return FakeResp(200, {"data": {"organization": {"projectV2": {"id": "PVT_1", "field": {
                "id": "F_1", "options": [{"id": "o1", "name": "PSA"}, {"id": "o2", "name": "Goalvera"}]}}}}})
        if "addProjectV2ItemById" in q:
            return FakeResp(200, {"data": {"addProjectV2ItemById": {"item": {"id": "ITEM_1"}}}})
        return FakeResp(200, {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "ITEM_1"}}}})

    gh = GitHub("github_pat_SECRET", repo="your-org/your-issues-repo", project_owner="your-org", project_number=1,
                project_field="Project", project_value="Goalvera", opener=opener)
    out = io.StringIO()
    with redirect_stdout(out):
        issue = gh.create_issue("Teacher bug report: x", "body")
        gh.add_to_project(issue["node_id"])
    assert issue == {"number": 77, "url": "https://github.com/o/r/issues/77", "node_id": "I_77"}
    seq = [(m, p) for m, p, _, _ in calls]
    assert seq[:4] == [("GET", "/repos/your-org/your-issues-repo/labels/bug"),
                       ("GET", "/repos/your-org/your-issues-repo/labels/teacher-feedback"),
                       ("POST", "/repos/your-org/your-issues-repo/labels"), ("POST", "/repos/your-org/your-issues-repo/issues")]
    assert calls[2][2]["name"] == "teacher-feedback"
    assert calls[3][2]["labels"] == ["bug", "teacher-feedback"]
    assert calls[-1][2]["variables"] == {"p": "PVT_1", "i": "ITEM_1", "f": "F_1", "o": "o2"}
    assert all(a == "Bearer github_pat_SECRET" for *_, a in calls)
    assert "SECRET" not in out.getvalue()


def test_secret_token_missing_then_present():
    clock = [0.0]
    answers = [None, "github_pat_x"]
    st = SecretToken("p", "github-issues-token", fetch=lambda: answers.pop(0), recheck=60, now=lambda: clock[0])
    assert st.get() is None
    clock[0] = 30
    assert st.get() is None and answers == ["github_pat_x"]       # not re-checked within a minute
    clock[0] = 61
    assert st.get() == "github_pat_x"

    def boom():
        raise RuntimeError("github_pat_leak")
    bad = SecretToken("p", "s", fetch=boom)
    assert bad.get() is None and "leak" not in bad.state
    assert SYSTEM.role == "system"


# ---------------------------------------------------------------- placeholders are not names (Oct 10)

@pytest.mark.parametrize("value", ["Student", "student", "Student 2", "the student", "Demo Teacher", "Teacher",
                                   "Unknown", "Student A", "RBT", "Sample Student", "N/A", "TBD"])
def test_generic_placeholders_are_not_names(value):
    assert is_placeholder(value)
    never = never_values({}, {"student_first_name": value, "teacher_name": value})
    assert never["student"] == [] and never["staff"] == []


@pytest.mark.parametrize("value", ["Marcus", "Student Marcus", "Pat Speech", "Doc", "Alex Morgan", "Ms. Rivera"])
def test_real_names_are_still_names(value):
    assert not is_placeholder(value)
    never = never_values({}, {"student_first_name": value})
    assert never["student"]


def test_a_recorder_report_on_a_placeholder_student_files_but_real_names_still_block():
    """The three Oct 8 recorder reports were held: their recordings store student_first_name "Student"
    and the issue text says "a teacher pressed Bug ..." / "Student" in the template."""
    session = {"student_first_name": "Student", "teacher_name": "Demo Teacher", "class_code": "Math 2B"}
    title, body = build_issue({"note": "Reported from recorder", "offset_seconds": 39, "recording_id": RID,
                               "report_id": "bug_1", "created_at": "t", "reporter_role": "recorder"})
    blob = title + body + " The student and the teacher. Student report."
    check_issue(title, blob, never_values({}, session))                       # files
    roster = {"students": {"stu_1": {"first_name": "Marcus", "last_name": "Okafor"}},
              "teachers": {"t1": {"name": "Sarah Jenkins"}}}
    never = never_values(roster, {**session, "student_first_name": "Student Doc"})
    for leak in ("Marcus waited", "Okafor", "Ms Jenkins", "Doc left", "Student Doc"):
        with pytest.raises(BugReportBlocked):
            check_issue(title, body + " " + leak, never)
