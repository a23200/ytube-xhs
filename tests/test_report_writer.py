from pathlib import Path

from app.services.report_writer import write_reports
from app.services.runtime_store import ProjectPaths, read_json


def test_write_reports_markdown_preserves_paths_prompts_and_source_anchors(tmp_path: Path):
    paths = ProjectPaths(tmp_path / "project")
    paths.ensure()
    frame_path = paths.frames_dir / "frame_0001.jpg"
    frame_path.write_bytes(b"jpg")

    metadata = {
        "video_id": "v1",
        "url": "https://example.com/video",
        "title": "标题",
        "author": "作者",
        "duration": 12,
    }
    transcript = {"segment_count": 1, "source": "subtitle", "segments": []}
    keyframes = {
        "frame_count": 3,
        "keyframes": [
            {
                "time": 1.0,
                "path": str(frame_path),
                "score": 0.9,
                "reason": "clear",
            },
            {
                "time": 2.0,
                "path": str(paths.source_dir / "frame_0002.jpg"),
                "score": 0.8,
                "reason": "outside frames dir",
            },
            {
                "time": 3.0,
                "path": str(paths.frames_dir / "frame_bad.jpg"),
                "score": 0.7,
                "reason": "non-standard filename",
            },
        ],
    }
    visual = {"frames": [], "warnings": []}
    content_assets = {
        "one_sentence_summary": "一句话总结",
        "source_evidence": [
            {
                "claim": "观点",
                "source_type": "keyframe",
                "source_path": str(frame_path),
                "source_text": "证据文本",
            }
        ],
    }
    xhs_post = {
        "titles": ["标题1", "标题2", "标题3", "标题4", "标题5"],
        "cover_text": "封面",
        "hook": "开头",
        "body": "正文",
        "image_plan": [
            {
                "page": 1,
                "role": "cover",
                "caption": "封面图",
                "source_frame_path": str(frame_path),
                "content_point": "观点",
            }
        ],
        "hashtags": ["#标签"],
    }
    image_prompts = {
        "image_prompts": [
            {
                "page": 1,
                "role": "cover",
                "caption": "封面图",
                "source_frame_time": None,
                "source_frame_path": str(frame_path),
                "visual_reference": "参考关键帧",
                "image_prompt": "构图、主体、背景、色调、留白齐全。",
                "negative_prompt": "不要直接复刻截图。",
            }
        ]
    }

    package = write_reports(
        metadata,
        transcript,
        keyframes,
        visual,
        content_assets,
        xhs_post,
        image_prompts,
        paths,
        warnings=["warning"],
    )

    markdown = (paths.analysis_dir / "xhs-post.md").read_text(encoding="utf-8")
    saved_package = read_json(paths.analysis_dir / "asset-package.json")

    assert package["materials"]["frame_paths"] == [str(frame_path)]
    assert saved_package["warnings"] == ["warning"]
    for section in ["视频信息", "小红书标题", "封面文案", "正文", "配图规划", "图片提示词", "标签", "素材路径", "来源时间点"]:
        assert section in markdown
    assert str(frame_path) in markdown
    assert "参考关键帧" in markdown
    assert "不要直接复刻截图" in markdown
    assert "证据文本" in markdown
