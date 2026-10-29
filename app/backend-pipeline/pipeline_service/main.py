"""Pipeline service (Cloud Run, private) - tasks#35 steps 2-4.

Eventarc delivers every "object finalized" event in the recordings bucket.
The service is event-driven in two stages so no request ever waits on a long
transcription job:

  Stage 1  rec/<device>/<YYYYMMDD_HHMMSS_CLASS>.wav lands
           -> claim the recording (work/<id>/manifest.json, create-only)
           -> check it is 16 kHz mono 16-bit WAV
           -> split into <=19-min parts if needed (work/<id>/chunks/)
           -> start a Chirp 3 BatchRecognize job (diarization + word times),
              output to work/<id>/stt/   and return.
  Stage 2  work/<id>/stt/*.json lands (written by Speech-to-Text)
           -> once the job is done, claim analysis (work/<id>/analysis.lock)
           -> build speaker_turns transcript -> transcripts/<id>.json
           -> run the Gemini draft-notes analyzer (Vertex AI, schema-checked)
           -> drafts/<id>.json with status "pending_review" (a teacher must
              review/approve; nothing is ever sent automatically).

Everything else in the bucket is ignored. Logs carry only ids, steps,
timings and error codes (common.safelog).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from flask import Flask, request

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.safelog import log, timer  # noqa: E402
from common import recording_crypto  # noqa: E402
from speech_transcriber import (  # noqa: E402
    build_speaker_turns, plan_chunks, read_wav_info, words_from_stt_json, write_chunks,
)
from pipeline_service.records import build_draft, build_transcript, markers_from_sidecar  # noqa: E402

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
BUCKET = os.environ.get("BUCKET", "")
SPEECH_LOCATION = os.environ.get("SPEECH_LOCATION", "us")
LANGUAGE = os.environ.get("SPEECH_LANGUAGE", "en-US")
ROSTER_OBJECT = os.environ.get("ROSTER_OBJECT", "config/demo_roster.json")
REC_KMS_KEYRING_PATH = os.environ.get("REC_KMS_KEYRING_PATH", "")  # tasks#22
_unwrap = None
MAX_AUDIO_BYTES = int(os.environ.get("MAX_AUDIO_BYTES", str(400 * 1024 * 1024)))

REC_RE = re.compile(r"^rec/(?P<device>[a-z0-9][a-z0-9-]{0,31})/(?P<stem>(?P<date>\d{8})_(?P<time>\d{6})_(?P<cls>[A-Za-z0-9-]{1,16}))\.wav$")
MEDIA_RE = re.compile(r"^media/(?P<rid>rec_[A-Za-z0-9_-]+)/(?P<mid>m[0-9a-f]{12})\.json$")
MAX_PHOTOS = int(os.environ.get("MAX_PHOTOS", "4"))
MAX_PHOTO_BYTES = 20 * 1024 * 1024
STT_RE = re.compile(r"^work/(?P<rid>rec_[A-Za-z0-9_-]+)/stt/[^/]+\.json$")

app = Flask(__name__)
_clients: dict = {}


def storage_client():
    if "gcs" not in _clients:
        from google.cloud import storage
        _clients["gcs"] = storage.Client(project=PROJECT)
    return _clients["gcs"]


def speech_client():
    if "stt" not in _clients:
        from google.api_core.client_options import ClientOptions
        from google.cloud.speech_v2 import SpeechClient
        _clients["stt"] = SpeechClient(
            client_options=ClientOptions(api_endpoint=f"{SPEECH_LOCATION}-speech.googleapis.com")
        )
    return _clients["stt"]


def recording_id(device: str, stem: str) -> str:
    return f"rec_{device}_{stem}".replace("-", "_")


def bucket():
    return storage_client().bucket(BUCKET)


def read_json(name: str) -> dict | None:
    from google.api_core.exceptions import NotFound
    try:
        return json.loads(bucket().blob(name).download_as_bytes())
    except NotFound:
        return None


def write_json(name: str, data: dict, *, create_only: bool = False) -> bool:
    """Write JSON; with create_only, returns False if the object already exists."""
    from google.api_core.exceptions import PreconditionFailed
    blob = bucket().blob(name)
    try:
        blob.upload_from_string(
            json.dumps(data, indent=2, ensure_ascii=False),
            content_type="application/json",
            if_generation_match=0 if create_only else None,
        )
        return True
    except PreconditionFailed:
        return False


# ---------------------------------------------------------------- stage 1

def start_transcription(obj: dict, m: re.Match) -> None:
    from google.cloud.speech_v2.types import cloud_speech as cs

    device, stem = m["device"], m["stem"]
    rid = recording_id(device, stem)
    gen = str(obj.get("generation", ""))
    base = dict(recording_id=rid, device_id=device, object_generation=gen)

    size = int(obj.get("size") or 0)
    if size > MAX_AUDIO_BYTES:
        log("rejected", "WARNING", step="validate", error_code="TOO_LARGE", size_bytes=size, **base)
        return

    manifest = {
        "recording_id": rid, "device_id": device, "class_code": m["cls"],
        "source_object": obj["name"], "source_generation": gen, "size_bytes": size,
        "recorded_at_local": f"{m['date'][:4]}-{m['date'][4:6]}-{m['date'][6:]}T{m['time'][:2]}:{m['time'][2:4]}:{m['time'][4:]}",
        "status": "claimed", "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if not write_json(f"work/{rid}/manifest.json", manifest, create_only=True):
        log("duplicate_event_skipped", step="claim", **base)
        return

    with timer() as t, tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, "in.wav")
        bucket().blob(obj["name"]).download_to_filename(local, if_generation_match=int(gen) if gen else None)
        decrypted = False
        with open(local, "rb") as fh:
            head = fh.read(8)
        if recording_crypto.is_encrypted(head):
            # tasks#22: APENC1 from the recorder. Unwrap the file key in Cloud KMS,
            # write plain WAV only to the temp dir and the work/ prefix.
            global _unwrap
            try:
                if _unwrap is None:
                    if not REC_KMS_KEYRING_PATH:
                        raise recording_crypto.RecordingCryptoError("REC_KMS_KEYRING_PATH not set")
                    _unwrap = recording_crypto.kms_unwrapper(REC_KMS_KEYRING_PATH)
                with open(local, "rb") as fh:
                    dec = recording_crypto.decrypt(fh.read(), _unwrap)
                recording_crypto.write_wav(dec, local)
                decrypted = True
                manifest["encryption"] = {"format": "apenc1", "key_id": dec.key_id, "truncated": dec.truncated}
                log("decrypted", "WARNING" if dec.truncated else "INFO", step="decrypt",
                    key_id=dec.key_id, truncated=dec.truncated, **base)
                del dec
            except Exception as exc:  # noqa: BLE001
                manifest["status"] = "failed"; manifest["error_code"] = "DECRYPT_FAILED"
                write_json(f"work/{rid}/manifest.json", manifest)
                log("rejected", "ERROR", step="decrypt", error_code="DECRYPT_FAILED", error_type=type(exc).__name__, **base)
                return
        try:
            info = read_wav_info(local)
        except Exception as exc:  # noqa: BLE001
            manifest["status"] = "failed"; manifest["error_code"] = "NOT_WAV"
            write_json(f"work/{rid}/manifest.json", manifest)
            log("rejected", "ERROR", step="validate", error_code="NOT_WAV", error_type=type(exc).__name__, **base)
            return
        if not info.is_recorder_format:
            manifest["status"] = "failed"; manifest["error_code"] = "BAD_FORMAT"
            write_json(f"work/{rid}/manifest.json", manifest)
            log("rejected", "ERROR", step="validate", error_code="BAD_FORMAT", **base)
            return

        bounds = plan_chunks(local)
        if len(bounds) == 1 and decrypted:
            name = f"work/{rid}/chunks/part_00.wav"
            bucket().blob(name).upload_from_filename(local, content_type="audio/wav")
            parts = [{"uri": f"gs://{BUCKET}/{name}", "offset_seconds": 0.0}]
        elif len(bounds) == 1:
            parts = [{"uri": f"gs://{BUCKET}/{obj['name']}", "offset_seconds": 0.0}]
        else:
            parts = []
            for path, offset in write_chunks(local, bounds, os.path.join(tmp, "chunks")):
                name = f"work/{rid}/chunks/{os.path.basename(path)}"
                bucket().blob(name).upload_from_filename(path, content_type="audio/wav")
                parts.append({"uri": f"gs://{BUCKET}/{name}", "offset_seconds": round(offset, 3)})
    log("validated", step="validate", ms=t.ms, duration_s=round(info.duration_s, 1), chunks=len(parts), **base)

    config = cs.RecognitionConfig(
        explicit_decoding_config=cs.ExplicitDecodingConfig(
            encoding=cs.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000, audio_channel_count=1,
        ),
        language_codes=[LANGUAGE],
        model="chirp_3",
        features=cs.RecognitionFeatures(
            diarization_config=cs.SpeakerDiarizationConfig(),   # speaker separation only, no voiceprints
            enable_word_time_offsets=True,
            enable_automatic_punctuation=True,
        ),
    )
    req = cs.BatchRecognizeRequest(
        recognizer=f"projects/{PROJECT}/locations/{SPEECH_LOCATION}/recognizers/_",
        config=config,
        files=[cs.BatchRecognizeFileMetadata(uri=p["uri"]) for p in parts],
        recognition_output_config=cs.RecognitionOutputConfig(
            gcs_output_config=cs.GcsOutputConfig(uri=f"gs://{BUCKET}/work/{rid}/stt/")
        ),
    )
    with timer() as t:
        op = speech_client().batch_recognize(request=req)
    manifest.update(status="transcribing", parts=parts, duration_seconds=round(info.duration_s, 2),
                    operation=op.operation.name, model="chirp_3", speech_location=SPEECH_LOCATION)
    write_json(f"work/{rid}/manifest.json", manifest)
    log("transcription_started", step="stt_start", ms=t.ms, chunks=len(parts),
        operation_id=op.operation.name.rsplit("/", 1)[-1], **base)


# ---------------------------------------------------------------- stage 2

def _wait_operation(name: str, timeout_s: int = 90):
    from google.longrunning import operations_pb2
    deadline = time.monotonic() + timeout_s
    while True:
        op = speech_client().transport.operations_client.get_operation(name)
        if op.done or time.monotonic() > deadline:
            return op
        time.sleep(3)


def finish_session(rid: str) -> None:
    from google.cloud.speech_v2.types import cloud_speech as cs

    manifest = read_json(f"work/{rid}/manifest.json")
    if not manifest or manifest.get("status") != "transcribing":
        return
    base = dict(recording_id=rid, device_id=manifest.get("device_id"))
    op = _wait_operation(manifest["operation"])
    if not op.done:
        log("waiting_for_other_parts", step="stt_wait", **base)
        return     # a later output file's event will finish it
    if not write_json(f"work/{rid}/analysis.lock", {"at": time.time()}, create_only=True):
        log("duplicate_event_skipped", step="claim_analysis", **base)
        return
    if op.HasField("error"):
        manifest.update(status="failed", error_code=f"STT_{op.error.code}")
        write_json(f"work/{rid}/manifest.json", manifest)
        log("transcription_failed", "ERROR", step="stt", error_code=f"STT_{op.error.code}", **base)
        return

    resp = cs.BatchRecognizeResponse.deserialize(op.response.value)
    words: list[dict] = []
    billed = 0
    for i, part in enumerate(manifest["parts"]):
        r = resp.results.get(part["uri"])
        if r is None or (r.error and r.error.code):
            code = f"STT_PART_{r.error.code if r else 'MISSING'}"
            manifest.update(status="failed", error_code=code)
            write_json(f"work/{rid}/manifest.json", manifest)
            log("transcription_failed", "ERROR", step="stt", error_code=code, **base)
            return
        billed += r.metadata.total_billed_duration.seconds
        out_uri = r.cloud_storage_result.uri or r.uri
        doc = json.loads(bucket().blob(out_uri.split(f"gs://{BUCKET}/", 1)[1]).download_as_bytes())
        words += words_from_stt_json(doc, offset_s=part["offset_seconds"], part=i)
    turns = build_speaker_turns(words)

    roster = read_json(ROSTER_OBJECT) or {}
    sidecar = read_json(manifest["source_object"][:-4] + ".json")   # optional: class, teacher label, markers
    markers = markers_from_sidecar(sidecar, manifest.get("duration_seconds"))
    transcript = build_transcript(
        rid=rid, manifest=manifest, turns=turns, roster=roster, billed_seconds=billed,
        speech_location=SPEECH_LOCATION, markers=markers, sidecar=sidecar,
    )
    write_json(f"transcripts/{rid}.json", transcript)
    speakers = len({t["speaker"] for t in turns})
    log("transcript_saved", step="transcript", turns=len(turns), speakers=speakers, chunks=len(manifest["parts"]),
        markers=len(markers), status="needs_class_assignment" if transcript["needs_class_assignment"] else "class_ok",
        reason=transcript["teacher_source"] or "NO_TEACHER", **base)

    from gemini_analyzer.analyzer import analyze_transcript, model_name
    photos = load_photos(rid)
    try:
        with timer() as t:
            wrapped = analyze_transcript(transcript, mode="live", images=photos, roster=roster)
    except Exception as exc:  # noqa: BLE001 - never log the message (may echo transcript/AI text)
        manifest.update(status="failed", error_code="ANALYZER_FAILED")
        write_json(f"work/{rid}/manifest.json", manifest)
        log("analysis_failed", "ERROR", step="gemini", error_code="ANALYZER_FAILED",
            error_type=type(exc).__name__, model=model_name(), **base)
        return
    draft = build_draft(transcript, wrapped)
    draft["media"] = media_list(rid)
    write_json(f"drafts/{rid}.json", draft)
    manifest.update(status="pending_review", draft_object=f"drafts/{rid}.json")
    write_json(f"work/{rid}/manifest.json", manifest)
    log("draft_saved", step="gemini", ms=t.ms, model=wrapped.get("model"), status="pending_review", **base)


# ---------------------------------------------------------------- Teacher Notes photos (tasks#112)

def media_list(rid: str) -> list[dict]:
    """Confirmed uploads for this recording (media/<rid>/<mid>.json, written by upload-api)."""
    out = []
    for blob in storage_client().list_blobs(BUCKET, prefix=f"media/{rid}/"):
        if blob.name.endswith(".json"):
            meta = read_json(blob.name) or {}
            if meta.get("recording_id") == rid and meta.get("kind") in ("photo", "video"):
                out.append({k: meta.get(k) for k in ("media_id", "kind", "object", "content_type",
                                                     "size_bytes", "uploaded_at")})
    return sorted(out, key=lambda m: m.get("uploaded_at") or "")


def load_photos(rid: str) -> list[dict]:
    """Photo bytes for the analyzer (videos: no AI notes in this version)."""
    photos = []
    for m in media_list(rid):
        if m["kind"] != "photo" or not m.get("object") or (m.get("size_bytes") or 0) > MAX_PHOTO_BYTES:
            continue
        if len(photos) >= MAX_PHOTOS:
            break
        photos.append({"media_id": m["media_id"], "mime": m["content_type"] or "image/jpeg",
                       "data": bucket().blob(m["object"]).download_as_bytes()})
    return photos


def add_late_media(rid: str) -> None:
    """A photo/video confirmed after the draft was written: attach it, and for a photo add AI notes."""
    draft = read_json(f"drafts/{rid}.json")
    transcript = read_json(f"transcripts/{rid}.json")
    if not draft or not transcript or draft.get("status") != "pending_review":
        return     # not analyzed yet: finish_session picks it up
    media = media_list(rid)
    have = {x.get("media_id") for x in (draft.get("analysis", {}).get("image_insights") or [])}
    draft["media"] = media
    photos = [p for p in load_photos(rid) if p["media_id"] not in have]
    if photos:
        from gemini_analyzer.analyzer import analyze_transcript
        try:
            extra = analyze_transcript(transcript, mode="live", images=photos,
                                       roster=read_json(ROSTER_OBJECT) or {})
            draft["analysis"]["image_insights"] = (draft["analysis"].get("image_insights") or []) + \
                (extra["analysis"].get("image_insights") or [])
        except Exception as exc:  # noqa: BLE001
            log("photo_notes_failed", "ERROR", step="gemini_photo", error_type=type(exc).__name__,
                recording_id=rid)
    write_json(f"drafts/{rid}.json", draft)
    log("media_attached", step="media", recording_id=rid, media=len(media), photos_analyzed=len(photos))


# ---------------------------------------------------------------- HTTP

@app.post("/")
def on_event():
    obj = request.get_json(silent=True) or {}
    name = obj.get("name", "")
    if obj.get("bucket") != BUCKET or not name:
        return ("", 204)
    try:
        m = REC_RE.match(name)
        if m:
            start_transcription(obj, m)
            return ("", 204)
        m = STT_RE.match(name)
        if m:
            finish_session(m["rid"])
            return ("", 204)
        m = MEDIA_RE.match(name)
        if m:
            add_late_media(m["rid"])
        return ("", 204)
    except Exception as exc:  # noqa: BLE001
        log("unhandled_error", "ERROR", step="event", error_type=type(exc).__name__, error_code="INTERNAL")
        return ("", 500)   # Eventarc/Pub/Sub will retry; claims make retries safe


@app.get("/healthz")
def healthz():
    return {"ok": True}
