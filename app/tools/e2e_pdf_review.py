#!/usr/bin/env python3
"""End-to-end check of the approve -> Download PDF flow in the real review UI,
offline, on the synthetic sample session. Saves screenshots.

  1. seed the sample session (synthetic TTS clip -> recorded Chirp 3 + Gemini outputs)
     into a fresh local store (bucket layout)
  2. start review_api --local-dir and the Vite dev server
  3. in Chrome: open the session, check there is NO Download PDF button,
     edit the summary, Approve, check the button appears, download the PDF
  4. check the audit trail has the approval + download, that no PDF was stored,
     and render the PDF pages to PNG

  python app/tools/e2e_pdf_review.py --shots /workspace/ap-shots/pdf
Needs: playwright (+ Chrome or `playwright install chromium`), reportlab, pypdf,
poppler-utils (pdftoppm), node deps in app/frontend-web.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "tools"))
from seed_local_session import RID, seed  # noqa: E402

EDIT = " Teacher note: he chose the calm table on his own after the reminder."


def wait_http(url: str, timeout: float = 30) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    raise SystemExit(f"timed out waiting for {url}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default="/workspace/ap-shots/pdf")
    ap.add_argument("--keep-pdf", action="store_true", help="also keep the downloaded sample PDF in --shots")
    a = ap.parse_args()
    shots = Path(a.shots)
    shots.mkdir(parents=True, exist_ok=True)
    store = Path(tempfile.mkdtemp(prefix="ap-e2e-"))
    seed(str(store))
    procs = [
        subprocess.Popen([sys.executable, str(APP / "backend-pipeline/review_api/server.py"),
                          "--local-dir", str(store), "--port", "8787"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True),
        subprocess.Popen(["npx", "vite", "--port", "5173", "--strictPort"], cwd=APP / "frontend-web",
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True),   # own process group, so vite (npx's child) is stopped too
    ]
    checks: list[tuple[str, bool]] = []

    def check(name, ok):
        checks.append((name, bool(ok)))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")

    try:
        wait_http("http://127.0.0.1:8787/api/health")
        wait_http("http://localhost:5173/")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome", headless=True)
            except Exception:  # noqa: BLE001
                browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1360, "height": 1000}, accept_downloads=True)
            page = ctx.new_page()
            page.goto("http://localhost:5173/")
            page.evaluate("localStorage.clear()")
            page.reload()
            page.wait_for_selector(".pipeline-table")
            page.screenshot(path=str(shots / "01_review_list_pending.png"), full_page=True)

            page.click(".pipeline-table button:has-text('Review')")
            page.wait_for_selector(".turns li")
            check("Download PDF hidden before Approve", page.locator("[data-testid=download-pdf]").count() == 0)
            summary = page.locator("section:has(h3:text-is('Session summary')) textarea")
            summary.fill(summary.input_value() + EDIT)
            page.evaluate("document.querySelector('.modal').scrollTop = 0")
            page.screenshot(path=str(shots / "02_review_before_approve.png"))

            page.click("button:text-is('Approve')")
            page.wait_for_selector("[data-testid=download-pdf]", timeout=15000)
            check("Download PDF shown after Approve", True)
            head = page.inner_text(".modal-head")
            check("review screen shows 'approved by Sarah Jenkins'", "approved by Sarah Jenkins" in head)
            page.evaluate("document.querySelector('.modal').scrollTop = 0")
            page.screenshot(path=str(shots / "03_review_after_approve.png"))

            with page.expect_download() as dl:
                page.click("[data-testid=download-pdf]")
            pdf_tmp = Path(tempfile.mkdtemp()) / dl.value.suggested_filename
            dl.value.save_as(str(pdf_tmp))
            page.wait_for_selector("text=PDF downloads logged: 1", timeout=10000)
            page.screenshot(path=str(shots / "04_review_after_download.png"))
            browser.close()

        data = pdf_tmp.read_bytes()
        check("downloaded file is a PDF", data.startswith(b"%PDF"))
        txt = subprocess.run(["pdftotext", "-layout", str(pdf_tmp), "-"], capture_output=True, text=True).stdout
        for needle in ["Approved by Sarah Jenkins at", "RM12", "Sarah Jenkins", "Markers", "Incident",
                       "Mon, Sep 28, 2026, 4:45 PM EDT", "he chose the calm table on his own"]:
            check(f"PDF contains {needle!r}", needle in txt)
        audit = json.loads(urllib.request.urlopen(f"http://127.0.0.1:8787/api/sessions/{RID}/audit").read())["events"]
        check("audit trail = approval + 1 download",
              [e["event"] for e in audit] == ["session_approved", "pdf_downloaded"])
        import hashlib
        check("audit fingerprint matches the downloaded PDF",
              audit[-1].get("pdf_sha256") == hashlib.sha256(data).hexdigest())
        check("no PDF stored server-side", not list(store.rglob("*.pdf")))

        for old in shots.glob("05_pdf_page*.png"):
            old.unlink()
        subprocess.run(["pdftoppm", "-png", "-r", "110", str(pdf_tmp), str(shots / "05_pdf_page")], check=True)
        if a.keep_pdf:
            shutil.copy(pdf_tmp, shots / pdf_tmp.name)
        shutil.rmtree(pdf_tmp.parent, ignore_errors=True)
    finally:
        import signal
        for pr in procs:
            try:
                os.killpg(pr.pid, signal.SIGTERM) if pr is procs[1] else pr.terminate()
            except ProcessLookupError:
                pass
        for pr in procs:
            try:
                pr.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pr.kill()
        shutil.rmtree(store, ignore_errors=True)

    print("\nscreenshots:", *sorted(str(x) for x in shots.glob("*.png")), sep="\n  ")
    failed = [n for n, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
