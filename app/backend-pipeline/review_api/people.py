"""My profile and admin access changes.

My profile: the signed-in user (teacher, RBT or admin) opens it from their name at the top right and
edits their own profile: display name, preferred name, phone, default classroom (one of the classrooms
they may use: a teacher's own, an RBT's roster classrooms, any for an admin), title and credential type.
Role and sign-in email are shown read-only ("Ask an admin to change this") unless the single flag
PROFILE_SELF_EDIT_ACCESS=on (default off) lets people change their own role / email too.

Admins change anyone's app role (admin / teacher / RBT / no access) and sign-in email from the Teachers
tab. The email is the sign-in key, so the allowlist entry moves with it: the old address loses access
and the new one gets the same role, in the SAME record as the staff record's email change (one object,
so nobody is ever half moved). An admin can't remove the last admin.

Storage. The roster (config/demo_roster.json) is replaced on every deploy and the base allowlist
(config/review_allowlist.json) is managed with infra/review_allowlist.py, so neither is edited here.
Every change writes ONE new create-only record with the whole state:
  people/state-<8-digit number>.json
    {"version": n, "at", "by": uid, "change": {"kind": "profile"|"access", "person", "fields"},
     "profiles": {person: {display_name, preferred_name, phone, default_classroom, title, credential_type}},
     "emails":   {roster person id: [sign-in emails]},      # replaces the roster's emails for that person
     "accounts": {"acct_<hex>": email},                    # people on the allowlist only (not in the roster)
     "access":   {email: role | null}}                     # over the base allowlist; null = no access
The highest number is current. Record n+1 is created only if nobody created it first (create-only), so
two saves can't both win; the loser re-reads and re-applies its change (field-level, re-checked).
review-web may read, list and create under people/, never overwrite or delete
(infra/setup_people.sh). The allowlist (auth.Allowlist), the email -> person mapping (auth.Roster) and
the roster the app reads (Notes.roster) all apply the same snapshot.

Journal: profile_updated (person id, changed field names; never values) and access_changed (Ops,
): who made it (actor), the person id, the account's sign-in address (account_email),
"role" / "email", old_role and new_role (admin / teacher / rbt / none), and when the sign-in email
changes both addresses (old_email, new_email). The same old / new values go into the people/ record's
"change". Addresses are staff sign-in emails, and only access_changed may carry them (journal.py).
Names: a display / preferred name is a name, so it joins the never-to-the-AI lists like the roster
names (bug_reports.never_values, planner masks).
"""
from __future__ import annotations

import copy
import datetime as dt
import os
import re
import secrets
import threading
import time
import unicodedata

from common.classcodes import class_key
from common.roster import rbt_records
from common.safelog import log

from .notes import ApiError

PREFIX = "people/"
_STATE = re.compile(r"^people/state-(\d{8})\.json$")
TTL = 30.0                                        # same as the allowlist: other instances catch up in 30 s
SELF_FIELDS = ("display_name", "preferred_name", "phone", "default_classroom", "title", "credential_type")
SENSITIVE = frozenset({"phone", "email"})         # never a value in a log or the journal (none are anyway)
MAX = {"display_name": 80, "preferred_name": 40, "title": 80, "credential_type": 60}
CREDENTIAL_TYPES = ("", "Teaching certificate", "Special education certificate", "BCBA", "BCaBA", "RBT",
                    "SLP (CCC-SLP)", "OT", "PT", "School counselor", "Other")
APP_ROLES = ("admin", "teacher", "rbt")          # + "none" = no access
_PHONE = re.compile(r"^\+?[0-9 ().\-]{7,20}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}$")
_ACCT = re.compile(r"^acct_[0-9a-f]{12}$")
_PERSON = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def self_edit_access() -> bool:
    """The one switch for letting people change their own role / email (default off)."""
    return os.environ.get("PROFILE_SELF_EDIT_ACCESS", "off").strip().lower() in ("on", "1", "true", "yes")


def norm_email(e) -> str | None:
    if not isinstance(e, str):
        return None
    e = e.strip().lower()
    return e if _EMAIL.match(e) and len(e) <= 254 else None


def empty_state() -> dict:
    return {"version": 0, "profiles": {}, "emails": {}, "accounts": {}, "access": {}}


def _text(v, field: str, limit: int) -> str:
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise ApiError(400, f"BAD_{field.upper()}")
    v = unicodedata.normalize("NFC", v)
    v = "".join(" " if unicodedata.category(c).startswith(("C", "Z")) else c for c in v)
    v = re.sub(r"\s+", " ", v).strip()
    if len(v) > limit or any(c in v for c in "<>{}@"):
        raise ApiError(400, f"BAD_{field.upper()}")
    return v


# ---------------------------------------------------------------- applying a snapshot

def roster_people(roster: dict) -> dict:
    """roster person id -> its record (teachers, staff incl. RBTs, older rbts)."""
    r = roster or {}
    out = {k: v for k, v in (r.get("teachers") or {}).items() if isinstance(v, dict)}
    out.update(rbt_records(r))
    out.update({k: v for k, v in (r.get("staff") or {}).items() if isinstance(v, dict)})
    return out


def apply_roster(roster: dict, state: dict) -> dict:
    """The roster with the saved profiles / sign-in emails on the people's own records."""
    if not state or not (state.get("profiles") or state.get("emails")):
        return roster
    r = copy.deepcopy(roster or {})
    recs = roster_people(r)
    for pid, emails in (state.get("emails") or {}).items():
        if pid in recs and isinstance(emails, list):
            recs[pid]["emails"] = [e for e in emails if isinstance(e, str)]
    for pid, p in (state.get("profiles") or {}).items():
        rec = recs.get(pid)
        if rec is None or not isinstance(p, dict):
            continue
        for f in ("display_name", "preferred_name", "phone", "title", "credential_type"):
            if p.get(f):
                rec[f] = p[f]
        if p.get("default_classroom"):
            rec["default_class_code"] = p["default_classroom"]
    return r


def apply_access(users: dict, state: dict) -> dict:
    """{email: role} from the base allowlist, with the saved changes (None = removed)."""
    out = dict(users)
    for e, role in ((state or {}).get("access") or {}).items():
        if role is None:
            out.pop(e, None)
        elif role in APP_ROLES or role == "reviewer":
            out[e] = role
    return out


def saved_profile(store, roster: dict, email: str | None) -> dict:
    """The saved My profile of whoever signs in with this email ({} if none)."""
    st = state_for(store).get()
    email = (email or "").lower()
    pid = None
    for k, rec in roster_people(roster).items():
        if email and email in [e.lower() for e in rec.get("emails") or [] if isinstance(e, str)]:
            pid = k
            break
    if pid is None:
        pid = next((a for a, e in (st.get("accounts") or {}).items() if (e or "").lower() == email), None)
    return dict((st.get("profiles") or {}).get(pid) or {}) if pid else {}


class PeopleState:
    """The newest people/ record, cached for TTL seconds (one snapshot shared by the allowlist, the
    roster mapping and the app's roster, so they never disagree)."""

    def __init__(self, store, ttl: float = TTL, now=time.monotonic):
        self.store, self.ttl, self.now = store, ttl, now
        self._state, self._at, self._lock = None, None, threading.Lock()

    def _read(self) -> dict:
        if not hasattr(self.store, "list"):              # read-only test doubles: nothing saved
            return empty_state()
        try:
            names = self.store.list(f"{PREFIX}state-")
        except Exception as exc:  # noqa: BLE001 - no bucket access to people/ yet: nothing saved shows
            if type(exc).__name__ in ("Forbidden", "PermissionError", "PermissionDenied"):
                log("people_unreadable", "WARNING", step="people", error_type=type(exc).__name__)
                return empty_state()
            raise
        nums = sorted((int(m.group(1)), n) for n in names if (m := _STATE.match(n)))
        for _, n in reversed(nums):
            doc = self.store.read_json(n)
            if isinstance(doc, dict):
                st = empty_state()
                st.update({k: doc.get(k) or st[k] for k in ("profiles", "emails", "accounts", "access")})
                st["version"] = int(doc.get("version") or 0)
                return st
        return empty_state()

    def get(self) -> dict:
        with self._lock:
            if self._at is None or self.now() - self._at >= self.ttl:
                self._state, self._at = self._read(), self.now()
            return self._state

    def refresh(self) -> dict:
        with self._lock:
            self._at = None
        return self.get()

    def write(self, state: dict) -> None:
        """Create record version+1; Conflict if someone else made it first."""
        n = int(state["version"])
        if not 0 < n < 10 ** 8:
            raise ApiError(500, "BAD_VERSION")
        self.store.write_json(f"{PREFIX}state-{n:08d}.json", state, create_only=True)
        with self._lock:
            self._state, self._at = state, self.now()


_BY_STORE: dict = {}
_BY_STORE_LOCK = threading.Lock()


def state_for(store) -> PeopleState:
    """One snapshot cache per store object (the server, auth and notes share the store)."""
    with _BY_STORE_LOCK:
        ps = _BY_STORE.get(id(store))
        if ps is None or ps.store is not store:
            ps = _BY_STORE[id(store)] = PeopleState(store)
        return ps


# ---------------------------------------------------------------- the service

class People:
    def __init__(self, notes, *, now=None):
        self.notes = notes
        self.cache = state_for(notes.store)
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))

    @property
    def rec(self):
        return self.notes.recorder

    # ------------------------------------------------------------ lookups
    def _base_allowlist(self) -> dict:
        from .auth import ALLOWLIST_OBJECT, normalize_email
        users = {}
        for u in (self.notes.store.read_json(ALLOWLIST_OBJECT) or {}).get("users") or []:
            e = normalize_email((u or {}).get("email")) if isinstance(u, dict) else None
            if e:
                users[e] = u.get("role") if u.get("role") in ("admin", "teacher", "reviewer", "rbt") else "reviewer"
        return users

    def allowlist(self, state: dict | None = None) -> dict:
        return apply_access(self._base_allowlist(), state if state is not None else self.cache.get())

    def _person_of(self, email: str, state: dict, roster: dict) -> str | None:
        email = (email or "").lower()
        for pid, rec in roster_people(roster).items():
            if email in [e.lower() for e in rec.get("emails") or [] if isinstance(e, str)]:
                return pid
        for aid, e in (state.get("accounts") or {}).items():
            if (e or "").lower() == email:
                return aid
        return None

    def _emails_of(self, pid: str, state: dict, roster: dict) -> list[str]:
        if pid in (state.get("accounts") or {}):
            return [state["accounts"][pid]]
        rec = roster_people(roster).get(pid) or {}
        return [e.lower() for e in rec.get("emails") or [] if isinstance(e, str)]

    def allowed_classrooms(self, actor) -> list[str]:
        r = self.notes.roster()
        codes = r.get("class_codes") or {}
        if actor.is_admin:
            return sorted(codes)
        if actor.role == "rbt":
            return sorted(d for d in codes if class_key(d) in actor.class_keys)
        return sorted(d for d, e in codes.items() if isinstance(e, dict) and e.get("teacher_id") in actor.teacher_ids)

    @staticmethod
    def _role_label(role: str | None) -> str:
        return {"admin": "Admin", "rbt": "RBT (read-only)", "teacher": "Teacher", "reviewer": "Teacher"}.get(role or "", "No access")

    # ------------------------------------------------------------ My profile
    def me(self, actor) -> dict:
        self._signed_in(actor)
        state = self.cache.get()
        r = self.notes.roster()                          # already has the saved profile applied
        pid = self._person_of(actor.email, state, r)
        rec = roster_people(r).get(pid) if pid else None
        saved = (state.get("profiles") or {}).get(pid) or {} if pid else {}
        name = (rec or {}).get("teacher_name") or (rec or {}).get("name")
        allowed = self.allowed_classrooms(actor)
        default = saved.get("default_classroom") or self.notes.default_classroom(actor)
        flag = self_edit_access()
        return {
            "person_id": pid, "in_roster": rec is not None,
            "roster_name": name,
            "display_name": saved.get("display_name") or name or "",
            "preferred_name": saved.get("preferred_name") or "",
            "phone": saved.get("phone") or "",
            "title": saved.get("title") or (rec or {}).get("title") or "",
            "credential_type": saved.get("credential_type") or "",
            "default_classroom": default if default in allowed else None,
            "classrooms": allowed,
            "email": actor.email, "role": actor.role, "role_label": self._role_label(actor.role),
            "can_edit_access": flag,
            "credential_types": list(CREDENTIAL_TYPES),
            "version": state.get("version", 0),
            "sample_label": "Fictional sample data",
        }

    def _clean_profile(self, actor, body: dict) -> dict:
        out = {}
        for f in SELF_FIELDS:
            if f not in body:
                continue
            v = body.get(f)
            if f == "phone":
                v = _text(v, "phone", 20)
                if v and not _PHONE.match(v):
                    raise ApiError(400, "BAD_PHONE")
            elif f == "default_classroom":
                if v in (None, ""):
                    v = ""
                elif not isinstance(v, str) or v not in self.allowed_classrooms(actor):
                    raise ApiError(400, "CLASSROOM_NOT_ALLOWED")
            elif f == "credential_type":
                v = _text(v, f, MAX[f])
                if v not in CREDENTIAL_TYPES:
                    raise ApiError(400, "BAD_CREDENTIAL_TYPE")
            else:
                v = _text(v, f, MAX[f])
            out[f] = v
        return out

    def save_me(self, actor, body: dict) -> dict:
        self._signed_in(actor)
        if not isinstance(body, dict):
            raise ApiError(400, "BAD_BODY")
        if ("role" in body or "email" in body) and not self_edit_access():
            raise ApiError(403, "ASK_AN_ADMIN")
        clean = self._clean_profile(actor, body)
        changed: list[str] = []

        def change(state, r):
            pid = self._person_of(actor.email, state, r)
            if pid is None:                              # an allowlist-only person's first save
                pid = f"acct_{secrets.token_hex(6)}"
                state["accounts"][pid] = actor.email
            cur = dict(state["profiles"].get(pid) or {})
            changed.clear()
            for f, v in clean.items():
                if (cur.get(f) or "") != v:
                    changed.append(f)
                    if v:
                        cur[f] = v
                    else:
                        cur.pop(f, None)
            state["profiles"][pid] = cur
            return pid, list(changed)

        pid = self._commit(actor, "profile", change)
        if changed:
            self.rec.record(actor, "profile_updated", record_id=pid, required=True, fields=".".join(changed))
        access = {}
        if self_edit_access() and ("role" in body or "email" in body):
            access = self.change_access(actor, pid, body, self_service=True)
        return {**self.me(actor), "changed": changed + access.get("changed", [])}

    # ------------------------------------------------------------ admin: role / email
    def change_access(self, actor, person: str, body: dict, *, self_service: bool = False) -> dict:
        self._signed_in(actor)
        if not (actor.is_admin or (self_service and self_edit_access())):
            raise ApiError(403, "ADMIN_ONLY")
        if not isinstance(body, dict):
            raise ApiError(400, "BAD_BODY")
        role = body.get("role")
        if role is not None and role not in (*APP_ROLES, "none"):
            raise ApiError(400, "BAD_ROLE")
        new_email = None
        if body.get("email") not in (None, ""):
            new_email = norm_email(body.get("email"))
            if not new_email:
                raise ApiError(400, "BAD_EMAIL")
        old_hint = norm_email(body.get("current_email")) if body.get("current_email") else None
        if person in (None, "") and old_hint:
            person = None                                # an allowlist-only address, no profile yet
        elif not isinstance(person, str) or not _PERSON.match(person):
            raise ApiError(400, "BAD_PERSON")
        result: dict = {}

        def change(state, r):
            allow = self.allowlist(state)
            pid = person
            if pid is None:
                pid = self._person_of(old_hint, state, r)
                if pid is None:
                    if old_hint not in allow:
                        raise ApiError(404, "NOT_FOUND")
                    pid = f"acct_{secrets.token_hex(6)}"
                    state["accounts"][pid] = old_hint
            elif pid not in roster_people(r) and pid not in (state.get("accounts") or {}):
                raise ApiError(404, "NOT_FOUND")
            emails = self._emails_of(pid, state, r)
            old = old_hint if old_hint in emails else (emails[0] if emails else None)
            cur_role = allow.get(old) if old else None
            cur_role = "teacher" if cur_role == "reviewer" else cur_role
            want_role = cur_role if role is None else (None if role == "none" else role)
            email_to = new_email if (new_email and new_email != old) else None
            fields = []
            if email_to:
                owner = self._person_of(email_to, state, r)
                if (owner and owner != pid) or email_to in allow:
                    raise ApiError(409, "EMAIL_IN_USE")
                fields.append("email")
            if want_role != cur_role:
                if want_role is not None and not (old or email_to):
                    raise ApiError(400, "EMAIL_REQUIRED")
                fields.append("role")
            if not fields:
                return pid, []
            # the allowlist after this change: the entry moves with the email, the role goes with it
            entries: dict = {}
            if email_to:
                if old:
                    entries[old] = None
                entries[email_to] = want_role
            elif old:
                entries[old] = want_role
            if not any(ro == "admin" for ro in apply_access(allow, {"access": entries}).values()):
                raise ApiError(409, "LAST_ADMIN")       # never leave the app without an admin
            # one record: the allowlist entries and the person's sign-in email move together
            state["access"].update(entries)
            if email_to:
                if pid in (state.get("accounts") or {}):
                    state["accounts"][pid] = email_to
                else:
                    state["emails"][pid] = [email_to if e == old else e for e in emails] if old else [*emails, email_to]
            log_vals = {"old_role": cur_role or "none", "new_role": want_role or "none",
                        "account_email": old or email_to}
            if email_to:
                log_vals.update(old_email=old, new_email=email_to)
            result.update(fields=fields, role=want_role or "none", person=pid, log=log_vals)
            return pid, fields

        self._commit(actor, "access", change, extra=lambda: result.get("log") or {})
        fields = result.get("fields") or []
        pid = result.get("person") or person
        if fields:
            self.rec.record(actor, "access_changed", record_id=pid, required=True, fields=".".join(fields),
                            app_role=result.get("role"), **(result.get("log") or {}))
        return {"changed": fields, "person": pid}

    # ------------------------------------------------------------ the write
    def _commit(self, actor, kind: str, change, tries: int = 4, extra=None):
        from .notes import actor_id
        from .store import Conflict
        for attempt in range(tries):
            base = copy.deepcopy(self.cache.refresh() if attempt else self.cache.get())
            r = apply_roster(self.notes.roster_base(), base)
            pid, fields = change(base, r)
            if not fields:
                return pid
            base["version"] = int(base.get("version") or 0) + 1
            base["at"] = self._now().isoformat(timespec="seconds")
            base["by"] = actor_id(actor)
            base["change"] = {"kind": kind, "person": pid, "fields": fields, **((extra or dict)())}
            try:
                self.cache.write(base)
            except Conflict:
                continue
            self.notes.forget_roster()
            return pid
        raise ApiError(409, "STALE_VERSION")

    @staticmethod
    def _signed_in(actor) -> None:
        if not hasattr(actor, "uid"):
            raise ApiError(403, "SIGN_IN_REQUIRED")

