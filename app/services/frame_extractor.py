from pathlib import Path
from typing import Any, Dict, List

from app.schemas.models import Keyframe, TranscriptSegment
from app.services.errors import PipelineError
from app.services.media_utils import ffprobe_duration, require_command, run_command
from app.services.runtime_store import ProjectPaths, write_json
from app.services.text_utils import transcript_window


def _scene_timestamps(video_path: Path) -> List[float]:
    try:
        from scenedetect import ContentDetector, SceneManager, open_video
    except Exception as exc:
        raise PipelineError(
            code="missing_dependency",
            message="PySceneDetect is not installed, so keyframe scene detection cannot run.",
            step="extracting_frames",
            details={"dependency": "scenedetect"},
        ) from exc

    try:
        video = open_video(str(video_path))
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=27.0))
        scene_manager.detect_scenes(video)
        scene_list = scene_manager.get_scene_list()
    except Exception as exc:
        raise PipelineError(
            code="scene_detection_failed",
            message="PySceneDetect failed to analyze scenes.",
            step="extracting_frames",
            details={"error": str(exc), "video_file": str(video_path)},
        ) from exc

    timestamps = []
    for start, end in scene_list:
        midpoint = (start.get_seconds() + end.get_seconds()) / 2.0
        if midpoint > 0:
            timestamps.append(midpoint)
    return timestamps


def _sample_timestamps(duration: float, desired: int) -> List[float]:
    if duration <= 0:
        return []
    count = max(1, desired)
    padding = min(2.0, duration * 0.08)
    start = padding
    end = max(start + 0.1, duration - padding)
    if count == 1:
        return [(start + end) / 2.0]
    step = (end - start) / (count - 1)
    return [start + i * step for i in range(count)]


def _image_hash(image) -> str:
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (8, 8))
    avg = small.mean()
    bits = small > avg
    return "".join("1" if bit else "0" for bit in bits.flatten())


def _hamming(a: str, b: str) -> int:
    return sum(left != right for left, right in zip(a, b))


def _score_frame(path: Path) -> Dict[str, float]:
    try:
        import cv2
    except Exception as exc:
        raise PipelineError(
            code="missing_dependency",
            message="OpenCV is not installed, so keyframe filtering cannot run.",
            step="extracting_frames",
            details={"dependency": "opencv-python-headless"},
        ) from exc
    image = cv2.imread(str(path))
    if image is None:
        return {"valid": 0.0, "brightness": 0.0, "sharpness": 0.0, "score": 0.0, "hash": ""}
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    too_dark_or_bright = brightness < 12.0 or brightness > 242.0
    too_blurry = sharpness < 20.0
    valid = not too_dark_or_bright and not too_blurry
    score = min(1.0, (sharpness / 400.0) * 0.7 + (1.0 - abs(brightness - 128.0) / 128.0) * 0.3)
    return {
        "valid": 1.0 if valid else 0.0,
        "brightness": brightness,
        "sharpness": sharpness,
        "score": round(score, 4),
        "hash": _image_hash(image),
    }


def _extract_frame(video_path: Path, timestamp: float, output_path: Path) -> None:
    require_command("ffmpeg", "extracting_frames")
    run_command(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(output_path),
        ],
        step="extracting_frames",
        timeout=120,
    )


def extract_keyframes(
    metadata: Dict[str, Any],
    transcript_payload: Dict[str, Any],
    max_frames: int,
    paths: ProjectPaths,
) -> Dict[str, Any]:
    video_file = metadata.get("video_file")
    if not video_file or not Path(video_file).exists():
        if transcript_payload.get("segments"):
            payload = {
                "video_file": video_file,
                "duration": metadata.get("duration"),
                "requested_max_frames": max_frames,
                "frame_count": 0,
                "keyframes": [],
                "skipped": True,
                "skip_reason": "No video file is available; continuing with transcript-only analysis.",
            }
            write_json(paths.analysis_dir / "keyframes.json", payload)
            return payload
        raise PipelineError(
            code="video_file_missing",
            message="No video file is available for keyframe extraction.",
            step="extracting_frames",
            details={"video_file": video_file},
        )
    video_path = Path(video_file)
    duration = float(metadata.get("duration") or ffprobe_duration(video_path))
    scene_times = _scene_timestamps(video_path)
    desired_candidates = max(max_frames * 3, 24)
    timestamps = sorted(set(round(item, 3) for item in scene_times + _sample_timestamps(duration, desired_candidates)))

    transcript_segments = [
        TranscriptSegment(**item) for item in transcript_payload.get("segments", [])
    ]
    selected: List[Keyframe] = []
    hashes: List[str] = []
    paths.frames_dir.mkdir(parents=True, exist_ok=True)

    for index, timestamp in enumerate(timestamps, start=1):
        if len(selected) >= max_frames:
            break
        candidate_path = paths.frames_dir / f"candidate_{index:04d}.jpg"
        try:
            _extract_frame(video_path, timestamp, candidate_path)
        except PipelineError:
            raise
        score = _score_frame(candidate_path)
        if not score["valid"]:
            candidate_path.unlink(missing_ok=True)
            continue
        if any(_hamming(score["hash"], old_hash) <= 6 for old_hash in hashes):
            candidate_path.unlink(missing_ok=True)
            continue

        frame_name = f"frame_{len(selected) + 1:04d}.jpg"
        frame_path = paths.frames_dir / frame_name
        candidate_path.replace(frame_path)
        hashes.append(score["hash"])
        selected.append(
            Keyframe(
                time=round(timestamp, 3),
                path=str(frame_path),
                score=float(score["score"]),
                reason=(
                    f"scene_or_interval_candidate; sharpness={score['sharpness']:.1f}; "
                    f"brightness={score['brightness']:.1f}"
                ),
                related_transcript_text=transcript_window(transcript_segments, timestamp),
            )
        )

    for leftover in paths.frames_dir.glob("candidate_*.jpg"):
        leftover.unlink(missing_ok=True)

    if not selected:
        raise PipelineError(
            code="no_valid_keyframes",
            message="Frame extraction ran but no valid non-black, sharp, non-duplicate frame was selected.",
            step="extracting_frames",
            details={"video_file": str(video_path), "duration": duration},
        )

    payload = {
        "video_file": str(video_path),
        "duration": duration,
        "requested_max_frames": max_frames,
        "frame_count": len(selected),
        "keyframes": [frame.model_dump(mode="json") for frame in selected],
    }
    write_json(paths.analysis_dir / "keyframes.json", payload)
    return payload
