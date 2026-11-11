"""One incident's printable report (Oct 7: "Incident 1 · 0:03" + Download PDF).

Incidents are numbered in order within each recording, earliest first: the review
list, the session's ABC section, the approved-notes PDF and this print view all use
incident_numbers() so the same incident always has the same number.

The print view itself is the browser's: the review UI opens the report in a
print-ready layout and calls window.print() (Save as PDF). Nothing is rendered or
stored on the server. The server builds the report's content here, so what can
end up on paper is decided in one place:

  * student: first name + last initial only (privacy rule)
  * never the school id, full last name or date of birth, in the body or in the
    document title (the title becomes the PDF's file name). Any of those found in
    the incident text is replaced; if one still shows up the print is refused
    (PrintBlocked), the same way the activity report is refused (REPORT_BLOCKED).
"""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

FOOTER = "Confidential, contains student information"
DRAFT_LINE = "Draft, not yet approved by the teacher"   # drafts print with a DRAFT watermark
TEXT_FIELDS = (("Antecedent", "antecedent"), ("Behavior", "behavior"),
               ("Consequence / intervention", "consequence_intervention"))
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
# roster / session keys that hold something that must never be printed
_LAST = re.compile(r"(^|_)(last_?name|surname|family_?name)$", re.I)
_OTHER = re.compile(r"(^|_)(school_?id|student_?number|sis_?id|state_?id|dob|date_?of_?birth|birth_?date)$", re.I)
_TS = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


class PrintBlocked(Exception):
    pass


def clock(seconds) -> str:
    """m:ss, or h:mm:ss from an hour on. 3 -> 0:03, 75 -> 1:15, 3725 -> 1:02:05."""
    try:
        s = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError):
        s = 0
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _offset(inc: dict) -> float:
    try:
        return float(inc.get("timestamp_seconds") or 0)
    except (TypeError, ValueError):
        return 0.0


def incident_numbers(incidents: list) -> list[tuple[int, dict]]:
    """[(1, earliest), (2, next), ...]; ties keep their original order."""
    order = sorted(range(len(incidents or [])), key=lambda i: (_offset(incidents[i]), i))
    return [(n, incidents[i]) for n, i in enumerate(order, start=1)]


def incident_label(number: int, seconds) -> str:
    return f"Incident {number} \u00b7 {clock(seconds)}"


# ---------------------------------------------------------------- clock time
# The recorder names each file with its local start time, YYYYMMDD_HHMMSS (firmware/recorder-idf
# audio_rec.c build_recording_path), only when its clock is set (RTC / SNTP; otherwise NOTIME_...,
# which the pipeline never accepts). The firmware runs on Eastern time (wifi_time.c:
# TZ=EST5EDT,M3.2.0,M11.1.0), so the file name is Eastern wall-clock time without an offset. The
# pipeline copies it into session.timestamp_start. Newer drafts may also carry the sidecar's own
# start_time (with its UTC offset) and time_valid; those win when present.
ET = ZoneInfo("America/New_York")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")


def _parse(ts) -> dt.datetime | None:
    m = _ISO.match(ts.strip()) if isinstance(ts, str) else None
    if not m:
        return None
    try:
        naive = dt.datetime(*(int(x) for x in m.groups()[:6]))
    except ValueError:
        return None
    off = m.group(7)
    if off:
        if off == "Z":
            return naive.replace(tzinfo=dt.timezone.utc)
        sign = -1 if off[0] == "-" else 1
        hh, mm = int(off[1:3]), int(off[-2:])
        return naive.replace(tzinfo=dt.timezone(sign * dt.timedelta(hours=hh, minutes=mm)))
    local = naive.replace(tzinfo=ET)                        # Eastern wall clock (ambiguous hour: the first one)
    if local.astimezone(dt.timezone.utc).astimezone(ET).replace(tzinfo=None) != naive:
        return None                                         # a time that doesn't exist (spring-forward gap)
    return local


def recording_start(session: dict) -> dt.datetime | None:
    """When the recording started (timezone-aware), or None if missing or not trustworthy."""
    s = session or {}
    if s.get("start_time_valid") is False:
        return None
    start = _parse(s.get("start_time")) or _parse(s.get("timestamp_start"))
    if start is None or not 2020 <= start.year <= 2100:
        return None
    return start


def clock_at(start: dt.datetime | None, seconds) -> str | None:
    """Eastern clock time of `seconds` into the recording, e.g. "1:02 PM" (None without a start)."""
    if start is None:
        return None
    try:
        off = max(0.0, float(seconds or 0))
    except (TypeError, ValueError):
        off = 0.0
    t = (start.astimezone(dt.timezone.utc) + dt.timedelta(seconds=int(off))).astimezone(ET)
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def time_line(start: dt.datetime | None, seconds) -> str:
    """"1:02 PM \u00b7 12:34 into recording"; without a trustworthy start: "12:34 into recording"."""
    at = clock_at(start, seconds)
    return " \u00b7 ".join(x for x in (at, f"{clock(seconds)} into recording") if x)


NO_CLOCK_NOTE = "Clock time not available for this recording (no reliable start time), so only the time into the recording is shown."


def incident_title(number: int, name: str | None) -> str:
    """"Incident 1: Left seat and refused redirection" or plain "Incident 1"."""
    return f"Incident {number}: {name}" if name else f"Incident {number}"


def _date_parts(ts) -> tuple[str, str] | None:
    """(Oct 7, 2026 / 2026-10-07) from the recording's start, as recorded (school wall clock)."""
    m = _TS.match(ts or "") if isinstance(ts, str) else None
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    if not 1 <= mo <= 12:
        return None
    return f"{MONTHS[mo - 1]} {d}, {y}", f"{y:04d}-{mo:02d}-{d:02d}"


def _safe(s: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^A-Za-z0-9]+", "-", s)).strip("-")


def student_label(session: dict) -> str:
    first = (session.get("student_first_name") or "").strip()
    last = (session.get("student_last_initial") or "").strip()
    initial = f"{last[0].upper()}." if last[:1].isalpha() else ""
    return " ".join(x for x in (first.split(" ")[0] if first else "", initial) if x) or "-"


def forbidden_values(*sources) -> tuple[list[str], list[str]]:
    """(full last names, other never-print values) from roster / session dicts, at any depth."""
    last, other = set(), set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, (str, int)) and not isinstance(v, bool) and len(str(v).strip()) >= 2:
                    if _LAST.search(str(k)):
                        last.add(str(v).strip())
                    elif _OTHER.search(str(k)):
                        other.add(str(v).strip())
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    for s in sources:
        walk(s)
    return sorted(last, key=len, reverse=True), sorted(other, key=len, reverse=True)


def _scrub(text: str, last: list[str], other: list[str]) -> str:
    out = text or ""
    for v in other:
        out = re.sub(re.escape(v), "[removed]", out, flags=re.I)
    for v in last:
        out = re.sub(rf"\b{re.escape(v)}\b", f"{v[0].upper()}.", out, flags=re.I)
    return out


def student_records(roster: dict, student_id) -> list:
    """Every roster entry about this student (where a last name / school id / DOB could live)."""
    if not student_id:
        return []
    out = [e for e in (roster.get("class_codes") or {}).values()
           if isinstance(e, dict) and e.get("student_id") == student_id]
    st = (roster.get("students") or {}).get(student_id) if isinstance(roster.get("students"), dict) else None
    return out + ([st] if st else [])


def build_print(*, session: dict, approval: dict | None, number: int, recording_id: str,
                roster_records: list | None = None, sample_data: bool = True, draft: dict | None = None) -> dict:
    """The report for incident `number`. Approved session: from the approval record.
    Not approved yet: from the stored draft (`draft` = the draft's analysis), marked
    draft=True (DRAFT title / file name, "Draft, not yet approved" line, watermark in the
    UI). Only the fields below ever leave the server for printing. Raises KeyError if
    there is no such incident."""
    is_draft = not approval
    analysis = (draft or {}) if is_draft else ((approval or {}).get("analysis") or {})
    incidents = incident_numbers(analysis.get("incident_analysis") or [])
    found = [inc for n, inc in incidents if n == number]
    if not found:
        raise KeyError(number)
    inc = found[0]
    last, other = forbidden_values(session, *(roster_records or []))
    label = incident_label(number, inc.get("timestamp_seconds"))
    name = inc.get("name") if isinstance(inc.get("name"), str) else None
    if name and _scrub(name, last, other) != name:
        name = None                                  # the name scrub failed: plain "Incident N"
    heading = incident_title(number, name)
    start = recording_start(session)
    at = clock_at(start, inc.get("timestamp_seconds"))
    cls = (session.get("class_code") or "").strip() or "Class"
    dates = _date_parts(session.get("timestamp_start"))
    shown_date, file_date = dates if dates else ("", "")
    appr = _date_parts((approval or {}).get("approved_at"))
    # file name: number + clock time (offset if no clock) + class + date. Never the AI name.
    when = at.replace(":", "-").replace(" ", "") if at else clock(inc.get("timestamp_seconds")).replace(":", "-")
    model = {
        "recording_id": recording_id,
        "number": number,
        "offset_seconds": int(_offset(inc)),
        "label": label,
        "name": name,
        "heading": heading,
        "clock_time": at,
        "time_line": time_line(start, inc.get("timestamp_seconds")),
        "time_note": None if at else NO_CLOCK_NOTE,
        "of": len(incidents),
        "title": " \u2013 ".join(x for x in ("DRAFT" if is_draft else "", heading, cls, shown_date) if x),
        "file_title": "_".join(x for x in ("DRAFT" if is_draft else "", f"Incident-{number}",
                                           when, _safe(cls), file_date) if x),
        "draft": is_draft,
        "status_line": DRAFT_LINE if is_draft else None,
        "student": student_label(session),
        "class_code": cls,
        "recorded": shown_date or "-",
        "teacher_name": session.get("teacher_name") or "-",
        "approved_on": "Not yet approved" if is_draft else (appr[0] if appr else "-"),
        "fields": [{"label": lbl, "text": _scrub(str(inc.get(key) or ""), last, other)} for lbl, key in TEXT_FIELDS],
        "footer": FOOTER,
        "sample_data": bool(sample_data),
    }
    check_print(model, last, other)
    return model


def check_print(model: dict, last: list[str], other: list[str]) -> None:
    """Last line of defence: no full last name, school id or DOB anywhere in the report."""
    import json

    blob = json.dumps(model, ensure_ascii=False)
    for v in other:
        if v.lower() in blob.lower():
            raise PrintBlocked("forbidden value")
    for v in last:
        if re.search(rf"\b{re.escape(v)}\b", blob, flags=re.I):
            raise PrintBlocked("full last name")
