"""My profile + admins' role / email changes.

Everyone signed in (teacher, RBT, admin) edits their own display name, preferred name, phone, default
classroom (only one of theirs), title and credential type; role and email are read-only unless the one
flag PROFILE_SELF_EDIT_ACCESS is on. Admins change anyone's app role / sign-in email (Teachers tab); the
allowlist entry moves with the email in the same record. Every change is journaled with field names only.

  python -m pytest app/backend-pipeline/tests/test_people.py -q
"""
from __future__ import annotations

import json

import pytest

from test_hosted_auth import ADMIN, TEACHER_EMAIL, call, token  # noqa: F401
from test_planner import RBT_EMAIL, ART_TEACHER, authn, hosted, journal, pinned_today, pstore, who  # noqa: F401
from review_api import people as P
from review_api.bug_reports import never_values
from review_api.journal import Recorder
from review_api.notes import ApiError, Notes
from review_api.people import People, apply_access, state_for
from review_api.store import Conflict, LocalStore


@pytest.fixture()
def env(pstore, journal):  # noqa: F811
    notes = Notes(pstore, roster_ttl=0, recorder=Recorder(journal))
    return type("E", (), {"store": pstore, "notes": notes, "journal": journal, "people": People(notes)})


def uid(email):
    return "uid" + email.split("@")[0].replace("-", "")


def rows(env, action):
    return [r for r in env.journal.rows() if r["action"] == action]


# ---------------------------------------------------------------- My profile

def test_teacher_sees_own_profile_with_role_and_email_read_only(env, authn):
    me = env.people.me(who(authn, TEACHER_EMAIL))
    assert me["person_id"] == "usr_demo_sarah" and me["in_roster"]
    assert me["display_name"] == "Sarah Jenkins" and me["email"] == TEACHER_EMAIL
    assert me["role_label"] == "Teacher" and me["can_edit_access"] is False
    assert set(me["classrooms"]) == {"RM12", "Reading 3A", "Math 2B", "Circle Time"}


def test_teacher_edits_own_profile_journaled_without_values(env, authn):
    t = who(authn, TEACHER_EMAIL)
    out = env.people.save_me(t, {"display_name": "Sarah J.", "preferred_name": "Sadie", "phone": "(404) 555-0100",
                                 "default_classroom": "Math 2B", "title": "Lead teacher",
                                 "credential_type": "Special education certificate"})
    assert out["display_name"] == "Sarah J." and out["phone"] == "(404) 555-0100"
    assert out["default_classroom"] == "Math 2B"
    assert sorted(out["changed"]) == sorted(P.SELF_FIELDS)
    # the default classroom feeds every classroom list
    assert env.notes.default_classroom(t) == "Math 2B"
    (j,) = rows(env, "profile_updated")
    assert j["record_type"] == "person" and j["record_id"] == "usr_demo_sarah"
    blob = json.dumps(j)
    for v in ("555-0100", "Sadie", "Sarah J.", "Lead teacher", "Math 2B", TEACHER_EMAIL):
        assert v not in blob
    assert set(j["details"]["fields"].split(".")) == set(P.SELF_FIELDS)
    # stored as one create-only record; the roster file itself is untouched
    assert env.store.list("people/") == ["people/state-00000001.json"]
    assert env.store.read_json("config/demo_roster.json")["teachers"]["usr_demo_sarah"].get("phone") is None


def test_nothing_changed_writes_nothing(env, authn):
    t = who(authn, TEACHER_EMAIL)
    env.people.save_me(t, {"phone": "404 555 0101"})
    out = env.people.save_me(t, {"phone": "404 555 0101"})
    assert out["changed"] == []
    assert len(env.store.list("people/")) == 1 and len(rows(env, "profile_updated")) == 1


def test_clearing_a_field(env, authn):
    t = who(authn, TEACHER_EMAIL)
    env.people.save_me(t, {"preferred_name": "Sadie"})
    out = env.people.save_me(t, {"preferred_name": ""})
    assert out["preferred_name"] == "" and out["changed"] == ["preferred_name"]


@pytest.mark.parametrize("body,code", [
    ({"phone": "call me"}, "BAD_PHONE"),
    ({"phone": "12"}, "BAD_PHONE"),
    ({"display_name": "x" * 81}, "BAD_DISPLAY_NAME"),
    ({"display_name": "<script>"}, "BAD_DISPLAY_NAME"),
    ({"preferred_name": "me@x.org"}, "BAD_PREFERRED_NAME"),
    ({"credential_type": "Wizard"}, "BAD_CREDENTIAL_TYPE"),
    ({"default_classroom": "Art 9Z"}, "CLASSROOM_NOT_ALLOWED"),     # someone else's classroom
    ({"default_classroom": "Nowhere"}, "CLASSROOM_NOT_ALLOWED"),
    ({"role": "admin"}, "ASK_AN_ADMIN"),
    ({"email": "new@example.org"}, "ASK_AN_ADMIN"),
])
def test_bad_self_edits_refused(env, authn, body, code):
    with pytest.raises(ApiError) as e:
        env.people.save_me(who(authn, TEACHER_EMAIL), body)
    assert e.value.code == code
    assert env.store.list("people/") == []


def test_rbt_edits_own_profile_default_within_own_classrooms(env, authn):
    r = who(authn, RBT_EMAIL)
    me = env.people.me(r)
    assert me["person_id"] == "rbt_demo_rm12" and me["classrooms"] == ["RM12"] and me["role_label"] == "RBT (read-only)"
    out = env.people.save_me(r, {"preferred_name": "Robin", "default_classroom": "RM12"})
    assert out["preferred_name"] == "Robin"
    with pytest.raises(ApiError) as e:
        env.people.save_me(r, {"default_classroom": "Math 2B"})
    assert e.value.code == "CLASSROOM_NOT_ALLOWED"
    assert env.notes.default_classroom(r) == "RM12"


def test_admin_not_in_roster_gets_an_account_id_and_a_default_classroom(env, authn):
    a = who(authn, ADMIN)
    assert env.people.me(a)["person_id"] is None and env.notes.default_classroom(a) is None
    out = env.people.save_me(a, {"display_name": "Alex H", "default_classroom": "Art 9Z"})
    assert out["person_id"].startswith("acct_") and out["display_name"] == "Alex H"
    assert env.notes.default_classroom(a) == "Art 9Z"
    (j,) = rows(env, "profile_updated")
    assert j["record_id"] == out["person_id"] and "@" not in json.dumps(j)


def test_display_and_preferred_names_join_the_never_to_ai_lists(env, authn):
    env.people.save_me(who(authn, TEACHER_EMAIL), {"display_name": "Sadie Jenks", "preferred_name": "Sadie"})
    staff = never_values(env.notes.roster())["staff"]
    assert {"Sadie", "Jenks"} <= set(staff)
    from review_api.planner import Planner
    assert "Sadie" in Planner(env.notes)._teacher_names()


# ---------------------------------------------------------------- admin access changes

def test_admin_changes_teacher_email_moves_allowlist_and_roster_in_one_record(env, authn):
    a = who(authn, ADMIN)
    out = env.people.change_access(a, "usr_demo_sarah", {"email": "Sarah.New@Example.org"})
    assert out["changed"] == ["email"]
    (name,) = env.store.list("people/")
    st = env.store.read_json(name)
    assert st["access"] == {TEACHER_EMAIL: None, "sarah.new@example.org": "teacher"}
    assert st["emails"] == {"usr_demo_sarah": ["sarah.new@example.org"]}
    # sign-in follows at once (same snapshot): the old address is out, the new one is the same teacher
    assert authn.allowlist.role(TEACHER_EMAIL) is None
    assert authn.allowlist.role("sarah.new@example.org") == "teacher"
    assert authn.roster.teacher_ids_for("sarah.new@example.org") == {"usr_demo_sarah"}
    assert authn.roster.teacher_ids_for(TEACHER_EMAIL) == frozenset()
    (j,) = rows(env, "access_changed")
    # Ops (): an email change logs both addresses and the (unchanged) old / new role
    assert j["details"] == {"fields": "email", "app_role": "teacher", "old_role": "teacher", "new_role": "teacher",
                            "account_email": TEACHER_EMAIL, "old_email": TEACHER_EMAIL,
                            "new_email": "sarah.new@example.org"}


def test_admin_changes_role_and_the_scope_follows(env, authn):
    a = who(authn, ADMIN)
    env.people.change_access(a, "usr_demo_sarah", {"role": "rbt"})
    assert authn.allowlist.role(TEACHER_EMAIL) == "rbt"
    t = who(authn, TEACHER_EMAIL)
    assert t.role == "rbt" and t.teacher_ids == frozenset()
    env.people.change_access(a, "usr_demo_sarah", {"role": "none"})
    with pytest.raises(ApiError) as e:
        who(authn, TEACHER_EMAIL)
    assert e.value.code == "NOT_APPROVED"
    assert [r["details"]["app_role"] for r in rows(env, "access_changed")] == ["rbt", "none"]


def test_admin_gives_a_person_without_email_access(env, authn):
    r = env.store.read_json("config/demo_roster.json")
    r["teachers"]["usr_new"] = {"teacher_name": "Pat Newman", "emails": []}
    env.store.write_json("config/demo_roster.json", r)
    a = who(authn, ADMIN)
    with pytest.raises(ApiError) as e:
        env.people.change_access(a, "usr_new", {"role": "teacher"})
    assert e.value.code == "EMAIL_REQUIRED"
    env.people.change_access(a, "usr_new", {"role": "teacher", "email": "pat@example.org"})
    authn.roster._at = None
    assert authn.allowlist.role("pat@example.org") == "teacher"
    assert authn.roster.teacher_ids_for("pat@example.org") == {"usr_new"}


def test_allowlist_only_person_changed_by_email(env, authn):
    env.store.write_json("config/review_allowlist.json", {"users": [
        {"email": ADMIN, "role": "admin"}, {"email": "guest@example.org", "role": "teacher"}]})
    authn.allowlist._at = None
    a = who(authn, ADMIN)
    out = env.people.change_access(a, None, {"current_email": "guest@example.org", "email": "guest2@example.org",
                                            "role": "admin"})
    assert out["person"].startswith("acct_") and set(out["changed"]) == {"email", "role"}
    assert authn.allowlist.role("guest@example.org") is None
    assert authn.allowlist.role("guest2@example.org") == "admin"
    rows_ = Students_rows(env, a)
    assert any(r.get("sign_in_email") == "guest2@example.org" and r["app_role"] == "admin" for r in rows_)


def Students_rows(env, actor):
    from review_api.planner import Planner
    from review_api.students import Students
    return Students(env.notes, Planner(env.notes)).teachers(actor)["teachers"]


def test_teachers_tab_shows_sign_in_and_app_role_to_admins_only(env, authn):
    rows_ = Students_rows(env, who(authn, ADMIN))
    sarah = next(r for r in rows_ if r["id"] == "usr_demo_sarah")
    assert sarah["sign_in_email"] == TEACHER_EMAIL and sarah["app_role"] == "teacher"
    t_rows = Students_rows(env, who(authn, TEACHER_EMAIL))
    assert all("app_role" not in r and "emails" not in r for r in t_rows)


@pytest.mark.parametrize("body,code", [
    ({"email": "not-an-email"}, "BAD_EMAIL"),
    ({"email": ART_TEACHER}, "EMAIL_IN_USE"),
    ({"email": ADMIN}, "EMAIL_IN_USE"),
    ({"role": "superuser"}, "BAD_ROLE"),
])
def test_bad_access_changes_refused(env, authn, body, code):
    with pytest.raises(ApiError) as e:
        env.people.change_access(who(authn, ADMIN), "usr_demo_sarah", body)
    assert e.value.code == code
    assert env.store.list("people/") == []


def test_only_admins_change_access(env, authn):
    for email in (TEACHER_EMAIL, RBT_EMAIL):
        with pytest.raises(ApiError) as e:
            env.people.change_access(who(authn, email), "usr_demo_sarah", {"role": "admin"})
        assert e.value.code == "ADMIN_ONLY"
    with pytest.raises(ApiError) as e:
        env.people.change_access(who(authn, ADMIN), "usr_nobody", {"role": "admin"})
    assert e.value.code == "NOT_FOUND"


def test_the_last_admin_stays(env, authn):
    a = who(authn, ADMIN)
    out = env.people.save_me(a, {"display_name": "Alex"})
    with pytest.raises(ApiError) as e:
        env.people.change_access(a, out["person_id"], {"role": "teacher"})
    assert e.value.code == "LAST_ADMIN"
    env.people.change_access(a, "usr_demo_sarah", {"role": "admin"})
    env.people.change_access(a, out["person_id"], {"role": "teacher"})      # now fine: Sarah is an admin
    assert authn.allowlist.role(ADMIN) == "teacher"


# ---------------------------------------------------------------- the flag

def test_flag_on_lets_people_change_their_own_email(env, authn, monkeypatch):
    monkeypatch.setenv("PROFILE_SELF_EDIT_ACCESS", "on")
    t = who(authn, TEACHER_EMAIL)
    assert env.people.me(t)["can_edit_access"] is True
    out = env.people.save_me(t, {"email": "sarah2@example.org", "phone": "404-555-0199"})
    assert set(out["changed"]) == {"phone", "email"}
    assert authn.allowlist.role("sarah2@example.org") == "teacher" and authn.allowlist.role(TEACHER_EMAIL) is None


def test_flag_default_is_off(monkeypatch):
    monkeypatch.delenv("PROFILE_SELF_EDIT_ACCESS", raising=False)
    assert P.self_edit_access() is False
    monkeypatch.setenv("PROFILE_SELF_EDIT_ACCESS", "on")
    assert P.self_edit_access() is True


# ---------------------------------------------------------------- storage

def test_two_saves_at_once_both_land(env, authn):
    """Record n+1 is create-only: the second writer re-reads and re-applies its own change."""
    t, a = who(authn, TEACHER_EMAIL), who(authn, ADMIN)
    env.people.save_me(t, {"phone": "404 555 0101"})
    # another instance writes record 2 behind this instance's cache
    other = state_for(env.store)
    stale = dict(other.get())
    env.store.write_json("people/state-00000002.json", {**stale, "version": 2,
                         "profiles": {**stale["profiles"], "usr_demo_sarah": {"phone": "404 555 0102"}}}, create_only=True)
    env.people.save_me(a, {"display_name": "Alex"})
    assert env.store.list("people/")[-1] == "people/state-00000003.json"
    st = env.store.read_json("people/state-00000003.json")
    assert st["profiles"]["usr_demo_sarah"]["phone"] == "404 555 0102"       # the other save kept
    assert any(p.get("display_name") == "Alex" for p in st["profiles"].values())


def test_records_are_never_overwritten(tmp_path):
    s = LocalStore(tmp_path)
    ps = P.PeopleState(s)
    ps.write({**P.empty_state(), "version": 1})
    with pytest.raises(Conflict):
        ps.write({**P.empty_state(), "version": 1})


def test_apply_access_removes_and_adds():
    assert apply_access({"a@x.org": "admin", "b@x.org": "reviewer"}, {"access": {"b@x.org": None, "c@x.org": "rbt"}}) \
        == {"a@x.org": "admin", "c@x.org": "rbt"}


def test_unreadable_people_prefix_means_nothing_saved(tmp_path):
    class Forbidden(Exception):
        pass

    class S(LocalStore):
        def list(self, prefix):
            raise Forbidden()
    assert P.PeopleState(S(tmp_path)).get() == P.empty_state()


# ---------------------------------------------------------------- HTTP

def test_http_my_profile_for_teacher_rbt_admin(hosted, authn, journal):  # noqa: F811
    for email in (TEACHER_EMAIL, RBT_EMAIL, ADMIN):
        tok = token(email, uid=uid(email))
        st, body, _ = call(hosted, "/api/profile/me", tok=tok)
        assert st == 200 and json.loads(body)["email"] == email
        st, body, _ = call(hosted, "/api/profile/me", tok=tok, method="POST", body={"preferred_name": "Sam"},
                           headers={"Origin": "http://localhost:5173"})
        assert st == 200, body                     # RBTs too: their own profile only
        assert json.loads(body)["changed"] == ["preferred_name"]
    st, body, _ = call(hosted, "/api/me", tok=token(ADMIN, uid=uid(ADMIN)))
    assert st == 200
    # an RBT still can't write anything else
    st, body, _ = call(hosted, "/api/admin/people/access", tok=token(RBT_EMAIL, uid=uid(RBT_EMAIL)), method="POST",
                       body={"person": "usr_demo_sarah", "role": "admin"}, headers={"Origin": "http://localhost:5173"})
    assert st == 403
    st, body, _ = call(hosted, "/api/admin/people/access", tok=token(TEACHER_EMAIL, uid=uid(TEACHER_EMAIL)),
                       method="POST", body={"person": "usr_demo_sarah", "role": "admin"},
                       headers={"Origin": "http://localhost:5173"})
    assert st == 403 and json.loads(body)["error"] == "ADMIN_ONLY"
    st, body, _ = call(hosted, "/api/admin/people/access", tok=token(ADMIN, uid=uid(ADMIN)), method="POST",
                       body={"person": "usr_demo_sarah", "role": "rbt"}, headers={"Origin": "http://localhost:5173"})
    assert st == 200 and json.loads(body)["changed"] == ["role"]
    assert {r["action"] for r in journal.rows()} >= {"profile_updated", "access_changed"}


def test_http_display_name_in_me(hosted):  # noqa: F811
    tok = token(TEACHER_EMAIL, uid=uid(TEACHER_EMAIL))
    call(hosted, "/api/profile/me", tok=tok, method="POST", body={"display_name": "Sarah J."},
         headers={"Origin": "http://localhost:5173"})
    st, body, _ = call(hosted, "/api/me", tok=tok)
    assert json.loads(body)["display_name"] == "Sarah J."


def test_http_signed_out_gets_401(hosted):  # noqa: F811
    assert call(hosted, "/api/profile/me")[0] == 401
