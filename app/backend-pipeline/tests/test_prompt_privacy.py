"""Privacy rule: no student first name reaches the AI.

The prompt says "the student"; the first name is replaced in the transcript and
IEP goal text before sending, and put back into the returned notes on our side.
The live call is exercised end to end with a fake Gemini client that records
everything it was handed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

from gemini_analyzer import analyzer  # noqa: E402
from gemini_analyzer.prompt import (SYSTEM_INSTRUCTION, build_user_prompt, redact_first_name,  # noqa: E402
                                    reinsert_first_name)

SAMPLES = HERE.parents[2] / "samples"
TRANSCRIPT = json.loads((SAMPLES / "transcript_session_student_a_thu.json").read_text())
FIXTURE = json.loads((SAMPLES / "expected_gemini_output_student_a_thu.json").read_text())


class _FakeModels:
    def __init__(self, sent, reply):
        self.sent, self.reply = sent, reply

    def generate_content(self, **kw):
        self.sent.append(kw)

        class R:
            text = json.dumps(self.reply)
        return R()


class _FakeClient:
    def __init__(self, sent, reply):
        self.models = _FakeModels(sent, reply)


def _ai_reply():
    """What a model that only saw "the student" would write back."""
    return json.loads(redact_first_name(json.dumps(FIXTURE), "Marcus"))


def test_sample_really_contains_the_name():
    blob = json.dumps(TRANSCRIPT)
    assert TRANSCRIPT["student_first_name"] == "Marcus" and blob.count("Marcus") >= 5


def test_no_first_name_reaches_the_ai(monkeypatch):
    sent = []
    monkeypatch.setattr(analyzer, "_client", lambda: _FakeClient(sent, _ai_reply()))
    out = analyzer.analyze_transcript(TRANSCRIPT, mode="live")

    [call] = sent
    everything_sent = json.dumps(call, default=str)
    assert "marcus" not in everything_sent.casefold()
    assert "Student: the student" in call["contents"]
    assert '"the student"' in call["config"]["system_instruction"]
    assert call["config"]["system_instruction"] == SYSTEM_INSTRUCTION

    # first name put back after the AI returns, on our side
    text = json.dumps(out["analysis"])
    assert "Marcus" in text and "the student" not in text.replace("The student", "the student")
    assert out["analysis"]["draft_parent_email"].count("Marcus") >= 1


def test_name_variants_are_redacted():
    t = {**TRANSCRIPT, "speaker_turns": [
        {"speaker": "Speaker 1", "start_seconds": 0, "text": "MARCUS, sit with me. marcus's turn. Marcus?"},
        {"speaker": "Speaker 2", "start_seconds": 4, "text": "Marcuses is not a name; Marcusville neither."},
    ]}
    prompt = build_user_prompt(t)
    assert "MARCUS" not in prompt and "marcus's" not in prompt and "Marcus?" not in prompt
    assert "the student, sit with me" in prompt and "the student's turn" in prompt
    assert "Marcuses" in prompt and "Marcusville" in prompt     # whole words only


def test_reinsert_and_no_name_cases():
    assert reinsert_first_name({"a": ["The student smiled; the student's turn."]}, "Marcus") == \
        {"a": ["Marcus smiled; Marcus's turn."]}
    assert reinsert_first_name("the students lined up", "Marcus") == "the students lined up"
    # sessions with no student on the roster (first name "Student"): nothing to put back
    assert reinsert_first_name("The student smiled", "Student") == "The student smiled"
    assert reinsert_first_name("The student smiled", None) == "The student smiled"
    assert "Student: the student" in build_user_prompt({"speaker_turns": [], "student_first_name": None})
