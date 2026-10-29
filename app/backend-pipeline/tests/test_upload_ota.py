"""OTA event log (POST /v1/device/ota): device auth, allow-listed events, create-only object."""
from __future__ import annotations

import json

from test_upload_media import DEVICE, TOKEN, api  # noqa: F401 - fixture


def post(c, body, token=TOKEN):
    return c.post("/v1/device/ota", json=body, headers={"Authorization": f"Bearer {token}"})


def good(**kw):
    body = {"device_id": DEVICE, "event": "blocked", "from_version": "0.8.0",
            "to_version": "0.8.1", "detail": "battery"}
    body.update(kw)
    return body


def test_needs_device_auth(api):  # noqa: F811
    c, _, _ = api
    assert post(c, good(), token="nope").status_code == 401


def test_stores_event(api):  # noqa: F811
    c, store, _ = api
    r = post(c, good())
    assert r.status_code == 200 and r.get_json()["ok"] is True
    names = [n for n in store["objs"] if n.startswith(f"control/ota-log/{DEVICE}/")]
    assert len(names) == 1
    saved = json.loads(store["objs"][names[0]]["data"])
    assert saved["event"] == "blocked" and saved["detail"] == "battery"
    assert saved["from_version"] == "0.8.0" and saved["to_version"] == "0.8.1"


def test_rejects_bad_event_and_version(api):  # noqa: F811
    c, store, _ = api
    assert post(c, good(event="rm -rf")).status_code == 400
    assert post(c, good(from_version="yesterday")).status_code == 400
    assert post(c, good(detail="Battery Low")).status_code == 400
    assert store["objs"] == {}


def test_confirmed_boot_is_logged(api):  # noqa: F811
    c, store, _ = api
    r = post(c, good(event="confirmed", to_version="0.8.0", detail="boot"))
    assert r.status_code == 200
    saved = json.loads(next(iter(store["objs"].values()))["data"])
    assert saved["event"] == "confirmed" and saved["detail"] == "boot"


def test_second_event_does_not_overwrite(api):  # noqa: F811
    c, store, _ = api
    assert post(c, good(event="check", detail="current")).status_code == 200
    assert post(c, good(event="rollback", detail="timeout")).status_code == 200
    assert len(store["objs"]) == 2
