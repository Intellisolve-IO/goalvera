#!/usr/bin/env python3
"""Dictation / paste check for every text box in the review app, lesson planner and the
student page's weekly summary & parent report ( review screen, planner,
 Students tab). Fully offline, same setup as
e2e_planner.py: built UI + review service + Firebase Auth emulator + fictional data.

Dictation apps (Wispr Flow, macOS Dictation, Dragon) put text into the focused box
in different ways. Each box is filled with each of these, then blurred, then the
screen is re-rendered, and the text must still be there (and in the saved data):

  paste        real Ctrl+V from the system clipboard
  insertText   document.execCommand('insertText')
  cdp_insert   Input.insertText (how IMEs / accessibility text arrives in Chrome)
  keys         typed key by key
  native_input prototype value setter + 'input' event (well-behaved automation)
  plain_input  el.value = ... + 'input' event (naive automation; React ignores it)
  ax_noevent   value changed with no event at all, then blur (accessibility set)

  python app/tools/e2e_dictation.py [--json out.json]
Needs: firebase-tools (FIREBASE_BIN), playwright + Chrome, a built UI (npx vite build).
Ports 8795 / 9299 so it can run next to the other e2e scripts.
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
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "tools"))
sys.path.insert(0, str(APP / "backend-pipeline"))
os.environ.setdefault("PLANNER_TODAY", "2026-10-07")

import e2e_planner as ep  # noqa: E402  (helpers only; its ports are overridden below)

PROJECT = "demo-goalvera-review"
EMU = "127.0.0.1:9299"
PORT = 8795
BASE = f"http://127.0.0.1:{PORT}"
ep.EMU, ep.PORT, ep.BASE = EMU, PORT, BASE
EMAIL, PASSWORD = "e2e-dictation@example.org", "correct-horse-battery"
METHODS = ["paste", "insertText", "cdp_insert", "keys", "native_input", "plain_input", "ax_noevent"]

JS_FILL = """([el, method, text]) => {
  el.focus();
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  if (method === 'insertText') { el.select(); return document.execCommand('insertText', false, text); }
  if (method === 'native_input') { setter.call(el, text); el.dispatchEvent(new Event('input', {bubbles: true})); return true; }
  if (method === 'plain_input') { el.value = text; el.dispatchEvent(new Event('input', {bubbles: true})); return true; }
  if (method === 'ax_noevent') { setter.call(el, text); return true; }
  return false;
}"""
results = []


def fill(page, loc, method, text):
    loc.scroll_into_view_if_needed()
    loc.click()
    if method == "paste":
        page.evaluate("t => navigator.clipboard.writeText(t)", text)
        page.keyboard.press("Control+A")
        page.keyboard.press("Control+V")
    elif method == "cdp_insert":
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(text)
    elif method == "keys":
        page.keyboard.press("Control+A")
        page.keyboard.type(text)
    else:
        loc.evaluate(f"(el, a) => ({JS_FILL})([el, a[0], a[1]])", [method, text])
    page.evaluate("() => document.activeElement && document.activeElement.blur()")


def record(screen, field, method, ok, detail=""):
    results.append({"screen": screen, "field": field, "method": method, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {screen} / {field} / {method} {detail}")


def review_fields(page):
    m = page.locator(".modal")
    out = [("Goal ID", m.locator(".card:not(.abc):not(.marker-add) input:not([type])").first),
           ("Evidence quote", m.locator(".card:not(.abc):not(.marker-add) textarea").nth(0)),
           ("Analysis", m.locator(".card:not(.abc):not(.marker-add) textarea").nth(1))]
    if m.locator(".card.abc").count():
        out += [("Antecedent", m.locator(".card.abc textarea").nth(0)),
                ("Behavior", m.locator(".card.abc textarea").nth(1)),
                ("Consequence / intervention", m.locator(".card.abc textarea").nth(2)),
                ("Incident name", m.locator(".card.abc .abc-name input").first)]
    out += [("Session summary", m.locator("section:has(h3:text-is('Session summary')) textarea"))]
    # no "Draft parent email" on the session any more
    return out


def run_review(page):
    page.click("[data-testid=tab-review]")
    page.wait_for_selector(".pipeline-table")
    page.locator(".pipeline-table tr:has-text('pending') button:has-text('Review')").first.click()
    page.wait_for_selector(".modal")
    for name, loc in review_fields(page):
        for method in METHODS:
            text = f"Dictated {name} via {method} 42"
            if method == "keys" and name == "Goal ID":
                text = "g1x"
            elif name == "Goal ID":
                text = f"g1_{method}"
            fill(page, loc, method, text)
            # re-render: another field changes state, then Save Draft (local) and a toast
            page.click(".modal button:text-is('Save Draft')")
            page.wait_for_timeout(150)
            # the field may have been re-mounted: find it again
            loc = dict(review_fields(page))[name]
            got = loc.input_value()
            record("review", name, method, got == text, "" if got == text else f"(got {got[:40]!r})")
            if name == "Goal ID":
                fill(page, loc, "native_input", "g1")
                loc = dict(review_fields(page))[name]
    page.click(".modal button:has-text('Close')")


def planner_editors(page):
    cur = page.locator("[data-testid=week-current]")
    nxt = page.locator("[data-testid=week-next]")
    return [
        ("plan", "Goals", lambda: nxt.locator("button:has-text('Edit plan'), button:has-text('Write plan')").first.click(),
         lambda: nxt.locator("textarea[name=goals]"), "Save draft", nxt),
        ("plan", "Objectives", lambda: nxt.locator("button:has-text('Edit plan'), button:has-text('Write plan')").first.click(),
         lambda: nxt.locator("textarea[name=objectives]"), "Save draft", nxt),
        ("plan", "The week's plan", lambda: nxt.locator("button:has-text('Edit plan'), button:has-text('Write plan')").first.click(),
         lambda: nxt.locator("textarea[name=plan]"), "Save draft", nxt),
        ("note", "Teacher note", lambda: cur.locator(".pl-note button.link").first.click(),
         lambda: cur.locator("[data-testid=note-editor] textarea[name=note]"), "Save note", cur),
    ]


def run_planner(page):
    page.click("[data-testid=tab-planner]")
    page.wait_for_selector("[data-testid=week-current] [data-testid=plan-summary]")
    page.wait_for_load_state("networkidle")
    for kind, name, open_ed, field, save_label, col in planner_editors(page):
        for method in METHODS:
            text = f"Planner {name} by {method} on Tuesday"
            open_ed()
            loc = field()
            loc.wait_for()
            fill(page, loc, method, text)
            # re-render inside the editor (a select / another field changing state)
            page.wait_for_timeout(100)
            got_dom = field().input_value()
            col.locator(f".pl-form button:has-text('{save_label}')").first.click()
            col_sel = "[data-testid=week-next]" if kind == "plan" else "[data-testid=week-current]"
            page.wait_for_selector(f"{col_sel} .pl-form", state="detached", timeout=8000)
            page.wait_for_load_state("networkidle")
            ok = text in col.inner_text()  # saved, reloaded from the service and shown
            record("planner", name, method, ok and got_dom == text,
                   "" if ok and got_dom == text else f"(field after blur {got_dom[:40]!r}; saved/shown={ok})")


def report_editors(page):
    """Weekly summary + parent report: built once, on the Students tab's student page ()."""
    rep = page.locator("[data-testid=weekly-report]")
    open_ed = lambda: rep.locator("button.link:has-text('summary')").first.click()  # noqa: E731
    return rep, [
        ("Weekly summary", open_ed, lambda: rep.locator("[data-testid=summary-editor] textarea[name=summary]")),
        ("IEP check reason", open_ed, lambda: rep.locator("[data-testid=summary-editor] textarea[name=iep_note]").first),
        ("Parent report", open_ed, lambda: rep.locator("[data-testid=summary-editor] textarea[name=parent_email]")),
    ]


def run_student_report(page):
    page.click("[data-testid=week-current] [data-testid=student-report-link]")
    page.wait_for_selector("[data-testid=weekly-report]")
    page.wait_for_load_state("networkidle")
    rep, editors = report_editors(page)
    for name, open_ed, field in editors:
        for method in METHODS:
            text = f"Report {name} by {method} on Tuesday"
            open_ed()
            loc = field()
            loc.wait_for()
            fill(page, loc, method, text)
            page.wait_for_timeout(100)
            got_dom = field().input_value()
            rep.locator(".pl-form button:has-text('Save draft')").first.click()
            page.wait_for_selector("[data-testid=weekly-report] .pl-form", state="detached", timeout=8000)
            try:   # saved, reloaded from the service and shown
                page.wait_for_function("t => document.querySelector('[data-testid=weekly-report]')"
                                       ".innerText.includes(t)", arg=text, timeout=8000)
                ok = True
            except Exception:  # noqa: BLE001
                ok = False
            record("student page", name, method, ok and got_dom == text,
                   "" if ok and got_dom == text else f"(field after blur {got_dom[:40]!r}; saved/shown={ok})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    dist = APP / "frontend-web/dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("build the UI first: (cd app/frontend-web && npx vite build)")
    from seed_planner_demo import seed_planner
    work = Path(tempfile.mkdtemp(prefix="e2e-dictation-"))
    store_dir = work / "bucket"
    seed_planner(str(store_dir))
    roster = json.loads((store_dir / "config/demo_roster.json").read_text())
    roster["teachers"]["usr_demo_sarah"]["emails"] = [EMAIL]
    (store_dir / "config/demo_roster.json").write_text(json.dumps(roster))
    (store_dir / "config/review_allowlist.json").write_text(json.dumps({"users": [{"email": EMAIL, "role": "teacher"}]}))
    (work / "firebase.json").write_text(json.dumps({"emulators": {"auth": {"port": 9299, "host": "127.0.0.1"},
                                                                 "ui": {"enabled": False}}}))
    fb = os.environ.get("FIREBASE_BIN", "/workspace/tools/fbtools/node_modules/.bin/firebase")
    env = {**os.environ, "PROJECT_ID": PROJECT, "FIREBASE_AUTH_EMULATOR_HOST": EMU, "REVIEW_ALLOWED_ORIGINS": BASE,
           "REVIEW_ALLOWLIST_TTL": "1", "PLANNER_AI": "offline", "JOURNAL_MODE": "memory"}
    env.pop("K_SERVICE", None)
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
        ep.make_user(EMAIL, PASSWORD)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome", headless=True)
            except Exception:  # noqa: BLE001
                browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1400, "height": 900})
            ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
            page = ctx.new_page()
            console = []
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"[:300]))
            ep.sign_in(page, EMAIL, PASSWORD)
            try:
                run_review(page)
                run_planner(page)
                run_student_report(page)
            except Exception:
                page.screenshot(path=str(work / "failure.png"), full_page=True)
                print(f"failure screenshot: {work / 'failure.png'}\n" + "\n".join(console[-10:]))
                raise
            browser.close()
    finally:
        for pr in procs:
            try:
                os.killpg(pr.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=1))
    bad = [r for r in results if not r["ok"]]
    print(f"\n{len(results) - len(bad)}/{len(results)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
