"""Admin journal views and report downloads ().

The journal holds internal IDs only. Names are looked up here, at view time,
and only as first name + last initial ("Marcus A.", "Sarah J."). Downloads
never show a school ID, a full last name or a date of birth (IE9): none of those
fields is ever read, and every download is checked against them before it goes
out. Each download is itself a journal entry (report_downloaded).
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter, defaultdict

from .journal import ACTIONS

_WORD = re.compile(r"[^\W\d_]+(?:['\-][^\W\d_]+)*", re.UNICODE)


def first_last_initial(first, last=None, last_initial=None) -> str | None:
    """'Marcus', 'Anderson' -> 'Marcus A.'  Only the first word of the first name, and
    one letter of the last name, whatever the source holds."""
    fw = _WORD.findall(first) if isinstance(first, str) else []
    if not fw:
        return None
    src = last_initial if isinstance(last_initial, str) and last_initial.strip() else last
    lw = _WORD.findall(src) if isinstance(src, str) else []
    return f"{fw[0]} {lw[0][0].upper()}." if lw else fw[0]


class Directory:
    """Turns internal ids into display names, from the roster (and, for staff, the
    sign-in email -> roster teacher name). Never returns an email."""

    def __init__(self, roster: dict, allowlist: dict | None = None, people: dict | None = None):
        self.roster = roster or {}
        self.allow = {(u.get("email") or "").casefold(): u for u in (allowlist or {}).get("users", [])}
        self.people = dict(people or {})          # uid -> email (learned at sign-in / from the audit trail)
        self._teacher_by_email = {}
        for tid, t in (self.roster.get("teachers") or {}).items():
            for e in (t.get("emails") or []):
                if isinstance(e, str):
                    self._teacher_by_email[e.casefold()] = tid

    def remember(self, uid: str, email: str) -> None:
        self.people[uid] = email

    def _teacher_name(self, tid) -> str | None:
        t = (self.roster.get("teachers") or {}).get(tid) or {}
        parts = (t.get("teacher_name") or "").split()
        return first_last_initial(parts[0], parts[-1]) if len(parts) > 1 else first_last_initial(t.get("teacher_name"))

    def person(self, actor_id: str, role: str | None = None) -> str:
        if actor_id.startswith("laptop:"):
            name = self._teacher_name(actor_id.split(":", 1)[1])
            if name:
                return name
        email = (self.people.get(actor_id) or "").casefold()
        if email:
            tid = self._teacher_by_email.get(email)
            if tid and self._teacher_name(tid):
                return self._teacher_name(tid)
            entry = self.allow.get(email) or {}
            parts = (entry.get("name") or "").split()
            name = first_last_initial(entry.get("first_name") or (parts[0] if parts else None),
                                      entry.get("last_name") or (parts[-1] if len(parts) > 1 else None))
            if name:
                return name
        short = hashlib.sha256(actor_id.encode()).hexdigest()[:6]
        return f"{(role or 'user').capitalize()} #{short}"

    def student(self, student_id: str | None) -> str | None:
        if not student_id:
            return None
        st = (self.roster.get("students") or {}).get(student_id) or {}
        if st.get("first_name"):
            return first_last_initial(st.get("first_name"), st.get("last_name"), st.get("last_initial"))
        for e in (self.roster.get("class_codes") or {}).values():
            if isinstance(e, dict) and e.get("student_id") == student_id:
                return first_last_initial(e.get("student_first_name"), last_initial=e.get("student_last_initial"))
        return f"Student #{hashlib.sha256(student_id.encode()).hexdigest()[:6]}"

    def forbidden_values(self) -> list[str]:
        """What a download must never contain: school IDs, full last names, dates of birth."""
        out = []
        for st in (self.roster.get("students") or {}).values():
            for k in ("school_id", "student_number", "last_name", "dob", "date_of_birth", "birth_date"):
                v = st.get(k)
                if isinstance(v, str) and len(v.strip()) >= 2:
                    out.append(v.strip())
        for t in (self.roster.get("teachers") or {}).values():
            parts = (t.get("teacher_name") or "").split()
            if len(parts) > 1 and len(parts[-1]) > 1:
                out.append(parts[-1])
            out.extend(e for e in (t.get("emails") or []) if isinstance(e, str))
        out.extend(e for e in self.allow if "@" in e)
        out.extend(v for v in self.people.values() if isinstance(v, str))
        return out


class ReportBlocked(Exception):
    pass


def journal_rows_for_admin(rows: list[dict], d: Directory) -> list[dict]:
    """The admin journal page: each row plus display names looked up now."""
    return [{**r, "actor": d.person(r["actor_id"], r.get("actor_role")), "student": d.student(r.get("student_id"))}
            for r in rows]


def activity_report(rows: list[dict], group_by: str, d: Directory) -> tuple[list[str], list[list]]:
    """Activity per person or per student: one line each, a count per action."""
    if group_by not in ("person", "student"):
        raise ValueError("group_by")
    actions = [a for a in ACTIONS if any(r["action"] == a for r in rows)]
    counts: dict = defaultdict(Counter)
    first_last: dict = {}
    for r in rows:
        key = (r["actor_id"], r["actor_role"]) if group_by == "person" else r.get("student_id")
        if key is None:
            continue
        counts[key][r["action"]] += 1
        t = r["occurred_at"][:10]
        lo, hi = first_last.get(key, (t, t))
        first_last[key] = (min(lo, t), max(hi, t))
    if group_by == "person":
        header = ["person", "role", "first_activity", "last_activity", *actions, "total"]
        body = [[d.person(aid, role), role, *first_last[(aid, role)], *(c[a] for a in actions), sum(c.values())]
                for (aid, role), c in counts.items()]
    else:
        header = ["student", "student_internal_id", "first_activity", "last_activity", *actions, "total"]
        body = [[d.student(sid), sid, *first_last[sid], *(c[a] for a in actions), sum(c.values())]
                for sid, c in counts.items()]
    body.sort(key=lambda row: (str(row[0]), str(row[1])))
    return header, body


def to_csv(header: list[str], body: list[list], d: Directory) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(body)
    text = buf.getvalue()
    if "@" in text or any(re.search(rf"(?<!\w){re.escape(v)}(?!\w)", text, re.IGNORECASE)
                          for v in d.forbidden_values()):
        raise ReportBlocked("a value that must never leave in a download")   # IE9 last line of defence
    return text.encode("utf-8")
