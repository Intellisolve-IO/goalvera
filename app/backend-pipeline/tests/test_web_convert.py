"""Phone pieces become one 16 kHz mono 16-bit WAV on the existing rec/ path.

ffmpeg builds a short m4a and a short webm in the test (no audio is committed).
The board's rec/ handler is not replaced: the WAV name matches REC_RE, and gaps
are carried on the sidecar into the transcript.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline_service.main import REC_RE  # noqa: E402
from pipeline_service.records import build_transcript, gaps_from_sidecar
from pipeline_service.web_convert import convert_web_session
from speech_transcriber.chunking import read_wav_info

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required to build the sample pieces")

SID = "s" + "cd" * 16


def _tone(path: Path, codec: str):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4",
         "-c:a", codec, str(path)],
        check=True, timeout=30,
    )


class Blob:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def download_to_filename(self, path):
        Path(path).write_bytes(self.store[self.name])

    def download_as_bytes(self):
        return self.store[self.name]

    def upload_from_filename(self, path, content_type=None, if_generation_match=None):
        if if_generation_match == 0 and self.name in self.store:
            from google.api_core.exceptions import PreconditionFailed
            raise PreconditionFailed("exists")
        self.store[self.name] = Path(path).read_bytes()

    def delete(self):
        self.store.pop(self.name, None)


class Bucket:
    def __init__(self, store):
        self.store = store

    def blob(self, name):
        return Blob(self.store, name)

    def get_blob(self, name):
        return Blob(self.store, name) if name in self.store else None


def _session(tmp: Path, ext: str, codec: str):
    part = tmp / f"part_0001.{ext}"
    _tone(part, codec)
    store = {}
    store[f"web/{SID}/part_0001.{ext}"] = part.read_bytes()
    manifest = {
        "session_id": SID, "user_id": "TeacherUID123", "device_id": "web-teacheruid123",
        "class_code": "Room-104", "class_label": "Room 104", "started_local": "20261102_183045",
        "start_time": "2026-11-02T18:30:45-0400", "parts": [f"part_0001.{ext}"],
        "gaps": [{"offset_seconds": 134, "duration_seconds": 40}],
    }
    store[f"web/{SID}/session.json"] = json.dumps(manifest).encode()
    store[f"web/{SID}/complete.json"] = json.dumps({"session_id": SID}).encode()
    return store


@pytest.mark.parametrize("ext,codec", [("m4a", "aac"), ("webm", "libopus")])
def test_sample_piece_becomes_a_recorder_wav_and_the_piece_is_deleted(tmp_path, ext, codec):
    store = _session(tmp_path, ext, codec)
    out = convert_web_session(Bucket(store), f"web/{SID}/complete.json")
    wav = out["wav"]
    assert wav == "rec/web-teacheruid123/20261102_183045_Room-104.wav"
    assert REC_RE.match(wav)
    assert f"web/{SID}/part_0001.{ext}" not in store
    info = read_wav_info_bytes(store[wav], tmp_path / "out.wav")
    assert info.is_recorder_format
    assert info.duration_s > 0.2
    side = json.loads(store[wav[:-4] + ".json"])
    assert side["recording_gaps"] == [{"offset_seconds": 134.0, "duration_seconds": 40.0}]
    assert side["markers"][0]["type"] == "Recording paused"
    transcript = build_transcript(
        rid="rec_web_teacheruid123_20261102_183045_Room_104",
        manifest={"class_code": "Room-104", "device_id": "web-teacheruid123", "source_object": wav,
                  "recorded_at_local": "2026-11-02T18:30:45", "duration_seconds": 1},
        turns=[], roster={"class_codes": {"Room 104": {"teacher_id": "t1", "teacher_name": "Alex", "aliases": ["Room-104"]}}},
        billed_seconds=1, speech_location="us", sidecar=side,
    )
    assert gaps_from_sidecar(side) == transcript["recording_gaps"]
    assert transcript["recording_gaps"][0]["duration_seconds"] == 40.0


def read_wav_info_bytes(data: bytes, path: Path):
    path.write_bytes(data)
    with wave.open(str(path)) as w:
        assert w.getnchannels() == 1 and w.getframerate() == 16000 and w.getsampwidth() == 2
    return read_wav_info(str(path))


def test_a_second_delivery_does_not_rewrite_the_wav(tmp_path):
    store = _session(tmp_path, "m4a", "aac")
    bucket = Bucket(store)
    first = convert_web_session(bucket, f"web/{SID}/complete.json")
    wav = store[first["wav"]]
    # put the piece back; a retry must delete it again and leave the WAV bytes alone
    store[f"web/{SID}/part_0001.m4a"] = b"not-audio"
    second = convert_web_session(bucket, f"web/{SID}/complete.json")
    assert second["already"] is True
    assert store[first["wav"]] == wav
    assert f"web/{SID}/part_0001.m4a" not in store


def test_the_event_handler_sends_web_completions_to_convert(monkeypatch):
    import pipeline_service.main as main
    import pipeline_service.web_convert as wc
    seen = {}
    monkeypatch.setattr(wc, "convert_web_session", lambda bucket, name, log=None: seen.setdefault("name", name))
    monkeypatch.setattr(main, "BUCKET", "bucket-1")
    monkeypatch.setattr(main, "bucket", lambda: object())
    res = main.app.test_client().post("/", json={"bucket": "bucket-1", "name": f"web/{SID}/complete.json"})
    assert res.status_code == 204
    assert seen["name"] == f"web/{SID}/complete.json"
    assert REC_RE.match("rec/dev01/20261102_183045_Room-104.wav")
