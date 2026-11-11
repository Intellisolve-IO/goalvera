#!/usr/bin/env python3
"""Validate the FICTIONAL sample school dataset in app/samples/sis-demo/.

    python3 app/tools/validate_sis_demo.py [path]     # exit 0 = all checks pass

Checks: every file in the manifest is present; every CSV row and IEP JSON carries the fictional flag;
sourcedIds are unique; every reference resolves (orgs, sessions, courses, classes, users, roles,
enrollments, guardians both ways, staff, credentials, caseloads, programs, IEP student/staff/goal/
period refs, 504 plans); one active IEP per IEP student; IEP CSV flattening matches the JSON;
homerooms hold 8-12 students; the seven focus students exist; contact data is obviously fake
(example.com/.org emails, 555-01xx phones); no file name contains a date of birth or a name.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "samples" / "sis-demo"
errors: list[str] = []
checks = Counter()


def ok(cond: bool, msg: str):
    checks["checks"] += 1
    if not cond:
        errors.append(msg)


def load(rel: str) -> list[dict]:
    p = ROOT / rel
    ok(p.is_file(), f"missing file {rel}")
    if not p.is_file():
        return []
    with p.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for i, r in enumerate(rows, 2):
        ok(r.get("metadata.fictional") == "true", f"{rel}:{i} missing metadata.fictional=true")
    checks["csv_rows"] += len(rows)
    return rows


def unique(rows, key, rel):
    c = Counter(r[key] for r in rows)
    dup = [k for k, n in c.items() if n > 1]
    ok(not dup, f"{rel}: duplicate {key} {dup[:5]}")
    return {r[key]: r for r in rows}


def refs(rows, col, target: dict | set, rel, allow_blank=False, multi=False):
    for i, r in enumerate(rows, 2):
        v = r.get(col, "")
        vals = [x for x in v.split(",") if x] if multi else [v]
        if not v and allow_blank:
            continue
        for x in vals:
            ok(x in target, f"{rel}:{i} {col}={x!r} does not resolve")


# ---------------------------------------------------------------- manifest
man = {r["propertyName"]: r["value"] for r in csv.DictReader((ROOT / "manifest.csv").open(encoding="utf-8"))}
ok(man.get("oneroster.version") == "1.2", "manifest: oneroster.version must be 1.2")
ok(man.get("ext.synthetic") == "true", "manifest: ext.synthetic must be true")
for k, v in man.items():
    if k.startswith("file.") and v in ("bulk", "delta"):
        ok((ROOT / f"{k[5:]}.csv").is_file(), f"manifest lists {k} but the file is missing")
for f in man.get("ext.files", "").split(","):
    if f and "*" not in f:
        ok((ROOT / f).is_file(), f"manifest ext.files lists missing {f}")

# ---------------------------------------------------------------- OneRoster
orgs = unique(load("orgs.csv"), "sourcedId", "orgs.csv")
refs([r for r in orgs.values() if r["parentSourcedId"]], "parentSourcedId", orgs, "orgs.csv")
sess = unique(load("academicSessions.csv"), "sourcedId", "academicSessions.csv")
refs([r for r in sess.values() if r["parentSourcedId"]], "parentSourcedId", sess, "academicSessions.csv")
for r in sess.values():
    ok(r["startDate"] <= r["endDate"], f"academicSessions {r['sourcedId']} starts after it ends")
courses = unique(load("courses.csv"), "sourcedId", "courses.csv")
refs(list(courses.values()), "orgSourcedId", orgs, "courses.csv")
refs(list(courses.values()), "schoolYearSourcedId", sess, "courses.csv")
classes = unique(load("classes.csv"), "sourcedId", "classes.csv")
refs(list(classes.values()), "courseSourcedId", courses, "classes.csv")
refs(list(classes.values()), "schoolSourcedId", orgs, "classes.csv")
refs(list(classes.values()), "termSourcedIds", sess, "classes.csv", multi=True)
ok(len({c["classCode"].casefold() for c in classes.values()}) == len(classes), "classes.csv: classCode not unique")
users = unique(load("users.csv"), "sourcedId", "users.csv")
roles = load("roles.csv")
unique(roles, "sourcedId", "roles.csv")
refs(roles, "userSourcedId", users, "roles.csv")
refs(roles, "orgSourcedId", orgs, "roles.csv")
role_of = defaultdict(set)
for r in roles:
    role_of[r["userSourcedId"]].add(r["role"])
prim = Counter((r["userSourcedId"], r["orgSourcedId"]) for r in roles if r["roleType"] == "primary")
ok(all(n == 1 for n in prim.values()) and len(prim) == len(users), "roles.csv: every user needs exactly one primary role")
students = {u for u, rs in role_of.items() if "student" in rs}
contacts_ids = {u for u, rs in role_of.items() if rs & {"parent", "guardian", "relative"}}
staff_ids = set(users) - students - contacts_ids
enr = load("enrollments.csv")
unique(enr, "sourcedId", "enrollments.csv")
refs(enr, "classSourcedId", classes, "enrollments.csv")
refs(enr, "userSourcedId", users, "enrollments.csv")
refs(enr, "schoolSourcedId", orgs, "enrollments.csv")
for i, e in enumerate(enr, 2):
    want = "student" if e["role"] == "student" else None
    if want:
        ok(e["userSourcedId"] in students, f"enrollments.csv:{i} student enrollment for non-student {e['userSourcedId']}")
    else:
        ok(e["userSourcedId"] in staff_ids, f"enrollments.csv:{i} staff enrollment for non-staff {e['userSourcedId']}")
for s in students:
    ok(any(e["userSourcedId"] == s for e in enr), f"student {s} has no enrollment")
for c in classes.values():
    ok(any(e["classSourcedId"] == c["sourcedId"] and e["role"] == "teacher" for e in enr), f"class {c['sourcedId']} has no teacher")
# guardians linked both ways through agentSourcedIds
for u, row in users.items():
    for a in [x for x in row["agentSourcedIds"].split(",") if x]:
        ok(a in users, f"users.csv {u} agent {a} does not resolve")
        ok(u in users.get(a, {}).get("agentSourcedIds", "").split(","), f"users.csv agent link {u}->{a} is not mirrored")
demo = unique(load("demographics.csv"), "sourcedId", "demographics.csv")
refs(list(demo.values()), "sourcedId", students, "demographics.csv")
ok(set(demo) == students, "demographics.csv: one row per student")

# ---------------------------------------------------------------- add-on CSVs
ext_st = unique(load("ext_students.csv"), "sourcedId", "ext_students.csv")
ok(set(ext_st) == students, "ext_students.csv: one row per student")
refs(list(ext_st.values()), "homeroomClassSourcedId", {k for k, c in classes.items() if c["classType"] == "homeroom"}, "ext_students.csv")
refs(list(ext_st.values()), "residentDistrict", orgs, "ext_students.csv")
nums = Counter(r["schoolStudentNumber"] for r in ext_st.values())
ok(all(n == 1 for n in nums.values()), "ext_students.csv: duplicate school student numbers")
ok(all(re.fullmatch(r"\d{6}", r["schoolStudentNumber"]) for r in ext_st.values()), "ext_students.csv: school student numbers must be 6 digits")
ok(all(re.fullmatch(r"999\d{7}", r["stateStudentId"]) for r in ext_st.values()), "ext_students.csv: sample GTIDs must be 10 digits starting 999")
ok(all(r["label"] == "Fictional sample data" for r in ext_st.values()), "ext_students.csv: label must be 'Fictional sample data'")
for s, r in ext_st.items():
    ok(users[s]["identifier"] == r["schoolStudentNumber"], f"{s}: users.identifier != schoolStudentNumber")
    ok(any(e["userSourcedId"] == s and e["classSourcedId"] == r["homeroomClassSourcedId"] for e in enr), f"{s}: not enrolled in homeroom")
    ok(r["healthAlertOnFile"] in ("true", "false"), f"{s}: healthAlertOnFile must be a flag only")
room_size = Counter(r["homeroomClassSourcedId"] for r in ext_st.values())
for room, n in room_size.items():
    ok(8 <= n <= 12, f"{room}: {n} students (want 8-12)")
ext_con = unique(load("ext_contacts.csv"), "sourcedId", "ext_contacts.csv")
ok(set(ext_con) == contacts_ids, "ext_contacts.csv: one row per contact")
links = load("ext_student_contacts.csv")
refs(links, "studentSourcedId", students, "ext_student_contacts.csv")
refs(links, "contactSourcedId", contacts_ids, "ext_student_contacts.csv")
for s in students:
    ok(any(l["studentSourcedId"] == s and l["educationalRightsHolder"] == "true" for l in links), f"{s}: no educational rights holder")
    ok(any(l["studentSourcedId"] == s and l["emergencyPriority"] for l in links), f"{s}: no emergency contact")
ext_staff = unique(load("ext_staff.csv"), "sourcedId", "ext_staff.csv")
ok(set(ext_staff) == staff_ids, "ext_staff.csv: one row per staff user")
refs([r for r in ext_staff.values() if r["supervisorSourcedId"]], "supervisorSourcedId", ext_staff, "ext_staff.csv")
for r in ext_staff.values():
    if r["staffType"] == "para_rbt":
        ok(ext_staff.get(r["supervisorSourcedId"], {}).get("staffType") == "bcba", f"RBT {r['sourcedId']} must be supervised by a BCBA")
creds = load("ext_staff_credentials.csv")
refs(creds, "staffSourcedId", ext_staff, "ext_staff_credentials.csv")
# credential numbers are staff personal data outside the student data agreement: optional, left empty
for r in creds:
    ok(not (r.get("number") or "").strip(), f"ext_staff_credentials.csv: {r['staffSourcedId']} has a credential number (must be empty)")
    ok(bool(r.get("type")) and bool(r.get("field")), f"ext_staff_credentials.csv: {r['staffSourcedId']} needs type and field")
# no pronouns in the sample data (and never in any AI request)
for r in users.values() if isinstance(users, dict) else users:
    ok(not (r.get("pronouns") or "").strip(), f"users.csv: {r['sourcedId']} has pronouns (must be empty)")
caseloads = load("ext_caseloads.csv")
refs(caseloads, "studentSourcedId", students, "ext_caseloads.csv")
refs(caseloads, "staffSourcedId", ext_staff, "ext_caseloads.csv")
programs = load("ext_student_programs.csv")
refs(programs, "studentSourcedId", students, "ext_student_programs.csv")
periods = unique(load("ext_periods.csv"), "sourcedId", "ext_periods.csv")
refs(list(classes.values()), "periods", periods, "classes.csv", multi=True, allow_blank=True)

# ---------------------------------------------------------------- attendance (Oct 10)
import datetime as _dt  # noqa: E402
codes = unique(load("ext_attendance_codes.csv"), "code", "ext_attendance_codes.csv")
ok(set(codes) == {"P", "AE", "AU", "T"}, "ext_attendance_codes.csv: codes must be P, AE, AU, T")
att = load("ext_attendance.csv")
unique(att, "sourcedId", "ext_attendance.csv")
refs(att, "studentSourcedId", students, "ext_attendance.csv")
refs(att, "code", codes, "ext_attendance.csv")
seen_day = set()
for r in att:
    d = _dt.date.fromisoformat(r["date"])
    ok("2026-08-03" <= r["date"] <= "2026-10-09", f"ext_attendance.csv: {r['sourcedId']} outside Aug 3-Oct 9")
    ok(d.weekday() < 5 and r["date"] != "2026-09-07", f"ext_attendance.csv: {r['sourcedId']} not a school day")
    ok(r["label"] == "Fictional sample data", f"ext_attendance.csv: {r['sourcedId']} missing label")
    ok(r["date"] >= ext_st[r["studentSourcedId"]]["entryDate"], f"ext_attendance.csv: {r['sourcedId']} before entry")
    key = (r["studentSourcedId"], r["date"])
    ok(key not in seen_day, f"ext_attendance.csv: two rows for {key}")
    seen_day.add(key)
for s in students:
    ok(any(k[0] == s for k in seen_day), f"{s}: no attendance")
checks["attendance_rows"] = len(att)

# ---------------------------------------------------------------- IEP JSON
ieps = {}
for p in sorted((ROOT / "iep" / "json").glob("*.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    ok(d.get("fictional") is True and d.get("label") == "Fictional sample data", f"{p.name}: missing fictional flag/label")
    ok(p.stem == d["sourcedId"], f"{p.name}: file name != sourcedId")
    ok(d["sourcedId"] not in ieps, f"duplicate IEP {d['sourcedId']}")
    ieps[d["sourcedId"]] = d
checks["iep_json_files"] = len(ieps)
goal_ids = set()
active = Counter()
for i, d in ieps.items():
    ok(d["studentSourcedId"] in students, f"{i}: student does not resolve")
    ok(d["startDate"] <= d["endDate"], f"{i}: start after end")
    ok(d.get("supersedes") is None or d["supersedes"] in ieps, f"{i}: supersedes does not resolve")
    ok(d.get("caseManagerSourcedId") in ext_staff, f"{i}: case manager does not resolve")
    ok(d["responsibleLea"] in orgs, f"{i}: responsibleLea does not resolve")
    ok(all(gp in sess for gp in d["progressReportingPeriods"]), f"{i}: reporting period does not resolve")
    ok(bool(d.get("eligibility")) and bool(d.get("goals")) and bool(d.get("services")) and bool(d.get("accommodations")),
       f"{i}: needs eligibility, goals, services and accommodations")
    if d["status"] == "active":
        active[d["studentSourcedId"]] += 1
    for g in d["goals"]:
        ok(g["sourcedId"] not in goal_ids, f"{i}: duplicate goal id {g['sourcedId']}")
        goal_ids.add(g["sourcedId"])
        ok(all(g.get(k) for k in ("baseline", "statement", "criteriaForMastery", "measurementMethod")), f"{g['sourcedId']}: goal fields missing")
    for s in d["services"]:
        ok(s["providerSourcedId"] is None or s["providerSourcedId"] in ext_staff, f"{s['sourcedId']}: provider does not resolve")
        ok(s["providerSourcedId"] is None or any(c["studentSourcedId"] == d["studentSourcedId"] and c["staffSourcedId"] == s["providerSourcedId"]
                                                 for c in caseloads), f"{s['sourcedId']}: provider not on the student's caseload")
    for pr in d.get("goalProgress", []):
        ok(pr["goalSourcedId"] in {g["sourcedId"] for g in d["goals"]}, f"{i}: progress for unknown goal")
        ok(pr["reportingPeriodSourcedId"] in sess, f"{i}: progress period does not resolve")
    for t in d.get("team", []):
        ok((t.get("staffSourcedId") in ext_staff) or (t.get("contactSourcedId") in contacts_ids), f"{i}: team member does not resolve")
    for c in d.get("consents", []):
        ok(c["givenBySourcedId"] in contacts_ids, f"{i}: consent giver does not resolve")
    b = d.get("behaviorPlan")
    if b:
        ok(b["dataCollection"]["responsibleSourcedId"] in ext_staff, f"{i}: BIP data collector does not resolve")
        ok(d["specialFactors"]["bipDeveloped"] is True, f"{i}: BIP present but special factor says no BIP")
    text = json.dumps(d).lower()
    for bad in ("diagnos", "medication", "mg ", "prescri"):
        if bad == "diagnos":
            ok("diagnosed" not in text, f"{i}: health detail ('diagnosed') - health must stay a flag")
        else:
            ok(bad not in text, f"{i}: health detail ({bad.strip()!r}) - health must stay a flag")
sped = {p["studentSourcedId"]: p for p in programs if p["program"] == "sped_iep"}
ok(set(sped) == set(active), "every sped_iep student needs exactly one active IEP (and vice versa)")
ok(all(n == 1 for n in active.values()), "more than one active IEP for a student")
for s, p in sped.items():
    ok(p["planSourcedId"] in ieps and ieps[p["planSourcedId"]]["status"] == "active", f"{s}: sped program plan is not the active IEP")
plans504 = unique(load("iep/csv/section504_plans.csv"), "sourcedId", "section504_plans.csv")
refs(list(plans504.values()), "studentSourcedId", students, "section504_plans.csv")
for p in programs:
    if p["program"] == "section_504":
        ok(p["planSourcedId"] in plans504, f"{p['studentSourcedId']}: 504 plan does not resolve")

# ---------------------------------------------------------------- IEP CSV flattening == JSON
flat = {f: load(f"iep/csv/{f}.csv") for f in ["ieps", "iep_eligibility", "iep_present_levels", "iep_goals", "iep_objectives", "iep_services",
                                                "iep_accommodations", "iep_goal_progress", "iep_team", "iep_notices", "iep_consents",
                                                "behavior_plans", "behavior_targets"]}
ok({r["sourcedId"] for r in flat["ieps"]} == set(ieps), "ieps.csv != IEP JSON set")
expect = {"iep_eligibility": "eligibility", "iep_present_levels": "presentLevels", "iep_goals": "goals", "iep_services": "services",
          "iep_accommodations": "accommodations", "iep_goal_progress": "goalProgress", "iep_team": "team", "iep_notices": "notices",
          "iep_consents": "consents"}
for f, k in expect.items():
    ok(len(flat[f]) == sum(len(d.get(k) or []) for d in ieps.values()), f"{f}.csv row count != JSON {k}")
    refs(flat[f], "iepSourcedId", ieps, f"{f}.csv")
ok({r["sourcedId"] for r in flat["iep_goals"]} == goal_ids, "iep_goals.csv ids != JSON goal ids")
refs(flat["iep_objectives"], "goalSourcedId", goal_ids, "iep_objectives.csv")
ok(len(flat["behavior_plans"]) == sum(1 for d in ieps.values() if d.get("behaviorPlan")), "behavior_plans.csv != JSON")

# ---------------------------------------------------------------- the seven focus students
dwarfs = ["Doc", "Grumpy", "Happy", "Sleepy", "Bashful", "Sneezy", "Dopey"]
for n in dwarfs:
    m = [u for u in users.values() if u["givenName"] == n and u["familyName"] == "Dwarf" and u["sourcedId"] in students]
    ok(len(m) == 1, f"focus student {n} Dwarf missing or duplicated")
    if m:
        sid = m[0]["sourcedId"]
        cur = [d for d in ieps.values() if d["studentSourcedId"] == sid and d["status"] == "active"]
        ok(len(cur) == 1 and all(cur[0].get(k) for k in ("presentLevels", "specialFactors", "team", "notices", "consents", "goalProgress", "lreExplanation")),
           f"{n} Dwarf: full IEP sections missing")
        ok(all(g["objectives"] for g in cur[0]["goals"]) if cur else False, f"{n} Dwarf: every goal needs short-term objectives")
        ok(ext_st[sid]["homeroomClassSourcedId"] == "cls-rm104-hr", f"{n} Dwarf: not in Alex Morgan's homeroom")
lead = [u for u in users.values() if u["givenName"] == "Alex" and u["familyName"] == "Morgan"]
ok(len(lead) == 1 and any(e["userSourcedId"] == lead[0]["sourcedId"] and e["classSourcedId"] == "cls-rm104-hr" and e["role"] == "teacher" for e in enr),
   "Alex Morgan must teach the RM104 homeroom")

# ---------------------------------------------------------------- obviously fake contact data, file names
for u in users.values():
    for col in ("email", "username"):
        v = u[col]
        if "@" in v:
            ok(re.search(r"@([a-z0-9-]+\.)*example\.(com|org)$", v) is not None, f"{u['sourcedId']}: {col} {v} not on example.com/.org")
    for col in ("phone", "sms"):
        if u[col]:
            ok(re.fullmatch(r"\(\d{3}\) 555-01\d\d", u[col]) is not None, f"{u['sourcedId']}: {col} {u[col]} is not a 555-01xx number")
for p in ROOT.rglob("*"):
    ok(not re.search(r"(19|20)\d\d-\d\d-\d\d", p.name), f"file name looks like it carries a date: {p.name}")
    ok(not any(n.lower() in p.name.lower() for n in dwarfs + ["snow"]), f"file name carries a person's name: {p.name}")

# ---------------------------------------------------------------- report
print(f"sis-demo validation: {ROOT}")
print(f"  students={len(students)} staff={len(staff_ids)} contacts={len(contacts_ids)} classes={len(classes)} "
      f"enrollments={len(enr)} caseloads={len(caseloads)} iep_versions={len(ieps)} active_ieps={sum(active.values())} "
      f"504_plans={len(plans504)} goals={len(goal_ids)}")
print(f"  attendance rows={checks['attendance_rows']}")
print(f"  homeroom sizes: {dict(sorted(room_size.items()))}")
print(f"  csv rows checked={checks['csv_rows']} iep json files={checks['iep_json_files']} checks run={checks['checks']}")
if errors:
    print(f"FAIL: {len(errors)} problem(s)")
    for e in errors[:50]:
        print("  -", e)
    sys.exit(1)
print("PASS: every reference resolves and every record carries the fictional flag")
