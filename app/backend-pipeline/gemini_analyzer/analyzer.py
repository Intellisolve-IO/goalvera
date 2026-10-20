"""Analyze a session transcript via Gemini (live) or offline fixture (default)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from .prompt import (PROMPT_VERSION, SYSTEM_INSTRUCTION, build_user_prompt, reinsert_first_name,
                     roster_first_names, scrub_image_insights)
from .schema import validate_output

Mode = Literal["offline", "live", "auto"]

# gemini-1.5-flash (the old default) is retired, and the 2.5 family retires on
# Vertex AI on 2026-10-16. Gemini 3.x Flash is served from the "us"
# multi-region (US-only processing), so that is the default location too.
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_VERTEX_LOCATION = "us"


def model_name() -> str:
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"
DEFAULT_EXPECTED = SAMPLES_DIR / "expected_gemini_output_student_a_thu.json"
DEFAULT_TRANSCRIPT = SAMPLES_DIR / "transcript_session_student_a_thu.json"


def load_json(path: Path | str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_offline_fixture(
    expected_path: Path | str | None = None,
) -> dict[str, Any]:
    path = Path(expected_path) if expected_path else DEFAULT_EXPECTED
    data = load_json(path)
    errors = validate_output(data)
    if errors:
        raise ValueError(f"Fixture failed schema validation: {errors}")
    return data


def _gemini_available() -> bool:
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        return True
    if os.environ.get("GOOGLE_CLOUD_PROJECT") and _use_vertex():
        return True
    return False


def _use_vertex() -> bool:
    return os.environ.get("VERTEX_AI", os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "")).lower() in (
        "1", "true", "yes",
    )


def _client():
    """Vertex AI in our project when VERTEX_AI=1 (the demo and go-live path: data
    stays in the project, no training on it), otherwise the Gemini API key path
    for local experiments. Requires google-genai."""
    from google import genai  # type: ignore

    if _use_vertex():
        return genai.Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GEMINI_LOCATION", DEFAULT_VERTEX_LOCATION),
        )
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    return genai.Client(api_key=api_key) if api_key else genai.Client()


def _call_gemini(transcript: dict[str, Any], images: list[dict] | None = None) -> dict[str, Any]:
    """Live call. The request carries no student first name (prompt.py, DPA §3(a)(5)).

    images: Teacher Notes photos [{"media_id", "mime", "data": bytes}] (), sent as image
    parts after the text. The photos themselves cannot be redacted; the prompt forbids copying any
    written name and scrub_image_insights() removes roster first names from the answer."""
    from .schema import OUTPUT_SCHEMA

    images = images or []
    user_prompt = build_user_prompt(transcript, [i["media_id"] for i in images])
    client = _client()
    contents: Any = user_prompt
    if images:
        from google.genai import types as gtypes  # type: ignore
        contents = [user_prompt] + [gtypes.Part.from_bytes(data=i["data"], mime_type=i["mime"]) for i in images]

    schema = json.loads(json.dumps({k: v for k, v in OUTPUT_SCHEMA.items() if k not in ("$schema", "title")}))
    # The session analyzer writes neither a parent email (weekly summary now, nor
    # incident names (the review service names incidents with its own checks: incident_names.py).
    schema["properties"].pop("draft_parent_email", None)
    for k in ("name", "name_source"):
        schema["properties"]["incident_analysis"]["items"]["properties"].pop(k, None)
    if not images:
        schema["properties"].pop("image_insights", None)
    response = client.models.generate_content(
        model=model_name(),
        contents=contents,
        config={
            "system_instruction": SYSTEM_INSTRUCTION,
            "response_mime_type": "application/json",
            "response_json_schema": schema,
            "temperature": 0.2,
        },
    )
    text = response.text or ""

    # Strip accidental markdown fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

    return json.loads(cleaned)


def analyze_transcript(
    transcript: dict[str, Any] | None = None,
    *,
    mode: Mode = "offline",
    expected_path: Path | str | None = None,
    transcript_path: Path | str | None = None,
    images: list[dict] | None = None,
    roster: dict | None = None,
) -> dict[str, Any]:
    """
    Run analysis.

    mode:
      - offline (default): load expected fixture JSON — CI/demo reliable
      - live: call Gemini (requires API key / Vertex)
      - auto: live if credentials present, else offline
    """
    if transcript is None:
        transcript = load_json(transcript_path or DEFAULT_TRANSCRIPT)

    resolved: Mode = mode
    if mode == "auto":
        resolved = "live" if _gemini_available() else "offline"

    if resolved == "offline":
        # Prefer session-linked fixture if recording_id matches marcus thu
        path = expected_path
        if path is None and transcript.get("recording_id") == "rec_20260924_1014":
            path = DEFAULT_EXPECTED
        result = load_offline_fixture(path)
        source = "offline_fixture"
        if images:
            result["image_insights"] = [{"media_id": i["media_id"], "observations":
                                         "Offline fixture: photo attached; no AI notes in offline mode.",
                                         "related_goal_ids": []} for i in images]
    else:
        result = _call_gemini(transcript, images)
        source = "vertex_gemini_live" if _use_vertex() else "gemini_live"

    errors = validate_output(result)
    if errors:
        raise ValueError(f"Analyzer output failed schema: {errors}")
    if "image_insights" in result:
        known = {i["media_id"] for i in images or []}
        result["image_insights"] = scrub_image_insights(
            [x for x in result["image_insights"] if x.get("media_id") in known],
            roster_first_names(roster or {}) + [n for n in [transcript.get("student_first_name")] if n])
    # The AI only ever saw "the student"; put the first name back on our side.
    result = reinsert_first_name(result, transcript.get("student_first_name"))

    return {
        "prompt_version": PROMPT_VERSION,
        "source": source,
        "model": model_name() if source != "offline_fixture" else None,
        "recording_id": transcript.get("recording_id"),
        "analysis": result,
    }
