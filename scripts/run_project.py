#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.schemas.models import ProjectCreate  # noqa: E402
from app.services.pipeline import (  # noqa: E402
    run_project_downstream_pipeline,
    run_project_pipeline,
    run_project_visual_pipeline,
)
from app.services.runtime_store import store  # noqa: E402
from scripts.verify_project import verify_project  # noqa: E402


def run_project(
    url: str,
    language: str,
    style: str,
    use_whisper: bool,
    max_frames: int,
    text_only: bool = False,
) -> Dict[str, Any]:
    record = store.create(
        ProjectCreate(
            url=url,
            language=language,
            style=style,
            use_whisper=use_whisper,
            max_frames=max_frames,
            text_only=text_only,
        )
    )
    run_project_pipeline(record.project_id)
    final_record = store.get(record.project_id)
    project_dir = store.paths(record.project_id).project_dir
    verification = verify_project(project_dir)
    return {
        "project_id": record.project_id,
        "status": final_record.status,
        "project_dir": str(project_dir),
        "error": final_record.error,
        "warnings": final_record.warnings,
        "outputs": final_record.outputs,
        "verification": verification,
    }


def _project_result(project_id: str) -> Dict[str, Any]:
    final_record = store.get(project_id)
    project_dir = store.paths(project_id).project_dir
    verification = verify_project(project_dir)
    return {
        "project_id": project_id,
        "status": final_record.status,
        "project_dir": str(project_dir),
        "error": final_record.error,
        "warnings": final_record.warnings,
        "outputs": final_record.outputs,
        "verification": verification,
    }


def rerun_project_downstream(project_id: str) -> Dict[str, Any]:
    started, record, missing_inputs = store.try_start_downstream_rerun(project_id)
    if not started:
        result = _project_result(project_id)
        result["rerun"] = {
            "started": False,
            "scope": "downstream",
            "status": record.status,
            "missing_inputs": missing_inputs,
            "reason": "resume_artifacts_missing" if missing_inputs else "project_busy",
        }
        return result
    run_project_downstream_pipeline(project_id)
    result = _project_result(project_id)
    result["rerun"] = {"started": True, "scope": "downstream", "missing_inputs": []}
    return result


def rerun_project_visuals(project_id: str) -> Dict[str, Any]:
    started, record, missing_inputs = store.try_start_visual_rerun(project_id)
    if not started:
        result = _project_result(project_id)
        result["rerun"] = {
            "started": False,
            "scope": "visuals_and_downstream",
            "status": record.status,
            "missing_inputs": missing_inputs,
            "reason": "resume_artifacts_missing" if missing_inputs else "project_busy",
        }
        return result
    run_project_visual_pipeline(project_id)
    result = _project_result(project_id)
    result["rerun"] = {"started": True, "scope": "visuals_and_downstream", "missing_inputs": []}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one video-to-Xiaohongshu project synchronously.")
    rerun_group = parser.add_mutually_exclusive_group()
    rerun_group.add_argument("--rerun-downstream", metavar="PROJECT_ID", help="Rerun content generation from existing artifacts.")
    rerun_group.add_argument(
        "--rerun-visuals",
        metavar="PROJECT_ID",
        help="Rerun OCR/visual analysis from existing keyframes, then refresh downstream outputs.",
    )
    parser.add_argument("url", nargs="?", help="Public or authorized video URL to process.")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--style", default="干货")
    parser.add_argument("--max-frames", type=int, default=12)
    parser.add_argument("--no-whisper", action="store_true", help="Disable faster-whisper fallback when no subtitles are available.")
    parser.add_argument("--text-only", action="store_true", help="Analyze transcript/copy only; skip keyframes, OCR, screenshots, and image-card rendering.")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Exit 0 when upstream artifacts were truthfully produced but downstream failed, for environments without LLM/OCR.",
    )
    args = parser.parse_args()

    if args.rerun_downstream:
        result = rerun_project_downstream(args.rerun_downstream)
    elif args.rerun_visuals:
        result = rerun_project_visuals(args.rerun_visuals)
    else:
        if not args.url:
            parser.error("url is required unless --rerun-downstream or --rerun-visuals is used.")
        result = run_project(
            url=args.url,
            language=args.language,
            style=args.style,
            use_whisper=not args.no_whisper,
            max_frames=args.max_frames,
            text_only=args.text_only,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("rerun", {}).get("started") is False:
        return 1
    verification = result["verification"]
    if verification["completed_ok"]:
        return 0
    if args.allow_partial and verification["partial_ok"]:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
