"""JSON Schema for Gemini analyzer output (design spec §6)."""

from __future__ import annotations

import json
from typing import Any

OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "OpenClassroomRecorderGeminiOutput",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "goal_evaluations",
        "incident_analysis",
        "general_session_summary",
    ],
    "properties": {
        "goal_evaluations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["goal_id", "status", "evidence_quote", "analysis"],
                "properties": {
                    "goal_id": {"type": "string", "minLength": 1},
                    "status": {
                        "type": "string",
                        "enum": ["Demonstrated", "Struggled", "Not Observed"],
                    },
                    "evidence_quote": {"type": "string"},
                    "analysis": {"type": "string"},
                },
            },
        },
        "incident_analysis": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "timestamp_seconds",
                    "antecedent",
                    "behavior",
                    "consequence_intervention",
                ],
                "properties": {
                    "timestamp_seconds": {"type": "integer", "minimum": 0},
                    "antecedent": {"type": "string"},
                    "behavior": {"type": "string"},
                    "consequence_intervention": {"type": "string"},
                    # short observed-behavior name (review service, incident_names.py)
                    "name": {"type": "string", "maxLength": 80},
                    "name_source": {"type": "string", "enum": ["ai", "teacher"]},
                },
            },
        },
        "general_session_summary": {"type": "string", "minLength": 1},
        # no longer generated per session: the parent email is part of the weekly
        # student summary (planner). Older drafts still carry one; it stays stored, never shown.
        "draft_parent_email": {"type": "string"},
        # Teacher Notes photos: one entry per photo attached to the session. Shown to the
        # teacher in review only; never part of the parent email. Absent on sessions without photos.
        "image_insights": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["media_id", "observations"],
                "properties": {
                    "media_id": {"type": "string", "minLength": 1},
                    "observations": {"type": "string"},
                    "related_goal_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}


def validate_output(data: dict[str, Any]) -> list[str]:
    """Validate analyzer output. Returns a list of error strings (empty = ok).

    Uses jsonschema if installed; otherwise a lightweight structural check so
    offline fixture mode works with zero pip deps.
    """
    try:
        import jsonschema  # type: ignore

        validator = jsonschema.Draft202012Validator(OUTPUT_SCHEMA)
        return [
            f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
            for e in validator.iter_errors(data)
        ]
    except ImportError:
        return _lightweight_validate(data)


def _lightweight_validate(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["<root>: expected object"]

    for key in OUTPUT_SCHEMA["required"]:   # same list as jsonschema (no parent email since Oct 8)
        if key not in data:
            errors.append(f"{key}: required")

    allowed = set(OUTPUT_SCHEMA["properties"].keys())
    for key in data:
        if key not in allowed:
            errors.append(f"{key}: additional property not allowed")

    status_ok = {"Demonstrated", "Struggled", "Not Observed"}

    if isinstance(data.get("goal_evaluations"), list):
        for i, g in enumerate(data["goal_evaluations"]):
            if not isinstance(g, dict):
                errors.append(f"goal_evaluations/{i}: expected object")
                continue
            for req in ("goal_id", "status", "evidence_quote", "analysis"):
                if req not in g:
                    errors.append(f"goal_evaluations/{i}/{req}: required")
            if "status" in g and g["status"] not in status_ok:
                errors.append(
                    f"goal_evaluations/{i}/status: must be one of {sorted(status_ok)}"
                )
    elif "goal_evaluations" in data:
        errors.append("goal_evaluations: expected array")

    if isinstance(data.get("incident_analysis"), list):
        for i, inc in enumerate(data["incident_analysis"]):
            if not isinstance(inc, dict):
                errors.append(f"incident_analysis/{i}: expected object")
                continue
            for req in (
                "timestamp_seconds",
                "antecedent",
                "behavior",
                "consequence_intervention",
            ):
                if req not in inc:
                    errors.append(f"incident_analysis/{i}/{req}: required")
            ts = inc.get("timestamp_seconds")
            if ts is not None and not isinstance(ts, int):
                errors.append(f"incident_analysis/{i}/timestamp_seconds: expected int")
    elif "incident_analysis" in data:
        errors.append("incident_analysis: expected array")

    for text_key in ("general_session_summary", "draft_parent_email"):
        val = data.get(text_key)
        if val is not None and (not isinstance(val, str) or not val.strip()):
            errors.append(f"{text_key}: non-empty string required")

    return errors


def schema_as_json() -> str:
    return json.dumps(OUTPUT_SCHEMA, indent=2)
