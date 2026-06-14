#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.diagnostics import collect_diagnostics  # noqa: E402

PARTIAL_REQUIREMENTS = ["ingest", "subtitle_transcript", "frame_extraction"]
FULL_REQUIREMENTS = [*PARTIAL_REQUIREMENTS, "whisper_transcript", "ocr", "llm_generation"]


def _missing_requirements(diagnostics: Dict[str, Any], requirements: List[str]) -> List[str]:
    ready_for = diagnostics.get("ready_for") or {}
    return [name for name in requirements if not ready_for.get(name)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check local runtime readiness for the video-to-Xiaohongshu pipeline.")
    parser.add_argument(
        "--require-full",
        action="store_true",
        help="Exit non-zero unless Whisper fallback, OCR, and LLM generation are also ready.",
    )
    args = parser.parse_args()

    diagnostics = collect_diagnostics()
    requirements = FULL_REQUIREMENTS if args.require_full else PARTIAL_REQUIREMENTS
    missing = _missing_requirements(diagnostics, requirements)
    result = {
        "ok": not missing,
        "mode": "full" if args.require_full else "partial_upstream",
        "required_ready_for": requirements,
        "missing_ready_for": missing,
        "diagnostics": diagnostics,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
