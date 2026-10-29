#!/usr/bin/env python3
"""My profile + admins' role / email changes in a real browser (David, Oct 10 2026; tasks#107).

Fully offline, same setup as e2e_school_setup.py: built UI + review service + Firebase Auth emulator +
the fictional Everyday Heroes Academy roster (app/samples/demo_roster.json).

  every role: no Download button at the top right; the signed-in name opens My profile.
  teacher (Alex Morgan): edits display name, preferred name, phone, default classroom (only Room 104 is
           offered), title, credential type; role and email read-only with "Ask an admin to change this";
           the top-right name shows the new display name.
  RBT (Flynn Rider): may edit their own profile (the rest of the app stays read-only).
  admin:   Download report (CSV) is on the Activity Log page; the Teachers tab shows each person's app role
           and sign-in email; the admin moves Alex Morgan to a new sign-in email (the old one stops working,
           the new one signs her in as the same teacher); the journal shows profile_updated and
           access_changed with field names only.

Screenshot: --shots/my_profile.png (the teacher's My profile).

  python app/tools/e2e_my_profile.py --shots /workspace/domains
Ports 8799 / 9401 so it can run next to the other e2e scripts.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "tools"))
sys.path.insert(0, str(APP / "backend-pipeline"))
sys.path.insert(0, str(APP / "backend-pipeline/tests"))

import e2e_planner as ep  # noqa: E402  (helpers only; its ports are overridden below)

PROJECT = "demo-atlanta-review"
EMU = "127.0.0.1:9401"
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
ep.EMU, ep.PORT, ep.BASE = EMU, PORT, BASE
TEACHER = ("e2e-profile-teacher@example.org", "correct-horse-battery")   # Alex Morgan (stf_0001), Room 104
RBT = ("e2e-profile-rbt@example.org", "staple-battery-horse")            # Flynn Rider (stf_0010)
ADMIN = ("e2e-profile-admin@example.org", "battery-horse-staple-correct")
NEW_EMAIL = ("e2e-profile-snow@example.org", "horse-staple-battery-correct")
results = []
TOKENS = {}


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{'' if ok else '  ' + str(detail)[:800]}")


def api(path, email):
    req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {TOKENS[email]}"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def sign_in(page, email, password, tab="recordings"):
    page.goto(BASE + f"/#{tab}")
    page.wait_for_selector("[data-testid=signin-form]")
    page.fill("input[type=email]", email)
    page.fill("input[type=password]", password)
    page.click("button:has-text('Sign in')")
    page.wait_for_selector("[role=tablist]")
    page.wait_for_timeout(600)


def open_profile(page):
    page.click("[data-testid=open-profile]")
    page.wait_for_selector("[data-testid=profile-form]")


def teacher_flow(browser, shots):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
    page = ctx.new_page()
    sign_in(page, *TEACHER)
    check("teacher: no Download button at the top right", page.locator("[data-testid=open-report]").count() == 0)
    check("teacher: the top-right name is the staff record's name",
          page.inner_text("[data-testid=open-profile]").strip() == "Alex Morgan", page.inner_text("[data-testid=open-profile]"))
    open_profile(page)
    opts = page.locator("[data-testid=profile-default-classroom] option").all_inner_texts()
    check("teacher: default classroom offers only her own classroom", opts == ["Automatic", "Room 104"], opts)
    check("teacher: role and email read-only, 'Ask an admin to change this'",
          page.inner_text("[data-testid=profile-email]") == TEACHER[0]
          and page.inner_text("[data-testid=profile-role]") == "Teacher"
          and "ask an admin to change this" in page.inner_text("[data-testid=profile-ask-admin]").lower()
          and page.locator("[data-testid=profile-email-input]").count() == 0)
    page.fill("[data-testid=profile-display-name]", "Pat Lee")
    page.fill("[data-testid=profile-preferred-name]", "Pat")
    page.fill("[data-testid=profile-phone]", "(404) 555-0104")
    page.select_option("[data-testid=profile-default-classroom]", "Room 104")
    page.fill("[data-testid=profile-title]", "Lead Special Education Teacher")
    page.select_option("[data-testid=profile-credential]", "Special education certificate")
    page.click("[data-testid=profile-save]")
    page.wait_for_selector("[data-testid=profile-saved]")
    check("teacher: saved; the top-right name shows the new display name",
          page.inner_text("[data-testid=open-profile]").strip() == "Pat Lee")
    st, me = api("/api/profile/me", TEACHER[0])
    check("teacher: the profile is stored (phone, preferred name, credential)",
          st == 200 and me["phone"] == "(404) 555-0104" and me["preferred_name"] == "Pat"
          and me["credential_type"] == "Special education certificate", me)
    page.locator("[data-testid=my-profile]").screenshot(path=str(shots / "my_profile.png"))
    page.click("[data-testid=profile-close]")
    ctx.close()


def rbt_flow(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
    page = ctx.new_page()
    sign_in(page, *RBT)
    open_profile(page)
    check("RBT: My profile opens, role shown read-only",
          page.inner_text("[data-testid=profile-role]") == "RBT (read-only)")
    page.fill("[data-testid=profile-preferred-name]", "Flynn")
    page.click("[data-testid=profile-save]")
    page.wait_for_selector("[data-testid=profile-saved], [data-testid=profile-error]")
    check("RBT: may save their own profile", page.locator("[data-testid=profile-saved]").count() == 1,
          page.locator("[data-testid=profile-error]").all_inner_texts())
    ctx.close()


def admin_flow(browser, shots):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=1)
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept())
    sign_in(page, *ADMIN)
    check("admin: no Download button in the header",
          page.locator("[data-testid=site-header] [data-testid=open-report]").count() == 0)
    page.click("[data-testid=tab-activity]")
    page.wait_for_selector("[data-testid=journal-panel]")
    check("admin: Download report (CSV) is on the Activity Log page",
          page.locator("[data-testid=journal-panel] [data-testid=open-report]").count() == 1)
    open_profile(page)
    check("admin: My profile works for an admin who is not in the roster",
          page.inner_text("[data-testid=profile-role]") == "Admin")
    page.click("[data-testid=profile-close]")

    page.click("[data-testid=tab-teachers]")
    page.wait_for_selector("[data-testid=teacher-row]")
    snow = page.locator("[data-testid=teacher-row]:has-text('Alex Morgan')").first
    check("admin: Teachers tab shows Alex Morgan's app role and sign-in email",
          "Teacher" in snow.locator("[data-testid=teacher-access]").inner_text()
          and TEACHER[0] in snow.locator("[data-testid=teacher-access]").inner_text())
    snow.locator("[data-testid=access-change]").click()
    page.fill("[data-testid=access-email]", NEW_EMAIL[0])
    page.click("[data-testid=access-save]")
    page.wait_for_selector(f"[data-testid=teacher-row]:has-text('{NEW_EMAIL[0]}')")
    page.locator("[data-testid=teacher-row]:has-text('Alex Morgan')").first.screenshot(path=str(shots / "my_profile_admin_row.png"))
    st_old, _ = api("/api/me", TEACHER[0])
    me_old = _
    TOKENS[NEW_EMAIL[0]] = ep.make_user(*NEW_EMAIL)
    st_new, me_new = api("/api/me", NEW_EMAIL[0])
    check("admin: the email change moved the sign-in entry (old address no longer approved, new one is the same teacher)",
          me_old.get("approved") is False and st_new == 200 and me_new.get("role") == "teacher"
          and me_new.get("display_name") == "Pat Lee", (st_old, me_old, st_new, me_new))
    st, j = api("/api/admin/journal?action=access_changed", ADMIN[0])
    ent = j.get("entries", []) if st == 200 else []
    check("admin: journal has access_changed with field names only (no addresses)",
          bool(ent) and "email" in json.dumps(ent[0].get("details")) and "@" not in json.dumps(ent), ent[:1])
    st, j = api("/api/admin/journal?action=profile_updated", ADMIN[0])
    ent = j.get("entries", []) if st == 200 else []
    check("admin: journal has profile_updated without values (no phone, no names)",
          len(ent) >= 2 and "555" not in json.dumps(ent) and "Pat Lee" not in json.dumps(ent), ent[:2])
    ctx.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="/tmp/my-profile-shots")
    a = ap.parse_args()
    shots = Path(a.shots)
    shots.mkdir(parents=True, exist_ok=True)
    dist = APP / "frontend-web/dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("build the UI first: (cd app/frontend-web && npx vite build)")
    from review_api.store import LocalStore
    from test_materials import seed
    work = Path(tempfile.mkdtemp(prefix="e2e-profile-"))
    store_dir = work / "bucket"
    store = LocalStore(str(store_dir))
    roster = json.loads((APP / "samples/demo_roster.json").read_text())
    roster["teachers"]["stf_0001"]["emails"] = [TEACHER[0]]
    roster["staff"]["stf_0010"]["emails"] = [RBT[0]]
    seed(store, roster=roster)
    (store_dir / "config/review_allowlist.json").write_text(json.dumps({"users": [
        {"email": TEACHER[0], "role": "reviewer"}, {"email": RBT[0], "role": "rbt"}, {"email": ADMIN[0], "role": "admin"}]}))
    (work / "firebase.json").write_text(json.dumps({"emulators": {"auth": {"port": 9401, "host": "127.0.0.1"},
                                                                 "ui": {"enabled": False}}}))
    fb = os.environ.get("FIREBASE_BIN", "/workspace/tools/fbtools/node_modules/.bin/firebase")
    env = {**os.environ, "PROJECT_ID": PROJECT, "FIREBASE_AUTH_EMULATOR_HOST": EMU, "REVIEW_ALLOWED_ORIGINS": BASE,
           "REVIEW_ALLOWLIST_TTL": "1", "PLANNER_AI": "offline", "JOURNAL_MODE": "memory",
           "MATERIALS_EMAIL_DEFAULT": "off"}
    for k in ("K_SERVICE", "SCHOOL_PROFILE", "PROFILE_SELF_EDIT_ACCESS"):
        env.pop(k, None)
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
        for email, pw in (TEACHER, RBT, ADMIN):
            TOKENS[email] = ep.make_user(email, pw)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome", headless=True)
            except Exception:  # noqa: BLE001
                browser = p.chromium.launch(headless=True)
            teacher_flow(browser, shots)
            rbt_flow(browser)
            admin_flow(browser, shots)
            browser.close()
        logs = (work / "server.log").read_text()
        check("service logs carry no email or phone", "example.org" not in logs and "555-0104" not in logs)
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
