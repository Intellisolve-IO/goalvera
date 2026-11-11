"""Identity Platform checks for phone recordings (same rules as review_api/auth.py).

The browser sends the teacher's ID token. This service, not the phone, decides:

  no / malformed token          -> 401 SIGN_IN_REQUIRED
  bad signature, expired, wrong
  project / issuer / audience   -> 401 BAD_TOKEN
  email not verified            -> 403 EMAIL_NOT_VERIFIED
  provider not switched on      -> 403 PROVIDER_NOT_ALLOWED
  email not on the allowlist    -> 403 NOT_APPROVED
  role rbt                      -> 403 READ_ONLY_ROLE
  teacher / reviewer / admin    -> a user id (never the email) that may record

The allowlist is gs://<bucket>/config/review_allowlist.json, with the admins'
access changes from the newest people/state-*.json laid on top (None removes
access). Both are re-read at most every ALLOWLIST_TTL seconds. Emulator tokens
(alg "none") are accepted only when IDP_EMULATOR=1 and this is not Cloud Run.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
import urllib.request

ALLOWLIST_OBJECT = "config/review_allowlist.json"
PEOPLE_PREFIX = "people/"
STATE_RE = re.compile(r"^people/state-(\d{8})\.json$")
ALLOWLIST_TTL = float(os.environ.get("REVIEW_ALLOWLIST_TTL", "30"))
SECURETOKEN_CERTS = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
CLOCK_SKEW = 30
KNOWN_PROVIDERS = ("password", "google.com")
ROLES = frozenset({"admin", "teacher", "reviewer", "rbt"})
# Recording is teacher or admin only. reviewer, rbt, and any other value are read-only.
RECORD_ROLES = frozenset({"admin", "teacher"})
_EMAIL = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}$")


class AuthError(Exception):
    def __init__(self, http: int, code: str):
        super().__init__(code)
        self.http, self.code = http, code


def sign_in_providers(raw: str | None = None) -> tuple[str, ...]:
    raw = os.environ.get("IDP_PROVIDERS", "password") if raw is None else raw
    want = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return tuple(p for p in KNOWN_PROVIDERS if p in want) or ("password",)


def normalize_email(e) -> str | None:
    if not isinstance(e, str):
        return None
    e = e.strip().lower()
    if len(e) > 254 or not _EMAIL.match(e) or e.count("@") != 1:
        return None
    local, domain = e.split("@")
    if not local or domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return None
    return e


def bearer_token(headers) -> str | None:
    h = headers.get("Authorization") or ""
    if not h.startswith("Bearer "):
        return None
    tok = h[len("Bearer "):].strip()
    return tok if 0 < len(tok) < 8192 and tok.count(".") == 2 else None


def actor_ref(uid: str) -> str:
    """One-way log reference. The uid itself is mixed-case and must not be logged."""
    return "u_" + hashlib.sha256(uid.encode()).hexdigest()[:16]


def web_device_id(uid: str) -> str:
    """rec/ folder id: web- plus the user id, lowercased, 28 characters at most.

    The pipeline's device pattern allows 32 characters total. web- is 4 of them.
    """
    body = re.sub(r"[^a-z0-9]", "", (uid or "").lower())
    if len(body) < 4:
        body = hashlib.sha256((uid or "").encode()).hexdigest()
    return "web-" + body[:28]


def _b64json(part: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


class CertCache:
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


class TokenVerifier:
    """Verifies Identity Platform ID tokens for one project (review_api/auth.py)."""

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
            raise AuthError(401, "BAD_TOKEN") from exc
        if self.emulator:
            try:
                claims = _b64json(token.split(".")[1])
            except Exception as exc:  # noqa: BLE001
                raise AuthError(401, "BAD_TOKEN") from exc
        else:
            if header.get("alg") != "RS256" or not header.get("kid"):
                raise AuthError(401, "BAD_TOKEN")
            from google.auth import jwt as gjwt
            certs = self.certs.get()
            if header["kid"] not in certs:
                raise AuthError(401, "BAD_TOKEN")
            try:
                claims = gjwt.decode(token, certs={header["kid"]: certs[header["kid"]]},
                                     audience=self.project_id, clock_skew_in_seconds=CLOCK_SKEW)
            except Exception as exc:  # noqa: BLE001
                raise AuthError(401, "BAD_TOKEN") from exc
        now = self.now()
        sub = claims.get("sub")
        if (claims.get("aud") != self.project_id
                or claims.get("iss") != f"https://securetoken.google.com/{self.project_id}"
                or not isinstance(sub, str) or not 0 < len(sub) <= 128
                or not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now - CLOCK_SKEW
                or not isinstance(claims.get("iat"), (int, float)) or claims["iat"] > now + CLOCK_SKEW
                or claims.get("auth_time", 0) > now + CLOCK_SKEW
                or (claims.get("firebase") or {}).get("tenant")):
            raise AuthError(401, "BAD_TOKEN")
        return claims


def _apply_access(users: dict, state: dict) -> dict:
    out = dict(users)
    for e, role in ((state or {}).get("access") or {}).items():
        key = normalize_email(e) or ""
        if not key:
            continue
        if role is None:
            out.pop(key, None)
        elif role in ROLES:
            out[key] = role
    return out


class Allowlist:
    def __init__(self, bucket, ttl: float = ALLOWLIST_TTL, now=time.monotonic):
        self.bucket, self.ttl, self.now = bucket, ttl, now
        self._users, self._at, self._lock = {}, None, threading.Lock()

    def _read_json(self, name: str):
        blob = self.bucket.blob(name)
        try:
            return json.loads(blob.download_as_bytes())
        except Exception:  # noqa: BLE001
            return None

    def _people_state(self) -> dict:
        latest, n = None, -1
        try:
            blobs = self.bucket.list_blobs(prefix=PEOPLE_PREFIX)
        except Exception:  # noqa: BLE001 - fail closed: an access removal must not be skipped
            raise AuthError(503, "ALLOWLIST_UNAVAILABLE")
        for blob in blobs:
            m = STATE_RE.match(getattr(blob, "name", ""))
            if m and int(m.group(1)) > n:
                latest, n = blob.name, int(m.group(1))
        if not latest:
            return {}
        doc = self._read_json(latest)
        if not isinstance(doc, dict):
            raise AuthError(503, "ALLOWLIST_UNAVAILABLE")
        return doc

    def _load(self) -> dict:
        doc = self._read_json(ALLOWLIST_OBJECT)
        if not isinstance(doc, dict):
            raise AuthError(503, "ALLOWLIST_UNAVAILABLE")
        users = {}
        for u in doc.get("users") or []:
            if not isinstance(u, dict):
                continue
            e = normalize_email(u.get("email"))
            if e:
                role = u.get("role")
                # A missing or unknown role stays on the list so the call is refused as
                # read-only, rather than being treated as a teacher.
                users[e] = role if role in ROLES else "unknown"
        return _apply_access(users, self._people_state())

    def role(self, email: str | None) -> str | None:
        with self._lock:
            if self._at is None or self.now() - self._at >= self.ttl:
                self._users, self._at = self._load(), self.now()
            return self._users.get(normalize_email(email) or "")


class WebAuthenticator:
    def __init__(self, bucket, *, project_id: str | None = None, providers: tuple[str, ...] | None = None,
                 verifier: TokenVerifier | None = None, allowlist: Allowlist | None = None):
        self.project_id = project_id if project_id is not None else (
            os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "")
        emulator = os.environ.get("IDP_EMULATOR") == "1" and not os.environ.get("K_SERVICE")
        self.verifier = verifier or TokenVerifier(self.project_id, emulator=emulator)
        self.allowlist = allowlist or Allowlist(bucket)
        self.providers = tuple(providers) if providers is not None else sign_in_providers()

    def require_recorder(self, headers) -> dict:
        """A teacher or admin. Raises AuthError otherwise. The email is not returned."""
        if not self.project_id:
            raise AuthError(500, "AUTH_NOT_CONFIGURED")
        tok = bearer_token(headers)
        if not tok:
            raise AuthError(401, "SIGN_IN_REQUIRED")
        claims = self.verifier.verify(tok)
        provider = (claims.get("firebase") or {}).get("sign_in_provider")
        if provider not in self.providers:
            raise AuthError(403, "PROVIDER_NOT_ALLOWED")
        email = normalize_email(claims.get("email"))
        if not email or claims.get("email_verified") is not True:
            raise AuthError(403, "EMAIL_NOT_VERIFIED")
        role = self.allowlist.role(email)
        if role is None:
            raise AuthError(403, "NOT_APPROVED")
        if role not in RECORD_ROLES:
            # reviewer, rbt, missing role, or any role this service does not record for
            raise AuthError(403, "READ_ONLY_ROLE")
        uid = claims["sub"]
        return {"uid": uid, "role": role, "actor_id": actor_ref(uid), "device_id": web_device_id(uid)}
