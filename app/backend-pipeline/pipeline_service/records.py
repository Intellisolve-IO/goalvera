"""Pure builders for the pipeline's stored records (no cloud calls), so the
pipeline service, the local demo seeder and the tests all produce exactly the
same transcript / draft shapes.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any

_PIPE = Path(__file__).resolve().parents[1]
if str(_PIPE) not in sys.path:
    sys.path.insert(0, str(_PIPE))

from common.classcodes import resolve_session  # noqa: E402

# Recorder marker types are a fixed list on the device (firmware/recorder-idf/main/marker_types.h).
# Anything else in a sidecar is dropped, so no free text can ride along.
MARKER_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z /-]{0,31}$")
INCIDENT_MARKER_TYPES = {"Incident", "Behavior Event"}
_START = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:?\d{2})$")


def markers_from_sidecar(sidecar: dict | None, duration_s: float | None = None) -> list[dict]:
    """Keep only {type, offset_seconds} from the recorder sidecar's markers list."""
    out: list[dict] = []
    for m in (sidecar or {}).get("markers") or []:
        if not isinstance(m, dict):
            continue
        typ = str(m.get("type", "")).strip()
        off = m.get("offset_ms")
        if not MARKER_TYPE_RE.match(typ) or not isinstance(off, (int, float)) or off < 0:
            continue
        sec = round(off / 1000.0, 1)
        if duration_s is not None and sec > duration_s + 1:
            continue
        mark = {"type": typ, "offset_seconds": sec}
        wall = m.get("wall_time")
        if typ.lower() == "bug" and isinstance(wall, str) and _START.match(wall):
            mark["wall_time"] = wall          # firmware 0.6.7 Bug marker: its own clock time (may be null)
        out.append(mark)
    out.sort(key=lambda m: m["offset_seconds"])
    return out[:100]


# Fields that come from the roster/teacher step (resolve_session). The backfill
# script rewrites exactly these on existing records, nothing else.
ROSTER_FIELDS = (
    "class_code", "class_code_file", "class_key", "needs_class_assignment",
    "student_id", "student_first_name", "student_last_initial",
    "teacher_id", "teacher_name", "teacher_source",
)


def gaps_from_sidecar(sidecar: dict | None) -> list[dict]:
    """Pauses from a phone recording: offset into the audio, and how long the mic was closed."""
    out: list[dict] = []
    for g in (sidecar or {}).get("recording_gaps") or []:
        if not isinstance(g, dict):
            continue
        off, dur = g.get("offset_seconds"), g.get("duration_seconds")
        if isinstance(off, bool) or isinstance(dur, bool):
            continue
        if not isinstance(off, (int, float)) or not isinstance(dur, (int, float)):
            continue
        if off < 0 or dur < 0 or off > 6 * 3600 or dur > 2 * 3600:
            continue
        out.append({"offset_seconds": round(float(off), 1), "duration_seconds": round(float(dur), 1)})
    return out[:50]


def build_transcript(
    *, rid: str, manifest: dict, turns: list[dict], roster: dict, billed_seconds: int,
    speech_location: str, markers: list[dict] | None = None, sidecar: dict | None = None,
    recording_gaps: list[dict] | None = None,
) -> dict[str, Any]:
    """roster = the whole roster document (class_codes + teachers)."""
    ctx = resolve_session(roster, filename_code=manifest["class_code"], sidecar=sidecar)
    markers = markers or []
    return {
        "recording_id": rid,
        **{k: ctx[k] for k in ROSTER_FIELDS},
        "device_id": manifest["device_id"],
        "source_object": manifest.get("source_object"),
        "timestamp_start": manifest["recorded_at_local"],
        "duration_seconds": int(round(manifest.get("duration_seconds", 0))),
        **recorder_facts(sidecar),
        # Teacher-pressed "Incident" markers become the incident flags the analyzer
        # writes ABC notes for; all markers are kept for the review screen and PDF.
        "incident_flags": [int(m["offset_seconds"]) for m in markers if m["type"] in INCIDENT_MARKER_TYPES],
        "markers": markers,
        "recording_gaps": recording_gaps if recording_gaps is not None else gaps_from_sidecar(sidecar),
        "iep_goals": ctx["iep_goals"],
        "speaker_turns": turns,
        "_meta": {
            "source": "speech-to-text-v2/chirp_3", "location": speech_location,
            "diarization": True, "word_timestamps": True, "chunks": len(manifest.get("parts", [])) or 1,
            "billed_audio_seconds": billed_seconds,
            "speaker_labels": "Generic labels from speaker separation; a person maps them to names in review.",
        },
    }


SESSION_KEYS = ROSTER_FIELDS + ("device_id", "timestamp_start", "duration_seconds", "markers", "source_object",
                                "start_time", "start_time_valid", "firmware_version", "recording_gaps")
_FW = re.compile(r"^[A-Za-z0-9._+\-]{1,40}$")


def recorder_facts(sidecar: dict | None) -> dict[str, Any]:
    """From the recorder's sidecar, only: its own start time with UTC offset (firmware strftime
    %z), whether its clock was set (time_valid), and the firmware version. Used for the
    incidents' clock time and for recorder Bug markers (review service). Anything else: dropped."""
    sc = sidecar or {}
    st, fw, ok = sc.get("start_time"), sc.get("firmware_version"), sc.get("time_valid")
    return {"start_time": st if isinstance(st, str) and _START.match(st) else None,
            "start_time_valid": ok if isinstance(ok, bool) else None,
            "firmware_version": fw if isinstance(fw, str) and _FW.match(fw) else None}


def build_draft(transcript: dict, wrapped: dict, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.timezone.utc)
    rid = transcript["recording_id"]
    return {
        **wrapped,
        "status": "pending_review",          # human-in-the-loop: a teacher must review/approve
        "created_at": now.isoformat(),
        "session": {k: transcript.get(k) for k in SESSION_KEYS},
        "incident_flag_count": len(wrapped["analysis"].get("incident_analysis", [])),
        "transcript_object": f"transcripts/{rid}.json",
    }
