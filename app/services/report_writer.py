import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.runtime_store import ProjectPaths, read_json, write_json

FRAME_FILENAME_RE = re.compile(r"^frame_\d{4}\.jpg$")


def _md_list(items: List[Any]) -> str:
    if not items:
        return "- 暂无"
    return "\n".join(f"- {item}" for item in items)


def _resolve_standard_frame(paths: ProjectPaths, value: Any) -> Optional[Path]:
    if not value:
        return None
    raw_path = Path(str(value))
    candidate = raw_path if raw_path.is_absolute() else paths.project_dir / raw_path
    try:
        resolved = candidate.resolve()
        resolved.relative_to(paths.frames_dir.resolve())
    except (OSError, ValueError):
        return None
    if not resolved.is_file() or FRAME_FILENAME_RE.fullmatch(resolved.name) is None:
        return None
    return resolved


def _frame_paths(paths: ProjectPaths, keyframes_payload: Dict[str, Any]) -> List[str]:
    frame_paths: List[Path] = []
    seen = set()
    for frame in keyframes_payload.get("keyframes", []) or []:
        if not isinstance(frame, dict):
            continue
        resolved = _resolve_standard_frame(paths, frame.get("path"))
        if resolved is None or resolved in seen:
            continue
        frame_paths.append(resolved)
        seen.add(resolved)
    return [str(path) for path in sorted(frame_paths, key=lambda value: value.name)]


def _card_paths(paths: ProjectPaths, image_cards: Optional[Dict[str, Any]], cards_dir: Optional[Path] = None) -> List[str]:
    if not isinstance(image_cards, dict):
        return []
    cards_dir = cards_dir or paths.cards_dir
    card_paths: List[Path] = []
    seen = set()
    for card in image_cards.get("cards", []) or []:
        if not isinstance(card, dict):
            continue
        raw_path = Path(str(card.get("output_path") or ""))
        if not raw_path:
            continue
        candidate = raw_path if raw_path.is_absolute() else paths.project_dir / raw_path
        try:
            resolved = candidate.resolve()
            resolved.relative_to(cards_dir.resolve())
        except (OSError, ValueError):
            continue
        if not resolved.is_file() or resolved.suffix.lower() != ".png" or resolved in seen:
            continue
        card_paths.append(resolved)
        seen.add(resolved)
    return [str(path) for path in sorted(card_paths, key=lambda value: value.name)]


def _frame_anchor(item: Dict[str, Any]) -> str:
    time = item.get("source_frame_time")
    if time not in (None, ""):
        return f"{time}s"
    path = item.get("source_frame_path")
    if path:
        return str(path)
    return "未绑定"


def _evidence_anchor(item: Dict[str, Any]) -> str:
    time = item.get("time")
    if time not in (None, ""):
        return f"{time}s"
    path = item.get("source_path") or item.get("frame_path") or item.get("path")
    if path:
        return str(path)
    return "未绑定"


def write_reports(
    metadata: Dict[str, Any],
    transcript_payload: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    content_assets: Dict[str, Any],
    xhs_post: Dict[str, Any],
    image_prompts: Dict[str, Any],
    paths: ProjectPaths,
    warnings: List[str],
    image_cards: Optional[Dict[str, Any]] = None,
    platform: str = "xhs",
) -> Dict[str, Any]:
    image_cards = image_cards or {}
    is_toutiao = platform == "toutiao"
    platform_name = "今日头条" if is_toutiao else "小红书"
    text_only = content_assets.get("analysis_mode") == "text_only" or keyframes_payload.get("analysis_mode") == "text_only"
    post_key = "toutiao_post" if is_toutiao else "xiaohongshu_post"
    markdown_filename = "toutiao-post.md" if is_toutiao else "xhs-post.md"
    cards_dir = paths.toutiao_cards_dir if is_toutiao else paths.cards_dir
    card_paths = _card_paths(paths, image_cards, cards_dir=cards_dir)
    existing_package_path = paths.analysis_dir / "asset-package.json"
    if existing_package_path.exists():
        try:
            asset_package = read_json(existing_package_path)
        except Exception:
            asset_package = {}
    else:
        asset_package = {}
    asset_package.update(
        {
            "metadata": metadata,
            "transcript": {
                "path": str(paths.transcript_dir / "transcript.json"),
                "segment_count": transcript_payload.get("segment_count"),
                "source": transcript_payload.get("source"),
            },
            "keyframes": keyframes_payload,
            "visual_analysis": visual_payload,
            "content_assets": content_assets,
            post_key: xhs_post,
            f"{platform}_image_prompts": image_prompts.get("image_prompts", []),
            f"{platform}_image_cards": image_cards.get("cards", []),
            "materials": {
                "source_dir": str(paths.source_dir),
                "frames_dir": str(paths.frames_dir),
                "cards_dir": str(paths.cards_dir),
                "toutiao_cards_dir": str(paths.toutiao_cards_dir),
                "frame_paths": _frame_paths(paths, keyframes_payload),
                "card_paths": card_paths if not is_toutiao else asset_package.get("materials", {}).get("card_paths", []),
                "toutiao_card_paths": card_paths if is_toutiao else asset_package.get("materials", {}).get("toutiao_card_paths", []),
            },
            "warnings": warnings,
            "compliance": {
                "rights_boundary": "Only process public, owned, or authorized videos. Do not bypass login, paywall, DRM, or region restrictions.",
                "rewrite_boundary": "Generated copy should be an original article based on source facts and direction, not a verbatim transcript repost.",
            },
        }
    )
    if not is_toutiao:
        asset_package["xiaohongshu_post"] = xhs_post
        asset_package["image_prompts"] = image_prompts.get("image_prompts", [])
        asset_package["image_cards"] = image_cards.get("cards", [])
    if is_toutiao:
        asset_package["toutiao_post"] = xhs_post
        asset_package["toutiao_image_prompts"] = image_prompts.get("image_prompts", [])
        asset_package["toutiao_image_cards"] = image_cards.get("cards", [])
    write_json(paths.analysis_dir / "asset-package.json", asset_package)
    titles = xhs_post.get("titles", [])
    image_plan = xhs_post.get("image_plan", [])
    hashtags = xhs_post.get("hashtags", [])
    image_sections = ""
    if not text_only:
        image_sections = f"""
## 配图规划

{_md_list([f"第 {item.get('page')} 页｜{item.get('role')}｜{item.get('caption')}｜来源：{_frame_anchor(item)}｜内容点：{item.get('content_point') or ''}" for item in image_plan])}

## 图片提示词

{_md_list([f"第 {item.get('page')} 页｜{item.get('caption')}｜参考：{item.get('visual_reference')}｜来源：{_frame_anchor(item)}｜提示词：{item.get('image_prompt')}｜负向：{item.get('negative_prompt')}" for item in image_prompts.get("image_prompts", [])])}

## 图文卡片

{_md_list([f"第 {item.get('page')} 页｜{item.get('role')}｜{item.get('title')}｜{item.get('caption')}｜来源：{_frame_anchor(item)}｜文件：{item.get('output_path')}" for item in image_cards.get("cards", [])])}
"""
    else:
        image_sections = """
## 图片/截图

- 纯文案模式已启用：不抽关键帧、不 OCR、不生成截图、不生成图片卡片。
"""
    markdown = f"""# {platform_name}{'文章稿' if text_only else '图文稿'}

## 视频信息

- 标题：{metadata.get("title") or ""}
- 作者：{metadata.get("author") or ""}
- URL：{metadata.get("url") or ""}
- 时长：{metadata.get("duration") or ""}
- 视频 ID：{metadata.get("video_id") or ""}

## 一句话总结

{content_assets.get("one_sentence_summary") or ""}

## {platform_name}标题

{_md_list(titles)}

## 封面文案

{xhs_post.get("cover_text") or ""}

## 开头

{xhs_post.get("hook") or ""}

## 正文

{xhs_post.get("body") or ""}

{image_sections}

## 标签

{_md_list(hashtags)}

## 素材路径

- metadata：{paths.source_dir / "metadata.json"}
- transcript：{paths.transcript_dir / "transcript.json"}
- keyframes：{paths.analysis_dir / "keyframes.json"}
- visual analysis：{paths.analysis_dir / "visual-analysis.json"}
- frames：{"纯文案模式已跳过" if text_only else paths.frames_dir}
- cards：{"纯文案模式已跳过" if text_only else cards_dir}

## 来源时间点

{_md_list([f"{item.get('claim')}｜{item.get('source_type')}｜{_evidence_anchor(item)}｜{item.get('source_text')}" for item in content_assets.get("source_evidence", [])])}

## 合规说明

本稿仅适用于用户有权处理的视频、公开视频或用户自有视频；不绕过付费、登录、DRM 或地域限制；文案为基于原始信息的二次创作，保留来源信息便于追溯。
"""
    md_path = paths.analysis_dir / markdown_filename
    md_path.write_text(markdown, encoding="utf-8")
    return asset_package


def write_legacy_reports(
    metadata: Dict[str, Any],
    transcript_payload: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    content_assets: Dict[str, Any],
    xhs_post: Dict[str, Any],
    image_prompts: Dict[str, Any],
    paths: ProjectPaths,
    warnings: List[str],
    image_cards: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    image_cards = image_cards or {}
    card_paths = _card_paths(paths, image_cards)
    asset_package = {
        "metadata": metadata,
        "transcript": {
            "path": str(paths.transcript_dir / "transcript.json"),
            "segment_count": transcript_payload.get("segment_count"),
            "source": transcript_payload.get("source"),
        },
        "keyframes": keyframes_payload,
        "visual_analysis": visual_payload,
        "content_assets": content_assets,
        "xiaohongshu_post": xhs_post,
        "image_prompts": image_prompts.get("image_prompts", []),
        "image_cards": image_cards.get("cards", []),
        "materials": {
            "source_dir": str(paths.source_dir),
            "frames_dir": str(paths.frames_dir),
            "cards_dir": str(paths.cards_dir),
            "frame_paths": _frame_paths(paths, keyframes_payload),
            "card_paths": card_paths,
        },
        "warnings": warnings,
        "compliance": {
            "rights_boundary": "Only process public, owned, or authorized videos. Do not bypass login, paywall, DRM, or region restrictions.",
            "rewrite_boundary": "Generated copy should be an original article based on source facts and direction, not a verbatim transcript repost.",
        },
    }
    write_json(paths.analysis_dir / "asset-package.json", asset_package)

    titles = xhs_post.get("titles", [])
    image_plan = xhs_post.get("image_plan", [])
    hashtags = xhs_post.get("hashtags", [])
    markdown = f"""# 小红书图文稿

## 视频信息

- 标题：{metadata.get("title") or ""}
- 作者：{metadata.get("author") or ""}
- URL：{metadata.get("url") or ""}
- 时长：{metadata.get("duration") or ""}
- 视频 ID：{metadata.get("video_id") or ""}

## 一句话总结

{content_assets.get("one_sentence_summary") or ""}

## 小红书标题

{_md_list(titles)}

## 封面文案

{xhs_post.get("cover_text") or ""}

## 开头

{xhs_post.get("hook") or ""}

## 正文

{xhs_post.get("body") or ""}

## 配图规划

{_md_list([f"第 {item.get('page')} 页｜{item.get('role')}｜{item.get('caption')}｜来源：{_frame_anchor(item)}｜内容点：{item.get('content_point') or ''}" for item in image_plan])}

## 图片提示词

{_md_list([f"第 {item.get('page')} 页｜{item.get('caption')}｜参考：{item.get('visual_reference')}｜来源：{_frame_anchor(item)}｜提示词：{item.get('image_prompt')}｜负向：{item.get('negative_prompt')}" for item in image_prompts.get("image_prompts", [])])}

## 图文卡片

{_md_list([f"第 {item.get('page')} 页｜{item.get('role')}｜{item.get('title')}｜{item.get('caption')}｜来源：{_frame_anchor(item)}｜文件：{item.get('output_path')}" for item in image_cards.get("cards", [])])}

## 标签

{_md_list(hashtags)}

## 素材路径

- metadata：{paths.source_dir / "metadata.json"}
- transcript：{paths.transcript_dir / "transcript.json"}
- keyframes：{paths.analysis_dir / "keyframes.json"}
- visual analysis：{paths.analysis_dir / "visual-analysis.json"}
- frames：{paths.frames_dir}
- cards：{paths.cards_dir}

## 来源时间点

{_md_list([f"{item.get('claim')}｜{item.get('source_type')}｜{_evidence_anchor(item)}｜{item.get('source_text')}" for item in content_assets.get("source_evidence", [])])}

## 合规说明

本稿仅适用于用户有权处理的视频、公开视频或用户自有视频；不绕过付费、登录、DRM 或地域限制；文案为基于原始信息的二次创作，保留来源信息便于追溯。
"""
    md_path = paths.analysis_dir / "xhs-post.md"
    md_path.write_text(markdown, encoding="utf-8")
    return asset_package


def write_analysis_asset_package(
    metadata: Dict[str, Any],
    transcript_payload: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    content_assets: Dict[str, Any],
    paths: ProjectPaths,
    warnings: List[str],
) -> Dict[str, Any]:
    text_only = content_assets.get("analysis_mode") == "text_only" or keyframes_payload.get("analysis_mode") == "text_only"
    asset_package = {
        "status": "analysis_completed",
        "analysis_mode": "text_only" if text_only else "full",
        "metadata": metadata,
        "transcript": {
            "path": str(paths.transcript_dir / "transcript.json"),
            "segment_count": transcript_payload.get("segment_count"),
            "source": transcript_payload.get("source"),
        },
        "keyframes": keyframes_payload,
        "visual_analysis": visual_payload,
        "content_assets": content_assets,
        "materials": {
            "source_dir": str(paths.source_dir),
            "frames_dir": str(paths.frames_dir),
            "cards_dir": str(paths.cards_dir),
            "frame_paths": _frame_paths(paths, keyframes_payload),
            "card_paths": [],
        },
        "warnings": warnings,
        "next_step": {
            "action": "produce",
            "description": (
                "Review or edit content-assets.json, then run Produce to generate an original platform article. "
                "Text-only mode disables keyframes, OCR, screenshots, image prompts, and PNG cards."
                if text_only
                else "Review or edit content-assets.json, then run Produce to generate an original platform article. Run Generate Images after that to render PNG cards."
            ),
        },
        "compliance": {
            "rights_boundary": "Only process public, owned, or authorized videos. Do not bypass login, paywall, DRM, or region restrictions.",
            "rewrite_boundary": "Analyze only creates source-bound creative briefs; platform copy must become original writing based on source facts and direction.",
        },
    }
    write_json(paths.analysis_dir / "asset-package.json", asset_package)
    return asset_package


def write_partial_asset_package(
    paths: ProjectPaths,
    error: Dict[str, Any],
    warnings: List[str],
) -> Dict[str, Any]:
    """Write a truthful package of whatever the pipeline produced before failing."""
    known_files = {
        "metadata": paths.source_dir / "metadata.json",
        "transcript": paths.transcript_dir / "transcript.json",
        "keyframes": paths.analysis_dir / "keyframes.json",
        "visual_analysis": paths.analysis_dir / "visual-analysis.json",
        "content_assets": paths.analysis_dir / "content-assets.json",
        "xiaohongshu_post": paths.analysis_dir / "xiaohongshu-post.json",
        "image_prompts": paths.analysis_dir / "image-prompts.json",
        "image_cards": paths.analysis_dir / "image-cards.json",
        "toutiao_post": paths.analysis_dir / "toutiao-post.json",
        "toutiao_image_prompts": paths.analysis_dir / "toutiao-image-prompts.json",
        "toutiao_image_cards": paths.analysis_dir / "toutiao-image-cards.json",
    }
    loaded: Dict[str, Any] = {}
    available_files: Dict[str, str] = {}
    for key, path in known_files.items():
        if not path.exists():
            continue
        available_files[key] = str(path)
        try:
            loaded[key] = read_json(path)
        except Exception:
            loaded[key] = {"path": str(path), "read_error": "Could not parse JSON."}

    loaded_keyframes = loaded.get("keyframes")
    frame_paths = _frame_paths(paths, loaded_keyframes) if isinstance(loaded_keyframes, dict) else []
    if frame_paths:
        available_files["frames"] = str(paths.frames_dir)
    loaded_image_cards = loaded.get("image_cards")
    card_paths = _card_paths(paths, loaded_image_cards) if isinstance(loaded_image_cards, dict) else []
    if card_paths:
        available_files["cards"] = str(paths.cards_dir)
    loaded_toutiao_cards = loaded.get("toutiao_image_cards")
    toutiao_card_paths = _card_paths(paths, loaded_toutiao_cards, cards_dir=paths.toutiao_cards_dir) if isinstance(loaded_toutiao_cards, dict) else []
    if toutiao_card_paths:
        available_files["toutiao_cards"] = str(paths.toutiao_cards_dir)

    asset_package = {
        "status": "partial_failed",
        "error": error,
        "available_files": available_files,
        "metadata": loaded.get("metadata"),
        "transcript": loaded.get("transcript"),
        "keyframes": loaded.get("keyframes"),
        "visual_analysis": loaded.get("visual_analysis"),
        "content_assets": loaded.get("content_assets"),
        "xiaohongshu_post": loaded.get("xiaohongshu_post"),
        "image_prompts": loaded.get("image_prompts", {}).get("image_prompts", []),
        "image_cards": loaded.get("image_cards", {}).get("cards", []),
        "toutiao_post": loaded.get("toutiao_post"),
        "toutiao_image_prompts": loaded.get("toutiao_image_prompts", {}).get("image_prompts", []),
        "toutiao_image_cards": loaded.get("toutiao_image_cards", {}).get("cards", []),
        "materials": {
            "source_dir": str(paths.source_dir),
            "frames_dir": str(paths.frames_dir),
            "cards_dir": str(paths.cards_dir),
            "toutiao_cards_dir": str(paths.toutiao_cards_dir),
            "frame_paths": frame_paths,
            "card_paths": card_paths,
            "toutiao_card_paths": toutiao_card_paths,
        },
        "warnings": warnings,
        "compliance": {
            "rights_boundary": "Only process public, owned, or authorized videos. Do not bypass login, paywall, DRM, or region restrictions.",
            "rewrite_boundary": "No missing downstream content is fabricated in this partial package.",
        },
    }
    write_json(paths.analysis_dir / "asset-package.json", asset_package)
    return asset_package
