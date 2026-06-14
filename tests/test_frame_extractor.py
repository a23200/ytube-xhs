from pathlib import Path

import pytest

from app.services.errors import PipelineError
from app.services.frame_extractor import extract_keyframes
from app.services.runtime_store import ProjectPaths, read_json


def test_extract_keyframes_writes_skipped_payload_for_transcript_only_run(tmp_path: Path):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()

    payload = extract_keyframes(
        {"video_file": None, "duration": 60},
        {"segments": [{"start": 0.0, "end": 1.0, "text": "真实字幕", "source": "subtitle"}]},
        8,
        paths,
    )

    assert payload["skipped"] is True
    assert payload["frame_count"] == 0
    assert payload["keyframes"] == []
    assert read_json(paths.analysis_dir / "keyframes.json")["skip_reason"]


def test_extract_keyframes_still_fails_without_video_or_transcript(tmp_path: Path):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()

    with pytest.raises(PipelineError) as exc_info:
        extract_keyframes({"video_file": None}, {"segments": []}, 8, paths)

    assert exc_info.value.to_dict()["code"] == "video_file_missing"
