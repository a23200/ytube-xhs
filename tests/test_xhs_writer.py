from pathlib import Path

import pytest

from app.services import xhs_writer
from app.services.errors import PipelineError
from app.services.runtime_store import ProjectPaths, read_json


def _valid_post(body: str) -> dict:
    return {
        "content_type": "干货",
        "target_audience": ["新手"],
        "titles": ["标题1", "标题2", "标题3", "标题4", "标题5"],
        "cover_text": "封面",
        "hook": "开头",
        "body": body,
        "image_plan": [
            {
                "page": 1,
                "role": "cover",
                "caption": "封面",
                "source_frame_time": 1.0,
                "content_point": "观点",
            }
        ],
        "hashtags": ["#干货"],
        "publish_suggestion": "晚上发布",
    }


def _post_with(**overrides) -> dict:
    payload = _valid_post("这条内容提炼成一个可执行提醒：先保留证据，再重写成自己的表达。")
    payload.update(overrides)
    return payload


def _content_assets() -> dict:
    source_text = "这是一段来自原始字幕的很长连续文本，用来验证系统不会把原文直接搬到小红书正文里。"
    return {
        "one_sentence_summary": "围绕原始信息形成一个可执行的新判断。",
        "source_evidence": [
            {
                "claim": "观点",
                "source_type": "transcript",
                "time": 1.0,
                "source_text": source_text,
            }
        ],
        "core_points": [
            {
                "point": "观点",
                "why_it_matters": "这个观点能帮助读者形成自己的行动判断。",
                "evidence": [{"type": "transcript", "time": 1.0, "text": source_text}],
            }
        ],
        "audience": ["新手创作者"],
        "pain_points": ["担心直接照搬原始信息"],
        "recommended_content_type": "原创图文",
    }


def _keyframes(paths: ProjectPaths) -> dict:
    frame_path = paths.frames_dir / "frame_0001.jpg"
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path.write_bytes(b"jpg")
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


def _keyframes_without_time(paths: ProjectPaths) -> tuple[dict, Path]:
    frame_path = paths.frames_dir / "frame_0001.jpg"
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path.write_bytes(b"jpg")
    return {"keyframes": [{"path": str(frame_path), "score": 0.9, "reason": "test"}]}, frame_path


def _transcript_only_keyframes() -> dict:
    return {
        "frame_count": 0,
        "keyframes": [],
        "skipped": True,
        "skip_reason": "No video file is available; continuing with transcript-only analysis.",
    }


def test_write_xhs_post_rejects_long_verbatim_source_copy(tmp_path: Path, monkeypatch):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    body = "这是一段来自原始字幕的很长连续文本，用来验证系统不会把原文直接搬到小红书正文里。"
    monkeypatch.setattr(xhs_writer.llm_client, "json_chat", lambda *args, **kwargs: _valid_post(body))

    with pytest.raises(PipelineError) as exc_info:
        xhs_writer.write_xhs_post({}, _content_assets(), _keyframes(paths), {"frames": []}, "干货", paths)

    error = exc_info.value.to_dict()
    assert error["code"] == "verbatim_source_copy_detected"
    assert error["details"]["field"] == "body"
    assert not (paths.analysis_dir / "xiaohongshu-post.json").exists()


def test_write_xhs_post_rejects_verbatim_title_copy(tmp_path: Path, monkeypatch):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    source_copy = "这是一段来自原始字幕的很长连续文本，用来验证系统不会把原文直接搬到小红书正文里。"
    monkeypatch.setattr(
        xhs_writer.llm_client,
        "json_chat",
        lambda *args, **kwargs: _post_with(titles=[source_copy, "标题2", "标题3", "标题4", "标题5"]),
    )

    with pytest.raises(PipelineError) as exc_info:
        xhs_writer.write_xhs_post({}, _content_assets(), _keyframes(paths), {"frames": []}, "干货", paths)

    error = exc_info.value.to_dict()
    assert error["code"] == "verbatim_source_copy_detected"
    assert error["details"]["field"] == "titles[0]"
    assert not (paths.analysis_dir / "xiaohongshu-post.json").exists()


def test_write_xhs_post_rejects_verbatim_image_plan_copy(tmp_path: Path, monkeypatch):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    source_copy = "这是一段来自原始字幕的很长连续文本，用来验证系统不会把原文直接搬到小红书正文里。"
    post = _post_with()
    post["image_plan"][0]["caption"] = source_copy
    monkeypatch.setattr(xhs_writer.llm_client, "json_chat", lambda *args, **kwargs: post)

    with pytest.raises(PipelineError) as exc_info:
        xhs_writer.write_xhs_post({}, _content_assets(), _keyframes(paths), {"frames": []}, "干货", paths)

    error = exc_info.value.to_dict()
    assert error["code"] == "verbatim_source_copy_detected"
    assert error["details"]["field"] == "image_plan[0].caption"
    assert not (paths.analysis_dir / "xiaohongshu-post.json").exists()


def test_write_xhs_post_allows_rewritten_copy(tmp_path: Path, monkeypatch):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    monkeypatch.setattr(
        xhs_writer.llm_client,
        "json_chat",
        lambda *args, **kwargs: _valid_post("这条内容提炼成一个可执行提醒：先保留证据，再重写成自己的表达。"),
    )

    payload = xhs_writer.write_xhs_post({}, _content_assets(), _keyframes(paths), {"frames": []}, "干货", paths)

    assert payload["source_disclaimer"]
    saved = read_json(paths.analysis_dir / "xiaohongshu-post.json")
    assert saved["body"].startswith("这条内容提炼")


def test_write_xhs_post_repairs_invalid_image_plan_time_to_keyframe_path(tmp_path: Path, monkeypatch):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    keyframes, frame_path = _keyframes_without_time(paths)
    post = _post_with()
    post["image_plan"][0]["source_frame_time"] = 139.84
    post["image_plan"][0]["source_frame_path"] = None
    monkeypatch.setattr(xhs_writer.llm_client, "json_chat", lambda *args, **kwargs: post)

    payload = xhs_writer.write_xhs_post({}, _content_assets(), keyframes, {"frames": []}, "干货", paths)

    image_plan = payload["image_plan"][0]
    assert image_plan["source_frame_time"] is None
    assert image_plan["source_frame_path"] == str(frame_path)
    saved = read_json(paths.analysis_dir / "xiaohongshu-post.json")
    assert saved["image_plan"][0]["source_frame_path"] == str(frame_path)


def _visible_platform_text(payload: dict) -> str:
    chunks = [
        payload.get("cover_text", ""),
        payload.get("hook", ""),
        payload.get("body", ""),
        payload.get("publish_suggestion", ""),
        payload.get("source_disclaimer", ""),
        *payload.get("titles", []),
        *payload.get("hashtags", []),
    ]
    for item in payload.get("image_plan", []):
        chunks.append(str(item.get("caption", "")))
        chunks.append(str(item.get("content_point", "")))
    return "\n".join(str(chunk) for chunk in chunks if chunk)


def test_write_xhs_post_rejects_empty_llm_payload_without_template_fallback(tmp_path: Path, monkeypatch):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    monkeypatch.setattr(xhs_writer.llm_client, "json_chat", lambda *args, **kwargs: {})

    with pytest.raises(PipelineError) as exc_info:
        xhs_writer.write_xhs_post({}, _content_assets(), _keyframes(paths), {"frames": []}, "干货", paths)

    assert exc_info.value.to_dict()["code"] == "llm_contract_invalid"
    assert not (paths.analysis_dir / "xiaohongshu-post.json").exists()


def test_write_toutiao_post_rejects_empty_llm_payload_without_template_fallback(tmp_path: Path, monkeypatch):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    monkeypatch.setattr(xhs_writer.llm_client, "json_chat", lambda *args, **kwargs: {})

    with pytest.raises(PipelineError) as exc_info:
        xhs_writer.write_toutiao_post({}, _content_assets(), _keyframes(paths), {"frames": []}, "资讯图文", paths)

    assert exc_info.value.to_dict()["code"] == "llm_contract_invalid"
    assert not (paths.analysis_dir / "toutiao-post.json").exists()


def test_write_xhs_post_allows_transcript_only_image_plan_without_frame_anchor(tmp_path: Path, monkeypatch):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    post = _post_with()
    post["image_plan"][0]["source_frame_time"] = None
    post["image_plan"][0]["source_frame_path"] = None
    monkeypatch.setattr(xhs_writer.llm_client, "json_chat", lambda *args, **kwargs: post)

    payload = xhs_writer.write_xhs_post({}, _content_assets(), _transcript_only_keyframes(), {"frames": [], "skipped": True}, "干货", paths)

    assert payload["image_plan"][0]["source_frame_time"] is None
    assert payload["image_plan"][0]["source_frame_path"] is None
    visible_text = _visible_platform_text(payload)
    for forbidden in ["视频拆解", "字幕摘要", "逐帧", "逐段", "本视频", "这条视频", "第几秒"]:
        assert forbidden not in visible_text
    assert read_json(paths.analysis_dir / "xiaohongshu-post.json")["image_plan"][0]["source_frame_path"] is None
