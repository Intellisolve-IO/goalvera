#!/usr/bin/env python3
"""CLI: analyze a fixture transcript (offline by default).

Usage:
  python run_fixture.py
  python run_fixture.py --mode offline
  python run_fixture.py --mode auto
  python run_fixture.py --mode live --transcript ../../samples/transcript_session_student_a_thu.json
  python run_fixture.py --validate-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python run_fixture.py` from this directory without installing a package.
_HERE = Path(__file__).resolve().parent
_PIPELINE = _HERE.parent
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

from gemini_analyzer.analyzer import (  # noqa: E402
    DEFAULT_EXPECTED,
    DEFAULT_TRANSCRIPT,
    analyze_transcript,
    load_json,
)
from gemini_analyzer.prompt import PROMPT_VERSION, build_user_prompt  # noqa: E402
from gemini_analyzer.schema import schema_as_json, validate_output  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR Gemini analyzer fixture runner")
    parser.add_argument(
        "--mode",
        choices=["offline", "live", "auto"],
        default="offline",
        help="offline (default) loads expected JSON; live calls Gemini; auto picks",
    )
    parser.add_argument(
        "--transcript",
        type=Path,
        default=DEFAULT_TRANSCRIPT,
        help="Path to transcript fixture JSON",
    )
    parser.add_argument(
        "--expected",
        type=Path,
        default=DEFAULT_EXPECTED,
        help="Path to expected Gemini output (offline mode)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path to write analysis JSON",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate the expected fixture against schema",
    )
    parser.add_argument(
        "--print-prompt",
        action="store_true",
        help="Print the rendered user prompt and exit",
    )
    parser.add_argument(
        "--print-schema",
        action="store_true",
        help="Print JSON schema and exit",
    )
    args = parser.parse_args()

    if args.print_schema:
        print(schema_as_json())
        return 0

    if args.validate_only:
        data = load_json(args.expected)
        errors = validate_output(data)
        if errors:
            print("FAIL schema:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1
        print(f"OK schema · prompt_version={PROMPT_VERSION} · {args.expected}")
        return 0

    transcript = load_json(args.transcript)

    if args.print_prompt:
        print(build_user_prompt(transcript))
        return 0

    try:
        wrapped = analyze_transcript(
            transcript,
            mode=args.mode,
            expected_path=args.expected,
        )
    except Exception as exc:  # noqa: BLE001 — CLI surface
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    payload = json.dumps(wrapped, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
        print(f"Wrote {args.out} (source={wrapped['source']})")
    else:
        print(payload)

    print(
        f"\n# validated OK · source={wrapped['source']} · prompt={PROMPT_VERSION}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
