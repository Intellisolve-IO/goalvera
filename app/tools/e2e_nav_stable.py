#!/usr/bin/env python3
"""The top navigation must not move between tabs.

Fully offline, same setup as e2e_planner.py: built UI + review service + Firebase Auth
emulator + fictional data. For a teacher and a demo admin, at 1024 / 1280 / 1440 px wide,
clicks every tab and records the bounding boxes of the header, the tab row,
every tab button and the signed-in name (opens My profile; Oct 10 the top-right Download button is gone).
Every box must be identical on
every tab (same role, same width), and the header must be the same height for both roles.
Screenshots of each tab's top go to --shots.

Then (Oct 9, school name + logo): the admin sets a long school name and uploads an SVG logo
(with a <script> in it) on the Settings tab. The top bar must show the name (cut with an
ellipsis) and the logo as a server-made PNG, and every box above must still be identical on
every tab and the same as with the default name and badge. Top-bar screenshots of both go to
--shots (branding_default.png, branding_custom.png).

  python app/tools/e2e_nav_stable.py --shots /tmp/nav-shots
Ports 8797 / 9399 so it can run next to the other e2e scripts.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "tools"))
sys.path.insert(0, str(APP / "backend-pipeline"))
os.environ.setdefault("PLANNER_TODAY", "2026-10-07")

import e2e_planner as ep  # noqa: E402  (helpers only; its ports are overridden below)

PROJECT = "demo-local-review"
EMU = "127.0.0.1:9399"
PORT = 8797
BASE = f"http://127.0.0.1:{PORT}"
ep.EMU, ep.PORT, ep.BASE = EMU, PORT, BASE
TEACHER = ("e2e-nav-teacher@example.org", "correct-horse-battery")
ADMIN = ("e2e-nav-admin@example.org", "battery-horse-staple-correct")
WIDTHS = [1024, 1280, 1440]
NAV = {"header": "[data-testid=site-header]",
       "tabrow": "[data-testid=site-header] [role=tablist]", "profile": "[data-testid=open-profile]",
       "signed_in": "[data-testid=signed-in-as]"}
results = []
TOKENS = {}


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{'' if ok else '  ' + detail}")


def boxes(page):
    out = {}
    for k, sel in NAV.items():
        loc = page.locator(sel)
        out[k] = loc.first.bounding_box() if loc.count() else None
    for i, b in enumerate(page.locator("[role=tab]").all()):
        out[f"tab{i}"] = b.bounding_box()
    out["tabs_wrap"] = page.evaluate(
        "() => new Set([...document.querySelectorAll('[role=tab]')].map(t => t.getBoundingClientRect().top)).size")
    out["page_width"] = page.evaluate("() => document.documentElement.clientWidth")  # scrollbar gutter
    out["content_top"] = page.evaluate(
        "() => { const m = document.querySelector('[data-testid=tab-content]'); return m ? m.getBoundingClientRect().top : null; }")
    return out


def sign_in(page, email, password):
    page.goto(BASE + "/#recordings")
    page.wait_for_selector("[data-testid=signin-form]")
    page.fill("input[type=email]", email)
    page.fill("input[type=password]", password)
    page.click("button:has-text('Sign in')")
    page.wait_for_selector("[role=tablist]")
    page.wait_for_timeout(800)


LONG_NAME = "Peachtree Learning Academy for Exceptional Children of the Greater Riverside Area"
TALL_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="60" height="120" viewBox="0 0 60 120" onload="alert(1)">
<script>document.title='pwned'</script><rect width="60" height="120" rx="10" fill="#1d4e89"/>
<circle cx="30" cy="40" r="18" fill="#f2b632"/><rect x="12" y="72" width="36" height="30" fill="#ffffff"/></svg>"""


def brand_state(page):
    return page.evaluate("""() => {
      const n = document.querySelector('[data-testid=school-name]');
      const img = document.querySelector('[data-testid=school-logo]');
      const bar = document.querySelector('.site-bar');
      return { name: n && n.textContent, truncated: !!n && n.scrollWidth > n.clientWidth,
               logo: !!img && img.complete && img.naturalWidth > 0, logo_src: img && img.src,
               badge: !!document.querySelector('[data-testid=school-badge]'),
               bar_h: bar && bar.getBoundingClientRect().height, title: document.title };
    }""")


def set_custom_branding(browser, shots, workdir):
    ctx = browser.new_context(viewport={"width": 1024, "height": 800}, device_scale_factor=2)
    page = ctx.new_page()
    sign_in(page, *ADMIN)
    before = brand_state(page)
    check("default top bar: Everyday Heroes Academy with its badge", before["name"] == "Everyday Heroes Academy" and (before["logo"] or before["badge"]),
          json.dumps(before))
    page.screenshot(path=str(shots / "branding_default.png"), clip={"x": 0, "y": 0, "width": 1024, "height": 120})
    page.locator("[data-testid=tab-settings]").click()
    page.wait_for_selector("[data-testid=school-name-input]")
    page.fill("[data-testid=school-name-input]", LONG_NAME)
    page.click("[data-testid=school-name-save]")
    page.wait_for_selector("[data-testid=school-msg]")
    svg = workdir / "logo.svg"
    svg.write_text(TALL_SVG)
    with page.expect_response(lambda r: r.url.endswith("/api/admin/branding/logo")) as resp:
        page.set_input_files("[data-testid=school-logo-input]", str(svg))
    check("admin: SVG logo upload accepted", resp.value.status == 200, str(resp.value.status))
    page.wait_for_selector(".site-bar [data-testid=school-logo]")
    page.wait_for_timeout(500)
    after = brand_state(page)
    check("custom top bar: long name shown and cut with an ellipsis",
          after["name"] == LONG_NAME and after["truncated"], json.dumps(after))
    check("custom top bar: logo shown (no badge), page script untouched",
          after["logo"] and not after["badge"] and after["title"] != "pwned", json.dumps(after))
    check("custom top bar: same height as the default", after["bar_h"] == before["bar_h"],
          f"{before['bar_h']} vs {after['bar_h']}")
    import urllib.request
    info = json.load(urllib.request.urlopen(urllib.request.Request(
        BASE + "/api/branding", headers={"Authorization": f"Bearer {TOKENS[TEACHER[0]]}"})))
    with urllib.request.urlopen(urllib.request.Request(
            BASE + info["logo"]["url"], headers={"Authorization": f"Bearer {TOKENS[TEACHER[0]]}"})) as r:
        body, ctype = r.read(), r.headers.get("Content-Type")
    served = {"type": ctype, "png": body[:8] == b"\x89PNG\r\n\x1a\n", "teacher_reads_name": info["school_name"] == LONG_NAME,
              "script": any(x in body.lower() for x in (b"script", b"alert", b"<svg"))}
    check("logo served as a PNG made on the server, no SVG or script in it",
          served["type"] == "image/png" and served["png"] and not served["script"] and served["teacher_reads_name"], json.dumps(served))
    page.locator("[data-testid=school-settings]").screenshot(path=str(shots / "branding_settings.png"))
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(shots / "branding_custom.png"), clip={"x": 0, "y": 0, "width": 1024, "height": 120})
    ctx.close()


def run_role(browser, role, email, password, shots):
    heights = {}
    for w in WIDTHS:
        ctx = browser.new_context(viewport={"width": w, "height": 800}, device_scale_factor=1)
        page = ctx.new_page()
        sign_in(page, email, password)
        tabs = page.locator("[role=tab]").all_inner_texts()
        seen = {}
        for i, name in enumerate(tabs):
            page.locator("[role=tab]").nth(i).click()
            page.wait_for_timeout(900)          # let the tab load its data (lists, tables)
            page.evaluate("window.scrollTo(0, 0)")
            seen[name] = boxes(page)
            page.screenshot(path=str(shots / f"{role}_{w}_{i + 1}_{name.replace(' ', '_')}.png"),
                            clip={"x": 0, "y": 0, "width": w, "height": 320})
        first = next(iter(seen.values()))
        diff = {n: [k for k in first if b.get(k) != first[k]] for n, b in seen.items()}
        diff = {n: ks for n, ks in diff.items() if ks}
        check(f"{role} @ {w}px: header, tab row, tabs, the name (My profile) "
              f"and content start identical on all {len(tabs)} tabs", not diff,
              json.dumps({n: {k: (first[k], seen[n][k]) for k in ks} for n, ks in diff.items()})[:1500])
        check(f"{role} @ {w}px: tabs on one line", all(b["tabs_wrap"] == 1 for b in seen.values()))
        if role.startswith("admin"):
            check(f"{role} @ {w}px: no Download button in the header (Activity Log has it); the name is in the top bar",
                  page.locator("[data-testid=site-header] [data-testid=open-report]").count() == 0
                  and first["profile"] is not None and first["profile"]["y"] < first["tabrow"]["y"])
        heights[w] = first["header"]["height"], first["tabrow"]["y"]
        ctx.close()
    return heights


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="/tmp/nav-shots")
    a = ap.parse_args()
    shots = Path(a.shots)
    shots.mkdir(parents=True, exist_ok=True)
    dist = APP / "frontend-web/dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("build the UI first: (cd app/frontend-web && npx vite build)")
    from seed_planner_demo import seed_planner
    work = Path(tempfile.mkdtemp(prefix="e2e-nav-"))
    store_dir = work / "bucket"
    seed_planner(str(store_dir))
    roster = json.loads((store_dir / "config/demo_roster.json").read_text())
    roster["teachers"]["usr_demo_sarah"]["emails"] = [TEACHER[0]]
    (store_dir / "config/demo_roster.json").write_text(json.dumps(roster))
    (store_dir / "config/review_allowlist.json").write_text(json.dumps({"users": [
        {"email": TEACHER[0], "role": "reviewer"}, {"email": ADMIN[0], "role": "admin"}]}))
    (work / "firebase.json").write_text(json.dumps({"emulators": {"auth": {"port": 9399, "host": "127.0.0.1"},
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
        for email, pw in (TEACHER, ADMIN):
            TOKENS[email] = ep.make_user(email, pw)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome", headless=True)
            except Exception:  # noqa: BLE001
                browser = p.chromium.launch(headless=True)
            t = run_role(browser, "teacher", *TEACHER, shots)
            ad = run_role(browser, "admin", *ADMIN, shots)
            for w in WIDTHS:
                check(f"@ {w}px: header height and tab row position the same for teacher and admin",
                      t[w] == ad[w], f"teacher {t[w]} admin {ad[w]}")
            set_custom_branding(browser, shots, work)
            t2 = run_role(browser, "teacher_custom", *TEACHER, shots)
            ad2 = run_role(browser, "admin_custom", *ADMIN, shots)
            for w in WIDTHS:
                check(f"@ {w}px: custom name + logo leave the header and tab row where they were",
                      t2[w] == t[w] and ad2[w] == ad[w], f"teacher {t[w]}->{t2[w]} admin {ad[w]}->{ad2[w]}")
            browser.close()
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
