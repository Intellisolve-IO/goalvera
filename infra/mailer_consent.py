#!/usr/bin/env python3
"""One-time approval for classroom-materials email (tasks#99): the sender mailbox signs in once.

The review service sends classroom materials with the Gmail API as ONE mailbox, which approves
exactly one Gmail scope, https://www.googleapis.com/auth/gmail.send (send only, no reading),
plus "openid email" so this script can check WHO approved. No domain-wide delegation.

  python3 infra/mailer_consent.py --client-json ~/Downloads/client_secret_XXXX.json
  python3 infra/mailer_consent.py --client-json ... --manual       # browser on another computer
  python3 infra/mailer_consent.py --client-json ... --test-to snowwhite@staff.example

What it does:
  1. builds the Google sign-in link (PKCE, login hint = $MAILER_SENDER_MAILBOX, staff.example only)
     and opens it in this computer's browser; you sign in AS THE SENDER MAILBOX and click Allow
  2. Google sends the browser back to http://127.0.0.1:<port>/ on this computer (or, with --manual,
     you paste the address the browser ends up on: the page itself won't load, that's expected)
  3. checks the approving account is the sender mailbox and the scopes are exactly as above
  4. stores the OAuth client {client_id, client_secret} and the refresh token as new versions of the
     Secret Manager secrets $MAILER_CLIENT_SECRET and $MAILER_TOKEN_SECRET in $PROJECT_ID, through
     stdin to gcloud (never printed, never written to disk)
  5. optional --test-to: sends one plain test email (no student data) to that staff.example address

Needs: gcloud signed in with permission to add secret versions in $PROJECT_ID (infra/setup_materials.sh
created the secrets). Re-run it any time the approval is revoked (password change, 6 months unused).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
SCOPES = ["openid", "email", GMAIL_SEND]
ALLOWED_SCOPES = {"openid", "https://www.googleapis.com/auth/userinfo.email", "email", GMAIL_SEND}


def cfg(name: str, default: str) -> str:
    return os.environ.get(name) or default


def load_client(path: str) -> dict:
    raw = json.loads(Path(path).expanduser().read_text())
    c = raw.get("installed") or raw.get("web") or raw
    if not c.get("client_id") or not c.get("client_secret"):
        raise SystemExit("that file has no client_id / client_secret (download the OAuth client JSON)")
    if "installed" not in raw:
        print("note: this is not a 'Desktop app' client; the loopback redirect may be refused", file=sys.stderr)
    return {"client_id": c["client_id"], "client_secret": c["client_secret"]}


def auth_link(client: dict, redirect: str, sender: str) -> tuple[str, str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    q = {"client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
         "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent select_account",
         "login_hint": sender, "hd": sender.split("@")[1], "code_challenge": challenge,
         "code_challenge_method": "S256", "state": state}
    return f"{AUTH_URL}?{urllib.parse.urlencode(q)}", verifier, state


def wait_for_code(port: int, state: str) -> str:
    got: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            if q.get("state") == state and (q.get("code") or q.get("error")):
                got.update(q)
                msg = b"Done. You can close this tab and go back to the chat." if q.get("code") else b"Not approved."
            else:
                msg = b"Waiting for Google..."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(msg)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    t = threading.Thread(target=lambda: [srv.handle_request() for _ in iter(lambda: "code" in got or "error" in got, True)], daemon=True)
    t.start()
    t.join(timeout=600)
    srv.server_close()
    if got.get("error") or not got.get("code"):
        raise SystemExit(f"not approved ({got.get('error') or 'timed out'})")
    return got["code"]


def code_from_pasted(url: str, state: str) -> str:
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url.strip()).query))
    if q.get("state") != state or not q.get("code"):
        raise SystemExit("that address has no matching code (copy the whole address after clicking Allow)")
    return q["code"]


def exchange(client: dict, code: str, verifier: str, redirect: str) -> dict:
    data = urllib.parse.urlencode({"client_id": client["client_id"], "client_secret": client["client_secret"],
                                   "code": code, "code_verifier": verifier, "grant_type": "authorization_code",
                                   "redirect_uri": redirect}).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=data), timeout=20) as r:
        return json.loads(r.read())


def check(tokens: dict, sender: str) -> None:
    if not tokens.get("refresh_token"):
        raise SystemExit("Google returned no refresh token (re-run; the link asks for consent again)")
    scopes = set((tokens.get("scope") or "").split())
    if GMAIL_SEND not in scopes or scopes - ALLOWED_SCOPES:
        raise SystemExit(f"unexpected scopes approved: {sorted(scopes)}")
    payload = tokens.get("id_token", "").split(".")[1] if tokens.get("id_token") else ""
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))) if payload else {}
    if (claims.get("email") or "").lower() != sender.lower() or claims.get("email_verified") is not True:
        raise SystemExit(f"approved by {claims.get('email') or 'an unknown account'}, not {sender}; nothing stored")


def store(project: str, name: str, value: str) -> None:
    r = subprocess.run(["gcloud", "secrets", "versions", "add", name, "--data-file=-", f"--project={project}"],
                       input=value, text=True, capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"could not store {name}: {r.stderr.strip()[:300]}")


def test_send(client: dict, refresh: str, to: str) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/backend-pipeline"))
    from review_api import mailer
    vals = {cfg("MAILER_CLIENT_SECRET", "mailer-oauth-client"): json.dumps(client),
            cfg("MAILER_TOKEN_SECRET", "mailer-refresh-token"): refresh}
    g = mailer.GmailSender(read_secret=vals.get)
    msg = mailer.build_message(to=to, subject="Test: classroom materials email is set up",
                               body="This is a test from the review site's classroom materials email.\n"
                                    "No student information is in this message.\n",
                               pdf=b"%PDF-1.4\n%test\n", filename="test.pdf", from_name="Classroom materials")
    print("test email sent, Gmail id", g.send(msg))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-json", required=True, help="the OAuth client JSON downloaded from the Cloud console")
    ap.add_argument("--sender", default=cfg("MAILER_SENDER_MAILBOX", "snowwhite@staff.example"))
    ap.add_argument("--project", default=cfg("PROJECT_ID", "example-project"))
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--manual", action="store_true", help="paste the address the browser lands on")
    ap.add_argument("--no-browser", action="store_true", help="print the link, don't open a browser")
    ap.add_argument("--dry-run", action="store_true", help="check everything, store nothing")
    ap.add_argument("--test-to", help="send one test email to this staff.example address afterwards")
    a = ap.parse_args()
    client = load_client(a.client_json)
    redirect = f"http://127.0.0.1:{a.port}/"
    link, verifier, state = auth_link(client, redirect, a.sender)
    print(f"Sign in as {a.sender} and click Allow (asks only to send email):\n\n{link}\n", flush=True)
    if a.manual:
        code = code_from_pasted(input("Paste the full address the browser ended on: "), state)
    else:
        if not a.no_browser:
            webbrowser.open(link)
        code = wait_for_code(a.port, state)
    tokens = exchange(client, code, verifier, redirect)
    check(tokens, a.sender)
    print(f"approved by {a.sender}; scopes ok (gmail.send only)")
    if a.dry_run:
        print("dry run: nothing stored")
    else:
        store(a.project, cfg("MAILER_CLIENT_SECRET", "mailer-oauth-client"), json.dumps(client))
        store(a.project, cfg("MAILER_TOKEN_SECRET", "mailer-refresh-token"), tokens["refresh_token"])
        print("stored: OAuth client + refresh token in Secret Manager (new versions)")
    if a.test_to:
        test_send(client, tokens["refresh_token"], a.test_to)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
