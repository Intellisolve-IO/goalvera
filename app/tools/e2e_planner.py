#!/usr/bin/env python3
"""Browser click-through of the weekly lesson planner (tasks#83), fully offline:
built UI + review service (same code as Cloud Run) + Firebase Auth emulator +
a local bucket copy with fictional weeks of RM12 sessions (seed_planner_demo.py).
"Today" is pinned (PLANNER_TODAY, default 2026-10-07) so the weeks are stable.

  teacher: Lesson planner tab -> last week / this week (anchored) / next week ->
  arrows step only the left column (back x2, forward, "Back to last week") while
  this week and next week stay put -> each student card links to "Weekly summary &
  parent report →" (no summary on the planner) -> a note quoting a transcript is
  refused (8 words) -> AI draft of next week's goals + objectives -> saved
  Students tab (tasks#85): the link opens the student page for that week (IEP,
  Mon-Fri days) -> save a weekly summary with IEP check, stored as "the student" ->
  previous week -> "Generate draft weekly parent report" (marked "AI draft · not
  saved") -> saved as AI-assisted, never sent
  (PLANNER_AI=offline: the local stand-in gets the same masked payload; no network)
  RBT: same screens, no edit controls, report button disabled, direct saves refused (403)

  python app/tools/e2e_planner.py --shots shots/planner
Needs: firebase-tools (FIREBASE_BIN), playwright + Chrome, a built UI (npx vite build).
Uses ports 8789 / 9199 so it can run next to e2e_hosted_review.py.
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
import urllib.error
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "tools"))
sys.path.insert(0, str(APP / "backend-pipeline"))

PROJECT = "demo-local-review"
EMU = "127.0.0.1:9199"
PORT = 8789
BASE = f"http://127.0.0.1:{PORT}"
TODAY = os.environ.setdefault("PLANNER_TODAY", "2026-10-07")
LAST, THIS, NEXT = "2026-09-28", "2026-10-05", "2026-10-12"
EMAIL, PASSWORD = "e2e-teacher@example.org", "correct-horse-battery"
RBT_EMAIL, RBT_PASSWORD = "e2e-demo-rbt@example.org", "staple-battery-horse"
QUOTE = "He said good morning, everyone. Marcus, let's start with our math warm-up."
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


def make_user(email, password):
    """Admin-created, email-verified account (the emulator keeps the verify link)."""
    acct = emu_post("accounts:signUp", {"email": email, "password": password, "returnSecureToken": True})
    emu_post("accounts:sendOobCode", {"requestType": "VERIFY_EMAIL", "idToken": acct["idToken"]})
    codes = json.load(urllib.request.urlopen(f"http://{EMU}/emulator/v1/projects/{PROJECT}/oobCodes"))
    link = [c for c in codes["oobCodes"] if c["email"] == email][-1]["oobLink"]
    urllib.request.urlopen(link.replace("http://127.0.0.1:9199", f"http://{EMU}"))
    return emu_post("accounts:signInWithPassword", {"email": email, "password": password,
                                                    "returnSecureToken": True})["idToken"]


def api(path, tok, method="GET", body=None):
    h = {"Authorization": f"Bearer {tok}"}
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, method=method, headers=h)) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def weeks(page):
    return [page.get_attribute(f"[data-testid=week-{s}]", "data-week") for s in ("past", "current", "next")]


def wait_past(page, week):
    page.wait_for_selector(f"[data-testid=week-past][data-week='{week}'] [data-testid=plan-summary]")
    page.wait_for_load_state("networkidle")


def sign_in(page, email, password):
    page.goto(BASE + "/#planner")
    page.wait_for_selector("[data-testid=signin-form]")
    page.fill("input[type=email]", email)
    page.fill("input[type=password]", password)
    page.click("button:has-text('Sign in')")
    page.wait_for_selector("[data-testid=week-current] [data-testid=plan-summary]")
    page.wait_for_load_state("networkidle")


def _run(page, ctx, shots, store_dir, teacher_tok, rbt_tok):
    # ---------------------------------------------------------- teacher
    sign_in(page, EMAIL, PASSWORD)
    check("planner tab opens from #planner", page.get_attribute("[data-testid=tab-planner]",
                                                                "aria-selected") == "true")
    check("three columns: last week / this week / next week", weeks(page) == [LAST, THIS, NEXT])
    titles = [page.inner_text(f"[data-testid=week-{s}] .pl-week-name") for s in ("past", "current", "next")]
    check("column titles", [t.replace("ANCHORED", "").replace("Anchored", "").strip() for t in titles]
          == ["Last week", "This week", "Next week"])
    check("this week is marked anchored", page.locator("[data-testid=week-current] .pl-anchor").count() == 1)
    check("arrows live on the last-week column only",
          page.locator("[data-testid=week-past] .pl-arrow").count() == 2
          and page.locator("[data-testid=week-current] .pl-arrow, [data-testid=week-next] .pl-arrow").count() == 0)
    check("forward is disabled on last week", page.is_disabled("[data-testid=week-forward]"))
    cur = page.locator("[data-testid=week-current]")
    html = cur.inner_html()
    check("plan summary comes first: goals + objectives as bullets, then the week's plan",
          html.index("Goals") < html.index("Objectives") < html.index("The week’s plan") < html.index("Students")
          and cur.locator("[data-testid=plan-goals] li").count() == 2
          and cur.locator("[data-testid=plan-objectives] li").count() == 3)
    card = cur.locator("[data-testid=student-card]")
    check("per-student card: name, sessions, link to the weekly summary & parent report",
          "Marcus A." in card.inner_text()
          and card.locator("[data-testid=student-report-link]").get_attribute("href") == f"#students/stu_101/{THIS}")
    check("no weekly summary, IEP check or parent email on the planner",
          page.locator("[data-testid=planner-board] [data-testid=summary-editor], "
                       "[data-testid=planner-board] [data-testid=summary-ai-draft], "
                       "[data-testid=planner-board] [data-testid=parent-email]").count() == 0
          and "Review & save summary" not in page.inner_text("[data-testid=planner-board]"))
    check("plan stored as 'the student', shown with the name",
          "Marcus leading the warm-up" in cur.inner_text())
    page.screenshot(path=str(shots / "01_planner_teacher.png"))
    page.screenshot(path=str(shots / "01_planner_teacher_full.png"), full_page=True)

    before = cur.inner_text()
    next_before = page.inner_text("[data-testid=week-next]")
    page.click("[data-testid=week-back]")
    wait_past(page, "2026-09-21")
    page.click("[data-testid=week-back]")
    wait_past(page, "2026-09-14")
    check("back x2: left column shows 3 weeks ago", weeks(page) == ["2026-09-14", THIS, NEXT]
          and "3 weeks ago" in page.inner_text("[data-testid=week-past] .pl-week-name"))
    check("this week did not move or change", cur.inner_text() == before)
    check("next week did not move or change", page.inner_text("[data-testid=week-next]") == next_before)
    page.screenshot(path=str(shots / "02_planner_three_weeks_back.png"))
    page.click("[data-testid=week-forward]")
    wait_past(page, "2026-09-21")
    check("forward: left column 2 weeks ago", weeks(page) == ["2026-09-21", THIS, NEXT])
    page.click("button:has-text('Back to last week')")
    wait_past(page, LAST)
    check("'Back to last week' resets the left column", weeks(page) == [LAST, THIS, NEXT]
          and page.is_disabled("[data-testid=week-forward]"))

    cur.locator("button:has-text('Add note')").click()
    cur.locator("[data-testid=note-editor] textarea").fill(QUOTE)
    cur.locator("button:has-text('Save note')").click()
    page.wait_for_selector("[data-testid=week-current] [data-testid=planner-error]")
    err = cur.locator("[data-testid=planner-error]").inner_text()
    check("note quoting a transcript is refused (8 words in a row)", "8 or more words" in err)
    check("nothing saved for the refused note",
          not (store_dir / f"planner/students/stu_101/{THIS}/note").exists())
    page.evaluate("document.querySelector('[data-testid=week-current] [data-testid=planner-error]')"
                  ".scrollIntoView({block: 'center'})")
    page.screenshot(path=str(shots / "04_quote_refused.png"))
    cur.locator("[data-testid=note-editor] textarea").fill("Probe 3 on Friday; keep the timer card out.")
    cur.locator("button:has-text('Save note')").click()
    page.wait_for_selector("[data-testid=week-current] [data-testid=note-text]:has-text('Probe 3')")
    check("teacher note saved", True)

    nxt = page.locator("[data-testid=week-next]")
    nxt.locator("button:has-text('Edit plan')").click()
    nxt.locator("textarea[name=objectives]").fill("Introduce borrowing with base-ten blocks\nTwo guided problems a day")
    nxt.locator("button:has-text('Save draft')").click()
    page.wait_for_selector("[data-testid=week-next] [data-testid=plan-objectives] li:nth-child(2)")
    check("teacher edits next week's plan (new draft version)",
          len(list((store_dir / f"planner/plans/rm12/{NEXT}").glob("*.json"))) == 2)
    # ---------------------------------------------------------- AI plan draft (offline stand-in, no network)
    nxt.locator("[data-testid=plan-ai-open]").click()
    page.wait_for_selector("[data-testid=week-next] [data-testid=plan-editor] [data-testid=ai-draft-note]")
    goals_text = nxt.locator("textarea[name=goals]").input_value()
    check("AI plan draft fills next week's goals and objectives, marked AI draft",
          "Marcus" in goals_text and nxt.locator("textarea[name=objectives]").input_value().count("\n") >= 2
          and "the week of Oct 5" in nxt.locator("[data-testid=ai-draft-note]").inner_text())
    page.evaluate("document.querySelector('[data-testid=week-next] [data-testid=plan-editor]')"
                  ".scrollIntoView({block: 'center'})")
    page.screenshot(path=str(shots / "08_ai_plan_draft.png"))
    nxt.locator("button:has-text('Save draft')").click()
    page.wait_for_selector("[data-testid=week-next] [data-testid=plan-goals] li:has-text('Marcus')")
    plans = sorted((store_dir / f"planner/plans/rm12/{NEXT}").glob("*.json"))
    check("saved AI plan draft: new version, AI-assisted, 'the student' stored",
          len(plans) == 3 and json.loads(plans[-1].read_text()).get("ai_assisted", {}).get("source")
          == "offline_stand_in" and "Marcus" not in plans[-1].read_text())

    page.wait_for_load_state("networkidle")
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(shots / "05_planner_after_saves.png"))
    page.screenshot(path=str(shots / "05_planner_after_saves_full.png"), full_page=True)

    page.reload()
    page.wait_for_selector("[data-testid=week-current] [data-testid=plan-summary]")
    check("reload keeps the planner and this week anchored", weeks(page) == [LAST, THIS, NEXT])

    # ---------------------------------------------------------- Students tab: summary + parent report
    page.click("[data-testid=week-current] [data-testid=student-report-link]")
    page.wait_for_selector("[data-testid=student-page]")
    page.wait_for_load_state("networkidle")
    sp = page.locator("[data-testid=student-page]")
    check("link opens the Students tab on this student and week",
          page.get_attribute("[data-testid=tab-students]", "aria-selected") == "true"
          and page.evaluate("location.hash") == f"#students/stu_101/{THIS}" and "Marcus A." in sp.inner_text())
    check("student page: IEP (read-only), then Mon-Fri, then the report",
          sp.locator("[data-testid=iep] .iep-goals li").count() == 2
          and sp.locator("[data-testid=student-day]").count() == 5
          and sp.inner_html().index('data-testid="iep"') < sp.inner_html().index('data-testid="student-days"')
          < sp.inner_html().index('data-testid="weekly-report"'))
    page.screenshot(path=str(shots / "09_student_page.png"), full_page=True)
    sp.locator("button:has-text('Review & save summary')").click()
    ed = sp.locator("[data-testid=summary-editor]")
    ed.locator("textarea[name=summary]").fill("Marcus had a strong start: break card used on his own, probe 2 done.")
    ed.locator("select").first.select_option("evidence_seen")
    ed.locator("textarea[name=iep_note]").first.fill("Asked for the break card twice without a prompt")
    page.screenshot(path=str(shots / "03_summary_editor.png"))
    ed.locator("button:has-text('Save & approve')").click()
    page.wait_for_selector("[data-testid=weekly-report] [data-testid=summary-text]:has-text('strong start')")
    check("teacher saved the weekly summary on the student page (approved)",
          sp.locator("[data-testid=weekly-report] .pl-pill.approved").count() == 1)
    saved = [json.loads(f.read_text()) for f in (store_dir / f"planner/students/stu_101/{THIS}/summary").glob("*.json")]
    check("summary stored with 'the student', not the name",
          len(saved) == 1 and "Marcus" not in json.dumps(saved) and saved[0]["summary"].startswith("the student had"))

    sp.locator("[aria-label='Previous week']").click()
    page.wait_for_function(f"location.hash === '#students/stu_101/{LAST}'")
    page.wait_for_selector("[data-testid=student-page] h3:has-text('Week of')")
    page.wait_for_load_state("networkidle")
    check("previous week on the student page", page.evaluate("location.hash") == f"#students/stu_101/{LAST}")
    page.click("[data-testid=generate-parent-report]")
    page.wait_for_selector("[data-testid=weekly-report] [data-testid=summary-editor] [data-testid=ai-draft-note]")
    rep_ = page.locator("[data-testid=weekly-report]")
    note = rep_.locator("[data-testid=ai-draft-note]").inner_text()
    check("'Generate draft weekly parent report' opens an AI draft marked 'AI draft · not saved'",
          "ai draft" in note.lower() and "not saved" in note.lower() and "offline stand-in" in note
          and "2 approved session notes and 1 teacher note" in note)
    check("AI draft: name shown on screen, per-goal status filled, parent email drafted",
          "Marcus" in rep_.locator("textarea[name=summary]").input_value()
          and rep_.locator("[data-testid=summary-editor] select").first.input_value() == "evidence_seen"
          and rep_.locator("textarea[name=parent_email]").input_value().strip() != "")
    check("AI draft is not stored before the teacher saves",
          not (store_dir / f"planner/students/stu_101/{LAST}/summary").exists())
    page.screenshot(path=str(shots / "07_ai_summary_draft.png"), full_page=True)
    rep_.locator("[data-testid=summary-editor] button:has-text('Save & approve')").click()
    page.wait_for_selector("[data-testid=weekly-report] .pl-pill.ai-saved")
    saved = [json.loads(f.read_text()) for f in (store_dir / f"planner/students/stu_101/{LAST}/summary").glob("*.json")]
    check("saved AI draft: marked AI-assisted, stored as 'the student'",
          len(saved) == 1 and saved[0]["ai_assisted"]["source"] == "offline_stand_in"
          and "Marcus" not in json.dumps(saved))
    check("parent report shown as a draft, not sent",
          "not sent" in page.inner_text("[data-testid=parent-email]").lower())
    page.screenshot(path=str(shots / "10_parent_report_saved.png"), full_page=True)
    page.evaluate("location.hash = '#students/stu_101/2026-09-14'")
    page.wait_for_selector("[data-testid=weekly-report] h3:has-text('Sep 14')")
    page.wait_for_load_state("networkidle")
    check("3 weeks ago (student page): suggestion shows goals not yet shown",
          page.locator("[data-testid=weekly-report] .pl-check.not_seen").count() == 2)
    page.click("[data-testid=signed-in-as] button:has-text('Sign out')")
    page.wait_for_selector("[data-testid=signin-form]")

    # ---------------------------------------------------------- RBT (read-only)
    sign_in(page, RBT_EMAIL, RBT_PASSWORD)
    board = page.locator("[data-testid=planner-board]")
    labels = [b.strip() for b in board.locator("button:not(.pl-arrow)").all_inner_texts()]
    check("RBT: same three weeks", weeks(page) == [LAST, THIS, NEXT])
    check("RBT: no edit / save / AI draft controls (only the week arrows)",
          labels == [] and "AI draft" not in board.inner_text())
    check("RBT: planner shows the report link, not the summary",
          page.locator("[data-testid=week-current] [data-testid=student-report-link]").count() == 1
          and "strong start" not in page.inner_text("[data-testid=week-current]"))
    page.click("[data-testid=week-back]")
    wait_past(page, "2026-09-21")
    check("RBT: arrows work, this week stays", weeks(page) == ["2026-09-21", THIS, NEXT])
    page.screenshot(path=str(shots / "06_planner_rbt_read_only.png"))
    page.click("[data-testid=week-current] [data-testid=student-report-link]")
    page.wait_for_selector("[data-testid=weekly-report] [data-testid=summary-text]")
    check("RBT: student page shows the teacher's saved summary, report button disabled",
          "strong start" in page.inner_text("[data-testid=weekly-report]")
          and page.is_disabled("[data-testid=generate-parent-report]")
          and page.locator("[data-testid=weekly-report] button:has-text('Review & save summary')").count() == 0)
    page.screenshot(path=str(shots / "11_student_page_rbt.png"), full_page=True)
    snapshot = sorted(str(x) for x in store_dir.rglob("*"))
    refused = [api("/api/planner/note", rbt_tok, "POST", {"student_id": "stu_101", "week": THIS, "note": "x"}),
               api("/api/planner/draft/summary", rbt_tok, "POST", {"student_id": "stu_101", "week": LAST}),
               api("/api/planner/plan", rbt_tok, "POST", {"class_code": "RM12", "week": THIS, "plan": "x"})]
    check("RBT: direct saves and AI drafts refused (403 READ_ONLY_ROLE)",
          all(st == 403 and b == {"error": "READ_ONLY_ROLE"} for st, b in refused))
    check("RBT: nothing written", sorted(str(x) for x in store_dir.rglob("*")) == snapshot)
    st, v = api("/api/planner?class_code=RM12", teacher_tok)
    check("API: this week anchored for the teacher too", st == 200 and v["this_week"] == THIS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="shots/planner")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=800)
    a = ap.parse_args()
    shots = Path(a.shots)
    shots.mkdir(parents=True, exist_ok=True)
    dist = APP / "frontend-web/dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("build the UI first: (cd app/frontend-web && npx vite build)")

    from seed_planner_demo import seed_planner
    work = Path(tempfile.mkdtemp(prefix="e2e-planner-"))
    store_dir = work / "bucket"
    seed_planner(str(store_dir))
    roster = json.loads((store_dir / "config/demo_roster.json").read_text())
    roster["teachers"]["usr_demo_sarah"]["emails"] = [EMAIL]
    roster["rbts"]["rbt_demo_rm12"]["emails"] = [RBT_EMAIL]
    (store_dir / "config/demo_roster.json").write_text(json.dumps(roster))
    (store_dir / "config/review_allowlist.json").write_text(json.dumps({"users": [
        {"email": EMAIL, "role": "teacher"}, {"email": RBT_EMAIL, "role": "rbt"}]}))
    (work / "firebase.json").write_text(json.dumps({"emulators": {"auth": {"port": 9199, "host": "127.0.0.1"},
                                                                 "ui": {"enabled": False}}}))
    fb = os.environ.get("FIREBASE_BIN", "/workspace/tools/fbtools/node_modules/.bin/firebase")
    env = {**os.environ, "PROJECT_ID": PROJECT, "FIREBASE_AUTH_EMULATOR_HOST": EMU,
           "REVIEW_ALLOWED_ORIGINS": BASE, "REVIEW_ALLOWLIST_TTL": "1", "PLANNER_TODAY": TODAY,
           "PLANNER_AI": "offline"}
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
        wait_http(f"http://{EMU}/")
        wait_http(f"{BASE}/api/auth-config")
        teacher_tok = make_user(EMAIL, PASSWORD)
        rbt_tok = make_user(RBT_EMAIL, RBT_PASSWORD)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome", headless=True)
            except Exception:  # noqa: BLE001
                browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": a.width, "height": a.height}, device_scale_factor=1)
            page = ctx.new_page()
            console = []
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}"[:300]))
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"[:300]))
            try:
                _run(page, ctx, shots, store_dir, teacher_tok, rbt_tok)
            except Exception:
                page.screenshot(path=str(work / "failure.png"), full_page=True)
                print(f"failure screenshot: {work / 'failure.png'}\nbrowser console:\n" + "\n".join(console[-15:]))
                raise
            browser.close()

        logs = (work / "server.log").read_text()
        check("service logs carry no email or student name",
              "example.org" not in logs and "Marcus" not in logs)
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
