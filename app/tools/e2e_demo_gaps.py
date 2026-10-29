#!/usr/bin/env python3
"""Demo gaps in a real browser (David, Oct 10 2026; tasks#106). Fully offline, same setup as
e2e_school_setup.py: built UI + review service + Firebase Auth emulator + the fictional Everyday
Heroes Academy roster, one fictional Room 104 session (Monday 8:25 AM, Reading), two fake uploads
still processing and one failed. Email goes to an in-memory outbox (nothing leaves the machine).

  teacher (Alex Morgan): Recordings shows the Uploading / processing rows; the session's "Students in
           this session" offers Room 104 Reading's 10 students, not confirmed; one click confirms; the
           session then shows on Sneezy's week; after the weekly report is approved, "Send weekly
           email" shows the demo redirect, Sneezy's contacts (Wren receives reports; Rory, a
           non-custodial parent, and Jamal are excluded) and the exact email; one click sends it to
           the teacher's own staff.example address.
  admin:   Settings shows the parent email demo redirect (on); the Activity Log has
           session_students_set and parent_email_sent.

Screenshots (--shots): processing_rows.png, session_students_suggested.png, session_students_saved.png,
student_week_linked.png, parent_email_preview.png, parent_email_sent.png, parent_email_setting.png.

  python app/tools/e2e_demo_gaps.py --shots /workspace/demo-script/shots
"""
from __future__ import annotations

import argparse
import datetime as dt
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
sys.path.insert(0, str(APP / "backend-pipeline/tests"))

import e2e_planner as ep  # noqa: E402

PROJECT = "demo-atlanta-review"
EMU = "127.0.0.1:9410"
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
ep.EMU, ep.PORT, ep.BASE = EMU, PORT, BASE
TEACHER = ("e2e-gaps-teacher@staff.example", "correct-horse-battery")     # Alex Morgan (stf_0001)
ADMIN = ("e2e-gaps-admin@staff.example", "battery-horse-staple-correct")
SNEEZY = "stu_0006"
results, TOKENS = [], {}


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{'' if ok else '  ' + str(detail)[:800]}")


def api(path, email, body=None):
    h = {"Authorization": f"Bearer {TOKENS[email]}"}
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=h, method="POST" if body is not None else "GET")
    return json.load(urllib.request.urlopen(req))


def sign_in(page, email, password, tab="recordings"):
    page.goto(BASE + f"/#{tab}")
    page.wait_for_selector("[data-testid=signin-form]")
    page.fill("input[type=email]", email)
    page.fill("input[type=password]", password)
    page.click("button:has-text('Sign in')")
    page.wait_for_selector("[role=tablist]")
    page.wait_for_timeout(600)


def teacher_flow(browser, shots, rid, week):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
    page = ctx.new_page()
    sign_in(page, *TEACHER)
    page.wait_for_selector("[data-testid=processing-group]", timeout=20000)
    rows = page.locator("[data-testid=processing-row]").all_inner_texts()
    check("Recordings: 3 Uploading / processing rows (uploaded, transcribing, failed)",
          len(rows) == 3 and any("transcribing" in r.lower() for r in rows)
          and any("transcription failed" in r.lower() for r in rows), rows)
    page.locator("[data-testid=processing-group]").screenshot(path=str(shots / "processing_rows.png"))
    page.screenshot(path=str(shots / "processing_rows_page.png"))

    page.goto(BASE + f"/#recordings/{rid}")
    page.wait_for_selector("[data-testid=session-students-unsaved]", timeout=20000)
    page.wait_for_timeout(600)
    box = page.locator("[data-testid=session-students]")
    t = box.inner_text()
    check("session: Room 104 Reading's 10 students suggested, not confirmed",
          "not confirmed" in t.lower() and "Suggested: the 10 students of Room 104 Reading" in t, t[:300])
    box.scroll_into_view_if_needed()
    box.screenshot(path=str(shots / "session_students_suggested.png"))
    page.uncheck("[data-testid=session-student-stu_0043]")            # Russell was absent
    page.click("[data-testid=save-session-students]")
    page.wait_for_selector("[data-testid=session-students-saved]")
    page.wait_for_timeout(400)
    box.screenshot(path=str(shots / "session_students_saved.png"))
    v = api(f"/api/sessions/{rid}/students", TEACHER[0])
    check("session: 9 students saved on the server", v["saved"] and len(v["student_ids"]) == 9 and "stu_0043" not in v["student_ids"], v)

    page.goto(BASE + f"/#students/{SNEEZY}/{week}")
    page.wait_for_selector("[data-testid=student-page]", timeout=20000)
    page.wait_for_timeout(800)
    days = page.locator("[data-testid=student-day]").first.inner_text()
    check("Sneezy's week: the linked classroom session shows on Monday", "Room 104" in days, days[:300])
    page.locator("[data-testid=student-day]").first.screenshot(path=str(shots / "student_week_linked.png"))

    api("/api/planner/summary", TEACHER[0], {
        "student_id": SNEEZY, "week": week, "summary": "Sneezy joined the reading group every day.",
        "parent_email": "Hello! This week Sneezy read along with the group and practiced new sight words. "
                        "Ask about the story with the dragon!", "approve": True})
    page.reload()
    page.wait_for_selector("[data-testid=parent-send]", timeout=20000)
    page.wait_for_timeout(600)
    ps = page.locator("[data-testid=parent-send]")
    t = ps.inner_text()
    check("send panel: demo redirect banner to the teacher's own address",
          f"parent emails go to {TEACHER[0]}, not to parents" in t, t[:400])
    check("send panel: Wren receives reports; Rory (non-custodial) and Jamal excluded",
          "Wren" in t and "excluded: not set to receive reports" in t.lower() and "Rory" in t and "excluded: no email" in t.lower(), t[:600])
    check("send panel: exact email shown with the 'would have gone to' banner",
          "Demo: would have gone to Wren at w" in t and "Ask about the story with the dragon!" in t, t[:800])
    ps.scroll_into_view_if_needed()
    ps.screenshot(path=str(shots / "parent_email_preview.png"))
    page.click("[data-testid=send-weekly-email]")
    page.wait_for_selector("[data-testid=last-sent]", timeout=20000)
    page.wait_for_timeout(400)
    t = ps.inner_text()
    check("send: 1 demo email to the teacher, not to parents", "Sent 1 demo email" in t and "not to parents" in t, t[-400:])
    ps.screenshot(path=str(shots / "parent_email_sent.png"))
    ctx.close()


def admin_flow(browser, shots):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
    page = ctx.new_page()
    sign_in(page, *ADMIN)
    page.locator("[data-testid=tab-settings]").click()
    page.wait_for_selector("[data-testid=parent-email-setting]", timeout=20000)
    t = page.locator("[data-testid=parent-email-setting]").inner_text()
    check("Settings: parent email demo redirect is on", "demo redirect" in t and t.count("On") >= 1, t)
    page.locator("[data-testid=parent-email-setting]").screenshot(path=str(shots / "parent_email_setting.png"))
    acts = [r.get("action") for r in api("/api/admin/journal?limit=50", ADMIN[0]).get("entries", [])]
    check("Activity Log: session_students_set and parent_email_sent", {"session_students_set", "parent_email_sent"} <= set(acts), acts[:15])
    ctx.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="/tmp/demo-gaps-shots")
    a = ap.parse_args()
    shots = Path(a.shots)
    shots.mkdir(parents=True, exist_ok=True)
    dist = APP / "frontend-web/dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("build the UI first: (cd app/frontend-web && npx vite build)")
    from review_api.planner import today, week_of
    from review_api.store import LocalStore
    from test_materials import RID, seed
    week = week_of(today())
    work = Path(tempfile.mkdtemp(prefix="e2e-gaps-"))
    store_dir = work / "bucket"
    store = LocalStore(str(store_dir))
    roster = json.loads((APP / "samples/demo_roster.json").read_text())
    roster["teachers"]["stf_0001"]["emails"] = [TEACHER[0]]
    seed(store, roster=roster)
    d = store.read_json(f"drafts/{RID}.json")
    start = f"{week.isoformat()}T08:25:00-04:00"                     # Monday of this week, 8:25 AM ET (Reading)
    d["session"]["start_time"] = d["session"]["timestamp_start"] = start
    d["session"]["duration_seconds"] = 600
    store.write_json(f"drafts/{RID}.json", d)
    stamp = dt.datetime.now().strftime("%Y%m%d")
    for name, man in ((f"{stamp}_090000_RM104", None),
                      (f"{stamp}_091000_RM104", {"status": "transcribing", "duration_seconds": 1500}),
                      (f"{stamp}_092000_RM104", {"status": "failed", "error_code": "STT_3"})):
        p = store_dir / f"rec/dev01/{name}.wav"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"RIFF")
        if man:
            store.write_json(f"work/rec_dev01_{name}/manifest.json", {"recording_id": f"rec_dev01_{name}", **man})
    (store_dir / "config/review_allowlist.json").write_text(json.dumps({"users": [
        {"email": TEACHER[0], "role": "reviewer"}, {"email": ADMIN[0], "role": "admin"}]}))
    (work / "firebase.json").write_text(json.dumps({"emulators": {"auth": {"port": 9410, "host": "127.0.0.1"},
                                                                 "ui": {"enabled": False}}}))
    fb = os.environ.get("FIREBASE_BIN", "/workspace/tools/fbtools/node_modules/.bin/firebase")
    env = {**os.environ, "PROJECT_ID": PROJECT, "FIREBASE_AUTH_EMULATOR_HOST": EMU, "REVIEW_ALLOWED_ORIGINS": BASE,
           "REVIEW_ALLOWLIST_TTL": "1", "PLANNER_AI": "offline", "JOURNAL_MODE": "memory",
           "MATERIALS_EMAIL_DEFAULT": "off", "MATERIALS_MAIL": "outbox"}
    env.pop("K_SERVICE", None)
    env.pop("SCHOOL_PROFILE", None)
    procs = [
        subprocess.Popen([fb, "emulators:start", "--only", "auth", "--project", PROJECT], cwd=work,
                         stdout=open(work / "emu.log", "w"), stderr=subprocess.STDOUT, start_new_session=True),
        subprocess.Popen([sys.executable, str(APP / "backend-pipeline/review_api/server.py"), "--auth", "emulator",
                          "--local-dir", str(store_dir), "--static", str(dist), "--port", str(PORT)],
                         env=env, stdout=open(work / "server.log", "w"), stderr=subprocess.STDOUT,
                         start_new_session=True),
    ]
    try:
        ep.wait_http(f"http://{EMU}/")
        ep.wait_http(f"{BASE}/api/auth-config")
        for email, pw in (TEACHER, ADMIN):
            TOKENS[email] = ep.make_user(email, pw)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome", headless=True)
            except Exception:  # noqa: BLE001
                browser = p.chromium.launch(headless=True)
            teacher_flow(browser, shots, RID, week.isoformat())
            admin_flow(browser, shots)
            browser.close()
    finally:
        for pr in procs:
            try:
                os.killpg(pr.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        time.sleep(0.2)
        print(f"server log: {work / 'server.log'}")
    ok = sum(r for _, r in results)
    print(f"\n{ok}/{len(results)} checks passed; screenshots in {shots}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
