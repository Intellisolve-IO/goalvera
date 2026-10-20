"""Recorder banner school name (POST /v1/device/school): device auth, newest record of the
active profile, legacy records = the legacy school, default when unset or storage fails."""
from __future__ import annotations

import json
import types

import pytest

from test_upload_media import DEVICE, TOKEN, api  # noqa: F401 - fixture


def ask(c, token=TOKEN):
    return c.post("/v1/device/school", json={"device_id": DEVICE},
                  headers={"Authorization": f"Bearer {token}"})


def rec(store, name, **kw):
    store["objs"][f"branding/{name}.json"] = {"data": json.dumps(kw).encode(), "ct": "application/json"}


@pytest.fixture()
def bucket_with_list(api, monkeypatch):  # noqa: F811
    c, store, _ = api
    import main
    b = main._bucket
    b.list_blobs = lambda prefix="": [types.SimpleNamespace(name=n) for n in list(store["objs"]) if n.startswith(prefix)]
    import school  # noqa: F401
    return c, store, b


def test_needs_device_auth(bucket_with_list):
    c, _, _ = bucket_with_list
    assert ask(c, token="nope" * 10).status_code == 401


def test_default_when_never_set(bucket_with_list):
    c, _, _ = bucket_with_list
    j = ask(c).get_json()
    assert j == {"school_name": "Sample Elementary School", "profile": "sample-school", "is_default": True}


def test_newest_record_of_active_profile_wins(bucket_with_list):
    c, store, _ = bucket_with_list
    rec(store, "20261010T000000Z_a", profile="sample-school", school_name="Old Name")
    rec(store, "20261012T000000Z_b", profile="sample-school", school_name="  Maplewood Learning Academy ")
    rec(store, "20261013T000000Z_c", school_name="Legacy AP Name")   # no profile: the legacy school's
    j = ask(c).get_json()
    assert j["school_name"] == "Maplewood Learning Academy" and j["is_default"] is False


def test_legacy_records_belong_to_legacy_profile(bucket_with_list, monkeypatch):
    c, store, _ = bucket_with_list
    monkeypatch.setenv("SCHOOL_PROFILE", "legacy-school")
    rec(store, "20261013T000000Z_c", school_name="Legacy School Academy")
    assert ask(c).get_json()["school_name"] == "Legacy School Academy"


def test_storage_failure_falls_back_to_default(bucket_with_list):
    c, _, b = bucket_with_list
    def boom(prefix=""):
        raise RuntimeError("x")
    b.list_blobs = boom
    r = ask(c)
    assert r.status_code == 200 and r.get_json()["school_name"] == "Sample Elementary School"
