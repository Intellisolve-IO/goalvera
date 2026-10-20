"""Turn Chirp 3 BatchRecognize JSON output into the transcript shape used by
app/samples/transcript_session_student_a_thu.json (`speaker_turns` with
start/end seconds), keeping word-level timestamps for cite-backs.

Speaker labels stay generic ("Speaker 1", "Speaker 2", ...). Diarization only
separates voices; it does not identify anyone. A person maps labels to names
in the review screen. When a session was split into chunks, labels are
per-chunk (Chirp can't match voices across files), so later chunks are
labelled "Speaker 1 (part 2)" etc. to avoid implying they are the same person.
"""
from __future__ import annotations

from typing import Any

MAX_TURN_GAP_S = 2.0      # same speaker but a long pause -> new turn


def _secs(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    return float(s[:-1]) if s.endswith("s") else float(s)


def words_from_stt_json(doc: dict, offset_s: float = 0.0, part: int = 0) -> list[dict]:
    words: list[dict] = []
    for res in doc.get("results", []):
        alts = res.get("alternatives") or []
        if not alts:
            continue
        for w in alts[0].get("words", []) or []:
            label = w.get("speakerLabel") or "?"
            spk = f"Speaker {label}" if part == 0 else f"Speaker {label} (part {part + 1})"
            words.append({
                "w": w.get("word", ""),
                "s": round(_secs(w.get("startOffset")) + offset_s, 3),
                "e": round(_secs(w.get("endOffset")) + offset_s, 3),
                "speaker": spk,
            })
    return words


def build_speaker_turns(words: list[dict]) -> list[dict]:
    turns: list[dict] = []
    cur: dict | None = None
    for w in words:
        if cur and w["speaker"] == cur["speaker"] and w["s"] - cur["end_seconds"] <= MAX_TURN_GAP_S:
            cur["_words"].append(w)
            cur["end_seconds"] = w["e"]
            continue
        cur = {"speaker": w["speaker"], "start_seconds": w["s"], "end_seconds": w["e"], "_words": [w]}
        turns.append(cur)
    out = []
    for t in turns:
        ws = t.pop("_words")
        t["text"] = " ".join(x["w"] for x in ws).strip()
        t["start_seconds"] = round(t["start_seconds"], 2)
        t["end_seconds"] = round(t["end_seconds"], 2)
        t["words"] = [{"w": x["w"], "s": x["s"], "e": x["e"]} for x in ws]
        out.append(t)
    return out
