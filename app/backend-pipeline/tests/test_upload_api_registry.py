"""upload-api device registry on a cold start ().

Cloud Run instances start with time.monotonic() near zero. The registry cache
used to start at 0.0, so for the first ~30 s of every new instance the registry
was never read and every device got a 401 (a real device upload).
Google libraries are stubbed; nothing reaches the cloud.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

UPLOAD_API = Path(__file__).resolve().parents[2] / "upload-api"
TOKEN = "tok-" + "x" * 40
DEVICE = "a1b2c3d4e5f6"


class FakeBlob:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def download_as_bytes(self):
        self.store["reads"] += 1
        if self.store["fail"]:
            raise OSError("storage hiccup")
        return json.dumps(self.store["registry"]).encode()


class FakeBucket:
    def __init__(self, store):
        self.store = store

    def blob(self, name):
        return FakeBlob(self.store, name)


@pytest.fixture()
def api(monkeypatch):
    store = {"reads": 0, "fail": False, "registry": {"devices": {
        DEVICE: {"token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest(), "revoked": False}}}}
    storage = types.ModuleType("google.cloud.storage")
    storage.Client = lambda *a, **k: types.SimpleNamespace(bucket=lambda name: FakeBucket(store))
    cloud = types.ModuleType("google.cloud")
    cloud.storage = storage
    auth = types.ModuleType("google.auth")
    auth.default = lambda scopes=None: (types.SimpleNamespace(valid=True, token="t", service_account_email="sa@x"), "p")
    transport = types.ModuleType("google.auth.transport")
    requests_mod = types.ModuleType("google.auth.transport.requests")
    requests_mod.Request = object
    for name, mod in {"google.cloud": cloud, "google.cloud.storage": storage, "google.auth": auth,
                      "google.auth.transport": transport, "google.auth.transport.requests": requests_mod}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    google = importlib.import_module("google")
    monkeypatch.setattr(google, "auth", auth, raising=False)
    monkeypatch.setattr(google, "cloud", cloud, raising=False)
    auth.transport = transport
    transport.requests = requests_mod
    monkeypatch.setenv("BUCKET", "test-bucket")
    monkeypatch.syspath_prepend(str(UPLOAD_API))
    for m in ("main", "safelog"):
        sys.modules.pop(m, None)
    main = importlib.import_module("main")
    clock = {"now": 3.0}  # a fresh instance: a few seconds since the sandbox booted
    monkeypatch.setattr(main.time, "monotonic", lambda: clock["now"])
    yield main, store, clock
    sys.modules.pop("main", None)


def post_confirm(main, token=TOKEN):
    with main.app.test_client() as c:
        return c.post("/v1/confirm", json={"device_id": DEVICE, "object": "x"},
                      headers={"Authorization": f"Bearer {token}"})


def test_first_request_on_a_fresh_instance_reads_the_registry(api):
    main, store, _ = api
    r = post_confirm(main)
    # authorized: it gets past auth to object validation (400 BAD_OBJECT), not 401
    assert r.status_code == 400 and r.get_json() == {"error": "BAD_OBJECT"}
    assert store["reads"] == 1


def test_registry_is_cached_then_re_read(api):
    main, store, clock = api
    post_confirm(main)
    clock["now"] += 10
    post_confirm(main)
    assert store["reads"] == 1
    clock["now"] += main.TOKEN_CACHE_SECONDS
    post_confirm(main)
    assert store["reads"] == 2


def test_failed_read_is_not_cached(api, capsys):
    main, store, _ = api
    store["fail"] = True
    assert post_confirm(main).status_code == 401  # fail closed
    store["fail"] = False
    assert post_confirm(main).status_code == 400  # next request reads again, same second
    assert store["reads"] == 2
    out = capsys.readouterr().out
    assert '"event":"registry_unavailable"' in out and '"reason":"UNKNOWN_DEVICE"' in out


def test_wrong_token_is_401_with_reason_and_no_secret_in_logs(api, capsys):
    main, _, _ = api
    r = post_confirm(main, token="wrong-" + "y" * 40)
    assert r.status_code == 401 and r.get_json() == {"error": "UNAUTHORIZED"}
    out = capsys.readouterr().out
    assert '"reason":"TOKEN_MISMATCH"' in out
    assert TOKEN not in out and "wrong-" not in out
    assert hashlib.sha256(TOKEN.encode()).hexdigest() not in out


def test_revoked_device_is_401(api, capsys):
    main, store, _ = api
    store["registry"]["devices"][DEVICE]["revoked"] = True
    assert post_confirm(main).status_code == 401
    assert '"reason":"REVOKED"' in capsys.readouterr().out
