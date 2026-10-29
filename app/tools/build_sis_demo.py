#!/usr/bin/env python3
"""Build the FICTIONAL sample school dataset in app/samples/sis-demo/ (import layout from docs/sis-schema.md).

    python3 app/tools/build_sis_demo.py      # rewrites app/samples/sis-demo/ (deterministic, seeded)
    python3 app/tools/validate_sis_demo.py   # checks links and the fictional flag

Everything is fictional sample data for the sign-in-only demo (David, Oct 10 2026): character
first names, invented people, example.com/example.org emails, 555-01xx phones, fake addresses,
invented assessment results. Eligibility and needs are assigned independently of character names.
Health is a "health alert on file" flag only. School name (David, Oct 10): Everyday Heroes Academy.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import random
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sis_demo_dwarfs import DWARFS  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "samples" / "sis-demo"
FICT = "true"                                  # metadata.fictional on every CSV row
LABEL = "Fictional sample data"
rng = random.Random(20261010)
D = dt.date.fromisoformat

SCHOOL = "org-school-0001"
DISTRICT = "org-district-0001"
SY = "as-sy-2026-27"
SEM = ["as-sem-2026-1", "as-sem-2027-2"]
GP = ["as-gp-2026-q1", "as-gp-2026-q2", "as-gp-2027-q3", "as-gp-2027-q4"]
GP_DATES = [("2026-08-03", "2026-10-09"), ("2026-10-13", "2026-12-18"), ("2027-01-05", "2027-03-12"), ("2027-03-15", "2027-05-21")]

# ------------------------------------------------------------------ attendance
ATT_START, ATT_END = "2026-08-03", "2026-10-09"        # school year to date
ATT_HOLIDAYS = {"2026-09-07"}                           # Labor Day
ATT_CODES = [  # code, description, counts as absent, counts as tardy
    ("P", "Present", False, False),
    ("AE", "Absent, excused", True, False),
    ("AU", "Absent, unexcused", True, False),
    ("T", "Tardy (present, arrived late)", False, True),
]

# ------------------------------------------------------------------ staff
STAFF = [  # key, first, last, staffType, onerosterRole, title, room
 ("snow", "Alex", "Morgan", "teacher", "teacher", "Lead Special Education Teacher", "RM104"),
 ("belle", "Belle", "Beaumont", "teacher", "teacher", "Special Education Teacher", "RM101"),
 ("tiana", "Tiana", "Rogers", "teacher", "teacher", "Special Education Teacher", "RM102"),
 ("mulan", "Mulan", "Fa", "teacher", "teacher", "Special Education Teacher", "RM103"),
 ("rapunzel", "Rapunzel", "Hart", "teacher", "teacher", "Special Education Teacher", "RM105"),
 ("hercules", "Hercules", "Stone", "teacher", "teacher", "Special Education Teacher", "RM106"),
 ("moana", "Moana", "Kealoha", "teacher", "teacher", "Special Education Teacher", "RM107"),
 ("ariel", "Ariel", "Marin", "teacher", "teacher", "Special Education Teacher (intensive supports)", "RM108"),
 ("mary", "Mary", "Poppins", "bcba", "ext:bcba", "Board Certified Behavior Analyst", None),
 ("flynn", "Flynn", "Rider", "para_rbt", "aide", "Registered Behavior Technician", "RM104"),
 ("kristoff", "Kristoff", "Bjorgman", "para_rbt", "aide", "Registered Behavior Technician", "RM106"),
 ("shang", "Shang", "Li", "para_rbt", "aide", "Registered Behavior Technician", "RM107"),
 ("tadashi", "Tadashi", "Hamada", "para_rbt", "aide", "Registered Behavior Technician", "RM108"),
 ("naveen", "Naveen", "Lambert", "para_rbt", "aide", "Registered Behavior Technician", "RM102"),
 ("jasmine", "Jasmine", "Nadir", "slp", "ext:slp", "Speech-Language Pathologist", None),
 ("esmeralda", "Esmeralda", "Rossi", "ot", "ext:ot", "Occupational Therapist", None),
 ("fauna", "Fauna", "Greenleaf", "pt", "ext:pt", "Physical Therapist", None),
 ("flora", "Flora", "Rose", "dhh_teacher", "teacher", "Itinerant Teacher of the Deaf/Hard of Hearing", None),
 ("merryweather", "Merryweather", "Blue", "tvi", "teacher", "Teacher of Students with Visual Impairments / O&M Specialist", None),
 ("eudora", "Eudora", "Ellis", "counselor", "counselor", "School Counselor", None),
 ("elsa", "Elsa", "Winters", "case_manager", "ext:caseManager", "Special Education Coordinator / Case Manager", None),
 ("cinderella", "Cinderella", "Ashby", "principal", "principal", "Principal", None),
 ("potts", "Agatha", "Potts", "office", "ext:officeAdmin", "Office Administrator", None),
]
APP_ROLE = {"teacher": "teacher", "dhh_teacher": "teacher", "tvi": "teacher", "para_rbt": "rbt", "bcba": "teacher", "slp": "teacher",
            "ot": "teacher", "pt": "teacher", "counselor": "teacher", "case_manager": "admin", "principal": "admin", "office": "admin"}
SID = {k: f"u-stf-{i:04d}" for i, (k, *_r) in enumerate(STAFF, 1)}
STAFF_BY = {s[0]: s for s in STAFF}

ROOMS = [  # code, teacher, rbt, grades, size
 ("RM101", "belle", "naveen", ["KG", "01"], 11),
 ("RM102", "tiana", "naveen", ["KG", "01", "02"], 11),
 ("RM103", "mulan", None, ["01", "02", "03"], 11),
 ("RM104", "snow", "flynn", ["02", "03", "04", "05"], 10),
 ("RM105", "rapunzel", None, ["03", "04"], 11),
 ("RM106", "hercules", "kristoff", ["03", "04", "05"], 12),
 ("RM107", "moana", "shang", ["04", "05"], 12),
 ("RM108", "ariel", "tadashi", ["KG", "01", "02", "03", "04", "05"], 9),
]
SUBJECTS = [("hr", "Homeroom", "crs-hr", ""), ("rdg", "Reading", "crs-rdg", " Reading"),
            ("mth", "Math", "crs-mth", " Math"), ("soc", "Social Skills", "crs-soc", " Social Skills")]
PERIODS = [("per-arrival", "Arrival", "07:45", "08:00"), ("per-meeting", "Morning Meeting", "08:00", "08:20"),
           ("per-reading", "Reading Block", "08:20", "09:50"), ("per-services", "Related Services / Specials", "09:50", "10:35"),
           ("per-math", "Math Block", "10:35", "11:45"), ("per-lunch", "Lunch and Recess", "11:45", "12:30"),
           ("per-social", "Social Skills / Science", "12:30", "13:15"), ("per-centers", "Life Skills / Centers", "13:15", "14:15"),
           ("per-dismissal", "Dismissal", "14:15", "14:30")]
CLASS_PERIODS = {"hr": "per-meeting", "rdg": "per-reading", "mth": "per-math", "soc": "per-social"}

HERO = ["Aladdin", "Aurora", "Merida", "Anna", "Kida", "Milo", "Hiro", "Miguel", "Mirabel", "Luisa", "Isabela", "Bruno",
        "Raya", "Mei", "Luca", "Alberto", "Giulia", "Joy", "Riley", "Dash", "Violet", "Lilo", "Nani", "Kuzco", "Pacha",
        "Taran", "Penny", "Wendy", "Peter", "Alice", "Ralph", "Vanellope", "Judy", "Nick", "Russell", "Ellie", "Remy",
        "Asha", "Ian", "Barley"]
GENERIC = ["Ava", "Liam", "Noah", "Emma", "Olivia", "Elijah", "Mateo", "Sofia", "Amara", "Jayden", "Zoe", "Caleb", "Priya",
           "Omar", "Nia", "Ethan", "Lucas", "Chloe", "Isaiah", "Layla", "Mason", "Grace", "Diego", "Aaliyah", "Carter", "Hana",
           "Elias", "Maya", "Xavier", "Leah", "Kai", "Imani", "Owen", "Ruby", "Andre", "Sara", "Theo", "Jada", "Malik", "Lily"]
SURNAMES = ["Johnson", "Williams", "Brown", "Garcia", "Martinez", "Davis", "Lopez", "Wilson", "Anderson", "Thomas", "Taylor",
            "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis",
            "Robinson", "Walker", "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores", "Green",
            "Adams", "Nelson", "Baker", "Hall", "Campbell", "Mitchell", "Carter", "Roberts", "Patel", "Okafor", "Kim", "Chen",
            "Washington", "Brooks", "Bennett", "Reyes", "Coleman", "Bell", "Hughes", "Price", "Freeman", "Shah", "Diaz",
            "Ortiz", "Murphy", "Bailey"]
ADULT = ["Marisol", "Denise", "Robert", "Angela", "Kevin", "Tamika", "Luis", "Jennifer", "Michael", "Keisha", "David", "Linh",
         "Rosa", "James", "Fatima", "Carlos", "Monique", "Brian", "Yolanda", "Anthony", "Sunita", "Gregory", "Patricia",
         "Hector", "Lakshmi", "Darnell", "Ingrid", "Samuel", "Beatriz", "Terrence", "Nadia", "Paul", "Erica", "Victor",
         "Gloria", "Marcus", "Helen", "Jamal", "Mei", "Oscar", "Riley", "Tyrone", "Alma", "Kenji", "Bianca", "Curtis"]
DWARF_GUARDIANS = {"doc": [("Marigold", "mother"), ("Elliot", "father")], "grumpy": [("Hazel", "grandmother")],
                   "happy": [("June", "mother"), ("Oren", "stepfather")], "sleepy": [("Rowan", "father"), ("Iris", "mother")],
                   "bashful": [("Clementine", "mother")], "sneezy": [("Wren", "foster parent")], "dopey": [("Basil", "father"), ("Poppy", "mother")]}
NONCUSTODIAL = {"sneezy": ("Rory", "Dwarf", "example.com"), "happy": ("Linden", "Dwarf", "example.org")}
STREETS = ["Example Way", "Sample Lane", "Placeholder Ct", "Fictional Ave", "Demo Drive", "Mockingbird Test Rd", "Storybook Ln", "Pretend Pl"]
LANGS = [("Spanish", "spa"), ("Vietnamese", "vie"), ("Arabic", "ara"), ("Haitian Creole", "hat"), ("Korean", "kor")]

# lighter IEPs: category -> (state code, federal code, label)
CAT = {"AUT": ("GA-AUT", "AUT", "Autism"), "SLD": ("GA-SLD", "SLD", "Specific Learning Disability"),
       "OHI": ("GA-OHI", "OHI", "Other Health Impairment"), "EBD": ("GA-EBD", "EMN", "Emotional and Behavioral Disorder"),
       "SLI": ("GA-SLI", "SLI", "Speech-Language Impairment"), "ID": ("GA-ID-MOD", "ID", "Intellectual Disability (moderate)"),
       "IDM": ("GA-ID-MILD", "ID", "Intellectual Disability (mild)"), "SDD": ("GA-SDD", "DD", "Significant Developmental Delay")}
ROOM_WEIGHTS = {
 "RM101": {"AUT": 4, "SDD": 3, "SLI": 2, "EBD": 1}, "RM102": {"AUT": 3, "SDD": 3, "SLI": 2, "OHI": 1, "EBD": 1},
 "RM103": {"AUT": 3, "SLD": 2, "OHI": 2, "EBD": 2, "SLI": 1, "IDM": 1}, "RM104": {"SLD": 1, "OHI": 1, "AUT": 1},
 "RM105": {"SLD": 3, "OHI": 2, "AUT": 2, "EBD": 2, "IDM": 1}, "RM106": {"EBD": 4, "AUT": 3, "OHI": 2, "SLD": 2},
 "RM107": {"SLD": 3, "EBD": 3, "OHI": 2, "AUT": 2, "IDM": 1}, "RM108": {"ID": 4, "AUT": 4}}
GOAL_BANK = {
 "AUT": [("communication", "Communication", "Requests help or a break with words or AAC in 2 of 10 opportunities.",
          "{fn} will request help or a break using words, signs or AAC in 8 of 10 opportunities across 3 sessions.", "8 of 10, 3 sessions", "SLP / teacher data"),
         ("social", "Behavioral", "Takes turns in a structured game with 3 adult prompts.",
          "{fn} will take turns with a peer in a structured activity for 5 exchanges with 1 or fewer prompts in 4 of 5 sessions.", "4 of 5 sessions", "Observation"),
         ("behavior", "Behavioral", "Transitions between activities with physical guidance in 50% of transitions.",
          "Using a visual schedule, {fn} will move to the next activity within 2 minutes with 1 or fewer verbal prompts in 80% of transitions.", "80% of transitions, 3 weeks", "Data sheet")],
 "SLD": [("reading", "Academic", "Reads 35 words correct per minute on instructional-level passages.",
          "{fn} will read instructional-level passages at 70 words correct per minute with 95% accuracy on 3 consecutive probes.", "70 wcpm, 3 probes", "Fluency probes"),
         ("written_expression", "Academic", "Writes 2 related sentences with an organizer.",
          "{fn} will write a 5-sentence paragraph with a topic sentence and conclusion, scoring 4 of 5 on the rubric in 4 of 5 samples.", "4 of 5 samples", "Writing rubric"),
         ("math", "Academic", "45% accuracy on grade-level computation.",
          "{fn} will solve grade-level computation problems with 80% accuracy across 3 consecutive probes.", "80%, 3 probes", "Curriculum-based probes")],
 "OHI": [("executive_functioning", "Behavioral", "On task in 50% of intervals.",
          "{fn} will stay on task during independent work in 80% of intervals across 5 sessions using a self-monitoring checklist.", "80%, 5 sessions", "Time sampling"),
         ("executive_functioning", "Behavioral", "Completes 55% of independent work.",
          "{fn} will complete 85% of independent work within the time given for 4 consecutive weeks.", "85%, 4 weeks", "Work log")],
 "EBD": [("behavior", "Behavioral", "Uses a coping strategy independently in 1 of 5 opportunities.",
          "When upset, {fn} will use a taught coping strategy in 4 of 5 observed opportunities for 4 consecutive weeks.", "4 of 5, 4 weeks", "Behavior data sheet"),
         ("social", "Behavioral", "Resolves peer conflict without adult help in 1 of 5 conflicts.",
          "{fn} will use a 4-step problem-solving routine with peers with 1 or fewer prompts in 4 of 5 conflicts.", "4 of 5", "Observation"),
         ("executive_functioning", "Behavioral", "Starts tasks within 2 minutes on 40% of occasions.",
          "{fn} will start assigned tasks within 2 minutes of directions on 80% of occasions across 3 weeks.", "80%, 3 weeks", "Latency recording")],
 "SLI": [("communication", "Communication", "Produces target sounds correctly in 40% of words.",
          "{fn} will produce target sounds in sentences with 80% accuracy across 3 consecutive sessions.", "80%, 3 sessions", "SLP data sheet"),
         ("communication", "Communication", "Uses 3-word phrases in 2 of 10 opportunities.",
          "{fn} will use 4-word phrases to comment or request in 8 of 10 opportunities across 3 sessions.", "8 of 10, 3 sessions", "SLP data sheet")],
 "ID": [("adaptive", "Functional", "Completes a 4-step arrival routine with physical prompts.",
         "{fn} will complete a 4-step arrival routine using a picture schedule with gestural prompts only in 4 of 5 days.", "4 of 5 days, 3 weeks", "Task analysis"),
        ("reading", "Academic", "Identifies 5 functional sight words.",
         "{fn} will read 20 functional sight words (signs, schedule words) with 90% accuracy across 3 probes.", "20 words, 90%, 3 probes", "Sight word probes"),
        ("math", "Academic", "Counts objects to 5 with 1:1 correspondence.",
         "{fn} will count up to 20 objects with 1:1 correspondence in 4 of 5 trials.", "4 of 5 trials", "Trial data")],
 "SDD": [("communication", "Communication", "Follows 1-step directions in 50% of trials.",
          "{fn} will follow 2-step classroom directions in 80% of trials across 3 sessions.", "80%, 3 sessions", "Teacher data"),
         ("pre_academic", "Academic", "Names 8 uppercase letters.",
          "{fn} will name all uppercase and lowercase letters with 90% accuracy across 3 probes.", "90%, 3 probes", "Letter naming probes"),
         ("motor_fine", "Functional", "Holds a pencil with a fisted grasp.",
          "{fn} will use a tripod grasp to trace and copy simple shapes in 4 of 5 trials.", "4 of 5 trials", "OT data")]}
CAT_SERVICES = {"AUT": [("speech_language", 30, 2, "slp"), ("behavior_aba", 30, 5, "rbt"), ("ot", 30, 1, "ot")],
                "SLD": [], "OHI": [], "EBD": [("behavior_aba", 30, 5, "rbt"), ("counseling", 30, 1, "counselor")],
                "SLI": [("speech_language", 30, 2, "slp")], "ID": [("speech_language", 30, 2, "slp"), ("ot", 30, 1, "ot")],
                "IDM": [("speech_language", 30, 1, "slp")], "SDD": [("speech_language", 30, 2, "slp"), ("ot", 30, 1, "ot")]}
CAT_ACC = {"AUT": ["Visual schedule and transition warnings", "Break card and quiet area", "Small group testing"],
           "SLD": ["Read aloud of directions and items (except reading measures)", "Graphic organizers", "Extended time (1.5x)"],
           "OHI": ["Chunked assignments with check-ins", "Movement breaks", "Separate setting for tests"],
           "EBD": ["Daily check-in/check-out", "Access to a cool-down area", "Choice of task order"],
           "SLI": ["Extra wait time for verbal responses", "Visual supports for directions"],
           "ID": ["Picture supports and modified materials", "Instruction on alternate achievement standards (modification)", "Frequent breaks"],
           "IDM": ["Modified assignments (reduced length)", "Picture supports", "Small group testing"],
           "SDD": ["Visual supports", "Short tasks with frequent breaks", "Small group setting"]}


def add_years(d: dt.date, n: int) -> dt.date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


def next_school_day(d: dt.date) -> dt.date:
    d += dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def write_csv(path: Path, header: list[str], rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header + ["metadata.fictional"], extrasaction="raise", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({**{k: r.get(k, "") for k in header}, "metadata.fictional": FICT})


def build():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    used_numbers, used_phones = set(), set()

    def student_number():
        while True:
            n = f"{rng.randint(104000, 109999)}"
            if n not in used_numbers:
                used_numbers.add(n)
                return n

    def phone():
        while True:
            p = f"({rng.choice(['404', '470', '678', '770'])}) 555-01{rng.randint(0, 99):02d}"
            if p not in used_phones:
                used_phones.add(p)
                return p

    # -------------------------------------------------- students (who is in which room)
    students = []   # dict per student
    hero, generic, surnames = HERO[:], GENERIC[:], SURNAMES[:]
    rng.shuffle(hero); rng.shuffle(generic); rng.shuffle(surnames)
    pool = [(n, True) for n in hero] + [(n, False) for n in generic]
    rng.shuffle(pool)
    for key, d in DWARFS.items():
        students.append(dict(key=key, first=d["first"], last="Dwarf", grade=d["grade"], room="RM104", dob=d["dob"], dwarf=True))
    for code, _t, _r, grades, size in ROOMS:
        have = sum(1 for s in students if s["room"] == code)
        for _ in range(size - have):
            first, _is_hero = pool.pop()
            g = rng.choice(grades if code != "RM104" else ["03", "04", "05"])
            gi = 0 if g == "KG" else int(g)
            dob = dt.date(2026 - 5 - gi - (1 if rng.random() < 0.5 else 0), rng.randint(1, 12), rng.randint(1, 28))
            if dob > D(f"{2026 - 5 - gi}-09-01"):
                dob = dob.replace(year=dob.year - 1)
            students.append(dict(key=None, first=first, last=rng.choice(surnames), grade=g, room=code, dob=dob.isoformat(), dwarf=False))
    assert len(students) == 87, len(students)
    for i, s in enumerate(students, 1):
        s["sid"] = f"u-stu-{i:04d}"
        s["number"] = student_number()
        s["gtid"] = f"999{rng.randint(1000000, 9999999)}"          # 10 digits, 999 prefix = clearly not a real GTID
    nondwarf = [s for s in students if not s["dwarf"]]
    rng.shuffle(nondwarf)
    for i, s in enumerate(nondwarf):
        s["plan"] = "iep" if i < 70 else ("504" if i < 76 else "none")
        if s["room"] == "RM108":
            s["plan"] = "iep"
    for s in students:
        if s["dwarf"]:
            s["plan"] = "iep"
    ell = rng.sample([s for s in nondwarf], 8)
    for s in students:
        s["ell"] = s in ell
        s["lang"] = rng.choice(LANGS) if s["ell"] else ("English", "eng")
        s["health_alert"] = s["key"] in ("sleepy", "bashful", "dopey") or (not s["dwarf"] and rng.random() < 0.12)
        s["funding"] = {"doc": "gsns", "grumpy": "gsns", "happy": "district_placement", "sleepy": "gsns", "bashful": "tuition",
                        "sneezy": "district_placement", "dopey": "gsns"}.get(s["key"]) or rng.choices(
            ["gsns", "tuition", "district_placement", "promise"], [55, 25, 15, 5])[0]
        s["transport"] = "district_special" if s["key"] == "sleepy" else rng.choices(["parent", "bus", "district_special"], [55, 30, 15])[0]
        s["entry"] = rng.choice(["2023-08-07", "2024-08-05", "2025-08-04", "2026-08-03"]) if not s["dwarf"] else "2024-08-05"

    # -------------------------------------------------- contacts
    contacts, links = [], []
    def add_contact(first, last, rel, guardian, s, emergency=None, email=True):
        cid = f"u-con-{len(contacts) + 1:04d}"
        dom = rng.choice(["example.com", "example.org"])
        em = f"{first.lower()}.{last.lower()}{rng.randint(1, 99)}@{dom}" if email else ""
        c = dict(sid=cid, first=first, last=last, email=em, mobile=phone(), home=phone() if rng.random() < 0.3 else "",
                 role="parent" if rel in ("mother", "father") else ("guardian" if guardian else "relative"),
                 street=f"{rng.randint(100, 9899)} {rng.choice(STREETS)}", city="Exampleville", state="GA",
                 zip=f"3999{rng.randint(0, 9)}", language=s["lang"][0] if s["ell"] else "English",
                 interpreter="true" if s["ell"] and rng.random() < 0.6 else "false", students=[s["sid"]])
        contacts.append(c)
        links.append(dict(studentSourcedId=s["sid"], contactSourcedId=cid, relationship=rel,
                          legalGuardian="true" if guardian else "false", educationalRightsHolder="true" if guardian else "false",
                          livesWith="true" if guardian else "false", receivesMail="true" if guardian else "false",
                          emergencyPriority=str(emergency) if emergency else "", pickup="allowed",
                          receivesWeeklyDraft="true" if guardian and em else "false"))
        return c
    for s in students:
        if s["dwarf"]:
            gl = DWARF_GUARDIANS[s["key"]]
            for j, (fn, rel) in enumerate(gl):
                add_contact(fn, "Dwarf" if rel != "foster parent" else "Hollis", rel, True, s, emergency=j + 1)
        else:
            n = 2 if rng.random() < 0.55 else 1
            rels = rng.sample(["mother", "father"], n) if rng.random() < 0.85 else [rng.choice(["grandmother", "aunt", "guardian", "foster parent"])]
            for j, rel in enumerate(rels):
                last = s["last"] if rel in ("mother", "father", "grandmother") or rng.random() < 0.5 else rng.choice(surnames)
                add_contact(rng.choice(ADULT), last, rel, True, s, emergency=j + 1)
        nguard = sum(1 for l in links if l["studentSourcedId"] == s["sid"])
        add_contact(rng.choice(ADULT), rng.choice(surnames), rng.choice(["aunt", "uncle", "neighbor", "family friend", "grandfather"]),
                    False, s, emergency=nguard + 1, email=False)
    # Non-custodial contacts (Ops, Oct 10; tasks#106): fixed values, no random draws, so every other
    # row is unchanged. They have an email but "receivesWeeklyDraft" is false: the weekly parent email
    # must leave them out, and the demo shows that.
    for key, (fn, ln, dom) in NONCUSTODIAL.items():
        s = next(x for x in students if x["key"] == key)
        cid = f"u-con-{len(contacts) + 1:04d}"
        contacts.append(dict(sid=cid, first=fn, last=ln, email=f"{fn.lower()}.{ln.lower()}@{dom}", mobile="(404) 555-0190",
                             home="", role="parent", street="1 Pretend Pl", city="Exampleville", state="GA", zip="39990",
                             language="English", interpreter="false", students=[s["sid"]]))
        links.append(dict(studentSourcedId=s["sid"], contactSourcedId=cid, relationship="non-custodial parent",
                          legalGuardian="false", educationalRightsHolder="false", livesWith="false", receivesMail="false",
                          emergencyPriority="", pickup="not_allowed", receivesWeeklyDraft="false"))
    s_by = {s["sid"]: s for s in students}

    # -------------------------------------------------- OneRoster: orgs, sessions, courses, classes
    write_csv(OUT / "orgs.csv", ["sourcedId", "status", "dateLastModified", "name", "type", "identifier", "parentSourcedId"], [
        dict(sourcedId=DISTRICT, name="Fictional County School District (sample)", type="district", identifier="FICT-LEA-000"),
        dict(sourcedId=SCHOOL, name="Everyday Heroes Academy", type="school", identifier="FICT-0001",
             parentSourcedId=DISTRICT)])
    sess = [dict(sourcedId=SY, title="School Year 2026-2027", type="schoolYear", startDate="2026-08-03", endDate="2027-05-21", schoolYear="2027"),
            dict(sourcedId=SEM[0], title="Semester 1", type="semester", startDate="2026-08-03", endDate="2026-12-18", parentSourcedId=SY, schoolYear="2027"),
            dict(sourcedId=SEM[1], title="Semester 2", type="semester", startDate="2027-01-05", endDate="2027-05-21", parentSourcedId=SY, schoolYear="2027")]
    for i, (gid, (a, b)) in enumerate(zip(GP, GP_DATES)):
        sess.append(dict(sourcedId=gid, title=f"Reporting Period {i + 1}", type="gradingPeriod", startDate=a, endDate=b,
                         parentSourcedId=SEM[0] if i < 2 else SEM[1], schoolYear="2027"))
    write_csv(OUT / "academicSessions.csv", ["sourcedId", "status", "dateLastModified", "title", "type", "startDate", "endDate", "parentSourcedId", "schoolYear"], sess)
    write_csv(OUT / "courses.csv", ["sourcedId", "status", "dateLastModified", "schoolYearSourcedId", "title", "courseCode", "grades", "orgSourcedId", "subjects"], [
        dict(sourcedId=cid, schoolYearSourcedId=SY, title=f"{t} (K-5 alternative)", courseCode=cid.split("-")[1].upper() + "-ALT",
             grades="KG,01,02,03,04,05", orgSourcedId=SCHOOL, subjects=t) for _k, t, cid, _x in SUBJECTS])
    classes = []
    for code, *_r in ROOMS:
        for k, t, cid, suffix in SUBJECTS:
            grades = next(r[3] for r in ROOMS if r[0] == code)
            classes.append(dict(sourcedId=f"cls-{code.lower()}-{k}", title=f"{code} {t}", grades=",".join(grades), courseSourcedId=cid,
                                classCode=f"{code}{suffix}", classType="homeroom" if k == "hr" else "scheduled",
                                location=f"Room {code[2:]}", schoolSourcedId=SCHOOL, termSourcedIds=SY, subjects=t,
                                periods=CLASS_PERIODS[k]))
    write_csv(OUT / "classes.csv", ["sourcedId", "status", "dateLastModified", "title", "grades", "courseSourcedId", "classCode", "classType",
                                    "location", "schoolSourcedId", "termSourcedIds", "subjects", "subjectCodes", "periods"], classes)

    # -------------------------------------------------- users, roles, enrollments, demographics
    users, roles, enr = [], [], []
    for s in students:
        agents = [l["contactSourcedId"] for l in links if l["studentSourcedId"] == s["sid"]]
        users.append(dict(sourcedId=s["sid"], enabledUser="true", username=f"stu{s['number']}", userIds=f"{{studentNumber:{s['number']}}}",
                          givenName=s["first"], familyName=s["last"], identifier=s["number"], agentSourcedIds=",".join(agents),
                          grades=s["grade"], primaryOrgSourcedId=SCHOOL))
        roles.append(dict(sourcedId=f"rol-{s['sid']}", userSourcedId=s["sid"], roleType="primary", role="student", orgSourcedId=SCHOOL,
                          beginDate=s["entry"]))
        for k, *_x in SUBJECTS:
            enr.append(dict(sourcedId=f"enr-{s['sid']}-{k}", classSourcedId=f"cls-{s['room'].lower()}-{k}", schoolSourcedId=SCHOOL,
                            userSourcedId=s["sid"], role="student", primary="false", beginDate=max(s["entry"], "2026-08-03")))
    for key, first, last, stype, orrole, title, room in STAFF:
        sid = SID[key]
        users.append(dict(sourcedId=sid, enabledUser="true", username=f"{first.lower()}.{last.lower()}@school.example.org",
                          givenName=first, familyName=last, identifier=f"E{20000 + list(SID).index(key) * 37}",
                          email=f"{first.lower()}.{last.lower()}@school.example.org", phone="(404) 555-0100", primaryOrgSourcedId=SCHOOL))
        roles.append(dict(sourcedId=f"rol-{sid}", userSourcedId=sid, roleType="primary", role=orrole, orgSourcedId=SCHOOL, beginDate="2026-07-27"))
    for code, teacher, rbt, *_r in ROOMS:
        for k, *_x in SUBJECTS:
            enr.append(dict(sourcedId=f"enr-{SID[teacher]}-{code.lower()}-{k}", classSourcedId=f"cls-{code.lower()}-{k}", schoolSourcedId=SCHOOL,
                            userSourcedId=SID[teacher], role="teacher", primary="true", beginDate="2026-08-03"))
            if rbt:
                enr.append(dict(sourcedId=f"enr-{SID[rbt]}-{code.lower()}-{k}", classSourcedId=f"cls-{code.lower()}-{k}", schoolSourcedId=SCHOOL,
                                userSourcedId=SID[rbt], role="aide", primary="false", beginDate="2026-08-03"))
    for c in contacts:
        users.append(dict(sourcedId=c["sid"], enabledUser="false", username=c["email"] or f"contact-{c['sid'][-4:]}", givenName=c["first"],
                          familyName=c["last"], email=c["email"], sms=c["mobile"], phone=c["home"], agentSourcedIds=",".join(c["students"]),
                          primaryOrgSourcedId=SCHOOL))
        roles.append(dict(sourcedId=f"rol-{c['sid']}", userSourcedId=c["sid"], roleType="primary", role=c["role"], orgSourcedId=SCHOOL))
    write_csv(OUT / "users.csv", ["sourcedId", "status", "dateLastModified", "enabledUser", "username", "userIds", "givenName", "familyName",
                                  "middleName", "identifier", "email", "sms", "phone", "agentSourcedIds", "grades", "password",
                                  "userMasterIdentifier", "preferredGivenName", "preferredMiddleName", "preferredFamilyName",
                                  "primaryOrgSourcedId", "pronouns"], users)   # pronouns: column kept for the layout, always empty (Ops)
    write_csv(OUT / "roles.csv", ["sourcedId", "status", "dateLastModified", "userSourcedId", "roleType", "role", "beginDate", "endDate",
                                  "orgSourcedId", "userProfileSourcedId"], roles)
    write_csv(OUT / "enrollments.csv", ["sourcedId", "status", "dateLastModified", "classSourcedId", "schoolSourcedId", "userSourcedId",
                                        "role", "primary", "beginDate", "endDate"], enr)
    write_csv(OUT / "demographics.csv", ["sourcedId", "status", "dateLastModified", "birthDate"],
              [dict(sourcedId=s["sid"], birthDate=s["dob"]) for s in students])

    # -------------------------------------------------- IEPs
    ieps, plans504, programs, caseloads = [], [], [], []
    def caseload(stu, staff, role, begin="2026-08-03"):
        row = dict(studentSourcedId=stu, staffSourcedId=SID[staff], role=role, beginDate=begin)
        if row not in caseloads:
            caseloads.append(row)
    room_of = {r[0]: r for r in ROOMS}
    n_iep = 0

    def provider_for(role, room):
        return {"slp": "jasmine", "ot": "esmeralda", "pt": "fauna", "bcba": "mary", "counselor": "eudora",
                "rbt": room_of[room][2] or "kristoff", "sped_teacher": room_of[room][1]}[role]

    def iep_doc(s, n, version, status, meeting, mtype, extra):
        m = D(meeting)
        start = next_school_day(m)
        doc = {"fictional": True, "label": LABEL,
               "sourcedId": f"iep-{n:04d}-v{version}", "studentSourcedId": s["sid"], "version": version, "status": status,
               "supersedes": f"iep-{n:04d}-v{version - 1}" if version > 1 else None, "source": "sis_import",
               "meetingType": mtype, "meetingDate": meeting, "startDate": start.isoformat(),
               "endDate": (add_years(m, 1) - dt.timedelta(days=1)).isoformat(), "annualReviewDue": (add_years(m, 1) - dt.timedelta(days=1)).isoformat(),
               "responsibleLea": DISTRICT, "schoolSourcedId": SCHOOL, "schoolYearSourcedId": SY,
               "progressReportFrequency": "each_reporting_period",
               "progressReportingPeriods": GP}
        doc.update(extra)
        return doc

    for s in students:
        if s["plan"] != "iep":
            continue
        n_iep += 1
        room = s["room"]
        cm = "elsa" if room == "RM108" else room_of[room][1]
        if s["dwarf"]:
            d = DWARFS[s["key"]]
            team = [dict(role="lea_rep", staffSourcedId=SID["cinderella"], attended=True),
                    dict(role="sped_teacher", staffSourcedId=SID["snow"], attended=True),
                    dict(role="gen_ed_teacher", staffSourcedId=SID["belle"], attended=True,
                         note="General curriculum representative (the school has no general education classrooms)"),
                    dict(role="case_manager", staffSourcedId=SID["snow"], attended=True)]
            for l in links:
                if l["studentSourcedId"] == s["sid"] and l["educationalRightsHolder"] == "true":
                    team.insert(0, dict(role="parent", contactSourcedId=l["contactSourcedId"], attended=True))
            for role, key in d["team_extra"]:
                team.append(dict(role="related_service" if role not in ("bcba", "counselor") else role, staffSourcedId=SID[key], attended=True))
            for t in team:
                t["signature"] = f"Signed electronically {d['meeting']} (fictional)"
            m = D(d["meeting"])
            goals, progress = [], []
            ov = dict(d["progress"])
            for gi, g in enumerate(d["goals"], 1):
                gid = f"iep-{n_iep:04d}-v2-g{gi}"
                goals.append({"sourcedId": gid, "number": gi, "area": g["area"], "category": g["category"], "baseline": g["baseline"],
                              "statement": g["statement"], "criteriaForMastery": g["criteria"], "measurementMethod": g["method"],
                              "targetDate": (add_years(m, 1) - dt.timedelta(days=1)).isoformat(), "status": "active",
                              "objectives": [{"sourcedId": f"{gid}-o{oi}", "number": oi, "statement": st, "criteria": cr}
                                             for oi, (st, cr) in enumerate(g["objectives"], 1)]})
                progress.append({"goalSourcedId": gid, "reportingPeriodSourcedId": GP[0],
                                 "progressCode": ov.get(g["area"], g["code"]),
                                 "narrative": f"Reporting period 1 (Aug 3-Oct 9, 2026): {g['q1']}. Baseline: {g['baseline']}",
                                 "enteredBySourcedId": SID["snow"], "enteredAt": "2026-10-09"})
            services = []
            for si, sv in enumerate(d["services"], 1):
                services.append({"sourcedId": f"iep-{n_iep:04d}-v2-s{si}", "type": sv["type"], "delivery": sv["delivery"], "setting": sv["setting"],
                                 "minutes": sv["minutes"], "frequencyCount": sv["frequencyCount"], "frequencyPeriod": sv["frequencyPeriod"],
                                 "groupSize": sv["groupSize"], "providerRole": sv["providerRole"],
                                 "providerSourcedId": SID[sv["provider"]] if sv["provider"] else None, "location": sv["location"],
                                 "startDate": (d["esy"].get("startDate") if sv["esy"] else next_school_day(m).isoformat()),
                                 "endDate": (d["esy"].get("endDate") if sv["esy"] else (add_years(m, 1) - dt.timedelta(days=1)).isoformat()),
                                 "esy": sv["esy"]})
                if sv["provider"]:
                    caseload(s["sid"], sv["provider"], {"sped_teacher": "teacher", "dhh_teacher": "dhh_teacher", "coms": "tvi"}.get(sv["providerRole"], sv["providerRole"]))
            accs = [{"sourcedId": f"iep-{n_iep:04d}-v2-a{ai}", "category": c, "description": t, "subjects": subj}
                    for ai, (c, t, subj) in enumerate(d["accommodations"], 1)]
            for ti, (test, setting, timing, pres, resp, std) in enumerate(d["state_testing"], 1):
                accs.append({"sourcedId": f"iep-{n_iep:04d}-v2-t{ti}", "category": "state_testing", "description": test, "subjects": [],
                             "stateTestSetting": setting, "stateTestTiming": timing, "stateTestPresentation": pres,
                             "stateTestResponse": resp, "standardOrConditional": std})
            notice = (m - dt.timedelta(days=14))
            extra = {
                "lastEvaluationDate": d["last_eval"], "reevaluationDue": d["reeval_due"],
                "eligibilityDate": d["eligibility"][0][3], "caseManagerSourcedId": SID["snow"],
                "eligibility": [{"order": i, "stateCode": sc, "federalCode": fc, "label": lb, "determinedDate": dd}
                                for i, (sc, fc, lb, dd) in enumerate(d["eligibility"], 1)],
                "evaluationSummary": d["evaluation"],
                "strengths": d["strengths"], "needs": d["needs"], "parentConcerns": d["parent_concerns"], "disabilityImpact": d["impact"],
                "presentLevels": [{"area": a, "text": t} for a, t in d["present_levels"]],
                "specialFactors": d["special_factors"],
                "transition": {"required": False, "note": "Not required: the student is in elementary school (Georgia requires a transition plan by 9th grade entry or age 16)."},
                "goals": goals, "services": services, "accommodations": accs,
                "assessmentParticipation": d["assessment"], "alternateAssessmentReason": d["alt_reason"] or None,
                "environmentCode": d["environment"], "percentTimeGenEd": d["pct_gen_ed"], "lreExplanation": d["lre"],
                "esy": d["esy"], "goalProgress": progress,
                "behaviorPlan": ({**d["bip"], "dataCollection": {"method": d["bip"]["dataCollection"]["method"],
                                                                 "responsibleSourcedId": SID[d["bip"]["dataCollection"]["responsible"]]}}
                                 if d["bip"] else None),
                "team": team,
                "notices": [{"n": 1, "date": notice.isoformat(), "method": "invitation", "bySourcedId": SID["snow"]},
                            {"n": 2, "date": (m - dt.timedelta(days=3)).isoformat(), "method": "reminder_notice", "bySourcedId": SID["potts"]}],
                "documentsProvided": ["parental_rights", "iep", "eligibility_report"],
                "priorWrittenNoticeDate": d["meeting"],
                "consents": [{"type": "initial_evaluation", "givenBySourcedId": team[0].get("contactSourcedId"), "dateGiven": d["consent_initial_eval"]},
                             {"type": "initial_services", "givenBySourcedId": team[0].get("contactSourcedId"), "dateGiven": d["consent_initial_services"]},
                             {"type": "reevaluation", "givenBySourcedId": team[0].get("contactSourcedId"),
                              "dateGiven": (D(d["last_eval"]) - dt.timedelta(days=40)).isoformat()}],
            }
            # previous year's version, superseded (shows versioning)
            prev_m = add_years(m, -1).isoformat()
            prev = iep_doc(s, n_iep, 1, "superseded", prev_m, "annual", {
                "lastEvaluationDate": extra["lastEvaluationDate"], "reevaluationDue": extra["reevaluationDue"],
                "eligibilityDate": extra["eligibilityDate"], "caseManagerSourcedId": SID["snow"], "eligibility": extra["eligibility"],
                "presentLevels": [{"area": "summary", "text": "Prior-year IEP (superseded). See the current version for present levels."}],
                "goals": [{"sourcedId": f"iep-{n_iep:04d}-v1-g{gi}", "number": gi, "area": g["area"], "category": g["category"],
                           "baseline": "Prior-year baseline (see archived IEP)", "statement": g["statement"],
                           "criteriaForMastery": g["criteria"], "measurementMethod": g["method"],
                           "targetDate": (m - dt.timedelta(days=1)).isoformat(), "status": "carried_forward", "objectives": []}
                          for gi, g in enumerate(d["goals"][:2], 1)],
                "services": [dict(services[0], sourcedId=f"iep-{n_iep:04d}-v1-s1", startDate=next_school_day(D(prev_m)).isoformat(),
                                  endDate=(m - dt.timedelta(days=1)).isoformat())],
                "accommodations": [dict(accs[0], sourcedId=f"iep-{n_iep:04d}-v1-a1")],
                "assessmentParticipation": d["assessment"], "environmentCode": d["environment"], "percentTimeGenEd": d["pct_gen_ed"],
                "esy": {"needed": False}, "goalProgress": [], "behaviorPlan": None, "team": [], "notices": [], "consents": []})
            ieps.append(prev)
            ieps.append(iep_doc(s, n_iep, 2, "active", d["meeting"], d["meeting_type"], extra))
            caseload(s["sid"], "snow", "case_manager")
            if d["bip"]:
                caseload(s["sid"], "mary", "bcba"); caseload(s["sid"], "flynn", "rbt")
            iep_id = f"iep-{n_iep:04d}-v2"
        else:
            g = int(s["grade"]) if s["grade"] != "KG" else 0
            weights = dict(ROOM_WEIGHTS[room])
            if g > 2:
                weights.pop("SDD", None)
            cat = rng.choices(list(weights), list(weights.values()))[0]
            sc, fc, lb = CAT[cat]
            bank = GOAL_BANK["ID" if cat in ("ID", "IDM") else cat]
            picked = rng.sample(bank, min(len(bank), rng.choice([2, 3])))
            meeting = (D("2025-10-15") + dt.timedelta(days=rng.randint(0, 330)))
            while meeting.weekday() >= 5:
                meeting += dt.timedelta(days=1)
            end = add_years(meeting, 1) - dt.timedelta(days=1)
            goals = []
            for gi, (area, catg, base, stmt, crit, meth) in enumerate(picked, 1):
                goals.append({"sourcedId": f"iep-{n_iep:04d}-v1-g{gi}", "number": gi, "area": area, "category": catg, "baseline": base,
                              "statement": stmt.format(fn=s["first"]), "criteriaForMastery": crit, "measurementMethod": meth,
                              "targetDate": end.isoformat(), "status": "active", "objectives": []})
            services = [{"sourcedId": f"iep-{n_iep:04d}-v1-s1", "type": "specialized_instruction", "delivery": "direct", "setting": "separate_school",
                         "minutes": 1500, "frequencyCount": 1, "frequencyPeriod": "week", "groupSize": "small_group", "providerRole": "sped_teacher",
                         "providerSourcedId": SID[room_of[room][1]], "location": f"Room {room[2:]}",
                         "startDate": next_school_day(meeting).isoformat(), "endDate": end.isoformat(), "esy": False}]
            caseload(s["sid"], room_of[room][1], "teacher")
            for si, (typ, mins, cnt, role) in enumerate([x for x in CAT_SERVICES[cat] if rng.random() < 0.85], 2):
                prov = provider_for(role, room)
                services.append({"sourcedId": f"iep-{n_iep:04d}-v1-s{si}", "type": typ, "delivery": "direct",
                                 "setting": "separate_school" if role == "rbt" else "outside_gen_ed", "minutes": mins, "frequencyCount": cnt,
                                 "frequencyPeriod": "week", "groupSize": "small_group" if role in ("slp", "counselor") else "individual",
                                 "providerRole": role, "providerSourcedId": SID[prov], "location": "Therapy room" if role in ("slp", "ot") else f"Room {room[2:]}",
                                 "startDate": next_school_day(meeting).isoformat(), "endDate": end.isoformat(), "esy": False})
                caseload(s["sid"], prov, role)
                if role == "rbt":
                    caseload(s["sid"], "mary", "bcba")
            accs = [{"sourcedId": f"iep-{n_iep:04d}-v1-a{ai}", "category": "modification" if "modif" in t.lower() else
                     ("classroom_testing" if "test" in t.lower() else "instructional"), "description": t, "subjects": ["all"]}
                    for ai, t in enumerate(CAT_ACC[cat], 1)]
            gaa = cat == "ID"
            assess = ("not_applicable_grade" if g < 3 else ("alternate_gaa" if gaa else "regular_with_accommodations"))
            ieps.append(iep_doc(s, n_iep, 1, "active", meeting.isoformat(), rng.choice(["annual", "annual", "annual", "initial"]), {
                "lastEvaluationDate": (meeting - dt.timedelta(days=rng.randint(60, 900))).isoformat(),
                "reevaluationDue": add_years(meeting, rng.choice([1, 2, 3])).isoformat(),
                "eligibilityDate": (meeting - dt.timedelta(days=rng.randint(30, 1000))).isoformat(), "caseManagerSourcedId": SID[cm],
                "eligibility": [{"order": 1, "stateCode": sc, "federalCode": fc, "label": lb,
                                 "determinedDate": (meeting - dt.timedelta(days=rng.randint(30, 1000))).isoformat()}],
                "presentLevels": [{"area": goals[0]["area"], "text": f"Summary: {goals[0]['baseline']} (fictional)"}],
                "goals": goals, "services": services, "accommodations": accs,
                "assessmentParticipation": assess,
                "alternateAssessmentReason": "Student has a significant cognitive disability and is instructed on alternate achievement standards (fictional)." if assess == "alternate_gaa" else None,
                "environmentCode": "SS", "percentTimeGenEd": 0, "esy": {"needed": False}, "goalProgress": [], "behaviorPlan": None,
                "team": [], "notices": [], "consents": []}))
            caseload(s["sid"], cm, "case_manager")
            iep_id = f"iep-{n_iep:04d}-v1"
        programs.append(dict(studentSourcedId=s["sid"], program="sped_iep", beginDate=s["entry"], status="active", planSourcedId=iep_id))
    for s in students:
        if s["plan"] == "504":
            pid = f"p504-{len(plans504) + 1:04d}"
            md = D("2025-11-01") + dt.timedelta(days=rng.randint(0, 280))
            plans504.append(dict(sourcedId=pid, studentSourcedId=s["sid"], meetingDate=md.isoformat(), startDate=next_school_day(md).isoformat(),
                                 reviewDate=(add_years(md, 1) - dt.timedelta(days=1)).isoformat(), qualifyingCondition="On file (not recorded in sample data)",
                                 accommodations="Preferential seating; extended time (1.5x); breaks as needed", coordinatorSourcedId=SID["elsa"]))
            programs.append(dict(studentSourcedId=s["sid"], program="section_504", beginDate=s["entry"], status="active", planSourcedId=pid))
            caseload(s["sid"], "elsa", "case_manager")
        if s["ell"]:
            programs.append(dict(studentSourcedId=s["sid"], program="ell", beginDate=s["entry"], status="active", level=str(rng.randint(1, 4))))
        if s["plan"] == "none":
            caseload(s["sid"], room_of[s["room"]][1], "teacher")

    # -------------------------------------------------- add-on CSVs
    write_csv(OUT / "ext_students.csv", ["sourcedId", "schoolStudentNumber", "stateStudentId", "gradeLevel", "enrollmentStatus", "entryDate",
                                         "entryCode", "exitDate", "exitCode", "homeroomClassSourcedId", "primaryLanguage", "homeLanguage",
                                         "interpreterNeeded", "residentDistrict", "fundingSource", "transportMode", "healthAlertOnFile", "label"],
              [dict(sourcedId=s["sid"], schoolStudentNumber=s["number"], stateStudentId=s["gtid"], gradeLevel=s["grade"], enrollmentStatus="active",
                    entryDate=s["entry"], entryCode="E01" if s["entry"] != "2026-08-03" else "E02", homeroomClassSourcedId=f"cls-{s['room'].lower()}-hr",
                    primaryLanguage=s["lang"][1], homeLanguage=s["lang"][1], interpreterNeeded="false", residentDistrict=DISTRICT,
                    fundingSource=s["funding"], transportMode=s["transport"], healthAlertOnFile=str(s["health_alert"]).lower(), label=LABEL)
               for s in students])
    write_csv(OUT / "ext_contacts.csv", ["sourcedId", "addressLine1", "city", "state", "postalCode", "preferredLanguage", "interpreterNeeded", "label"],
              [dict(sourcedId=c["sid"], addressLine1=c["street"], city=c["city"], state=c["state"], postalCode=c["zip"],
                    preferredLanguage=c["language"], interpreterNeeded=c["interpreter"], label=LABEL) for c in contacts])
    write_csv(OUT / "ext_student_contacts.csv", ["studentSourcedId", "contactSourcedId", "relationship", "legalGuardian", "educationalRightsHolder",
                                                 "livesWith", "receivesMail", "emergencyPriority", "pickup", "receivesWeeklyDraft"], links)
    write_csv(OUT / "ext_staff.csv", ["sourcedId", "staffNumber", "title", "staffType", "appRole", "supervisorSourcedId", "deviceLabels",
                                      "employmentStart", "employmentEnd", "label"],
              [dict(sourcedId=SID[k], staffNumber=f"E{20000 + i * 37}", title=title, staffType=stype, appRole=APP_ROLE[stype],
                    supervisorSourcedId=SID["mary"] if stype == "para_rbt" else (SID["cinderella"] if k != "cinderella" else ""),
                    deviceLabels=f"{first} {last}" if stype == "teacher" else "", employmentStart=rng.choice(["2022-07-25", "2023-07-24", "2024-07-29", "2025-07-28"]),
                    label=LABEL) for i, (k, first, last, stype, _o, title, _r) in enumerate(STAFF)])
    cred = []
    for k, first, last, stype, *_x in STAFF:
        t = {"teacher": ("gapsc_certificate", "Special Education General Curriculum (P-12)"), "dhh_teacher": ("gapsc_certificate", "Deaf Education (P-12)"),
             "tvi": ("gapsc_certificate", "Vision Impairment (P-12); O&M (COMS)"), "case_manager": ("gapsc_certificate", "Special Education Adapted Curriculum (P-12)"),
             "principal": ("gapsc_certificate", "Educational Leadership (Tier II)"), "counselor": ("gapsc_certificate", "School Counseling (P-12)"),
             "para_rbt": ("bacb_rbt", "Registered Behavior Technician"), "bcba": ("bacb_bcba", "Board Certified Behavior Analyst"),
             "slp": ("asha_ccc", "Certificate of Clinical Competence, SLP"), "ot": ("state_license", "Occupational Therapist (GA)"),
             "pt": ("state_license", "Physical Therapist (GA)")}.get(stype)
        if t:
            # Ops (Oct 10): a credential number is staff personal data the student DPA doesn't cover, so the
            # column is optional and always left empty here; type, area and dates are enough for the demo.
            # The rng call stays so every other generated value is unchanged.
            {"bacb_rbt": f"9-26-{rng.randint(10000, 99999)}", "bacb_bcba": f"9-19-{rng.randint(10000, 99999)}"}.get(t[0], f"FICT-{rng.randint(100000, 999999)}")
            num = ""
            iss = D(rng.choice(["2021-06-01", "2022-06-01", "2023-06-01", "2024-06-01", "2025-06-01"]))
            cred.append(dict(staffSourcedId=SID[k], type=t[0], number=num, field=t[1], issued=iss.isoformat(),
                             expires=add_years(iss, 1 if t[0] == "bacb_rbt" else 5).isoformat() if t[0] != "bacb_rbt" else "2027-06-30"))
    write_csv(OUT / "ext_staff_credentials.csv", ["staffSourcedId", "type", "number", "field", "issued", "expires"], cred)
    write_csv(OUT / "ext_caseloads.csv", ["studentSourcedId", "staffSourcedId", "role", "beginDate", "endDate"], caseloads)
    write_csv(OUT / "ext_student_programs.csv", ["studentSourcedId", "program", "beginDate", "endDate", "status", "level", "planSourcedId"], programs)
    write_csv(OUT / "ext_periods.csv", ["sourcedId", "name", "startTime", "endTime", "days", "rotation"],
              [dict(sourcedId=p, name=n, startTime=a, endTime=b, days="Mon,Tue,Wed,Thu,Fri") for p, n, a, b in PERIODS])

    # -------------------------------------------------- daily attendance (David, Oct 10: in scope)
    # School days from the first day (Aug 3) to Oct 9, 2026, minus Labor Day. One row per student per
    # school day they were enrolled. Its own seeded generator, so every other file is unchanged.
    arng = random.Random(20261011)
    days = [d for d in (D(ATT_START) + dt.timedelta(days=i) for i in range((D(ATT_END) - D(ATT_START)).days + 1))
            if d.weekday() < 5 and d.isoformat() not in ATT_HOLIDAYS]
    write_csv(OUT / "ext_attendance_codes.csv", ["code", "description", "countsAsAbsent", "countsAsTardy"],
              [dict(code=c, description=t, countsAsAbsent=str(a).lower(), countsAsTardy=str(tt).lower())
               for c, t, a, tt in ATT_CODES])
    att = []
    for s in students:
        profile = arng.random()
        p_abs = 0.02 if profile < 0.6 else (0.06 if profile < 0.92 else 0.14)   # a few chronically absent
        p_tardy = arng.choice([0.01, 0.03, 0.06])
        for d in days:
            if d.isoformat() < s["entry"]:
                continue
            r = arng.random()
            code = ("AE" if arng.random() < 0.65 else "AU") if r < p_abs else ("T" if r < p_abs + p_tardy else "P")
            att.append(dict(sourcedId=f"att-{s['sid'][-4:]}-{d.strftime('%Y%m%d')}", studentSourcedId=s["sid"],
                            schoolSourcedId=SCHOOL, date=d.isoformat(), code=code, label=LABEL))
    write_csv(OUT / "ext_attendance.csv", ["sourcedId", "studentSourcedId", "schoolSourcedId", "date", "code", "label"], att)

    # -------------------------------------------------- IEP JSON (one file per version) + CSV flattening
    (OUT / "iep" / "json").mkdir(parents=True)
    for doc in ieps:
        (OUT / "iep" / "json" / f"{doc['sourcedId']}.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    flatten(ieps, plans504)

    # -------------------------------------------------- manifest
    files = sorted(p.relative_to(OUT).as_posix() for p in OUT.rglob("*") if p.is_file())
    man = [("manifest.version", "1.0"), ("oneroster.version", "1.2"), ("file.academicSessions", "bulk"), ("file.categories", "absent"),
           ("file.classes", "bulk"), ("file.classResources", "absent"), ("file.courses", "bulk"), ("file.courseResources", "absent"),
           ("file.demographics", "bulk"), ("file.enrollments", "bulk"), ("file.lineItemLearningObjectiveIds", "absent"),
           ("file.lineItems", "absent"), ("file.lineItemScoreScales", "absent"), ("file.orgs", "bulk"), ("file.resources", "absent"),
           ("file.resultLearningObjectiveIds", "absent"), ("file.results", "absent"), ("file.resultScoreScales", "absent"),
           ("file.roles", "bulk"), ("file.scoreScales", "absent"), ("file.userProfiles", "absent"), ("file.userResources", "absent"),
           ("file.users", "bulk"), ("source.systemName", "Goalvera fictional sample generator"), ("source.systemCode", "fict-demo"),
           ("ext.synthetic", "true"), ("ext.label", LABEL), ("ext.schemaVersion", "1"),
           ("ext.files", ",".join(f for f in files if f.startswith("ext_") or f.startswith("iep/csv/")) + ",iep/json/*.json")]
    with (OUT / "manifest.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["propertyName", "value"])
        w.writerows(man)
    return students, contacts, ieps


def flatten(ieps, plans504):
    base = OUT / "iep" / "csv"
    yn = lambda v: "" if v is None else (str(v).lower() if isinstance(v, bool) else v)
    rows = {k: [] for k in ["ieps", "eligibility", "present_levels", "goals", "objectives", "services", "accommodations", "progress",
                            "team", "notices", "consents", "bip", "bip_targets"]}
    for d in ieps:
        i = d["sourcedId"]
        sf = d.get("specialFactors") or {}
        esy = d.get("esy") or {}
        rows["ieps"].append(dict(sourcedId=i, studentSourcedId=d["studentSourcedId"], version=d["version"], status=d["status"],
                                 supersedes=d["supersedes"] or "", source=d["source"], meetingType=d["meetingType"], meetingDate=d["meetingDate"],
                                 startDate=d["startDate"], endDate=d["endDate"], annualReviewDue=d["annualReviewDue"],
                                 reevaluationDue=d.get("reevaluationDue", ""), lastEvaluationDate=d.get("lastEvaluationDate", ""),
                                 eligibilityDate=d.get("eligibilityDate", ""), responsibleLea=d["responsibleLea"],
                                 caseManagerSourcedId=d.get("caseManagerSourcedId", ""), progressReportFrequency=d["progressReportFrequency"],
                                 assessmentParticipation=d.get("assessmentParticipation", ""), alternateAssessmentReason=d.get("alternateAssessmentReason") or "",
                                 environmentCode=d.get("environmentCode", ""), percentTimeGenEd=d.get("percentTimeGenEd", ""),
                                 lreExplanation=d.get("lreExplanation", ""), esyNeeded=yn(esy.get("needed")), esyNotes=esy.get("notes", ""),
                                 sfBehaviorImpedes=yn(sf.get("behaviorImpedes")), sfBipDeveloped=yn(sf.get("bipDeveloped")), sfLep=yn(sf.get("lep")),
                                 sfVisual=yn(sf.get("visual")), sfCommunication=yn(sf.get("communication")), sfDeafHoh=yn(sf.get("deafHoh")),
                                 sfAssistiveTech=yn(sf.get("assistiveTech")), sfAltFormat=",".join(sf.get("altFormat") or []), sfNotes=sf.get("notes", ""),
                                 evaluationSummary=d.get("evaluationSummary", ""), strengths=d.get("strengths", ""), needs=d.get("needs", ""),
                                 parentConcerns=d.get("parentConcerns", ""), disabilityImpact=d.get("disabilityImpact", ""),
                                 priorWrittenNoticeDate=d.get("priorWrittenNoticeDate", ""), label=LABEL))
        for e in d.get("eligibility", []):
            rows["eligibility"].append(dict(iepSourcedId=i, **e))
        for n, p in enumerate(d.get("presentLevels", []), 1):
            rows["present_levels"].append(dict(iepSourcedId=i, order=n, area=p["area"], text=p["text"]))
        for g in d.get("goals", []):
            rows["goals"].append(dict(sourcedId=g["sourcedId"], iepSourcedId=i, **{k: g[k] for k in
                                      ("number", "area", "category", "baseline", "statement", "criteriaForMastery", "measurementMethod", "targetDate", "status")}))
            for o in g.get("objectives", []):
                rows["objectives"].append(dict(sourcedId=o["sourcedId"], goalSourcedId=g["sourcedId"], number=o["number"], statement=o["statement"], criteria=o["criteria"]))
        for s in d.get("services", []):
            rows["services"].append(dict(iepSourcedId=i, **{k: yn(v) for k, v in s.items()}))
        for a in d.get("accommodations", []):
            rows["accommodations"].append(dict(iepSourcedId=i, sourcedId=a["sourcedId"], category=a["category"], description=a["description"],
                                               subjects=",".join(a.get("subjects") or []), stateTestSetting=a.get("stateTestSetting", ""),
                                               stateTestTiming=a.get("stateTestTiming", ""), stateTestPresentation=a.get("stateTestPresentation", ""),
                                               stateTestResponse=a.get("stateTestResponse", ""), standardOrConditional=a.get("standardOrConditional", "")))
        for p in d.get("goalProgress", []):
            rows["progress"].append(dict(iepSourcedId=i, **p))
        for t in d.get("team", []):
            rows["team"].append(dict(iepSourcedId=i, role=t["role"], staffSourcedId=t.get("staffSourcedId", ""), contactSourcedId=t.get("contactSourcedId", ""),
                                     attended=yn(t.get("attended")), signature=t.get("signature", ""), note=t.get("note", "")))
        for nt in d.get("notices", []):
            rows["notices"].append(dict(iepSourcedId=i, **nt))
        for c in d.get("consents", []):
            rows["consents"].append(dict(iepSourcedId=i, studentSourcedId=d["studentSourcedId"], type=c["type"], givenBySourcedId=c["givenBySourcedId"] or "",
                                         dateGiven=c["dateGiven"], dateRevoked=c.get("dateRevoked", "")))
        b = d.get("behaviorPlan")
        if b:
            rows["bip"].append(dict(iepSourcedId=i, studentSourcedId=d["studentSourcedId"], fbaDate=b["fbaDate"], bipDate=b["bipDate"], reviewDate=b["reviewDate"],
                                    settingEvents=b["settingEvents"], antecedents=b["antecedents"], consequences=b["consequences"],
                                    hypothesizedFunction=b["hypothesizedFunction"], functionStatement=b["functionStatement"],
                                    replacementBehaviors="; ".join(b["replacementBehaviors"]), antecedentStrategies=b["antecedentStrategies"],
                                    teachingStrategies=b["teachingStrategies"], reinforcement=b["reinforcement"], responseStrategies=b["responseStrategies"],
                                    crisisPlan=b["crisisPlan"], dataCollectionMethod=b["dataCollection"]["method"],
                                    dataCollectionResponsibleSourcedId=b["dataCollection"]["responsibleSourcedId"]))
            for t in b["targetBehaviors"]:
                rows["bip_targets"].append(dict(iepSourcedId=i, **t))
    names = {"ieps": "ieps.csv", "eligibility": "iep_eligibility.csv", "present_levels": "iep_present_levels.csv", "goals": "iep_goals.csv",
             "objectives": "iep_objectives.csv", "services": "iep_services.csv", "accommodations": "iep_accommodations.csv",
             "progress": "iep_goal_progress.csv", "team": "iep_team.csv", "notices": "iep_notices.csv", "consents": "iep_consents.csv",
             "bip": "behavior_plans.csv", "bip_targets": "behavior_targets.csv"}
    for k, fn in names.items():
        r = rows[k]
        h = []
        for row in r:
            for col in row:
                if col not in h:
                    h.append(col)
        write_csv(base / fn, h, r)
    write_csv(base / "section504_plans.csv", list(plans504[0].keys()) + ["label"], [dict(p, label=LABEL) for p in plans504])


if __name__ == "__main__":
    st, co, ie = build()
    print(f"students={len(st)} contacts={len(co)} iep_versions={len(ie)} -> {OUT}")
