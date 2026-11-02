"""Turn a finished phone session into one recorder WAV.

Eventarc delivers web/<session_id>/complete.json. This downloads the AAC/Opus
pieces, joins them, and writes 16 kHz mono 16-bit WAV to
rec/web-<user>/<YYYYMMDD_HHMMSS_CLASS>.wav plus a JSON sidecar. The existing
pipeline then sees an ordinary rec/ object and does not change. The audio
pieces are deleted after the WAV is stored. Anything left under web/ is removed
by the bucket lifecycle after one day.

Gaps (screen lock, app switch, phone call) are copied onto the sidecar so the
review page can say when the recording paused. They are not silence in the WAV:
the audio is only the time the microphone was open.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile

COMPLETE_RE = re.compile(r"^web/(?P<sid>s[a-f0-9]{16,40})/complete\.json$")
PART_RE = re.compile(r"^part_\d{4}\.(m4a|mp4|webm)$")
DEVICE_RE = re.compile(r"^web-[a-z0-9]{4,28}$")


class ConvertError(Exception):
    def __init__(self, code: str, *, retry: bool):
        super().__init__(code)
        self.code, self.retry = code, retry


def _read_json(bucket, name: str) -> dict | None:
    blob = bucket.get_blob(name)
    if blob is None:
        return None
    try:
        doc = json.loads(blob.download_as_bytes())
    except Exception as exc:  # noqa: BLE001
        raise ConvertError("BAD_MANIFEST", retry=False) from exc
    return doc if isinstance(doc, dict) else None


def _run(cmd: list[str], timeout: int) -> None:
    try:
        subprocess.run(cmd, check=True, timeout=timeout, capture_output=True)
    except FileNotFoundError as exc:
        raise ConvertError("FFMPEG_MISSING", retry=False) from exc
    except subprocess.TimeoutExpired as exc:
        raise ConvertError("FFMPEG_TIMEOUT", retry=True) from exc
    except subprocess.CalledProcessError as exc:
        raise ConvertError("FFMPEG_FAILED", retry=False) from exc


def transcode_piece(src: str, dst: str) -> None:
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
          "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", "-c:a", "pcm_s16le", dst], 180)


def concat_wavs(paths: list[str], dst: str, list_path: str) -> None:
    with open(list_path, "w", encoding="utf-8") as fh:
        for p in paths:
            fh.write(f"file '{p}'\n")
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
          "-i", list_path, "-c", "copy", dst], 300)


def gaps_for_sidecar(raw) -> list[dict]:
    out = []
    for g in raw or []:
        if not isinstance(g, dict):
            continue
        off, dur = g.get("offset_seconds"), g.get("duration_seconds")
        if isinstance(off, bool) or isinstance(dur, bool):
            continue
        if not isinstance(off, (int, float)) or not isinstance(dur, (int, float)):
            continue
        if off < 0 or dur < 0 or off > 6 * 3600 or dur > 2 * 3600:
            continue
        out.append({"offset_seconds": round(float(off), 1), "duration_seconds": round(float(dur), 1)})
    return out[:50]


def paused_markers(gaps: list[dict]) -> list[dict]:
    return [{"type": "Recording paused", "offset_ms": int(round(g["offset_seconds"] * 1000))} for g in gaps]


def _upload_new(bucket, name: str, filename: str, content_type: str) -> bool:
    """Create the object. False when it is already there."""
    from google.api_core.exceptions import PreconditionFailed
    blob = bucket.blob(name)
    try:
        blob.upload_from_filename(filename, content_type=content_type, if_generation_match=0)
        return True
    except PreconditionFailed:
        return False
    except TypeError:
        # Test doubles that only implement upload_from_string.
        with open(filename, "rb") as fh:
            data = fh.read()
        try:
            blob.upload_from_string(data, content_type=content_type, if_generation_match=0)
            return True
        except PreconditionFailed:
            return False


def _delete_parts(bucket, session_id: str, names: list[str]) -> None:
    for name in names:
        blob = bucket.blob(f"web/{session_id}/{name}")
        delete = getattr(blob, "delete", None)
        if delete:
            delete()


def convert_web_session(bucket, name: str, *, log=None) -> dict:
    """Join one session. Returns {wav, deleted}. Raises ConvertError."""
    m = COMPLETE_RE.match(name or "")
    if not m:
        raise ConvertError("NOT_WEB", retry=False)
    session_id = m["sid"]
    marker = _read_json(bucket, name)
    if not marker:
        raise ConvertError("NO_MARKER", retry=False)
    manifest = _read_json(bucket, f"web/{session_id}/session.json")
    if not manifest:
        raise ConvertError("NO_MANIFEST", retry=True)
    device = str(manifest.get("device_id") or "")
    parts = manifest.get("parts") or []
    stamp = str(manifest.get("started_local") or "")
    code = str(manifest.get("class_code") or "")
    if not DEVICE_RE.match(device) or not parts or len(parts) > 400:
        raise ConvertError("BAD_MANIFEST", retry=False)
    if not re.fullmatch(r"\d{8}_\d{6}", stamp) or not re.fullmatch(r"[A-Za-z0-9-]{1,16}", code):
        raise ConvertError("BAD_MANIFEST", retry=False)
    if any(not PART_RE.match(str(p)) for p in parts):
        raise ConvertError("BAD_MANIFEST", retry=False)
    wav_object = f"rec/{device}/{stamp}_{code}.wav"
    side_object = f"rec/{device}/{stamp}_{code}.json"
    if bucket.get_blob(wav_object) is not None:
        _delete_parts(bucket, session_id, [str(p) for p in parts])
        if log:
            log("web_convert", step="web_convert", session_id=session_id, device_id=device,
                part_count=len(parts), status="already")
        return {"wav": wav_object, "deleted": len(parts), "already": True}

    gaps = gaps_for_sidecar(manifest.get("gaps"))
    with tempfile.TemporaryDirectory() as tmp:
        wavs = []
        for i, part in enumerate(parts):
            src = os.path.join(tmp, f"in_{i:04d}")
            dst = os.path.join(tmp, f"out_{i:04d}.wav")
            blob = bucket.get_blob(f"web/{session_id}/{part}")
            if blob is None:
                raise ConvertError("PART_MISSING", retry=True)
            download = getattr(blob, "download_to_filename", None)
            if download:
                download(src)
            else:
                with open(src, "wb") as fh:
                    fh.write(blob.download_as_bytes())
            transcode_piece(src, dst)
            wavs.append(dst)
        joined = os.path.join(tmp, "joined.wav")
        concat_wavs(wavs, joined, os.path.join(tmp, "list.txt"))
        from speech_transcriber.chunking import read_wav_info
        info = read_wav_info(joined)
        if not info.is_recorder_format:
            raise ConvertError("BAD_FORMAT", retry=False)
        created = _upload_new(bucket, wav_object, joined, "audio/wav")
        sidecar = {
            "class_code": manifest.get("class_label") or code,
            "class_label": manifest.get("class_label") or code,
            "start_time": manifest.get("start_time"),
            "time_valid": True,
            "firmware_version": "web",
            "source": "iphone-web",
            "recording_gaps": gaps,
            "markers": paused_markers(gaps),
        }
        side_path = os.path.join(tmp, "side.json")
        with open(side_path, "w", encoding="utf-8") as fh:
            json.dump(sidecar, fh)
        _upload_new(bucket, side_object, side_path, "application/json")
    _delete_parts(bucket, session_id, [str(p) for p in parts])
    if log:
        log("web_convert", step="web_convert", session_id=session_id, device_id=device,
            part_count=len(parts), status="wrote" if created else "exists",
            duration_s=round(info.duration_s, 1))
    return {"wav": wav_object, "deleted": len(parts), "already": not created}
