#!/usr/bin/env python3
"""Build the review site's demo roster (app/samples/demo_roster.json) from the FICTIONAL sample
school in app/samples/sis-demo/ (Everyday Heroes Academy).

    python3 app/tools/build_demo_roster.py           # rewrite app/samples/demo_roster.json
    python3 app/tools/build_demo_roster.py --check   # exit 1 if the file is out of date

It also writes app/samples/contacts/demo_guardians.json: the weekly parent email's
recipients, kept OUT of the roster and read only by review_api/parent_email.py. Per student: contact
id, first name, relationship, a fictional example.com / example.org email and "receives_reports"
(the SIS receivesWeeklyDraft flag). Non-custodial contacts are listed with the flag off, so the demo
shows they are excluded. Never: last names, addresses, phones, pickup or custody details.

Only what the review site needs goes into the roster (data minimization):
  * students: first name, last name (only so the name scrub can catch it; screens show the last
    initial), grade, classroom, teachers, a school-year-to-date attendance summary (counts only,
    from ext_attendance.csv), IEP / 504 summary (eligibility, present levels, goals,
    services, accommodations, placement, behavior plan targets, ESY, dates, case manager) and the
    "Fictional sample data" label.
  * never: birth dates, school or state ids, guardians and contacts, addresses, health flags,
    pronouns (never sent to the AI, so never in the roster), staff credential numbers.
  * staff: names, titles, classrooms; teachers keep the recorder's old labels as device labels.
    Staff emails only from TEACHER_EMAILS (Alex Morgan's sign-in), never from the sample CSVs.
Recordings made before the switch keep working: old class codes are aliases of Room 104 (see
LEGACY_ALIASES and docs/demo-script.md, "Recording remap").
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SRC = APP / "samples" / "sis-demo"
OUT = APP / "samples" / "demo_roster.json"
GUARDIANS_OUT = APP / "samples" / "contacts" / "demo_guardians.json"
FICTIONAL_DOMAINS = ("example.com", "example.org")
LABEL = "Fictional sample data"
SCHOOL = "Everyday Heroes Academy"

# Older demo class codes (roster RM12 + the recorder's built-in sample classes and
# classrooms, firmware/recorder-idf/main/sample_device.json). Every old demo recording belonged to
# the one demo teacher, so they all land in Room 104 with Alex Morgan, the demo's lead teacher.
LEGACY_ALIASES = {"Room 104": ["RM12", "Reading 3A", "Math 2B", "Circle Time", "Room B", "Hall"]}
LEGACY_TEACHER_LABELS = {"Alex Morgan": ["Demo Teacher", "J. Rivera", "S. Okonkwo"]}
# The demo RBT sign-in keeps working as Room 104's RBT.
LEGACY_RBT_EMAILS = {"Room 104": ["demo-rbt@staff.example"]}
# Staff email on file (sign-in + the teacher's own address for emailed materials). The default
# mailbox is a staff.example placeholder (override TEACHER_EMAILS for any other host).
TEACHER_EMAILS = {"Alex Morgan": ["snowwhite@staff.example"]}
ROLE_TITLE = {"bcba": "BCBA", "slp": "Speech-language pathologist", "ot": "Occupational therapist",
              "pt": "Physical therapist", "dhh_teacher": "Teacher of the Deaf/Hard of Hearing",
              "tvi": "Vision teacher / O&M", "counselor": "School counselor", "case_manager": "Case manager",
              "principal": "Principal", "office_admin": "Office administrator"}
SERVICE = {"specialized_instruction": "Specialized instruction", "speech_language": "Speech-language therapy",
           "occupational_therapy": "Occupational therapy", "physical_therapy": "Physical therapy",
           "behavior_support": "Behavior support", "counseling": "Counseling", "deaf_hoh": "Deaf/HH services",
           "vision": "Vision services", "orientation_mobility": "Orientation & mobility", "aba": "ABA support"}
PLACEMENT = {"SS": "Separate school, full day", "SC": "Separate class", "RC": "Resource", "GE": "General education"}


def load(name: str) -> list[dict]:
    with (SRC / name).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def app_id(sourced: str) -> str:
    """u-stu-0001 -> stu_0001, u-stf-0001 -> stf_0001 (the review API's id pattern)."""
    return sourced.removeprefix("u-").replace("-", "_")


def room(class_sourced: str) -> str:
    """cls-rm104-hr -> Room 104."""
    return "Room " + class_sourced.split("-")[1].removeprefix("rm")


def grade(g: str) -> str:
    return "K" if g == "KG" else str(int(g))


def iep_view(doc: dict, staff_name) -> dict:
    out = {"plan_type": "IEP",
           "eligibility": [e["label"] for e in sorted(doc.get("eligibility") or [], key=lambda e: e.get("order", 0))],
           "present_levels": [f"{p['area'].replace('_', ' ').capitalize()}: {p['text']}" for p in doc.get("presentLevels") or []],
           "accommodations": [a["description"] for a in doc.get("accommodations") or []],
           "services": [], "iep_start_date": doc.get("startDate"), "iep_review_date": doc.get("annualReviewDue"),
           "case_manager": staff_name(doc.get("caseManagerSourcedId"))}
    for s in doc.get("services") or []:
        per = f"{s['minutes']} min x {s['frequencyCount']}/{s['frequencyPeriod']}"
        out["services"].append(f"{SERVICE.get(s['type'], s['type'].replace('_', ' ').capitalize())}: {per} ({s.get('location') or ''})".replace(" ()", ""))
    if doc.get("environmentCode"):
        out["placement"] = PLACEMENT.get(doc["environmentCode"], doc["environmentCode"])
    bp = doc.get("behaviorPlan")
    if isinstance(bp, dict) and bp.get("targetBehaviors"):
        out["behavior_plan"] = "Behavior plan targets: " + "; ".join(t["name"] for t in bp["targetBehaviors"])
    esy = doc.get("esy")
    if isinstance(esy, dict):
        out["esy"] = "Extended school year: " + ("yes" if esy.get("needed") else "no")
    goals = [{"goal_id": f"g{g['number']}", "category": g.get("category") or g.get("area"),
              "description": g["statement"]} for g in doc.get("goals") or []]
    return {"iep": {k: v for k, v in out.items() if v not in (None, "", [])}, "iep_goals": goals}


def build() -> dict:
    users = {u["sourcedId"]: u for u in load("users.csv")}
    staff = load("ext_staff.csv")
    classes = {c["sourcedId"]: c for c in load("classes.csv")}
    homerooms = {cid: c for cid, c in classes.items() if c["classType"] == "homeroom"}
    enr = load("enrollments.csv")
    full = lambda sid: f"{users[sid]['givenName']} {users[sid]['familyName']}" if sid in users else None  # noqa: E731

    # classrooms: one per homeroom, with its lead teacher
    teacher_of = {e["classSourcedId"]: e["userSourcedId"] for e in enr if e["role"] == "teacher" and e["primary"] == "true"}
    aides = {}
    for e in enr:
        if e["role"] == "aide" and e["classSourcedId"] in homerooms:
            aides.setdefault(e["userSourcedId"], set()).add(room(e["classSourcedId"]))
    class_codes = {}
    for cid, c in sorted(homerooms.items()):
        name, tid = room(cid), teacher_of[cid]
        num = name.split()[-1]
        class_codes[name] = {"teacher_id": app_id(tid), "teacher_name": full(tid), "grades": c["grades"].replace(",", ", "),
                             "aliases": [f"RM{num}", *LEGACY_ALIASES.get(name, [])], "iep_goals": [], "label": LABEL}

    # staff: one record per person with the role on the record (person schema, docs/sis-schema.md;
    #). Teachers stay under "teachers" (classrooms point at them); RBTs and every other staff
    # member are "staff" records with "role" ("rbt", "bcba", ...) and "supervisor_id" (RBTs: the BCBA).
    teachers, other = {}, {}
    sup = lambda s: app_id(s["supervisorSourcedId"]) if s.get("supervisorSourcedId") else None  # noqa: E731
    for s in staff:
        sid, nm = s["sourcedId"], full(s["sourcedId"])
        if s["staffType"] == "teacher":
            own = sorted(d for d, e in class_codes.items() if e["teacher_id"] == app_id(sid))
            teachers[app_id(sid)] = {"teacher_name": nm, "title": s["title"], "role": "teacher",
                                     "device_labels": [nm, *LEGACY_TEACHER_LABELS.get(nm, [])],
                                     "default_class_code": own[0] if own else None,
                                     "emails": list(TEACHER_EMAILS.get(nm, [])), "supervisor_id": sup(s), "label": LABEL}
        elif s["staffType"] == "para_rbt":
            rooms = sorted(aides.get(sid, set()))
            other[app_id(sid)] = {"name": nm, "title": s["title"], "role": "rbt", "class_codes": rooms,
                                  "emails": sorted({e for r in rooms for e in LEGACY_RBT_EMAILS.get(r, [])}),
                                  "device_labels": [], "supervisor_id": sup(s), "label": LABEL}
        else:
            other[app_id(sid)] = {"name": nm, "title": ROLE_TITLE.get(s["staffType"], s["title"]),
                                  "role": s["staffType"], "class_codes": [], "supervisor_id": sup(s), "label": LABEL}

    # students
    ext = {s["sourcedId"]: s for s in load("ext_students.csv")}
    p504 = {p["studentSourcedId"]: p for p in load("iep/csv/section504_plans.csv")}
    ieps = {}
    for f in sorted((SRC / "iep" / "json").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("status") == "active":
            ieps[d["studentSourcedId"]] = d
    staff_name = lambda sid: full(sid) if sid else None  # noqa: E731
    # attendance (Oct 10): only a school-year-to-date summary per student goes into the roster
    att: dict = {}
    for a in load("ext_attendance.csv"):
        c = att.setdefault(a["studentSourcedId"], {"P": 0, "AE": 0, "AU": 0, "T": 0, "through": ""})
        c[a["code"]] += 1
        c["through"] = max(c["through"], a["date"])
    students = {}
    for sid, x in sorted(ext.items()):
        u = users[sid]
        rm = room(x["homeroomClassSourcedId"])
        rec = {"first_name": u["givenName"], "last_initial": u["familyName"][:1] + ".", "last_name": u["familyName"],
               "grade": grade(x["gradeLevel"]), "class_codes": [rm], "teacher_ids": [class_codes[rm]["teacher_id"]],
               "iep_goals": [], "iep": {}, "label": LABEL}
        if sid in ieps:
            v = iep_view(ieps[sid], staff_name)
            rec["iep"], rec["iep_goals"] = v["iep"], v["iep_goals"]
        elif sid in p504:
            p = p504[sid]
            rec["iep"] = {"plan_type": "Section 504 plan", "accommodations": [a.strip() for a in p["accommodations"].split(";")],
                          "iep_start_date": p["startDate"], "iep_review_date": p["reviewDate"],
                          "case_manager": staff_name(p["coordinatorSourcedId"])}
        if sid in att:
            c = att[sid]
            days = c["P"] + c["AE"] + c["AU"] + c["T"]
            rec["attendance"] = {"school_days": days, "present": c["P"] + c["T"], "absent_excused": c["AE"],
                                 "absent_unexcused": c["AU"], "tardy": c["T"],
                                 "rate": round(100 * (c["P"] + c["T"]) / days, 1) if days else None,
                                 "from": "2026-08-03", "through": c["through"]}
        students[app_id(sid)] = rec

    return {
        "_meta": {"synthetic": True, "label": LABEL, "school_name": SCHOOL, "school_profile": "everyday-heroes",
                  "source": "app/samples/sis-demo (built by app/tools/build_demo_roster.py; do not edit by hand)",
                  "note": ("DEMO ONLY - fictional sample school. Every person here is invented. Classrooms are the "
                           "homerooms; recorder class codes match a classroom or one of its aliases (common/classcodes.py). "
                           "Older demo codes are aliases of Room 104. The placeholder roster is kept in "
                           "app/samples/profiles/demo-school/demo_roster.json.")},
        "class_codes": class_codes,
        "teachers": teachers,
        "staff": other,
        "students": students,
        "settings": {"iep_access": {"rbt": "read", "_note": "The school's IEP access setting for RBTs: read (default) or none."}},
    }


def build_guardians() -> dict:
    """student id -> the student's contacts for the weekly parent email (see the module docstring)."""
    users = {u["sourcedId"]: u for u in load("users.csv")}
    out: dict = {}
    for link in load("ext_student_contacts.csv"):
        u = users.get(link["contactSourcedId"]) or {}
        email = (u.get("email") or "").strip().lower()
        if email and email.rsplit("@", 1)[-1] not in FICTIONAL_DOMAINS:
            raise SystemExit(f"{link['contactSourcedId']}: not a fictional address")
        out.setdefault(app_id(link["studentSourcedId"]), []).append({
            "contact_id": app_id(link["contactSourcedId"]),
            "first_name": (u.get("preferredGivenName") or u.get("givenName") or "").strip(),
            "relationship": link["relationship"],
            "email": email or None,
            "receives_reports": link["receivesWeeklyDraft"] == "true" and bool(email),
        })
    return {"label": LABEL, "school": SCHOOL,
            "note": "Weekly parent email recipients. Fictional; example.com / example.org only.",
            "students": {k: out[k] for k in sorted(out)}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    text = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    gtext = json.dumps(build_guardians(), indent=2, ensure_ascii=False) + "\n"
    if a.check:
        same = OUT.exists() and OUT.read_text(encoding="utf-8") == text
        gsame = GUARDIANS_OUT.exists() and GUARDIANS_OUT.read_text(encoding="utf-8") == gtext
        print("demo_roster.json is up to date" if same else "demo_roster.json is out of date: run app/tools/build_demo_roster.py")
        print("demo_guardians.json is up to date" if gsame else "demo_guardians.json is out of date: run app/tools/build_demo_roster.py")
        return 0 if same and gsame else 1
    OUT.write_text(text, encoding="utf-8")
    GUARDIANS_OUT.parent.mkdir(parents=True, exist_ok=True)
    GUARDIANS_OUT.write_text(gtext, encoding="utf-8")
    r = json.loads(text)
    print(f"{OUT}: {len(r['class_codes'])} classrooms, {len(r['students'])} students, "
          f"{len(r['teachers'])} teachers, {sum(1 for x in r['staff'].values() if x['role'] == 'rbt')} RBTs, "
          f"{len(r['staff'])} staff records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
