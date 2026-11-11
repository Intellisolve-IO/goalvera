"""Who is speaking: choices for the transcript's speaker selector.

Speaker separation only gives "Speaker 1/2/3"; the reviewer says who is who. The choices come from
the roster (fictional Everyday Heroes Academy sample data): students and staff, shown as first name +
last initial ("Sneezy D.", "Flynn R."). People in the session's class come first (the class from the
Classes tab: its teachers / RBTs and students; else the classroom's teacher, RBTs and students), then
everyone else (searchable in the browser), then generic options.

Privacy: this only lists names to a signed-in reader of the session (every approved role reads every
classroom; may_see). The who-is-who choice stays in the reviewer's browser (as before): it is not
stored on the server, not put on the transcript and never sent to the AI; AI calls keep masking roster
names (bug_reports.never_values covers every staff record and student).

  GET /api/sessions/<rid>/speakers
"""
from __future__ import annotations

from common.classcodes import class_key, find_class
from common.roster import RBT_ROLE, TEACHER_ROLE, class_students, roster_students, short_name, staff_directory, student_classrooms

from .notes import may_see

GENERIC = ["Unknown speaker", "Other adult", "Other student"]


def _student_label(st: dict) -> str:
    first = str(st.get("first_name") or "").strip()
    last = str(st.get("last_initial") or "").strip()
    if not last and st.get("last_name"):
        last = f"{str(st['last_name']).strip()[:1]}."
    return f"{first} {last}".strip()


class Speakers:
    def __init__(self, notes, setup):
        self.notes, self.setup = notes, setup

    def choices(self, rid: str, actor) -> dict:
        s = self.notes.context(rid)
        if not may_see(actor, s):
            from .notes import ApiError
            raise ApiError(403, "NOT_ALLOWED")
        r = self.notes.roster()
        display, entry = find_class(r, s.get("class_code"))
        here_key = class_key(display or s.get("class_code"))

        staff = staff_directory(r)
        studs = roster_students(r)
        people = {}
        for p in staff:
            people[p["id"]] = {"id": p["id"], "label": p["short_name"], "kind": "staff",
                               "role": "RBT" if p["role"] == RBT_ROLE else (p["title"] or "Staff"),
                               "rank": 0 if p["role"] == TEACHER_ROLE else 1 if p["role"] == RBT_ROLE else 2,
                               "classrooms": p["class_codes"]}
        for sid, st in studs.items():
            people[sid] = {"id": sid, "label": _student_label(st), "kind": "student", "role": "Student", "rank": 3,
                           "classrooms": student_classrooms(r, sid)}
        for st in class_students(r, display, entry) if display else []:    # old roster shape: the class entry's student
            sid = st.get("student_id")
            if sid and sid not in people:
                people[sid] = {"id": sid, "label": _student_label(st), "kind": "student", "role": "Student", "rank": 3,
                               "classrooms": [display]}

        # the session's class (picked, else suggested from the schedule), else its classroom
        klass = None
        try:
            cur = (self.setup.session_class(rid, actor, s) or {}).get("class")
            klass = self.setup.klass(cur["id"]) if cur else None
        except Exception:  # noqa: BLE001 - fall back to the classroom
            klass = None
        if klass:
            ids = list(klass.get("teacher_ids") or []) + list(klass.get("student_ids") or [])
            basis = {"kind": "class", "name": klass.get("name")}
        else:
            ids = [p["id"] for p in staff if here_key and any(class_key(c) == here_key for c in p["class_codes"])]
            ids += [st.get("student_id") for st in (class_students(r, display, entry) if display else [])]
            basis = {"kind": "classroom", "name": display or s.get("class_code")}
        for extra in (s.get("teacher_id"), s.get("student_id")):           # the session's own teacher and student
            if extra and extra not in ids:
                ids.append(extra)
        inside = set(i for i in ids if i in people)

        # a label used by two people gets the classroom (or role) so the reviewer can tell them apart
        count: dict = {}
        for p in people.values():
            count[p["label"]] = count.get(p["label"], 0) + 1

        def out(p):
            label = p["label"]
            if count[label] > 1:
                label = f"{label} ({', '.join(p['classrooms'][:1]) or p['role']})"
            return {"id": p["id"], "label": label, "kind": p["kind"], "role": p["role"],
                    **({"classroom": p["classrooms"][0]} if p["classrooms"] else {}),
                    **({"session_student": True} if p["id"] == s.get("student_id") else {})}

        key = lambda p: (p["rank"], p["label"].lower())  # noqa: E731
        in_class = [out(p) for p in sorted((people[i] for i in inside), key=lambda p: (
            p["id"] != s.get("student_id") if p["kind"] == "student" else False, *key(p)))]
        others = [out(p) for p in sorted((p for i, p in people.items() if i not in inside and p["label"]),
                                         key=lambda p: (p["kind"] == "student", p["label"].lower()))]
        return {"recording_id": rid, "basis": basis, "in_class": [p for p in in_class if p["label"]],
                "others": others, "generic": GENERIC,
                "label": (r.get("_meta") or {}).get("label") if (r.get("_meta") or {}).get("synthetic") else None}
