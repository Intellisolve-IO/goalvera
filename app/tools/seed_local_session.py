#!/usr/bin/env python3
"""Build a pipeline session from the synthetic sample audio into a LOCAL folder
with the bucket's layout, for offline demo runs and tests (no cloud calls).

It runs the same code the cloud pipeline runs, on recorded cloud outputs for
the synthetic TTS clip (app/tools/make_sample_audio.py):
  app/samples/pipeline/chirp3_output_rm12.json  Chirp 3 output (speaker separation, word times)
  app/samples/pipeline/sidecar_rm12.json        recorder sidecar with two markers
  app/samples/pipeline/gemini_output_rm12.json  Gemini 3.8 Flash draft notes

  python app/tools/seed_local_session.py --local-dir /tmp/ap-local
  python app/backend-pipeline/review_api/server.py --local-dir /tmp/ap-local
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "backend-pipeline"))

from gemini_analyzer.schema import validate_output  # noqa: E402
from pipeline_service.records import build_draft, build_transcript, markers_from_sidecar  # noqa: E402
from review_api.store import LocalStore  # noqa: E402
from speech_transcriber import build_speaker_turns, words_from_stt_json  # noqa: E402

SAMPLES = APP / "samples"
# The RM12 sample clip (Marcus A., Sarah Jenkins) belongs to the old one-student-per-class roster,
# kept as the Goalvera profile; tests and the laptop seed use it. The hosted demo's roster is
# samples/demo_roster.json (Everyday Heroes Academy, Oct 10), where RM12 is an alias of Room 104.
FIXTURE_ROSTER = SAMPLES / "profiles/demo-school/demo_roster.json"
DEVICE, STEM = "laptop-demo", "20260928_164500_RM12"
RID = f"rec_{DEVICE}_{STEM}".replace("-", "_")


def seed(local_dir: str, *, with_markers: bool = True) -> str:
    store = LocalStore(local_dir)
    stt = json.loads((SAMPLES / "pipeline/chirp3_output_rm12.json").read_text())
    side = json.loads((SAMPLES / "pipeline/sidecar_rm12.json").read_text())
    gem = json.loads((SAMPLES / "pipeline/gemini_output_rm12.json").read_text())
    roster = json.loads(FIXTURE_ROSTER.read_text())
    if validate_output(gem["analysis"]):
        raise SystemExit("sample Gemini output fails the schema")

    source = f"rec/{DEVICE}/{STEM}.wav"
    manifest = {"device_id": DEVICE, "class_code": "RM12", "source_object": source,
                "recorded_at_local": "2026-09-28T16:45:00", "duration_seconds": 103.92,
                "parts": [{"uri": source, "offset_seconds": 0.0}]}
    turns = build_speaker_turns(words_from_stt_json(stt))
    markers = markers_from_sidecar(side if with_markers else None, manifest["duration_seconds"])
    transcript = build_transcript(rid=RID, manifest=manifest, turns=turns, roster=roster,
                                  billed_seconds=104, speech_location="us", markers=markers,
                                  sidecar=side if with_markers else None)
    wrapped = {"prompt_version": gem["prompt_version"], "source": gem["source"], "model": gem["model"],
               "recording_id": RID, "analysis": gem["analysis"]}
    store.write_json(f"transcripts/{RID}.json", transcript)
    store.write_json(f"drafts/{RID}.json", build_draft(transcript, wrapped))
    if with_markers:
        store.write_json(source[:-4] + ".json", side)
    return RID


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local-dir", required=True)
    ap.add_argument("--no-markers", action="store_true")
    a = ap.parse_args()
    rid = seed(a.local_dir, with_markers=not a.no_markers)
    print(f"seeded {rid} into {a.local_dir} (pending_review)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
