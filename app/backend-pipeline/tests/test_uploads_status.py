"""Processing rows: a recording shows as soon as its upload lands,
with its stage from the pipeline's manifest, until its notes exist; failures show a clear state.
Nothing is read from the audio and nothing is written.

  python -m pytest app/backend-pipeline/tests/test_uploads_status.py -q
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time

from test_hosted_auth import call  # noqa: F401  (sets sys.path)
from test_materials import RID, seed
from review_api.journal import MemoryJournal
from review_api.notes import Notes
from review_api.server import build_server
from review_api.store import LocalStore
from review_api.uploads_status import UploadsStatus, failure_text

NOW = dt.datetime.now(dt.timezone.utc)


def wav(store, root, name, age_min=1):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"RIFF")
    t = time.time() - age_min * 60
    os.utime(p, (t, t))


def manifest(store, rid, **kw):
    store.write_json(f"work/{rid}/manifest.json", {"recording_id": rid, **kw})


def test_states_from_upload_to_ready(tmp_path):
    store = LocalStore(tmp_path)
    seed(store)                                                    # RID has a draft: not listed
    wav(store, tmp_path, "rec/dev01/20261009_101500_Room104.wav")
    wav(store, tmp_path, "rec/dev01/20261010_090000_RM104.wav")            # just uploaded
    wav(store, tmp_path, "rec/dev01/20261010_091000_RM104.wav")
    manifest(store, "rec_dev01_20261010_091000_RM104", status="transcribing", duration_seconds=300)
    wav(store, tmp_path, "rec/dev01/20261010_092000_RM104.wav")
    manifest(store, "rec_dev01_20261010_092000_RM104", status="transcribing")
    store.write_json("work/rec_dev01_20261010_092000_RM104/analysis.lock", {"at": 1})
    wav(store, tmp_path, "rec/dev01/20261010_093000_RM104.wav")
    manifest(store, "rec_dev01_20261010_093000_RM104", status="failed", error_code="STT_3")
    wav(store, tmp_path, "rec/dev01/20261010_094000_Zoo.wav", age_min=30)  # never started
    wav(store, tmp_path, "rec/dev01/20261001_094000_RM104.wav", age_min=60 * 24 * 9)   # too old
    v = UploadsStatus(Notes(store)).view()
    got = {r["recording_id"]: r for r in v["uploads"]}
    assert RID not in got and "rec_dev01_20261001_094000_RM104" not in got
    assert got["rec_dev01_20261010_090000_RM104"]["state"] == "uploaded"
    assert got["rec_dev01_20261010_090000_RM104"]["class_code"] == "Room 104"
    assert got["rec_dev01_20261010_091000_RM104"]["state"] == "transcribing"
    assert got["rec_dev01_20261010_092000_RM104"]["state"] == "drafting"
    f = got["rec_dev01_20261010_093000_RM104"]
    assert f["state"] == "failed" and f["error_text"] == "Transcription failed." and f["processing"] is False
    s = got["rec_dev01_20261010_094000_Zoo"]
    assert s["state"] == "failed" and s["error_code"] == "NO_START" and s["needs_class_assignment"] is True
    assert v["processing"] == 3
    assert not store.list("audit/") and not store.list("journal/")                 # nothing written
    # the notes land: the row goes away (the normal session row replaces it)
    store.write_json("drafts/rec_dev01_20261010_092000_RM104.json", {"recording_id": "x"})
    assert "rec_dev01_20261010_092000_RM104" not in {r["recording_id"] for r in UploadsStatus(Notes(store)).rows()}


def test_failure_texts():
    assert failure_text("NOT_WAV") == "The file isn't a WAV recording."
    assert failure_text("ANALYZER_FAILED") == "Drafting the notes failed."
    assert failure_text("WHAT") == "Processing failed."


def test_no_bucket_access_shows_nothing(tmp_path):
    class NoList(LocalStore):
        def list_times(self, prefix):
            raise PermissionError("403")
    store = NoList(tmp_path)
    seed(store)
    assert UploadsStatus(Notes(store)).view()["uploads"] == []


def test_http_route(tmp_path):
    store = LocalStore(tmp_path)
    seed(store)
    wav(store, tmp_path, "rec/dev01/20261010_090000_RM104.wav")
    srv = build_server(store, 0, "local", journal=MemoryJournal())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        st, body, _ = call(base, "/api/uploads/status", headers={"X-Reviewer-Id": "stf_0001"})
        assert st == 200 and json.loads(body)["uploads"][0]["state"] == "uploaded"
    finally:
        srv.shutdown()
