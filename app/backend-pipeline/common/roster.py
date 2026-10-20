"""Students in the demo roster: the ONE place that knows where they live ().

Two shapes are read, so older rosters keep working:
  * roster.students.<student_id>   many students per classroom (Sample Elementary School,
    Oct 10 2026): first_name, last_initial, last_name, class_codes [...], teacher_ids [...],
    iep_goals [...], iep {...}, label.
  * roster.class_codes.<code>.student_id   the old one-student-per-class-code shape (RM12).
Classroom names are matched with common.classcodes.class_key, so "Room 104", "room-104" and an
alias such as "RM104" are the same classroom.
"""
from __future__ import annotations

from .classcodes import class_key, find_class


def _goals(goals) -> list[dict]:
    return [{"goal_id": g.get("goal_id"), "category": g.get("category"), "description": g.get("description")}
            for g in goals or [] if isinstance(g, dict) and g.get("goal_id")]


def _from_student(sid: str, st: dict) -> dict:
    return {"student_id": sid, "first_name": st.get("first_name") or "",
            "last_initial": st.get("last_initial") or "", "iep_goals": _goals(st.get("iep_goals"))}


def _from_entry(entry: dict) -> dict:
    return {"student_id": entry["student_id"], "first_name": entry.get("student_first_name") or "",
            "last_initial": entry.get("student_last_initial") or "", "iep_goals": _goals(entry.get("iep_goals"))}


def roster_students(roster: dict) -> dict:
    """student_id -> its roster record (roster.students only)."""
    st = (roster or {}).get("students")
    # a record without a first name only adds facts (teacher_ids, IEP fields) to an old-shape student
    return {k: v for k, v in st.items() if isinstance(v, dict) and v.get("first_name")} if isinstance(st, dict) else {}


def student_classrooms(roster: dict, sid: str) -> list[str]:
    """The roster display codes of a student's classrooms (either shape), roster order."""
    codes = (roster or {}).get("class_codes") or {}
    out = [d for d, e in codes.items() if isinstance(e, dict) and e.get("student_id") == sid]
    for c in roster_students(roster).get(sid, {}).get("class_codes") or []:
        d, _ = find_class(roster, c)
        if d and d not in out:
            out.append(d)
    return out


def class_students(roster: dict, display: str | None, entry: dict | None = None) -> list[dict]:
    """The classroom's students, sorted by name: the class entry's own student (old shape) plus
    every roster.students record listing the classroom."""
    entry = entry if entry is not None else ((roster or {}).get("class_codes") or {}).get(display) or {}
    out, seen = [], set()
    if isinstance(entry, dict) and entry.get("student_id"):
        out.append(find_student(roster, entry["student_id"])[0] or _from_entry(entry))
        seen.add(entry["student_id"])
    k = class_key(display)
    for sid, st in roster_students(roster).items():
        if sid in seen or not k:
            continue
        if any(class_key(find_class(roster, c)[0] or c) == k for c in st.get("class_codes") or []):
            out.append(_from_student(sid, st))
            seen.add(sid)
    return sorted(out, key=lambda s: (s["first_name"].casefold(), s["last_initial"].casefold(), s["student_id"]))


def find_student(roster: dict, sid) -> tuple[dict | None, str | None]:
    """(planner-shaped student, first classroom) or (None, None)."""
    if not isinstance(sid, str):
        return None, None
    st = roster_students(roster).get(sid)
    for display, entry in ((roster or {}).get("class_codes") or {}).items():
        if isinstance(entry, dict) and entry.get("student_id") == sid:
            old = _from_entry(entry)                   # old shape; a students.<id> record may add a name
            if st is not None:
                new = _from_student(sid, st)
                old = {**old, "first_name": new["first_name"] or old["first_name"],
                       "last_initial": new["last_initial"] or old["last_initial"],
                       "iep_goals": new["iep_goals"] or old["iep_goals"]}
            return old, display
    if st is not None:
        rooms = student_classrooms(roster, sid)
        return _from_student(sid, st), (rooms[0] if rooms else None)
    return None, None


def all_student_ids(roster: dict) -> list[str]:
    ids = [e["student_id"] for e in ((roster or {}).get("class_codes") or {}).values()
           if isinstance(e, dict) and e.get("student_id")]
    ids += [s for s in roster_students(roster) if s not in ids]
    return ids


def classrooms_with_students(roster: dict) -> list[str]:
    return [d for d in ((roster or {}).get("class_codes") or {}) if class_students(roster, d)]


# ---------------------------------------------------------------- staff (person schema)
# One staff record per person, with the role on the record (docs/sis-schema.md, PR #22/#23):
#   roster.staff.<id> = {"name", "title", "role": "rbt"|"bcba"|"slp"|..., "class_codes", "emails",
#                        "supervisor_id", "label"}
# Teachers stay under roster.teachers (their classrooms point at them). Older rosters (the legacy
# Prep profile, test fixtures) still keep RBTs under roster.rbts; both are read the same way.
TEACHER_ROLE, RBT_ROLE = "teacher", "rbt"


def staff_role(rec: dict) -> str:
    r = str((rec or {}).get("role") or "").strip().lower()
    return r or "staff"


def rbt_records(roster: dict) -> dict:
    """id -> record for every RBT: roster.staff records with role "rbt", plus the old roster.rbts."""
    out = {k: v for k, v in ((roster or {}).get("rbts") or {}).items() if isinstance(v, dict)}
    for k, v in ((roster or {}).get("staff") or {}).items():
        if isinstance(v, dict) and staff_role(v) == RBT_ROLE:
            out[k] = v
    return out


def _initial(last: str) -> str:
    last = (last or "").strip()
    return f"{last[0]}." if last else ""


def short_name(full: str) -> str:
    """'Flynn Rider' -> 'Flynn R.' (first name + last initial, like students)."""
    parts = str(full or "").split()
    if not parts:
        return ""
    return parts[0] if len(parts) == 1 else f"{parts[0]} {_initial(parts[-1])}"


def staff_directory(roster: dict) -> list[dict]:
    """Every staff member once: teachers, RBTs and other staff, each with its role from its own record.
    [{"id", "name", "short_name", "title", "role", "class_codes", "emails", "supervisor_id", "label"}]"""
    r = roster or {}
    codes = r.get("class_codes") or {}
    out, seen = [], set()

    def add(pid, rec, name, role, classes):
        if pid in seen:
            return
        seen.add(pid)
        out.append({"id": pid, "name": name or pid, "short_name": short_name(name or pid),
                    "title": rec.get("title") or {TEACHER_ROLE: "Teacher", RBT_ROLE: "RBT"}.get(role, "Staff"), "role": role,
                    "class_codes": classes,
                    "emails": [e for e in rec.get("emails") or [] if isinstance(e, str)],
                    "supervisor_id": rec.get("supervisor_id") if isinstance(rec.get("supervisor_id"), str) else None,
                    "label": rec.get("label")})

    for tid, t in (r.get("teachers") or {}).items():
        if isinstance(t, dict):
            add(tid, t, t.get("teacher_name"), TEACHER_ROLE,
                sorted(d for d, e in codes.items() if isinstance(e, dict) and e.get("teacher_id") == tid))
    for sid, t in rbt_records(r).items():
        add(sid, t, t.get("name"), RBT_ROLE, sorted(c for c in t.get("class_codes") or [] if isinstance(c, str)))
    for sid, t in (r.get("staff") or {}).items():
        if isinstance(t, dict):
            add(sid, t, t.get("name"), staff_role(t), sorted(c for c in t.get("class_codes") or [] if isinstance(c, str)))
    names = {p["id"]: p["name"] for p in out}
    for p in out:
        p["supervisor_name"] = names.get(p["supervisor_id"]) if p["supervisor_id"] else None
    return out
