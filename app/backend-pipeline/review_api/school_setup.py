"""Domains (educational methodology) and Classes (David, Oct 10 2026; tasks#103).

Domains: every Subject / Domain from David's Educational Methodology document with its Core
Educational Methodology (a name and a description), Key Curricular Programs and Recommended Book /
Guide. Reference data, not student data. Admins add, edit and remove; everyone signed in reads.

Classes: the actual class. A name, teacher(s) (one or more roster staff), an educational domain (from
Domains), students (roster students), a classroom (a roster classroom) and a schedule (one or more
meeting patterns: days of the week, start time, duration in minutes). Admins create, edit and delete.
A teacher may edit a class they teach (same rule as sessions and plans: a teacher acts on their own;
RBTs read only); they must stay on it and keep it in one of their own classrooms.

Lookups:
  * the session page picks the session's class from the Classes of its classroom; the app suggests the
    class whose schedule matches the recording's start time (Eastern) and length. The pick is a
    create-only audit record (audit/<rec>/..._session_class_assigned_...json) and a journal row.
  * classroom materials use the class's domain: its name and core methodology (text only, no student
    data) go into the brief and the per-draft prompt (materials.py).

Storage (same pattern as branding/ and materials_settings/): the seed files in the image
(app/samples/school-setup/domains.json, classes.json; built by app/tools/build_school_setup.py) are the
starting point. Every admin or teacher change writes ONE new create-only record with the whole list:
  school_setup/domains-<time>-<x>.json   {"kind", "profile", "items": [...], "by", "at", "base", "change"}
  school_setup/classes-<time>-<x>.json
The newest record of the active school profile is current (review-web can create and read there, never
overwrite or delete; infra/setup_school_setup.sh). A save names the version it was edited from; a save
from an older version is refused (409 STALE_VERSION) so two people can't overwrite each other.
The classes seed belongs to the demo school (everyday-heroes) only; domains apply to every profile.

Journal (ids only): domain_added / domain_edited / domain_removed (domain id, changed field names),
class_added / class_edited / class_removed (class id, changed field names, counts), and
session_class_assigned (session, class id, whether the suggestion was taken).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
import secrets
import unicodedata
from pathlib import Path

from common.classcodes import class_key, find_class
from common.roster import roster_students, staff_directory
from common.safelog import log

from .branding import active_profile
from .incident_print import ET, recording_start
from .notes import ApiError, actor_id, check_actor, is_read_only, may_act, signed_in
from .store import Conflict

PREFIX = "school_setup/"
DEMO_PROFILE = "everyday-heroes"          # the classes seed is the fictional demo school's
LABEL = "Fictional sample data"
_HERE = Path(__file__).resolve()
SEED_DIRS = [_HERE.parents[2] / "samples" / "school-setup",     # repo: app/samples/school-setup
             _HERE.parents[1] / "samples" / "school-setup"]     # image: /srv/samples/school-setup
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
CLASS_EVENT = "session_class_assigned"
GRACE_MINUTES = 10                        # a recording may start a little before / end after its class
MAX = {"name": 80, "methodology": 120, "methodology_detail": 600, "programs": 300, "book": 300}
MAX_CLASS_NAME = 80
MAX_MEETINGS = 10
MAX_CLASSES = 500
MAX_DOMAINS = 100
_ID = re.compile(r"^[a-z0-9][a-z0-9\-]{0,63}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_SEED: dict = {}


def _seed(name: str) -> dict:
    if name not in _SEED:
        doc = {}
        for d in SEED_DIRS:
            if (d / name).is_file():
                doc = json.loads((d / name).read_text(encoding="utf-8"))
                break
        _SEED[name] = doc
    return copy.deepcopy(_SEED[name])


def seed_domains() -> dict:
    return _seed("domains.json")


def seed_classes() -> dict:
    return _seed("classes.json")


def clean_text(v, field: str, limit: int, *, required: bool = False) -> str:
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise ApiError(400, f"BAD_{field.upper()}")
    v = unicodedata.normalize("NFC", v)
    v = "".join(" " if unicodedata.category(c).startswith(("C", "Z")) else c for c in v)
    v = re.sub(r"\s+", " ", v).strip()
    if (required and not v) or len(v) > limit:
        raise ApiError(400, f"BAD_{field.upper()}")
    return v


def slug(name: str, prefix: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("&", " ")).strip("-")[:48] or "x"
    return f"{prefix}-{s}"


def _hhmm(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def schedule_text(schedule: list[dict]) -> str:
    """[{days: [Mon..Fri], start: "08:20", minutes: 90}] -> "Mon–Fri 8:20 AM, 90 min"."""
    out = []
    for m in schedule or []:
        days = m.get("days") or []
        idx = [DAYS.index(d) for d in days if d in DAYS]
        if idx and idx == list(range(idx[0], idx[-1] + 1)) and len(idx) > 2:
            dtext = f"{DAYS[idx[0]]}–{DAYS[idx[-1]]}"
        else:
            dtext = ", ".join(days)
        h, mi = divmod(_hhmm(m["start"]), 60)
        out.append(f"{dtext} {h % 12 or 12}:{mi:02d} {'AM' if h < 12 else 'PM'}, {m['minutes']} min")
    return "; ".join(out)


class SchoolSetup:
    def __init__(self, notes, *, now=None):
        self.notes = notes
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))

    @property
    def rec(self):
        return self.notes.recorder

    # ------------------------------------------------------------ storage
    def _list(self, prefix: str) -> list[str]:
        try:
            return self.notes.store.list(prefix)
        except Exception as exc:  # noqa: BLE001 - no bucket access to school_setup/ yet: the seed shows
            if type(exc).__name__ in ("Forbidden", "PermissionError", "PermissionDenied"):
                log("school_setup_unreadable", "WARNING", step="school_setup", error_type=type(exc).__name__)
                return []
            raise

    def _latest(self, kind: str) -> tuple[list[dict], str]:
        """(items, version): the newest record of this kind for the active profile, else the seed."""
        want = active_profile()
        for n in sorted((n for n in self._list(f"{PREFIX}{kind}-") if n.endswith(".json")), reverse=True):
            r = self.notes.store.read_json(n)
            if isinstance(r, dict) and r.get("profile") == want and isinstance(r.get("items"), list):
                return r["items"], n[len(PREFIX):-len(".json")]
        if kind == "domains":
            return seed_domains().get("domains") or [], "seed"
        return (seed_classes().get("classes") or []) if want == DEMO_PROFILE else [], "seed"

    def _save(self, kind: str, items: list[dict], actor, base: str, change: dict) -> str:
        _, current = self._latest(kind)
        if base != current:
            raise ApiError(409, "STALE_VERSION")
        now = self._now()
        ver = f"{kind}-{now.strftime('%Y%m%dT%H%M%S%f')}-{secrets.token_hex(3)}"
        try:
            self.notes.store.write_json(f"{PREFIX}{ver}.json", {
                "kind": kind, "profile": active_profile(), "items": items, "by": actor_id(actor),
                "at": now.isoformat(timespec="seconds"), "base": base, "change": change}, create_only=True)
        except Conflict as exc:
            raise ApiError(409, "STALE_VERSION") from exc
        return ver

    @staticmethod
    def _version(body: dict) -> str:
        v = body.get("version")
        if not isinstance(v, str) or not v:
            raise ApiError(400, "VERSION_REQUIRED")
        return v

    @staticmethod
    def _require_admin(actor):
        if not (signed_in(actor) and actor.is_admin):
            raise ApiError(403, "ADMIN_ONLY")

    def _never(self) -> dict:
        from .bug_reports import never_values
        return never_values(self.notes.roster(), {}, {})

    # ------------------------------------------------------------ domains
    def domains(self) -> list[dict]:
        return self._latest("domains")[0]

    def domain(self, did) -> dict | None:
        return next((d for d in self.domains() if d.get("id") == did), None) if did else None

    def domains_view(self, actor) -> dict:
        actor = check_actor(actor)
        items, ver = self._latest("domains")
        classes = self.classes()
        used = {}
        for c in classes:
            if c.get("domain_id"):
                used[c["domain_id"]] = used.get(c["domain_id"], 0) + 1
        seed = seed_domains()
        return {"domains": [{**d, "classes": used.get(d["id"], 0)} for d in items], "version": ver,
                "can_edit": bool(signed_in(actor) and actor.is_admin),
                "delivery_formats": seed.get("delivery_formats") or [],
                "source": (seed.get("_meta") or {}).get("source"),
                "limits": MAX}

    def _clean_domain(self, body: dict, items: list[dict], keep_id: str | None) -> dict:
        d = body.get("domain")
        if not isinstance(d, dict):
            raise ApiError(400, "BAD_DOMAIN")
        out = {"name": clean_text(d.get("name"), "name", MAX["name"], required=True)}
        for k in ("methodology", "methodology_detail", "programs", "book"):
            out[k] = clean_text(d.get(k), k, MAX[k])
        if any(x["name"].casefold() == out["name"].casefold() and x["id"] != keep_id for x in items):
            raise ApiError(409, "DOMAIN_EXISTS")
        # name + methodology go to the AI with materials: no roster names, emails or ids in them
        from .materials import has_forbidden_name_cased
        if has_forbidden_name_cased(" ".join((out["name"], out["methodology"], out["methodology_detail"])), self._never()):
            raise ApiError(400, "DOMAIN_HAS_NAMES")
        return out

    def add_domain(self, actor, body: dict) -> dict:
        self._require_admin(actor)
        base = self._version(body)
        items, _ = self._latest("domains")
        if len(items) >= MAX_DOMAINS:
            raise ApiError(400, "TOO_MANY_DOMAINS")
        d = self._clean_domain(body, items, None)
        did, n = slug(d["name"], "dom"), 2
        while any(x["id"] == did for x in items):
            did, n = f"{slug(d['name'], 'dom')}-{n}", n + 1
        new = {"id": did, **d}
        self._save("domains", items + [new], actor, base, {"op": "add", "id": did})
        self.rec.record(actor, "domain_added", record_id=did, required=True,
                        fields=".".join(k for k, v in d.items() if v))
        return self.domains_view(actor)

    def edit_domain(self, actor, did: str, body: dict) -> dict:
        self._require_admin(actor)
        base = self._version(body)
        items, _ = self._latest("domains")
        i = next((n for n, x in enumerate(items) if x.get("id") == did), None)
        if i is None:
            raise ApiError(404, "UNKNOWN_DOMAIN")
        d = self._clean_domain(body, items, did)
        changed = [k for k, v in d.items() if (items[i].get(k) or "") != v]
        if not changed:
            return self.domains_view(actor)
        items = [*items[:i], {**items[i], **d}, *items[i + 1:]]
        self._save("domains", items, actor, base, {"op": "edit", "id": did, "fields": changed})
        self.rec.record(actor, "domain_edited", record_id=did, required=True, fields=".".join(changed))
        return self.domains_view(actor)

    def remove_domain(self, actor, did: str, body: dict) -> dict:
        self._require_admin(actor)
        base = self._version(body)
        items, _ = self._latest("domains")
        if not any(x.get("id") == did for x in items):
            raise ApiError(404, "UNKNOWN_DOMAIN")
        if any(c.get("domain_id") == did for c in self.classes()):
            raise ApiError(409, "DOMAIN_IN_USE")            # pick another domain for those classes first
        self._save("domains", [x for x in items if x.get("id") != did], actor, base, {"op": "remove", "id": did})
        self.rec.record(actor, "domain_removed", record_id=did, required=True)
        return self.domains_view(actor)

    # ------------------------------------------------------------ classes
    def classes(self) -> list[dict]:
        return self._latest("classes")[0]

    def klass(self, cid) -> dict | None:
        return next((c for c in self.classes() if c.get("id") == cid), None) if cid else None

    def _staff(self) -> dict:
        """id -> {"id", "name", "role"} for every staff member (one directory, tasks#104)."""
        return {p["id"]: {"id": p["id"], "name": p["name"],
                          "role": "RBT" if p["role"] == "rbt" else (p["title"] or "Staff")}
                for p in staff_directory(self.notes.roster())}

    def _mine(self, actor) -> set:
        if signed_in(actor):
            return set(actor.teacher_ids or ())
        return {actor} if isinstance(actor, str) else set()

    def _may_edit(self, actor, c: dict) -> bool:
        if is_read_only(actor):
            return False
        if signed_in(actor) and actor.is_admin:
            return True
        return bool(self._mine(actor) & set(c.get("teacher_ids") or []))

    def _own_classrooms(self, actor) -> set:
        codes = self.notes.roster().get("class_codes") or {}
        mine = self._mine(actor)
        return {d for d, e in codes.items() if isinstance(e, dict) and e.get("teacher_id") in mine}

    def _public_class(self, c: dict, actor, staff: dict, studs: dict, doms: dict) -> dict:
        d = doms.get(c.get("domain_id")) or {}
        return {**c,
                "teachers": [{"id": t, "name": (staff.get(t) or {}).get("name") or t, "role": (staff.get(t) or {}).get("role")}
                             for t in c.get("teacher_ids") or []],
                "students": [{"student_id": s, "name": f"{(studs.get(s) or {}).get('first_name') or s} "
                                                      f"{(studs.get(s) or {}).get('last_initial') or ''}".strip()}
                             for s in c.get("student_ids") or []],
                "domain_name": d.get("name"), "domain_methodology": d.get("methodology"),
                "schedule_text": schedule_text(c.get("schedule") or []),
                "can_edit": self._may_edit(actor, c)}

    def classes_view(self, actor) -> dict:
        """Everyone signed in reads every class (students: first name + last initial, like the other
        lists). The list opens on the viewer's default classroom (notes.default_classroom)."""
        actor = check_actor(actor)
        items, ver = self._latest("classes")
        r = self.notes.roster()
        staff, studs = self._staff(), roster_students(r)
        doms = {d["id"]: d for d in self.domains()}
        admin = signed_in(actor) and actor.is_admin
        rows = sorted((self._public_class(c, actor, staff, studs, doms) for c in items),
                      key=lambda c: (c.get("classroom") or "", (c.get("schedule") or [{}])[0].get("start") or "", c["name"]))
        return {
            "classes": rows, "version": ver, "can_add": bool(admin),
            "default_classroom": self.notes.default_classroom(actor) if signed_in(actor) else None,
            "classrooms": sorted((r.get("class_codes") or {}).keys()),
            "own_classrooms": sorted(self._own_classrooms(actor)) if not admin else [],
            "domains": [{"id": d["id"], "name": d["name"], "methodology": d.get("methodology")} for d in self.domains()],
            "staff": sorted(staff.values(), key=lambda s: (s["role"] != "Teacher" and "Teacher" not in (s["role"] or ""), s["name"])),
            "students": sorted(({"student_id": sid, "name": f"{st.get('first_name')} {st.get('last_initial') or ''}".strip(),
                                 "classrooms": [find_class(r, x)[0] or x for x in st.get("class_codes") or []]}
                                for sid, st in studs.items()), key=lambda s: s["name"].casefold()),
            "days": DAYS, "label": LABEL if active_profile() == DEMO_PROFILE else None,
        }

    def _clean_class(self, actor, body: dict, keep: dict | None) -> dict:
        c = body.get("class")
        if not isinstance(c, dict):
            raise ApiError(400, "BAD_CLASS")
        r = self.notes.roster()
        name = clean_text(c.get("name"), "class_name", MAX_CLASS_NAME, required=True)
        room, _ = find_class(r, c.get("classroom") if isinstance(c.get("classroom"), str) else None)
        if room is None:
            raise ApiError(400, "UNKNOWN_CLASSROOM")
        staff = self._staff()
        tids = c.get("teacher_ids")
        if not isinstance(tids, list) or not tids or any(not isinstance(t, str) or t not in staff for t in tids):
            raise ApiError(400, "BAD_TEACHERS")
        tids = list(dict.fromkeys(tids))
        studs = roster_students(r)
        sids = c.get("student_ids") or []
        if not isinstance(sids, list) or any(not isinstance(s, str) or s not in studs for s in sids):
            raise ApiError(400, "BAD_STUDENTS")
        sids = sorted(dict.fromkeys(sids))
        did = c.get("domain_id") or None
        if did is not None and (not isinstance(did, str) or self.domain(did) is None):
            raise ApiError(400, "UNKNOWN_DOMAIN")
        sched = c.get("schedule") or []
        if not isinstance(sched, list) or len(sched) > MAX_MEETINGS:
            raise ApiError(400, "BAD_SCHEDULE")
        meetings = []
        for m in sched:
            if not isinstance(m, dict):
                raise ApiError(400, "BAD_SCHEDULE")
            days = m.get("days")
            start = m.get("start")
            mins = m.get("minutes")
            if (not isinstance(days, list) or not days or any(d not in DAYS for d in days)
                    or not isinstance(start, str) or not _TIME.match(start)
                    or not isinstance(mins, int) or isinstance(mins, bool) or not 5 <= mins <= 600
                    or _hhmm(start) + mins > 24 * 60):
                raise ApiError(400, "BAD_SCHEDULE")
            mt = {"days": [d for d in DAYS if d in days], "start": start, "minutes": mins}
            if isinstance(m.get("period"), str) and m["period"].strip():
                mt["period"] = clean_text(m["period"], "period", 40)
            meetings.append(mt)
        out = {"name": name, "classroom": room, "teacher_ids": tids, "domain_id": did,
               "student_ids": sids, "schedule": meetings}
        if not (signed_in(actor) and actor.is_admin):        # a teacher editing their own class
            mine = self._mine(actor)
            if not mine & set(tids):
                raise ApiError(403, "MUST_STAY_ON_CLASS")
            if room != (keep or {}).get("classroom") and room not in self._own_classrooms(actor):
                raise ApiError(403, "NOT_YOUR_CLASSROOM")
        return out

    def add_class(self, actor, body: dict) -> dict:
        actor = check_actor(actor)
        self._require_admin(actor)
        base = self._version(body)
        items, _ = self._latest("classes")
        if len(items) >= MAX_CLASSES:
            raise ApiError(400, "TOO_MANY_CLASSES")
        c = self._clean_class(actor, body, None)
        cid = f"cls-{secrets.token_hex(4)}"
        new = {"id": cid, **c, **({"label": LABEL} if active_profile() == DEMO_PROFILE else {})}
        self._save("classes", items + [new], actor, base, {"op": "add", "id": cid})
        self.rec.record(actor, "class_added", record_id=cid, required=True, classroom=class_key(c["classroom"]),
                        teachers=len(c["teacher_ids"]), students=len(c["student_ids"]), meetings=len(c["schedule"]),
                        domain_id=c["domain_id"])
        return self.classes_view(actor)

    def edit_class(self, actor, cid: str, body: dict) -> dict:
        actor = check_actor(actor)
        base = self._version(body)
        items, _ = self._latest("classes")
        i = next((n for n, x in enumerate(items) if x.get("id") == cid), None)
        if i is None:
            raise ApiError(404, "UNKNOWN_CLASS")
        if not self._may_edit(actor, items[i]):
            raise ApiError(403, "NOT_CLASS_TEACHER")
        c = self._clean_class(actor, body, items[i])
        changed = [k for k, v in c.items() if items[i].get(k) != v]
        if not changed:
            return self.classes_view(actor)
        items = [*items[:i], {**items[i], **c}, *items[i + 1:]]
        self._save("classes", items, actor, base, {"op": "edit", "id": cid, "fields": changed})
        self.rec.record(actor, "class_edited", record_id=cid, required=True, fields=".".join(changed),
                        classroom=class_key(c["classroom"]), teachers=len(c["teacher_ids"]),
                        students=len(c["student_ids"]), meetings=len(c["schedule"]), domain_id=c["domain_id"])
        return self.classes_view(actor)

    def remove_class(self, actor, cid: str, body: dict) -> dict:
        actor = check_actor(actor)
        self._require_admin(actor)
        base = self._version(body)
        items, _ = self._latest("classes")
        if not any(x.get("id") == cid for x in items):
            raise ApiError(404, "UNKNOWN_CLASS")
        self._save("classes", [x for x in items if x.get("id") != cid], actor, base, {"op": "remove", "id": cid})
        self.rec.record(actor, "class_removed", record_id=cid, required=True)
        return self.classes_view(actor)

    # ------------------------------------------------------------ session <-> class
    @staticmethod
    def match_score(c: dict, start: dt.datetime | None, seconds) -> tuple[float, float]:
        """How well a recording fits the class, on the recording's Eastern weekday: (minutes inside a
        meeting, minutes inside a meeting widened by GRACE_MINUTES either side). (0, 0) = no match."""
        if start is None:
            return 0.0, 0.0
        t = start.astimezone(ET)
        day = DAYS[t.weekday()]
        a = t.hour * 60 + t.minute + t.second / 60
        try:
            b = a + max(1.0, float(seconds or 0) / 60)
        except (TypeError, ValueError):
            b = a + 1
        best = (0.0, 0.0)
        for m in c.get("schedule") or []:
            if day not in (m.get("days") or []):
                continue
            s0, e0 = _hhmm(m["start"]), _hhmm(m["start"]) + m["minutes"]
            inside = max(0.0, min(b, e0) - max(a, s0))
            near = max(0.0, min(b, e0 + GRACE_MINUTES) - max(a, s0 - GRACE_MINUTES))
            best = max(best, (inside, near))
        return best

    def suggest(self, s: dict) -> tuple[dict | None, list[dict]]:
        """(best matching class or None, the classroom's classes) for a session."""
        k = class_key(s.get("class_code"))
        here = [c for c in self.classes() if k and class_key(c.get("classroom")) == k]
        start = recording_start(s)
        scored = sorted(((self.match_score(c, start, s.get("duration_seconds")), c) for c in here),
                        key=lambda x: (-x[0][0], -x[0][1]))
        best = scored[0][1] if scored and scored[0][0][1] > 0 else None
        return best, here

    def assigned(self, rid: str) -> dict | None:
        names = [n for n in self.notes.store.list(f"audit/{rid}/") if f"_{CLASS_EVENT}_" in n]
        return (self.notes.store.read_json(sorted(names)[-1]) or {}) if names else None

    def session_class(self, rid: str, actor, s: dict | None = None) -> dict:
        """GET /api/sessions/<rid>/class: the session's class (picked, else suggested) and the choices."""
        if s is None:
            s = self.notes.context(rid)
        best, here = self.suggest(s)
        got = self.assigned(rid)
        doms = {d["id"]: d for d in self.domains()}
        cur = self.klass((got or {}).get("class_id")) if got else None
        chosen = got is not None
        if not chosen:
            cur = best

        def row(c):
            d = doms.get(c.get("domain_id")) or {}
            return {"id": c["id"], "name": c["name"], "domain_id": c.get("domain_id"), "domain_name": d.get("name"),
                    "methodology": d.get("methodology"), "schedule_text": schedule_text(c.get("schedule") or [])}
        can = actor is None or (not is_read_only(actor) and may_act(actor, s)) or (signed_in(actor) and actor.is_admin)
        return {"recording_id": rid, "classroom": s.get("class_code"),
                "class": row(cur) if cur else None,
                "source": ("teacher" if (got or {}).get("class_id") else "cleared") if chosen else ("suggested" if cur else None),
                "suggested_id": best["id"] if best else None,
                "choices": [row(c) for c in sorted(here, key=lambda c: ((c.get("schedule") or [{}])[0].get("start") or "", c["name"]))],
                "can_change": bool(can)}

    def assign(self, rid: str, actor, body: dict) -> dict:
        """POST /api/sessions/<rid>/class {"class_id": "..."|null}: logged (audit trail + journal)."""
        actor = check_actor(actor)
        if is_read_only(actor):
            raise ApiError(403, "READ_ONLY_ROLE")
        s = self.notes.context(rid)
        if not (may_act(actor, s) or (signed_in(actor) and actor.is_admin)):
            raise ApiError(403, "NOT_SESSION_TEACHER")
        cid = body.get("class_id")
        if cid is not None:
            if not isinstance(cid, str) or not _ID.match(cid):
                raise ApiError(400, "BAD_CLASS")
            c = self.klass(cid)
            if c is None:
                raise ApiError(400, "UNKNOWN_CLASS")
            if class_key(c.get("classroom")) != class_key(s.get("class_code")):
                raise ApiError(409, "CLASS_NOT_IN_CLASSROOM")   # move the session's classroom first
        best, _ = self.suggest(s)
        self.notes._audit(rid, CLASS_EVENT, actor, self.notes.store.created(f"drafts/{rid}.json"),
                          class_id=cid, session=s, ids_only=True,
                          journal={"class_id": cid, "suggested": bool(best and cid == best["id"])})
        return self.session_class(rid, actor, s)

    def domain_context(self, session: dict) -> str:
        """Materials hook (materials.Materials(domain_context=...)): "<domain>: <methodology>" of the
        session's class, or "". Only the domain name and the core methodology; no class name, programs,
        book, schedule or student data."""
        rid = (session or {}).get("recording_id")
        if (session or {}).get("class_id"):        # the planner's Make form: the class the teacher picked
            got = self.class_domain(session["class_id"])
        else:
            got = self.session_domain(rid, session) if rid else None
        return f"{got['domain']}: {got['methodology']}" if got else ""

    def approach_view(self, rid: str, s: dict) -> dict | None:
        """What the make form shows (the class name is shown to the teacher, never sent to the AI)."""
        got = self.session_domain(rid, s)
        return {k: got[k] for k in ("class_name", "domain", "methodology")} if got else None

    def class_domain(self, cid) -> dict | None:
        """Domain + core methodology of one class (the planner's Make form picks the class)."""
        c = self.klass(cid)
        d = self.domain((c or {}).get("domain_id"))
        if not d:
            return None
        meth = " ".join(x for x in ((d.get("methodology") or "").strip(), (d.get("methodology_detail") or "").strip()) if x)
        return {"class_id": c["id"], "class_name": c["name"], "domain": d["name"], "methodology": meth}

    def classroom_classes(self, class_code) -> list[dict]:
        """The classes held in a classroom (for the planner's Make form): id, name, domain name."""
        k = class_key(class_code)
        doms = {d["id"]: d for d in self.domains()}
        return [{"id": c["id"], "name": c["name"], "domain_name": (doms.get(c.get("domain_id")) or {}).get("name")}
                for c in self.classes() if k and class_key(c.get("classroom")) == k]

    def session_domain(self, rid: str, s: dict) -> dict | None:
        """{"class_id", "domain", "methodology"} for the materials prompt: domain name + core methodology
        (name and description) of the session's class (picked, else suggested). No student data."""
        try:
            got = self.assigned(rid)
            c = self.klass(got.get("class_id")) if got else self.suggest(s)[0]
        except Exception as exc:  # noqa: BLE001 - never block materials on the lookup
            log("school_setup_lookup_failed", "WARNING", step="materials", error_type=type(exc).__name__)
            return None
        d = self.domain((c or {}).get("domain_id"))
        if not d:
            return None
        meth = " ".join(x for x in ((d.get("methodology") or "").strip(), (d.get("methodology_detail") or "").strip()) if x)
        return {"class_id": c["id"], "class_name": c["name"], "domain": d["name"], "methodology": meth}
