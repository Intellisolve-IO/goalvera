"""Class codes and the demo roster - the ONE place they are normalized (tasks#35).

The recorder writes the class two ways: the file name token is sanitized by
the firmware (spaces -> "-", other punctuation dropped, e.g. "Reading-3A"),
while the sidecar keeps the label as chosen on the device ("Reading 3A").
Both must land on the same roster entry, so every lookup goes through
class_key(): case-insensitive, and spaces, hyphens and underscores are the same.

Resolution rules (resolve_session):
  class    sidecar class_code first (the label the teacher picked), then the
           file-name token; the first one found in the roster wins.
  teacher  the sidecar's teacher if it matches a roster teacher (name or device
           label); otherwise the roster's teacher for the resolved class.
  unknown  class not in the roster -> needs_class_assignment = True. Nothing is
           guessed; the review list shows the flag and Approve stays blocked
           until a class is assigned (infra/backfill_session_roster.py --class).
"""
from __future__ import annotations

import re
from typing import Any

_DROP = re.compile(r"[^0-9a-z \-_]+")
_SEP = re.compile(r"[ \-_]+")


def class_key(code: Any) -> str | None:
    """'Reading 3A', 'reading-3a', 'READING_3A', ' Reading -- 3A ' -> 'reading-3a'."""
    if not isinstance(code, str):
        return None
    k = _SEP.sub("-", _DROP.sub("", code.strip().casefold())).strip("-")
    return k or None


def _name_key(name: Any) -> str | None:
    if not isinstance(name, str):
        return None
    k = re.sub(r"\s+", " ", re.sub(r"[^0-9a-z .\-']+", "", name.casefold())).strip()
    return k or None


def find_class(roster: dict, code: Any) -> tuple[str | None, dict | None]:
    """Return (roster display code, entry) for any spelling of a known class."""
    k = class_key(code)
    if not k:
        return None, None
    for display, entry in (roster.get("class_codes") or {}).items():
        keys = {class_key(display), *(class_key(a) for a in entry.get("aliases", []))}
        if k in keys:
            return display, entry
    return None, None


def find_teacher(roster: dict, label: Any) -> tuple[str | None, str | None]:
    """Match a device teacher label to a roster teacher (by name or device label)."""
    k = _name_key(label)
    if not k:
        return None, None
    for tid, t in (roster.get("teachers") or {}).items():
        names = {_name_key(t.get("teacher_name")), *(_name_key(x) for x in t.get("device_labels", []))}
        if k in names:
            return tid, t.get("teacher_name")
    return None, None


def resolve_session(roster: dict, *, filename_code: str | None, sidecar: dict | None) -> dict:
    """Class, student and teacher context for one recording (see module doc)."""
    sidecar = sidecar or {}
    side_code = sidecar.get("class_code") if isinstance(sidecar.get("class_code"), str) else None
    display, entry = None, None
    for cand in (side_code, filename_code):
        display, entry = find_class(roster, cand)
        if entry is not None:
            break
    entry = entry or {}
    known = bool(display)

    teacher_id, teacher_name = find_teacher(roster, sidecar.get("teacher"))
    source = "device_sidecar" if teacher_id else None
    if not teacher_id and known and entry.get("teacher_id"):
        teacher_id, teacher_name, source = entry["teacher_id"], entry.get("teacher_name"), "class_roster"

    return {
        # what the review screen / PDF shows: the roster's spelling when known
        "class_code": display or side_code or filename_code,
        "class_code_file": filename_code,
        "class_key": class_key(display or side_code or filename_code),
        "needs_class_assignment": not known,
        "student_id": entry.get("student_id"),
        "student_first_name": entry.get("student_first_name", "Student"),
        "student_last_initial": entry.get("student_last_initial", ""),
        "teacher_id": teacher_id,
        "teacher_name": teacher_name,
        "teacher_source": source,
        "iep_goals": entry.get("iep_goals", []),
    }
