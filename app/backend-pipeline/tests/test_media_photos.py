"""Teacher Notes photos in the session analysis and review (tasks#112).

- the photo goes to the AI as an image part, the text part still carries no first name;
- names the AI copies off a photo anyway are scrubbed on our side;
- the parent-email inputs never include photo notes;
- review shows media from the draft (even after approval) and serves the bytes only for that session.
"""
from __future__ import annotations

import io
import json
import sys
import types
from contextlib import redirect_stdout
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

from gemini_analyzer import analyzer  # noqa: E402
from gemini_analyzer.prompt import roster_first_names, scrub_image_insights  # noqa: E402
from review_api.notes import session_media  # noqa: E402

SAMPLES = HERE.parents[2] / "samples"
TRANSCRIPT = json.loads((SAMPLES / "transcript_session_student_a_thu.json").read_text())
FIXTURE = json.loads((SAMPLES / "expected_gemini_output_student_a_thu.json").read_text())
FIXTURE.pop("draft_parent_email", None)
PHOTO = {"media_id": "m0123456789ab", "mime": "image/jpeg", "data": b"\xff\xd8fakejpeg"}


def _live(monkeypatch, reply):
    sent = []

    class Models:
        def generate_content(self, **kw):
            sent.append(kw)
            return types.SimpleNamespace(text=json.dumps(reply))

    monkeypatch.setattr(analyzer, "_client", lambda: types.SimpleNamespace(models=Models()))
    gtypes = types.SimpleNamespace(Part=types.SimpleNamespace(
        from_bytes=lambda data, mime_type: {"inline": data, "mime": mime_type}))
    genai = types.ModuleType("google.genai")
    genai.types = gtypes
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", gtypes)
    import google
    monkeypatch.setattr(google, "genai", genai, raising=False)
    return sent


def test_photo_is_sent_as_image_and_text_has_no_first_name(monkeypatch):
    first = TRANSCRIPT["student_first_name"]
    reply = {**FIXTURE, "image_insights": [
        {"media_id": PHOTO["media_id"], "observations": "The student colored the whole sheet; it is signed Grumpy.",
         "related_goal_ids": []},
        {"media_id": "mffffffffffff", "observations": "invented", "related_goal_ids": []}]}
    sent = _live(monkeypatch, reply)
    out = analyzer.analyze_transcript(TRANSCRIPT, mode="live", images=[PHOTO],
                                      roster={"students": {"s1": {"first_name": "Grumpy"}}})
    contents = sent[0]["contents"]
    assert isinstance(contents, list) and contents[1] == {"inline": PHOTO["data"], "mime": "image/jpeg"}
    assert first not in contents[0] and PHOTO["media_id"] in contents[0]
    assert "never copy any name" in contents[0]
    assert "image_insights" in sent[0]["config"]["response_json_schema"]["properties"]
    ins = out["analysis"]["image_insights"]
    assert [i["media_id"] for i in ins] == [PHOTO["media_id"]]       # unknown ids dropped
    assert "Grumpy" not in ins[0]["observations"] and "a student" in ins[0]["observations"]
    assert ins[0]["observations"].startswith(first)                  # "the student" -> name, our side


def test_no_photos_keeps_the_old_request(monkeypatch):
    sent = _live(monkeypatch, FIXTURE)
    analyzer.analyze_transcript(TRANSCRIPT, mode="live")
    assert isinstance(sent[0]["contents"], str)
    assert "image_insights" not in sent[0]["config"]["response_json_schema"]["properties"]


def test_offline_marks_photos_without_ai():
    out = analyzer.analyze_transcript(TRANSCRIPT, mode="offline", images=[PHOTO])
    assert out["analysis"]["image_insights"][0]["media_id"] == PHOTO["media_id"]


def test_scrub_is_case_sensitive_for_word_names():
    names = roster_first_names({"a": [{"first_name": "Happy"}, {"x": {"first_name": "Doc"}}]})
    assert set(names) == {"Happy", "Doc"}
    got = scrub_image_insights([{"media_id": "m", "observations": "Happy wrote it; the student seemed happy."}], names)
    assert got[0]["observations"] == "a student wrote it; the student seemed happy."


def test_parent_email_inputs_leave_photo_notes_out():
    from review_api import planner_ai
    mask = types.SimpleNamespace(hide=lambda x: x)
    a = {**FIXTURE, "image_insights": [{"media_id": "m1", "observations": "PHOTO-NOTE", "related_goal_ids": []}]}
    out = planner_ai.summary_payload(week_label="w", iep_goals=[], notes=[], mask=mask,
                                     sessions=[{"date": "2026-10-19", "analysis": a}])
    assert "PHOTO-NOTE" not in json.dumps(out) and "image_insights" not in json.dumps(out)


def test_review_media_joins_notes_without_paths():
    d = {"analysis": {"image_insights": [{"media_id": "m1", "observations": "obs", "related_goal_ids": ["G1"]}]},
         "media": [{"media_id": "m1", "kind": "photo", "object": "media/rec_a/m1.jpg", "content_type": "image/jpeg"},
                   {"media_id": "m2", "kind": "video", "object": "media/rec_a/m2.mp4", "content_type": "video/mp4"}]}
    rows = session_media(d)
    assert rows[0]["insights"] == "obs" and rows[1]["insights"] is None
    assert "object" not in json.dumps(rows) and "media/rec_a" not in json.dumps(rows)


def test_media_list_forbidden_returns_empty(monkeypatch):
    """A missing media/ IAM grant must not abort the notes draft (preview smoke, tasks#165)."""
    import pipeline_service.main as main

    class Forbidden(Exception):
        pass

    class Dummy:
        def list_blobs(self, *a, **k):
            raise Forbidden("no media list")

    monkeypatch.setattr(main, "storage_client", lambda: Dummy())
    monkeypatch.setattr(main, "BUCKET", "bucket-1")
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert main.media_list("rec_preview_smoke") == []
        assert main.load_photos("rec_preview_smoke") == []
    events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    assert any(e.get("event") == "media_list_unavailable" and e.get("error_type") == "Forbidden"
               and e.get("recording_id") == "rec_preview_smoke" for e in events)


def test_load_photos_forbidden_on_download_returns_empty(monkeypatch):
    import pipeline_service.main as main

    class Forbidden(Exception):
        pass

    monkeypatch.setattr(main, "media_list", lambda rid: [{
        "media_id": "m0123456789ab", "kind": "photo", "object": "media/rec_a/m.jpg",
        "content_type": "image/jpeg", "size_bytes": 12, "uploaded_at": "2026-11-11T00:00:00Z",
    }])

    class Blob:
        def download_as_bytes(self):
            raise Forbidden("no media read")

    monkeypatch.setattr(main, "bucket", lambda: types.SimpleNamespace(blob=lambda name: Blob()))
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert main.load_photos("rec_a") == []
    events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    assert any(e.get("event") == "media_list_unavailable" and e.get("error_type") == "Forbidden" for e in events)
