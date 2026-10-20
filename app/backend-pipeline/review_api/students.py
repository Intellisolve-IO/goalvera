"""Students, Teachers and Settings tabs.

Students: every roster student, for every signed-in role (design decision:
teachers see all students and every IEP; the list only opens filtered to their own
classrooms). Opening a student returns the IEP (whatever the roster holds), one
school week (Mon-Fri) of that student's daily session summaries, and the week's
saved weekly summary / draft parent email. The weekly summary and draft parent
email are built ONLY here now (the lesson planner links to this page); saving and
AI drafting reuse the planner's endpoints and rules (planner.py: "the student"
de-identification, 8-word transcript check, never sent).

Every opening of a student's IEP is journaled: iep_viewed, the student's internal
id, who and when (required: no journal, no IEP). RBTs with iep_access "none" get
no IEP and no iep_viewed entry. Parent emails only for the student's teachers and
admins (planner rule).

Teachers: names, classrooms and roles from the roster; emails for admins only.
Settings: admins only, read-only view of the configuration the app runs with.
"""
from __future__ import annotations

import datetime as dt
import os

from gemini_analyzer.prompt import reinsert_first_name

from . import planner_ai as ai
from common.roster import (RBT_ROLE, TEACHER_ROLE, all_student_ids, class_students, find_student, rbt_records,
                           roster_students, staff_directory, student_classrooms)

from .notes import ApiError, is_read_only, rbt_iep_access, signed_in
from .planner import MAX_WEEKS_BACK, _SID, _public, parse_week, suggest, three_weeks, today, week_of

# IEP fields shown as they are, when the roster carries them (students.<id>.iep or the classroom entry)
RETENTION = {"audio_days": int(os.environ.get("AUDIO_RETENTION_DAYS", "7")),
             "derived_days": int(os.environ.get("DERIVED_RETENTION_DAYS", "30")),
             "log_days": int(os.environ.get("LOG_RETENTION_DAYS", "7"))}
IEP_FIELDS = ("plan_type", "eligibility", "present_levels", "accommodations", "modifications", "services",
              "placement", "behavior_plan", "esy", "iep_start_date", "iep_review_date", "case_manager")


class Students:
    def __init__(self, notes, planner):
        self.notes, self.planner, self.rec = notes, planner, notes.recorder

    # ------------------------------------------------------------ roster
    def _all(self) -> dict:
        """student_id -> {student, class_codes, teacher_names, extra IEP fields, label, grade}.
        Both roster shapes (common/roster.py): roster.students (many per classroom) and the old
        class_codes.<code>.student_id."""
        r = self.notes.roster()
        codes = r.get("class_codes") or {}
        teachers = r.get("teachers") or {}
        extra = {k: v for k, v in (r.get("students") or {}).items() if isinstance(v, dict)}
        out: dict = {}
        for sid in all_student_ids(r):
            st, _ = find_student(r, sid)
            if st is None:
                continue
            row = {"student": st, "class_codes": student_classrooms(r, sid), "teacher_names": [], "iep": {},
                   "label": None, "grade": None, "attendance": None}
            more = extra.get(sid) or {}
            for d in row["class_codes"]:
                e = codes.get(d) or {}
                for f in IEP_FIELDS:                     # old shape: IEP fields on the class entry
                    if e.get("student_id") == sid and e.get(f) not in (None, "", []):
                        row["iep"][f] = e[f]
                n = e.get("teacher_name")
                if n and n not in row["teacher_names"]:
                    row["teacher_names"].append(n)
            for tid in more.get("teacher_ids") or []:
                n = (teachers.get(tid) or {}).get("teacher_name") if isinstance(tid, str) else None
                if n and n not in row["teacher_names"]:
                    row["teacher_names"].append(n)
            src = more.get("iep") if isinstance(more.get("iep"), dict) else more
            for f in IEP_FIELDS:
                if src.get(f) not in (None, "", []):
                    row["iep"][f] = src[f]
            row["label"] = more.get("label") or ((r.get("_meta") or {}).get("label") if (r.get("_meta") or {}).get("synthetic") else None)
            row["grade"] = more.get("grade")
            row["attendance"] = more.get("attendance") if isinstance(more.get("attendance"), dict) else None
            out[sid] = row
        return out

    def _iep_hidden(self, actor) -> bool:
        return is_read_only(actor) and rbt_iep_access(self.notes.roster()) == "none"

    def list(self, actor) -> dict:
        home = self.notes.home_classrooms(actor) if signed_in(actor) else []
        rows = []
        for sid, s in self._all().items():
            st = s["student"]
            rows.append({"student_id": sid, "name": f"{st['first_name']} {st['last_initial']}".strip(),
                         "class_codes": s["class_codes"], "teacher_names": s["teacher_names"],
                         "grade": s["grade"], "label": s["label"], "plan_type": s["iep"].get("plan_type"),
                         "iep_goals": len(st["iep_goals"]),
                         "mine": bool(set(s["class_codes"]) & set(home)) or (not signed_in(actor) and actor in
                                                                             self.notes.student_teacher_ids(sid))})
        rows.sort(key=lambda x: (x["class_codes"][:1], x["name"]))
        return {"students": rows, "home_classrooms": home, "default_classroom": self.notes.default_classroom(actor),
                "classrooms": sorted({c for r in rows for c in r["class_codes"]})}

    # ------------------------------------------------------------ one student
    def _week(self, s) -> dt.date:
        if s in (None, ""):
            return week_of(today())
        wk = parse_week(s)
        w = three_weeks()
        if wk > w["next"] or wk < w["current"] - dt.timedelta(days=7 * MAX_WEEKS_BACK):
            raise ApiError(400, "BAD_WEEK")
        return wk

    def detail(self, actor, sid, week: str | None = None) -> dict:
        if not isinstance(sid, str) or not _SID.match(sid):
            raise ApiError(400, "BAD_STUDENT")
        s = self._all().get(sid)
        if s is None:
            raise ApiError(404, "UNKNOWN_STUDENT")
        st, fn = s["student"], s["student"]["first_name"]
        wk = self._week(week)
        hide_iep = self._iep_hidden(actor)
        can_edit = self.planner.may_edit_student(actor, sid)
        if not hide_iep:
            # who, when, which student (internal id), "viewed IEP" - every time; no journal, no IEP
            self.rec.record(actor, "iep_viewed", student_id=sid, record_id=sid, required=True,
                            week=wk.isoformat())
        days = [{"date": (wk + dt.timedelta(days=i)).isoformat(), "sessions": []} for i in range(5)]
        by_date = {d["date"]: d for d in days}
        week_sessions = [x for x in self.planner._sessions(student_id=sid) if x["week"] == wk]
        for x in week_sessions:
            day = by_date.get(x["date"])
            if day is None:                       # a weekend recording: not a school day
                continue
            row = self.notes.session(x["recording_id"], actor)
            a = row.get("processed_analysis") or {}
            day["sessions"].append({
                "recording_id": x["recording_id"], "approved": x["approved"],
                "class_code": row.get("class_code"), "teacher_name": row.get("teacher_name"),
                "timestamp_start": row.get("timestamp_start"), "recording_start": row.get("recording_start"),
                "summary": a.get("general_session_summary") or "",
                "incidents": len(a.get("incident_analysis") or []),
            })
        base = f"planner/students/{sid}/{wk.isoformat()}/"
        summary, versions = self.planner._latest(base + "summary/")
        summary = reinsert_first_name(_public(summary), fn) if summary else None
        if summary and not can_edit:
            summary = {k: v for k, v in summary.items() if k != "parent_email"}
        approved = [x for x in week_sessions if x["approved"]]
        suggestion = suggest(st, approved) if approved else None
        if hide_iep:
            if summary:
                summary = {**summary, "iep_check": []}
            if suggestion:
                suggestion = {**suggestion, "iep_check": []}
        this = week_of(today())
        return {
            "student_id": sid, "name": f"{fn} {st['last_initial']}".strip(), "first_name": fn,
            "class_codes": s["class_codes"], "teacher_names": s["teacher_names"],
            "grade": s["grade"], "label": s["label"], "attendance": s["attendance"],
            "iep": None if hide_iep else {"goals": st["iep_goals"], **s["iep"]},
            "iep_hidden": hide_iep,
            "week": wk.isoformat(), "week_end": (wk + dt.timedelta(days=4)).isoformat(),
            "this_week": this.isoformat(),
            "prev_week": (wk - dt.timedelta(days=7)).isoformat()
            if wk - dt.timedelta(days=7) >= this - dt.timedelta(days=7 * MAX_WEEKS_BACK) else None,
            "next_week": (wk + dt.timedelta(days=7)).isoformat() if wk < this + dt.timedelta(days=7) else None,
            "days": days, "summary": summary, "summary_versions": versions, "suggestion": suggestion,
            "parent_email_hidden": not can_edit,
            "can_edit": can_edit,
            # the AI draft is for this week or earlier (planner.draft_summary)
            "can_draft": can_edit and ai.mode() != "off" and wk <= this,
            "ai_drafts": ai.mode(),
        }

    # ------------------------------------------------------------ teachers
    def teachers(self, actor) -> dict:
        r = self.notes.roster()
        admin = signed_in(actor) and actor.is_admin
        from .people import People, self_edit_access
        allow = People(self.notes).allowlist() if admin else {}   # base file + admins' changes ()
        accounts = (People(self.notes).cache.get().get("accounts") or {}) if admin else {}
        rows, seen = [], set()

        def access(emails):
            """Admins: the sign-in email and app role (the allowlist entry) of a person."""
            signin = next((e for e in emails if e.lower() in allow), emails[0] if emails else None)
            role = allow.get((signin or "").lower())
            return {"sign_in_email": signin, "app_role": "teacher" if role == "reviewer" else (role or "none")}
        # one staff directory, role from each person's own record (): teachers, RBTs (read-only
        # in this app, supervised by the BCBA), BCBA, therapists, office
        for p in staff_directory(r):
            seen.update(e.lower() for e in p["emails"])
            role = p["title"] or "Staff"
            if p["role"] == RBT_ROLE:
                role = "RBT (read-only)"          # as before; the full title is on the staff record
            rows.append({"id": p["id"], "name": p["name"], "role": role, "role_key": p["role"],
                         **({"label": p["label"]} if p.get("label") else {}),
                         "classrooms": p["class_codes"],
                         **({"default_classroom": self.notes.teacher_default_classroom(p["id"])} if p["role"] == TEACHER_ROLE else {}),
                         **({"supervisor": p["supervisor_name"]} if p["role"] == RBT_ROLE and p.get("supervisor_name") else {}),
                         **({"emails": p["emails"], **access(p["emails"])} if admin else {})})
        if admin:
            for email, role in sorted(allow.items()):
                if email in seen:
                    continue
                rows.append({"id": next((a for a, e in accounts.items() if e == email), None), "name": email,
                             "role": {"admin": "Admin", "rbt": "RBT (read-only)"}.get(role, "Teacher"),
                             "classrooms": [], "emails": [email], "allowlist_only": True, **access([email])})
        return {"teachers": rows, "emails_shown": admin, "can_edit_access": admin,
                "self_edit_access": self_edit_access()}

    # ------------------------------------------------------------ settings (admins)
    def settings(self, actor) -> dict:
        if not (signed_in(actor) and actor.is_admin):
            raise ApiError(403, "ADMIN_ONLY")
        r = self.notes.roster()
        codes = r.get("class_codes") or {}
        env = os.environ.get
        from .people import People, self_edit_access
        roles: dict = {}
        for role in People(self.notes).allowlist().values():   # base file + admins' changes ()
            roles[role] = roles.get(role, 0) + 1
        return {
            "iep_access_rbt": rbt_iep_access(r),
            "teacher_defaults": [{"teacher_name": (t or {}).get("teacher_name") or tid,
                                  "default_classroom": self.notes.teacher_default_classroom(tid),
                                  "set_in_roster": bool((t or {}).get("default_class_code"))}
                                 for tid, t in sorted((r.get("teachers") or {}).items()) if isinstance(t, dict)],
            "classrooms": [{"class_code": d, "teacher_name": (e or {}).get("teacher_name"),
                            "has_student": bool(class_students(r, d, e)), "students": len(class_students(r, d, e)),
                            "aliases": [a for a in (e or {}).get("aliases") or [] if isinstance(a, str)],
                            "iep_goals": len((e or {}).get("iep_goals") or [])}
                           for d, e in sorted(codes.items()) if isinstance(e, dict)],
            "roster": {"synthetic": bool((r.get("_meta") or {}).get("synthetic")), "classrooms": len(codes),
                       "students": len(self._all()), "teachers": len(r.get("teachers") or {}),
                       "rbts": len(rbt_records(r)),
                       "staff": sum(1 for p in staff_directory(r) if p["role"] not in (TEACHER_ROLE, RBT_ROLE)),
                       "school": (r.get("_meta") or {}).get("school_name"),
                       "profile": (r.get("_meta") or {}).get("school_profile")},
            "access": {"allowlist_roles": roles, "profile_self_edit_access": self_edit_access()},
            "ai": {"planner_ai": ai.mode(), "model": env("GEMINI_MODEL"), "location": env("GEMINI_LOCATION")},
            "journal": {"mode": env("JOURNAL_MODE") or "off", "dataset": env("JOURNAL_DATASET"),
                        "location": env("JOURNAL_LOCATION")},
            "bug_reports": {"repo": env("GITHUB_ISSUES_REPO")},
            "school_timezone": str(self.planner_tz()),
            "build": self.build(),
            # set at deploy by infra/config.sh (bucket lifecycle and log bucket); shown, not changed here
            "retention": {"audio_days": RETENTION["audio_days"], "transcripts_and_drafts_days": RETENTION["derived_days"],
                          "logs_days": RETENTION["log_days"], "planner": "kept to pilot end"},
        }

    @staticmethod
    def build() -> dict:
        """The commit and Cloud Run revision this instance runs (deploy_review.sh sets APP_COMMIT)."""
        from .bug_reports import build_info
        info = build_info()
        commit = os.environ.get("APP_COMMIT") or info.get("commit")
        return {"commit": commit, "revision": info.get("revision")}

    @staticmethod
    def planner_tz():
        from .planner import SCHOOL_TZ
        return SCHOOL_TZ
