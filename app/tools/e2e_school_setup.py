#!/usr/bin/env python3
"""Domains + Classes tabs in a real browser.

Fully offline, same setup as e2e_nav_stable.py: built UI + review service + Firebase Auth
emulator + the fictional Everyday Heroes Academy roster (app/samples/demo_roster.json) and one
fictional session in Room 104 on a Monday at 8:25 AM.

  admin:   the Domains tab lists the 6 seed domains with all four columns; adds a domain, edits it,
           removes it; the Classes tab lists all 32 fictional classes; edits a class's schedule to two
           meeting patterns; every change is in the Activity Log.
  teacher: Domains read-only; Classes opens on their classroom (Room 104); can edit their own class,
           not someone else's; on the session the class picker suggests "Room 104 Reading" and the
           teacher saves it; the materials panel shows the class's teaching approach; the transcript's
           speaker selector lists Room 104 Reading's people first (Alex M., Flynn R., Doc D., ...), search
           finds Mary P. (BCBA), and a choice labels the turns.

Screenshots go to --shots (domains_admin.png, domains_editor.png, classes_admin.png, class_editor.png,
domains_teacher.png, classes_teacher.png, session_class.png, speaker_lookup_timeline.png, speaker_lookup.png).

  python app/tools/e2e_school_setup.py --shots /workspace/domains
Ports 8798 / 9400 so it can run next to the other e2e scripts.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "tools"))
sys.path.insert(0, str(APP / "backend-pipeline"))
sys.path.insert(0, str(APP / "backend-pipeline/tests"))

import e2e_planner as ep  # noqa: E402  (helpers only; its ports are overridden below)

PROJECT = "demo-local-review"
EMU = "127.0.0.1:9400"
PORT = 8798
BASE = f"http://127.0.0.1:{PORT}"
ep.EMU, ep.PORT, ep.BASE = EMU, PORT, BASE
TEACHER = ("e2e-setup-teacher@example.org", "correct-horse-battery")   # Alex Morgan (stf_0001), Room 104
ADMIN = ("e2e-setup-admin@example.org", "battery-horse-staple-correct")
DOMAINS = ["Reading & Phonics", "Mathematics", "Handwriting & Writing", "Social-Emotional & Self-Regulation",
           "Science & Social Studies", "Physical & Creative Arts"]
results = []
TOKENS = {}


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{'' if ok else '  ' + str(detail)[:800]}")


def api(path, email):
    req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {TOKENS[email]}"})
    return json.load(urllib.request.urlopen(req))


def sign_in(page, email, password, tab="recordings"):
    page.goto(BASE + f"/#{tab}")
    page.wait_for_selector("[data-testid=signin-form]")
    page.fill("input[type=email]", email)
    page.fill("input[type=password]", password)
    page.click("button:has-text('Sign in')")
    page.wait_for_selector("[role=tablist]")
    page.wait_for_timeout(600)


def open_tab(page, key):
    page.locator(f"[data-testid=tab-{key}]").click()
    page.wait_for_timeout(700)


def full(page, shots, name):
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(shots / name), full_page=True)


def admin_flow(browser, shots):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept())
    sign_in(page, *ADMIN)
    tabs = page.locator("[role=tab]").all_inner_texts()
    check("admin tabs: Recordings, Students, Classes, Lesson planner, Domains, Teachers, Activity Log, Settings",
          tabs == ["Recordings", "Students", "Classes", "Lesson planner", "Domains", "Teachers", "Activity Log", "Settings"], tabs)

    open_tab(page, "domains")
    page.wait_for_selector("[data-testid=domain-row]")
    names = [t.strip() for t in page.locator("[data-testid=domain-row] td:first-child").all_inner_texts()]
    check("Domains: the document's 6 domains in order", names == DOMAINS, names)
    table = page.locator("[data-testid=domain-table]").inner_text()
    check("Domains: four columns from the document (OG, CPA, Eureka Math, Van de Walle, Zones)",
          all(x in table for x in ("Orton-Gillingham", "Concrete-Pictorial-Abstract", "Eureka Math", "Van de Walle",
                                   "Zones of Regulation")), table[:400])
    check("Domains: operational delivery formats shown", page.locator("[data-testid=delivery-formats] li").count() == 3)
    full(page, shots, "domains_admin.png")

    page.click("[data-testid=add-domain]")
    ed = page.locator("[data-testid=domain-editor]")
    ed.locator("[name=name]").fill("Life Skills (e2e)")
    ed.locator("[name=methodology]").fill("Task Analysis")
    ed.locator("[name=methodology_detail]").fill("Break daily routines into small taught steps.")
    ed.locator("[name=programs]").fill("Sample program")
    page.screenshot(path=str(shots / "domains_editor.png"), full_page=True)
    ed.locator("button[type=submit]").click()
    page.wait_for_selector("[data-testid=domain-row]:has-text('Life Skills (e2e)')")
    check("admin adds a domain", True)
    page.click("[data-testid=edit-domain-dom-life-skills-e2e]")
    page.locator("[data-testid=domain-editor] [name=book]").fill("A sample guide")
    page.locator("[data-testid=domain-editor] button[type=submit]").click()
    page.wait_for_selector("[data-testid=domain-row]:has-text('A sample guide')")
    check("admin edits it", True)
    page.click("[data-testid=edit-domain-dom-life-skills-e2e]")
    page.click("[data-testid=remove-domain]")
    page.wait_for_timeout(800)
    check("admin removes it", page.locator("[data-testid=domain-row]:has-text('Life Skills (e2e)')").count() == 0)

    open_tab(page, "classes")
    page.wait_for_selector("[data-testid=class-row]")
    check("Classes: admin opens on every classroom: 32 fictional classes", page.locator("[data-testid=class-row]").count() == 32,
          page.locator("[data-testid=class-row]").count())
    check("Classes: labelled fictional", "fictional" in page.locator("[data-testid=classes-fictional-note]").inner_text().lower())
    full(page, shots, "classes_admin.png")
    page.click("[data-testid=edit-class-cls-rm104-mth]")
    page.click("[data-testid=add-meeting]")
    m = page.locator("[data-testid=meeting]").nth(1)
    m.locator(".cl-days label:has-text('Tue')").click()     # a new pattern starts with no days
    m.locator("input[type=time]").fill("13:30")
    m.locator("input[type=number]").fill("25")
    page.locator("[data-testid=class-editor]").screenshot(path=str(shots / "class_editor.png"))
    page.locator("[data-testid=class-editor] button[type=submit]").click()
    page.wait_for_timeout(900)
    row = page.locator("[data-testid=class-row]:has-text('Room 104 Math') [data-testid=class-schedule-text]").inner_text()
    check("admin gives Room 104 Math a second meeting pattern", "Tue 1:30 PM, 25 min" in row, row)

    acts = [r.get("action") for r in api("/api/admin/journal?limit=50", ADMIN[0]).get("entries", [])]
    check("Activity Log: domain added / edited / removed and class edited",
          {"domain_added", "domain_edited", "domain_removed", "class_edited"} <= set(acts), acts[:12])
    ctx.close()


def teacher_flow(browser, shots, rid):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
    page = ctx.new_page()
    sign_in(page, *TEACHER)
    open_tab(page, "domains")
    page.wait_for_selector("[data-testid=domain-row]")
    check("teacher: Domains read-only (no Add, no Edit)",
          page.locator("[data-testid=add-domain]").count() == 0 and page.locator("[data-testid^=edit-domain-]").count() == 0)
    full(page, shots, "domains_teacher.png")
    open_tab(page, "classes")
    page.wait_for_selector("[data-testid=class-row]")
    rooms = set(page.locator("[data-testid=class-row] td:nth-child(2)").all_inner_texts())
    check("teacher: Classes opens on their classroom (Room 104)", rooms == {"Room 104"}, rooms)
    check("teacher: can add no class; can edit their own",
          page.locator("[data-testid=add-class]").count() == 0 and page.locator("[data-testid=edit-class-cls-rm104-rdg]").count() == 1)
    full(page, shots, "classes_teacher.png")

    page.goto(BASE + f"/#recordings/{rid}")
    page.wait_for_selector("[data-testid=session-class]", timeout=20000)
    page.wait_for_timeout(600)
    box = page.locator("[data-testid=session-class]")
    check("session: suggests Room 104 Reading (Room 104, Monday 8:25 AM)", "Room 104 Reading" in box.inner_text(), box.inner_text())
    page.select_option("[data-testid=class-select]", "cls-rm104-rdg")
    page.click("[data-testid=save-class]")
    page.wait_for_timeout(900)
    box.screenshot(path=str(shots / "session_class.png"))
    sc = api(f"/api/sessions/{rid}/class", TEACHER[0])
    check("session: the teacher's pick is saved", sc.get("source") == "teacher" and (sc.get("class") or {}).get("id") == "cls-rm104-rdg", sc)
    # speaker selector: the class's people first, then search everyone, then generic options
    page.wait_for_selector("[data-testid=speaker-select]", timeout=20000)
    sel = page.locator("[data-testid=speaker-select]").first
    groups = sel.locator("optgroup").evaluate_all("gs => gs.map(g => g.label)")
    check("speakers: class group first, then everyone else, then Other",
          groups[:1] == ["In Room 104 Reading"] and groups[-1] == "Other" and "Everyone else" in groups, groups)
    first = sel.locator("optgroup").first.locator("option").all_inner_texts()
    check("speakers: Alex M. (teacher), Flynn R. (RBT), Doc D. first; Sneezy D. in the class",
          first[:3] == ["Alex M. · Lead Special Education Teacher", "Flynn R. · RBT", "Doc D."] and "Sneezy D." in first, first)
    sel.select_option("Sneezy D.")
    page.wait_for_timeout(300)
    turns = page.locator(".speaker-label").all_inner_texts()
    check("speakers: the choice labels that speaker's turns", any(t.startswith("Sneezy D. (Speaker 1)") for t in turns), turns[:3])
    search = page.locator("[data-testid=speaker-search]").nth(1)
    search.fill("bcba")
    page.wait_for_timeout(300)
    found = page.locator("[data-testid=speaker-select]").nth(1).locator("optgroup").nth(1).locator("option").all_inner_texts()
    check("speakers: search finds Mary P. (BCBA) outside the class", found == ["Mary P. · BCBA"], found)
    page.locator("[data-testid=speaker-select]").nth(1).select_option("Mary P.")
    search.fill("")
    page.locator("[data-testid=speaker-timeline]").evaluate("el => el.scrollIntoView({block: 'center'})")
    page.wait_for_timeout(300)
    page.locator("[data-testid=speaker-timeline]").screenshot(path=str(shots / "speaker_lookup_timeline.png"))
    # a native dropdown can't be captured open, so show the first selector's list in place for the picture
    page.locator("[data-testid=speaker-search]").first.fill("")
    sel.evaluate("el => { el.size = 18; el.style.height = 'auto'; el.closest('.tl-row').style.alignItems = 'start'; }")
    page.set_viewport_size({"width": 1280, "height": 1400})
    sel.evaluate("el => el.closest('[data-testid=speaker-timeline]').scrollIntoView({block: 'center'})")
    page.wait_for_timeout(300)
    page.locator("[data-testid=speaker-timeline]").screenshot(path=str(shots / "speaker_lookup.png"))
    sel.evaluate("el => { el.size = 0; el.style.height = ''; el.closest('.tl-row').style.alignItems = ''; }")
    ta = page.locator("[data-testid=teaching-approach]")
    check("materials: the teaching approach (Reading & Phonics, Orton-Gillingham) is shown",
          ta.count() == 1 and "Orton-Gillingham" in ta.inner_text(), ta.all_inner_texts())
    ctx.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="/tmp/school-setup-shots")
    a = ap.parse_args()
    shots = Path(a.shots)
    shots.mkdir(parents=True, exist_ok=True)
    dist = APP / "frontend-web/dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("build the UI first: (cd app/frontend-web && npx vite build)")
    from review_api.store import LocalStore
    from test_materials import RID, seed
    work = Path(tempfile.mkdtemp(prefix="e2e-setup-"))
    store_dir = work / "bucket"
    store = LocalStore(str(store_dir))
    roster = json.loads((APP / "samples/demo_roster.json").read_text())
    roster["teachers"]["stf_0001"]["emails"] = [TEACHER[0]]
    seed(store, roster=roster)
    d = store.read_json(f"drafts/{RID}.json")
    d["session"]["start_time"] = "2026-10-05T08:25:00-04:00"      # a Monday, 8:25 AM ET
    d["session"]["duration_seconds"] = 600
    store.write_json(f"drafts/{RID}.json", d)
    (store_dir / "config/review_allowlist.json").write_text(json.dumps({"users": [
        {"email": TEACHER[0], "role": "reviewer"}, {"email": ADMIN[0], "role": "admin"}]}))
    (work / "firebase.json").write_text(json.dumps({"emulators": {"auth": {"port": 9400, "host": "127.0.0.1"},
                                                                 "ui": {"enabled": False}}}))
    fb = os.environ.get("FIREBASE_BIN", "/workspace/tools/fbtools/node_modules/.bin/firebase")
    env = {**os.environ, "PROJECT_ID": PROJECT, "FIREBASE_AUTH_EMULATOR_HOST": EMU, "REVIEW_ALLOWED_ORIGINS": BASE,
           "REVIEW_ALLOWLIST_TTL": "1", "PLANNER_AI": "offline", "JOURNAL_MODE": "memory",
           "MATERIALS_EMAIL_DEFAULT": "off"}
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
            admin_flow(browser, shots)
            teacher_flow(browser, shots, RID)
            browser.close()
    finally:
        for pr in procs:
            try:
                os.killpg(pr.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        print(f"server log: {work / 'server.log'}")
    ok = sum(r for _, r in results)
    print(f"\n{ok}/{len(results)} checks passed; screenshots in {shots}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
