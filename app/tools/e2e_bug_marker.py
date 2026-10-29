#!/usr/bin/env python3
"""End-to-end check of Bug markers (tasks#94), offline, in the real review UI on the
synthetic sample session. Saves screenshots.

  1. seed the sample session into a fresh local store (one incident at 0:49); put a full
     last name, school id and date of birth in the roster, as a real record might hold them
  2. start review_api --local-dir (no GitHub token: reports stay pending) and Vite
  3. in Chrome: the marker type dropdown offers "Bug"; picking it shows the note box with
     "Don't include student names; this goes to our support team." under it
  4. a note that names the student, the teacher, an email and a school id is sent: the
     teacher sees "Thanks, sent to our team." and a small "Bug reported · 1:23" item;
     the stored report is pending and scrubbed; the issue it will become holds no
     forbidden value (title and body)
  5. a note quoting the transcript is refused (QUOTES_TRANSCRIPT); nothing is saved
  6. incidents, their numbering, the timeline's markers, the incident print view and the
     session PDF are exactly as before
  The review screen has no separate markers section (David, Oct 8): markers are in the
  timeline, and "+ Add marker" (same form: type incl. Bug, time, note) sits just below it.

  python app/tools/e2e_bug_marker.py --shots /workspace/ap-bug-marker-2026-10-07/local-e2e
Needs: playwright (+ Chrome or `playwright install chromium`), poppler-utils (pdftotext), node deps.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "tools"))
sys.path.insert(0, str(APP / "backend-pipeline"))
from seed_local_session import RID, seed  # noqa: E402
from review_api.bug_reports import build_issue  # noqa: E402
from review_api.store import LocalStore  # noqa: E402

SCHOOL_ID, LAST_NAME, DOB = "ATL-0042317", "Anderson", "2015-04-03"
NOTE = (f"When I clicked Approve for Marcus {LAST_NAME} (ID {SCHOOL_ID}, born {DOB}) the button spun forever. "
        "Ms. Jenkins saw it too, write to sarah.jenkins@school.example if you need more.")
QUOTE = "It said Good morning, everyone. Marcus, let's start with our math warm-up. Double digit addition on page 12."
NEVER = ("Marcus", LAST_NAME, SCHOOL_ID, DOB, "Jenkins", "Sarah", "@", "Good morning")
INCIDENTS = [{"timestamp_seconds": 49, "antecedent": "The teacher announced the switch to math.",
              "behavior": "Covered his ears.", "consequence_intervention": "Offered headphones."}]


def wait_http(url: str, timeout: float = 40) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    raise SystemExit(f"timed out waiting for {url}")


def prepare(store_dir: Path) -> None:
    seed(str(store_dir))
    s = LocalStore(store_dir)
    d = s.read_json(f"drafts/{RID}.json")
    d["analysis"]["incident_analysis"] = INCIDENTS
    s.write_json(f"drafts/{RID}.json", d)
    r = s.read_json("config/demo_roster.json") or json.loads((APP / "samples/profiles/demo-school/demo_roster.json").read_text())
    r["students"] = r.get("students") or {}
    r["students"]["stu_101"] = {**(r["students"].get("stu_101") or {}), "first_name": "Marcus",
                                "last_name": LAST_NAME, "dob": DOB, "school_id": SCHOOL_ID}
    s.write_json("config/demo_roster.json", r)


def api(path: str, body=None, reviewer="usr_demo_sarah"):
    req = urllib.request.Request("http://127.0.0.1:8787" + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Reviewer-Id": reviewer, "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return r.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="/workspace/ap-shots/bug-marker")
    a = ap.parse_args()
    shots = Path(a.shots)
    shots.mkdir(parents=True, exist_ok=True)
    store = Path(tempfile.mkdtemp(prefix="ap-e2e-bug-"))
    prepare(store)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}   # no token: pending
    procs = [
        subprocess.Popen([sys.executable, str(APP / "backend-pipeline/review_api/server.py"),
                          "--local-dir", str(store), "--port", "8787"], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True),
        subprocess.Popen(["npx", "vite", "--port", "5173", "--strictPort"], cwd=APP / "frontend-web",
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True),
    ]
    checks: list[tuple[str, bool]] = []

    def check(name, ok, info=""):
        checks.append((name, bool(ok)))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({info})" if info else ""))

    try:
        wait_http("http://127.0.0.1:8787/api/health")
        wait_http("http://localhost:5173/")
        before_print = json.loads(api(f"/api/sessions/{RID}/incident_print", {"number": 1}))
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome", headless=True)
            except Exception:  # noqa: BLE001
                browser = p.chromium.launch(headless=True)
            page = browser.new_context(viewport={"width": 1360, "height": 1000}).new_page()
            page.goto("http://localhost:5173/")
            page.evaluate("localStorage.clear()")
            page.reload()
            page.wait_for_selector(".pipeline-table")
            page.click(".pipeline-table button:has-text('Review')")
            page.wait_for_selector(".speaker-timeline .tl-marker")
            labels_before = page.locator("[data-testid=incident-label]").all_inner_texts()
            line_before = page.locator(".speaker-timeline .tl-marker").all_inner_texts()
            check("no separate markers section; markers are in the timeline",
                  page.locator("[data-testid=markers-section], .markers-line").count() == 0
                  and "Incident" in line_before, str(line_before))
            tl = page.locator(".speaker-timeline").bounding_box()
            tog = page.locator("[data-testid=add-marker-toggle]").bounding_box()
            turns = page.locator(".turns").bounding_box()
            check("'+ Add marker' sits just below the timeline, above the transcript turns",
                  tl["y"] + tl["height"] <= tog["y"] < turns["y"], f"{tl} {tog} {turns}")
            check("the form is folded until clicked", not page.is_visible("[data-testid=marker-type]"))
            page.click("[data-testid=add-marker-toggle] > summary")
            page.wait_for_selector("[data-testid=marker-type]", state="visible")
            opts = page.locator("[data-testid=marker-type] option").evaluate_all("os => os.map(o => o.value)")
            check("marker type dropdown offers Bug", opts == ["", "Bug"], str(opts))
            sec = page.locator("[data-testid=bug-markers]")
            sec.scroll_into_view_if_needed()
            sec.screenshot(path=str(shots / "01_marker_type_dropdown.png"))
            page.select_option("[data-testid=marker-type]", "Bug")
            hint = page.inner_text("[data-testid=bug-hint]")
            check("names hint under the note box",
                  hint == "Don\u2019t include student names; this goes to our support team.", hint)
            page.fill("[data-testid=marker-time]", "1:23")
            page.select_option("[data-testid=bug-area]", "approve")
            page.fill("[data-testid=bug-note]", NOTE)
            sec.screenshot(path=str(shots / "02_bug_form_filled.png"))
            page.click("[data-testid=add-marker-submit]")
            page.wait_for_selector("[data-testid=bug-result]")
            msg = page.inner_text("[data-testid=bug-result]")
            check("teacher sees 'Thanks, sent to our team.'", msg == "Thanks, sent to our team.", msg)
            item = page.inner_text("[data-testid=bug-item]")
            check("small 'Bug reported' item shown", item.startswith("Bug reported \u00b7 1:23"), item)
            sec.screenshot(path=str(shots / "03_bug_reported_pending.png"))

            # transcript text is refused
            page.select_option("[data-testid=marker-type]", "Bug")
            page.fill("[data-testid=bug-note]", QUOTE)
            page.click("[data-testid=add-marker-submit]")
            page.wait_for_selector("[data-testid=bug-result].warn-text")
            check("a note quoting the transcript is refused", "transcript" in page.inner_text("[data-testid=bug-result]"))
            sec.screenshot(path=str(shots / "04_transcript_quote_refused.png"))

            labels_after = page.locator("[data-testid=incident-label]").all_inner_texts()
            check("incident list and numbering unchanged", len(labels_after) == 1
                  and labels_after[0].split(":")[0] == labels_before[0].split(":")[0] == "Incident 1", str(labels_after))
            after = page.locator(".speaker-timeline .tl-marker").all_inner_texts()
            check("timeline markers unchanged, no Bug in them", after == line_before
                  and not any("bug" in x.lower() for x in after), str(after))
            page.screenshot(path=str(shots / "05_session_after_bug_report.png"), full_page=True)
            browser.close()

        names = sorted(str(x.relative_to(store)) for x in (store / "bug_reports").rglob("*.json"))
        reports = [n for n in names if n.count(".") == 1]
        check("one report stored, pending (no .filed.json)", len(reports) == 1 and not any(".filed" in n for n in names),
              str(names))
        rep = json.loads((store / reports[0]).read_text())
        title, body = build_issue(rep)
        (shots / "issue_preview.md").write_text(f"# {title}\n\n{body}\n")
        blob = rep["note"] + title + body        # (the stored record also keeps the reporter's internal id)
        for never in NEVER:
            check(f"stored note + issue title/body have no {never!r}", never.lower() not in blob.lower())
        check("student names replaced with [student]", "[student]" in rep["note"], rep["note"])
        check("issue holds time, screen, recording id", "| Time in recording | 1:23 |" in body
              and "\u2192 Approve |" in body and RID in body)
        after_print = json.loads(api(f"/api/sessions/{RID}/incident_print", {"number": 1}))
        check("incident print view unchanged", {k: after_print[k] for k in ("label", "of", "fields", "file_title")}
              == {k: before_print[k] for k in ("label", "of", "fields", "file_title")})
        api(f"/api/sessions/{RID}/approve", {"analysis": json.loads((store / f"drafts/{RID}.json").read_text())["analysis"]})
        pdf = shots / "session_notes.pdf"
        pdf.write_bytes(api(f"/api/sessions/{RID}/pdf"))
        text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
        check("session PDF has the incident and no Bug marker", "Incident 1" in text and "0:49 into recording" in text
              and "Bug" not in text)
        jr = [e for e in json.loads(api(f"/api/sessions/{RID}/audit"))["events"] if "bug" in json.dumps(e).lower()]
        check("bug reports are not in the session's audit trail", jr == [])
    finally:
        for pr in procs:
            try:
                os.killpg(pr.pid, signal.SIGTERM) if pr is procs[1] else pr.terminate()
            except ProcessLookupError:
                pass
        for pr in procs:
            try:
                pr.wait(timeout=10)
            except Exception:  # noqa: BLE001
                pass
    ok = sum(c[1] for c in checks)
    print(f"{ok}/{len(checks)} checks passed; screenshots in {shots}")
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
