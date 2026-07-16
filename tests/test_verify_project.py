from pathlib import Path

import pytest

from app.services.docx_writer import write_article_docx
from app.services.platforms import get_platform
from app.services.runtime_store import read_json, write_json
from scripts.verify_project import verify_project


def _write_valid_upstream_project(project: Path, status: str = "failed") -> None:
    video_path = project / "source/source.mp4"
    thumbnail_path = project / "source/thumbnail.jpg"
    frame_path = project / "frames/frame_0001.jpg"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"mp4")
    thumbnail_path.write_bytes(b"\xff\xd8\xff\xd9")
    frame_path.write_bytes(b"jpg")
    write_json(
        project / "project.json",
        {
            "status": status,
            "error": {"code": "llm_unavailable"} if status == "failed" else None,
            "warnings": ["warn"],
            "outputs": {
                "metadata": "source/metadata.json",
                "transcript": "transcript/transcript.json",
                "keyframes": "analysis/keyframes.json",
                "visual_analysis": "analysis/visual-analysis.json",
                "run_metadata": "analysis/run-metadata.json",
            },
        },
    )
    write_json(
        project / "source/metadata.json",
        {
            "video_id": "v1",
            "url": "https://example.com/video",
            "title": "Title",
            "author": "Author",
            "duration": 12,
            "video_file": str(video_path),
            "thumbnail": "https://example.com/thumb.jpg",
            "thumbnail_file": str(thumbnail_path),
            "available_subtitles": ["en"],
            "automatic_captions": ["en-auto"],
            "subtitle_track_summary": {
                "available_subtitles": {
                    "count": 1,
                    "languages": ["en"],
                    "formats_by_language": {"en": ["vtt"]},
                },
                "automatic_captions": {
                    "count": 1,
                    "languages": ["en-auto"],
                    "formats_by_language": {"en-auto": ["vtt"]},
                },
            },
        },
    )
    write_json(
        project / "transcript/transcript.json",
        {
            "source": "subtitle",
            "language": "zh",
            "segment_count": 1,
            "segments": [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "text": "字幕文本",
                    "source": "subtitle:subtitles.vtt",
                    "importance": 0.5,
                }
            ],
        },
    )
    write_json(
        project / "analysis/keyframes.json",
        {
            "frame_count": 1,
            "keyframes": [
                {
                    "time": 1.0,
                    "path": str(frame_path),
                    "score": 0.8,
                    "reason": "sharp frame",
                    "related_transcript_text": "字幕文本",
                }
            ],
        },
    )
    write_json(
        project / "analysis/visual-analysis.json",
        {
            "ocr_provider": "none",
            "warnings": ["OCR disabled"],
            "frames": [
                {
                    "time": 1.0,
                    "path": str(frame_path),
                    "ocr_text": "",
                    "visual_summary": "No OCR.",
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
            ],
        },
    )
    write_json(
        project / "analysis/run-metadata.json",
        {
            "status": status,
            "video_id": "v1",
            "title": "Title",
            "author": "Author",
            "duration": 12,
            "source_url": "https://example.com/video",
            "source_metadata": {
                "video_id": "v1",
                "title": "Title",
                "author": "Author",
                "duration": 12,
                "source_url": "https://example.com/video",
                "video_file": str(video_path),
            },
        },
    )


def _write_valid_downstream_outputs(project: Path) -> None:
    card_path = project / "cards/cover.png"
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    content_assets = {
        "one_sentence_summary": "一句话总结",
        "core_points": [{"point": "观点", "evidence": [{"type": "transcript", "time": 1.0, "text": "字幕文本"}]}],
        "golden_quotes": [{"quote": "改写金句", "time": 1.0, "rewrite_note": "已改写"}],
        "chapters": [{"title": "章节", "start": 0.0, "end": 2.0, "summary": "总结"}],
        "steps": [{"step": "步骤", "evidence_time": 1.0}],
        "audience": ["目标用户"],
        "pain_points": ["痛点"],
        "xiaohongshu_angles": ["角度"],
        "recommended_content_type": "干货",
        "source_evidence": [{"claim": "观点", "source_type": "transcript", "time": 1.0, "source_text": "字幕文本"}],
    }
    xhs_post = {
        "content_type": "干货",
        "target_audience": ["目标用户"],
        "titles": ["标题1", "标题2", "标题3", "标题4", "标题5"],
        "cover_text": "封面文案",
        "hook": "开头",
        "body": "正文",
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
    image_prompts = {
        "image_prompts": [
            {
                "page": 1,
                "role": "cover",
                "caption": "封面",
                "source_frame_time": 1.0,
                "visual_reference": "参考帧",
                "image_prompt": "原创小红书图文视觉，构图为主体居中，主体清晰，背景干净，色调明亮，右侧留白放标题。",
                "negative_prompt": "不要直接复刻截图。",
            }
        ]
    }
    image_cards = {
        "cards": [
            {
                "page": 1,
                "role": "cover",
                "title": "封面",
                "caption": "观点",
                "source_frame_time": 1.0,
                "source_frame_path": str(project / "frames/frame_0001.jpg"),
                "layout": "vertical_4_5_media_text",
                "style": "clean",
                "output_path": str(card_path),
                "image_prompt": "原创小红书图文视觉，构图为主体居中，主体清晰，背景干净，色调明亮，右侧留白放标题。",
            }
        ],
        "card_count": 1,
        "aspect_ratio": "4:5",
        "renderer": "pillow_template_v1",
    }
    write_json(project / "analysis/content-assets.json", content_assets)
    write_json(project / "analysis/xiaohongshu-post.json", xhs_post)
    write_article_docx(
        {"title": "Title", "author": "Author", "url": "https://example.com/video"},
        xhs_post,
        get_platform("xhs"),
        project / "analysis/xhs-article.docx",
    )
    xhs_quality_report = {
        "platform": "xhs",
        "passed": True,
        "similarity": {"estimated_rewrite_degree": 0.95},
        "violations": [],
    }
    write_json(project / "analysis/xhs-quality-report.json", xhs_quality_report)
    write_json(project / "analysis/image-prompts.json", image_prompts)
    write_json(project / "analysis/image-cards.json", image_cards)
    write_json(
        project / "analysis/asset-package.json",
        {
            "metadata": {"title": "Title"},
            "transcript": {"segment_count": 1},
            "keyframes": {"frame_count": 1},
            "visual_analysis": {"frames": [{}]},
            "content_assets": content_assets,
            "xiaohongshu_post": xhs_post,
            "xhs_quality_report": xhs_quality_report,
            "image_prompts": image_prompts["image_prompts"],
            "image_cards": image_cards["cards"],
            "materials": {
                "frames_dir": str(project / "frames"),
                "frame_paths": [str(project / "frames/frame_0001.jpg")],
                "cards_dir": str(project / "cards"),
                "card_paths": [str(card_path)],
            },
            "compliance": {"rights_boundary": "authorized only"},
        },
    )
    (project / "analysis/xhs-post.md").write_text(
        "\n".join(
            [
                "## 视频信息",
                "- 标题：Title",
                "- 作者：Author",
                "- URL：https://example.com/video",
                "## 一句话总结",
                "一句话总结",
                "## 小红书标题",
                "- 标题1",
                "- 标题2",
                "- 标题3",
                "- 标题4",
                "- 标题5",
                "## 封面文案",
                "封面文案",
                "## 开头",
                "开头",
                "## 正文",
                "正文",
                "## 配图规划",
                "- 第 1 页｜cover｜封面｜来源：1s｜内容点：观点",
                "## 图片提示词",
                "- 第 1 页｜封面｜参考：参考帧｜来源：1s｜提示词：原创小红书图文视觉，构图为主体居中，主体清晰，背景干净，色调明亮，右侧留白放标题。｜负向：不要直接复刻截图。",
                "## 图文卡片",
                f"- 第 1 页｜cover｜封面｜观点｜来源：1s｜文件：{card_path}",
                "## 标签",
                "- #干货",
                "## 素材路径",
                f"- metadata：{project / 'source/metadata.json'}",
                f"- transcript：{project / 'transcript/transcript.json'}",
                f"- keyframes：{project / 'analysis/keyframes.json'}",
                f"- visual analysis：{project / 'analysis/visual-analysis.json'}",
                f"- frames：{project / 'frames'}",
                f"- cards：{project / 'cards'}",
                "## 来源时间点",
                "- 观点｜transcript｜1s｜字幕文本",
            ]
        ),
        encoding="utf-8",
    )
    project_record = read_json(project / "project.json")
    project_record["outputs"].update(
        {
            "content_assets": "analysis/content-assets.json",
            "xhs_post_json": "analysis/xiaohongshu-post.json",
            "xhs_post_md": "analysis/xhs-post.md",
            "xhs_post_docx": "analysis/xhs-article.docx",
            "xhs_quality_report": "analysis/xhs-quality-report.json",
            "image_prompts": "analysis/image-prompts.json",
            "image_cards": "analysis/image-cards.json",
            "asset_package": "analysis/asset-package.json",
        }
    )
    write_json(project / "project.json", project_record)


def _write_valid_text_article_project(project: Path, platform: str) -> None:
    adapter = get_platform(platform)
    status = f"{platform}_completed"
    video_path = project / "source/source.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"mp4")
    project_record = {
        "status": status,
        "text_only": True,
        "outputs": {},
        "error": None,
        "warnings": [],
    }
    write_json(project / "project.json", project_record)
    write_json(
        project / "source/metadata.json",
        {
            "video_id": "text-v1",
            "url": "https://example.com/video",
            "title": "Text source",
            "author": None,
            "duration": None,
            "video_file": str(video_path),
            "available_subtitles": [],
            "automatic_captions": [],
            "subtitle_track_summary": {
                "available_subtitles": {"count": 0, "languages": [], "formats_by_language": {}},
                "automatic_captions": {"count": 0, "languages": [], "formats_by_language": {}},
            },
        },
    )
    write_json(
        project / "transcript/transcript.json",
        {
            "source": "faster-whisper",
            "language": "zh",
            "segment_count": 1,
            "segments": [{"start": 0.0, "end": 2.0, "text": "原始证据", "source": "faster-whisper", "importance": 0.5}],
        },
    )
    write_json(
        project / "analysis/keyframes.json",
        {
            "video_file": str(video_path),
            "requested_max_frames": 8,
            "transcript_segment_count": 1,
            "frame_count": 0,
            "keyframes": [],
            "skipped": True,
            "skip_reason": "Text-only mode",
            "analysis_mode": "text_only",
        },
    )
    write_json(
        project / "analysis/visual-analysis.json",
        {
            "ocr_provider": "none",
            "ocr_enabled": False,
            "frames": [],
            "skipped": True,
            "skip_reason": "Text-only mode",
            "analysis_mode": "text_only",
        },
    )
    write_json(
        project / "analysis/run-metadata.json",
        {"status": status, "video_id": "text-v1", "title": "Text source", "author": None, "duration": None, "source_url": "https://example.com/video"},
    )
    content_assets = {
        "analysis_mode": "text_only",
        "one_sentence_summary": "先核对证据，再用自己的话说明问题。",
        "core_points": [{"point": "先核对证据", "why_it_matters": "避免误解", "evidence": [{"type": "transcript", "time": 1.0, "text": "原始证据"}]}],
        "golden_quotes": [{"quote": "讲清楚比堆术语更重要", "rewrite_note": "已改写"}],
        "chapters": [{"title": "表达方法", "summary": "把复杂内容讲清楚"}],
        "steps": [{"step": "核对来源", "evidence_time": 1.0}],
        "audience": ["普通读者"],
        "pain_points": ["内容难懂"],
        "xiaohongshu_angles": ["讲人话"],
        "recommended_content_type": "原创文章",
        "source_evidence": [{"claim": "证据需要核对", "source_type": "transcript", "time": 1.0, "source_text": "原始证据"}],
    }
    write_json(project / "analysis/content-assets.json", content_assets)
    post = {
        "content_type": "原创文章",
        "target_audience": ["普通读者"],
        "titles": ["标题一", "标题二", "标题三", "标题四", "标题五"],
        "cover_text": "先别急着下结论",
        "hook": "看似只是省一步，结果最容易出错的，反而是这一步。",
        "body": "很多人先给答案，再回头找证据。更稳妥的做法，是先核对来源，再把复杂内容讲成普通人听得懂的话。",
        "image_plan": [{"page": 1, "role": "cover", "caption": "先核对证据", "source_frame_time": None, "source_frame_path": None, "content_point": "核对来源"}],
        "hashtags": ["#内容创作"],
        "publish_suggestion": "发布前复核事实。",
        "platform": platform,
        "platform_name": adapter.name,
    }
    write_json(project / f"analysis/{adapter.post_filename}", post)
    quality = {
        "platform": platform,
        "passed": True,
        "similarity": {"estimated_rewrite_degree": 0.92, "estimated_similarity": 0.08, "longest_common_fragment_chars": 0},
        "violations": [],
    }
    write_json(project / f"analysis/{adapter.quality_filename}", quality)
    write_article_docx({"title": "Text source", "url": "https://example.com/video"}, post, adapter, project / f"analysis/{adapter.docx_filename}")
    heading = {"xhs": "小红书标题", "toutiao": "今日头条标题", "douyin": "抖音标题", "bilibili": "哔哩哔哩标题"}[platform]
    markdown = f"""# {adapter.name}文章稿

## 视频信息
标题：Text source

## 一句话总结
先核对证据，再用自己的话说明问题。

## {heading}
- 标题一
- 标题二
- 标题三
- 标题四
- 标题五

## 封面文案
先别急着下结论

## 开头
看似只是省一步，结果最容易出错的，反而是这一步。

## 正文
很多人先给答案，再回头找证据。更稳妥的做法，是先核对来源，再把复杂内容讲成普通人听得懂的话。

## 图片/截图
- 纯文案模式已启用。

## 标签
- #内容创作

## 素材路径
- source/metadata.json
- transcript/transcript.json
- analysis/keyframes.json
- analysis/visual-analysis.json
- frames：纯文案模式已跳过

## 来源时间点
- 证据需要核对｜transcript｜1s｜原始证据
"""
    (project / f"analysis/{adapter.markdown_filename}").write_text(markdown, encoding="utf-8")
    package = {
        "metadata": {"title": "Text source"},
        "transcript": {"segment_count": 1},
        "keyframes": {"frame_count": 0},
        "visual_analysis": {"frames": []},
        "content_assets": content_assets,
        "materials": {
            "frame_paths": [],
            "card_paths": [],
            "toutiao_card_paths": [],
            "frames_dir": str(project / "frames"),
            "cards_dir": str(project / "cards"),
            "toutiao_cards_dir": str(project / "toutiao-cards"),
        },
        "compliance": {"rights_boundary": "authorized"},
        "warnings": [],
        "quality": quality,
        "platform": platform,
        f"{platform}_quality_report": quality,
    }
    package["xiaohongshu_post" if platform == "xhs" else f"{platform}_post"] = post
    write_json(project / "analysis/asset-package.json", package)
    outputs = {
        "metadata": "source/metadata.json",
        "transcript": "transcript/transcript.json",
        "keyframes": "analysis/keyframes.json",
        "visual_analysis": "analysis/visual-analysis.json",
        "run_metadata": "analysis/run-metadata.json",
        "content_assets": "analysis/content-assets.json",
        adapter.post_json_kind: f"analysis/{adapter.post_filename}",
        adapter.post_md_kind: f"analysis/{adapter.markdown_filename}",
        adapter.post_docx_kind: f"analysis/{adapter.docx_filename}",
        adapter.quality_kind: f"analysis/{adapter.quality_filename}",
        "asset_package": "analysis/asset-package.json",
    }
    project_record["outputs"] = outputs
    write_json(project / "project.json", project_record)


def test_verify_partial_failed_project(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="failed")
    write_json(
        project / "analysis/asset-package.json",
        {
            "status": "partial_failed",
            "error": {"code": "llm_unavailable"},
            "metadata": {"title": "Title"},
            "transcript": {"segment_count": 1},
            "keyframes": {"frame_count": 1},
            "visual_analysis": {"frames": [{}]},
        },
    )
    project_record = read_json(project / "project.json")
    project_record["outputs"]["asset_package"] = "analysis/asset-package.json"
    write_json(project / "project.json", project_record)

    result = verify_project(project)

    assert result["ok"] is True
    assert result["completed_ok"] is False
    assert result["partial_ok"] is True
    assert result["issues"] == []
    assert result["summary"]["transcript_segments"] == 1
    assert result["summary"]["frame_files"] == 1
    assert result["summary"]["available_subtitle_languages"] == 1
    assert result["summary"]["automatic_caption_languages"] == 1


@pytest.mark.parametrize("platform", ["xhs", "toutiao", "douyin", "bilibili"])
def test_verify_text_only_article_completion_for_each_platform(tmp_path: Path, platform: str):
    project = tmp_path / platform
    _write_valid_text_article_project(project, platform)

    result = verify_project(project)

    assert result["ok"] is True
    assert result["completed_ok"] is True
    assert result["platform"] == platform
    assert result["text_only"] is True
    assert result["missing"] == []
    assert result["issues"] == []
    assert result["summary"]["frame_files"] == 0


def test_verify_text_only_article_rejects_unmarked_empty_visual_artifacts(tmp_path: Path):
    project = tmp_path / "unmarked"
    _write_valid_text_article_project(project, "douyin")
    keyframes = read_json(project / "analysis/keyframes.json")
    keyframes.pop("skipped")
    keyframes.pop("analysis_mode")
    write_json(project / "analysis/keyframes.json", keyframes)

    result = verify_project(project)

    assert result["ok"] is False
    assert any(issue["code"] == "text_only_keyframes_not_marked_skipped" for issue in result["issues"])


def test_verify_text_only_allows_transcript_only_source_without_media_file(tmp_path: Path):
    project = tmp_path / "subtitle-only"
    _write_valid_text_article_project(project, "douyin")
    metadata = read_json(project / "source/metadata.json")
    metadata["video_file"] = None
    metadata["subtitle_file"] = None
    write_json(project / "source/metadata.json", metadata)

    result = verify_project(project)

    assert result["ok"] is True
    assert result["completed_ok"] is True
    assert result["issues"] == []


def test_verify_text_only_rejects_unmarked_visual_analysis(tmp_path: Path):
    project = tmp_path / "unmarked-visual"
    _write_valid_text_article_project(project, "douyin")
    visual = read_json(project / "analysis/visual-analysis.json")
    visual.pop("skipped")
    visual.pop("skip_reason")
    write_json(project / "analysis/visual-analysis.json", visual)

    result = verify_project(project)

    assert result["ok"] is False
    assert any(issue["code"] == "text_only_visual_not_marked_skipped" for issue in result["issues"])


def test_verify_article_rejects_invalid_docx(tmp_path: Path):
    project = tmp_path / "bad-docx"
    _write_valid_text_article_project(project, "bilibili")
    (project / "analysis/bilibili-article.docx").write_bytes(b"not-a-docx")

    result = verify_project(project)

    assert result["ok"] is False
    assert any(issue["code"] == "invalid_docx" for issue in result["issues"])


def test_verify_article_rejects_failed_quality_report(tmp_path: Path):
    project = tmp_path / "bad-quality"
    _write_valid_text_article_project(project, "toutiao")
    quality = read_json(project / "analysis/toutiao-quality-report.json")
    quality["passed"] = False
    quality["violations"] = [{"code": "subheading_detected"}]
    write_json(project / "analysis/toutiao-quality-report.json", quality)
    package = read_json(project / "analysis/asset-package.json")
    package["toutiao_quality_report"] = quality
    write_json(project / "analysis/asset-package.json", package)

    result = verify_project(project)

    assert result["ok"] is False
    issue_codes = {issue["code"] for issue in result["issues"]}
    assert {"quality_report_failed", "quality_report_has_violations"} <= issue_codes


def test_verify_completed_project_requires_downstream_outputs(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="completed")

    result = verify_project(project)

    assert result["ok"] is False
    assert result["completed_ok"] is False
    assert "content_assets" in result["missing"]
    assert "xhs_post_md" in result["missing"]


def test_verify_completed_project_accepts_valid_outputs(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="completed")
    _write_valid_downstream_outputs(project)

    result = verify_project(project)

    assert result["ok"] is True
    assert result["completed_ok"] is True
    assert result["partial_ok"] is False
    assert result["missing"] == []
    assert result["issues"] == []


def test_verify_project_reports_structural_issues(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="failed")
    write_json(project / "transcript/transcript.json", {"segment_count": 2, "segments": []})
    write_json(project / "analysis/asset-package.json", {"status": "partial_failed", "error": {"code": "x"}})

    result = verify_project(project)

    assert result["ok"] is False
    assert any(issue["code"] == "empty_transcript" for issue in result["issues"])


def test_verify_project_reports_output_registration_issues(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="failed")
    write_json(project / "analysis/asset-package.json", {"status": "partial_failed", "error": {"code": "x"}})
    project_record = read_json(project / "project.json")
    project_record["outputs"]["metadata"] = "analysis/keyframes.json"
    project_record["outputs"]["unknown"] = "source/metadata.json"
    write_json(project / "project.json", project_record)

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "output_path_mismatch" in issue_codes
    assert "unknown_output_kind" in issue_codes
    assert "output_not_registered" in issue_codes


def test_verify_project_reports_thumbnail_path_issue(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="failed")
    wrong_thumbnail = project / "source/thumb.png"
    wrong_thumbnail.write_bytes(b"png")
    metadata = read_json(project / "source/metadata.json")
    metadata["thumbnail_file"] = str(wrong_thumbnail)
    write_json(project / "source/metadata.json", metadata)
    write_json(project / "analysis/asset-package.json", {"status": "partial_failed", "error": {"code": "x"}})
    project_record = read_json(project / "project.json")
    project_record["outputs"]["asset_package"] = "analysis/asset-package.json"
    write_json(project / "project.json", project_record)

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "thumbnail_standard_path_mismatch" in issue_codes


def test_verify_project_reports_missing_standard_thumbnail_when_remote_exists(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="failed")
    (project / "source/thumbnail.jpg").unlink()
    metadata = read_json(project / "source/metadata.json")
    metadata.pop("thumbnail_file")
    write_json(project / "source/metadata.json", metadata)
    write_json(project / "analysis/asset-package.json", {"status": "partial_failed", "error": {"code": "x"}})
    project_record = read_json(project / "project.json")
    project_record["outputs"]["asset_package"] = "analysis/asset-package.json"
    write_json(project / "project.json", project_record)

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "thumbnail_file_missing" in issue_codes


def test_verify_project_reports_missing_subtitle_language_lists(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="failed")
    metadata = read_json(project / "source/metadata.json")
    metadata.pop("available_subtitles")
    metadata.pop("automatic_captions")
    write_json(project / "source/metadata.json", metadata)
    write_json(project / "analysis/asset-package.json", {"status": "partial_failed", "error": {"code": "x"}})
    project_record = read_json(project / "project.json")
    project_record["outputs"]["asset_package"] = "analysis/asset-package.json"
    write_json(project / "project.json", project_record)

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "metadata_subtitle_fields_missing" in issue_codes


def test_verify_project_reports_run_metadata_traceability_issue(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="failed")
    write_json(project / "analysis/run-metadata.json", {"status": "failed", "video_id": "wrong"})
    write_json(project / "analysis/asset-package.json", {"status": "partial_failed", "error": {"code": "x"}})
    project_record = read_json(project / "project.json")
    project_record["outputs"]["asset_package"] = "analysis/asset-package.json"
    write_json(project / "project.json", project_record)

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "run_metadata_missing_fields" in issue_codes
    assert "run_metadata_source_mismatch" in issue_codes


def test_verify_project_reports_missing_visual_frame_metrics(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="failed")
    visual = read_json(project / "analysis/visual-analysis.json")
    visual["frames"][0].pop("frame_metrics")
    write_json(project / "analysis/visual-analysis.json", visual)
    write_json(project / "analysis/asset-package.json", {"status": "partial_failed", "error": {"code": "x"}})
    project_record = read_json(project / "project.json")
    project_record["outputs"]["asset_package"] = "analysis/asset-package.json"
    write_json(project / "project.json", project_record)

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "invalid_visual_frame" in issue_codes


def test_verify_completed_project_reports_vague_image_prompt(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="completed")
    _write_valid_downstream_outputs(project)
    image_prompts = read_json(project / "analysis/image-prompts.json")
    image_prompts["image_prompts"][0]["image_prompt"] = "好看的小红书封面。"
    image_prompts["image_prompts"][0]["negative_prompt"] = "不要低清。"
    write_json(project / "analysis/image-prompts.json", image_prompts)

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "image_prompt_missing_visual_requirements" in issue_codes


def test_verify_completed_project_reports_image_prompt_copy_request(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="completed")
    _write_valid_downstream_outputs(project)
    image_prompts = read_json(project / "analysis/image-prompts.json")
    image_prompts["image_prompts"][0][
        "image_prompt"
    ] = "原创小红书图文视觉，构图为主体居中，主体清晰，背景干净，色调明亮，右侧留白放标题，但要直接复刻截图。"
    write_json(project / "analysis/image-prompts.json", image_prompts)

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "image_prompt_requests_screenshot_copy" in issue_codes


def test_verify_completed_project_reports_unanchored_content_evidence(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="completed")
    _write_valid_downstream_outputs(project)
    content_assets = read_json(project / "analysis/content-assets.json")
    content_assets["core_points"][0]["evidence"][0].pop("time")
    content_assets["source_evidence"][0].pop("time")
    write_json(project / "analysis/content-assets.json", content_assets)

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "core_point_evidence_missing_anchor" in issue_codes


def test_verify_completed_project_reports_fabricated_source_time(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="completed")
    _write_valid_downstream_outputs(project)
    content_assets = read_json(project / "analysis/content-assets.json")
    content_assets["core_points"][0]["evidence"][0]["time"] = 999.0
    write_json(project / "analysis/content-assets.json", content_assets)

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "source_anchor_invalid" in issue_codes


def test_verify_completed_project_reports_verbatim_content_assets(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="completed")
    _write_valid_downstream_outputs(project)
    source_text = "这是一段来自原始字幕的很长连续文本，用来验证离线验收不会放过内容资产直接搬运原文。"
    transcript = read_json(project / "transcript/transcript.json")
    transcript["segments"][0]["text"] = source_text
    write_json(project / "transcript/transcript.json", transcript)
    content_assets = read_json(project / "analysis/content-assets.json")
    content_assets["one_sentence_summary"] = source_text
    content_assets["core_points"][0]["evidence"][0]["text"] = source_text
    content_assets["source_evidence"][0]["source_text"] = source_text
    write_json(project / "analysis/content-assets.json", content_assets)

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "verbatim_source_copy_detected" in issue_codes
    assert any(
        issue["artifact"] == "content_assets" and issue["details"]["field"] == "one_sentence_summary"
        for issue in result["issues"]
    )


def test_verify_completed_project_reports_verbatim_xhs_post(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="completed")
    _write_valid_downstream_outputs(project)
    source_text = "这是一段来自原始字幕的很长连续文本，用来验证离线验收不会放过小红书正文直接搬运原文。"
    transcript = read_json(project / "transcript/transcript.json")
    transcript["segments"][0]["text"] = source_text
    write_json(project / "transcript/transcript.json", transcript)
    content_assets = read_json(project / "analysis/content-assets.json")
    content_assets["core_points"][0]["evidence"][0]["text"] = source_text
    content_assets["source_evidence"][0]["source_text"] = source_text
    write_json(project / "analysis/content-assets.json", content_assets)
    xhs_post = read_json(project / "analysis/xiaohongshu-post.json")
    xhs_post["body"] = source_text
    write_json(project / "analysis/xiaohongshu-post.json", xhs_post)

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "verbatim_source_copy_detected" in issue_codes
    assert any(issue["artifact"] == "xhs_post_json" and issue["details"]["field"] == "body" for issue in result["issues"])


def test_verify_completed_project_rejects_keyframe_source_path_that_is_not_keyframe(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="completed")
    _write_valid_downstream_outputs(project)
    content_assets = read_json(project / "analysis/content-assets.json")
    content_assets["source_evidence"][0]["source_path"] = str(project / "source/metadata.json")
    content_assets["source_evidence"][0].pop("time")
    content_assets["source_evidence"][0]["source_type"] = "keyframe"
    write_json(project / "analysis/content-assets.json", content_assets)

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "source_anchor_invalid" in issue_codes


def test_verify_project_rejects_keyframe_path_outside_frames_dir(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="failed")
    wrong_dir_frame = project / "source/frame_0001.jpg"
    wrong_dir_frame.write_bytes(b"\xff\xd8\xff\xd9")
    keyframes = read_json(project / "analysis/keyframes.json")
    keyframes["keyframes"][0]["path"] = str(wrong_dir_frame)
    write_json(project / "analysis/keyframes.json", keyframes)
    write_json(project / "analysis/asset-package.json", {"status": "partial_failed", "error": {"code": "x"}})
    project_record = read_json(project / "project.json")
    project_record["outputs"]["asset_package"] = "analysis/asset-package.json"
    write_json(project / "project.json", project_record)

    result = verify_project(project)

    assert any(issue["code"] == "keyframe_image_missing" for issue in result["issues"])


def test_verify_completed_project_reports_unbound_image_prompt(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="completed")
    _write_valid_downstream_outputs(project)
    image_prompts = read_json(project / "analysis/image-prompts.json")
    image_prompts["image_prompts"][0]["source_frame_time"] = None
    write_json(project / "analysis/image-prompts.json", image_prompts)

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "source_anchor_invalid" in issue_codes


def test_verify_completed_project_reports_empty_markdown_sections(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="completed")
    _write_valid_downstream_outputs(project)
    (project / "analysis/xhs-post.md").write_text(
        "\n".join(
            [
                "## 视频信息",
                "## 一句话总结",
                "## 小红书标题",
                "## 封面文案",
                "## 正文",
                "## 配图规划",
                "## 图片提示词",
                "## 标签",
                "## 素材路径",
                "## 来源时间点",
            ]
        ),
        encoding="utf-8",
    )

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "markdown_empty_section" in issue_codes


def test_verify_completed_project_reports_markdown_missing_material_paths(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="completed")
    _write_valid_downstream_outputs(project)
    markdown_path = project / "analysis/xhs-post.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    markdown_path.write_text(markdown.replace("analysis/visual-analysis.json", "analysis/visual.json"), encoding="utf-8")

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "markdown_missing_material_paths" in issue_codes


def test_verify_completed_project_reports_markdown_missing_prompt_content(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="completed")
    _write_valid_downstream_outputs(project)
    markdown_path = project / "analysis/xhs-post.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    prompt_section = "- 第 1 页｜封面｜参考：参考帧｜来源：1s｜提示词：原创小红书图文视觉，构图为主体居中，主体清晰，背景干净，色调明亮，右侧留白放标题。｜负向：不要直接复刻截图。"
    markdown_path.write_text(markdown.replace(prompt_section, "- 提示词已省略"), encoding="utf-8")

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "markdown_missing_image_prompt_content" in issue_codes


def test_verify_completed_project_reports_verbatim_markdown_body(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="completed")
    _write_valid_downstream_outputs(project)
    source_text = "这是一段来自原始字幕的很长连续文本，用来验证 Markdown 正文不能直接搬运原文。"
    transcript = read_json(project / "transcript/transcript.json")
    transcript["segments"][0]["text"] = source_text
    write_json(project / "transcript/transcript.json", transcript)
    content_assets = read_json(project / "analysis/content-assets.json")
    content_assets["core_points"][0]["evidence"][0]["text"] = source_text
    content_assets["source_evidence"][0]["source_text"] = source_text
    write_json(project / "analysis/content-assets.json", content_assets)
    markdown_path = project / "analysis/xhs-post.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    markdown_path.write_text(markdown.replace("\n正文\n", f"\n{source_text}\n", 1), encoding="utf-8")

    result = verify_project(project)

    assert any(
        issue["code"] == "verbatim_source_copy_detected"
        and issue["artifact"] == "xhs_post_md"
        and issue["details"]["field"] == "markdown.正文"
        for issue in result["issues"]
    )


def test_verify_completed_project_reports_asset_package_missing_frame_paths(tmp_path: Path):
    project = tmp_path / "project"
    _write_valid_upstream_project(project, status="completed")
    _write_valid_downstream_outputs(project)
    asset_package = read_json(project / "analysis/asset-package.json")
    asset_package["materials"]["frame_paths"] = []
    write_json(project / "analysis/asset-package.json", asset_package)

    result = verify_project(project)

    issue_codes = {issue["code"] for issue in result["issues"]}
    assert "asset_package_materials_invalid" in issue_codes
