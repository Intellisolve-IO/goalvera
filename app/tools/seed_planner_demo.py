#!/usr/bin/env python3
"""Fictional demo data for the weekly lesson planner (), in a LOCAL folder
with the bucket's layout. No cloud calls; nothing here is real student data.

Starts from the synthetic RM12 session (seed_local_session.py) and copies it to
other school days over four weeks, approving most of them with different goal
statuses, so each week has something to count. Then saves a sample weekly plan
and one teacher note through the planner itself (so the 8-word quote check and
the "the student" storage rule apply to the seed too).

  PLANNER_TODAY=2026-10-07 python app/tools/seed_planner_demo.py --local-dir /tmp/ap-planner
  PLANNER_TODAY=2026-10-07 python app/backend-pipeline/review_api/server.py --auth off --local-dir /tmp/ap-planner

The class list (config/demo_roster.json) is copied from app/samples/demo_roster.json;
its IEP goals are the fictional ones the planner checks against until.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "backend-pipeline"))
sys.path.insert(0, str(APP / "tools"))

from review_api.notes import Notes  # noqa: E402
from review_api.planner import Planner, today, week_of  # noqa: E402
from review_api.store import LocalStore  # noqa: E402
from seed_local_session import FIXTURE_ROSTER, RID as BASE_RID, seed  # noqa: E402

TEACHER = "usr_demo_sarah"
D, S, N = "Demonstrated", "Struggled", "Not Observed"

# (weeks before this week, weekday 0=Mon, time, g1 status, g2 status or None = left pending)
SESSIONS = [
    (3, 0, "091500", S, S), (3, 2, "131000", S, N),
    (2, 0, "094000", S, D), (2, 2, "101500", D, S), (2, 4, "140500", N, D),
    (1, 2, "093000", D, D), (1, 3, "113000", S, D),          # + the base session (Mon 28 Sep, pending)
    (0, 0, "092000", D, D), (0, 1, "100500", None, None),
]


def _clone(store, day: dt.date, hhmmss: str) -> str:
    stem = f"{day:%Y%m%d}_{hhmmss}_RM12"
    rid = f"rec_laptop_demo_{stem}"
    stamp = f"{day.isoformat()}T{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:]}"
    for kind in ("transcripts", "drafts"):
        doc = copy.deepcopy(store.read_json(f"{kind}/{BASE_RID}.json"))
        doc["recording_id"] = rid
        target = doc.get("session") if kind == "drafts" else doc
        target["timestamp_start"] = stamp
        target["source_object"] = f"rec/laptop-demo/{stem}.wav"
        if kind == "drafts":
            doc["transcript_object"] = f"transcripts/{rid}.json"
            doc["created_at"] = f"{stamp}+00:00"
        store.write_json(f"{kind}/{rid}.json", doc)
    return rid


def _analysis(base: dict, g1: str, g2: str) -> dict:
    a = copy.deepcopy(base)
    for ev in a["goal_evaluations"]:
        ev["status"] = {"g1": g1, "g2": g2}[ev["goal_id"]]
        if ev["status"] == N:
            ev["evidence_quote"] = ""
    if g1 != D:
        a["incident_analysis"] = a.get("incident_analysis") or []
    else:
        a["incident_analysis"] = []
    return a


def seed_planner(local_dir: str) -> dict:
    seed(local_dir)
    store = LocalStore(local_dir)
    roster = json.loads(FIXTURE_ROSTER.read_text())   # the RM12 fixtures' roster (seed_local_session)
    store.write_json("config/demo_roster.json", roster)
    notes = Notes(store, roster_ttl=0)
    base = store.read_json(f"drafts/{BASE_RID}.json")["analysis"]
    this_week = week_of(today())
    made = []
    for back, wd, hhmmss, g1, g2 in SESSIONS:
        day = this_week - dt.timedelta(days=7 * back) + dt.timedelta(days=wd)
        if day > today():
            continue
        rid = _clone(store, day, hhmmss)
        if g1 is not None:
            notes.approve(rid, TEACHER, _analysis(base, g1, g2))
        made.append(rid)

    planner = Planner(notes)
    last = this_week - dt.timedelta(days=7)
    planner.save_plan(TEACHER, {
        "class_code": "RM12", "week": last.isoformat(), "approve": True,
        "goals": ["Use a calming strategy without a prompt", "Regrouping in double-digit addition"],
        "objectives": ["Ask for headphones or the break space within 1 minute of overload",
                       "Score 80% or better on two regrouping probes"],
        "plan": "Mon-Tue: model the break request with the visual card. Wed: regrouping with base-ten blocks. "
                "Thu: independent probe. Fri: review and reward chart."})
    planner.save_plan(TEACHER, {
        "class_code": "RM12", "week": this_week.isoformat(),
        "goals": ["Return to task within 5 minutes after a break",
                  "Three probes in a row at 80% on regrouping"],
        "objectives": ["Timer card on the desk after each break",
                       "Short daily probe (7 problems), tracked on the chart",
                       "Practice the fire drill routine with headphones ready"],
        "plan": "Mon: fire drill practice with the calm table set up. Tue-Wed: regrouping centers with "
                "Marcus leading the warm-up. Thu: probe 2. Fri: probe 3 and family update draft."})
    planner.save_plan(TEACHER, {
        "class_code": "RM12", "week": (this_week + dt.timedelta(days=7)).isoformat(),
        "goals": ["Start subtraction with regrouping"],
        "objectives": ["Introduce borrowing with base-ten blocks"],
        "plan": "Draft - to finish on Friday after this week's probes."})
    planner.save_note(TEACHER, {
        "student_id": "stu_101", "week": last.isoformat(),
        "note": "Marcus asked for headphones on his own twice. Keep the break card on the desk."})
    return {"sessions": made, "this_week": this_week.isoformat()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local-dir", required=True)
    a = ap.parse_args()
    out = seed_planner(a.local_dir)
    print(f"seeded {len(out['sessions']) + 1} RM12 sessions and sample plans into {a.local_dir} "
          f"(this week {out['this_week']}, PLANNER_TODAY={os.environ.get('PLANNER_TODAY', 'unset')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
