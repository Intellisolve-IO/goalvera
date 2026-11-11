#!/usr/bin/env python3
"""End-to-end check of incident numbering + Download PDF on an incident,
offline, in the real review UI on the synthetic sample session. Saves screenshots.

  1. seed the sample session into a fresh local store; give it three incidents out of
     time order, one of them long, one naming the student in full with a school id and
     a date of birth (and put those in the roster, as a real record might hold them)
  2. start review_api --local-dir and the Vite dev server
  3. in Chrome: incidents read "Incident 1 · 0:03", "Incident 2 · 0:49", ... earliest
     first; Download PDF on an incident opens the print view, sets the document title
     (= the PDF's file name) and calls the browser's print dialog. Before Approve it
     prints as a DRAFT (DRAFT_ title, "Draft, not yet approved" line, diagonal DRAFT
     watermark on every page); after Approve the same incident prints without any of it
  4. print the view to PDF with Chrome's own print engine (print stylesheet): the
     footer "Confidential, contains student information" is on every page; no school
     id, full last name or date of birth anywhere, nor in the title
  5. the audit trail has one incident_pdf_downloaded per click with the incident number
     and the draft flag

  python app/tools/e2e_incident_pdf.py --shots /workspace/ap-shots/incident-pdf
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
from review_api.store import LocalStore  # noqa: E402

SCHOOL_ID, LAST_NAME, DOB = "ATL-0042317", "Anderson", "2015-04-03"
FOOTER = "Confidential, contains student information"
LONG = " ".join(["He walked to the window, came back to the table, and asked twice whether the bell had rung."] * 28)
INCIDENTS = [
    {"timestamp_seconds": 49, "antecedent": "The teacher announced the switch from reading to math.",
     "behavior": LONG, "consequence_intervention": "Teacher gave a 2-minute warning and a visual timer."},
    {"timestamp_seconds": 3, "antecedent": f"Marcus {LAST_NAME} (ID {SCHOOL_ID}, DOB {DOB}) was asked to wait his turn.",
     "behavior": "Covered his ears and put his head on the desk.",
     "consequence_intervention": "Offered headphones; he returned to the task within a minute."},
    {"timestamp_seconds": 95, "antecedent": "Group work started.", "behavior": "Left the table for the calm corner.",
     "consequence_intervention": "Used the break card; came back after 3 minutes."},
]
# "Incident N: <AI name>" (offline stand-in locally: the first clause of the behavior)
# and the Eastern clock time (recording start 4:45:00 PM + offset) with the offset
WANT = ["Incident 1: Covered his ears and put his head on the desk", "Incident 2: Walked to the window",
        "Incident 3: Left the table for the calm corner"]
TIMES = ["4:45 PM \u00b7 0:03 into recording", "4:45 PM \u00b7 0:49 into recording", "4:46 PM \u00b7 1:35 into recording"]


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
    r.setdefault("students", {})
    r["students"] = r["students"] or {}
    r["students"]["stu_101"] = {**(r["students"].get("stu_101") or {}), "first_name": "Marcus",
                                "last_name": LAST_NAME, "dob": DOB, "school_id": SCHOOL_ID}
    s.write_json("config/demo_roster.json", r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="/workspace/ap-shots/incident-pdf")
    a = ap.parse_args()
    shots = Path(a.shots)
    shots.mkdir(parents=True, exist_ok=True)
    store = Path(tempfile.mkdtemp(prefix="ap-e2e-inc-"))
    prepare(store)
    procs = [
        subprocess.Popen([sys.executable, str(APP / "backend-pipeline/review_api/server.py"),
                          "--local-dir", str(store), "--port", "8787"],
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
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome", headless=True)
            except Exception:  # noqa: BLE001
                browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1360, "height": 1000})
            # count print-dialog calls instead of opening one (headless has no dialog)
            ctx.add_init_script("window.__prints = 0; window.print = () => { window.__prints += 1; };")
            page = ctx.new_page()
            page.goto("http://localhost:5173/")
            page.evaluate("localStorage.clear()")
            page.reload()
            page.wait_for_selector(".pipeline-table")
            page.click(".pipeline-table button:has-text('Review')")
            page.wait_for_selector("[data-testid=incident-label]")
            page.wait_for_function("document.querySelectorAll('[data-testid=incident-label]')[2]"
                                   " && document.querySelectorAll('[data-testid=incident-label]')[2].innerText.includes(':')",
                                   timeout=15000)                  # names arrive on first view
            labels = page.locator("[data-testid=incident-label]").all_inner_texts()
            check("incidents numbered earliest first, with their names", labels == WANT, str(labels))
            times = [t.strip() for t in page.locator("[data-testid=incident-time]").all_inner_texts()]
            check("time (clock + offset) read-only at the top of each incident", times == TIMES, str(times))
            check("no clock-time / seconds edit box on any incident",
                  page.locator("[data-testid^=incident-clock-], [data-testid^=incident-seconds-]").count() == 0)
            tabs = [x.split("\n")[0].strip() for x in page.locator("[data-testid^=incident-tab-]").all_inner_texts()]
            check("incidents are tabs under 'Incident analysis': Incident 1, 2, 3", tabs == ["Incident 1", "Incident 2", "Incident 3"]
                  and page.locator("section:has(h3:text-is('Incident analysis')) [role=tablist]").count() == 1, str(tabs))
            shown = []
            for n in (1, 2, 3):
                page.click(f"[data-testid=incident-tab-{n}]")
                shown.append([k for k in (1, 2, 3) if page.is_visible(f"[data-testid=incident-{k}]")])
                check(f"tab {n}: its name below the label and on hover",
                      page.get_attribute(f"[data-testid=incident-tab-{n}]", "title") == WANT[n - 1].split(": ", 1)[-1])
            check("clicking a tab shows that incident only", shown == [[1], [2], [3]], str(shown))
            page.click("[data-testid=incident-tab-1]")
            check("teacher can edit the name before approval (80 max)",
                  page.locator("[data-testid=incident-name-1]").get_attribute("maxlength") == "80")
            check("no draft parent email on the session", "parent email" not in page.inner_text(".modal").lower())
            check("no bare 'Timestamp' label", "Timestamp" not in page.inner_text(".modal"))
            check("Download PDF enabled before Approve (prints as a draft)",
                  all(page.locator(f"[data-testid=incident-pdf-{n}]").is_enabled() for n in (1, 2, 3)))
            sec = page.locator("section:has(h3:text-is('Incident analysis'))")
            sec.scroll_into_view_if_needed()
            sec.screenshot(path=str(shots / "01_incidents_numbered_draft.png"))

            results = {}

            def print_one(n: int, draft: bool, tag: str) -> None:
                page.evaluate("window.__prints = 0")
                page.click(f"[data-testid=incident-tab-{n}]")
                page.click(f"[data-testid=incident-pdf-{n}]")
                page.wait_for_selector("[data-testid=incident-print]")
                page.wait_for_function("window.__prints > 0", timeout=5000)
                title = page.title()
                view = page.inner_text("[data-testid=incident-print]")
                base = {1: "Incident-1_4-45PM_RM12_2026-09-28", 2: "Incident-2_4-45PM_RM12_2026-09-28"}[n]
                want_title = ("DRAFT_" if draft else "") + base
                k = f"{tag} incident {n}"
                check(f"{k}: print dialog opened", page.evaluate("window.__prints") >= 1)
                check(f"{k}: print view heading", page.inner_text("[data-testid=incident-print-title]") == WANT[n - 1])
                check(f"{k}: clock time at the top", page.inner_text("[data-testid=incident-print-time]") == TIMES[n - 1])
                check(f"{k}: the AI name is not in the file name", WANT[n - 1].split(": ", 1)[1] not in title)
                check(f"{k}: document title / file name", title == want_title, title)
                has_wm = page.locator("[data-testid=incident-print-watermark]").count() == 1
                has_line = "Draft, not yet approved by the teacher" in view
                check(f"{k}: watermark + draft line {'shown' if draft else 'absent'}",
                      has_wm == draft and has_line == draft)
                for never in (SCHOOL_ID, LAST_NAME, DOB):
                    check(f"{k}: no {never!r} on screen or in the title", never not in view and never not in title)
                check(f"{k}: student shown as first name + last initial", "Marcus A." in view)
                page.screenshot(path=str(shots / f"{tag}_print_view_incident_{n}.png"))
                page.emulate_media(media="print")
                page.screenshot(path=str(shots / f"{tag}_print_view_incident_{n}_paper.png"), full_page=True)
                pdf = shots / f"{want_title}.pdf"
                page.pdf(path=str(pdf), format="Letter", prefer_css_page_size=True)
                page.emulate_media(media="screen")
                pages = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True,
                                       text=True).stdout.split("\f")
                pages = [x for x in pages if x.strip()]
                check(f"{k}: footer on every printed page ({len(pages)} pages)", pages and all(FOOTER in x for x in pages))
                raw = [x for x in subprocess.run(["pdftotext", "-raw", str(pdf), "-"], capture_output=True,
                                                 text=True).stdout.split("\f") if x.strip()]   # -raw keeps rotated text
                if draft:
                    check(f"{k}: DRAFT watermark on every printed page", len(raw) == len(pages)
                          and all("DRAFT" in x for x in raw))
                else:
                    check(f"{k}: no DRAFT anywhere in the approved PDF", not any("DRAFT" in x or "Draft" in x for x in raw))
                alltext = "\n".join(pages)
                check(f"{k}: printed PDF has only this incident and no app chrome",
                      WANT[n - 1] in alltext and "Save Draft" not in alltext and "Teacher weekly matrix" not in alltext)
                for never in (SCHOOL_ID, LAST_NAME, DOB):
                    check(f"{k}: printed PDF has no {never!r}", never not in alltext)
                subprocess.run(["pdftoppm", "-png", "-r", "80", str(pdf), str(shots / f"pdf_{tag}_incident_{n}_page")],
                               check=True)
                results[f"{tag}{n}"] = {"title": title, "pages": len(pages)}
                page.click("[data-testid=incident-print-close]")
                page.wait_for_selector("[data-testid=incident-print]", state="detached")
                check(f"{k}: title restored after Close", page.title() != want_title, page.title())

            print_one(1, True, "draft")
            print_one(2, True, "draft")

            page.click("button:text-is('Approve')")
            page.wait_for_selector("[data-testid=download-pdf]", timeout=15000)
            labels = page.locator("[data-testid=incident-label]").all_inner_texts()
            check("same numbers after Approve", labels == WANT, str(labels))
            check("Download PDF enabled after Approve",
                  all(page.locator(f"[data-testid=incident-pdf-{n}]").is_enabled() for n in (1, 2, 3)))
            sec.scroll_into_view_if_needed()
            sec.screenshot(path=str(shots / "02_incidents_numbered_approved.png"))
            page.screenshot(path=str(shots / "03_session_incident_list.png"))
            print_one(1, False, "approved")
            print_one(2, False, "approved")
            browser.close()

        audit = json.loads(urllib.request.urlopen(f"http://127.0.0.1:8787/api/sessions/{RID}/audit").read())["events"]
        inc = [e for e in audit if e["event"] == "incident_pdf_downloaded"]
        got = [(e.get("incident_number"), e.get("draft")) for e in inc]
        check("audit: one incident_pdf_downloaded per click, with the number and draft flag",
              got == [(1, True), (2, True), (1, False), (2, False)], str(got))
        check("audit: ids only (no email, no name)",
              all("actor_email" not in e and "Marcus" not in json.dumps(e) for e in inc))
        check("nothing stored server-side as a PDF", not list(store.rglob("*.pdf")))
        print(json.dumps(results))
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
