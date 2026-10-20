"""Class-code normalization, teacher fallback, needs-class-assignment and the
roster backfill (). Offline: local store / fake gcloud runner.

  python -m pytest app/backend-pipeline/tests -q
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
REPO = HERE.parents[3]
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE.parents[2] / "tools"))

from common.classcodes import class_key, find_class, find_teacher, resolve_session  # noqa: E402
from pipeline_service.records import ROSTER_FIELDS, build_draft, build_transcript  # noqa: E402
from review_api.notes import ApiError, Notes  # noqa: E402
from review_api.store import Conflict, GcloudCliStore, LocalStore  # noqa: E402

_spec = importlib.util.spec_from_file_location("backfill_session_roster", REPO / "infra/backfill_session_roster.py")
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)

ROSTER = json.loads((REPO / "app/samples/legacy_demo_roster.json").read_text())   # old shape
FIRMWARE_CLASSES = ["Reading 3A", "Math 2B", "Circle Time"]
TEACHER = "usr_demo_sarah"


# ------------------------------------------------------------ class_key / lookups

@pytest.mark.parametrize("code", ["Reading 3A", "Reading-3A", "READING_3a", " reading -- 3a ", "Reading__3A"])
def test_class_key_spellings_are_the_same(code):
    assert class_key(code) == "reading-3a"
    assert find_class(ROSTER, code)[0] == "Reading 3A"


@pytest.mark.parametrize("code", [None, "", "   ", "--", 12])
def test_class_key_empty(code):
    assert class_key(code) is None
    assert find_class(ROSTER, code) == (None, None)


def test_different_classes_stay_different():
    assert class_key("Reading 3A") != class_key("Reading 3B")
    assert find_class(ROSTER, "Reading 3B") == (None, None)


def test_roster_has_the_firmware_classes_with_the_demo_teacher():
    fw = {class_key(c) for c in FIRMWARE_CLASSES}
    assert fw <= {class_key(c) for c in ROSTER["class_codes"]}
    for c in FIRMWARE_CLASSES:
        _, e = find_class(ROSTER, c)
        assert e["teacher_id"] == TEACHER and e["teacher_name"] == "Sarah Jenkins"
    assert find_class(ROSTER, "rm12")[0] == "RM12"          # the old demo class still works


def test_roster_matches_firmware_sample_list_when_available():
    # the firmware worktree sits next to this one on the demo box; skip elsewhere
    fw = REPO / "firmware/recorder-idf/main/sample_device.json"
    if not fw.is_file():
        pytest.skip("firmware checkout not present")
    classes = json.loads(fw.read_text()).get("classes", [])
    assert classes and all(find_class(ROSTER, c)[0] for c in classes)


def test_find_teacher_by_name_or_device_label():
    assert find_teacher(ROSTER, "Demo Teacher") == (TEACHER, "Sarah Jenkins")
    assert find_teacher(ROSTER, "sarah  jenkins") == (TEACHER, "Sarah Jenkins")
    assert find_teacher(ROSTER, "J. Rivera") == (None, None)
    assert find_teacher(ROSTER, None) == (None, None)


# ------------------------------------------------------------ resolve_session

def test_sidecar_space_and_filename_hyphen_resolve_to_one_class():
    ctx = resolve_session(ROSTER, filename_code="Reading-3A",
                          sidecar={"class_code": "Reading 3A", "teacher": "Demo Teacher"})
    assert ctx["class_code"] == "Reading 3A" and ctx["class_code_file"] == "Reading-3A"
    assert ctx["class_key"] == "reading-3a" and ctx["needs_class_assignment"] is False
    assert (ctx["teacher_id"], ctx["teacher_name"], ctx["teacher_source"]) == (TEACHER, "Sarah Jenkins", "device_sidecar")


def test_teacher_falls_back_to_class_roster():
    for side in (None, {}, {"class_code": "math 2b"}, {"class_code": "Math 2B", "teacher": "J. Rivera"}):
        ctx = resolve_session(ROSTER, filename_code="Math-2B", sidecar=side)
        assert ctx["class_code"] == "Math 2B" and ctx["teacher_id"] == TEACHER
        assert ctx["teacher_source"] == "class_roster" and not ctx["needs_class_assignment"]


def test_sidecar_class_wins_then_filename():
    ctx = resolve_session(ROSTER, filename_code="Circle-Time", sidecar={"class_code": "Math 2B"})
    assert ctx["class_code"] == "Math 2B"
    ctx = resolve_session(ROSTER, filename_code="Circle-Time", sidecar={"class_code": "Art 9Z"})
    assert ctx["class_code"] == "Circle Time"                 # unknown sidecar label -> file token


def test_unknown_class_is_flagged_not_guessed():
    ctx = resolve_session(ROSTER, filename_code="Art-9Z", sidecar={"class_code": "Art 9Z"})
    assert ctx["needs_class_assignment"] is True
    assert ctx["teacher_id"] is None and ctx["teacher_source"] is None
    assert ctx["class_code"] == "Art 9Z" and ctx["student_first_name"] == "Student"
    # a matching device teacher is still recorded, but the class stays flagged
    ctx = resolve_session(ROSTER, filename_code="Art-9Z", sidecar={"teacher": "Demo Teacher"})
    assert ctx["needs_class_assignment"] is True and ctx["teacher_id"] == TEACHER


def test_rm12_student_and_goals_unchanged():
    ctx = resolve_session(ROSTER, filename_code="RM12", sidecar=None)
    assert ctx["student_first_name"] != "Student" and ctx["iep_goals"]
    assert ctx["teacher_id"] == TEACHER and ctx["teacher_source"] == "class_roster"


# ------------------------------------------------------------ pipeline records

def _manifest(code="Reading-3A", device="a4cb8fc08704", stem="20260929_131649"):
    src = f"rec/{device}/{stem}_{code}.wav"
    return {"device_id": device, "class_code": code, "source_object": src,
            "recorded_at_local": "2026-09-29T13:16:49", "duration_seconds": 12.0,
            "parts": [{"uri": src, "offset_seconds": 0.0}]}


def test_build_transcript_uses_sidecar_and_roster():
    t = build_transcript(rid="rec_x", manifest=_manifest(), turns=[], roster=ROSTER, billed_seconds=12,
                         speech_location="us", sidecar={"class_code": "Reading 3A", "teacher": "Demo Teacher"})
    for k in ROSTER_FIELDS:
        assert k in t
    assert t["teacher_id"] == TEACHER and t["class_code"] == "Reading 3A"
    assert t["needs_class_assignment"] is False


# ------------------------------------------------------------ fixtures: a Reading-3A-like session as it was before the fix

# schema-valid synthetic notes (the recorded sample Gemini output)
ANALYSIS = json.loads((REPO / "app/samples/pipeline/gemini_output_rm12.json").read_text())["analysis"]
OLD_ROSTER = {"class_codes": {k: v for k, v in ROSTER["class_codes"].items() if class_key(k) == "rm12"}}
RID = "rec_a4cb8fc08704_20260929_131649_Reading_3A"


def _seed_unknown(root: Path, *, code="Reading-3A", sidecar_class="Reading 3A", with_sidecar=True) -> LocalStore:
    store = LocalStore(root)
    m = _manifest(code)
    side = {"class_code": sidecar_class, "teacher": "Demo Teacher", "classroom_code": "room-210", "markers": []}
    t = build_transcript(rid=RID, manifest=m, turns=[{"speaker": "Speaker 1", "start": 0.0, "end": 1.0, "text": "synthetic"}],
                         roster=OLD_ROSTER, billed_seconds=12, speech_location="us", sidecar=side)
    wrapped = {"prompt_version": "v1", "source": "sample", "model": "gemini-3.8-flash",
               "recording_id": RID, "analysis": ANALYSIS}
    d = build_draft(t, wrapped)
    # shape the records like the real one written before this fix: file-name class
    # code, none of the new fields
    for rec in (t, d["session"]):
        for k in ("class_code_file", "class_key", "needs_class_assignment", "teacher_source"):
            rec.pop(k, None)
        rec["class_code"] = code
    store.write_json(f"transcripts/{RID}.json", t)
    store.write_json(f"drafts/{RID}.json", d)
    if with_sidecar:
        store.write_json(m["source_object"][:-4] + ".json", side)
    return store


def test_unknown_class_is_flagged_in_list_and_blocks_approve(tmp_path):
    notes = Notes(_seed_unknown(tmp_path))
    [s] = notes.list_sessions()
    assert s["needs_class_assignment"] is True and s["can_approve"] is False and s["teacher_id"] is None
    with pytest.raises(ApiError) as e:
        notes.approve(RID, TEACHER, ANALYSIS)
    assert (e.value.http, e.value.code) == (409, "NEEDS_CLASS_ASSIGNMENT")
    assert not notes.store.list("approvals/")


# ------------------------------------------------------------ backfill

def _snapshot(store):
    return {n: store.read_json(n) for n in store.list("")}


def test_backfill_dry_run_writes_nothing(tmp_path):
    store = _seed_unknown(tmp_path)
    before = _snapshot(store)
    rep = bf.backfill(store, RID, ROSTER)
    assert rep["applied"] is False and rep["sidecar_found"] is True
    assert rep["changes"]["teacher_id"] == [None, TEACHER]
    assert rep["changes"]["class_code"] == ["Reading-3A", "Reading 3A"]
    assert _snapshot(store) == before


def test_backfill_fills_teacher_keeps_notes_then_teacher_can_approve(tmp_path):
    store = _seed_unknown(tmp_path)
    before = _snapshot(store)
    rep = bf.backfill(store, RID, ROSTER, apply=True, now=dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc))
    assert rep["applied"] is True and rep["needs_class_assignment"] is False
    after = _snapshot(store)
    d0, d1 = before[f"drafts/{RID}.json"], after[f"drafts/{RID}.json"]
    t0, t1 = before[f"transcripts/{RID}.json"], after[f"transcripts/{RID}.json"]
    # only roster fields changed; notes, transcript text, markers, status untouched
    assert d1["analysis"] == d0["analysis"] and d1["status"] == "pending_review"
    assert t1["speaker_turns"] == t0["speaker_turns"] and t1["markers"] == t0["markers"]
    assert t1["iep_goals"] == t0["iep_goals"]
    for k in set(d0["session"]) - set(ROSTER_FIELDS):
        assert d1["session"][k] == d0["session"][k]
    assert d1["session"]["teacher_id"] == TEACHER and d1["session"]["teacher_name"] == "Sarah Jenkins"
    assert d1["roster_backfill"][0]["fields"] == sorted(rep["changes"])
    # nothing deleted; exactly one audit record added, ids + field names only
    assert set(before) <= set(after)
    [audit] = [n for n in set(after) - set(before)]
    assert audit.startswith(f"audit/{RID}/") and "roster_backfilled" in audit
    assert "Sarah" not in json.dumps(after[audit])
    # now it shows the teacher and the teacher can approve
    notes = Notes(store)
    s = notes.session(RID)
    assert s["teacher_name"] == "Sarah Jenkins" and s["can_approve"] and not s["needs_class_assignment"]
    notes.approve(RID, TEACHER, s["processed_analysis"])
    assert notes.session(RID)["status"] == "approved"
    # idempotent / refuses once approved
    with pytest.raises(bf.BackfillError, match="ALREADY_APPROVED"):
        bf.backfill(store, RID, ROSTER, apply=True)


def test_backfill_second_run_is_a_no_op(tmp_path):
    store = _seed_unknown(tmp_path)
    bf.backfill(store, RID, ROSTER, apply=True)
    snap = _snapshot(store)
    rep = bf.backfill(store, RID, ROSTER, apply=True)
    assert rep["changes"] == {} and rep["applied"] is False and _snapshot(store) == snap


def test_backfill_without_sidecar_uses_filename_token(tmp_path):
    store = _seed_unknown(tmp_path, with_sidecar=False)
    rep = bf.backfill(store, RID, ROSTER, apply=True)
    assert rep["sidecar_found"] is False and rep["applied"]
    s = store.read_json(f"drafts/{RID}.json")["session"]
    assert s["class_code"] == "Reading 3A" and s["teacher_source"] == "class_roster"


def test_backfill_unknown_class_needs_manual_class(tmp_path):
    store = _seed_unknown(tmp_path, code="Art-9Z", sidecar_class="Art 9Z")
    rep = bf.backfill(store, RID, ROSTER)
    assert rep["needs_class_assignment"] is True
    with pytest.raises(bf.BackfillError, match="CLASS_NOT_IN_ROSTER"):
        bf.backfill(store, RID, ROSTER, assign_class="Nope 1", apply=True)
    rep = bf.backfill(store, RID, ROSTER, assign_class="circle-time", apply=True)
    s = store.read_json(f"drafts/{RID}.json")
    assert s["session"]["class_code"] == "Circle Time" and s["session"]["teacher_id"] == TEACHER
    assert s["roster_backfill"][-1]["class_assigned_by_hand"] is True


def test_backfill_detects_concurrent_change(tmp_path, monkeypatch):
    store = _seed_unknown(tmp_path)
    real = store.read_json

    def racing_read(name):
        out = real(name)
        if name == f"drafts/{RID}.json":           # someone rewrites the draft after we took its generation
            p = store._p(name)
            p.write_text(p.read_text())
            import os
            st = p.stat()
            os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        return out
    monkeypatch.setattr(store, "read_json", racing_read)
    with pytest.raises(Conflict):
        bf.backfill(store, RID, ROSTER, apply=True)


def test_backfill_rejects_bad_ids_and_missing(tmp_path):
    store = LocalStore(tmp_path)
    with pytest.raises(bf.BackfillError, match="BAD_RECORDING_ID"):
        bf.backfill(store, "../etc", ROSTER)
    with pytest.raises(bf.BackfillError, match="DRAFT_NOT_FOUND"):
        bf.backfill(store, RID, ROSTER)


# ------------------------------------------------------------ GcloudCliStore (fake runner, no cloud)

class FakeRunner:
    def __init__(self, responses):
        self.calls, self.responses = [], list(responses)

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        rc, out, err = self.responses.pop(0)
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


def test_gcloud_cli_store_reads_and_missing():
    r = FakeRunner([(0, '{"a": 1}', ""), (1, "", "ERROR: The following URLs matched no objects or files"),
                    (1, "", "ERROR: permission denied")])
    s = GcloudCliStore("bkt", "proj", runner=r)
    assert s.read_json("drafts/x.json") == {"a": 1}
    assert s.read_json("drafts/y.json") is None
    with pytest.raises(RuntimeError):
        s.read_json("drafts/z.json")
    assert r.calls[0][0] == ["gcloud", "storage", "cat", "gs://bkt/drafts/x.json", "--project=proj"]


def test_gcloud_cli_store_generation_created_prefer_custom_time():
    desc = {"generation": "123", "creation_time": "2026-09-29T17:19:31+0000"}
    r = FakeRunner([(0, json.dumps(desc), ""), (0, json.dumps(desc), ""),
                    (0, json.dumps({**desc, "custom_time": "2026-09-01T00:00:00+0000"}), "")])
    s = GcloudCliStore("bkt", "proj", runner=r)
    assert s.generation("drafts/x.json") == 123
    assert s.created("drafts/x.json") == dt.datetime(2026, 9, 29, 17, 19, 31, tzinfo=dt.timezone.utc)
    assert s.created("drafts/x.json") == dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)


def test_gcloud_cli_store_conditional_write_flags_and_conflict():
    r = FakeRunner([(0, "", ""), (1, "", "HTTPError 412: At least one of the pre-conditions you specified did not hold."),
                    (0, "", "")])
    s = GcloudCliStore("bkt", "proj", runner=r)
    ct = dt.datetime(2026, 9, 29, 17, 19, 31, tzinfo=dt.timezone.utc)
    s.write_json("drafts/x.json", {"a": 1}, if_generation=123, custom_time=ct)
    args, kw = r.calls[0]
    assert args[:5] == ["gcloud", "storage", "cp", "-", "gs://bkt/drafts/x.json"]
    assert "--if-generation-match=123" in args and "--custom-time=2026-09-29T17:19:31Z" in args
    assert json.loads(kw["input"]) == {"a": 1}
    with pytest.raises(Conflict):
        s.write_json("drafts/x.json", {"a": 2}, if_generation=1)
    s.write_json("audit/x/1.json", {"e": 1}, create_only=True)
    assert "--if-generation-match=0" in r.calls[2][0]


def test_gcloud_cli_store_rejects_path_tricks():
    s = GcloudCliStore("bkt", "proj", runner=FakeRunner([]))
    with pytest.raises(Exception):
        s.read_json("../secret")
