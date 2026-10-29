"""Teacher Notes QR upload (tasks#112): token mint, expiry, single use, limits.
Google libraries are stubbed; nothing reaches the cloud."""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

UPLOAD_API = Path(__file__).resolve().parents[2] / "upload-api"
TOKEN = "tok-" + "x" * 40
DEVICE = "14c19fd1a744"
STEM = "20261019_140000_ROOM12"


class PreconditionFailed(Exception):
    pass


class Blob:
    def __init__(self, store, name):
        self.store, self.name = store, name
        o = store["objs"].get(name) or {}
        self.content_type, self.size = o.get("ct"), o.get("size")

    def download_as_bytes(self):
        if self.name == "control/devices.json":
            return json.dumps(store_registry(self.store)).encode()
        return self.store["objs"][self.name]["data"]

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        if if_generation_match == 0 and self.name in self.store["objs"]:
            raise PreconditionFailed()
        data = data.encode() if isinstance(data, str) else data
        self.store["objs"][self.name] = {"data": data, "ct": content_type, "size": len(data)}

    def generate_signed_url(self, **kw):
        self.store["signed"].append((self.name, kw["headers"]))
        return f"https://storage.example/{self.name}?sig"


def store_registry(store):
    return {"devices": {DEVICE: {"token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest()}}}


class Bucket:
    def __init__(self, store):
        self.store = store

    def blob(self, name):
        return Blob(self.store, name)

    def get_blob(self, name):
        return Blob(self.store, name) if name in self.store["objs"] else None


@pytest.fixture()
def api(monkeypatch):
    store = {"objs": {}, "signed": []}
    storage = types.ModuleType("google.cloud.storage")
    storage.Client = lambda *a, **k: types.SimpleNamespace(bucket=lambda name: Bucket(store))
    cloud = types.ModuleType("google.cloud")
    cloud.storage = storage
    auth = types.ModuleType("google.auth")
    auth.default = lambda scopes=None: (types.SimpleNamespace(valid=True, token="t", service_account_email="sa@x"), "p")
    transport = types.ModuleType("google.auth.transport")
    requests_mod = types.ModuleType("google.auth.transport.requests")
    requests_mod.Request = object
    core = types.ModuleType("google.api_core")
    exc = types.ModuleType("google.api_core.exceptions")
    exc.PreconditionFailed = PreconditionFailed
    core.exceptions = exc
    for name, mod in {"google.cloud": cloud, "google.cloud.storage": storage, "google.auth": auth,
                      "google.auth.transport": transport, "google.auth.transport.requests": requests_mod,
                      "google.api_core": core, "google.api_core.exceptions": exc}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    google = importlib.import_module("google")
    for k, v in (("auth", auth), ("cloud", cloud), ("api_core", core)):
        monkeypatch.setattr(google, k, v, raising=False)
    auth.transport = transport
    transport.requests = requests_mod
    monkeypatch.setenv("BUCKET", "test-bucket")
    monkeypatch.syspath_prepend(str(UPLOAD_API))
    for m in ("main", "safelog", "media", "school", "ota_log"):
        sys.modules.pop(m, None)
    main = importlib.import_module("main")
    media = sys.modules["media"]
    clock = {"now": dt.datetime(2026, 10, 19, 18, 0, tzinfo=dt.timezone.utc)}
    monkeypatch.setattr(media, "_now", lambda: clock["now"])
    yield main.app.test_client(), store, clock
    for m in ("main", "media", "school", "ota_log"):
        sys.modules.pop(m, None)


def mint(c, token=TOKEN, stem=STEM):
    return c.post("/v1/media/token", json={"device_id": DEVICE, "recording": stem},
                  headers={"Authorization": f"Bearer {token}"})


def tok_of(r):
    return r.get_json()["url"].rsplit("/", 1)[1]


def put(store, obj, ct, size):
    store["objs"][obj] = {"data": b"x" * size, "ct": ct, "size": size}


def test_device_auth_required(api):
    c, _, _ = api
    assert mint(c, token="wrong" + "y" * 40).status_code == 401
    assert mint(c, stem="../etc").status_code == 400


def test_token_is_random_hashed_and_tied_to_the_recording(api, capsys):
    c, store, _ = api
    r = mint(c)
    assert r.status_code == 200 and r.get_json()["ttl_seconds"] == 300
    t = tok_of(r)
    assert len(t) == 32 and r.get_json()["url"].startswith("http")
    rec = json.loads(store["objs"][f"media_tokens/{hashlib.sha256(t.encode()).hexdigest()}.json"]["data"])
    assert rec["recording_id"] == f"rec_{DEVICE}_{STEM}"
    assert t not in json.dumps(store["objs"], default=str) and t not in capsys.readouterr().out
    assert tok_of(mint(c)) != t


def test_page_needs_no_sign_in(api):
    c, _, _ = api
    r = c.get("/u/" + tok_of(mint(c)))
    assert r.status_code == 200 and b"Upload this photo?" in r.data and r.headers["Cache-Control"] == "no-store"


def test_photo_upload_end_to_end_then_single_use(api):
    c, store, _ = api
    t = tok_of(mint(c))
    assert c.get(f"/v1/media/{t}").get_json()["state"] == "open"
    r = c.post(f"/v1/media/{t}/start", json={"kind": "photo", "content_type": "image/jpeg", "size": 1234})
    assert r.status_code == 200
    j = r.get_json()
    assert j["object"].startswith(f"media/rec_{DEVICE}_{STEM}/m") and j["object"].endswith(".jpg")
    assert j["headers"]["x-goog-if-generation-match"] == "0"
    assert j["headers"]["x-goog-content-length-range"] == "1234,1234"
    assert c.post(f"/v1/media/{t}/done").status_code == 409          # nothing uploaded yet
    put(store, j["object"], "image/jpeg", 1234)
    d = c.post(f"/v1/media/{t}/done")
    assert d.status_code == 200 and d.get_json()["kind"] == "photo"
    meta = json.loads(store["objs"][j["object"].rsplit(".", 1)[0] + ".json"]["data"])
    assert meta["recording_id"] == f"rec_{DEVICE}_{STEM}" and meta["kind"] == "photo"
    # spent: a second photo with the same QR is refused
    assert c.get(f"/v1/media/{t}").get_json()["state"] == "used"
    r2 = c.post(f"/v1/media/{t}/start", json={"kind": "photo", "content_type": "image/jpeg", "size": 10})
    assert r2.status_code == 410 and r2.get_json()["error"] == "TOKEN_USED"
    assert c.post(f"/v1/media/{t}/done").status_code == 410


def test_expired_token_is_rejected(api):
    c, _, clock = api
    t = tok_of(mint(c))
    clock["now"] += dt.timedelta(seconds=301)
    assert c.get(f"/v1/media/{t}").get_json()["state"] == "expired"
    r = c.post(f"/v1/media/{t}/start", json={"kind": "photo", "content_type": "image/jpeg", "size": 10})
    assert r.status_code == 410 and r.get_json()["error"] == "TOKEN_EXPIRED"
    assert c.post(f"/v1/media/{t}/done").status_code == 410


def test_upload_started_in_time_may_finish_after_expiry(api):
    c, store, clock = api
    t = tok_of(mint(c))
    j = c.post(f"/v1/media/{t}/start", json={"kind": "video", "content_type": "video/mp4",
                                            "size": 5000, "duration_s": 28.4}).get_json()
    put(store, j["object"], "video/mp4", 5000)
    clock["now"] += dt.timedelta(seconds=400)
    assert c.post(f"/v1/media/{t}/done").get_json()["kind"] == "video"


def test_video_limits(api):
    c, _, _ = api
    t = tok_of(mint(c))
    long = c.post(f"/v1/media/{t}/start", json={"kind": "video", "content_type": "video/quicktime",
                                               "size": 100, "duration_s": 45})
    assert long.status_code == 400 and long.get_json()["error"] == "TOO_LONG"
    big = c.post(f"/v1/media/{t}/start", json={"kind": "video", "content_type": "video/mp4",
                                              "size": 10**10, "duration_s": 10})
    assert big.get_json()["error"] == "TOO_LARGE"
    bad = c.post(f"/v1/media/{t}/start", json={"kind": "photo", "content_type": "text/html", "size": 10})
    assert bad.get_json()["error"] == "BAD_TYPE"


def test_unknown_token(api):
    c, _, _ = api
    assert c.get("/v1/media/" + "A" * 32).status_code == 404
    assert c.post("/v1/media/bad/start", json={}).status_code == 404
