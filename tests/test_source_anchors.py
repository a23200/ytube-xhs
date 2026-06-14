from pathlib import Path

import pytest

from app.services.errors import PipelineError
from app.services.runtime_store import ProjectPaths
from app.services.source_anchors import (
    validate_content_asset_anchors,
    validate_image_prompt_anchors,
    validate_xhs_post_anchors,
)


def _paths_with_frame(tmp_path: Path) -> tuple[ProjectPaths, Path]:
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    frame_path = paths.frames_dir / "frame_0001.jpg"
    frame_path.write_bytes(b"jpg")
    return paths, frame_path


def _transcript() -> dict:
    return {
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "字幕文本",
                "source": "subtitle",
            }
        ]
    }


def _keyframes(frame_path: Path) -> dict:
    return {
        "keyframes": [
            {
                "time": 1.0,
                "path": str(frame_path),
                "score": 0.9,
                "reason": "test",
            }
        ]
    }


def test_content_asset_anchors_accept_real_transcript_and_keyframe_sources(tmp_path: Path):
    paths, frame_path = _paths_with_frame(tmp_path)
    content_assets = {
        "core_points": [
            {
                "point": "观点",
                "evidence": [{"type": "transcript", "time": 1.0, "text": "字幕文本"}],
            }
        ],
        "source_evidence": [
            {
                "claim": "画面证据",
                "source_type": "keyframe",
                "source_path": str(frame_path),
                "source_text": "关键帧",
            }
        ],
    }

    validate_content_asset_anchors(content_assets, _transcript(), _keyframes(frame_path), paths)


def test_content_asset_anchors_reject_keyframe_source_path_that_is_not_keyframe(tmp_path: Path):
    paths, frame_path = _paths_with_frame(tmp_path)
    metadata_path = paths.source_dir / "metadata.json"
    metadata_path.write_text("{}", encoding="utf-8")
    content_assets = {
        "core_points": [
            {
                "point": "观点",
                "evidence": [{"type": "transcript", "time": 1.0, "text": "字幕文本"}],
            }
        ],
        "source_evidence": [
            {
                "claim": "画面证据",
                "source_type": "keyframe",
                "source_path": str(metadata_path),
                "source_text": "错误路径",
            }
        ],
    }

    with pytest.raises(PipelineError) as exc_info:
        validate_content_asset_anchors(content_assets, _transcript(), _keyframes(frame_path), paths)

    error = exc_info.value.to_dict()
    assert error["code"] == "source_anchor_invalid"
    assert error["details"]["source_type"] == "keyframe"


def test_content_asset_anchors_reject_fabricated_transcript_time(tmp_path: Path):
    paths, frame_path = _paths_with_frame(tmp_path)
    content_assets = {
        "core_points": [
            {
                "point": "观点",
                "evidence": [{"type": "transcript", "time": 999.0, "text": "字幕文本"}],
            }
        ],
        "source_evidence": [{"claim": "观点", "source_type": "transcript", "time": 1.0, "source_text": "字幕文本"}],
    }

    with pytest.raises(PipelineError) as exc_info:
        validate_content_asset_anchors(content_assets, _transcript(), _keyframes(frame_path), paths)

    error = exc_info.value.to_dict()
    assert error["code"] == "source_anchor_invalid"
    assert error["details"]["field"] == "core_points.evidence"


def test_xhs_post_anchors_reject_non_keyframe_time(tmp_path: Path):
    paths, frame_path = _paths_with_frame(tmp_path)
    post = {
        "image_plan": [
            {
                "page": 1,
                "role": "cover",
                "caption": "封面",
                "source_frame_time": 99.0,
                "content_point": "观点",
            }
        ]
    }

    with pytest.raises(PipelineError) as exc_info:
        validate_xhs_post_anchors(post, _keyframes(frame_path), paths)

    assert exc_info.value.to_dict()["code"] == "source_anchor_invalid"


def test_xhs_post_anchors_allow_transcript_only_route_without_keyframes(tmp_path: Path):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    post = {
        "image_plan": [
            {
                "page": 1,
                "role": "cover",
                "caption": "封面",
                "source_frame_time": None,
                "source_frame_path": None,
                "content_point": "观点",
            }
        ]
    }

    validate_xhs_post_anchors(post, {"keyframes": [], "skipped": True}, paths)


def test_xhs_post_anchors_reject_time_from_keyframe_outside_frames_dir(tmp_path: Path):
    paths, _frame_path = _paths_with_frame(tmp_path)
    wrong_dir_frame = paths.source_dir / "frame_0001.jpg"
    wrong_dir_frame.write_bytes(b"jpg")
    post = {
        "image_plan": [
            {
                "page": 1,
                "role": "cover",
                "caption": "封面",
                "source_frame_time": 1.0,
                "content_point": "观点",
            }
        ]
    }

    with pytest.raises(PipelineError) as exc_info:
        validate_xhs_post_anchors(post, _keyframes(wrong_dir_frame), paths)

    assert exc_info.value.to_dict()["code"] == "source_anchor_invalid"


def test_content_asset_anchors_reject_keyframe_time_from_bad_frame_path(tmp_path: Path):
    paths, _frame_path = _paths_with_frame(tmp_path)
    wrong_dir_frame = paths.source_dir / "frame_0001.jpg"
    wrong_dir_frame.write_bytes(b"jpg")
    content_assets = {
        "core_points": [
            {
                "point": "观点",
                "evidence": [{"type": "keyframe", "time": 1.0, "text": "画面"}],
            }
        ],
        "source_evidence": [{"claim": "观点", "source_type": "transcript", "time": 1.0, "source_text": "字幕文本"}],
    }

    with pytest.raises(PipelineError) as exc_info:
        validate_content_asset_anchors(content_assets, _transcript(), _keyframes(wrong_dir_frame), paths)

    assert exc_info.value.to_dict()["code"] == "source_anchor_invalid"


def test_image_prompt_anchors_reject_wrong_frame_path_even_with_valid_time(tmp_path: Path):
    paths, frame_path = _paths_with_frame(tmp_path)
    wrong_frame = paths.frames_dir / "frame_9999.jpg"
    wrong_frame.write_bytes(b"jpg")
    prompts = {
        "image_prompts": [
            {
                "page": 1,
                "role": "cover",
                "caption": "封面",
                "source_frame_time": 1.0,
                "source_frame_path": str(wrong_frame),
                "visual_reference": "参考",
                "image_prompt": "构图、主体、背景、色调、留白。",
                "negative_prompt": "不要复刻截图。",
            }
        ]
    }

    with pytest.raises(PipelineError) as exc_info:
        validate_image_prompt_anchors(prompts, _keyframes(frame_path), paths)

    error = exc_info.value.to_dict()
    assert error["code"] == "source_anchor_invalid"
    assert error["details"]["path"] == str(wrong_frame)


def test_image_prompt_anchors_allow_transcript_only_route_without_keyframes(tmp_path: Path):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    prompts = {
        "image_prompts": [
            {
                "page": 1,
                "role": "cover",
                "caption": "封面",
                "source_frame_time": None,
                "source_frame_path": None,
                "visual_reference": "无可用关键帧，基于文章要点生成",
                "image_prompt": "构图为上标题下信息卡片，主体是观点关系图，背景干净，色调明亮，留白充足。",
                "negative_prompt": "不要复刻截图。",
            }
        ]
    }

    validate_image_prompt_anchors(prompts, {"keyframes": [], "skipped": True}, paths)
