import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.schemas.models import ProjectCreate
from app.services.runtime_store import ProjectStore, write_json
from scripts import run_project as run_project_script


def _visual_frame(frame_path: Path) -> dict:
    return {
        "time": 0.5,
        "path": str(frame_path),
        "ocr_text": "",
        "visual_summary": "summary",
        "detected_objects": [],
        "screen_text_confidence": 0.0,
        "ocr_provider": "none",
        "frame_metrics": {
            "available": True,
            "width": 320,
            "height": 240,
            "brightness": 100.0,
            "sharpness": 200.0,
            "brightness_label": "medium",
            "sharpness_label": "sharp",
            "color_tone": "neutral",
        },
    }


def test_run_project_creates_record_runs_pipeline_and_verifies(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(run_project_script, "store", test_store)

    def fake_pipeline(project_id: str) -> None:
        paths = test_store.paths(project_id)
        video_path = paths.source_dir / "source.mp4"
        frame_path = paths.frames_dir / "frame_0001.jpg"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        frame_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"mp4")
        frame_path.write_bytes(b"jpg")
        write_json(
            paths.source_dir / "metadata.json",
            {
                "video_id": "v1",
                "url": "https://example.com/video",
                "title": "Title",
                "author": "Author",
                "duration": 12,
                "video_file": str(video_path),
                "available_subtitles": ["en"],
                "automatic_captions": ["en-auto"],
            },
        )
        write_json(
            paths.transcript_dir / "transcript.json",
            {
                "source": "subtitle",
                "segment_count": 1,
                "segments": [{"start": 0.0, "end": 1.0, "text": "字幕", "source": "subtitle"}],
            },
        )
        write_json(
            paths.analysis_dir / "keyframes.json",
            {
                "frame_count": 1,
                "keyframes": [{"time": 0.5, "path": str(frame_path), "score": 0.9, "reason": "test"}],
            },
        )
        write_json(
            paths.analysis_dir / "visual-analysis.json",
            {
                "frames": [_visual_frame(frame_path)],
                "warnings": [],
            },
        )
        write_json(paths.analysis_dir / "run-metadata.json", {"status": "failed"})
        write_json(
            paths.analysis_dir / "asset-package.json",
            {"status": "partial_failed", "error": {"code": "llm_unavailable"}},
        )
        test_store.add_output(project_id, "metadata", paths.source_dir / "metadata.json")
        test_store.add_output(project_id, "transcript", paths.transcript_dir / "transcript.json")
        test_store.add_output(project_id, "keyframes", paths.analysis_dir / "keyframes.json")
        test_store.add_output(project_id, "visual_analysis", paths.analysis_dir / "visual-analysis.json")
        test_store.add_output(project_id, "asset_package", paths.analysis_dir / "asset-package.json")
        test_store.add_output(project_id, "run_metadata", paths.analysis_dir / "run-metadata.json")
        test_store.fail(project_id, {"code": "llm_unavailable", "message": "missing LLM", "step": "planning_content"})

    monkeypatch.setattr(run_project_script, "run_project_pipeline", fake_pipeline)

    result = run_project_script.run_project(
        "https://example.com/video",
        language="zh",
        style="干货",
        use_whisper=True,
        max_frames=8,
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "llm_unavailable"
    assert result["verification"]["partial_ok"] is True
    assert result["verification"]["issues"] == []
    assert Path(result["project_dir"]).exists()


def test_run_project_reruns_downstream_existing_project(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(run_project_script, "store", test_store)
    record = test_store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="干货",
            use_whisper=True,
            max_frames=8,
        )
    )
    paths = test_store.paths(record.project_id)
    video_path = paths.source_dir / "source.mp4"
    frame_path = paths.frames_dir / "frame_0001.jpg"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"mp4")
    frame_path.write_bytes(b"jpg")
    write_json(
        paths.source_dir / "metadata.json",
        {
            "video_id": "v1",
            "url": "https://example.com/video",
            "title": "Title",
            "author": "Author",
            "duration": 12,
            "video_file": str(video_path),
            "available_subtitles": ["en"],
            "automatic_captions": ["en-auto"],
        },
    )
    write_json(
        paths.transcript_dir / "transcript.json",
        {
            "source": "subtitle",
            "segment_count": 1,
            "segments": [{"start": 0.0, "end": 1.0, "text": "字幕", "source": "subtitle"}],
        },
    )
    write_json(
        paths.analysis_dir / "keyframes.json",
        {
            "frame_count": 1,
            "keyframes": [{"time": 0.5, "path": str(frame_path), "score": 0.9, "reason": "test"}],
        },
    )
    write_json(
        paths.analysis_dir / "visual-analysis.json",
        {
            "frames": [_visual_frame(frame_path)],
            "warnings": [],
        },
    )
    for kind in ["metadata", "transcript", "keyframes", "visual_analysis"]:
        test_store.add_output(record.project_id, kind, paths.file_for_kind(kind))
    test_store.fail(record.project_id, {"code": "llm_unavailable", "message": "missing", "step": "planning_content"})
    called = []

    def fake_downstream(project_id: str) -> None:
        called.append(project_id)
        write_json(paths.analysis_dir / "asset-package.json", {"status": "partial_failed", "error": {"code": "x"}})
        test_store.add_output(project_id, "asset_package", paths.file_for_kind("asset_package"))
        test_store.fail(project_id, {"code": "llm_unavailable", "message": "missing", "step": "planning_content"})

    monkeypatch.setattr(run_project_script, "run_project_downstream_pipeline", fake_downstream)

    result = run_project_script.rerun_project_downstream(record.project_id)

    assert called == [record.project_id]
    assert result["rerun"] == {"started": True, "scope": "downstream", "missing_inputs": []}
    assert result["verification"]["partial_ok"] is True


def test_run_project_rerun_reports_missing_inputs(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(run_project_script, "store", test_store)
    record = test_store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="干货",
            use_whisper=True,
            max_frames=8,
        )
    )
    test_store.fail(record.project_id, {"code": "llm_unavailable", "message": "missing", "step": "planning_content"})

    result = run_project_script.rerun_project_visuals(record.project_id)

    assert result["rerun"]["started"] is False
    assert result["rerun"]["scope"] == "visuals_and_downstream"
    assert result["rerun"]["reason"] == "resume_artifacts_missing"
    assert result["rerun"]["missing_inputs"] == ["metadata", "transcript", "keyframes"]


def test_run_project_script_is_directly_executable():
    result = subprocess.run(
        [".venv/bin/python", "scripts/run_project.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Run one video-to-Xiaohongshu project synchronously" in result.stdout
    assert "--rerun-downstream" in result.stdout
    assert "--rerun-visuals" in result.stdout


def test_main_exits_zero_for_partial_result_when_allowed(monkeypatch, capsys):
    def fake_run_project(url: str, language: str, style: str, use_whisper: bool, max_frames: int) -> dict:
        return {
            "project_id": "p1",
            "status": "failed",
            "project_dir": "/tmp/p1",
            "error": {"code": "llm_unavailable"},
            "warnings": [],
            "outputs": {},
            "verification": {"completed_ok": False, "partial_ok": True},
        }

    monkeypatch.setattr(run_project_script, "run_project", fake_run_project)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_project.py",
            "https://example.com/video",
            "--language",
            "zh",
            "--style",
            "干货",
            "--max-frames",
            "8",
            "--allow-partial",
        ],
    )

    exit_code = run_project_script.main()

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["project_id"] == "p1"
    assert payload["verification"]["partial_ok"] is True


def test_main_exits_nonzero_for_partial_result_without_allow_partial(monkeypatch):
    monkeypatch.setattr(
        run_project_script,
        "run_project",
        lambda **kwargs: {
            "project_id": "p1",
            "status": "failed",
            "project_dir": "/tmp/p1",
            "error": {"code": "llm_unavailable"},
            "warnings": [],
            "outputs": {},
            "verification": {"completed_ok": False, "partial_ok": True},
        },
    )
    monkeypatch.setattr(sys, "argv", ["run_project.py", "https://example.com/video"])

    assert run_project_script.main() == 1


def test_main_exits_nonzero_when_rerun_does_not_start(monkeypatch):
    monkeypatch.setattr(
        run_project_script,
        "rerun_project_visuals",
        lambda project_id: {
            "project_id": project_id,
            "status": "failed",
            "project_dir": "/tmp/p1",
            "error": {"code": "llm_unavailable"},
            "warnings": [],
            "outputs": {},
            "verification": {"completed_ok": False, "partial_ok": False},
            "rerun": {
                "started": False,
                "scope": "visuals_and_downstream",
                "missing_inputs": ["metadata"],
                "reason": "resume_artifacts_missing",
            },
        },
    )
    monkeypatch.setattr(sys, "argv", ["run_project.py", "--rerun-visuals", "p1", "--allow-partial"])

    assert run_project_script.main() == 1


def test_main_requires_url_unless_rerunning(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_project.py"])

    with pytest.raises(SystemExit) as exc_info:
        run_project_script.main()

    assert exc_info.value.code == 2
