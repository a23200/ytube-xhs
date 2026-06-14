import sys
import types
from pathlib import Path

import pytest

from app.services.errors import PipelineError
from app.services.report_writer import write_partial_asset_package
from app.services.runtime_store import ProjectPaths, read_json, write_json
from app.services.transcript import build_transcript


def test_no_subtitle_without_whisper_flag_fails_structurally(tmp_path: Path):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()

    with pytest.raises(PipelineError) as exc_info:
        build_transcript(
            metadata={"video_file": None},
            language="zh",
            use_whisper=False,
            paths=paths,
        )

    error = exc_info.value.to_dict()
    assert error["code"] == "no_transcript_source"
    assert error["step"] == "transcript"
    assert not (paths.transcript_dir / "transcript.json").exists()


def test_build_transcript_uses_subtitle_before_whisper(tmp_path: Path):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    subtitle = paths.source_dir / "subtitles.vtt"
    subtitle.write_text(
        """WEBVTT

00:00:01.000 --> 00:00:02.000
Hello <c>there</c>

00:00:02.100 --> 00:00:03.000
Hello there

00:00:04.000 --> 00:00:05.000
Next idea
""",
        encoding="utf-8",
    )

    payload = build_transcript(
        metadata={"subtitle_file": str(subtitle)},
        language="en",
        use_whisper=False,
        paths=paths,
    )

    assert payload["source"] == "subtitle"
    assert payload["segment_count"] == 2
    assert payload["segments"][0]["text"] == "Hello there"
    assert (paths.source_dir / "transcript_source.txt").exists()
    assert read_json(paths.transcript_dir / "transcript.json")["segment_count"] == 2


def test_build_transcript_parses_ass_subtitles_without_whisper(tmp_path: Path):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    subtitle = paths.source_dir / "subtitles.ass"
    subtitle.write_text(
        """[Script Info]
Title: Example

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour
Style: Default,Arial,20,&H00FFFFFF

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.20,0:00:02.80,Default,,0,0,0,,{\\an8}Hello\\Nthere
Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,Next, idea
""",
        encoding="utf-8",
    )

    payload = build_transcript(
        metadata={"subtitle_file": str(subtitle), "video_file": None},
        language="en",
        use_whisper=False,
        paths=paths,
    )

    assert payload["source"] == "subtitle"
    assert payload["segment_count"] == 1
    assert payload["segments"][0]["start"] == 1.2
    assert payload["segments"][0]["end"] == 4.0
    assert payload["segments"][0]["text"] == "Hello there Next, idea"
    assert payload["segments"][0]["source"] == "subtitle:subtitles.ass"
    assert "Dialogue:" in (paths.source_dir / "transcript_source.txt").read_text(encoding="utf-8")


def test_build_transcript_parses_youtube_srv_xml_without_whisper(tmp_path: Path):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    subtitle = paths.source_dir / "subtitles.srv3"
    subtitle.write_text(
        """<?xml version="1.0" encoding="utf-8" ?>
<transcript>
  <text start="1.2" dur="1.1">Hello &amp; welcome</text>
  <text start="2.4" dur="0.8"><![CDATA[next idea]]></text>
</transcript>
""",
        encoding="utf-8",
    )

    payload = build_transcript(
        metadata={"subtitle_file": str(subtitle), "video_file": None},
        language="en",
        use_whisper=False,
        paths=paths,
    )

    assert payload["source"] == "subtitle"
    assert payload["segment_count"] == 1
    assert payload["segments"][0]["start"] == 1.2
    assert payload["segments"][0]["end"] == 3.2
    assert payload["segments"][0]["text"] == "Hello & welcome next idea"
    assert payload["segments"][0]["source"] == "subtitle:subtitles.srv3"


def test_build_transcript_parses_ttml_without_whisper(tmp_path: Path):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    subtitle = paths.source_dir / "subtitles.ttml"
    subtitle.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<tt xmlns="http://www.w3.org/ns/ttml">
  <body>
    <div>
      <p begin="00:00:01.000" end="00:00:02.500">First line<br />second line</p>
      <p begin="4.0s" dur="1.0s">Third line</p>
    </div>
  </body>
</tt>
""",
        encoding="utf-8",
    )

    payload = build_transcript(
        metadata={"subtitle_file": str(subtitle), "video_file": None},
        language="en",
        use_whisper=False,
        paths=paths,
    )

    assert payload["source"] == "subtitle"
    assert payload["segment_count"] == 2
    assert payload["segments"][0]["text"] == "First line second line"
    assert payload["segments"][0]["start"] == 1.0
    assert payload["segments"][0]["end"] == 2.5
    assert payload["segments"][1]["text"] == "Third line"


def test_whisper_model_load_failure_reports_configuration_context(tmp_path: Path, monkeypatch):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    video = paths.source_dir / "source.mp4"
    audio = paths.source_dir / "audio.mp3"
    video.write_bytes(b"mp4")
    audio.write_bytes(b"mp3")

    class FailingWhisperModel:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("model cache unavailable")

    module = types.SimpleNamespace(WhisperModel=FailingWhisperModel)
    monkeypatch.setitem(sys.modules, "faster_whisper", module)

    with pytest.raises(PipelineError) as exc_info:
        build_transcript(
            metadata={"video_file": str(video), "audio_file": str(audio)},
            language="zh",
            use_whisper=True,
            paths=paths,
        )

    error = exc_info.value.to_dict()
    assert error["code"] == "whisper_model_unavailable"
    assert error["step"] == "transcript"
    assert error["details"]["model"]
    assert error["details"]["device"]
    assert error["details"]["compute_type"]
    assert error["details"]["audio_file"] == str(audio)
    assert error["details"]["error_type"] == "RuntimeError"
    assert not (paths.transcript_dir / "transcript.json").exists()


def test_whisper_transcribe_failure_reports_audio_context(tmp_path: Path, monkeypatch):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    video = paths.source_dir / "source.mp4"
    audio = paths.source_dir / "audio.mp3"
    video.write_bytes(b"mp4")
    audio.write_bytes(b"mp3")

    class FailingWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            raise ValueError("audio decode failed")

    module = types.SimpleNamespace(WhisperModel=FailingWhisperModel)
    monkeypatch.setitem(sys.modules, "faster_whisper", module)

    with pytest.raises(PipelineError) as exc_info:
        build_transcript(
            metadata={"video_file": str(video), "audio_file": str(audio)},
            language="en",
            use_whisper=True,
            paths=paths,
        )

    error = exc_info.value.to_dict()
    assert error["code"] == "whisper_failed"
    assert error["details"]["language"] == "en"
    assert error["details"]["audio_file"] == str(audio)
    assert error["details"]["error_type"] == "ValueError"
    assert "audio decode failed" in error["details"]["error"]


def test_partial_asset_package_does_not_fabricate_missing_downstream_files(tmp_path: Path):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    write_json(paths.source_dir / "metadata.json", {"title": "source title"})
    write_json(
        paths.transcript_dir / "transcript.json",
        {"source": "subtitle", "segment_count": 1, "segments": []},
    )
    frame_path = paths.frames_dir / "frame_0001.jpg"
    frame_path.write_bytes(b"not a real image for this unit test")
    stale_frame = paths.frames_dir / "frame_0002.jpg"
    stale_frame.write_bytes(b"stale frame")
    nonstandard_frame = paths.frames_dir / "frame_bad.jpg"
    nonstandard_frame.write_bytes(b"non-standard frame")
    write_json(
        paths.analysis_dir / "keyframes.json",
        {
            "frame_count": 1,
            "keyframes": [
                {
                    "time": 1.0,
                    "path": str(frame_path),
                    "score": 0.9,
                    "reason": "registered",
                }
            ],
        },
    )

    package = write_partial_asset_package(
        paths,
        error={"code": "llm_unavailable", "step": "planning_content"},
        warnings=["ocr unavailable"],
    )

    assert package["status"] == "partial_failed"
    assert package["metadata"]["title"] == "source title"
    assert package["content_assets"] is None
    assert package["xiaohongshu_post"] is None
    assert package["image_prompts"] == []
    assert package["materials"]["frame_paths"] == [str(frame_path)]
    assert str(stale_frame) not in package["materials"]["frame_paths"]
    assert str(nonstandard_frame) not in package["materials"]["frame_paths"]

    saved = read_json(paths.analysis_dir / "asset-package.json")
    assert saved["error"]["code"] == "llm_unavailable"
    assert "content_assets" not in saved["available_files"]
