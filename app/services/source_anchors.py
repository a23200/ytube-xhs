import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from app.services.errors import PipelineError
from app.services.runtime_store import ProjectPaths

FRAME_TIME_TOLERANCE_SECONDS = 1.5
TRANSCRIPT_TIME_TOLERANCE_SECONDS = 0.75
FRAME_FILENAME_RE = re.compile(r"^frame_\d{4}\.jpg$")


def _as_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(*values: Any) -> Optional[float]:
    for value in values:
        parsed = _as_float(value)
        if parsed is not None:
            return parsed
    return None


def _transcript_ranges(transcript_payload: Dict[str, Any]) -> List[tuple[float, float]]:
    ranges = []
    for segment in transcript_payload.get("segments", []) or []:
        start = _as_float(segment.get("start"))
        end = _as_float(segment.get("end"))
        if start is None or end is None:
            continue
        ranges.append((start, max(start, end)))
    return ranges


def _keyframe_times(paths: ProjectPaths, keyframes_payload: Dict[str, Any]) -> List[float]:
    times = []
    for frame in keyframes_payload.get("keyframes", []) or []:
        resolved = _resolve_project_path(paths, frame.get("path"))
        if resolved is None or not _is_standard_project_frame(paths, resolved):
            continue
        time = _as_float(frame.get("time"))
        if time is not None:
            times.append(time)
    return times


def _keyframe_paths(paths: ProjectPaths, keyframes_payload: Dict[str, Any]) -> Set[Path]:
    allowed = set()
    for frame in keyframes_payload.get("keyframes", []) or []:
        resolved = _resolve_project_path(paths, frame.get("path"))
        if resolved is not None and _is_standard_project_frame(paths, resolved):
            allowed.add(resolved)
    return allowed


def _time_in_transcript(time: float, transcript_ranges: List[tuple[float, float]]) -> bool:
    return any(start - TRANSCRIPT_TIME_TOLERANCE_SECONDS <= time <= end + TRANSCRIPT_TIME_TOLERANCE_SECONDS for start, end in transcript_ranges)


def _time_near_keyframe(time: float, keyframe_times: Iterable[float]) -> bool:
    return any(abs(time - frame_time) <= FRAME_TIME_TOLERANCE_SECONDS for frame_time in keyframe_times)


def _resolve_project_path(paths: ProjectPaths, value: Any) -> Optional[Path]:
    if not value:
        return None
    raw_path = Path(str(value))
    candidate = raw_path if raw_path.is_absolute() else paths.project_dir / raw_path
    try:
        resolved = candidate.resolve()
        resolved.relative_to(paths.project_dir.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _is_standard_project_frame(paths: ProjectPaths, path: Path) -> bool:
    try:
        resolved = path.resolve()
        resolved.relative_to(paths.frames_dir.resolve())
    except (OSError, ValueError):
        return False
    return resolved.is_file() and FRAME_FILENAME_RE.fullmatch(resolved.name) is not None


def _project_path_exists(paths: ProjectPaths, value: Any, allowed_paths: Optional[Set[Path]] = None) -> bool:
    resolved = _resolve_project_path(paths, value)
    if resolved is None or not resolved.exists():
        return False
    if allowed_paths is not None and resolved not in allowed_paths:
        return False
    return True


def _anchor_error(step: str, artifact: str, field: str, index: int, message: str, details: Dict[str, Any]) -> PipelineError:
    return PipelineError(
        code="source_anchor_invalid",
        message=message,
        step=step,
        details={"artifact": artifact, "field": field, "index": index, **details},
    )


def _validate_source_item(
    item: Dict[str, Any],
    *,
    paths: ProjectPaths,
    transcript_ranges: List[tuple[float, float]],
    keyframe_times: List[float],
    keyframe_paths: Set[Path],
    step: str,
    artifact: str,
    field: str,
    index: int,
    source_type: str,
) -> None:
    time = _first_float(item.get("time"), item.get("evidence_time"), item.get("source_frame_time"))
    frame_path = item.get("frame_path") or item.get("source_frame_path") or item.get("path")
    source_path = item.get("source_path")
    normalized_type = source_type.lower()

    if frame_path and not _project_path_exists(paths, frame_path, keyframe_paths):
        raise _anchor_error(
            step,
            artifact,
            field,
            index,
            "Source frame path does not match an extracted keyframe.",
            {"path": frame_path},
        )
    if source_path:
        if normalized_type in {"keyframe", "ocr", "visual", "frame"}:
            if not _project_path_exists(paths, source_path, keyframe_paths):
                raise _anchor_error(
                    step,
                    artifact,
                    field,
                    index,
                    "Frame or visual source path does not match an extracted keyframe.",
                    {"path": source_path, "source_type": source_type},
                )
        elif not _project_path_exists(paths, source_path):
            raise _anchor_error(
                step,
                artifact,
                field,
                index,
                "Source path does not point to an existing project artifact.",
                {"path": source_path},
            )
    if time is None:
        if frame_path or source_path:
            return
        raise _anchor_error(
            step,
            artifact,
            field,
            index,
            "Source anchor must include a valid time, frame path, or source path.",
            {"value": item},
        )
    if normalized_type == "transcript" and not _time_in_transcript(time, transcript_ranges):
        raise _anchor_error(
            step,
            artifact,
            field,
            index,
            "Transcript source time does not overlap any transcript segment.",
            {"time": time},
        )
    if normalized_type in {"keyframe", "ocr", "visual", "frame"} and not _time_near_keyframe(time, keyframe_times):
        raise _anchor_error(
            step,
            artifact,
            field,
            index,
            "Frame or visual source time does not match an extracted keyframe.",
            {"time": time},
        )
    if normalized_type not in {"transcript", "keyframe", "ocr", "visual", "frame"} and not (
        _time_in_transcript(time, transcript_ranges) or _time_near_keyframe(time, keyframe_times)
    ):
        raise _anchor_error(
            step,
            artifact,
            field,
            index,
            "Source time does not match any transcript segment or extracted keyframe.",
            {"time": time, "source_type": source_type},
        )


def validate_content_asset_anchors(
    content_assets: Dict[str, Any],
    transcript_payload: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    paths: ProjectPaths,
) -> None:
    transcript_ranges = _transcript_ranges(transcript_payload)
    keyframe_times = _keyframe_times(paths, keyframes_payload)
    keyframe_paths = _keyframe_paths(paths, keyframes_payload)
    for point in content_assets.get("core_points", []) or []:
        for evidence_index, evidence in enumerate(point.get("evidence", []) or []):
            if not isinstance(evidence, dict):
                continue
            _validate_source_item(
                evidence,
                paths=paths,
                transcript_ranges=transcript_ranges,
                keyframe_times=keyframe_times,
                keyframe_paths=keyframe_paths,
                step="planning_content",
                artifact="content-assets.json",
                field="core_points.evidence",
                index=evidence_index,
                source_type=str(evidence.get("type") or ""),
            )
    for index, item in enumerate(content_assets.get("source_evidence", []) or []):
        if not isinstance(item, dict):
            continue
        _validate_source_item(
            item,
            paths=paths,
            transcript_ranges=transcript_ranges,
            keyframe_times=keyframe_times,
            keyframe_paths=keyframe_paths,
            step="planning_content",
            artifact="content-assets.json",
            field="source_evidence",
            index=index,
            source_type=str(item.get("source_type") or ""),
        )


def validate_xhs_post_anchors(
    xhs_post: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    paths: ProjectPaths,
) -> None:
    has_keyframe_items = any(isinstance(frame, dict) for frame in keyframes_payload.get("keyframes", []) or [])
    keyframe_times = _keyframe_times(paths, keyframes_payload)
    keyframe_paths = _keyframe_paths(paths, keyframes_payload)
    if not has_keyframe_items:
        return
    for index, item in enumerate(xhs_post.get("image_plan", []) or []):
        if not isinstance(item, dict):
            continue
        time = _as_float(item.get("source_frame_time"))
        frame_path = item.get("source_frame_path")
        if frame_path:
            if _project_path_exists(paths, frame_path, keyframe_paths):
                continue
            raise _anchor_error(
                "writing_xhs",
                "xiaohongshu-post.json",
                "image_plan",
                index,
                "Image plan source frame path does not match an extracted keyframe.",
                {"path": frame_path},
            )
        if time is not None and _time_near_keyframe(time, keyframe_times):
            continue
        raise _anchor_error(
            "writing_xhs",
            "xiaohongshu-post.json",
            "image_plan",
            index,
            "Image plan source must reference an extracted keyframe time or path.",
            {"time": item.get("source_frame_time"), "path": frame_path},
        )


def validate_image_prompt_anchors(
    image_prompts: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    paths: ProjectPaths,
) -> None:
    has_keyframe_items = any(isinstance(frame, dict) for frame in keyframes_payload.get("keyframes", []) or [])
    keyframe_times = _keyframe_times(paths, keyframes_payload)
    keyframe_paths = _keyframe_paths(paths, keyframes_payload)
    if not has_keyframe_items:
        return
    for index, item in enumerate(image_prompts.get("image_prompts", []) or []):
        if not isinstance(item, dict):
            continue
        time = _as_float(item.get("source_frame_time"))
        frame_path = item.get("source_frame_path")
        if frame_path:
            if _project_path_exists(paths, frame_path, keyframe_paths):
                continue
            raise _anchor_error(
                "writing_xhs",
                "image-prompts.json",
                "image_prompts",
                index,
                "Image prompt source frame path does not match an extracted keyframe.",
                {"path": frame_path},
            )
        if time is not None and _time_near_keyframe(time, keyframe_times):
            continue
        raise _anchor_error(
            "writing_xhs",
            "image-prompts.json",
            "image_prompts",
            index,
            "Image prompt source must reference an extracted keyframe time or path.",
            {"time": item.get("source_frame_time"), "path": frame_path},
        )
