"""Phone upload API: teacher/admin Identity Platform tokens, web/ prefix, confirm-then-delete.

Google Cloud is stubbed. ID tokens are unsigned emulator tokens (IDP_EMULATOR=1), plus one
check that the real RS256 verifier agrees with the review service.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import importlib
import json
import sys
import time
import types
from pathlib import Path

import pytest

UPLOAD_API = Path(__file__).resolve().parents[2] / "upload-api"
PROJECT = "example-project"
TEACHER, ADMIN, RBT, REVIEWER = "teacher@example.org", "admin@example.org", "rbt@example.org", "reviewer@example.org"
KMS = "projects/example-project/locations/us-east1/keyRings/goalvera/cryptoKeys/recordings"


class PreconditionFailed(Exception):
    pass


def unsigned(email, uid, *, verified=True, provider="password", aud=PROJECT):
    now = int(time.time())
    claims = {"iss": f"https://securetoken.google.com/{aud}", "aud": aud, "sub": uid,
              "iat": now, "exp": now + 3600, "auth_time": now - 5, "email": email,
              "email_verified": verified, "firebase": {"sign_in_provider": provider}}

    def b(doc):
        return base64.urlsafe_b64encode(json.dumps(doc).encode()).rstrip(b"=").decode()

    return f"{b({'alg': 'none', 'typ': 'JWT'})}.{b(claims)}."


class Blob:
    def __init__(self, store, name):
        self.store, self.name = store, name
        o = store["objs"].get(name) or {}
        self.size = o.get("size")
        self.md5_hash = o.get("md5")
        self.crc32c = o.get("crc32c")
        self.generation = o.get("generation", 1)
        self.kms_key_name = o.get("kms")
        self.time_created = dt.datetime(2026, 11, 2, tzinfo=dt.timezone.utc)

    def download_as_bytes(self):
        if self.name not in self.store["objs"]:
            raise FileNotFoundError(self.name)
        return self.store["objs"][self.name]["data"]

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        if if_generation_match == 0 and self.name in self.store["objs"]:
            raise PreconditionFailed()
        raw = data.encode() if isinstance(data, str) else data
        self.store["objs"][self.name] = {"data": raw, "ct": content_type, "size": len(raw),
                                         "md5": base64.b64encode(hashlib.md5(raw).digest()).decode(),
                                         "kms": KMS + "/cryptoKeyVersions/1", "generation": 1}

    def generate_signed_url(self, **kw):
        self.store["signed"].append((self.name, kw["headers"]))
        return f"https://storage.example/{self.name}?sig"

    def delete(self):
        self.store["objs"].pop(self.name, None)


class Bucket:
    def __init__(self, store):
        self.store = store

    def blob(self, name):
        return Blob(self.store, name)

    def get_blob(self, name):
        return Blob(self.store, name) if name in self.store["objs"] else None

    def list_blobs(self, prefix=""):
        return [types.SimpleNamespace(name=n) for n in self.store["objs"] if n.startswith(prefix)]


@pytest.fixture()
def api(monkeypatch):
    store = {"objs": {}, "signed": []}
    store["objs"]["config/review_allowlist.json"] = {"data": json.dumps({"users": [
        {"email": TEACHER, "role": "teacher"},
        {"email": ADMIN, "role": "admin"},
        {"email": REVIEWER, "role": "reviewer"},
        {"email": RBT, "role": "rbt"},
        {"email": "coach@example.org", "role": "coach"},
        {"email": "norole@example.org"},
    ]}).encode()}
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
    monkeypatch.setenv("PROJECT_ID", PROJECT)
    monkeypatch.setenv("IDP_EMULATOR", "1")
    monkeypatch.setenv("IDP_PROVIDERS", "password")
    monkeypatch.setenv("EXPECTED_KMS_KEY", KMS)
    monkeypatch.setenv("WEB_ALLOWED_ORIGINS", "https://review.example.org")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.syspath_prepend(str(UPLOAD_API))
    for m in ("main", "safelog", "media", "school", "ota_log", "web_rec", "idp_auth"):
        sys.modules.pop(m, None)
    main = importlib.import_module("main")
    yield main.app.test_client(), store
    for m in ("main", "web_rec", "idp_auth", "media", "school", "ota_log"):
        sys.modules.pop(m, None)


def hdr(email, uid):
    return {"Authorization": f"Bearer {unsigned(email, uid)}", "Origin": "https://review.example.org"}


SID = "s" + "ab" * 16


def test_teacher_gets_a_create_only_url_under_web_and_rbt_does_not(api, capsys):
    c, store = api
    body = {"session_id": SID, "filename": "part_0001.m4a", "size": 12,
            "md5": base64.b64encode(hashlib.md5(b"x" * 12).digest()).decode()}
    r = c.post("/v1/web/uploads", json=body, headers=hdr(TEACHER, "TeacherUID123"))
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out["object"] == f"web/{SID}/part_0001.m4a"
    assert out["headers"]["x-goog-if-generation-match"] == "0"
    assert out["headers"]["Content-MD5"] == body["md5"]
    assert store["signed"][0][0] == out["object"]
    admin_body = {**body, "session_id": "s" + "cd" * 16}
    assert c.post("/v1/web/uploads", json=admin_body, headers=hdr(ADMIN, "AdminUid12345")).status_code == 200
    admin_part = f"web/{admin_body['session_id']}/part_0001.m4a"
    _put(store, admin_part, b"x" * 12)
    assert c.post("/v1/web/confirm", json={"session_id": admin_body["session_id"], "object": admin_part,
                                           "size": 12, "md5": body["md5"]},
                  headers=hdr(ADMIN, "AdminUid12345")).status_code == 200
    log = capsys.readouterr().out
    assert TEACHER not in log and "@" not in log
    assert "TeacherUID123" not in log


def _denied(c, email, uid):
    body = {"session_id": "s" + "11" * 16, "filename": "part_0001.m4a", "size": 4,
            "md5": base64.b64encode(hashlib.md5(b"abcd").digest()).decode()}
    up = c.post("/v1/web/uploads", json=body, headers=hdr(email, uid))
    conf = c.post("/v1/web/confirm", json={"session_id": body["session_id"], "object": f"web/{body['session_id']}/part_0001.m4a",
                                           "size": 4, "md5": body["md5"]}, headers=hdr(email, uid))
    return up, conf


def test_only_teacher_and_admin_may_record(api):
    c, _ = api
    denied = {
        "reviewer": (REVIEWER, "ReviewerUid1", "READ_ONLY_ROLE"),
        "rbt": (RBT, "RbtUid123456", "READ_ONLY_ROLE"),
        "unknown": ("coach@example.org", "CoachUid12345", "READ_ONLY_ROLE"),
        "missing-role": ("norole@example.org", "NoRoleUid1234", "READ_ONLY_ROLE"),
        "not-on-list": ("stranger@example.org", "StrangerUid12", "NOT_APPROVED"),
    }
    for name, (email, uid, code) in denied.items():
        up, conf = _denied(c, email, uid)
        assert up.status_code == 403 and up.get_json()["error"] == code, name
        assert conf.status_code == 403 and conf.get_json()["error"] == code, name


def test_missing_token_bad_names_and_the_board_path_still_wants_a_device_token(api):
    c, _ = api
    assert c.post("/v1/web/uploads", json={"session_id": SID, "filename": "part_0001.m4a", "size": 1, "md5": "a" * 22 + "=="}).status_code == 401
    bad = c.post("/v1/web/uploads", json={"session_id": "../etc", "filename": "part_0001.m4a", "size": 1, "md5": "a" * 22 + "=="},
                 headers=hdr(TEACHER, "TeacherUID123"))
    assert bad.status_code == 400
    sneak = c.post("/v1/web/uploads", json={"session_id": SID, "filename": "complete.json", "size": 1, "md5": "a" * 22 + "=="},
                   headers=hdr(TEACHER, "TeacherUID123"))
    assert sneak.status_code == 400
    board = c.post("/v1/uploads", json={"device_id": "dev01", "filename": "20261102_180000_Room-104.wav", "size": 10, "md5": "a" * 22 + "=="})
    assert board.status_code == 401


def _put(store, name, raw):
    store["objs"][name] = {"data": raw, "size": len(raw), "md5": base64.b64encode(hashlib.md5(raw).digest()).decode(),
                           "crc32c": "AAAAAA==", "kms": KMS + "/cryptoKeyVersions/1", "generation": 7}


def test_confirm_is_the_signal_to_delete_the_phone_copy(api):
    c, store = api
    raw = b"segment-bytes"
    name = f"web/{SID}/part_0001.m4a"
    _put(store, name, raw)
    md5 = base64.b64encode(hashlib.md5(raw).digest()).decode()
    # owner is written by the upload call
    c.post("/v1/web/uploads", json={"session_id": SID, "filename": "part_0001.m4a", "size": len(raw), "md5": md5},
           headers=hdr(TEACHER, "TeacherUID123"))
    ok = c.post("/v1/web/confirm", json={"session_id": SID, "object": name, "size": len(raw), "md5": md5},
                headers=hdr(TEACHER, "TeacherUID123"))
    assert ok.status_code == 200
    body = ok.get_json()
    assert body["verified"] is True and body["safe_to_delete_local_copy"] is True
    bad = c.post("/v1/web/confirm", json={"session_id": SID, "object": name, "size": 1, "md5": md5},
                 headers=hdr(TEACHER, "TeacherUID123"))
    assert bad.get_json()["safe_to_delete_local_copy"] is False
    other = c.post("/v1/web/confirm", json={"session_id": SID, "object": name, "size": len(raw), "md5": md5},
                   headers=hdr(ADMIN, "AdminUid99999"))
    assert other.status_code == 403


def test_an_admin_removing_access_is_refused(api):
    c, store = api
    store["objs"]["people/state-00000001.json"] = {"data": json.dumps({
        "version": 1, "access": {TEACHER: None},
    }).encode()}
    # the allowlist cache was filled by the previous request in this process only if this
    # client already called; this fixture is fresh, so the removal is seen on the first call
    body = {"session_id": "s" + "ef" * 16, "filename": "part_0001.m4a", "size": 4,
            "md5": base64.b64encode(hashlib.md5(b"abcd").digest()).decode()}
    r = c.post("/v1/web/uploads", json=body, headers=hdr(TEACHER, "TeacherUID123"))
    assert r.status_code == 403
    assert r.get_json()["error"] == "NOT_APPROVED"


def test_finalize_writes_the_marker_the_pipeline_watches(api):
    c, store = api
    raw = b"aaa"
    name = f"web/{SID}/part_0001.webm"
    md5 = base64.b64encode(hashlib.md5(raw).digest()).decode()
    c.post("/v1/web/uploads", json={"session_id": SID, "filename": "part_0001.webm", "size": len(raw), "md5": md5},
           headers=hdr(TEACHER, "TeacherUID123"))
    _put(store, name, raw)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    fin = {"session_id": SID, "finalize": True, "class_label": "Room 104", "started_local": stamp,
           "offset_minutes": -240, "parts": [{"name": "part_0001.webm", "size": len(raw), "md5": md5}],
           "gaps": [{"offset_seconds": 134, "duration_seconds": 40}]}
    r = c.post("/v1/web/confirm", json=fin, headers=hdr(TEACHER, "TeacherUID123"))
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["object"] == f"rec/web-teacheruid123/{stamp}_Room-104.wav"
    assert body["recording_id"].startswith("rec_web_teacheruid123_")
    session = json.loads(store["objs"][f"web/{SID}/session.json"]["data"])
    assert session["gaps"] == [{"offset_seconds": 134.0, "duration_seconds": 40.0}]
    assert session["user_id"] == "TeacherUID123"
    assert "email" not in session
    marker = json.loads(store["objs"][f"web/{SID}/complete.json"]["data"])
    assert marker["wav_object"] == body["object"]
    again = c.post("/v1/web/confirm", json=fin, headers=hdr(TEACHER, "TeacherUID123"))
    assert again.get_json()["recording_id"] == body["recording_id"]


def test_discard_deletes_only_that_session(api):
    c, store = api
    raw = b"piece"
    md5 = base64.b64encode(hashlib.md5(raw).digest()).decode()
    mine, other = "s" + "22" * 16, "s" + "33" * 16
    c.post("/v1/web/uploads", json={"session_id": mine, "filename": "part_0001.m4a", "size": len(raw), "md5": md5},
           headers=hdr(TEACHER, "TeacherUID123"))
    _put(store, f"web/{mine}/part_0001.m4a", raw)
    store["objs"][f"web/{other}/part_0001.m4a"] = {"data": raw, "size": len(raw)}
    store["objs"][f"web/{other}/owner.json"] = {"data": json.dumps({"user_id": "AdminUid12345", "device_id": "web-admin"}).encode()}
    blocked = c.post("/v1/web/confirm", json={"session_id": mine, "discard": True}, headers=hdr(ADMIN, "AdminUid12345"))
    assert blocked.status_code == 403
    assert f"web/{mine}/part_0001.m4a" in store["objs"]
    gone = c.post("/v1/web/confirm", json={"session_id": mine, "discard": True}, headers=hdr(TEACHER, "TeacherUID123"))
    assert gone.status_code == 200 and gone.get_json()["deleted"] >= 1
    assert not any(n.startswith(f"web/{mine}/") for n in store["objs"])
    assert f"web/{other}/part_0001.m4a" in store["objs"]
    # a reviewer cannot discard, and an empty session does not create objects
    assert c.post("/v1/web/confirm", json={"session_id": other, "discard": True},
                  headers=hdr(REVIEWER, "ReviewerUid1")).status_code == 403
    empty = c.post("/v1/web/confirm", json={"session_id": "s" + "44" * 16, "discard": True},
                   headers=hdr(TEACHER, "TeacherUID123"))
    assert empty.status_code == 200 and empty.get_json() == {"discarded": True, "deleted": 0}
    assert f"web/{'s' + '44' * 16}/owner.json" not in store["objs"]


def test_demo_recorder_is_off_unless_this_is_the_demo(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from review_api.server import demo_recorder
    monkeypatch.delenv("WEB_DEMO_RECORDER", raising=False)
    monkeypatch.delenv("SCHOOL_PROFILE", raising=False)
    assert demo_recorder() is False
    monkeypatch.setenv("SCHOOL_PROFILE", "everyday-heroes")
    assert demo_recorder() is True
    monkeypatch.setenv("SCHOOL_PROFILE", "demo-school")
    assert demo_recorder() is True
    monkeypatch.setenv("SCHOOL_PROFILE", "a-real-school")
    assert demo_recorder() is False
    monkeypatch.setenv("SCHOOL_PROFILE", "everyday-heroes")
    monkeypatch.setenv("WEB_DEMO_RECORDER", "off")
    assert demo_recorder() is False
    monkeypatch.setenv("WEB_DEMO_RECORDER", "on")
    monkeypatch.setenv("SCHOOL_PROFILE", "a-real-school")
    assert demo_recorder() is True


def test_setup_script_enforces_private_bucket_and_does_not_let_the_pipeline_write_web():
    text = (Path(__file__).resolve().parents[3] / "infra" / "setup_web_recorder.sh").read_text()
    assert "--public-access-prevention" in text
    assert 'pap" == "enforced"' in text or "pap\" == \"enforced\"" in text or '[[ "$pap" == "enforced" ]]' in text
    assert "storage.objects.create" not in text.split("webPieceCleanup")[1].split("CLEAN_ROLE")[0]
    assert 'roles/storage.objectUser' not in text
    assert "$(obj rec/web-)" in text
    assert "upload-discard-web" in text and "pipeline-cleanup-web" in text


def test_rs256_verifier_agrees_with_the_review_service():
    sys.path.insert(0, str(UPLOAD_API))
    from test_hosted_auth import CERT_PEM, KID, token
    from idp_auth import CertCache as WebCerts
    from idp_auth import TokenVerifier as WebVerifier
    from review_api.auth import CertCache, TokenVerifier

    web = WebVerifier(PROJECT, WebCerts(fetch=lambda url: ({KID: CERT_PEM}, 3600)))
    review = TokenVerifier(PROJECT, CertCache(fetch=lambda url: ({KID: CERT_PEM}, 3600)))
    good = token(email=TEACHER, uid="TeacherUID123")
    assert web.verify(good)["sub"] == review.verify(good)["sub"] == "TeacherUID123"
    with pytest.raises(Exception):
        web.verify(token(aud="other-project"))
    with pytest.raises(Exception):
        review.verify(token(aud="other-project"))
