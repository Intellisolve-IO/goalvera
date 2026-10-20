"""Offline unit tests (no cloud): chunking, turn building, safe logging.
Run:  python -m pytest app/backend-pipeline/tests -q   (or python tests/test_pipeline_units.py)
"""
from __future__ import annotations

import array
import io
import json
import os
import sys
import tempfile
import wave
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.safelog import log  # noqa: E402
from speech_transcriber import build_speaker_turns, plan_chunks, words_from_stt_json, write_chunks  # noqa: E402


def _tone_wav(path: str, seconds: int, quiet_at: list[int]) -> None:
    rate = 16000
    pcm = array.array("h", [3000 if (i // 40) % 2 else -3000 for i in range(rate * seconds)])
    for q in quiet_at:  # 1 s of silence
        for i in range(q * rate, (q + 1) * rate):
            pcm[i] = 0
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm.tobytes())


def test_chunking_prefers_quiet_spot():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "a.wav")
        _tone_wav(p, 130, quiet_at=[52])
        bounds = plan_chunks(p, chunk_seconds=60)
        assert bounds[0][0] == 0 and bounds[-1][1] == 130 * 16000
        assert 52 * 16000 <= bounds[0][1] <= 53 * 16000, bounds   # cut inside the silence
        assert all(b - a <= 60 * 16000 for a, b in bounds)
        parts = write_chunks(p, bounds, os.path.join(d, "c"))
        assert len(parts) == len(bounds) == 3
        assert parts[1][1] == bounds[1][0] / 16000


def test_short_file_single_chunk():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "a.wav")
        _tone_wav(p, 5, [])
        assert plan_chunks(p) == [(0, 5 * 16000)]


def test_turns_and_chunk_offsets():
    doc = {"results": [{"alternatives": [{"words": [
        {"word": "Hello", "endOffset": "0.5s", "speakerLabel": "1"},
        {"word": "class.", "startOffset": "0.5s", "endOffset": "1s", "speakerLabel": "1"},
        {"word": "Hi.", "startOffset": "1.2s", "endOffset": "1.5s", "speakerLabel": "2"},
        {"word": "Later", "startOffset": "9s", "endOffset": "9.5s", "speakerLabel": "2"},
    ]}]}]}
    turns = build_speaker_turns(words_from_stt_json(doc))
    assert [t["speaker"] for t in turns] == ["Speaker 1", "Speaker 2", "Speaker 2"]  # long gap splits
    assert turns[0]["text"] == "Hello class." and turns[0]["words"][1]["s"] == 0.5
    t2 = build_speaker_turns(words_from_stt_json(doc, offset_s=1140, part=1))
    assert t2[0]["speaker"] == "Speaker 1 (part 2)" and t2[0]["start_seconds"] == 1140.0


def test_safelog_blocks_text_and_names():
    buf = io.StringIO()
    with redirect_stdout(buf):
        log("x", recording_id="rec_atl_demo_01_20260924_101400_RM12", device_id="Marcus",
            error_code="BAD_FORMAT", text="Can I have my headphones please?", reason="Marcus said hi")
    entry = json.loads(buf.getvalue())
    assert entry["recording_id"] == "rec_atl_demo_01_20260924_101400_RM12"
    assert entry["device_id"] == "<redacted>"          # name-like value in an id field
    assert "text" not in entry                          # key not allow-listed
    assert entry["reason"] == "<redacted>"              # free text with spaces


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
