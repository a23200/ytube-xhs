from pathlib import Path

import pytest

from app.services import visual_analyzer
from app.services.errors import PipelineError
from app.services.runtime_store import ProjectPaths, read_json


def _write_test_image(path: Path) -> None:
    import cv2
    import numpy as np

    image = np.full((32, 64, 3), 220, dtype=np.uint8)
    image[:, :32] = (30, 80, 220)
    cv2.putText(image, "X", (20, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
    ok = cv2.imwrite(str(path), image)
    assert ok


def test_visual_analyzer_respects_disabled_ocr_provider(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(visual_analyzer.settings, "ocr_provider", "none")
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    frame_path = paths.frames_dir / "frame_0001.jpg"
    _write_test_image(frame_path)

    payload = visual_analyzer.analyze_visuals(
        {
            "keyframes": [
                {
                    "time": 1.0,
                    "path": str(frame_path),
                }
            ]
        },
        language="en",
        paths=paths,
    )

    assert payload["ocr_provider"] == "none"
    assert payload["requested_ocr_provider"] == "none"
    assert payload["warnings"] == ["OCR disabled by XHS_OCR_PROVIDER=none."]
    assert payload["frames"][0]["ocr_text"] == ""
    assert payload["frames"][0]["frame_metrics"]["available"] is True
    assert payload["frames"][0]["frame_metrics"]["width"] == 64
    assert "Frame metrics:" in payload["frames"][0]["visual_summary"]
    assert read_json(paths.analysis_dir / "visual-analysis.json")["ocr_provider"] == "none"


def test_visual_analyzer_writes_skipped_payload_when_keyframes_skipped(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(visual_analyzer.settings, "ocr_provider", "none")
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()

    payload = visual_analyzer.analyze_visuals(
        {
            "skipped": True,
            "skip_reason": "No video file is available; continuing with transcript-only analysis.",
            "analysis_mode": "transcript_only",
            "keyframes": [],
        },
        language="zh",
        paths=paths,
    )

    assert payload["skipped"] is True
    assert payload["frames"] == []
    assert payload["ocr_enabled"] is False
    assert payload["analysis_mode"] == "transcript_only"
    assert read_json(paths.analysis_dir / "visual-analysis.json")["skip_reason"]


def test_visual_analyzer_reports_unavailable_tesseract(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(visual_analyzer.settings, "ocr_provider", "tesseract")
    monkeypatch.setattr(visual_analyzer, "find_command", lambda command: None)
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    frame_path = paths.frames_dir / "frame_0001.jpg"
    frame_path.write_bytes(b"not a real image")

    payload = visual_analyzer.analyze_visuals(
        {"keyframes": [{"time": 1.0, "path": str(frame_path)}]},
        language="en",
        paths=paths,
    )

    assert payload["ocr_provider"] == "none"
    assert "Tesseract OCR was requested but is unavailable" in payload["warnings"][0]


def test_tesseract_provider_falls_back_to_english_when_chinese_data_missing(monkeypatch):
    monkeypatch.setattr(visual_analyzer, "find_command", lambda command: "/usr/bin/tesseract")

    def fake_run(command, capture_output, text, timeout, check, **kwargs):
        assert command == ["/usr/bin/tesseract", "--list-langs"]

        class Result:
            returncode = 0
            stdout = "List of available languages in /tmp:\neng\nosd\n"
            stderr = ""

        return Result()

    monkeypatch.setattr(visual_analyzer.subprocess, "run", fake_run)

    provider = visual_analyzer.TesseractOCRProvider("zh")

    assert provider.available() == (True, "")
    assert provider.language == "eng"
    assert provider._warning == "Tesseract Chinese language data chi_sim is not installed; falling back to eng OCR."


def test_tesseract_provider_reports_missing_language_data(monkeypatch):
    monkeypatch.setattr(visual_analyzer, "find_command", lambda command: "/usr/bin/tesseract")

    def fake_run(command, capture_output, text, timeout, check, **kwargs):
        assert command == ["/usr/bin/tesseract", "--list-langs"]

        class Result:
            returncode = 0
            stdout = "List of available languages in /tmp:\nosd\n"
            stderr = ""

        return Result()

    monkeypatch.setattr(visual_analyzer.subprocess, "run", fake_run)

    provider = visual_analyzer.TesseractOCRProvider("en")

    ok, reason = provider.available()
    assert ok is False
    assert "Tesseract language data for eng is not installed" in reason


def test_visual_analyzer_records_metric_warning_for_unreadable_frame(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(visual_analyzer.settings, "ocr_provider", "none")
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    frame_path = paths.frames_dir / "frame_0001.jpg"
    frame_path.write_bytes(b"not a real image")

    payload = visual_analyzer.analyze_visuals(
        {"keyframes": [{"time": 1.0, "path": str(frame_path)}]},
        language="en",
        paths=paths,
    )

    assert payload["frames"][0]["frame_metrics"]["available"] is False
    assert any("Could not read frame image" in warning for warning in payload["warnings"])
    assert "Frame metrics unavailable" in payload["frames"][0]["visual_summary"]


def test_visual_analyzer_rejects_all_invalid_keyframe_paths(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(visual_analyzer.settings, "ocr_provider", "none")
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    outside_frame = tmp_path / "frame_0001.jpg"
    outside_frame.write_bytes(b"jpg")

    with pytest.raises(PipelineError) as exc_info:
        visual_analyzer.analyze_visuals(
            {"keyframes": [{"time": 1.0, "path": str(outside_frame)}]},
            language="en",
            paths=paths,
        )

    error = exc_info.value.to_dict()
    assert error["code"] == "no_valid_visual_frames"
    assert error["step"] == "analyzing_visuals"
    assert not (paths.analysis_dir / "visual-analysis.json").exists()


def test_visual_analyzer_skips_invalid_frame_and_analyzes_valid_frame(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(visual_analyzer.settings, "ocr_provider", "none")
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    valid_frame = paths.frames_dir / "frame_0001.jpg"
    invalid_frame = paths.source_dir / "frame_0002.jpg"
    _write_test_image(valid_frame)
    invalid_frame.write_bytes(b"jpg")

    payload = visual_analyzer.analyze_visuals(
        {
            "keyframes": [
                {"time": 1.0, "path": str(invalid_frame)},
                {"time": 2.0, "path": str(valid_frame)},
            ]
        },
        language="en",
        paths=paths,
    )

    assert len(payload["frames"]) == 1
    assert payload["frames"][0]["path"] == str(valid_frame)
    assert any("non-standard frame path" in warning for warning in payload["warnings"])
