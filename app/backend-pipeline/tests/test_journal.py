"""Admin activity journal, against an in-memory journal and a fake BigQuery.

  * internal IDs only (no names, no emails, no free text), add-only
  * views (every RBT view), edits, approvals, exports, classroom moves, refused
    attempts, sign-ins, journal views and report downloads are journaled
  * admin only
  * IE9: report downloads use first name + last initial and never show a school
    ID, a full last name or a date of birth; each download is journaled
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import threading

import pytest

from test_hosted_auth import ADMIN, RID, TEACHER_EMAIL, call, token  # noqa: F401
from test_hosted_auth import authn, store  # noqa: F401  (pytest fixtures)
from test_roles import ART_TEACHER, LOOSE_RID, RBT_EMAIL, READING_RID, world  # noqa: F401
from review_api.journal import (COLUMNS, READER_SCOPES, BigQueryJournal, JournalError, JournalReadFailed,
                                MemoryJournal, Query, backfill_from_audit, make_entry)
from review_api.journal_reports import Directory, ReportBlocked, first_last_initial, to_csv
from review_api.notes import Notes
from review_api.server import build_server

SCHOOL_ID, LAST_NAME, DOB = "ATL-0042317", "Anderson", "2015-04-03"


class FailingJournal(MemoryJournal):
    kind = "bigquery"

    def append(self, entries):
        raise JournalError("down")


def serve(store, authn, journal):
    srv = build_server(store, 0, "local", auth=authn, journal=journal,
                       auth_config={"projectId": "p", "apiKey": "k", "providers": ["password"]})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture()
def jrnl():
    return MemoryJournal()


@pytest.fixture()
def site(world, authn, jrnl):
    r = world.read_json("config/demo_roster.json")
    r["teachers"]["usr_demo_sarah"]["emails"] = [TEACHER_EMAIL]
    # what a real student record may hold one day; none of it may ever reach a download
    r["students"]["stu_101"].update(first_name="Marcus", last_name=LAST_NAME, dob=DOB, school_id=SCHOOL_ID)
    world.write_json("config/demo_roster.json", r)
    srv, base = serve(world, authn, jrnl)
    yield base
    srv.shutdown()


def tok(email):
    return token(email, uid="uid" + email.split("@")[0].replace("-", ""))


# ---------------------------------------------------------------- entry format

def test_entries_hold_internal_ids_only():
    ok = make_entry(action="session_viewed", actor_id="kG7xQ2mVbN8Uid", actor_role="rbt",
                    record_id="rec_laptop_demo_20260929_090000_Reading_3A", student_id="stu_101")
    assert set(ok) == set(COLUMNS) and ok["record_type"] == "session" and ok["school_id"] == "demo-school"
    bad = [
        dict(actor_id="sarah@example.org"),                       # email
        dict(student_id="Marcus"),                                # a name instead of an id
        dict(student_id="Marcus A."),
        dict(details={"note": "free text"}),                      # unknown detail key
        dict(details={"to_classroom": "Reading 3A"}),             # display spelling, not the internal key
        dict(details={"error_code": "Sarah Jenkins"}),
        dict(details={"edited": "yes"}),
        dict(action="deleted_everything"),
        dict(actor_role="parent"),
    ]
    for kw in bad:
        args = dict(action="session_viewed", actor_id="uid1", actor_role="teacher", record_id="rec_x")
        args.update(kw)
        with pytest.raises(JournalError):
            make_entry(**args)


def test_memory_journal_is_add_only():
    j = MemoryJournal()
    e = make_entry(action="sign_in", actor_id="uid1", actor_role="admin", record_id="uid1")
    j.append([e])
    j.append([e])                                                # a retry: no second row
    assert len(j.rows()) == 1
    assert not any(hasattr(j, m) for m in ("update", "delete", "remove", "truncate", "clear"))
    with pytest.raises(JournalError):
        j.append([{**e, "event_id": "x2", "extra": "field"}])


def test_query_limits_are_a_cost_cap():
    q = Query.parse({"from": "2026-09-01", "to": "2026-10-02"})
    assert (q.period_from, q.period_to, q.limit) == (dt.date(2026, 9, 1), dt.date(2026, 10, 2), 500)
    for bad in ({"from": "2024-01-01", "to": "2026-10-02"}, {"from": "2026-10-03", "to": "2026-10-02"},
                {"action": "nope"}, {"student": "Marcus"}, {"from": "yesterday"}):
        with pytest.raises(JournalError):
            Query.parse(bad, today=dt.date(2026, 10, 2))
    assert Query.parse({"limit": "999999"}).limit == Query.MAX_LIMIT


# ---------------------------------------------------------------- what gets journaled

def test_views_edits_approvals_exports_and_moves_are_journaled(site, world, jrnl):
    # teacher: sign in, list, open, approve with an edit, download the PDF, move a session
    assert call(site, "/api/me", tok=tok(TEACHER_EMAIL))[0] == 200
    st, body, _ = call(site, "/api/sessions", tok=tok(TEACHER_EMAIL))
    analysis = {s["recording_id"]: s for s in json.loads(body)["sessions"]}[RID]["processed_analysis"]
    assert call(site, f"/api/sessions/{RID}/transcript", tok=tok(TEACHER_EMAIL))[0] == 200
    st, _, _ = call(site, f"/api/sessions/{RID}/approve", tok=tok(TEACHER_EMAIL), method="POST",
                    body={"analysis": {**analysis, "general_session_summary": "Teacher rewrote this summary"}})
    assert st == 200
    assert call(site, f"/api/sessions/{RID}/pdf", tok=tok(TEACHER_EMAIL))[0] == 200
    assert call(site, f"/api/sessions/{READING_RID}/classroom", tok=tok(TEACHER_EMAIL), method="POST",
                body={"class_code": "Art 9Z"})[0] == 200
    # RBT: every view, and a refused approve
    rbt = tok(RBT_EMAIL)
    assert call(site, "/api/me", tok=rbt)[0] == 200
    assert call(site, "/api/sessions", tok=rbt)[0] == 200
    for rid in (RID, READING_RID, LOOSE_RID):
        assert call(site, f"/api/sessions/{rid}/transcript", tok=rbt)[0] == 200
    assert call(site, f"/api/sessions/{RID}/audit", tok=rbt)[0] == 200
    assert call(site, f"/api/sessions/{READING_RID}/approve", tok=rbt, method="POST", body={"analysis": {}})[0] == 403

    rows = jrnl.rows()
    by = lambda role: [(r["action"], r["record_id"]) for r in rows if r["actor_role"] == role]  # noqa: E731
    assert by("reviewer") == [
        ("sign_in", "uidteacher"), ("sessions_listed", None), ("session_viewed", RID),
        ("session_approved", RID), ("notes_edited", RID), ("pdf_downloaded", RID),
        ("classroom_changed", READING_RID)]
    assert by("rbt") == [
        ("sign_in", "uiddemorbt"), ("sessions_listed", None), ("session_viewed", RID),
        ("session_viewed", READING_RID), ("session_viewed", LOOSE_RID), ("audit_viewed", RID),
        ("request_refused", READING_RID)]
    refused = rows[-1]
    assert refused["details"] == {"error_code": "READ_ONLY_ROLE", "attempted": "approve"}
    moved = [r for r in rows if r["action"] == "classroom_changed"][0]
    assert moved["details"]["from_classroom"] == "reading-3a" and moved["details"]["to_classroom"] == "art-9z"
    assert [r["student_id"] for r in rows if r["record_id"] == RID] == ["stu_101"] * 6
    blob = json.dumps(rows)
    for never in ("@", "Marcus", "Sarah", "Jenkins", LAST_NAME, DOB, SCHOOL_ID, "Teacher rewrote", "Hello family"):
        assert never not in blob, never


def test_rbt_view_refused_if_it_cannot_be_journaled(world, authn):
    srv, base = serve(world, authn, FailingJournal())
    try:
        st, body, _ = call(base, "/api/sessions", tok=tok(RBT_EMAIL))
        assert (st, json.loads(body)) == (503, {"error": "JOURNAL_UNAVAILABLE"})
        st, _, _ = call(base, f"/api/sessions/{RID}/transcript", tok=tok(RBT_EMAIL))
        assert st == 503
        assert call(base, "/api/sessions", tok=tok(TEACHER_EMAIL))[0] == 200   # teachers aren't blocked
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- admin only + IE9

def _activity(site):
    call(site, "/api/me", tok=tok(TEACHER_EMAIL))
    call(site, "/api/sessions", tok=tok(TEACHER_EMAIL))
    call(site, f"/api/sessions/{RID}/transcript", tok=tok(TEACHER_EMAIL))
    call(site, f"/api/sessions/{RID}/transcript", tok=tok(RBT_EMAIL))
    call(site, f"/api/sessions/{RID}/transcript", tok=tok(ART_TEACHER))


def test_journal_and_reports_are_admin_only(site, jrnl):
    _activity(site)
    for email in (TEACHER_EMAIL, ART_TEACHER, RBT_EMAIL):
        for path in ("/api/admin/journal", "/api/admin/reports/activity.csv?by=person"):
            st, body, _ = call(site, path, tok=tok(email))
            assert (st, json.loads(body)) == (403, {"error": "ADMIN_ONLY"}), (email, path)
    assert call(site, "/api/admin/journal")[0] == 401
    st, body, _ = call(site, f"/api/admin/journal?student=stu_101", tok=tok(ADMIN))
    assert st == 200
    entries = json.loads(body)["entries"]
    assert {e["action"] for e in entries} == {"session_viewed"} and len(entries) == 3
    assert {e["student"] for e in entries} == {"Marcus A."}
    assert "Sarah J." in {e["actor"] for e in entries}
    assert all("@" not in json.dumps(e) for e in entries)
    assert jrnl.rows()[-1]["action"] == "journal_viewed" and jrnl.rows()[-1]["details"]["filter_student_id"] == "stu_101"
    assert call(site, "/api/admin/journal?from=2020-01-01", tok=tok(ADMIN))[0] == 400


def test_IE9_downloads_never_show_school_id_full_last_name_or_dob(site, jrnl):
    _activity(site)
    for by in ("student", "person"):
        st, data, h = call(site, f"/api/admin/reports/activity.csv?by={by}", tok=tok(ADMIN))
        assert st == 200 and h["Content-Type"].startswith("text/csv")
        assert h["Content-Disposition"].startswith(f'attachment; filename="activity-by-{by}_')
        text = data.decode()
        for never in (SCHOOL_ID, LAST_NAME, DOB, "Jenkins", "Rivera", "@", "example.org", "staff.example"):
            assert never.casefold() not in text.casefold(), (by, never)
        rows = list(csv.DictReader(io.StringIO(text)))
        if by == "student":
            assert [r["student"] for r in rows] == ["Marcus A."] and rows[0]["session_viewed"] == "3"
        else:
            assert "Sarah J." in {r["person"] for r in rows}
            assert "Jordan R." in {r["person"] for r in rows}
        logged = jrnl.rows()[-1]
        assert logged["action"] == "report_downloaded" and logged["actor_role"] == "admin"
        assert logged["details"]["group_by"] == by and logged["details"]["row_count"] == len(rows)
        assert logged["details"]["report_sha256"] == hashlib.sha256(data).hexdigest()


def test_IE9_download_refused_if_not_journaled(world, authn):
    srv, base = serve(world, authn, FailingJournal())
    try:
        st, body, _ = call(base, "/api/admin/reports/activity.csv?by=person", tok=tok(ADMIN))
        assert (st, json.loads(body)) == (503, {"error": "JOURNAL_UNAVAILABLE"})
    finally:
        srv.shutdown()


def test_IE9_names_and_the_last_line_of_defence():
    assert first_last_initial("Marcus", "Anderson") == "Marcus A."
    assert first_last_initial("Marcus Lee", "Anderson-Smith") == "Marcus A."
    assert first_last_initial("Marcus", last_initial="A.") == "Marcus A."
    assert first_last_initial("Marcus") == "Marcus" and first_last_initial(None, "Anderson") is None
    d = Directory({"students": {"stu_1": {"first_name": "Marcus", "last_name": LAST_NAME, "dob": DOB,
                                          "school_id": SCHOOL_ID}}})
    for leak in (LAST_NAME, DOB, SCHOOL_ID, "someone@example.org"):
        with pytest.raises(ReportBlocked):
            to_csv(["student"], [[f"Marcus {leak}"]], d)
    assert to_csv(["student"], [["Marcus A."]], d) == b"student\nMarcus A.\n"


# ---------------------------------------------------------------- backfill + BigQuery writer

def test_backfill_copies_the_bucket_audit_trail_once(world, authn):
    notes = Notes(world)
    from test_roles import who
    notes.approve(RID, who(authn, TEACHER_EMAIL), world.read_json(f"drafts/{RID}.json")["analysis"])
    notes.pdf(RID, who(authn, TEACHER_EMAIL))
    notes.change_classroom(READING_RID, who(authn, TEACHER_EMAIL), "Math 2B")
    j = MemoryJournal()
    student_of = lambda rid: notes.context(rid).get("student_id")  # noqa: E731
    assert backfill_from_audit(world, j, student_of=student_of) == 3
    assert backfill_from_audit(world, j, student_of=student_of) == 3     # re-run
    rows = j.rows()
    assert len(rows) == 3 and {r["source"] for r in rows} == {"backfill"}
    assert [r["action"] for r in rows if r["record_id"] == RID] == ["session_approved", "pdf_downloaded"]
    assert all(r["student_id"] == "stu_101" for r in rows if r["record_id"] == RID)
    assert "@" not in json.dumps(rows)


class _FakeBQ:
    def __init__(self, errors=None):
        self.inserted, self.errors, self.jobs = [], errors or [], []

    def insert_rows_json(self, table, rows, row_ids=None):
        self.inserted.append((table, rows, row_ids))
        return self.errors

    def query(self, sql, job_config=None):
        self.jobs.append((sql, job_config))

        class R:
            def result(self_inner):
                return []
        return R()


def test_bigquery_writer_appends_only_with_insert_ids_and_caps_queries():
    w, r = _FakeBQ(), _FakeBQ()
    j = BigQueryJournal("example-project", "goalvera_journal", "activity",
                        "goalvera_journal_reports", writer=w, reader=r, max_bytes=1 << 30)
    e = make_entry(action="pdf_downloaded", actor_id="uid1", actor_role="teacher", record_id="rec_x",
                   details={"pdf_bytes": 10})
    j.append([e])
    [(table, rows, ids)] = w.inserted
    assert table == "example-project.goalvera_journal.activity" and ids == [e["event_id"]]
    assert rows[0]["details"] == '{"pdf_bytes": 10}'
    with pytest.raises(JournalError):
        BigQueryJournal("p", "d", "t", "r", writer=_FakeBQ(errors=[{"index": 0}])).append([e])

    q = Query.parse({"from": "2026-09-01", "to": "2026-10-02", "student": "stu_101"})
    assert j.query(q) == []
    [(sql, cfg)] = r.jobs
    assert "`example-project.goalvera_journal_reports.activity`" in sql   # the view, not the table
    assert "stu_101" not in sql and "@student_id" in sql                              # parameters, never inlined
    assert cfg.maximum_bytes_billed == 1 << 30
    assert not any(word in sql.upper() for word in ("DELETE", "UPDATE", "INSERT", "MERGE", "TRUNCATE"))
    assert "occurred_at >= TIMESTAMP(@frm)" in sql            # partition filter: scans only the asked-for days
    assert "QUALIFY ROW_NUMBER() OVER (PARTITION BY event_id" in sql   # a doubled insert is reported once
    assert {p.name: p.value for p in cfg.query_parameters}["upto"] in ("2026-10-02", __import__("datetime").date(2026, 10, 2))


# ---------------------------------------------------------------- Oct 7 2026: Journal "error 502, INTERNAL"

class _Forbidden(Exception):
    """Shaped like google.api_core.exceptions.Forbidden (code + errors[0].reason)."""
    code = 403
    errors = [{"reason": "insufficientPermissions", "message": "Request had insufficient authentication scopes."}]


class _RefusingReader:
    def query(self, sql, job_config=None):
        raise _Forbidden("403 POST jobs: Request had insufficient authentication scopes.")


class ReadFailingJournal(MemoryJournal):
    kind = "bigquery"

    def query(self, q):
        return BigQueryJournal("p", "d", "t", "r", reader=_RefusingReader()).query(q)


def test_reader_token_scope_lets_query_jobs_run():
    # client.query() = jobs.insert, which refuses bigquery.readonly (403, insufficient scopes)
    assert READER_SCOPES == ["https://www.googleapis.com/auth/bigquery"]


def test_bigquery_read_failure_is_mapped_to_codes():
    j = BigQueryJournal("p", "d", "t", "r", reader=_RefusingReader())
    with pytest.raises(JournalReadFailed) as ei:
        j.query(Query.parse({}))
    assert (ei.value.reason, ei.value.google_reason, ei.value.http_status) == \
        ("ACCESS_DENIED", "insufficientPermissions", 403)


def test_journal_read_failure_is_a_useful_503_not_502(world, authn, capsys):
    srv, base = serve(world, authn, ReadFailingJournal())
    try:
        for path in ("/api/admin/journal", "/api/admin/reports/activity.csv?by=person"):
            st, body, _ = call(base, path, tok=tok(ADMIN))
            assert (st, json.loads(body)) == (503, {"error": "JOURNAL_READ_FAILED", "reason": "ACCESS_DENIED"})
        logs = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
        failed = [e for e in logs if e.get("event") == "journal_read_failed"]
        assert failed and failed[0]["error_code"] == "ACCESS_DENIED"
        assert failed[0]["reason"] == "insufficientPermissions" and failed[0]["http_status"] == 403
        assert "scopes" not in json.dumps(logs)            # Google's message text is never logged
        assert call(base, "/api/sessions", tok=tok(TEACHER_EMAIL))[0] == 200   # the rest of the site is fine
    finally:
        srv.shutdown()
