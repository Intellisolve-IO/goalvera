"""Outgoing email for classroom materials (tasks#99): the app's first and only outgoing email.

How it sends (Chief of Staff / Ops, Oct 10 2026): the Gmail API with a single-mailbox OAuth
consent. The sender mailbox (demo: Alex Morgan's own Workspace user, snowwhite@staff.example)
approved one scope once, https://www.googleapis.com/auth/gmail.send (send only, no reading). The
OAuth client and the refresh token are two Secret Manager secrets in this project, readable only by
the review service account:
    MAILER_CLIENT_SECRET  (default mailer-oauth-client)  {"client_id", "client_secret"}
    MAILER_TOKEN_SECRET   (default mailer-refresh-token) the refresh token
infra/setup_materials.sh creates them; infra/mailer_consent.py fills them in (one sign-in as
the sender mailbox). No domain-wide delegation, nothing in anyone else's mailbox.

Pinned: From is always MAIL_FROM (notifications@staff.example, an alias of the sender mailbox).
The recipient is decided by materials.py (the teacher's own staff address on file) and checked
again here against MATERIALS_MAIL_DOMAINS. Gmail keeps a copy in the sender's Sent folder.

MATERIALS_MAIL = live | outbox | off. live: the above (Cloud Run, once the secrets exist).
outbox: nothing leaves the machine; messages are kept in memory (tests, laptop demo). off: no email.
Default: live on Cloud Run, outbox elsewhere.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

SCOPE = "https://www.googleapis.com/auth/gmail.send"
MAIL_FROM = os.environ.get("MAILER_FROM", "notifications@staff.example")
TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
MAX_BYTES = 8 * 1024 * 1024


class MailNotReady(Exception):
    """Email delivery isn't set up (no secrets yet) or needs re-approval (token revoked)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class MailRefused(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def mode() -> str:
    m = os.environ.get("MATERIALS_MAIL")
    if m in ("live", "outbox", "off"):
        return m
    return "live" if os.environ.get("K_SERVICE") else "outbox"


def allowed_domains() -> set[str]:
    raw = os.environ.get("MATERIALS_MAIL_DOMAINS", "staff.example")
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def check_recipient(addr: str) -> str:
    a = (addr or "").strip()
    if a.count("@") != 1 or any(ch in a for ch in " ,;<>\r\n\"'"):
        raise MailRefused("BAD_RECIPIENT")
    if a.rsplit("@", 1)[1].lower() not in allowed_domains():
        raise MailRefused("RECIPIENT_DOMAIN_NOT_ALLOWED")
    return a


def build_message(*, to: str, subject: str, body: str, pdf: bytes, filename: str, from_name: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, MAIL_FROM))
    msg["To"] = check_recipient(to)
    msg["Reply-To"] = MAIL_FROM
    msg["Subject"] = subject.replace("\r", " ").replace("\n", " ")[:160]
    msg["Message-ID"] = make_msgid(domain=MAIL_FROM.split("@")[1])
    msg.set_content(body)
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=filename)
    return msg


def _secret(project: str, name: str) -> str | None:
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    r = AuthorizedSession(creds).get(
        f"https://secretmanager.googleapis.com/v1/projects/{project}/secrets/{name}/versions/latest:access", timeout=10)
    if r.status_code in (400, 403, 404):
        return None
    r.raise_for_status()
    return base64.b64decode(r.json()["payload"]["data"]).decode().strip() or None


class GmailSender:
    """Refresh token -> access token (cached until shortly before it expires) -> users.messages.send."""

    def __init__(self, *, project: str | None = None, read_secret=None, http=None, now=time.time):
        self.project = project or os.environ.get("PROJECT_ID", "example-project")
        self.client_secret = os.environ.get("MAILER_CLIENT_SECRET", "mailer-oauth-client")
        self.token_secret = os.environ.get("MAILER_TOKEN_SECRET", "mailer-refresh-token")
        self._read = read_secret or (lambda name: _secret(self.project, name))
        self._http = http
        self._now = now
        self._access, self._exp, self._lock = None, 0.0, threading.Lock()
        self.state = "not checked"

    def _post(self, url: str, **kw):
        if self._http is not None:
            return self._http(url, **kw)
        import requests
        return requests.post(url, timeout=20, **kw)

    def _token(self) -> str:
        with self._lock:
            if self._access and self._now() < self._exp - 60:
                return self._access
            try:
                client = json.loads(self._read(self.client_secret) or "null")
                refresh = self._read(self.token_secret)
            except Exception as exc:  # noqa: BLE001 - never echo (could carry a secret)
                self.state = f"error ({type(exc).__name__})"
                raise MailNotReady("MAIL_NOT_SET_UP") from exc
            if not isinstance(client, dict) or not client.get("client_id") or not refresh:
                self.state = "not set up"
                raise MailNotReady("MAIL_NOT_SET_UP")
            r = self._post(TOKEN_URL, data={"client_id": client["client_id"], "client_secret": client.get("client_secret", ""),
                                            "refresh_token": refresh, "grant_type": "refresh_token"})
            if r.status_code == 400 or r.status_code == 401:
                self.state = "needs re-approval"
                raise MailNotReady("MAIL_NEEDS_REAPPROVAL")       # revoked / password changed / unused 6 months
            r.raise_for_status()
            j = r.json()
            scopes = set((j.get("scope") or SCOPE).split())
            if SCOPE not in scopes:
                self.state = "wrong scope"
                raise MailNotReady("MAIL_NEEDS_REAPPROVAL")
            self._access, self._exp = j["access_token"], self._now() + float(j.get("expires_in") or 3000)
            self.state = "ready"
            return self._access

    def send(self, msg: EmailMessage) -> str:
        raw = msg.as_bytes()
        if len(raw) > MAX_BYTES:
            raise MailRefused("MAIL_TOO_LARGE")
        token = self._token()
        r = self._post(SEND_URL, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                       data=json.dumps({"raw": base64.urlsafe_b64encode(raw).decode()}))
        if r.status_code == 401:
            with self._lock:
                self._access = None
            raise MailNotReady("MAIL_NEEDS_REAPPROVAL")
        r.raise_for_status()
        return str(r.json().get("id") or "")


class Outbox:
    """Nothing leaves the machine (tests, laptop demo)."""

    def __init__(self):
        self.sent: list[EmailMessage] = []
        self.state = "outbox"

    def send(self, msg: EmailMessage) -> str:
        self.sent.append(msg)
        return f"outbox-{len(self.sent)}"


def sender_from_env():
    m = mode()
    if m == "live":
        return GmailSender()
    if m == "outbox":
        return Outbox()
    return None
