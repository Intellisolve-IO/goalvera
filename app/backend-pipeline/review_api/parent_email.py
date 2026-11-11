"""Weekly parent email. A new outbound flow (under the school's data agreement
amendment list).

What is sent: the weekly report's parent email text exactly as the teacher approved it on the
student's page (planner/students/<sid>/<week>/summary/, status "approved"), with the student's first
name. Nothing else: no IEP check, no session notes, no last names, no ids (checked before sending).

Who it goes to (privacy rules):
  * Recipients are ONLY that student's guardians whose contact record has "receives reports" on
    (app/samples/contacts/demo_guardians.json, built from the fictional SIS sample; never "all
    contacts"). Contacts with the flag off (e.g. non-custodial parents) or without an email are listed
    as excluded. The teacher sees the recipients, the excluded contacts and the exact text first.
  * Manual only: one email per teacher click (POST .../parent_email/send with the preview's hash).
    There is no scheduled or automatic sending anywhere (tested).

Demo redirect (HARD RULE): a school-level setting, ON by default, admin-only, journaled. While it is
on, every parent email goes to the clicking teacher's own address on file (an admin's click: the
student's teacher's), never to the guardian, with a banner in the email ("Demo: would have gone to
<first name> at <masked address>") and in the UI; any recipient outside staff.example is refused.
With it off, the mailer's domain allowlist (MATERIALS_MAIL_DOMAINS, staff.example on the demo)
still applies, so no example.com / example.org address can be sent to either.

From notifications@staff.example through the existing Alex Morgan mailbox sender (mailer.py). Every
send is logged: a record under parent_email/sends/ (ids, recipient address hashes, status) and the
journal (parent_email_sent / parent_email_send_failed: counts and flags, never names or addresses).

  GET  /api/students/<sid>/parent_email?week=YYYY-MM-DD     preview (teachers of the student, admins)
  POST /api/students/<sid>/parent_email/send                {"week", "preview_sha256"}
  GET  /api/admin/parent_email                              redirect setting (admin)
  POST /api/admin/parent_email                              {"redirect": true|false} (admin)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import secrets
from pathlib import Path

from gemini_analyzer.prompt import reinsert_first_name

from common.roster import find_student, roster_students
from common.safelog import log

from . import mailer
from .bug_reports import _EMAIL, _LONG_NUMBER, _word
from .notes import ApiError, actor_id, check_actor, is_read_only, signed_in
from .store import Conflict

PREFIX = "parent_email/"
SETTINGS = f"{PREFIX}settings/redirect-"
REDIRECT_DOMAIN = os.environ.get("PARENT_EMAIL_REDIRECT_DOMAIN", "staff.example").lower()  # the only domain a redirected email may go to
_HERE = Path(__file__).resolve()
GUARDIANS_FILES = [_HERE.parents[2] / "samples" / "contacts" / "demo_guardians.json",   # repo: app/samples/contacts
                   _HERE.parents[1] / "samples" / "contacts" / "demo_guardians.json"]   # image: /srv/samples/contacts
_SID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def redirect_default() -> bool:
    return os.environ.get("PARENT_EMAIL_REDIRECT_DEFAULT", "on").strip().lower() not in ("off", "false", "0")


def mask_addr(a: str | None) -> str | None:
    if not a or "@" not in a:
        return None
    local, dom = a.split("@", 1)
    return f"{local[:1]}{'*' * max(3, len(local) - 1)}@{dom}"


def must_be_internal(addr: str) -> str:
    """Redirect on: the ONLY check that decides where a parent email may go. staff.example only."""
    a = mailer.check_recipient(addr)
    if a.rsplit("@", 1)[1].lower() != REDIRECT_DOMAIN:
        raise mailer.MailRefused("REDIRECT_NOT_INTERNAL")
    return a


def build_text_message(*, to: str, subject: str, body: str, from_name: str):
    """A plain-text email (no attachment), From pinned to mailer.MAIL_FROM."""
    from email.message import EmailMessage
    from email.utils import formataddr, make_msgid
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, mailer.MAIL_FROM))
    msg["To"] = mailer.check_recipient(to)
    msg["Reply-To"] = mailer.MAIL_FROM
    msg["Subject"] = subject.replace("\r", " ").replace("\n", " ")[:160]
    msg["Message-ID"] = make_msgid(domain=mailer.MAIL_FROM.split("@")[1])
    msg.set_content(body)
    return msg


class ParentEmail:
    def __init__(self, notes, planner, *, sender=None, guardians=None, now=None):
        self.notes, self.planner = notes, planner
        self.sender = sender
        self._guardians = guardians
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))

    @property
    def rec(self):
        return self.notes.recorder

    # ------------------------------------------------------------ contacts
    def guardians(self) -> dict:
        if self._guardians is None:
            self._guardians = {}
            for f in GUARDIANS_FILES:
                try:
                    self._guardians = json.loads(f.read_text(encoding="utf-8")).get("students") or {}
                    break
                except (OSError, ValueError):
                    continue
        return self._guardians

    def contacts(self, sid: str) -> list[dict]:
        out = []
        for c in self.guardians().get(sid) or []:
            email = (c.get("email") or "").strip() or None
            out.append({"contact_id": c.get("contact_id"), "first_name": (c.get("first_name") or "").strip(),
                        "relationship": c.get("relationship") or "", "email": email,
                        "receives_reports": bool(c.get("receives_reports")) and bool(email)})
        return out

    # ------------------------------------------------------------ redirect setting (admin)
    def redirect_on(self) -> bool:
        try:
            names = sorted(n for n in self.notes.store.list(SETTINGS) if n.startswith(SETTINGS))
        except Exception:  # noqa: BLE001 - can't read the setting: the safe value
            return True
        if not names:
            return redirect_default()
        v = (self.notes.store.read_json(names[-1]) or {}).get("redirect")
        return v is not False                    # anything but an explicit off is on

    def _admin(self, actor):
        actor = check_actor(actor)
        if not (signed_in(actor) and actor.is_admin):
            raise ApiError(403, "ADMIN_ONLY")
        return actor

    def setting(self, actor) -> dict:
        self._admin(actor)
        return {"redirect": self.redirect_on(), "default": redirect_default(), "redirect_domain": REDIRECT_DOMAIN,
                "sender": mailer.MAIL_FROM, "mail_mode": mailer.mode(),
                "mail_state": getattr(self.sender, "state", "off") if self.sender is not None else "off"}

    def set_redirect(self, actor, body: dict) -> dict:
        actor = self._admin(actor)
        v = (body or {}).get("redirect")
        if not isinstance(v, bool):
            raise ApiError(400, "BAD_SETTING")
        now = self._now()
        self.notes.store.write_json(f"{SETTINGS}{now.strftime('%Y%m%dT%H%M%S%f')}-{secrets.token_hex(3)}.json",
                                    {"redirect": v, "by": actor_id(actor), "at": now.isoformat(timespec="seconds")},
                                    create_only=True)
        self.rec.record(actor, "settings_changed", record_id="parent_email_redirect", record_type="settings",
                        required=True, setting="parent_email_redirect", enabled=v)
        return self.setting(actor)

    # ------------------------------------------------------------ preview
    def _teacher_address(self, actor, sid: str) -> tuple[str | None, str | None]:
        """The clicking teacher's own address on file; an admin's click: the student's teacher's."""
        teachers = self.notes.roster().get("teachers") or {}
        owners = sorted(self.notes.student_teacher_ids(sid))
        if signed_in(actor):
            ids = owners if actor.is_admin else ([t for t in sorted(actor.teacher_ids) if t in owners]
                                                 + [t for t in sorted(actor.teacher_ids) if t not in owners])
        else:
            ids = [actor]
        for tid in ids:
            emails = [e for e in ((teachers.get(tid) or {}).get("emails") or []) if isinstance(e, str) and e.strip()]
            if emails:
                return emails[0].strip(), tid
        return None, None

    def _approved(self, sid: str, wk: dt.date, fn: str) -> dict | None:
        doc, _ = self.planner._latest(f"planner/students/{sid}/{wk.isoformat()}/summary/")
        if not doc or doc.get("status") != "approved" or not (doc.get("parent_email") or "").strip():
            return None
        return reinsert_first_name(doc, fn)

    def check_text(self, subject: str, body: str) -> None:
        """No addresses, ids, last names, IEP goal text or long numbers in the approved text."""
        blob = f"{subject}\n{body}"
        if _EMAIL.search(blob):
            raise mailer.MailRefused("EMAIL_HAS_ADDRESS")
        if _LONG_NUMBER.search(blob):
            raise mailer.MailRefused("EMAIL_HAS_NUMBER")
        for sid, st in roster_students(self.notes.roster()).items():
            if sid in blob:
                raise mailer.MailRefused("EMAIL_HAS_ID")
            last = (st.get("last_name") or "").strip()
            if last and _word(last).search(blob):
                raise mailer.MailRefused("EMAIL_HAS_LAST_NAME")
            for g in st.get("iep_goals") or []:
                desc = " ".join(str((g or {}).get("description") or "").split())
                if len(desc) >= 20 and desc[:40] in blob:
                    raise mailer.MailRefused("EMAIL_HAS_IEP")
        for cs in self.guardians().values():
            for c in cs:
                if c.get("email") and c["email"].split("@")[0] in blob.lower():
                    raise mailer.MailRefused("EMAIL_HAS_ADDRESS")

    def preview(self, actor, sid, week) -> dict:
        actor = check_actor(actor)
        if not isinstance(sid, str) or not _SID.match(sid):
            raise ApiError(400, "BAD_STUDENT")
        st, _ = find_student(self.notes.roster(), sid)
        if st is None:
            raise ApiError(404, "UNKNOWN_STUDENT")
        if not self.planner.may_edit_student(actor, sid):
            raise ApiError(403, "NOT_YOUR_STUDENT")
        wk = self.planner._check_week(week)
        fn = st["first_name"]
        contacts = self.contacts(sid)
        to_guardians = [c for c in contacts if c["receives_reports"]]
        excluded = [{"first_name": c["first_name"], "relationship": c["relationship"],
                     "reason": "no email on file" if not c["email"] else "not set to receive reports"}
                    for c in contacts if not c["receives_reports"]]
        redirect = self.redirect_on()
        teacher_to, _ = self._teacher_address(actor, sid)
        doc = self._approved(sid, wk, fn)
        problem = None
        if doc is None:
            problem = "NOT_APPROVED"
        elif not to_guardians:
            problem = "NO_RECIPIENTS"
        elif redirect and not teacher_to:
            problem = "NO_STAFF_EMAIL"
        subject = f"{fn}'s week of {wk.strftime('%B')} {wk.day}"
        body = (doc or {}).get("parent_email") or ""
        if doc is not None:
            try:
                self.check_text(subject, body)
            except mailer.MailRefused as exc:
                problem = exc.code
        emails = []
        for c in to_guardians:
            banner = (f"Demo: would have gone to {c['first_name']} at {mask_addr(c['email'])}"
                      if redirect else None)
            emails.append({"contact_id": c["contact_id"], "guardian_first_name": c["first_name"],
                           "relationship": c["relationship"], "guardian_email_masked": mask_addr(c["email"]),
                           "to": teacher_to if redirect else c["email"], "banner": banner,
                           "subject": subject, "body": (f"{banner}\n\n{body}" if banner else body)})
        core = {"week": wk.isoformat(), "emails": [{k: e[k] for k in ("contact_id", "to", "subject", "body")} for e in emails]}
        return {"student_id": sid, "week": wk.isoformat(), "first_name": fn,
                "redirect": redirect, "redirect_to": teacher_to if redirect else None,
                "redirect_banner": f"Demo mode: parent emails go to {teacher_to or 'your own address'}, not to parents."
                if redirect else None,
                "from": mailer.MAIL_FROM, "approved_at": (doc or {}).get("approved_at"),
                "contacts": contacts, "emails": emails, "excluded": excluded,
                "can_send": problem is None and self.sender is not None, "problem": problem or (None if self.sender else "EMAIL_OFF"),
                "manual_only": True, "last_sent": self.last_sent(sid, wk),
                "preview_sha256": hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()}

    def last_sent(self, sid: str, wk: dt.date) -> dict | None:
        try:
            names = sorted(self.notes.store.list(f"{PREFIX}sends/{sid}/{wk.isoformat()}/"))
        except Exception:  # noqa: BLE001
            return None
        for n in reversed(names):
            r = self.notes.store.read_json(n) or {}
            if r.get("status") == "sent":
                return {"at": r.get("at"), "recipients": r.get("recipients"), "redirected": r.get("redirected")}
        return None

    # ------------------------------------------------------------ send (one teacher click)
    def send(self, actor, sid, body: dict) -> dict:
        actor = check_actor(actor)
        if is_read_only(actor):
            raise ApiError(403, "READ_ONLY_ROLE")
        p = self.preview(actor, sid, (body or {}).get("week"))
        if (body or {}).get("preview_sha256") != p["preview_sha256"]:
            raise ApiError(409, "PREVIEW_CHANGED")      # send exactly what the teacher was shown
        if p["problem"]:
            raise ApiError(409, p["problem"])
        redirect = p["redirect"]
        status, sent, ids = "sent", 0, []
        try:
            for e in p["emails"]:
                to = must_be_internal(e["to"]) if redirect else mailer.check_recipient(e["to"])
                if redirect and to != p["redirect_to"]:
                    raise mailer.MailRefused("REDIRECT_MISMATCH")
                msg = build_text_message(to=to, subject=e["subject"], body=e["body"], from_name=self._from_name())
                ids.append(self.sender.send(msg))
                sent += 1
        except mailer.MailNotReady as exc:
            status = exc.code.lower()
        except mailer.MailRefused as exc:
            status = exc.code.lower()
            log("parent_email_refused", "ERROR", step="parent_email", error_code=exc.code)
        except Exception as exc:  # noqa: BLE001 - never echo (could carry content)
            status = "send_failed"
            log("parent_email_failed", "ERROR", step="parent_email", error_type=type(exc).__name__)
        if status != "sent" and sent:
            status = f"partial_{status}"
        now = self._now()
        rec = {"student_id": sid, "week": p["week"], "status": status, "at": now.isoformat(timespec="seconds"),
               "by": actor_id(actor), "redirected": redirect, "recipients": sent, "planned": len(p["emails"]),
               "contact_ids": [e["contact_id"] for e in p["emails"]],
               "to_sha256": sorted({hashlib.sha256(e["to"].lower().encode()).hexdigest() for e in p["emails"]}),
               "body_sha256": hashlib.sha256("\n".join(e["body"] for e in p["emails"]).encode()).hexdigest(),
               "gmail_ids": [i for i in ids if i], "from": mailer.MAIL_FROM}
        try:
            self.notes.store.write_json(f"{PREFIX}sends/{sid}/{p['week']}/{now.strftime('%Y%m%dT%H%M%S%f')}-"
                                        f"{secrets.token_hex(3)}.json", rec, create_only=True)
        except Conflict:
            pass
        self.rec.record(actor, "parent_email_sent" if status == "sent" else "parent_email_send_failed",
                        student_id=sid, record_id=sid, week=p["week"], recipients=sent,
                        redirected=redirect, excluded=len(p["excluded"]), email_status=status)
        return {"status": status, "sent": sent, "redirected": redirect,
                "to": mask_addr(p["redirect_to"]) if redirect else None, "last_sent": self.last_sent(sid, dt.date.fromisoformat(p["week"]))}

    def _from_name(self) -> str:
        try:
            from .branding import Branding
            return (Branding(self.notes.store, self.rec).view().get("school_name") or "Weekly report").strip()
        except Exception:  # noqa: BLE001
            return "Weekly report"
