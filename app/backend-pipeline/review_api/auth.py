"""Sign-in checks for the hosted review screen (Identity Platform).

Every /api request except /api/auth-config carries an Identity Platform ID
token ("Authorization: Bearer <token>"). The server, never the browser, decides:

  no / malformed token          -> 401 SIGN_IN_REQUIRED
  bad signature, expired, wrong
  project / issuer / audience   -> 401 BAD_TOKEN
  email not verified            -> 403 EMAIL_NOT_VERIFIED
  email not on the allowlist    -> 403 NOT_APPROVED     ("waiting for access")
  allowed                       -> a Reviewer (uid, email, role)

The allowlist is a small JSON file in the bucket (config/review_allowlist.json),
re-read at most every ALLOWLIST_TTL seconds (entries that are not an email
address, such as the "TODO-..." placeholder, are ignored), so adding someone takes effect
within a minute without a redeploy:

  {"users": [{"email": "someone@example.org", "role": "reviewer"},
             {"email": "admin@example.org",   "role": "admin"},
             {"email": "rbt@example.org",     "role": "rbt"}]}

Everyone approved reads every classroom and session.
role "admin" = demo admin: may edit, approve, download and move any session.
role "reviewer" or "teacher" = a teacher: may edit, approve, download and move
only sessions of their own classrooms or their own students (roster
teachers.<teacher_id>.emails; students.<id>.teacher_ids). Their list opens
filtered to their own classrooms.
role "rbt" = an RBT: read-only. Never parent emails; no edit, approve, export or
classroom change (refused on the server). Their list opens on the classroom(s)
listed for their email (config/demo_roster.json -> the RBT's staff record class_codes).
The rules themselves live in review_api/notes.py.

Sign-in providers (IDP_PROVIDERS, comma-separated; default "password"): "password" and
"google.com" (Sign in with Google). Identity Platform issues the same ID token
for both. A token from a provider that isn't switched on here is refused (403
PROVIDER_NOT_ALLOWED), so the Google button and the server always agree.
Three server-side checks for every provider (Google included), all on the server:
  1. email_verified must be true (403 EMAIL_NOT_VERIFIED);
  2. the full address must match a directory / allowlist entry exactly, case-insensitive:
     one dict lookup of the lower-cased address, never a domain, prefix or partial match;
     entries that aren't a full address (a bare domain, "@staff.example", "TODO-...") match nobody;
  3. access removed by an admin (Teachers tab -> No access, people/ record) or taken off the
     allowlist file -> 403 NOT_APPROVED within 30 s, even with a still-valid token.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

_PIPE = Path(__file__).resolve().parents[1]
if str(_PIPE) not in sys.path:
    sys.path.insert(0, str(_PIPE))

from common.classcodes import class_key  # noqa: E402
from common.roster import rbt_records  # noqa: E402

from .notes import ApiError  # noqa: E402

KNOWN_PROVIDERS = ("password", "google.com")
SECURETOKEN_CERTS = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
ALLOWLIST_OBJECT = "config/review_allowlist.json"
ALLOWLIST_TTL = float(os.environ.get("REVIEW_ALLOWLIST_TTL", "30"))
CLOCK_SKEW = 30


@dataclass(frozen=True)
class Reviewer:
    uid: str
    email: str
    role: str = "reviewer"                      # "admin" | "teacher" | "reviewer" | "rbt"
    provider: str | None = None
    teacher_ids: frozenset = field(default_factory=frozenset)
    class_keys: frozenset = field(default_factory=frozenset)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_rbt(self) -> bool:
        return self.role == "rbt"


def sign_in_providers(raw: str | None = None) -> tuple[str, ...]:
    """The providers switched on for this site (IDP_PROVIDERS; unknown names are ignored)."""
    raw = os.environ.get("IDP_PROVIDERS", "password") if raw is None else raw
    want = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return tuple(p for p in KNOWN_PROVIDERS if p in want) or ("password",)


def normalize_email(e) -> str | None:
    """A full address, lower-cased (the case-insensitive key), or None.
    Exactly one "@" with something on both sides and a dot in the domain: a bare domain,
    "@staff.example" or "a@b@c" is not an address and never matches anyone."""
    if not isinstance(e, str):
        return None
    e = e.strip().lower()
    if len(e) > 254 or e.count("@") != 1 or any(c.isspace() for c in e):
        return None
    local, domain = e.split("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return None
    return e


def bearer_token(headers) -> str | None:
    h = headers.get("Authorization") or ""
    if not h.startswith("Bearer "):
        return None
    tok = h[len("Bearer "):].strip()
    return tok if 0 < len(tok) < 8192 and tok.count(".") == 2 else None


# ---------------------------------------------------------------- ID token verification

class CertCache:
    """Google's public signing certs for Identity Platform ID tokens, cached per Cache-Control."""

    def __init__(self, url: str = SECURETOKEN_CERTS, fetch=None):
        self.url, self._fetch = url, fetch or self._http
        self._certs, self._until, self._lock = {}, 0.0, threading.Lock()

    @staticmethod
    def _http(url):
        with urllib.request.urlopen(url, timeout=10) as r:
            cc = r.headers.get("Cache-Control", "")
            max_age = next((int(p.split("=", 1)[1]) for p in cc.replace(" ", "").split(",")
                            if p.startswith("max-age=") and p[8:].isdigit()), 3600)
            return json.loads(r.read()), max_age

    def get(self) -> dict:
        with self._lock:
            if time.time() >= self._until or not self._certs:
                certs, max_age = self._fetch(self.url)
                self._certs, self._until = certs, time.time() + min(max_age, 6 * 3600)
            return self._certs


def _b64json(part: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


class TokenVerifier:
    """Verifies Identity Platform (Firebase Auth) ID tokens for one project."""

    def __init__(self, project_id: str, certs: CertCache | None = None, *, emulator: bool = False,
                 now=time.time):
        if emulator and os.environ.get("K_SERVICE"):
            raise RuntimeError("the auth emulator mode must never run on Cloud Run")
        self.project_id, self.emulator, self.now = project_id, emulator, now
        self.certs = certs or CertCache()

    def verify(self, token: str) -> dict:
        try:
            header = _b64json(token.split(".")[0])
        except Exception as exc:  # noqa: BLE001
            raise ApiError(401, "BAD_TOKEN") from exc
        if self.emulator:
            # Local tests only: the Firebase Auth emulator signs nothing (alg "none").
            try:
                claims = _b64json(token.split(".")[1])
            except Exception as exc:  # noqa: BLE001
                raise ApiError(401, "BAD_TOKEN") from exc
        else:
            if header.get("alg") != "RS256" or not header.get("kid"):
                raise ApiError(401, "BAD_TOKEN")
            from google.auth import jwt as gjwt
            certs = self.certs.get()
            if header["kid"] not in certs:
                raise ApiError(401, "BAD_TOKEN")
            try:
                claims = gjwt.decode(token, certs={header["kid"]: certs[header["kid"]]},
                                     audience=self.project_id, clock_skew_in_seconds=CLOCK_SKEW)
            except Exception as exc:  # noqa: BLE001 - expired, bad signature, wrong audience...
                raise ApiError(401, "BAD_TOKEN") from exc
        now = self.now()
        sub = claims.get("sub")
        if (claims.get("aud") != self.project_id
                or claims.get("iss") != f"https://securetoken.google.com/{self.project_id}"
                or not isinstance(sub, str) or not 0 < len(sub) <= 128
                or not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now - CLOCK_SKEW
                or not isinstance(claims.get("iat"), (int, float)) or claims["iat"] > now + CLOCK_SKEW
                or claims.get("auth_time", 0) > now + CLOCK_SKEW
                or (claims.get("firebase") or {}).get("tenant")):      # no multi-tenancy here
            raise ApiError(401, "BAD_TOKEN")
        return claims


# ---------------------------------------------------------------- allowlist

# "reviewer" is the teacher role already used on the allowlist. "teacher" is the same scope.
_ROLES = frozenset({"admin", "teacher", "reviewer", "rbt"})


class Allowlist:
    """The base file, with the admins' role / email changes (people/, review_api/people.py) on top."""

    def __init__(self, store, name: str = ALLOWLIST_OBJECT, ttl: float = ALLOWLIST_TTL, now=time.monotonic):
        self.store, self.name, self.ttl, self.now = store, name, ttl, now
        self._users, self._at, self._ver, self._lock = {}, None, None, threading.Lock()

    def _load(self, state: dict) -> dict:
        from .people import apply_access
        doc = self.store.read_json(self.name) or {}
        users = {}
        for u in doc.get("users", []):
            e = normalize_email(u.get("email"))
            if e:                                        # placeholders (no "@") never match anyone
                role = u.get("role")
                users[e] = role if role in _ROLES else "reviewer"
        return apply_access(users, state)

    def role(self, email: str | None) -> str | None:
        from .people import state_for
        state = state_for(self.store).get()
        with self._lock:
            # the same people/ snapshot as the roster mapping: an email change moves both at once
            if self._at is None or self.now() - self._at >= self.ttl or self._ver != state.get("version"):
                self._users, self._at, self._ver = self._load(state), self.now(), state.get("version")
            return self._users.get(normalize_email(email) or "")


class Roster:
    """Who an email is, from the roster in the bucket (cached like the allowlist).

    teachers.<id>.emails -> teacher ids (a teacher's classes).
    RBT records (staff.<id> with role "rbt", or the older rbts.<id>) emails + class_codes ->
    classroom class keys. Same scope either way.
    """

    def __init__(self, store, name: str = "config/demo_roster.json", ttl: float = ALLOWLIST_TTL, now=time.monotonic):
        self.store, self.name, self.ttl, self.now = store, name, ttl, now
        self._teachers, self._classes, self._at, self._ver, self._lock = {}, {}, None, None, threading.Lock()

    def _load(self, state: dict) -> tuple[dict, dict]:
        from .people import apply_roster
        doc = apply_roster(self.store.read_json(self.name) or {}, state)   # sign-in emails changed by admins
        teachers: dict = {}
        for tid, t in (doc.get("teachers") or {}).items():
            if not isinstance(t, dict):
                continue
            for e in t.get("emails") or []:
                e = normalize_email(e)
                if e:
                    teachers.setdefault(e, set()).add(tid)
        classes: dict = {}
        for rbt in rbt_records(doc).values():
            if not isinstance(rbt, dict):
                continue
            keys = {k for k in (class_key(c) for c in (rbt.get("class_codes") or [])) if k}
            for e in rbt.get("emails") or []:
                e = normalize_email(e)
                if e:
                    classes.setdefault(e, set()).update(keys)
        return teachers, classes

    def _ensure(self) -> None:
        from .people import state_for
        state = state_for(self.store).get()
        if self._at is None or self.now() - self._at >= self.ttl or self._ver != state.get("version"):
            self._teachers, self._classes, self._at, self._ver = (*self._load(state), self.now(), state.get("version"))

    def teacher_ids_for(self, email: str) -> frozenset:
        with self._lock:
            self._ensure()
            return frozenset(self._teachers.get(normalize_email(email) or "", ()))

    def class_keys_for(self, email: str) -> frozenset:
        with self._lock:
            self._ensure()
            return frozenset(self._classes.get(normalize_email(email) or "", ()))


class Authenticator:
    def __init__(self, verifier: TokenVerifier, allowlist: Allowlist, roster: Roster | None = None,
                 providers: tuple[str, ...] | None = None):
        self.verifier, self.allowlist, self.roster = verifier, allowlist, roster
        self.providers = tuple(providers) if providers is not None else sign_in_providers()

    @staticmethod
    def provider_of(claims: dict) -> str | None:
        return (claims.get("firebase") or {}).get("sign_in_provider")

    def identify(self, headers) -> tuple[dict, str | None]:
        """Valid token -> (claims, role or None). Raises 401 for missing/bad tokens."""
        tok = bearer_token(headers)
        if not tok:
            raise ApiError(401, "SIGN_IN_REQUIRED")
        claims = self.verifier.verify(tok)
        email = normalize_email(claims.get("email"))
        ok = email and claims.get("email_verified") is True and self.provider_of(claims) in self.providers
        role = self.allowlist.role(email) if ok else None
        return claims, role

    def require(self, headers) -> Reviewer:
        claims, role = self.identify(headers)
        if self.provider_of(claims) not in self.providers:
            raise ApiError(403, "PROVIDER_NOT_ALLOWED")
        email = normalize_email(claims.get("email"))
        if not email or claims.get("email_verified") is not True:
            raise ApiError(403, "EMAIL_NOT_VERIFIED")
        if role is None:
            raise ApiError(403, "NOT_APPROVED")
        tids, ckeys = frozenset(), frozenset()
        if self.roster and role != "admin":
            # Role decides the scope. An RBT is not also given the teacher's classes.
            if role == "rbt":
                ckeys = self.roster.class_keys_for(email)
            else:
                tids = self.roster.teacher_ids_for(email)
        return Reviewer(uid=claims["sub"], email=email, role=role,
                        provider=self.provider_of(claims),
                        teacher_ids=tids, class_keys=ckeys)
