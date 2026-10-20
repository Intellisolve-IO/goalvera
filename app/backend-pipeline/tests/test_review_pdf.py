"""Tests for the approve -> PDF download slice (offline; local store, no cloud).

  python -m pytest app/backend-pipeline/tests -q
Needs: reportlab, pypdf, jsonschema (review_api/requirements.txt).
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
import threading
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE.parents[2] / "tools"))

from pypdf import PdfReader  # noqa: E402

from pipeline_service.records import markers_from_sidecar  # noqa: E402
from review_api.notes import ApiError, Notes  # noqa: E402
from review_api.server import build_server  # noqa: E402
from review_api.store import LocalStore  # noqa: E402
from seed_local_session import RID, seed  # noqa: E402

TEACHER = "usr_demo_sarah"


def pdf_text(data: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)


def all_files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


@pytest.fixture()
def notes(tmp_path):
    seed(str(tmp_path))
    return Notes(LocalStore(tmp_path))


# ---------------------------------------------------------------- records / markers

def test_seeded_session_is_pending_with_speakers_and_markers(notes):
    s = notes.session(RID)
    assert s["status"] == "pending_review" and s["approved_at"] is None
    assert s["teacher_name"] == "Sarah Jenkins" and s["class_code"] == "RM12"
    assert [m["type"] for m in s["markers"]] == ["Incident", "Teacher Notes"]
    t = notes.store.read_json(f"transcripts/{RID}.json")
    assert len({x["speaker"] for x in t["speaker_turns"]}) == 3
    assert t["incident_flags"] == [49]          # only "Incident" markers become ABC flags


def test_sidecar_markers_drop_free_text_and_bad_values():
    side = {"markers": [
        {"type": "Incident", "offset_ms": 1000},
        {"type": "Marcus hit Sophia at the table!!", "offset_ms": 2000},   # free text -> dropped
        {"type": "Other", "offset_ms": -5},
        {"type": "Other", "offset_ms": 999999999},                          # beyond the recording
        {"type": "Teacher Notes", "offset_ms": 500, "note": "extra field ignored"},
    ]}
    assert markers_from_sidecar(side, duration_s=60) == [
        {"type": "Teacher Notes", "offset_seconds": 0.5}, {"type": "Incident", "offset_seconds": 1.0}]
    assert markers_from_sidecar(None) == []


# ---------------------------------------------------------------- approve / PDF logic

def test_pdf_refused_until_a_person_approves(notes):
    with pytest.raises(ApiError) as e:
        notes.pdf(RID, TEACHER)
    assert (e.value.http, e.value.code) == (409, "NOT_APPROVED")
    assert notes.audit_trail(RID) == []


def test_approve_rules(notes):
    a = notes.session(RID)["processed_analysis"]
    for actor, code in [(None, "REVIEWER_REQUIRED"), ("Sarah Jenkins", "REVIEWER_REQUIRED"),
                        ("usr_someone_else", "NOT_SESSION_TEACHER")]:
        with pytest.raises(ApiError) as e:
            notes.approve(RID, actor, a)
        assert e.value.code == code
    with pytest.raises(ApiError) as e:
        notes.approve(RID, TEACHER, {"goal_evaluations": "nope"})
    assert e.value.code == "INVALID_NOTES"
    rec = notes.approve(RID, TEACHER, a)
    assert rec["approved_by_name"] == "Sarah Jenkins" and rec["edited"] is False
    with pytest.raises(ApiError) as e:
        notes.approve(RID, TEACHER, a)
    assert (e.value.http, e.value.code) == (409, "ALREADY_APPROVED")
    assert notes.session(RID)["status"] == "approved"


def test_pdf_contents_and_edits_are_what_was_approved(notes):
    a = copy.deepcopy(notes.session(RID)["processed_analysis"])
    a["general_session_summary"] = "Teacher-edited summary \u2014 calm return after the tone."
    notes.approve(RID, TEACHER, a)
    data, fname = notes.pdf(RID, TEACHER)
    assert data.startswith(b"%PDF") and fname.endswith(".pdf") and "Marcus" not in fname
    txt = pdf_text(data)
    for needle in ["Approved session notes", "Approved by Sarah Jenkins at", "Classroom", "RM12",
                   "Mon, Sep 28, 2026, 4:45 PM EDT", "Teacher", "Sarah Jenkins", "Markers",
                   "00:49", "Incident", "01:32", "Teacher Notes", "IEP goal evaluations",
                   "Can I have my headphones, please?", "Teacher-edited summary",
                   "not sent", "SAMPLE DATA", RID]:
        assert needle in txt, needle
    assert "Teacher-edited summary \u2014 calm return" in txt     # em dash survives the PDF font
    assert notes.session(RID)["processed_analysis"]["general_session_summary"].startswith("Teacher-edited")


def test_no_markers_section_when_there_are_none(tmp_path):
    seed(str(tmp_path), with_markers=False)
    n = Notes(LocalStore(tmp_path))
    n.approve(RID, TEACHER, n.session(RID)["processed_analysis"])
    txt = pdf_text(n.pdf(RID, TEACHER)[0])
    assert "Markers" not in txt and "Approved by Sarah Jenkins" in txt


def test_section_headings_stay_with_their_content(notes):
    """A heading must never sit alone at the bottom of a page (seen with the ABC section)."""
    a = copy.deepcopy(notes.session(RID)["processed_analysis"])
    a["general_session_summary"] = "Long summary. " * 40          # push later sections around
    a["incident_analysis"] = [{"timestamp_seconds": 49, "antecedent": "Tone announced. " * 6,
                               "behavior": "Said it was too loud. " * 6,
                               "consequence_intervention": "Offered headphones. " * 6}]
    for pad in range(0, 60, 6):                                     # try many page-break positions
        a["goal_evaluations"][0]["analysis"] = "Detail. " * (20 + pad * 8)
        notes.store.write_json(f"approvals/{RID}.json", {
            "approved_by_name": "Sarah Jenkins", "approved_at": "2026-09-29T17:00:00+00:00", "analysis": a})
        pages = [p.extract_text() for p in PdfReader(io.BytesIO(notes.pdf(RID, TEACHER)[0])).pages]
        for heading, content in [("Incident notes (ABC)", "Antecedent"), ("IEP goal evaluations", "g1"),
                                 ("Session summary", "Long summary")]:
            page = next(p for p in pages if heading in p)
            assert content in page.split(heading, 1)[1], (heading, pad)


def test_every_download_is_audited_and_no_pdf_is_stored(notes, tmp_path):
    notes.approve(RID, TEACHER, notes.session(RID)["processed_analysis"])
    before = all_files(tmp_path)
    buf = io.StringIO()
    with redirect_stdout(buf):
        d1, _ = notes.pdf(RID, TEACHER)
        d2, _ = notes.pdf(RID, TEACHER)
    new = all_files(tmp_path) - before
    assert len(new) == 2 and all(n.startswith(f"audit/{RID}/") and "_pdf_downloaded_" in n for n in new)
    assert not any(n.endswith(".pdf") for n in all_files(tmp_path))
    events = [e for e in notes.audit_trail(RID) if e["event"] == "pdf_downloaded"]
    assert {e["pdf_sha256"] for e in events} == {hashlib.sha256(d1).hexdigest(), hashlib.sha256(d2).hexdigest()}
    assert all(e["actor_id"] == TEACHER for e in events)
    # audit records and log lines: ids only - no names, no notes text
    blob = json.dumps(notes.audit_trail(RID)) + buf.getvalue()
    for bad in ["Sarah", "Jenkins", "Marcus", "headphones"]:
        assert bad not in blob, bad


def test_only_the_sessions_teacher_can_download(notes):
    notes.approve(RID, TEACHER, notes.session(RID)["processed_analysis"])
    with pytest.raises(ApiError) as e:
        notes.pdf(RID, "usr_someone_else")
    assert e.value.code == "NOT_SESSION_TEACHER"


# ---------------------------------------------------------------- HTTP surface

@pytest.fixture()
def api(tmp_path):
    seed(str(tmp_path))
    srv = build_server(LocalStore(tmp_path), 0, "test")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def req(url, method="GET", body=None, headers=None):
    h = {"X-Reviewer-Id": TEACHER, **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    r = urllib.request.Request(url, data=data, method=method, headers={k: v for k, v in h.items() if v is not None})
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_http_approve_then_download(api):
    base = f"{api}/api/sessions/{RID}"
    st, _, body = req(f"{base}/pdf")
    assert st == 409 and json.loads(body)["error"] == "NOT_APPROVED"
    st, _, body = req(f"{api}/api/sessions")
    analysis = json.loads(body)["sessions"][0]["processed_analysis"]

    st, _, _ = req(f"{base}/approve", "POST", {"analysis": analysis}, {"Origin": "https://evil.example"})
    assert st == 403                                    # only the local review UI may call
    st, _, _ = req(f"{base}/approve", "POST", {"analysis": analysis}, {"Content-Type": "text/plain"})
    assert st == 415                                    # forces a CORS preflight for browsers
    st, _, body = req(f"{base}/approve", "POST", {"analysis": analysis}, {"Origin": "http://localhost:5173"})
    assert st == 200 and json.loads(body)["approved_by_name"] == "Sarah Jenkins"

    st, _, _ = req(f"{base}/pdf", headers={"X-Reviewer-Id": None})
    assert st == 401
    st, h, data = req(f"{base}/pdf")
    assert st == 200 and h["Content-Type"] == "application/pdf" and data.startswith(b"%PDF")
    assert h["Content-Disposition"].startswith("attachment;") and h["Cache-Control"] == "no-store"
    st, _, body = req(f"{base}/audit")
    ev = [e["event"] for e in json.loads(body)["events"]]
    assert ev == ["session_approved", "pdf_downloaded"]

    st, _, body = req(f"{api}/api/sessions")
    s = json.loads(body)["sessions"][0]
    assert s["status"] == "approved" and s["approved_by_name"] == "Sarah Jenkins" and s["approved_at"]


def test_http_rejects_bad_ids_and_methods(api):
    assert req(f"{api}/api/sessions/..%2Fetc/pdf")[0] == 400
    assert req(f"{api}/api/sessions/rec_nope/pdf")[0] == 404
    assert req(f"{api}/api/sessions/{RID}/pdf", "POST", {})[0] == 405


def test_sidecar_under_rec_forbidden_still_lists_sessions(tmp_path):
    """Hosted: review-web can't read rec/ (403). A draft that needs the sidecar for its
    markers must still list (markers empty) instead of failing the whole session list."""
    seed(str(tmp_path))

    class Forbidden(Exception):
        pass

    class NoRec(LocalStore):
        def read_json(self, name):
            if name.startswith("rec/"):
                raise Forbidden(name)
            return super().read_json(name)

    store = NoRec(tmp_path)
    d = store.read_json(f"drafts/{RID}.json")
    d.setdefault("session", {}).pop("markers", None)
    store.write_json(f"drafts/{RID}.json", d)
    t = store.read_json(f"transcripts/{RID}.json")
    t.pop("markers", None)
    store.write_json(f"transcripts/{RID}.json", t)
    s = Notes(store).list_sessions()
    assert len(s) == 1
    assert s[0]["markers"] == []
