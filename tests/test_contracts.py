import pytest

from app.services.contracts import validate_content_assets, validate_image_prompts, validate_xhs_post
from app.services.errors import PipelineError


def test_content_assets_contract_rejects_missing_fields():
    with pytest.raises(PipelineError) as exc_info:
        validate_content_assets({"one_sentence_summary": "一句话"})

    error = exc_info.value.to_dict()
    assert error["code"] == "llm_contract_invalid"
    assert error["step"] == "planning_content"
    assert "core_points" in error["details"]["missing_fields"]


def test_xhs_post_contract_requires_five_titles():
    payload = {
        "content_type": "教程",
        "target_audience": ["新手"],
        "titles": ["标题1"],
        "cover_text": "封面",
        "hook": "开头",
        "body": "正文",
        "image_plan": [{"page": 1}],
        "hashtags": ["#学习"],
        "publish_suggestion": "晚上发布",
    }

    with pytest.raises(PipelineError) as exc_info:
        validate_xhs_post(payload)

    error = exc_info.value.to_dict()
    assert error["code"] == "llm_contract_invalid"
    assert error["details"]["field"] == "titles"


def test_content_assets_contract_requires_core_point_evidence():
    payload = {
        "one_sentence_summary": "一句话",
        "core_points": [{"point": "观点", "evidence": []}],
        "golden_quotes": [{"quote": "金句", "rewrite_note": "改写"}],
        "chapters": [{"title": "章节", "summary": "总结"}],
        "steps": [{"step": "步骤"}],
        "audience": ["读者"],
        "pain_points": ["痛点"],
        "xiaohongshu_angles": ["角度"],
        "recommended_content_type": "干货",
        "source_evidence": [{"claim": "观点", "source_type": "transcript", "time": 1.0, "source_text": "字幕"}],
    }

    with pytest.raises(PipelineError) as exc_info:
        validate_content_assets(payload)

    error = exc_info.value.to_dict()
    assert error["code"] == "llm_contract_invalid"
    assert error["details"]["field"] == "core_points"


def test_content_assets_contract_requires_time_or_path_for_evidence():
    payload = {
        "one_sentence_summary": "一句话",
        "core_points": [{"point": "观点", "evidence": [{"type": "transcript", "text": "字幕"}]}],
        "golden_quotes": [{"quote": "金句", "rewrite_note": "改写"}],
        "chapters": [{"title": "章节", "summary": "总结"}],
        "steps": [{"step": "步骤"}],
        "audience": ["读者"],
        "pain_points": ["痛点"],
        "xiaohongshu_angles": ["角度"],
        "recommended_content_type": "干货",
        "source_evidence": [{"claim": "观点", "source_type": "transcript", "source_text": "字幕"}],
    }

    with pytest.raises(PipelineError) as exc_info:
        validate_content_assets(payload)

    error = exc_info.value.to_dict()
    assert error["code"] == "llm_contract_invalid"
    assert error["details"]["field"] == "core_points.evidence"


def test_content_assets_contract_requires_non_empty_steps():
    payload = {
        "one_sentence_summary": "一句话",
        "core_points": [{"point": "观点", "evidence": [{"type": "transcript", "time": 1.0, "text": "字幕"}]}],
        "golden_quotes": [{"quote": "金句", "rewrite_note": "改写"}],
        "chapters": [{"title": "章节", "summary": "总结"}],
        "steps": [],
        "audience": ["读者"],
        "pain_points": ["痛点"],
        "xiaohongshu_angles": ["角度"],
        "recommended_content_type": "干货",
        "source_evidence": [{"claim": "观点", "source_type": "transcript", "time": 1.0, "source_text": "字幕"}],
    }

    with pytest.raises(PipelineError) as exc_info:
        validate_content_assets(payload)

    error = exc_info.value.to_dict()
    assert error["code"] == "llm_contract_invalid"
    assert "steps" in error["details"]["missing_fields"]


def test_xhs_post_contract_requires_image_plan_source_reference():
    payload = {
        "content_type": "教程",
        "target_audience": ["新手"],
        "titles": ["标题1", "标题2", "标题3", "标题4", "标题5"],
        "cover_text": "封面",
        "hook": "开头",
        "body": "正文",
        "image_plan": [{"page": 1, "role": "cover", "caption": "封面", "content_point": "观点"}],
        "hashtags": ["#学习"],
        "publish_suggestion": "晚上发布",
    }

    with pytest.raises(PipelineError) as exc_info:
        validate_xhs_post(payload)

    error = exc_info.value.to_dict()
    assert error["code"] == "llm_contract_invalid"
    assert error["details"]["field"] == "image_plan"


def test_xhs_post_contract_allows_missing_frame_reference_for_transcript_only_route():
    payload = {
        "content_type": "教程",
        "target_audience": ["新手"],
        "titles": ["标题1", "标题2", "标题3", "标题4", "标题5"],
        "cover_text": "封面",
        "hook": "开头",
        "body": "正文",
        "image_plan": [{"page": 1, "role": "cover", "caption": "封面", "content_point": "观点"}],
        "hashtags": ["#学习"],
        "publish_suggestion": "晚上发布",
    }

    assert validate_xhs_post(payload, require_frame_anchors=False) is payload


def test_image_prompt_contract_accepts_complete_payload():
    payload = {
        "image_prompts": [
            {
                "page": 1,
                "role": "cover",
                "caption": "封面",
                "source_frame_time": 1.2,
                "visual_reference": "人物在画面中央",
                "image_prompt": "原创小红书封面，构图为人物居中，主体清晰，背景干净，色调明亮，右侧留白放标题。",
                "negative_prompt": "不要直接复刻截图，不要低清",
            }
        ]
    }

    assert validate_image_prompts(payload) is payload


def test_image_prompt_contract_allows_null_source_frame_time_when_key_exists():
    payload = {
        "image_prompts": [
            {
                "page": 1,
                "role": "summary",
                "caption": "总结图",
                "source_frame_time": None,
                "visual_reference": "无具体参考帧，基于内容要点生成",
                "image_prompt": "原创小红书总结页，构图为上标题下要点，主体是信息卡片，背景简洁，色调柔和，留白充足。",
                "negative_prompt": "不要直接复刻截图。",
            }
        ]
    }

    assert validate_image_prompts(payload) is payload


def test_image_prompt_contract_rejects_vague_prompt_without_visual_requirements():
    payload = {
        "image_prompts": [
            {
                "page": 1,
                "role": "cover",
                "caption": "封面",
                "source_frame_time": 1.2,
                "visual_reference": "人物在画面中央",
                "image_prompt": "好看的小红书封面。",
                "negative_prompt": "不要低清。",
            }
        ]
    }

    with pytest.raises(PipelineError) as exc_info:
        validate_image_prompts(payload)

    error = exc_info.value.to_dict()
    assert error["code"] == "llm_contract_invalid"
    assert "missing_keywords" in error["details"]


def test_image_prompt_contract_rejects_prompt_that_requests_screenshot_copy():
    payload = {
        "image_prompts": [
            {
                "page": 1,
                "role": "cover",
                "caption": "封面",
                "source_frame_time": 1.2,
                "visual_reference": "人物在画面中央",
                "image_prompt": "原创小红书封面，构图为人物居中，主体清晰，背景干净，色调明亮，右侧留白放标题，但要直接复刻截图。",
                "negative_prompt": "不要直接复刻截图。",
            }
        ]
    }

    with pytest.raises(PipelineError) as exc_info:
        validate_image_prompts(payload)

    error = exc_info.value.to_dict()
    assert error["code"] == "llm_contract_invalid"
    assert "forbidden_terms" in error["details"]
