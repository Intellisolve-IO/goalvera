#!/usr/bin/env python3
"""Seed files for the review site's Domains and Classes tabs.

    # Domains: from the sample Educational Methodology document (Word). Needs python-docx.
    python3 app/tools/build_school_setup.py --docx /path/to/educational-methodology.docx
    # Classes: from the FICTIONAL sample school (app/samples/sis-demo, Sample Elementary School).
    python3 app/tools/build_school_setup.py --classes
    python3 app/tools/build_school_setup.py --check    # exit 1 if classes.json is out of date or
                                                       # domains.json doesn't hold together

Writes app/samples/school-setup/domains.json and classes.json. The review service reads them as the
starting point; admins' changes are stored as new records in the bucket (review_api/school_setup.py).

Domains are copied from the document's table exactly as written ("Subject / Domain", "Core Educational
Methodology", "Key Curricular Programs", "Recommended Book / Guide"). The methodology cell is a bold
name followed by a description; the two are kept apart (methodology / methodology_detail). Nothing is
added; an empty cell stays empty and is reported. The document's "Operational Delivery Framework" list is
kept as delivery_formats (shown under the table, read-only).

Classes are the 32 OneRoster classes in sis-demo/classes.csv (homeroom, reading, math and social skills in
Rooms 101-108), with their teacher and aide (enrollments.csv), their students, the sample bell
schedule (ext_periods.csv) and a domain from the document (DOMAIN_FOR_SUBJECT). Every class carries the
"Fictional sample data" label. Domain / methodology text is not student data.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SIS = APP / "samples" / "sis-demo"
OUT = APP / "samples" / "school-setup"
LABEL = "Fictional sample data"
HEADERS = ["Subject / Domain", "Core Educational Methodology", "Key Curricular Programs", "Recommended Book / Guide"]
# OneRoster subject -> Subject / Domain in the sample methodology document. Homeroom (Morning Meeting) and Social Skills
# are the self-regulation / SEL blocks of the day.
DOMAIN_FOR_SUBJECT = {"Homeroom": "Social-Emotional & Self-Regulation", "Reading": "Reading & Phonics",
                      "Math": "Mathematics", "Social Skills": "Social-Emotional & Self-Regulation"}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def slug(name: str) -> str:
    return "dom-" + re.sub(r"[^a-z0-9]+", "-", name.lower().replace("&", " ")).strip("-")


def _split_bold(cell) -> tuple[str, str]:
    """A cell's bold lead (the methodology's name) and the rest (its description)."""
    lead, rest, in_lead = [], [], True
    for p in cell.paragraphs:
        for r in p.runs:
            if in_lead and r.bold and not rest:
                lead.append(r.text)
            else:
                in_lead = False
                rest.append(r.text)
    return " ".join("".join(lead).split()), " ".join("".join(rest).split())


def parse_docx(path: Path) -> dict:
    import docx  # python-docx
    d = docx.Document(str(path))
    table = None
    for t in d.tables:
        head = [" ".join(c.text.split()) for c in t.rows[0].cells]
        if head == HEADERS:
            table = t
            break
    if table is None:
        raise SystemExit(f"no table with the columns {HEADERS} in {path}")
    domains, missing = [], []
    for row in table.rows[1:]:
        cells = row.cells
        name = " ".join(cells[0].text.split())
        if not name:
            continue
        meth, detail = _split_bold(cells[1])
        if not meth:                                  # no bold lead: keep the whole cell as the methodology
            meth, detail = " ".join(cells[1].text.split()), ""
        item = {"id": slug(name), "name": name, "methodology": meth, "methodology_detail": detail,
                "programs": " ".join(cells[2].text.split()), "book": " ".join(cells[3].text.split())}
        for k in ("methodology", "programs", "book"):
            if not item[k]:
                missing.append(f"{name}: {k}")
        domains.append(item)
    formats, in_framework = [], False
    for p in d.paragraphs:
        text = " ".join(p.text.split())
        if p.style.name.lower().startswith("heading"):
            in_framework = "delivery framework" in text.lower()
            continue
        if in_framework and ": " in text:          # "1:1 Personalized ...: Core literacy ..." (": " only)
            name, desc = text.split(": ", 1)
            if desc.strip():                          # the lead-in sentence ends with ":" and is skipped
                formats.append({"name": name.strip(), "description": desc.strip()})
    return {"_meta": {"source": "the sample Educational Methodology document (Oct 10 2026), the table "
                                "\"Methodology by Subject & Recommended Reference Books\"; built by "
                                "app/tools/build_school_setup.py. Not student data.",
                      "columns": HEADERS, "missing_fields": missing},
            "domains": domains, "delivery_formats": formats}


def _csv(name: str) -> list[dict]:
    with (SIS / name).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _sid(u: str) -> str:            # u-stu-0001 -> stu_0001, u-stf-0001 -> stf_0001 (roster ids)
    return u[2:].replace("-", "_")


def _minutes(a: str, b: str) -> int:
    (h1, m1), (h2, m2) = (map(int, a.split(":")), map(int, b.split(":")))
    return (h2 * 60 + m2) - (h1 * 60 + m1)


def build_classes(domains: dict) -> dict:
    names = {d["name"]: d["id"] for d in domains["domains"]}
    periods = {p["sourcedId"]: p for p in _csv("ext_periods.csv")}
    enr = _csv("enrollments.csv")
    out = []
    for c in _csv("classes.csv"):
        per = periods[c["periods"]]
        room = c["location"]
        subject = c["subjects"]
        title = c["title"].replace(f"RM{room.split()[-1]} ", "")
        staff = [e for e in enr if e["classSourcedId"] == c["sourcedId"] and e["role"] in ("teacher", "aide")]
        staff.sort(key=lambda e: (e["role"] != "teacher", e["userSourcedId"]))
        students = sorted(_sid(e["userSourcedId"]) for e in enr
                          if e["classSourcedId"] == c["sourcedId"] and e["role"] == "student")
        dom = DOMAIN_FOR_SUBJECT.get(subject)
        out.append({
            "id": c["sourcedId"], "name": f"{room} {title}", "classroom": room,
            "teacher_ids": [_sid(e["userSourcedId"]) for e in staff],
            "domain_id": names.get(dom) if dom else None,
            "student_ids": students,
            "schedule": [{"days": [d for d in DAYS if d in per["days"].split(",")], "start": per["startTime"],
                          "minutes": _minutes(per["startTime"], per["endTime"]), "period": per["name"]}],
            "grades": c["grades"], "subject": subject, "sis_id": c["sourcedId"], "label": LABEL})
    return {"_meta": {"synthetic": True, "label": LABEL, "school_profile": "sample-school",
                      "source": "app/samples/sis-demo classes.csv, enrollments.csv, ext_periods.csv (fictional); "
                                "domains mapped by app/tools/build_school_setup.py DOMAIN_FOR_SUBJECT. Do not edit by hand."},
            "classes": out}


def _dump(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docx", type=Path, help="the sample Educational Methodology document -> domains.json")
    ap.add_argument("--classes", action="store_true", help="rebuild classes.json from sis-demo")
    ap.add_argument("--check", action="store_true", help="exit 1 if classes.json is out of date")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.docx:
        doms = parse_docx(a.docx)
        (OUT / "domains.json").write_text(_dump(doms), encoding="utf-8")
        print(f"domains.json: {len(doms['domains'])} domains: " + "; ".join(d["name"] for d in doms["domains"]))
        for m in doms["_meta"]["missing_fields"]:
            print("  empty in the document:", m)
    doms = json.loads((OUT / "domains.json").read_text(encoding="utf-8"))
    want = _dump(build_classes(doms))
    if a.check:
        have = (OUT / "classes.json").read_text(encoding="utf-8") if (OUT / "classes.json").is_file() else ""
        ids = [d["id"] for d in doms["domains"]]
        if have != want or len(ids) != len(set(ids)):
            print("school-setup seed is out of date: run app/tools/build_school_setup.py --classes", file=sys.stderr)
            return 1
        print("school-setup seed up to date")
        return 0
    if a.classes or a.docx:
        (OUT / "classes.json").write_text(want, encoding="utf-8")
        print(f"classes.json: {len(json.loads(want)['classes'])} classes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
