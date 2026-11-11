#!/usr/bin/env python3
"""Browser click-through of the hosted review screen, fully offline:
the built UI + review service (same code as Cloud Run) against the Firebase
Auth emulator (stands in for Identity Platform) and a local bucket copy with the
synthetic RM12 session.

  sign-in page (no self sign-up) -> admin-created account signs in -> verify
  email (emulator link) -> "waiting for access" -> added to the allowlist
  (infra/review_allowlist.py) -> review list -> Approve -> Download PDF
  (approved by the signed-in email; audit has it)
  then the demo RBT (read-only): sees every classroom, no parent email in any
  API response, no edit/approve/export/move control, direct writes refused (403),
  no Activity Log / Settings tabs, no Download report button and 403 on the admin endpoints
  then a demo admin: tabs Recordings, Students, Lesson planner, Teachers, Activity Log,
  Settings; opens a student's IEP on the Students tab -> the Activity Log
  tab shows the Journal (newest first, ET) with that "viewed IEP" entry; Settings has
  Reset demo state; the activity report CSV download; the journal runs in
  memory (JOURNAL_MODE=memory)

  python app/tools/e2e_hosted_review.py --shots /workspace/ap-shots/hosted
Needs: firebase-tools (FIREBASE_BIN, default /workspace/tools/fbtools/node_modules/.bin/firebase),
playwright + Chrome, a built UI (npx vite build in app/frontend-web).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
sys.path.insert(0, str(APP / "tools"))
sys.path.insert(0, str(APP / "backend-pipeline"))
from seed_local_session import RID, seed  # noqa: E402

PROJECT = "demo-local-review"          # "demo-" = emulator never talks to a real project
EMU = "127.0.0.1:9099"
PORT = 8788
BASE = f"http://127.0.0.1:{PORT}"
EMAIL, PASSWORD = "e2e-teacher@example.org", "correct-horse-battery"
RBT_EMAIL, RBT_PASSWORD = "e2e-demo-rbt@example.org", "staple-battery-horse"
ADMIN_EMAIL, ADMIN_PASSWORD = "e2e-admin@example.org", "battery-horse-staple-correct"
ADMIN_BUTTONS = "[data-testid=tab-activity], [data-testid=tab-settings], [data-testid=open-report]"
REAL_ROWS = ".pipeline-table tbody tr[data-testid=recording-row]:not(.sample-row)"   # Recordings, minus samples


def group(cls):
    """Recordings are grouped by classroom: the group under that classroom's heading."""
    return f".classroom-group:has(h3:text-is('{cls}'))"


TAB_ORDER = ["Recordings", "Students", "Classes", "Lesson planner", "Domains", "Teachers", "Activity Log", "Settings"]
READING_RID = "rec_laptop_demo_20260929_090000_Reading_3A"
results = []


def check(name, ok):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")


def wait_http(url, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except urllib.error.HTTPError:
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    raise SystemExit(f"timeout waiting for {url}")


def emu_post(path, body):
    req = urllib.request.Request(f"http://{EMU}/identitytoolkit.googleapis.com/v1/{path}?key=demo-key",
                                 data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def admin_creates_account(email, password):
    """Self sign-up is off: an admin creates accounts (here: the emulator's REST API)."""
    return emu_post("accounts:signUp", {"email": email, "password": password, "returnSecureToken": True})


def verify_email(ctx, email, id_token):
    emu_post("accounts:sendOobCode", {"requestType": "VERIFY_EMAIL", "idToken": id_token})
    codes = json.load(urllib.request.urlopen(f"http://{EMU}/emulator/v1/projects/{PROJECT}/oobCodes"))
    link = [c for c in codes["oobCodes"] if c["email"] == email and c["requestType"] == "VERIFY_EMAIL"][-1]["oobLink"]
    vp = ctx.new_page()
    vp.goto(link)
    vp.wait_for_load_state()
    vp.close()


def api(path, tok, method="GET", body=None):
    h = {"Authorization": f"Bearer {tok}"}
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, method=method, headers=h)) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="/workspace/ap-shots/hosted")
    a = ap.parse_args()
    shots = Path(a.shots)
    shots.mkdir(parents=True, exist_ok=True)
    dist = APP / "frontend-web/dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("build the UI first: (cd app/frontend-web && npx vite build)")

    work = Path(tempfile.mkdtemp(prefix="e2e-hosted-"))
    store_dir = work / "bucket"
    seed(str(store_dir))
    roster = json.loads((APP / "samples/profiles/demo-school/demo_roster.json").read_text())
    roster["teachers"]["usr_demo_sarah"]["emails"] = [EMAIL]      # this user plays the session's teacher
    roster["rbts"]["rbt_demo_rm12"]["emails"] = [RBT_EMAIL]
    # a second classroom, so "every classroom" means something
    for kind in ("drafts", "transcripts"):
        doc = json.loads((store_dir / f"{kind}/{RID}.json").read_text())
        doc["recording_id"] = READING_RID
        sess = doc.get("session", doc)
        sess.update(class_code="Reading 3A", student_id=None, student_first_name="Student", student_last_initial="")
        if kind == "drafts":
            doc["analysis"]["draft_parent_email"] = "Hello family, Reading 3A parent email draft."
        (store_dir / f"{kind}/{READING_RID}.json").write_text(json.dumps(doc))
    (store_dir / "config").mkdir(exist_ok=True)
    (store_dir / "config/demo_roster.json").write_text(json.dumps(roster))
    shutil.copy(ROOT / "infra/review_allowlist.json", store_dir / "config/review_allowlist.json")
    (work / "firebase.json").write_text(json.dumps({"emulators": {"auth": {"port": 9099, "host": "127.0.0.1"},
                                                                 "ui": {"enabled": False}}}))
    fb = os.environ.get("FIREBASE_BIN", "/workspace/tools/fbtools/node_modules/.bin/firebase")
    env = {**os.environ, "PROJECT_ID": PROJECT, "FIREBASE_AUTH_EMULATOR_HOST": EMU,
           "REVIEW_ALLOWED_ORIGINS": BASE, "REVIEW_ALLOWLIST_TTL": "1",
           "JOURNAL_MODE": "memory"}
    procs = [
        subprocess.Popen([fb, "emulators:start", "--only", "auth", "--project", PROJECT], cwd=work,
                         stdout=open(work / "emu.log", "w"), stderr=subprocess.STDOUT, start_new_session=True),
        subprocess.Popen([sys.executable, str(APP / "backend-pipeline/review_api/server.py"), "--auth", "emulator",
                          "--local-dir", str(store_dir), "--static", str(dist), "--port", str(PORT)],
                         env=env, stdout=open(work / "server.log", "w"), stderr=subprocess.STDOUT,
                         start_new_session=True),
    ]
    try:
        wait_http(f"http://{EMU}/")
        wait_http(f"{BASE}/api/auth-config")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome", headless=True)
            except Exception:  # noqa: BLE001
                browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
            page = ctx.new_page()
            page.goto(BASE + "/")
            page.wait_for_selector("[data-testid=signin-form]")
            notice = "Demo only. Provided as-is with no warranty. Don't upload real data."
            check("sign-in page shows the demo notice", page.inner_text("[data-testid=demo-notice]") == notice)
            page.screenshot(path=str(shots / "01_sign_in.png"))

            check("sign-in page has no 'Create an account' link (self sign-up is off)",
                  page.locator("text=Create an account").count() == 0
                  and page.locator("[data-testid=signup-form]").count() == 0)

            acct = admin_creates_account(EMAIL, PASSWORD)
            page.fill("input[type=email]", EMAIL)
            page.fill("input[type=password]", PASSWORD)
            page.screenshot(path=str(shots / "02_sign_in_filled.png"))
            page.click("button:has-text('Sign in')")
            page.wait_for_selector("text=Verify your email")
            check("after sign-in: verify-email page, demo notice still shown",
                  page.locator("[data-testid=demo-notice]").count() == 1)
            page.click("button:has-text('I’ve verified my email')")
            page.wait_for_selector("text=Not verified yet")
            check("unverified user stays on the verify page", page.locator(".pipeline-table").count() == 0)
            page.screenshot(path=str(shots / "03_verify_email.png"))

            # the "email": the emulator keeps the verification link instead of sending it
            verify_email(ctx, EMAIL, acct["idToken"])

            page.click("button:has-text('I’ve verified my email')")
            page.wait_for_selector("[data-testid=waiting-title]")
            check("verified but not allowlisted: 'waiting for access', no sessions shown",
                  page.locator(".pipeline-table").count() == 0)
            status = urllib.request.Request(f"{BASE}/api/sessions")
            try:
                urllib.request.urlopen(status)
                check("API without a token refused", False)
            except urllib.error.HTTPError as e:
                check("API without a token refused (401)", e.code == 401)
            page.screenshot(path=str(shots / "04_waiting_for_access.png"))

            subprocess.run([sys.executable, str(ROOT / "infra/review_allowlist.py"), "add", EMAIL,
                            "--local-dir", str(store_dir)], check=True, capture_output=True)
            time.sleep(1.2)
            page.click("button:has-text('Check again')")
            page.wait_for_selector(".pipeline-table")
            check("teacher's Recordings open on their default classroom (RM12); All shows both of Sarah's",
                  page.locator(f"{REAL_ROWS} button:has-text('Review')").count() == 1
                  and page.locator(".classroom-group h3:text-is('RM12')").count() == 1)
            page.click("button.filter-reset")
            check("teacher: All shows both of Sarah's classrooms",
                  page.locator(f"{REAL_ROWS} button:has-text('Review')").count() == 2)
            check("top bar shows the signed-in name (opens My profile), the email in its tooltip",
                  "Sarah" in page.inner_text("[data-testid=open-profile]")
                  and EMAIL in (page.get_attribute("[data-testid=open-profile]", "title") or ""))
            check("teacher: no Activity Log / Settings tabs, no Download report button", page.locator(ADMIN_BUTTONS).count() == 0)
            page.screenshot(path=str(shots / "05_review_list.png"), full_page=True)

            page.click(f"{group('RM12')} button:has-text('Review')")
            page.wait_for_selector(".turns li")
            # Header: recorded date/time (school time) · classroom; student · length · status below.
            head = page.inner_text(".modal-head")
            check("review header: title is 'Sep 28, 2026 · 4:45 PM · RM12'",
                  page.inner_text("[data-testid=session-title]").strip() == "Sep 28, 2026 · 4:45 PM · RM12")
            check("review header: 'Marcus A. · 2 min · status: pending_review' under the title",
                  page.inner_text("[data-testid=session-subtitle]").strip().lower()
                  == "marcus a. · 2 min · status: pending_review")
            check("review header: no 'Session detail · HITL review' line, no recording id",
                  "hitl review" not in head.lower() and "session detail" not in head.lower() and RID not in head)
            check("Download PDF hidden before Approve", page.locator("[data-testid=download-pdf]").count() == 0)
            check("review screen: no speech-engine / 'voices separated' line under the transcript",
                  "voices separated" not in page.inner_text(".modal").lower())
            page.evaluate("document.querySelector('.modal').scrollTop = 0")
            page.screenshot(path=str(shots / "06_review_before_approve.png"))
            # Oct 8: no separate markers section, markers in the timeline, "+ Add marker" just
            # below it; incidents as tabs with the time read-only at the top; Approve / Save Draft
            page.wait_for_selector(".speaker-timeline .tl-marker")
            check("review: no separate markers section; markers shown in the timeline",
                  page.locator("[data-testid=markers-section], .markers-line, .modal h3:text-is('Markers')").count() == 0
                  and "Incident" in page.locator(".speaker-timeline .tl-marker").all_inner_texts())
            tl, tog, turns = (page.locator(s).bounding_box() for s in
                              (".speaker-timeline", "[data-testid=add-marker-toggle]", ".turns"))
            check("review: '+ Add marker' just below the timeline", tl["y"] + tl["height"] <= tog["y"] < turns["y"])
            page.click("[data-testid=add-marker-toggle] > summary")
            opts = page.locator("[data-testid=marker-type] option").evaluate_all("os => os.map(o => o.value)")
            check("review: Add marker keeps the same form (type incl. Bug, time)",
                  "Bug" in opts and page.is_visible("[data-testid=marker-time]"))
            page.locator(".speaker-timeline").scroll_into_view_if_needed()
            page.screenshot(path=str(shots / "06b_review_timeline_add_marker.png"))
            n_inc = page.locator("[data-testid=incident-label]").count()   # this sample has none (tabs: e2e_incident_pdf)
            check(f"review: 'Incident analysis' heading, one tab per incident ({n_inc}), no clock / seconds edit box",
                  page.locator(".modal h3:text-is('Incident analysis')").count() == 1
                  and page.locator("[data-testid^=incident-tab-]").count() == n_inc
                  and page.locator("[data-testid^=incident-clock-], [data-testid^=incident-seconds-]").count() == 0)
            check("review: buttons read 'Save Draft' and 'Approve' (no 'no send')",
                  page.locator(".modal button:text-is('Save Draft')").count() == 1
                  and page.locator(".modal button:text-is('Approve')").count() == 1
                  and "no send" not in page.inner_text(".modal").lower())
            page.locator("section:has(h3:text-is('Incident analysis'))").scroll_into_view_if_needed()
            page.screenshot(path=str(shots / "06c_review_incident_tabs.png"))
            page.click("button:text-is('Approve')")
            page.wait_for_selector("[data-testid=download-pdf]", timeout=15000)
            head = page.inner_text(".modal-head")
            check("review screen: approved by the signed-in email", f"approved by {EMAIL}" in head)
            page.evaluate("document.querySelector('.modal').scrollTop = 0")
            page.screenshot(path=str(shots / "07_after_approve.png"))
            with page.expect_download() as dl:
                page.click("[data-testid=download-pdf]")
            pdf_path = work / dl.value.suggested_filename
            dl.value.save_as(str(pdf_path))
            page.wait_for_selector("text=PDF downloads logged: 1", timeout=10000)
            page.screenshot(path=str(shots / "08_after_download.png"))

            from pypdf import PdfReader
            text = "\n".join(pg.extract_text() for pg in PdfReader(io.BytesIO(pdf_path.read_bytes())).pages)
            check("PDF says approved by the signed-in email", f"Approved by {EMAIL}" in text)
            ap_rec = json.loads((store_dir / f"approvals/{RID}.json").read_text())
            check("approval record: signed-in uid + email",
                  ap_rec["approved_by_email"] == EMAIL and ap_rec["approved_by_id"] not in ("", "usr_demo_sarah"))
            audits = [json.loads(f.read_text()) for f in sorted((store_dir / f"audit/{RID}").glob("*.json"))]
            check("audit: approval + 1 download, both by the signed-in user",
                  [x["event"] for x in audits] == ["session_approved", "pdf_downloaded"]
                  and all(x["actor_email"] == EMAIL for x in audits))
            check("no PDF stored server-side", not list(store_dir.rglob("*.pdf")))

            page.click("button:has-text('Close')")
            page.click("[data-testid=signed-in-as] button:has-text('Sign out')")
            page.wait_for_selector("[data-testid=signin-form]")
            check("sign out returns to sign-in", True)

            # ---------------------------------------------------------- demo RBT (read-only)
            rbt = admin_creates_account(RBT_EMAIL, RBT_PASSWORD)
            verify_email(ctx, RBT_EMAIL, rbt["idToken"])
            subprocess.run([sys.executable, str(ROOT / "infra/review_allowlist.py"), "add", RBT_EMAIL, "--rbt",
                            "--local-dir", str(store_dir)], check=True, capture_output=True)
            time.sleep(1.2)
            seen = []
            page.on("response", lambda r: seen.append(r) if "/api/" in r.url else None)
            page.fill("input[type=email]", RBT_EMAIL)
            page.fill("input[type=password]", RBT_PASSWORD)
            page.click("button:has-text('Sign in')")
            page.wait_for_selector(".pipeline-table")
            check("RBT: top bar says read-only", "read-only" in page.inner_text("[data-testid=signed-in-as]").lower())
            check("RBT: list opens on their classroom (RM12)",
                  page.locator(REAL_ROWS).count() == 1 and page.locator(group("RM12")).count() == 1)
            page.click("button.filter-reset")
            check("RBT: every classroom is readable (All shows both)",
                  page.locator(REAL_ROWS).count() == 2)
            page.screenshot(path=str(shots / "09_rbt_list.png"), full_page=True)
            for cls in ("RM12", "Reading 3A"):
                page.click(f"{group(cls)} button:has-text('Review')")
                page.wait_for_selector(".turns li")
                # Timeline blocks / markers are buttons too, but they only scroll the transcript.
                check(f"RBT {cls}: header title is 'Sep 28, 2026 · 4:45 PM · {cls}'",
                      page.inner_text("[data-testid=session-title]").strip() == f"Sep 28, 2026 · 4:45 PM · {cls}")
                labels = [b.strip() for b in page.locator(".modal button:not(.speaker-timeline button)").all_inner_texts()]
                check(f"RBT {cls}: only Close renders (no edit/approve/export/move)", labels == ["Close"])
                page.locator(".speaker-timeline .tl-block").first.click()
                check(f"RBT {cls}: speaker timeline shows; a block jumps to its turn",
                      page.locator(".speaker-timeline .tl-speaker-track").count() >= 1
                      and page.locator(".turns li.turn-flash").count() == 1)
                check(f"RBT {cls}: no parent email on screen", "parent email" not in page.inner_text(".modal").lower())
                check(f"RBT {cls}: no 'voices separated' line", "voices separated" not in page.inner_text(".modal").lower())
                check(f"RBT {cls}: fields and speaker tags are read-only",
                      page.locator(".modal textarea:not([disabled]), .modal select:not([disabled])").count() == 0)
                page.screenshot(path=str(shots / f"10_rbt_{cls.replace(' ', '_')}.png"))
                page.click("button:has-text('Close')")
            bodies = []
            for r in seen:
                try:
                    bodies.append(r.text())
                except Exception:  # noqa: BLE001
                    pass
            blob = "\n".join(bodies)
            parent_texts = [json.loads((store_dir / f"drafts/{x}.json").read_text())["analysis"]["draft_parent_email"]
                            for x in (RID, READING_RID)]
            parent_texts.append(json.loads((store_dir / f"approvals/{RID}.json").read_text())["analysis"]["draft_parent_email"])
            check(f"RBT: no parent email in any of {len(bodies)} API responses",
                  bool(bodies) and "parent_email" not in blob and not any(t in blob for t in parent_texts))
            tok = emu_post("accounts:signInWithPassword", {"email": RBT_EMAIL, "password": RBT_PASSWORD,
                                                           "returnSecureToken": True})["idToken"]
            before = sorted(str(x) for x in store_dir.rglob("*"))
            refused = [api(f"/api/sessions/{READING_RID}/approve", tok, "POST", {"analysis": {}}),
                       api(f"/api/sessions/{READING_RID}/classroom", tok, "POST", {"class_code": "RM12"}),
                       api(f"/api/sessions/{RID}/pdf", tok)]
            check("RBT: direct approve / classroom change / PDF export refused (403 READ_ONLY_ROLE)",
                  all(st == 403 and json.loads(b) == {"error": "READ_ONLY_ROLE"} for st, b in refused))
            check("RBT: nothing written by those requests", sorted(str(x) for x in store_dir.rglob("*")) == before)
            check("RBT: no Activity Log / Settings tabs, no Download report button", page.locator(ADMIN_BUTTONS).count() == 0)
            admin_refused = [api("/api/admin/journal", tok), api("/api/admin/reports/activity.csv?by=person", tok),
                             api("/api/admin/settings", tok)]
            check("RBT: admin journal, settings and report CSV refused (403 ADMIN_ONLY)",
                  all(st == 403 and json.loads(b) == {"error": "ADMIN_ONLY"} for st, b in admin_refused))
            page.click("[data-testid=signed-in-as] button:has-text('Sign out')")
            page.wait_for_selector("[data-testid=signin-form]")

            # ---------------------------------------------------------- demo admin: journal + report
            adm = admin_creates_account(ADMIN_EMAIL, ADMIN_PASSWORD)
            verify_email(ctx, ADMIN_EMAIL, adm["idToken"])
            subprocess.run([sys.executable, str(ROOT / "infra/review_allowlist.py"), "add", ADMIN_EMAIL, "--admin",
                            "--local-dir", str(store_dir)], check=True, capture_output=True)
            time.sleep(1.2)
            page.set_viewport_size({"width": 1280, "height": 800})
            page.fill("input[type=email]", ADMIN_EMAIL)
            page.fill("input[type=password]", ADMIN_PASSWORD)
            page.click("button:has-text('Sign in')")
            page.wait_for_selector(".pipeline-table")
            adm_tok = emu_post("accounts:signInWithPassword", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD,
                                                               "returnSecureToken": True})["idToken"]
            tabs = [x.strip() for x in page.locator("[role=tab]").all_inner_texts()]
            check(f"admin: tabs in order {TAB_ORDER} (got {tabs})", tabs == TAB_ORDER)
            check("admin: no Download button at the top right (it moved into the Activity Log tab); no Journal button",
                  page.locator("[data-testid=site-header] [data-testid=open-report]").count() == 0
                  and page.locator("[data-testid=open-journal]").count() == 0
                  and page.locator("[data-testid=site-header] [data-testid=open-profile]").count() == 1)
            page.screenshot(path=str(shots / "11_admin_strip.png"))
            page.screenshot(path=str(shots / "12_admin_full_page.png"), full_page=True)
            # opening a student's IEP writes "viewed IEP" to the Journal
            page.click("[data-testid=tab-students]")
            page.wait_for_selector("[data-testid=open-student-stu_101]")
            page.click("[data-testid=open-student-stu_101]")
            page.wait_for_selector("[data-testid=student-page] [data-testid=iep]")
            page.screenshot(path=str(shots / "15_admin_student_page.png"), full_page=True)
            page.click("[data-testid=tab-settings]")
            page.wait_for_selector("[data-testid=settings]", timeout=15000)
            check("admin: Settings tab shows the config and Reset demo state",
                  page.locator("[data-testid=reset-demo]").count() == 1 and "Read-only" in page.inner_text("[data-testid=settings]"))
            page.screenshot(path=str(shots / "16_admin_settings.png"), full_page=True)
            page.click("[data-testid=tab-activity]")
            page.wait_for_selector("[data-testid=journal-table] tbody tr, [data-testid=journal-empty], "
                                   "[data-testid=journal-error]", timeout=15000)
            rows = page.locator("[data-testid=journal-table] tbody tr")
            first = rows.first.inner_text() if rows.count() else ""
            times = page.locator("[data-testid=journal-table] tbody tr td:first-child").all_inner_texts()
            check(f"admin: journal lists entries newest first, times in ET ({rows.count()} rows)",
                  rows.count() >= 5 and "· admin" in first and all(t.endswith("ET") for t in times)
                  and "Session approved" in page.inner_text("[data-testid=journal-table]"))
            check("admin: Activity Log is a tab (no pop-up, no Close)",
                  page.locator(".modal [data-testid=journal-panel]").count() == 0
                  and page.locator("[data-testid=journal-panel] button:has-text('Close')").count() == 0)
            st, j = api("/api/admin/journal", adm_tok)
            ev = [e for e in json.loads(j).get("entries", json.loads(j).get("events", [])) if e.get("action") == "iep_viewed"] if st == 200 else []
            check("admin: opening the student's IEP was journaled (iep_viewed, internal student ID, actor)",
                  bool(ev) and ev[0].get("student_id") == "stu_101" and ev[0].get("actor_id"))
            page.screenshot(path=str(shots / "13_admin_journal.png"), full_page=True)
            check("admin: Download report (CSV) is on the Activity Log page",
                  page.locator("[data-testid=journal-panel] [data-testid=open-report]").count() == 1)
            page.click("[data-testid=open-report]")
            page.check("input[value=student]")
            with page.expect_download() as dl:
                page.click("[data-testid=report-download]")
            csv_path = work / dl.value.suggested_filename
            dl.value.save_as(str(csv_path))
            page.wait_for_selector("[data-testid=report-msg]")
            csv_text = csv_path.read_text()
            check("admin: Download report saves the by-student CSV (fetched with the token)",
                  dl.value.suggested_filename.startswith("activity-by-student_") and csv_text.startswith("student,")
                  and "@" not in csv_text)
            page.screenshot(path=str(shots / "14_admin_report.png"))
            browser.close()
        logs = (work / "server.log").read_text()
        check("service logs carry no email", EMAIL not in logs and RBT_EMAIL not in logs and "example.org" not in logs)
    finally:
        for pr in procs:
            try:
                os.killpg(pr.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    ok = sum(r for _, r in results)
    print(f"\n{ok}/{len(results)} checks passed; screenshots in {shots}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
