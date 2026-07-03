from pathlib import Path

import pytest

from app.services import content_planner
from app.services.errors import PipelineError
from app.services.runtime_store import ProjectPaths, read_json

SOURCE_TEXT = "这是一段来自原始字幕的很长连续文本，用来验证内容资产不会把原文直接搬到可发布字段里。"


def _paths(tmp_path: Path) -> ProjectPaths:
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    return paths


def _transcript() -> dict:
    return {
        "source": "subtitle",
        "segment_count": 1,
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "text": SOURCE_TEXT,
                "source": "subtitle:subtitles.vtt",
                "importance": 1.0,
            }
        ],
    }


def _content_assets(**overrides) -> dict:
    payload = {
        "one_sentence_summary": "这个主题提醒创作者：创作底稿要先保留证据，再发展成自己的表达。",
        "core_points": [
            {
                "point": "先保存可追溯证据，再发展原创观点",
                "why_it_matters": "这样能降低误读和照搬风险，也方便回看来源。",
                "evidence": [{"type": "transcript", "time": 0.5, "text": SOURCE_TEXT}],
            }
        ],
        "golden_quotes": [{"quote": "证据留下来，表达换成自己的。", "time": 0.5, "rewrite_note": "已改写"}],
        "chapters": [{"title": "证据与改写", "start": 0.0, "end": 2.0, "summary": "围绕素材留痕和二次表达展开。"}],
        "steps": [{"step": "先定位原始片段，再提炼成新表达。", "evidence_time": 0.5}],
        "audience": ["做原创图文的新手创作者"],
        "pain_points": ["担心创作时变成逐字搬运"],
        "xiaohongshu_angles": ["素材留痕工作流"],
        "recommended_content_type": "干货清单",
        "source_evidence": [
            {
                "claim": "视频强调保留证据并改写表达",
                "source_type": "transcript",
                "time": 0.5,
                "source_text": SOURCE_TEXT,
            }
        ],
    }
    payload.update(overrides)
    return payload


def _build(tmp_path: Path, llm_payload: dict) -> dict:
    paths = _paths(tmp_path)
    content_planner.llm_client.json_chat = lambda *args, **kwargs: llm_payload
    return content_planner.build_content_assets(
        metadata={
            "video_id": "v1",
            "url": "https://example.com/video",
            "title": "标题",
            "author": "作者",
            "duration": 2,
        },
        transcript_payload=_transcript(),
        keyframes_payload={"keyframes": []},
        visual_payload={"frames": []},
        language="zh",
        style="干货",
        paths=paths,
    )


def test_build_content_assets_rejects_verbatim_summary(tmp_path: Path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setattr(content_planner.llm_client, "json_chat", lambda *args, **kwargs: _content_assets(one_sentence_summary=SOURCE_TEXT))

    with pytest.raises(PipelineError) as exc_info:
        content_planner.build_content_assets(
            {},
            _transcript(),
            {"keyframes": []},
            {"frames": []},
            "zh",
            "干货",
            paths,
        )

    error = exc_info.value.to_dict()
    assert error["code"] == "verbatim_source_copy_detected"
    assert error["step"] == "planning_content"
    assert error["details"]["field"] == "one_sentence_summary"
    assert not (paths.analysis_dir / "content-assets.json").exists()


def test_build_content_assets_rejects_verbatim_source_claim(tmp_path: Path, monkeypatch):
    paths = _paths(tmp_path)
    payload = _content_assets()
    payload["source_evidence"][0]["claim"] = SOURCE_TEXT
    monkeypatch.setattr(content_planner.llm_client, "json_chat", lambda *args, **kwargs: payload)

    with pytest.raises(PipelineError) as exc_info:
        content_planner.build_content_assets({}, _transcript(), {"keyframes": []}, {"frames": []}, "zh", "干货", paths)

    error = exc_info.value.to_dict()
    assert error["code"] == "verbatim_source_copy_detected"
    assert error["details"]["field"] == "source_evidence[0].claim"
    assert not (paths.analysis_dir / "content-assets.json").exists()


def test_build_content_assets_allows_source_text_in_evidence_fields(tmp_path: Path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setattr(content_planner.llm_client, "json_chat", lambda *args, **kwargs: _content_assets())

    payload = content_planner.build_content_assets(
        {},
        _transcript(),
        {"keyframes": []},
        {"frames": []},
        "zh",
        "干货",
        paths,
    )

    assert payload["core_points"][0]["evidence"][0]["text"] == SOURCE_TEXT
    assert payload["source_evidence"][0]["source_text"] == SOURCE_TEXT
    saved = read_json(paths.analysis_dir / "content-assets.json")
    assert saved["one_sentence_summary"].startswith("这个主题提醒")


def test_build_content_assets_rejects_incomplete_llm_payload_without_template_fallback(tmp_path: Path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setattr(content_planner.llm_client, "json_chat", lambda *args, **kwargs: {"one_sentence_summary": "只有一句话"})

    with pytest.raises(PipelineError) as exc_info:
        content_planner.build_content_assets(
            {},
            _transcript(),
            {"keyframes": []},
            {"frames": []},
            "zh",
            "干货",
            paths,
        )

    error = exc_info.value.to_dict()
    assert error["code"] == "llm_contract_invalid"
    assert error["details"]["artifact"] == "content-assets.json"
    assert not (paths.analysis_dir / "content-assets.json").exists()


def test_build_content_assets_rejects_non_object_llm_payload(tmp_path: Path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setattr(content_planner.llm_client, "json_chat", lambda *args, **kwargs: [])

    with pytest.raises(PipelineError) as exc_info:
        content_planner.build_content_assets(
            {},
            _transcript(),
            {"keyframes": []},
            {"frames": []},
            "zh",
            "干货",
            paths,
        )

    error = exc_info.value.to_dict()
    assert error["code"] == "llm_contract_invalid"
    assert error["details"]["payload_type"] == "list"


def test_build_basic_content_assets_writes_source_grounded_fallback(tmp_path: Path):
    paths = _paths(tmp_path)

    payload = content_planner.build_basic_content_assets(
        metadata={
            "video_id": "v1",
            "url": "https://example.com/video",
            "title": "测试视频标题",
            "author": "作者",
            "duration": 2,
        },
        transcript_payload=_transcript(),
        keyframes_payload={"keyframes": []},
        visual_payload={"frames": []},
        language="zh",
        style="干货",
        paths=paths,
        fallback_reason="LLM timeout",
    )

    assert payload["analysis_mode"] == "local_basic_fallback"
    assert payload["fallback_reason"] == "LLM timeout"
    assert payload["core_points"][0]["evidence"][0]["type"] == "transcript"
    assert payload["source_evidence"][0]["source_text"] == SOURCE_TEXT
    saved = read_json(paths.analysis_dir / "content-assets.json")
    assert saved["analysis_mode"] == "local_basic_fallback"
