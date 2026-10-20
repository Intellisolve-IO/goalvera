#!/usr/bin/env python3
"""End-to-end smoke test for classroom materials (), fictional data only.

Runs the deployed code path (review_api.materials) on this computer against a throwaway local store
seeded with one fictional Room 104 session (Snow White, Doc Dwarf) whose Teacher Note asks for
flashcards. Live parts: Vertex Gemini (suggestions, brief, 3 SVG drafts) and the Gmail sender
(Secret Manager OAuth client + refresh token, From notifications@staff.example). Sends ONE email,
to Snow White's own staff address on the demo roster. Nothing is written to the bucket.

  PROJECT_ID=example-project python3 app/tools/e2e_materials_smoke.py [--no-email] [--out DIR]
Needs gcloud application-default credentials that may call Vertex and read the two mailer secrets.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app/backend-pipeline"))
os.environ.setdefault("PROJECT_ID", "example-project")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", os.environ["PROJECT_ID"])
os.environ.setdefault("VERTEX_AI", "1")
os.environ["MATERIALS_AI"] = "live"
os.environ["MATERIALS_EMAIL_DEFAULT"] = "on"

from review_api import mailer  # noqa: E402
from review_api import material_ai as mai  # noqa: E402
from review_api.journal import MemoryJournal, Recorder  # noqa: E402
from review_api.materials import Materials  # noqa: E402
from review_api.notes import Notes  # noqa: E402
from review_api.store import LocalStore  # noqa: E402

RID = "rec_smoke01_20261010_140000_Room104"
NOTE = ("Teacher note. Make a set of flashcards for Doc about farm animals for tomorrow, "
        "simple words for his grade level, with pictures.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--out", default=".")
    ap.add_argument("--count", type=int, default=8)
    a = ap.parse_args()
    roster = json.loads((ROOT / "app/samples/demo_roster.json").read_text())
    room = roster["class_codes"]["Room 104"]
    tid = room["teacher_id"]
    teacher = roster["teachers"][tid]
    sid = next(s for s, v in roster["students"].items() if v.get("first_name") == "Doc" and "Room 104" in (v.get("class_codes") or []))
    store = LocalStore(Path(tempfile.mkdtemp(prefix="mat-smoke-")))
    store.write_json("config/demo_roster.json", roster)
    markers = [{"type": "Teacher Notes", "offset_seconds": 30.0}]
    session = {"recording_id": RID, "class_code": "Room 104", "teacher_id": tid, "teacher_name": teacher["teacher_name"],
               "student_id": sid, "student_first_name": "Doc", "student_last_initial": "D.",
               "duration_seconds": 90, "markers": markers, "start_time": "2026-10-10T14:00:00-04:00"}
    store.write_json(f"drafts/{RID}.json", {"recording_id": RID, "session": session, "created_at": "2026-10-10T18:05:00Z",
                                            "analysis": {"goal_evaluations": [], "incident_analysis": [],
                                                         "general_session_summary": "Doc named farm animals."}})
    store.write_json(f"transcripts/{RID}.json", {"recording_id": RID, "teacher_name": teacher["teacher_name"], "markers": markers,
                                                 "speaker_turns": [{"speaker": "Speaker 1", "start_seconds": 31,
                                                                    "end_seconds": 45, "text": NOTE}]})
    journal = MemoryJournal()
    sender = None if a.no_email else mailer.GmailSender()
    mats = Materials(Notes(store, recorder=Recorder(journal)), sender=sender)
    snow = SimpleNamespace(uid="smoke_snow", email=teacher["emails"][0], role="teacher", is_admin=False,
                           teacher_ids=frozenset({tid}), class_keys=frozenset())
    if a.no_email:
        os.environ["MATERIALS_EMAIL_DEFAULT"] = "off"
    v = mats.view(RID, snow)
    note = v["notes"][0]
    print("suggestions:", json.dumps(note["suggestions"]))
    pick = next((s for s in note["suggestions"] if s["type"] == "flashcards"), None)
    body = {"note_index": 0, "type": "flashcards", "count": (pick or {}).get("count") or a.count,
            "topic": (pick or {}).get("topic") or "farm animals", "student_ids": [sid], "due": "2026-10-11"}
    out = mats.make(RID, snow, body)
    m = out["material"]
    pdf, name = mats.pdf(RID, m["material_id"], snow)[:2]
    path = Path(a.out) / name
    path.write_bytes(pdf)
    print("material:", m["material_id"], m["type"], m.get("count"), "drafts", len(m["drafts"]), "| pdf", path, len(pdf), "bytes")
    for d in m["drafts"]:
        (Path(a.out) / f"{m['material_id']}_draft{d['draft']}.png").write_bytes(__import__("base64").b64decode(d["preview_png"]))
    print("email:", json.dumps(out["email"]))
    print("journal:", [r["action"] for r in journal.rows()])
    sent = [r for r in journal.rows() if r["action"] == "materials_emailed"]
    return 0 if (a.no_email or sent) else 1


if __name__ == "__main__":
    raise SystemExit(main())
