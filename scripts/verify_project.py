#!/usr/bin/env python3
import argparse
import html
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REQUIRED_UPSTREAM = {
    "metadata": "source/metadata.json",
    "transcript": "transcript/transcript.json",
    "keyframes": "analysis/keyframes.json",
    "visual_analysis": "analysis/visual-analysis.json",
    "run_metadata": "analysis/run-metadata.json",
}

REQUIRED_COMPLETED = {
    **REQUIRED_UPSTREAM,
    "content_assets": "analysis/content-assets.json",
    "xhs_post_json": "analysis/xiaohongshu-post.json",
    "xhs_post_docx": "analysis/xhs-article.docx",
    "xhs_quality_report": "analysis/xhs-quality-report.json",
    "image_prompts": "analysis/image-prompts.json",
    "image_cards": "analysis/image-cards.json",
    "asset_package": "analysis/asset-package.json",
    "xhs_post_md": "analysis/xhs-post.md",
}

ARTICLE_COMPLETED_STATUSES = {
    "xhs_completed",
    "toutiao_completed",
    "douyin_completed",
    "bilibili_completed",
}
ARTICLE_STATUS_TO_PLATFORM = {
    "xhs_completed": "xhs",
    "toutiao_completed": "toutiao",
    "douyin_completed": "douyin",
    "bilibili_completed": "bilibili",
}
PLATFORM_NAMES = {
    "xhs": "小红书",
    "toutiao": "今日头条",
    "douyin": "抖音",
    "bilibili": "哔哩哔哩",
}
PLATFORM_BODY_LIMITS = {
    "xhs": (800, 1400),
    "toutiao": (1200, 2200),
    "douyin": (500, 1000),
    "bilibili": (1000, 2000),
}
PLATFORM_POST_KINDS = {
    "xhs": "xhs_post_json",
    "toutiao": "toutiao_post_json",
    "douyin": "douyin_post_json",
    "bilibili": "bilibili_post_json",
}
PLATFORM_POST_PACKAGE_KEYS = {
    "xhs": "xiaohongshu_post",
    "toutiao": "toutiao_post",
    "douyin": "douyin_post",
    "bilibili": "bilibili_post",
}
PLATFORM_QUALITY_PACKAGE_KEYS = {
    platform: f"{platform}_quality_report" for platform in PLATFORM_NAMES
}
PLATFORM_MARKDOWN_HEADINGS = {
    "xhs": "小红书标题",
    "toutiao": "今日头条标题",
    "douyin": "抖音标题",
    "bilibili": "哔哩哔哩标题",
}

FILE_KIND_TO_PATH = {
    "metadata": "source/metadata.json",
    "transcript": "transcript/transcript.json",
    "keyframes": "analysis/keyframes.json",
    "visual_analysis": "analysis/visual-analysis.json",
    "content_assets": "analysis/content-assets.json",
    "xhs_post_json": "analysis/xiaohongshu-post.json",
    "xhs_post_md": "analysis/xhs-post.md",
    "xhs_post_docx": "analysis/xhs-article.docx",
    "xhs_quality_report": "analysis/xhs-quality-report.json",
    "image_prompts": "analysis/image-prompts.json",
    "image_cards": "analysis/image-cards.json",
    "toutiao_post_json": "analysis/toutiao-post.json",
    "toutiao_post_md": "analysis/toutiao-post.md",
    "toutiao_post_docx": "analysis/toutiao-article.docx",
    "toutiao_quality_report": "analysis/toutiao-quality-report.json",
    "toutiao_image_prompts": "analysis/toutiao-image-prompts.json",
    "toutiao_image_cards": "analysis/toutiao-image-cards.json",
    "douyin_post_json": "analysis/douyin-post.json",
    "douyin_post_md": "analysis/douyin-post.md",
    "douyin_post_docx": "analysis/douyin-article.docx",
    "douyin_quality_report": "analysis/douyin-quality-report.json",
    "bilibili_post_json": "analysis/bilibili-post.json",
    "bilibili_post_md": "analysis/bilibili-post.md",
    "bilibili_post_docx": "analysis/bilibili-article.docx",
    "bilibili_quality_report": "analysis/bilibili-quality-report.json",
    "asset_package": "analysis/asset-package.json",
    "run_metadata": "analysis/run-metadata.json",
}

CONTENT_ASSET_LIST_FIELDS = [
    "core_points",
    "golden_quotes",
    "chapters",
    "steps",
    "audience",
    "pain_points",
    "xiaohongshu_angles",
    "source_evidence",
]

XHS_POST_FIELDS = [
    "content_type",
    "target_audience",
    "titles",
    "cover_text",
    "hook",
    "body",
    "image_plan",
    "hashtags",
    "publish_suggestion",
]

IMAGE_PROMPT_FIELDS = [
    "page",
    "role",
    "caption",
    "source_frame_time",
    "visual_reference",
    "image_prompt",
    "negative_prompt",
]

IMAGE_PROMPT_VALUE_FIELDS = [
    "page",
    "role",
    "caption",
    "visual_reference",
    "image_prompt",
    "negative_prompt",
]

IMAGE_PROMPT_REQUIRED_KEYWORDS = ["构图", "主体", "背景", "色调", "留白"]
NEGATIVE_PROMPT_COPY_TERMS = ["复刻", "截图"]
IMAGE_PROMPT_FORBIDDEN_COPY_TERMS = ["直接复刻", "复刻截图", "照搬截图", "还原截图", "原样截图", "复制截图"]
IMAGE_CARD_FIELDS = [
    "page",
    "role",
    "title",
    "caption",
    "source_frame_time",
    "source_frame_path",
    "layout",
    "style",
    "output_path",
    "image_prompt",
]
FRAME_TIME_TOLERANCE_SECONDS = 1.5
TRANSCRIPT_TIME_TOLERANCE_SECONDS = 0.75
MARKDOWN_REQUIRED_SECTIONS = [
    "视频信息",
    "一句话总结",
    "小红书标题",
    "封面文案",
    "正文",
    "配图规划",
    "图片提示词",
    "图文卡片",
    "标签",
    "素材路径",
    "来源时间点",
]
MARKDOWN_REQUIRED_MATERIAL_PATHS = [
    "source/metadata.json",
    "transcript/transcript.json",
    "analysis/keyframes.json",
    "analysis/visual-analysis.json",
    "frames",
    "cards",
]
MARKDOWN_PLACEHOLDER_VALUES = {"", "暂无", "-", "无", "N/A", "n/a", "None", "none"}
MIN_VERBATIM_CHARS = 24
FRAME_FILENAME_RE = re.compile(r"^frame_\d{4}\.jpg$")
CARD_FILENAME_RE = re.compile(r"^(cover|summary|slide_\d{2})\.png$")
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
TIMESTAMP_TAG_RE = re.compile(r"<\d{1,2}:\d{2}:\d{2}[.,]\d{3}>")


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json_or_issue(path: Path, artifact: str, issues: List[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        payload = _read_json(path)
    except Exception as exc:
        issues.append(
            {
                "code": "invalid_json",
                "artifact": artifact,
                "path": str(path),
                "message": str(exc),
            }
        )
        return {}
    if not isinstance(payload, dict):
        issues.append(
            {
                "code": "invalid_json_shape",
                "artifact": artifact,
                "path": str(path),
                "message": "Expected a JSON object.",
            }
        )
        return {}
    return payload


def _add_issue(
    issues: List[Dict[str, Any]],
    code: str,
    artifact: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    issue: Dict[str, Any] = {"code": code, "artifact": artifact, "message": message}
    if details:
        issue["details"] = details
    issues.append(issue)


def _required_fields(payload: Dict[str, Any], fields: Iterable[str]) -> List[str]:
    return [field for field in fields if payload.get(field) in (None, "", [])]


def _missing_keys(payload: Dict[str, Any], fields: Iterable[str]) -> List[str]:
    return [field for field in fields if field not in payload]


def _path_from_payload(project_dir: Path, value: Any) -> Optional[Path]:
    if not value:
        return None
    path = Path(str(value))
    if path.is_absolute():
        return path
    return project_dir / path


def _as_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(*values: Any) -> Optional[float]:
    for value in values:
        parsed = _as_float(value)
        if parsed is not None:
            return parsed
    return None


def _transcript_ranges(transcript_payload: Dict[str, Any]) -> List[tuple[float, float]]:
    ranges = []
    for segment in transcript_payload.get("segments", []) or []:
        start = _as_float(segment.get("start")) if isinstance(segment, dict) else None
        end = _as_float(segment.get("end")) if isinstance(segment, dict) else None
        if start is None or end is None:
            continue
        ranges.append((start, max(start, end)))
    return ranges


def _keyframe_times(project_dir: Path, keyframes_payload: Dict[str, Any]) -> List[float]:
    times = []
    for frame in keyframes_payload.get("keyframes", []) or []:
        resolved = _resolved_project_path(project_dir, frame.get("path")) if isinstance(frame, dict) else None
        if resolved is None or not _is_standard_project_frame(project_dir, resolved):
            continue
        time = _as_float(frame.get("time")) if isinstance(frame, dict) else None
        if time is not None:
            times.append(time)
    return times


def _resolved_project_path(project_dir: Path, value: Any) -> Optional[Path]:
    path = _path_from_payload(project_dir, value)
    if path is None:
        return None
    try:
        resolved = path.resolve()
        resolved.relative_to(project_dir.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _is_standard_project_frame(project_dir: Path, path: Path) -> bool:
    try:
        resolved = path.resolve()
        resolved.relative_to((project_dir / "frames").resolve())
    except (OSError, ValueError):
        return False
    return resolved.is_file() and FRAME_FILENAME_RE.fullmatch(resolved.name) is not None


def _is_standard_project_card(project_dir: Path, path: Path, platform: str = "xhs") -> bool:
    directory = "toutiao-cards" if platform == "toutiao" else "cards"
    try:
        resolved = path.resolve()
        resolved.relative_to((project_dir / directory).resolve())
    except (OSError, ValueError):
        return False
    return resolved.is_file() and CARD_FILENAME_RE.fullmatch(resolved.name) is not None


def _keyframe_paths(project_dir: Path, keyframes_payload: Dict[str, Any]) -> set[Path]:
    paths = set()
    for frame in keyframes_payload.get("keyframes", []) or []:
        if not isinstance(frame, dict):
            continue
        resolved = _resolved_project_path(project_dir, frame.get("path"))
        if resolved is not None and _is_standard_project_frame(project_dir, resolved):
            paths.add(resolved)
    return paths


def _card_paths(project_dir: Path, image_cards_payload: Dict[str, Any], platform: str = "xhs") -> set[Path]:
    paths = set()
    for card in image_cards_payload.get("cards", []) or []:
        if not isinstance(card, dict):
            continue
        resolved = _resolved_project_path(project_dir, card.get("output_path"))
        if resolved is not None and _is_standard_project_card(project_dir, resolved, platform):
            paths.add(resolved)
    return paths


def _time_in_transcript(time: float, transcript_ranges: List[tuple[float, float]]) -> bool:
    return any(start - TRANSCRIPT_TIME_TOLERANCE_SECONDS <= time <= end + TRANSCRIPT_TIME_TOLERANCE_SECONDS for start, end in transcript_ranges)


def _time_near_keyframe(time: float, keyframe_times: Iterable[float]) -> bool:
    return any(abs(time - frame_time) <= FRAME_TIME_TOLERANCE_SECONDS for frame_time in keyframe_times)


def _project_path_exists(project_dir: Path, value: Any, allowed_paths: Optional[set[Path]] = None) -> bool:
    resolved = _resolved_project_path(project_dir, value)
    if resolved is None or not resolved.exists():
        return False
    if allowed_paths is not None and resolved not in allowed_paths:
        return False
    return True


def _has_evidence_anchor(item: Dict[str, Any]) -> bool:
    return item.get("time") not in (None, "") or item.get("frame_path") not in (None, "") or item.get("path") not in (None, "")


def _relative_output_path(project_dir: Path, value: Any) -> Optional[Path]:
    if not value or Path(str(value)).is_absolute():
        return None
    path = (project_dir / str(value)).resolve()
    try:
        path.relative_to(project_dir)
    except ValueError:
        return None
    return path


def _add_anchor_issue(
    issues: List[Dict[str, Any]],
    artifact: str,
    field: str,
    index: int,
    message: str,
    details: Dict[str, Any],
) -> None:
    _add_issue(
        issues,
        "source_anchor_invalid",
        artifact,
        message,
        {"field": field, "index": index, **details},
    )


def _markdown_sections(markdown: str) -> Dict[str, str]:
    heading_pattern = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
    matches = list(heading_pattern.finditer(markdown))
    sections: Dict[str, str] = {}
    for index, match in enumerate(matches):
        heading = match.group(1).strip().strip("#").strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections[heading] = markdown[start:end].strip()
    return sections


def _markdown_content_is_useful(content: str) -> bool:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return False
    meaningful_lines = []
    for line in lines:
        cleaned = re.sub(r"^[\s\-*•\d.、|：:]+", "", line).strip()
        cleaned = cleaned.strip("`*_ ")
        if cleaned not in MARKDOWN_PLACEHOLDER_VALUES:
            meaningful_lines.append(cleaned)
    return bool(meaningful_lines)


def _markdown_contains_any(markdown: str, values: Iterable[Any], min_length: int = 2) -> bool:
    for value in values:
        text = str(value or "").strip()
        if len(text) >= min_length and text in markdown:
            return True
    return False


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = TIMESTAMP_TAG_RE.sub("", text)
    text = TAG_RE.sub("", text)
    text = text.replace("\u200b", "")
    return SPACE_RE.sub(" ", text).strip()


def _article_body_length(value: Any) -> int:
    return len(re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", _clean_text(value)))


def _unique_clean_texts(values: Iterable[Any]) -> List[str]:
    texts = []
    seen = set()
    for value in values:
        text = _clean_text(value)
        if text and text not in seen:
            texts.append(text)
            seen.add(text)
    return texts


def _contains_long_verbatim(source: str, generated: str, min_chars: int = MIN_VERBATIM_CHARS) -> str:
    source = _clean_text(source)
    generated = _clean_text(generated)
    if len(source) < min_chars or len(generated) < min_chars:
        return ""
    if source in generated:
        return source[:120]
    for start in range(0, len(source) - min_chars + 1):
        snippet = source[start : start + min_chars]
        if snippet.strip() and snippet in generated:
            return snippet
    return ""


def _verbatim_source_texts(transcript_payload: Dict[str, Any], content_assets: Dict[str, Any]) -> List[str]:
    texts = []
    for segment in transcript_payload.get("segments", []) or []:
        if isinstance(segment, dict):
            texts.append(segment.get("text"))
    for item in content_assets.get("source_evidence", []) or []:
        if isinstance(item, dict):
            texts.append(item.get("source_text"))
    for point in content_assets.get("core_points", []) or []:
        if not isinstance(point, dict):
            continue
        for evidence in point.get("evidence", []) or []:
            if isinstance(evidence, dict):
                texts.append(evidence.get("text"))
    return _unique_clean_texts(texts)


def _content_asset_generated_texts(content_assets: Dict[str, Any]) -> List[tuple[str, str]]:
    texts: List[tuple[str, str]] = []
    for field in ["one_sentence_summary", "recommended_content_type"]:
        if content_assets.get(field):
            texts.append((field, str(content_assets[field])))
    for index, point in enumerate(content_assets.get("core_points", []) or []):
        if not isinstance(point, dict):
            continue
        for field in ["point", "why_it_matters"]:
            if point.get(field):
                texts.append((f"core_points[{index}].{field}", str(point[field])))
    for index, quote in enumerate(content_assets.get("golden_quotes", []) or []):
        if not isinstance(quote, dict):
            continue
        for field in ["quote", "rewrite_note"]:
            if quote.get(field):
                texts.append((f"golden_quotes[{index}].{field}", str(quote[field])))
    for index, chapter in enumerate(content_assets.get("chapters", []) or []):
        if not isinstance(chapter, dict):
            continue
        for field in ["title", "summary"]:
            if chapter.get(field):
                texts.append((f"chapters[{index}].{field}", str(chapter[field])))
    for index, step in enumerate(content_assets.get("steps", []) or []):
        if isinstance(step, dict) and step.get("step"):
            texts.append((f"steps[{index}].step", str(step["step"])))
    for field in ["audience", "pain_points", "xiaohongshu_angles"]:
        for index, item in enumerate(content_assets.get(field, []) or []):
            texts.append((f"{field}[{index}]", str(item)))
    for index, item in enumerate(content_assets.get("source_evidence", []) or []):
        if isinstance(item, dict) and item.get("claim"):
            texts.append((f"source_evidence[{index}].claim", str(item["claim"])))
    return [(field, text) for field, text in ((field, _clean_text(text)) for field, text in texts) if text]


def _xhs_generated_texts(xhs_post: Dict[str, Any]) -> List[tuple[str, str]]:
    texts: List[tuple[str, str]] = []
    for field in ["cover_text", "hook", "body", "publish_suggestion"]:
        if xhs_post.get(field):
            texts.append((field, str(xhs_post[field])))
    for index, title in enumerate(xhs_post.get("titles", []) or []):
        texts.append((f"titles[{index}]", str(title)))
    for index, audience in enumerate(xhs_post.get("target_audience", []) or []):
        texts.append((f"target_audience[{index}]", str(audience)))
    for index, hashtag in enumerate(xhs_post.get("hashtags", []) or []):
        texts.append((f"hashtags[{index}]", str(hashtag)))
    for index, item in enumerate(xhs_post.get("image_plan", []) or []):
        if not isinstance(item, dict):
            continue
        for field in ["caption", "content_point"]:
            if item.get(field):
                texts.append((f"image_plan[{index}].{field}", str(item[field])))
    return [(field, text) for field, text in ((field, _clean_text(text)) for field, text in texts) if text]


def _markdown_generated_texts(markdown_sections: Dict[str, str]) -> List[tuple[str, str]]:
    generated_sections = [
        "一句话总结",
        "小红书标题",
        "封面文案",
        "开头",
        "正文",
        "配图规划",
        "标签",
    ]
    return [
        (f"markdown.{section}", text)
        for section in generated_sections
        if (text := _clean_text(markdown_sections.get(section)))
    ]


def _verify_no_verbatim_copy(
    *,
    issues: List[Dict[str, Any]],
    artifact: str,
    source_texts: Iterable[str],
    generated_texts: Iterable[tuple[str, str]],
) -> None:
    for source in source_texts:
        for field, generated in generated_texts:
            match = _contains_long_verbatim(source, generated)
            if match:
                _add_issue(
                    issues,
                    "verbatim_source_copy_detected",
                    artifact,
                    "Generated content contains a long verbatim source fragment.",
                    {"field": field, "matched_fragment": match, "min_chars": MIN_VERBATIM_CHARS},
                )
                return


def _markdown_time_values(items: Iterable[Dict[str, Any]]) -> List[str]:
    values = []
    for item in items:
        time = _first_float(item.get("time"), item.get("evidence_time"), item.get("source_frame_time"))
        if time is None:
            continue
        values.extend([f"{time}s", f"{time:g}s"])
    return values


def _validate_source_anchor(
    project_dir: Path,
    item: Dict[str, Any],
    *,
    source_type: str,
    transcript_ranges: List[tuple[float, float]],
    keyframe_times: List[float],
    keyframe_paths: set[Path],
    issues: List[Dict[str, Any]],
    artifact: str,
    field: str,
    index: int,
) -> None:
    time = _first_float(item.get("time"), item.get("evidence_time"), item.get("source_frame_time"))
    frame_path = item.get("frame_path") or item.get("source_frame_path") or item.get("path")
    source_path = item.get("source_path")
    normalized_type = source_type.lower()
    if frame_path and not _project_path_exists(project_dir, frame_path, keyframe_paths):
        _add_anchor_issue(
            issues,
            artifact,
            field,
            index,
            "Source frame path does not match an extracted keyframe.",
            {"path": frame_path},
        )
        return
    if source_path:
        if normalized_type in {"keyframe", "ocr", "visual", "frame"}:
            if not _project_path_exists(project_dir, source_path, keyframe_paths):
                _add_anchor_issue(
                    issues,
                    artifact,
                    field,
                    index,
                    "Frame or visual source path does not match an extracted keyframe.",
                    {"path": source_path, "source_type": source_type},
                )
                return
        elif not _project_path_exists(project_dir, source_path):
            _add_anchor_issue(
                issues,
                artifact,
                field,
                index,
                "Source path does not point to an existing project artifact.",
                {"path": source_path},
            )
            return
    if time is None:
        if frame_path or source_path:
            return
        _add_anchor_issue(
            issues,
            artifact,
            field,
            index,
            "Source anchor must include a valid time, frame path, or source path.",
            {"value": item},
        )
        return
    if normalized_type == "transcript" and not _time_in_transcript(time, transcript_ranges):
        _add_anchor_issue(
            issues,
            artifact,
            field,
            index,
            "Transcript source time does not overlap any transcript segment.",
            {"time": time},
        )
    if normalized_type in {"keyframe", "ocr", "visual", "frame"} and not _time_near_keyframe(time, keyframe_times):
        _add_anchor_issue(
            issues,
            artifact,
            field,
            index,
            "Frame or visual source time does not match an extracted keyframe.",
            {"time": time},
        )
    if normalized_type not in {"transcript", "keyframe", "ocr", "visual", "frame"} and not (
        _time_in_transcript(time, transcript_ranges) or _time_near_keyframe(time, keyframe_times)
    ):
        _add_anchor_issue(
            issues,
            artifact,
            field,
            index,
            "Source time does not match any transcript segment or extracted keyframe.",
            {"time": time, "source_type": source_type},
        )


def _platform_for_record(record: Dict[str, Any], status: str) -> str:
    configured = record.get("target_platform")
    if configured in PLATFORM_NAMES:
        return str(configured)
    mapped = ARTICLE_STATUS_TO_PLATFORM.get(status)
    if mapped:
        return mapped
    logs = record.get("logs") if isinstance(record.get("logs"), list) else []
    for log in reversed(logs):
        if not isinstance(log, dict):
            continue
        details = log.get("details")
        if isinstance(details, dict) and details.get("platform") in PLATFORM_NAMES:
            return str(details["platform"])
    outputs = record.get("outputs") if isinstance(record.get("outputs"), dict) else {}
    for platform, kind in PLATFORM_POST_KINDS.items():
        if outputs.get(kind):
            return platform
    return "xhs"


def _article_required_outputs(platform: str, *, text_only: bool, require_images: bool) -> Dict[str, str]:
    post_kind = PLATFORM_POST_KINDS[platform]
    required = {
        **REQUIRED_UPSTREAM,
        "content_assets": FILE_KIND_TO_PATH["content_assets"],
        post_kind: FILE_KIND_TO_PATH[post_kind],
        f"{platform}_post_md": FILE_KIND_TO_PATH[f"{platform}_post_md"],
        f"{platform}_post_docx": FILE_KIND_TO_PATH[f"{platform}_post_docx"],
        f"{platform}_quality_report": FILE_KIND_TO_PATH[f"{platform}_quality_report"],
        "asset_package": FILE_KIND_TO_PATH["asset_package"],
    }
    if not text_only and platform in {"xhs", "toutiao"}:
        required[f"{platform}_image_prompts" if platform != "xhs" else "image_prompts"] = FILE_KIND_TO_PATH[
            f"{platform}_image_prompts" if platform != "xhs" else "image_prompts"
        ]
        if require_images:
            required[f"{platform}_image_cards" if platform != "xhs" else "image_cards"] = FILE_KIND_TO_PATH[
                f"{platform}_image_cards" if platform != "xhs" else "image_cards"
            ]
    return required


def _article_post_fields(post: Dict[str, Any]) -> List[str]:
    return XHS_POST_FIELDS


def _verify_article_content_assets(
    project_dir: Path,
    content_assets: Dict[str, Any],
    transcript_payload: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    issues: List[Dict[str, Any]],
) -> List[str]:
    missing_fields = _required_fields(
        content_assets,
        [
            "one_sentence_summary",
            "core_points",
            "golden_quotes",
            "chapters",
            "steps",
            "audience",
            "pain_points",
            "xiaohongshu_angles",
            "recommended_content_type",
            "source_evidence",
        ],
    )
    if missing_fields:
        _add_issue(
            issues,
            "content_assets_missing_fields",
            "content_assets",
            "content-assets.json is missing required fields.",
            {"missing_fields": missing_fields},
        )
    for field in CONTENT_ASSET_LIST_FIELDS:
        if not isinstance(content_assets.get(field), list) or not content_assets.get(field):
            _add_issue(
                issues,
                "content_assets_empty_list",
                "content_assets",
                f"content-assets.json must contain non-empty list field: {field}.",
                {"field": field},
            )
    transcript_ranges = _transcript_ranges(transcript_payload)
    keyframe_times = _keyframe_times(project_dir, keyframes_payload)
    allowed_keyframe_paths = _keyframe_paths(project_dir, keyframes_payload)
    for index, point in enumerate(content_assets.get("core_points") or []):
        if not isinstance(point, dict):
            _add_issue(issues, "invalid_core_point", "content_assets", "Core point must be an object.", {"index": index})
            continue
        missing_point_fields = _required_fields(point, ["point", "evidence"])
        if missing_point_fields:
            _add_issue(issues, "invalid_core_point", "content_assets", "Core point is missing required fields.", {"index": index, "missing_fields": missing_point_fields})
            continue
        evidence = point.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            _add_issue(issues, "invalid_core_point_evidence", "content_assets", "Core point must include evidence.", {"index": index})
            continue
        for evidence_index, evidence_item in enumerate(evidence):
            if not isinstance(evidence_item, dict):
                _add_issue(issues, "invalid_core_point_evidence", "content_assets", "Core point evidence must be an object.", {"index": index, "evidence_index": evidence_index})
                continue
            missing_evidence_fields = _required_fields(evidence_item, ["type", "text"])
            if missing_evidence_fields:
                _add_issue(issues, "invalid_core_point_evidence", "content_assets", "Core point evidence is missing required fields.", {"index": index, "evidence_index": evidence_index, "missing_fields": missing_evidence_fields})
                continue
            if not _has_evidence_anchor(evidence_item):
                _add_issue(issues, "core_point_evidence_missing_anchor", "content_assets", "Core point evidence must include a source time or frame path.", {"index": index, "evidence_index": evidence_index})
                continue
            _validate_source_anchor(
                project_dir,
                evidence_item,
                source_type=str(evidence_item.get("type") or ""),
                transcript_ranges=transcript_ranges,
                keyframe_times=keyframe_times,
                keyframe_paths=allowed_keyframe_paths,
                issues=issues,
                artifact="content_assets",
                field="core_points.evidence",
                index=evidence_index,
            )
    for field, item_fields in [
        ("golden_quotes", ["quote", "rewrite_note"]),
        ("chapters", ["title", "summary"]),
        ("steps", ["step"]),
        ("source_evidence", ["claim", "source_type", "source_text"]),
    ]:
        for index, item in enumerate(content_assets.get(field) or []):
            if not isinstance(item, dict):
                _add_issue(issues, "invalid_content_asset_item", "content_assets", "Content asset item must be an object.", {"field": field, "index": index})
                continue
            missing_item_fields = _required_fields(item, item_fields)
            if missing_item_fields:
                _add_issue(issues, "invalid_content_asset_item", "content_assets", "Content asset item is missing required fields.", {"field": field, "index": index, "missing_fields": missing_item_fields})
                continue
            if field == "source_evidence":
                if item.get("time") in (None, "") and item.get("source_path") in (None, ""):
                    _add_issue(issues, "source_evidence_missing_anchor", "content_assets", "Source evidence must include a source time or source path.", {"field": field, "index": index})
                    continue
                _validate_source_anchor(
                    project_dir,
                    item,
                    source_type=str(item.get("source_type") or ""),
                    transcript_ranges=transcript_ranges,
                    keyframe_times=keyframe_times,
                    keyframe_paths=allowed_keyframe_paths,
                    issues=issues,
                    artifact="content_assets",
                    field="source_evidence",
                    index=index,
                )
    source_texts = _verbatim_source_texts(transcript_payload, content_assets)
    _verify_no_verbatim_copy(
        issues=issues,
        artifact="content_assets",
        source_texts=source_texts,
        generated_texts=_content_asset_generated_texts(content_assets),
    )
    return source_texts


def _verify_article_image_prompts(
    project_dir: Path,
    path: Path,
    keyframes_payload: Dict[str, Any],
    issues: List[Dict[str, Any]],
    *,
    artifact: str,
    allow_missing_frame_source: bool = False,
) -> Dict[str, Any]:
    payload = _read_json_or_issue(path, artifact, issues)
    items = payload.get("image_prompts")
    if not isinstance(items, list) or not items:
        _add_issue(issues, "image_prompts_empty", artifact, "Image prompt artifact must contain non-empty image_prompts.")
        return payload
    keyframe_times = _keyframe_times(project_dir, keyframes_payload)
    keyframe_paths = _keyframe_paths(project_dir, keyframes_payload)
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            _add_issue(issues, "invalid_image_prompt", artifact, "Image prompt must be an object.", {"index": index})
            continue
        missing = _missing_keys(item, IMAGE_PROMPT_FIELDS)
        if missing:
            _add_issue(issues, "invalid_image_prompt", artifact, "Image prompt is missing required fields.", {"index": index, "missing_fields": missing})
            continue
        missing_values = _required_fields(item, IMAGE_PROMPT_VALUE_FIELDS)
        if missing_values:
            _add_issue(issues, "invalid_image_prompt", artifact, "Image prompt is missing required values.", {"index": index, "missing_fields": missing_values})
            continue
        prompt = str(item.get("image_prompt") or "")
        missing_keywords = [keyword for keyword in IMAGE_PROMPT_REQUIRED_KEYWORDS if keyword not in prompt]
        if missing_keywords:
            _add_issue(issues, "image_prompt_missing_visual_requirements", artifact, "Image prompt is missing visual requirements.", {"index": index, "missing_keywords": missing_keywords})
        forbidden = [term for term in IMAGE_PROMPT_FORBIDDEN_COPY_TERMS if term in prompt]
        if forbidden:
            _add_issue(issues, "image_prompt_requests_screenshot_copy", artifact, "Image prompt requests source screenshot copying.", {"index": index, "forbidden_terms": forbidden})
        if not any(term in str(item.get("negative_prompt") or "") for term in NEGATIVE_PROMPT_COPY_TERMS):
            _add_issue(issues, "image_prompt_missing_copy_guard", artifact, "Negative prompt must avoid screenshot recreation.", {"index": index})
        prompt_time = _as_float(item.get("source_frame_time"))
        prompt_path = item.get("source_frame_path")
        if prompt_path and not _project_path_exists(project_dir, prompt_path, keyframe_paths):
            _add_anchor_issue(issues, artifact, "image_prompts", index, "Image prompt path does not match an extracted keyframe.", {"path": prompt_path})
        if prompt_time is not None and not _time_near_keyframe(prompt_time, keyframe_times):
            _add_anchor_issue(issues, artifact, "image_prompts", index, "Image prompt time does not match an extracted keyframe.", {"time": prompt_time})
        if prompt_time is None and not prompt_path and not allow_missing_frame_source:
            _add_anchor_issue(issues, artifact, "image_prompts", index, "Image prompt must reference a keyframe.", {})
    return payload


def _verify_article_image_cards(
    project_dir: Path,
    path: Path,
    keyframes_payload: Dict[str, Any],
    issues: List[Dict[str, Any]],
    *,
    artifact: str,
    platform: str = "xhs",
) -> Dict[str, Any]:
    payload = _read_json_or_issue(path, artifact, issues)
    items = payload.get("cards")
    if not isinstance(items, list) or not items:
        _add_issue(issues, "image_cards_empty", artifact, "Image card artifact must contain non-empty cards.")
        return payload
    keyframe_times = _keyframe_times(project_dir, keyframes_payload)
    keyframe_paths = _keyframe_paths(project_dir, keyframes_payload)
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            _add_issue(issues, "invalid_image_card", artifact, "Image card must be an object.", {"index": index})
            continue
        missing = _missing_keys(item, IMAGE_CARD_FIELDS)
        if missing:
            _add_issue(issues, "invalid_image_card", artifact, "Image card is missing required fields.", {"index": index, "missing_fields": missing})
            continue
        missing_values = _required_fields(item, ["page", "role", "title", "caption", "layout", "style", "output_path"])
        if missing_values:
            _add_issue(issues, "invalid_image_card", artifact, "Image card is missing required values.", {"index": index, "missing_fields": missing_values})
            continue
        output_path = _resolved_project_path(project_dir, item.get("output_path"))
        if output_path is None or not _is_standard_project_card(project_dir, output_path, platform):
            _add_issue(issues, "image_card_png_missing", artifact, "Image card output_path must point to a project PNG.", {"index": index, "output_path": item.get("output_path")})
        card_time = _as_float(item.get("source_frame_time"))
        card_path = item.get("source_frame_path")
        if card_path and not _project_path_exists(project_dir, card_path, keyframe_paths):
            _add_anchor_issue(issues, artifact, "cards", index, "Image card source path does not match an extracted keyframe.", {"path": card_path})
        if card_time is not None and not _time_near_keyframe(card_time, keyframe_times):
            _add_anchor_issue(issues, artifact, "cards", index, "Image card source time does not match an extracted keyframe.", {"time": card_time})
    return payload


def _verify_article_markdown(
    project_dir: Path,
    path: Path,
    platform: str,
    content_assets: Dict[str, Any],
    source_texts: List[str],
    text_only: bool,
    issues: List[Dict[str, Any]],
) -> None:
    if not path.exists():
        return
    artifact = f"{platform}_post_md"
    markdown = path.read_text(encoding="utf-8", errors="ignore")
    sections = _markdown_sections(markdown)
    required_sections = ["视频信息", "一句话总结", PLATFORM_MARKDOWN_HEADINGS[platform], "封面文案", "开头", "正文", "标签", "素材路径", "来源时间点"]
    for heading in required_sections:
        if heading not in sections:
            _add_issue(issues, "markdown_missing_section", artifact, "Platform Markdown is missing a required section.", {"section": heading})
        elif not _markdown_content_is_useful(sections[heading]):
            _add_issue(issues, "markdown_empty_section", artifact, "Platform Markdown section must contain content.", {"section": heading})
    material_section = sections.get("素材路径", "")
    material_paths = ["source/metadata.json", "transcript/transcript.json", "analysis/keyframes.json", "analysis/visual-analysis.json"]
    if not text_only:
        material_paths.append("frames")
    missing_material_paths = [item for item in material_paths if item not in material_section]
    if missing_material_paths:
        _add_issue(issues, "markdown_missing_material_paths", artifact, "Platform Markdown is missing traceable material paths.", {"missing_paths": missing_material_paths})
    generated_sections = ["一句话总结", PLATFORM_MARKDOWN_HEADINGS[platform], "封面文案", "开头", "正文", "标签"]
    _verify_no_verbatim_copy(
        issues=issues,
        artifact=artifact,
        source_texts=source_texts,
        generated_texts=[(f"markdown.{section}", _clean_text(sections.get(section))) for section in generated_sections if sections.get(section)],
    )
    source_values = []
    for item in content_assets.get("source_evidence") or []:
        if isinstance(item, dict):
            source_values.extend([item.get("claim"), item.get("source_text"), item.get("source_path")])
    source_values.extend(_markdown_time_values([item for item in content_assets.get("source_evidence") or [] if isinstance(item, dict)]))
    if source_values and not _markdown_contains_any(sections.get("来源时间点", ""), source_values):
        _add_issue(issues, "markdown_missing_source_evidence", artifact, "Platform Markdown is missing source evidence.")


def _verify_article_package(
    project_dir: Path,
    package: Dict[str, Any],
    platform: str,
    text_only: bool,
    require_images: bool,
    keyframes_payload: Dict[str, Any],
    issues: List[Dict[str, Any]],
) -> None:
    artifact = "asset_package"
    post_key = PLATFORM_POST_PACKAGE_KEYS[platform]
    quality_key = PLATFORM_QUALITY_PACKAGE_KEYS[platform]
    required_fields = ["metadata", "transcript", "keyframes", "visual_analysis", "content_assets", post_key, quality_key, "materials", "compliance"]
    missing = _required_fields(package, required_fields)
    if missing:
        _add_issue(issues, "asset_package_missing_fields", artifact, "asset-package.json is missing completed article fields.", {"missing_fields": missing})
    post = package.get(post_key)
    if isinstance(post, dict) and post.get("platform") not in (None, platform):
        _add_issue(issues, "asset_package_platform_mismatch", artifact, "Packaged platform post does not match project platform.", {"expected": platform, "actual": post.get("platform")})
    quality = package.get(quality_key)
    if isinstance(quality, dict) and quality.get("platform") not in (None, platform):
        _add_issue(issues, "asset_package_platform_mismatch", artifact, "Packaged quality report does not match project platform.", {"expected": platform, "actual": quality.get("platform")})
    materials = package.get("materials")
    if not isinstance(materials, dict):
        _add_issue(issues, "asset_package_materials_invalid", artifact, "asset-package.json materials must be an object.")
        return
    frame_paths = materials.get("frame_paths")
    if not isinstance(frame_paths, list):
        _add_issue(issues, "asset_package_materials_invalid", artifact, "materials.frame_paths must be a list.")
    elif text_only and frame_paths:
        _add_issue(issues, "text_only_visual_artifacts_present", artifact, "Text-only package must not register frame paths.", {"frame_paths": frame_paths[:5]})
    elif not text_only:
        allowed = _keyframe_paths(project_dir, keyframes_payload)
        invalid = [item for item in frame_paths if not _project_path_exists(project_dir, item, allowed)]
        if invalid:
            _add_issue(issues, "asset_package_materials_invalid", artifact, "materials.frame_paths must match extracted keyframes.", {"invalid_paths": invalid[:5]})
    card_paths_key = "toutiao_card_paths" if platform == "toutiao" else "card_paths"
    card_paths = materials.get(card_paths_key)
    if not isinstance(card_paths, list):
        _add_issue(
            issues,
            "asset_package_materials_invalid",
            artifact,
            f"materials.{card_paths_key} must be a list.",
        )
    elif text_only and card_paths:
        _add_issue(issues, "text_only_visual_artifacts_present", artifact, "Text-only package must not register card paths.", {"card_paths": card_paths[:5]})
    elif require_images and not card_paths:
        _add_issue(issues, "asset_package_materials_invalid", artifact, "Completed image workflow must register card paths.")


def _verify_article_outputs(
    project_dir: Path,
    record: Dict[str, Any],
    platform: str,
    text_only: bool,
    require_images: bool,
    transcript_only: bool,
    transcript_payload: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    issues: List[Dict[str, Any]],
) -> None:
    content_assets = _read_json_or_issue(project_dir / FILE_KIND_TO_PATH["content_assets"], "content_assets", issues)
    source_texts = _verify_article_content_assets(project_dir, content_assets, transcript_payload, keyframes_payload, issues)
    post_kind = PLATFORM_POST_KINDS[platform]
    post_artifact = post_kind
    post = _read_json_or_issue(project_dir / FILE_KIND_TO_PATH[post_kind], post_artifact, issues)
    missing_post_fields = _required_fields(post, _article_post_fields(post))
    if missing_post_fields:
        _add_issue(issues, "platform_post_missing_fields", post_artifact, "Platform post is missing required fields.", {"missing_fields": missing_post_fields})
    if post.get("platform") not in (None, platform):
        _add_issue(issues, "platform_mismatch", post_artifact, "Generated post platform does not match project platform.", {"expected": platform, "actual": post.get("platform")})
    if post.get("platform_name") not in (None, PLATFORM_NAMES[platform]):
        _add_issue(issues, "platform_mismatch", post_artifact, "Generated post platform name does not match project platform.", {"expected": PLATFORM_NAMES[platform], "actual": post.get("platform_name")})
    if not isinstance(post.get("titles"), list) or len(post.get("titles", [])) < 5:
        _add_issue(issues, "platform_post_too_few_titles", post_artifact, "Platform post must contain at least five titles.")
    minimum_body_chars, maximum_body_chars = PLATFORM_BODY_LIMITS[platform]
    body_chars = _article_body_length(post.get("body"))
    if body_chars < minimum_body_chars:
        _add_issue(
            issues,
            "platform_post_body_too_short",
            post_artifact,
            "Completed platform article body is shorter than the required minimum.",
            {"actual_chars": body_chars, "minimum_chars": minimum_body_chars, "maximum_chars": maximum_body_chars},
        )
    elif body_chars > maximum_body_chars:
        _add_issue(
            issues,
            "platform_post_body_too_long",
            post_artifact,
            "Completed platform article body exceeds the allowed maximum.",
            {"actual_chars": body_chars, "minimum_chars": minimum_body_chars, "maximum_chars": maximum_body_chars},
        )
    image_plan = post.get("image_plan")
    if not isinstance(image_plan, list) or not image_plan:
        _add_issue(issues, "platform_post_empty_image_plan", post_artifact, "Platform post must contain image_plan.")
    keyframe_times = _keyframe_times(project_dir, keyframes_payload)
    allowed_keyframe_paths = _keyframe_paths(project_dir, keyframes_payload)
    for index, item in enumerate(image_plan or []):
        if not isinstance(item, dict):
            _add_issue(issues, "invalid_image_plan", post_artifact, "image_plan item must be an object.", {"index": index})
            continue
        missing_item = _required_fields(item, ["page", "role", "caption", "content_point"])
        if missing_item:
            _add_issue(issues, "invalid_image_plan", post_artifact, "image_plan item is missing required fields.", {"index": index, "missing_fields": missing_item})
            continue
        item_time = _as_float(item.get("source_frame_time"))
        item_path = item.get("source_frame_path")
        if text_only:
            if item_time is not None or item_path not in (None, ""):
                _add_issue(issues, "text_only_visual_artifacts_present", post_artifact, "Text-only image_plan items must not reference frames.", {"index": index})
        elif item_time is None and not item_path and not transcript_only:
            _add_issue(issues, "invalid_image_plan_source", post_artifact, "Image plan item must reference a keyframe.", {"index": index})
        else:
            if item_path and not _project_path_exists(project_dir, item_path, allowed_keyframe_paths):
                _add_anchor_issue(issues, post_artifact, "image_plan", index, "Image plan path does not match an extracted keyframe.", {"path": item_path})
            if item_time is not None and not _time_near_keyframe(item_time, keyframe_times):
                _add_anchor_issue(issues, post_artifact, "image_plan", index, "Image plan time does not match an extracted keyframe.", {"time": item_time})
    _verify_no_verbatim_copy(issues=issues, artifact=post_artifact, source_texts=source_texts, generated_texts=_xhs_generated_texts(post))

    quality_path = project_dir / FILE_KIND_TO_PATH[f"{platform}_quality_report"]
    quality = _read_json_or_issue(quality_path, f"{platform}_quality_report", issues)
    if quality.get("platform") not in (None, platform):
        _add_issue(issues, "quality_platform_mismatch", f"{platform}_quality_report", "Quality report platform does not match project platform.", {"expected": platform, "actual": quality.get("platform")})
    if quality.get("passed") is not True:
        _add_issue(issues, "quality_report_failed", f"{platform}_quality_report", "Completed article quality report is not passed.")
    if quality.get("violations"):
        _add_issue(issues, "quality_report_has_violations", f"{platform}_quality_report", "Completed article quality report contains violations.", {"count": len(quality.get("violations")) if isinstance(quality.get("violations"), list) else None})
    similarity = quality.get("similarity")
    if isinstance(similarity, dict) and float(similarity.get("estimated_rewrite_degree", 0) or 0) < 0.70:
        _add_issue(issues, "rewrite_degree_below_target", f"{platform}_quality_report", "Completed article rewrite degree is below 70%.", {"estimated_rewrite_degree": similarity.get("estimated_rewrite_degree")})

    markdown_path = project_dir / FILE_KIND_TO_PATH[f"{platform}_post_md"]
    _verify_article_markdown(project_dir, markdown_path, platform, content_assets, source_texts, text_only, issues)
    docx_path = project_dir / FILE_KIND_TO_PATH[f"{platform}_post_docx"]
    if not zipfile.is_zipfile(docx_path):
        _add_issue(issues, "invalid_docx", f"{platform}_post_docx", "Article DOCX is not a valid ZIP-based Word document.")
    else:
        try:
            with zipfile.ZipFile(docx_path) as archive:
                names = set(archive.namelist())
                required_parts = {"[Content_Types].xml", "word/document.xml", "word/_rels/document.xml.rels"}
                missing_parts = sorted(required_parts - names)
                if missing_parts:
                    _add_issue(issues, "invalid_docx", f"{platform}_post_docx", "Article DOCX is missing required Word parts.", {"missing_parts": missing_parts})
                elif not archive.read("word/document.xml").strip():
                    _add_issue(issues, "invalid_docx", f"{platform}_post_docx", "Article DOCX document body is empty.")
        except (OSError, zipfile.BadZipFile, KeyError) as exc:
            _add_issue(issues, "invalid_docx", f"{platform}_post_docx", "Article DOCX could not be read.", {"error": str(exc)})

    package = _read_json_or_issue(project_dir / FILE_KIND_TO_PATH["asset_package"], "asset_package", issues)
    _verify_article_package(project_dir, package, platform, text_only, require_images, keyframes_payload, issues)
    if not text_only and platform in {"xhs", "toutiao"}:
        prompt_kind = "image_prompts" if platform == "xhs" else "toutiao_image_prompts"
        _verify_article_image_prompts(
            project_dir,
            project_dir / FILE_KIND_TO_PATH[prompt_kind],
            keyframes_payload,
            issues,
            artifact=prompt_kind,
            allow_missing_frame_source=transcript_only,
        )
        if require_images:
            card_kind = "image_cards" if platform == "xhs" else "toutiao_image_cards"
            _verify_article_image_cards(
                project_dir,
                project_dir / FILE_KIND_TO_PATH[card_kind],
                keyframes_payload,
                issues,
                artifact=card_kind,
                platform=platform,
            )


def verify_project(project_dir: Path) -> Dict[str, Any]:
    project_dir = project_dir.resolve()
    status_file = project_dir / "project.json"
    issues: List[Dict[str, Any]] = []
    missing: List[str] = []
    if status_file.exists():
        record = _read_json_or_issue(status_file, "project", issues)
    else:
        record = {}
        missing.append("project_record")
    status = record.get("status", "unknown")
    platform = _platform_for_record(record, status)
    text_only = bool(record.get("text_only"))
    early_keyframes_path = project_dir / "analysis/keyframes.json"
    early_keyframes = (
        _read_json_or_issue(early_keyframes_path, "keyframes", issues)
        if early_keyframes_path.exists()
        else {}
    )
    transcript_only = early_keyframes.get("analysis_mode") == "transcript_only"
    visual_optional = text_only or transcript_only
    article_completed = status in ARTICLE_COMPLETED_STATUSES or status == "completed"
    legacy_full_xhs = status == "completed" and platform == "xhs"
    # `completed` is retained for the legacy full XHS image-card workflow.
    # Platform article statuses mean the article/export package is complete;
    # image cards are a separate optional step for image-capable platforms.
    require_images = status == "completed"
    checks: Dict[str, Dict[str, Any]] = {}
    required = (
        _article_required_outputs(platform, text_only=text_only, require_images=require_images)
        if article_completed and not legacy_full_xhs
        else REQUIRED_COMPLETED
        if legacy_full_xhs
        else REQUIRED_UPSTREAM
    )

    for name, relative in required.items():
        path = project_dir / relative
        exists = path.exists()
        checks[name] = {"path": str(path), "exists": exists}
        if not exists:
            missing.append(name)

    outputs = record.get("outputs") or {}
    if outputs and not isinstance(outputs, dict):
        _add_issue(issues, "invalid_outputs_shape", "project", "project.json outputs must be an object.")
        outputs = {}
    for kind, value in outputs.items():
        expected_relative = FILE_KIND_TO_PATH.get(kind)
        if expected_relative is None:
            _add_issue(issues, "unknown_output_kind", "project", "project.json outputs contains an unknown kind.", {"kind": kind})
            continue
        output_path = _relative_output_path(project_dir, value)
        if output_path is None:
            _add_issue(
                issues,
                "invalid_output_path",
                "project",
                "project.json output path must be a project-relative file path.",
                {"kind": kind, "path": value},
            )
            continue
        expected_path = (project_dir / expected_relative).resolve()
        if output_path != expected_path:
            _add_issue(
                issues,
                "output_path_mismatch",
                "project",
                "project.json output path does not match the standard artifact path.",
                {"kind": kind, "path": value, "expected": expected_relative},
            )
            continue
        if not output_path.exists():
            _add_issue(
                issues,
                "output_file_missing",
                "project",
                "project.json output points to a missing file.",
                {"kind": kind, "path": value},
            )
    for kind, relative in FILE_KIND_TO_PATH.items():
        if (project_dir / relative).exists() and kind not in outputs:
            _add_issue(
                issues,
                "output_not_registered",
                "project",
                "Existing standard artifact is not registered in project.json outputs.",
                {"kind": kind, "path": relative},
            )

    frames = sorted(path for path in (project_dir / "frames").glob("frame_*.jpg") if _is_standard_project_frame(project_dir, path))
    checks["frames"] = {
        "path": str(project_dir / "frames"),
        "exists": bool(frames),
        "count": len(frames),
    }
    if not frames and not visual_optional:
        missing.append("frames")

    transcript_segments = None
    transcript_path = project_dir / "transcript/transcript.json"
    transcript_payload: Dict[str, Any] = {}
    if transcript_path.exists():
        transcript_payload = _read_json_or_issue(transcript_path, "transcript", issues)
        segments = transcript_payload.get("segments")
        transcript_segments = transcript_payload.get("segment_count")
        if not isinstance(segments, list) or not segments:
            _add_issue(issues, "empty_transcript", "transcript", "transcript.json must contain non-empty segments.")
        elif transcript_segments != len(segments):
            _add_issue(
                issues,
                "transcript_count_mismatch",
                "transcript",
                "segment_count must match the number of segments.",
                {"segment_count": transcript_segments, "segments": len(segments)},
            )
        for index, segment in enumerate(segments or []):
            if not isinstance(segment, dict):
                _add_issue(issues, "invalid_transcript_segment", "transcript", "Segment must be an object.", {"index": index})
                continue
            missing_segment_fields = _required_fields(segment, ["start", "end", "text", "source"])
            if missing_segment_fields:
                _add_issue(
                    issues,
                    "invalid_transcript_segment",
                    "transcript",
                    "Transcript segment is missing required fields.",
                    {"index": index, "missing_fields": missing_segment_fields},
                )
                break

    keyframe_count = None
    keyframes_payload: Dict[str, Any] = {}
    keyframes_path = project_dir / "analysis/keyframes.json"
    if keyframes_path.exists():
        keyframes_payload = _read_json_or_issue(keyframes_path, "keyframes", issues)
        keyframe_count = keyframes_payload.get("frame_count")
        keyframes = keyframes_payload.get("keyframes")
        keyframes_is_valid_list = isinstance(keyframes, list)
        if not keyframes_is_valid_list or (not keyframes and not visual_optional):
            _add_issue(issues, "empty_keyframes", "keyframes", "keyframes.json must contain non-empty keyframes.")
        if text_only:
            if keyframes_payload.get("analysis_mode") != "text_only" or keyframes_payload.get("skipped") is not True:
                _add_issue(
                    issues,
                    "text_only_keyframes_not_marked_skipped",
                    "keyframes",
                    "Text-only keyframes must explicitly record that visual extraction was skipped.",
                )
        elif transcript_only:
            if keyframes_payload.get("analysis_mode") != "transcript_only" or keyframes_payload.get("skipped") is not True:
                _add_issue(
                    issues,
                    "transcript_only_keyframes_not_marked_skipped",
                    "keyframes",
                    "Transcript-only keyframes must explicitly record why visual extraction was skipped.",
                )
        elif not keyframes_is_valid_list or keyframe_count != len(keyframes):
            _add_issue(
                issues,
                "keyframe_count_mismatch",
                "keyframes",
                "frame_count must match the number of keyframes.",
                {"frame_count": keyframe_count, "keyframes": len(keyframes)},
            )
        if keyframe_count is not None and keyframe_count != len(frames) and not visual_optional:
            _add_issue(
                issues,
                "frame_file_count_mismatch",
                "frames",
                "frame_count must match the number of frame_*.jpg files.",
                {"frame_count": keyframe_count, "frame_files": len(frames)},
            )
        for index, frame in enumerate(keyframes or []):
            if not isinstance(frame, dict):
                _add_issue(issues, "invalid_keyframe", "keyframes", "Keyframe must be an object.", {"index": index})
                continue
            missing_frame_fields = _required_fields(frame, ["time", "path", "score", "reason"])
            if missing_frame_fields:
                _add_issue(
                    issues,
                    "invalid_keyframe",
                    "keyframes",
                    "Keyframe is missing required fields.",
                    {"index": index, "missing_fields": missing_frame_fields},
                )
                break
            frame_path = _resolved_project_path(project_dir, frame.get("path"))
            if frame_path is None or not _is_standard_project_frame(project_dir, frame_path):
                _add_issue(
                    issues,
                    "keyframe_image_missing",
                    "keyframes",
                    "Keyframe path must point to a standard frame image inside the project frames directory.",
                    {"index": index, "path": frame.get("path")},
                )
                break

    metadata: Dict[str, Any] = {}
    metadata_path = project_dir / "source/metadata.json"
    if metadata_path.exists():
        metadata = _read_json_or_issue(metadata_path, "metadata", issues)
        metadata_required_fields = ["video_id", "url", "title"]
        if not visual_optional:
            metadata_required_fields.extend(["author", "duration", "video_file"])
        missing_metadata = _required_fields(metadata, metadata_required_fields)
        if missing_metadata:
            _add_issue(
                issues,
                "metadata_missing_fields",
                "metadata",
                "metadata.json is missing required traceability fields.",
                {"missing_fields": missing_metadata},
            )
        has_transcript_segments = isinstance(transcript_payload.get("segments"), list) and bool(transcript_payload.get("segments"))
        if text_only and not any(metadata.get(field) for field in ["video_file", "audio_file", "subtitle_file"]) and not has_transcript_segments:
            _add_issue(
                issues,
                "text_only_source_evidence_missing",
                "metadata",
                "Text-only metadata must retain a local media/subtitle path or a transcript artifact must be available.",
            )
        for subtitle_field in ["available_subtitles", "automatic_captions"]:
            if subtitle_field not in metadata or not isinstance(metadata.get(subtitle_field), list):
                _add_issue(
                    issues,
                    "metadata_subtitle_fields_missing",
                    "metadata",
                    "metadata.json must include yt-dlp subtitle and automatic caption language lists.",
                    {"field": subtitle_field},
                )
        track_summary = metadata.get("subtitle_track_summary")
        if track_summary is not None:
            if not isinstance(track_summary, dict):
                _add_issue(
                    issues,
                    "metadata_subtitle_summary_invalid",
                    "metadata",
                    "metadata.subtitle_track_summary must be an object when present.",
                )
            else:
                for group in ["available_subtitles", "automatic_captions"]:
                    summary = track_summary.get(group)
                    languages = metadata.get(group)
                    if not isinstance(summary, dict):
                        _add_issue(
                            issues,
                            "metadata_subtitle_summary_invalid",
                            "metadata",
                            "metadata.subtitle_track_summary is missing a subtitle group.",
                            {"group": group},
                        )
                        continue
                    if isinstance(languages, list) and summary.get("count") != len(languages):
                        _add_issue(
                            issues,
                            "metadata_subtitle_summary_mismatch",
                            "metadata",
                            "metadata.subtitle_track_summary count must match the language list.",
                            {"group": group, "count": summary.get("count"), "languages": len(languages)},
                        )
                    if isinstance(languages, list) and summary.get("languages") != languages:
                        _add_issue(
                            issues,
                            "metadata_subtitle_summary_mismatch",
                            "metadata",
                            "metadata.subtitle_track_summary languages must match the language list.",
                            {"group": group},
                        )
        video_file = _path_from_payload(project_dir, metadata.get("video_file"))
        if metadata.get("video_file") and (video_file is None or not video_file.exists()):
            _add_issue(
                issues,
                "metadata_video_file_missing",
                "metadata",
                "metadata.video_file does not point to an existing file.",
                {"video_file": metadata.get("video_file")},
            )
        thumbnail_file = _path_from_payload(project_dir, metadata.get("thumbnail_file"))
        expected_thumbnail = (project_dir / "source/thumbnail.jpg").resolve()
        if metadata.get("thumbnail") and not expected_thumbnail.exists():
            _add_issue(
                issues,
                "thumbnail_file_missing",
                "metadata",
                "metadata.thumbnail is present but source/thumbnail.jpg is missing.",
                {"thumbnail": metadata.get("thumbnail"), "expected": "source/thumbnail.jpg"},
            )
        if metadata.get("thumbnail_file") and (thumbnail_file is None or not thumbnail_file.exists()):
            _add_issue(
                issues,
                "metadata_thumbnail_file_missing",
                "metadata",
                "metadata.thumbnail_file does not point to an existing file.",
                {"thumbnail_file": metadata.get("thumbnail_file")},
            )
        if thumbnail_file and thumbnail_file.exists():
            if thumbnail_file.resolve() != expected_thumbnail:
                _add_issue(
                    issues,
                    "thumbnail_standard_path_mismatch",
                    "metadata",
                    "Local thumbnail should be normalized to source/thumbnail.jpg.",
                    {"thumbnail_file": metadata.get("thumbnail_file"), "expected": "source/thumbnail.jpg"},
                )

    run_metadata_path = project_dir / "analysis/run-metadata.json"
    if run_metadata_path.exists():
        run_metadata = _read_json_or_issue(run_metadata_path, "run_metadata", issues)
        missing_run_metadata = _required_fields(run_metadata, ["status"])
        if metadata:
            run_metadata_required_fields = ["video_id", "title", "source_url"]
            if not text_only:
                run_metadata_required_fields.extend(["author", "duration"])
            missing_run_metadata.extend(_required_fields(run_metadata, run_metadata_required_fields))
        if missing_run_metadata:
            _add_issue(
                issues,
                "run_metadata_missing_fields",
                "run_metadata",
                "run-metadata.json is missing required run or source traceability fields.",
                {"missing_fields": sorted(set(missing_run_metadata))},
            )
        if metadata:
            expected_values = {
                "video_id": metadata.get("video_id"),
                "title": metadata.get("title"),
                "author": metadata.get("author"),
                "duration": metadata.get("duration"),
                "source_url": metadata.get("url"),
            }
            mismatches = {
                field: {"expected": expected, "actual": run_metadata.get(field)}
                for field, expected in expected_values.items()
                if expected not in (None, "") and run_metadata.get(field) != expected
            }
            if mismatches:
                _add_issue(
                    issues,
                    "run_metadata_source_mismatch",
                    "run_metadata",
                    "run-metadata.json source summary must match source/metadata.json.",
                    mismatches,
                )

    visual_path = project_dir / "analysis/visual-analysis.json"
    if visual_path.exists():
        visual = _read_json_or_issue(visual_path, "visual_analysis", issues)
        visual_frames = visual.get("frames")
        if not isinstance(visual_frames, list):
            _add_issue(issues, "invalid_visual_analysis", "visual_analysis", "visual-analysis.json must contain frames list.")
        if visual_optional and (visual.get("skipped") is not True or not visual.get("skip_reason")):
            _add_issue(
                issues,
                "text_only_visual_not_marked_skipped" if text_only else "transcript_only_visual_not_marked_skipped",
                "visual_analysis",
                "Skipped visual analysis must explicitly record why OCR and frame analysis were skipped.",
            )
        elif keyframe_count is not None and len(visual_frames) != keyframe_count:
            _add_issue(
                issues,
                "visual_frame_count_mismatch",
                "visual_analysis",
                "visual-analysis frame count must match keyframe_count.",
                {"visual_frames": len(visual_frames), "keyframe_count": keyframe_count},
            )
        for index, frame in enumerate(visual_frames or []):
            if not isinstance(frame, dict):
                _add_issue(issues, "invalid_visual_frame", "visual_analysis", "Visual frame must be an object.", {"index": index})
                continue
            missing_visual_fields = _missing_keys(
                frame,
                [
                    "time",
                    "path",
                    "ocr_text",
                    "visual_summary",
                    "detected_objects",
                    "screen_text_confidence",
                    "ocr_provider",
                    "frame_metrics",
                ],
            )
            if missing_visual_fields:
                _add_issue(
                    issues,
                    "invalid_visual_frame",
                    "visual_analysis",
                    "Visual frame is missing required fields.",
                    {"index": index, "missing_fields": missing_visual_fields},
                )
                break
            if not _project_path_exists(project_dir, frame.get("path"), _keyframe_paths(project_dir, keyframes_payload)):
                _add_issue(
                    issues,
                    "invalid_visual_frame",
                    "visual_analysis",
                    "Visual frame path must match an extracted keyframe image.",
                    {"index": index, "path": frame.get("path")},
                )
                break
            if not isinstance(frame.get("detected_objects"), list):
                _add_issue(
                    issues,
                    "invalid_visual_frame",
                    "visual_analysis",
                    "Visual frame detected_objects must be a list.",
                    {"index": index},
                )
                break
            metrics = frame.get("frame_metrics")
            if not isinstance(metrics, dict) or "available" not in metrics:
                _add_issue(
                    issues,
                    "invalid_visual_frame_metrics",
                    "visual_analysis",
                    "Visual frame_metrics must be an object with an available flag.",
                    {"index": index},
                )
                break
            if metrics.get("available"):
                missing_metric_fields = _required_fields(
                    metrics,
                    [
                        "width",
                        "height",
                        "brightness",
                        "sharpness",
                        "brightness_label",
                        "sharpness_label",
                        "color_tone",
                    ],
                )
                if missing_metric_fields:
                    _add_issue(
                        issues,
                        "invalid_visual_frame_metrics",
                        "visual_analysis",
                        "Available visual frame_metrics is missing required fields.",
                        {"index": index, "missing_fields": missing_metric_fields},
                    )
                    break

    if legacy_full_xhs:
        transcript_ranges = _transcript_ranges(transcript_payload)
        keyframe_times = _keyframe_times(project_dir, keyframes_payload)
        allowed_keyframe_paths = _keyframe_paths(project_dir, keyframes_payload)
        content_assets = _read_json_or_issue(project_dir / "analysis/content-assets.json", "content_assets", issues)
        missing_asset_fields = _required_fields(
            content_assets,
            [
                "one_sentence_summary",
                "core_points",
                "golden_quotes",
                "chapters",
                "steps",
                "audience",
                "pain_points",
                "xiaohongshu_angles",
                "recommended_content_type",
                "source_evidence",
            ],
        )
        if missing_asset_fields:
            _add_issue(
                issues,
                "content_assets_missing_fields",
                "content_assets",
                "content-assets.json is missing required fields.",
                {"missing_fields": missing_asset_fields},
            )
        for field in CONTENT_ASSET_LIST_FIELDS:
            if not isinstance(content_assets.get(field), list) or not content_assets.get(field):
                _add_issue(
                    issues,
                    "content_assets_empty_list",
                    "content_assets",
                    f"content-assets.json must contain non-empty list field: {field}.",
                    {"field": field},
                )
                break
        for index, point in enumerate(content_assets.get("core_points") or []):
            if not isinstance(point, dict):
                _add_issue(issues, "invalid_core_point", "content_assets", "Core point must be an object.", {"index": index})
                continue
            missing_point_fields = _required_fields(point, ["point", "evidence"])
            if missing_point_fields:
                _add_issue(
                    issues,
                    "invalid_core_point",
                    "content_assets",
                    "Core point is missing required fields.",
                    {"index": index, "missing_fields": missing_point_fields},
                )
                break
            evidence = point.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                _add_issue(
                    issues,
                    "invalid_core_point_evidence",
                    "content_assets",
                    "Core point must include at least one evidence item.",
                    {"index": index},
                )
                break
            for evidence_index, evidence_item in enumerate(evidence):
                if not isinstance(evidence_item, dict):
                    _add_issue(
                        issues,
                        "invalid_core_point_evidence",
                        "content_assets",
                        "Core point evidence must be an object.",
                        {"index": index, "evidence_index": evidence_index},
                    )
                    break
                missing_evidence_fields = _required_fields(evidence_item, ["type", "text"])
                if missing_evidence_fields:
                    _add_issue(
                        issues,
                        "invalid_core_point_evidence",
                        "content_assets",
                        "Core point evidence is missing required fields.",
                        {
                            "index": index,
                            "evidence_index": evidence_index,
                            "missing_fields": missing_evidence_fields,
                        },
                    )
                    break
                if not _has_evidence_anchor(evidence_item):
                    _add_issue(
                        issues,
                        "core_point_evidence_missing_anchor",
                        "content_assets",
                        "Core point evidence must include a source time or frame path.",
                        {"index": index, "evidence_index": evidence_index},
                    )
                    break
                _validate_source_anchor(
                    project_dir,
                    evidence_item,
                    source_type=str(evidence_item.get("type") or ""),
                    transcript_ranges=transcript_ranges,
                    keyframe_times=keyframe_times,
                    keyframe_paths=allowed_keyframe_paths,
                    issues=issues,
                    artifact="content_assets",
                    field="core_points.evidence",
                    index=evidence_index,
                )
        for field, item_fields in [
            ("golden_quotes", ["quote", "rewrite_note"]),
            ("chapters", ["title", "summary"]),
            ("steps", ["step"]),
            ("source_evidence", ["claim", "source_type", "source_text"]),
        ]:
            for index, item in enumerate(content_assets.get(field) or []):
                if not isinstance(item, dict):
                    _add_issue(issues, "invalid_content_asset_item", "content_assets", "Content asset item must be an object.", {"field": field, "index": index})
                    break
                missing_item_fields = _required_fields(item, item_fields)
                if missing_item_fields:
                    _add_issue(
                        issues,
                        "invalid_content_asset_item",
                        "content_assets",
                        "Content asset item is missing required fields.",
                        {"field": field, "index": index, "missing_fields": missing_item_fields},
                    )
                    break
                if field == "source_evidence" and item.get("time") in (None, "") and item.get("source_path") in (None, ""):
                    _add_issue(
                        issues,
                        "source_evidence_missing_anchor",
                        "content_assets",
                        "Source evidence must include a source time or source path.",
                        {"field": field, "index": index},
                    )
                    break
                if field == "source_evidence":
                    _validate_source_anchor(
                        project_dir,
                        item,
                        source_type=str(item.get("source_type") or ""),
                        transcript_ranges=transcript_ranges,
                        keyframe_times=keyframe_times,
                        keyframe_paths=allowed_keyframe_paths,
                        issues=issues,
                        artifact="content_assets",
                        field="source_evidence",
                        index=index,
                    )
        source_texts = _verbatim_source_texts(transcript_payload, content_assets)
        _verify_no_verbatim_copy(
            issues=issues,
            artifact="content_assets",
            source_texts=source_texts,
            generated_texts=_content_asset_generated_texts(content_assets),
        )

        xhs_post = _read_json_or_issue(project_dir / "analysis/xiaohongshu-post.json", "xhs_post_json", issues)
        missing_post_fields = _required_fields(xhs_post, XHS_POST_FIELDS)
        if missing_post_fields:
            _add_issue(
                issues,
                "xhs_post_missing_fields",
                "xhs_post_json",
                "xiaohongshu-post.json is missing required fields.",
                {"missing_fields": missing_post_fields},
            )
        if not isinstance(xhs_post.get("titles"), list) or len(xhs_post.get("titles", [])) < 5:
            _add_issue(issues, "xhs_post_too_few_titles", "xhs_post_json", "xiaohongshu-post.json must contain at least 5 titles.")
        if not isinstance(xhs_post.get("image_plan"), list) or not xhs_post.get("image_plan"):
            _add_issue(issues, "xhs_post_empty_image_plan", "xhs_post_json", "xiaohongshu-post.json must contain image_plan.")
        _verify_no_verbatim_copy(
            issues=issues,
            artifact="xhs_post_json",
            source_texts=source_texts,
            generated_texts=_xhs_generated_texts(xhs_post),
        )
        for index, item in enumerate(xhs_post.get("image_plan") or []):
            if not isinstance(item, dict):
                _add_issue(issues, "invalid_image_plan", "xhs_post_json", "Image plan item must be an object.", {"index": index})
                continue
            missing_plan_fields = _required_fields(item, ["page", "role", "caption", "content_point"])
            if missing_plan_fields:
                _add_issue(
                    issues,
                    "invalid_image_plan",
                    "xhs_post_json",
                    "Image plan item is missing required fields.",
                    {"index": index, "missing_fields": missing_plan_fields},
                )
                break
            if item.get("source_frame_time") in (None, "") and item.get("source_frame_path") in (None, ""):
                _add_issue(
                    issues,
                    "invalid_image_plan_source",
                    "xhs_post_json",
                    "Image plan item must reference a source frame time or source frame path.",
                    {"index": index},
                )
                break
            source_frame_time = _as_float(item.get("source_frame_time"))
            source_frame_path = item.get("source_frame_path")
            if source_frame_path and not _project_path_exists(project_dir, source_frame_path, allowed_keyframe_paths):
                _add_anchor_issue(
                    issues,
                    "xhs_post_json",
                    "image_plan",
                    index,
                    "Image plan source frame path does not match an extracted keyframe.",
                    {"path": source_frame_path},
                )
                break
            if source_frame_time is not None and not _time_near_keyframe(source_frame_time, keyframe_times):
                _add_anchor_issue(
                    issues,
                    "xhs_post_json",
                    "image_plan",
                    index,
                    "Image plan source frame time does not match an extracted keyframe.",
                    {"time": source_frame_time},
                )
                break

        image_prompts = _read_json_or_issue(project_dir / "analysis/image-prompts.json", "image_prompts", issues)
        prompt_items = image_prompts.get("image_prompts")
        if not isinstance(prompt_items, list) or not prompt_items:
            _add_issue(issues, "image_prompts_empty", "image_prompts", "image-prompts.json must contain image_prompts.")
        for index, item in enumerate(prompt_items or []):
            if not isinstance(item, dict):
                _add_issue(issues, "invalid_image_prompt", "image_prompts", "Image prompt must be an object.", {"index": index})
                continue
            missing_prompt_fields = _missing_keys(item, IMAGE_PROMPT_FIELDS)
            if missing_prompt_fields:
                _add_issue(
                    issues,
                    "invalid_image_prompt",
                    "image_prompts",
                    "Image prompt is missing required fields.",
                    {"index": index, "missing_fields": missing_prompt_fields},
                )
                break
            missing_prompt_values = _required_fields(item, IMAGE_PROMPT_VALUE_FIELDS)
            if missing_prompt_values:
                _add_issue(
                    issues,
                    "invalid_image_prompt",
                    "image_prompts",
                    "Image prompt is missing required non-empty values.",
                    {"index": index, "missing_fields": missing_prompt_values},
                )
                break
            image_prompt = str(item.get("image_prompt") or "")
            missing_visual_keywords = [
                keyword for keyword in IMAGE_PROMPT_REQUIRED_KEYWORDS if keyword not in image_prompt
            ]
            if missing_visual_keywords:
                _add_issue(
                    issues,
                    "image_prompt_missing_visual_requirements",
                    "image_prompts",
                    "Image prompt must describe composition, subject, background, tone, and text whitespace.",
                    {"index": index, "missing_keywords": missing_visual_keywords},
                )
                break
            forbidden_copy_terms = [term for term in IMAGE_PROMPT_FORBIDDEN_COPY_TERMS if term in image_prompt]
            if forbidden_copy_terms:
                _add_issue(
                    issues,
                    "image_prompt_requests_screenshot_copy",
                    "image_prompts",
                    "Image prompt must transform source frames into original visuals, not request screenshot recreation.",
                    {"index": index, "forbidden_terms": forbidden_copy_terms},
                )
                break
            negative_prompt = str(item.get("negative_prompt") or "")
            if not any(term in negative_prompt for term in NEGATIVE_PROMPT_COPY_TERMS):
                _add_issue(
                    issues,
                    "image_prompt_missing_copy_guard",
                    "image_prompts",
                    "Negative prompt must explicitly avoid recreating source screenshots.",
                    {"index": index},
                )
                break
            prompt_frame_time = _as_float(item.get("source_frame_time"))
            prompt_frame_path = item.get("source_frame_path")
            if prompt_frame_path and not _project_path_exists(project_dir, prompt_frame_path, allowed_keyframe_paths):
                _add_anchor_issue(
                    issues,
                    "image_prompts",
                    "image_prompts",
                    index,
                    "Image prompt source frame path does not match an extracted keyframe.",
                    {"path": prompt_frame_path},
                )
                break
            if prompt_frame_time is not None and not _time_near_keyframe(prompt_frame_time, keyframe_times):
                _add_anchor_issue(
                    issues,
                    "image_prompts",
                    "image_prompts",
                    index,
                    "Image prompt source frame time does not match an extracted keyframe.",
                    {"time": prompt_frame_time},
                )
                break
            if prompt_frame_time is None and not prompt_frame_path:
                _add_anchor_issue(
                    issues,
                    "image_prompts",
                    "image_prompts",
                    index,
                    "Image prompt source must reference an extracted keyframe time or path.",
                    {"time": item.get("source_frame_time"), "path": prompt_frame_path},
                )
                break

        image_cards = _read_json_or_issue(project_dir / "analysis/image-cards.json", "image_cards", issues)
        card_items = image_cards.get("cards")
        if not isinstance(card_items, list) or not card_items:
            _add_issue(issues, "image_cards_empty", "image_cards", "image-cards.json must contain cards.")
        for index, item in enumerate(card_items or []):
            if not isinstance(item, dict):
                _add_issue(issues, "invalid_image_card", "image_cards", "Image card must be an object.", {"index": index})
                continue
            missing_card_fields = _missing_keys(item, IMAGE_CARD_FIELDS)
            if missing_card_fields:
                _add_issue(
                    issues,
                    "invalid_image_card",
                    "image_cards",
                    "Image card is missing required fields.",
                    {"index": index, "missing_fields": missing_card_fields},
                )
                break
            missing_card_values = _required_fields(item, ["page", "role", "title", "caption", "layout", "style", "output_path"])
            if missing_card_values:
                _add_issue(
                    issues,
                    "invalid_image_card",
                    "image_cards",
                    "Image card is missing required non-empty values.",
                    {"index": index, "missing_fields": missing_card_values},
                )
                break
            output_path = _resolved_project_path(project_dir, item.get("output_path"))
            if output_path is None or not _is_standard_project_card(project_dir, output_path):
                _add_issue(
                    issues,
                    "image_card_png_missing",
                    "image_cards",
                    "Image card output_path must point to a PNG inside the project cards directory.",
                    {"index": index, "output_path": item.get("output_path")},
                )
                break
            card_frame_time = _as_float(item.get("source_frame_time"))
            card_frame_path = item.get("source_frame_path")
            if card_frame_path and not _project_path_exists(project_dir, card_frame_path, allowed_keyframe_paths):
                _add_anchor_issue(
                    issues,
                    "image_cards",
                    "cards",
                    index,
                    "Image card source frame path does not match an extracted keyframe.",
                    {"path": card_frame_path},
                )
                break
            if card_frame_time is not None and not _time_near_keyframe(card_frame_time, keyframe_times):
                _add_anchor_issue(
                    issues,
                    "image_cards",
                    "cards",
                    index,
                    "Image card source frame time does not match an extracted keyframe.",
                    {"time": card_frame_time},
                )
                break

        markdown_path = project_dir / "analysis/xhs-post.md"
        if markdown_path.exists():
            markdown = markdown_path.read_text(encoding="utf-8", errors="ignore")
            markdown_sections = _markdown_sections(markdown)
            for heading in MARKDOWN_REQUIRED_SECTIONS:
                if heading not in markdown_sections:
                    _add_issue(
                        issues,
                        "markdown_missing_section",
                        "xhs_post_md",
                        "xhs-post.md is missing a required section.",
                        {"section": heading},
                    )
                    break
                if not _markdown_content_is_useful(markdown_sections[heading]):
                    _add_issue(
                        issues,
                        "markdown_empty_section",
                        "xhs_post_md",
                        "xhs-post.md section must contain non-empty generated content.",
                        {"section": heading},
                    )
                    break
            material_section = markdown_sections.get("素材路径", "")
            missing_material_paths = [path for path in MARKDOWN_REQUIRED_MATERIAL_PATHS if path not in material_section]
            if missing_material_paths:
                _add_issue(
                    issues,
                    "markdown_missing_material_paths",
                    "xhs_post_md",
                    "xhs-post.md must include traceable output material paths.",
                    {"missing_paths": missing_material_paths},
                )
            prompt_values = []
            for prompt in prompt_items or []:
                if isinstance(prompt, dict):
                    prompt_values.extend([prompt.get("image_prompt"), prompt.get("negative_prompt"), prompt.get("caption")])
            prompt_section = markdown_sections.get("图片提示词", "")
            if prompt_values and not _markdown_contains_any(prompt_section, prompt_values, min_length=6):
                _add_issue(
                    issues,
                    "markdown_missing_image_prompt_content",
                    "xhs_post_md",
                    "xhs-post.md must include generated image prompt content.",
                )
            card_values = []
            for item in card_items or []:
                if isinstance(item, dict):
                    card_values.extend([item.get("title"), item.get("caption"), item.get("output_path")])
            card_section = markdown_sections.get("图文卡片", "")
            if card_values and not _markdown_contains_any(card_section, card_values, min_length=4):
                _add_issue(
                    issues,
                    "markdown_missing_image_card_content",
                    "xhs_post_md",
                    "xhs-post.md must include generated image card content and output paths.",
                )
            image_plan_values = []
            for item in xhs_post.get("image_plan") or []:
                if isinstance(item, dict):
                    image_plan_values.extend([item.get("caption"), item.get("content_point")])
            image_plan_section = markdown_sections.get("配图规划", "")
            if image_plan_values and not _markdown_contains_any(image_plan_section, image_plan_values):
                _add_issue(
                    issues,
                    "markdown_missing_image_plan_content",
                    "xhs_post_md",
                    "xhs-post.md must include generated image plan content.",
                )
            source_evidence = [item for item in content_assets.get("source_evidence") or [] if isinstance(item, dict)]
            source_values = []
            for item in source_evidence:
                source_values.extend([item.get("claim"), item.get("source_text"), item.get("source_path")])
            source_values.extend(_markdown_time_values(source_evidence))
            source_section = markdown_sections.get("来源时间点", "")
            if source_values and not _markdown_contains_any(source_section, source_values):
                _add_issue(
                    issues,
                    "markdown_missing_source_evidence",
                    "xhs_post_md",
                    "xhs-post.md must include source evidence claims, source text, paths, or times.",
                )
            _verify_no_verbatim_copy(
                issues=issues,
                artifact="xhs_post_md",
                source_texts=source_texts,
                generated_texts=_markdown_generated_texts(markdown_sections),
            )

        asset_package = _read_json_or_issue(project_dir / "analysis/asset-package.json", "asset_package", issues)
        missing_package_fields = _required_fields(
            asset_package,
            [
                "metadata",
                "transcript",
                "keyframes",
                "visual_analysis",
                "content_assets",
                "xiaohongshu_post",
                "image_prompts",
                "image_cards",
                "materials",
                "compliance",
            ],
        )
        if missing_package_fields:
            _add_issue(
                issues,
                "asset_package_missing_fields",
                "asset_package",
                "asset-package.json is missing required completed package fields.",
                {"missing_fields": missing_package_fields},
            )
        if asset_package.get("status") == "partial_failed":
            _add_issue(issues, "completed_package_marked_partial", "asset_package", "Completed project cannot use a partial_failed asset package.")
        materials = asset_package.get("materials")
        if not isinstance(materials, dict):
            _add_issue(issues, "asset_package_materials_invalid", "asset_package", "asset-package.json materials must be an object.")
        else:
            frames_dir = materials.get("frames_dir")
            frames_dir_path = _resolved_project_path(project_dir, frames_dir)
            expected_frames_dir = (project_dir / "frames").resolve()
            if frames_dir_path is None or not frames_dir_path.exists() or not frames_dir_path.is_dir() or frames_dir_path != expected_frames_dir:
                _add_issue(
                    issues,
                    "asset_package_materials_invalid",
                    "asset_package",
                    "asset-package.json materials.frames_dir must point to the project frames directory.",
                    {"frames_dir": frames_dir},
                )
            frame_paths = materials.get("frame_paths")
            if not isinstance(frame_paths, list) or not frame_paths:
                _add_issue(
                    issues,
                    "asset_package_materials_invalid",
                    "asset_package",
                    "asset-package.json materials.frame_paths must list extracted frame image paths.",
                )
            else:
                material_frame_paths = {
                    resolved
                    for frame_path in frame_paths
                    if (resolved := _resolved_project_path(project_dir, frame_path)) is not None and resolved.exists()
                }
                invalid_material_frames = [frame_path for frame_path in frame_paths if not _project_path_exists(project_dir, frame_path, allowed_keyframe_paths)]
                if invalid_material_frames:
                    _add_issue(
                        issues,
                        "asset_package_materials_invalid",
                        "asset_package",
                        "asset-package.json materials.frame_paths must match extracted keyframe images.",
                        {"invalid_paths": invalid_material_frames[:5]},
                    )
                elif material_frame_paths != allowed_keyframe_paths:
                    _add_issue(
                        issues,
                        "asset_package_materials_invalid",
                        "asset_package",
                        "asset-package.json materials.frame_paths must include every extracted keyframe image exactly once.",
                        {
                            "expected_count": len(allowed_keyframe_paths),
                            "actual_count": len(material_frame_paths),
                        },
                    )
            cards_dir = materials.get("cards_dir")
            cards_dir_path = _resolved_project_path(project_dir, cards_dir)
            expected_cards_dir = (project_dir / "cards").resolve()
            if cards_dir_path is None or not cards_dir_path.exists() or not cards_dir_path.is_dir() or cards_dir_path != expected_cards_dir:
                _add_issue(
                    issues,
                    "asset_package_materials_invalid",
                    "asset_package",
                    "asset-package.json materials.cards_dir must point to the project cards directory.",
                    {"cards_dir": cards_dir},
                )
            card_paths = materials.get("card_paths")
            allowed_card_paths = _card_paths(project_dir, image_cards)
            if not isinstance(card_paths, list) or not card_paths:
                _add_issue(
                    issues,
                    "asset_package_materials_invalid",
                    "asset_package",
                    "asset-package.json materials.card_paths must list rendered image card PNG paths.",
                )
            else:
                material_card_paths = {
                    resolved
                    for card_path in card_paths
                    if (resolved := _resolved_project_path(project_dir, card_path)) is not None and resolved.exists()
                }
                invalid_material_cards = [card_path for card_path in card_paths if not _project_path_exists(project_dir, card_path, allowed_card_paths)]
                if invalid_material_cards:
                    _add_issue(
                        issues,
                        "asset_package_materials_invalid",
                        "asset_package",
                        "asset-package.json materials.card_paths must match rendered PNG image cards.",
                        {"invalid_paths": invalid_material_cards[:5]},
                    )
                elif material_card_paths != allowed_card_paths:
                    _add_issue(
                        issues,
                        "asset_package_materials_invalid",
                        "asset_package",
                        "asset-package.json materials.card_paths must include every rendered image card exactly once.",
                        {
                            "expected_count": len(allowed_card_paths),
                            "actual_count": len(material_card_paths),
                        },
                    )

    if article_completed:
        _verify_article_outputs(
            project_dir,
            record,
            platform,
            text_only,
            require_images=status == "completed",
            transcript_only=transcript_only,
            transcript_payload=transcript_payload,
            keyframes_payload=keyframes_payload,
            issues=issues,
        )

    if status == "failed":
        package_path = project_dir / "analysis/asset-package.json"
        if package_path.exists():
            package = _read_json_or_issue(package_path, "asset_package", issues)
            if package.get("status") != "partial_failed":
                _add_issue(issues, "partial_package_status_invalid", "asset_package", "Failed projects must write a partial_failed asset package.")
            if not package.get("error"):
                _add_issue(issues, "partial_package_missing_error", "asset_package", "Partial package must include the structured error.")

    partial_ok = status == "failed" and (project_dir / "analysis/asset-package.json").exists() and not issues
    completed_ok = (status == "completed" or article_completed) and not missing and not issues

    return {
        "project_dir": str(project_dir),
            "status": status,
        "platform": platform,
        "text_only": text_only,
        "transcript_only": transcript_only,
        "ok": completed_ok or (status != "completed" and not missing and not issues),
        "completed_ok": completed_ok,
        "partial_ok": partial_ok,
        "missing": missing,
        "issues": issues,
        "checks": checks,
        "summary": {
            "transcript_segments": transcript_segments,
            "keyframe_count": keyframe_count,
            "frame_files": len(frames),
            "available_subtitle_languages": (
                len(metadata.get("available_subtitles", [])) if isinstance(metadata.get("available_subtitles"), list) else None
            ),
            "automatic_caption_languages": (
                len(metadata.get("automatic_captions", [])) if isinstance(metadata.get("automatic_captions"), list) else None
            ),
            "error": record.get("error"),
            "warnings": record.get("warnings", []),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a runtime project output directory.")
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--require-completed", action="store_true")
    args = parser.parse_args()

    result = verify_project(args.project_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.require_completed and not result["completed_ok"]:
        return 1
    if not result["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
