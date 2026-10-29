"""Students in this session (David, Oct 10 2026; tasks#106).

A classroom recording has no single student, so the teacher says who was there. The review page
offers the session's class roster (the class the teacher confirmed, else the one suggested from the
schedule; else the classroom's students) as a suggestion: nothing is linked until the teacher (or an
admin) clicks Save. The choice is stored as an audit record (audit/<rid>/..._session_students_set_...,
create-only, the latest wins, expires with the session) and journaled (counts only). A linked
session then shows on each student's week (Students, Lesson planner) and feeds the weekly summary,
the weekly IEP check and the parent report (planner.py, students.py).

Privacy (Ops, Oct 10): student-session links are student data within today's DPA scope (sessions were
already per student or class). Ids only in the bucket record and the journal; names are shown to
signed-in readers of the session, as on the Students tab. RBTs read only.

  GET  /api/sessions/<rid>/students
  POST /api/sessions/<rid>/students  {"student_ids": [...]}
"""
from __future__ import annotations

from common.classcodes import class_key, find_class
from common.roster import class_students, roster_students

from .notes import (STUDENTS_EVENT, ApiError, check_actor, is_read_only, may_act, may_see, signed_in)

MAX_STUDENTS = 40


def _label(st: dict) -> str:
    first = str(st.get("first_name") or "").strip()
    last = str(st.get("last_initial") or "").strip()
    if not last and st.get("last_name"):
        last = f"{str(st['last_name']).strip()[:1]}."
    return f"{first} {last}".strip()


class SessionStudents:
    def __init__(self, notes, setup):
        self.notes, self.setup = notes, setup

    def _may_change(self, actor, s: dict) -> bool:
        if actor is None:
            return True
        if is_read_only(actor):
            return False
        return may_act(actor, s) or (signed_in(actor) and actor.is_admin)

    def suggestion(self, rid: str, s: dict, actor) -> dict:
        """The class's students (picked, else suggested class); else the classroom's students."""
        r = self.notes.roster()
        studs = roster_students(r)
        try:
            cls = self.setup.session_class(rid, actor, s).get("class")
        except Exception:  # noqa: BLE001 - no Classes data: fall back to the classroom
            cls = None
        c = self.setup.klass(cls["id"]) if cls else None
        if c and c.get("student_ids"):
            ids = [x for x in c["student_ids"] if x in studs]
            return {"source": "class", "class_name": c.get("name"), "student_ids": ids}
        display, entry = find_class(r, s.get("class_code"))
        ids = [x["student_id"] for x in class_students(r, display, entry)] if display else []
        return {"source": "classroom" if ids else None, "class_name": display, "student_ids": ids}

    def view(self, rid: str, actor) -> dict:
        s = self.notes.context(rid)
        if not may_see(actor, s):
            raise ApiError(404, "NOT_FOUND")
        r = self.notes.roster()
        studs = roster_students(r)
        sug = self.suggestion(rid, s, actor)
        here = set(sug["student_ids"])
        k = class_key(s.get("class_code"))
        for sid, st in studs.items():              # the whole classroom is offered too
            if k and any(class_key(find_class(r, c)[0] or c) == k for c in st.get("class_codes") or []):
                here.add(sid)
        here |= set(s.get("student_ids") or [])

        def row(sid):
            st = studs.get(sid) or {}
            return {"student_id": sid, "name": _label(st) or sid, "grade": st.get("grade")}
        near = sorted((row(x) for x in here if x in studs), key=lambda x: x["name"].casefold())
        others = sorted((row(x) for x in studs if x not in here), key=lambda x: x["name"].casefold())
        return {"recording_id": rid, "student_ids": list(s.get("student_ids") or []),
                "saved": bool(s.get("students_set")), "suggestion": sug,
                "choices": near, "others": others, "max": MAX_STUDENTS,
                "can_change": bool(self._may_change(actor, s))}

    def save(self, rid: str, actor, body: dict) -> dict:
        actor = check_actor(actor)
        if is_read_only(actor):
            raise ApiError(403, "READ_ONLY_ROLE")
        s = self.notes.context(rid)
        if not self._may_change(actor, s):
            raise ApiError(403, "NOT_SESSION_TEACHER")
        ids = (body or {}).get("student_ids")
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
            raise ApiError(400, "BAD_STUDENTS")
        ids = list(dict.fromkeys(ids))
        if len(ids) > MAX_STUDENTS:
            raise ApiError(400, "TOO_MANY_STUDENTS")
        studs = roster_students(self.notes.roster())
        if any(x not in studs for x in ids):
            raise ApiError(400, "UNKNOWN_STUDENT")
        sug = self.suggestion(rid, s, actor)
        same = sorted(ids) == sorted(sug["student_ids"])
        self.notes._audit(rid, STUDENTS_EVENT, actor, self.notes.store.created(f"drafts/{rid}.json"),
                          session=s, ids_only=True, student_ids=ids, suggested=same,
                          journal={"students": len(ids), "suggested": same})
        return self.view(rid, actor)
