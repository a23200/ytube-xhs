import json
from typing import Any, Dict, List, Optional

from app.services.contracts import validate_xhs_post
from app.services.errors import PipelineError
from app.services.llm_client import llm_client
from app.services.runtime_store import ProjectPaths, write_json
from app.services.source_anchors import validate_xhs_post_anchors
from app.services.text_utils import clean_text

MIN_VERBATIM_CHARS = 24
VISIBLE_TEXT_FIELDS = ["cover_text", "hook", "body", "publish_suggestion"]
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
    "source_disclaimer",
]
PLATFORM_PROMPTS = {
    "xhs": {
        "name": "小红书",
        "artifact": "xiaohongshu-post.json",
        "output_filename": "xiaohongshu-post.json",
        "system": (
            "你是小红书原创图文编辑。你要基于原视频提供的信息、情绪和方向进行二次创作，"
            "产出一篇能独立发布的原创文章图文，而不是视频拆解、字幕摘要或逐帧复述。不能逐字照搬字幕，"
            "不能生成侵权搬运文案，必须保留来源信息和时间点用于内部追溯。中文自然、有信息密度、适合收藏转发。"
            "返回严格 JSON。"
        ),
        "user": (
            "基于 content_assets 生成 xiaohongshu-post.json。请先消化原视频里的信息与方向，再转换成我们的原创观点、"
            "读者场景、行动建议和图文叙事。正文短段落，避免空泛鸡汤，也不要出现“本视频/这条视频/第几秒/拆解/整理”等报告感表达。"
            "图片计划要服务最终原创文章，可使用关键帧作为事实和视觉参考，但不要把卡片做成视频截图复盘。"
            "如果 keyframes 非空，image_plan 每一页必须优先填写 keyframes 中真实存在的 source_frame_path；"
            "source_frame_time 只能使用 keyframes 中已有的 time，不能编造时间点。"
            "如果 keyframes 为空，source_frame_time 和 source_frame_path 必须为 null，并用 content_point 说明文字视觉方向。"
            "返回字段必须匹配 schema，不能省略任何字段。\n\n"
        ),
    },
    "toutiao": {
        "name": "今日头条",
        "artifact": "toutiao-post.json",
        "output_filename": "toutiao-post.json",
        "system": (
            "你是今日头条原创图文编辑。你要基于原视频提供的信息、情绪和方向进行二次创作，"
            "产出一篇能独立发布的资讯型原创文章，而不是视频拆解、字幕摘要或逐帧复述。不能逐字照搬字幕，"
            "不能生成侵权搬运文案，必须保留来源信息和时间点用于内部追溯。文风应更像资讯图文：标题清楚、"
            "导语交代看点、正文有小标题和事实依据，少用营销感语气、表情符号和小红书式种草表达。返回严格 JSON。"
        ),
        "user": (
            "基于 content_assets 生成 toutiao-post.json。请先消化原视频的信息与方向，再转换成我们的原创论述、背景解释、"
            "读者关切和结论。标题要符合今日头条信息流阅读习惯，突出事实、冲突、趋势、方法或结论，但不能标题党。"
            "body 要使用导语 + 分段小标题 + 要点解释的结构，便于图文发布；不要出现“本视频/这条视频/第几秒/拆解/整理”等报告感表达。"
            "图片计划要服务最终原创文章，可使用关键帧作为事实和视觉参考，但不要把卡片做成视频截图复盘。"
            "如果 keyframes 非空，image_plan 每一页必须优先填写 keyframes 中真实存在的 source_frame_path；"
            "source_frame_time 只能使用 keyframes 中已有的 time，不能编造时间点。"
            "如果 keyframes 为空，source_frame_time 和 source_frame_path 必须为 null，并用 content_point 说明文字视觉方向。"
            "返回字段必须匹配 schema，不能省略任何字段。\n\n"
        ),
    },
}


def _as_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_text(value: Any, default: str = "") -> str:
    text = clean_text(str(value or ""))
    return text or default


def _keyframes(keyframes_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    frames = []
    for frame in keyframes_payload.get("keyframes", []) or []:
        if not isinstance(frame, dict):
            continue
        time = _as_float(frame.get("time"))
        path = str(frame.get("path") or "")
        if time is None and not path:
            continue
        frames.append({**frame, "time": time, "path": path})
    return frames


def _frame_for_index(frames: List[Dict[str, Any]], index: int) -> Dict[str, Any]:
    if not frames:
        return {"time": None, "path": None}
    return frames[min(index, len(frames) - 1)]


def _frame_for_anchor(frames: List[Dict[str, Any]], item: Dict[str, Any], index: int) -> Dict[str, Any]:
    if not frames:
        return {"time": None, "path": None}
    source_path = str(item.get("source_frame_path") or "")
    if source_path:
        for frame in frames:
            if str(frame.get("path") or "") == source_path:
                return frame
    source_time = _as_float(item.get("source_frame_time"))
    timed_frames = [frame for frame in frames if frame.get("time") is not None]
    if source_time is not None and timed_frames:
        return min(timed_frames, key=lambda frame: abs(float(frame["time"]) - source_time))
    page = _as_float(item.get("page"))
    page_index = int(page) - 1 if page is not None else index
    return _frame_for_index(frames, page_index)


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [clean_text(str(item)) for item in value if clean_text(str(item))]


def _clip(value: Any, limit: int = 180) -> str:
    text = clean_text(str(value or ""))
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _valid_image_plan(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        if any(item.get(field) in (None, "", []) for field in ["page", "role", "caption", "content_point"]):
            return False
    return True


def _normalize_post(
    payload: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    platform: str = "xhs",
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise PipelineError(
            code="llm_contract_invalid",
            message=f"LLM output for {PLATFORM_PROMPTS.get(platform, PLATFORM_PROMPTS['xhs'])['artifact']} must be a JSON object.",
            step="writing_xhs",
            details={"artifact": PLATFORM_PROMPTS.get(platform, PLATFORM_PROMPTS["xhs"])["artifact"], "payload_type": type(payload).__name__},
        )
    normalized = {field: payload.get(field) for field in XHS_POST_FIELDS if field in payload}
    normalized["target_audience"] = _string_list(normalized.get("target_audience"))
    normalized["titles"] = _string_list(normalized.get("titles"))
    normalized["hashtags"] = _string_list(normalized.get("hashtags"))
    if not _valid_image_plan(normalized.get("image_plan")):
        normalized["image_plan"] = payload.get("image_plan")
    return normalized


def _has_frame_anchors(keyframes_payload: Dict[str, Any]) -> bool:
    return bool(_keyframes(keyframes_payload))


def _normalize_xhs_post(
    payload: Dict[str, Any],
    metadata: Dict[str, Any],
    content_assets: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    style: str,
) -> Dict[str, Any]:
    return _normalize_post(payload, keyframes_payload, platform="xhs")


def _normalize_toutiao_post(
    payload: Dict[str, Any],
    metadata: Dict[str, Any],
    content_assets: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    style: str,
) -> Dict[str, Any]:
    return _normalize_post(payload, keyframes_payload, platform="toutiao")


def _repair_image_plan_anchors(payload: Dict[str, Any], keyframes_payload: Dict[str, Any]) -> Dict[str, Any]:
    frames = _keyframes(keyframes_payload)
    if not frames:
        repaired_plan = []
        for item in payload.get("image_plan", []) or []:
            if not isinstance(item, dict):
                repaired_plan.append(item)
                continue
            repaired = dict(item)
            repaired["source_frame_time"] = None
            repaired["source_frame_path"] = None
            repaired_plan.append(repaired)
        payload["image_plan"] = repaired_plan
        return payload
    allowed_paths = {str(frame.get("path") or "") for frame in frames if frame.get("path")}
    allowed_times = [float(frame["time"]) for frame in frames if frame.get("time") is not None]
    repaired_plan = []
    for index, item in enumerate(payload.get("image_plan", []) or []):
        if not isinstance(item, dict):
            repaired_plan.append(item)
            continue
        repaired = dict(item)
        source_path = str(repaired.get("source_frame_path") or "")
        source_time = _as_float(repaired.get("source_frame_time"))
        has_allowed_path = source_path in allowed_paths
        has_allowed_time = source_time is not None and any(abs(source_time - frame_time) <= 1.5 for frame_time in allowed_times)
        if not has_allowed_path:
            frame = _frame_for_anchor(frames, repaired, index)
            if frame.get("path"):
                repaired["source_frame_path"] = frame.get("path")
            if frame.get("time") is not None:
                repaired["source_frame_time"] = frame.get("time")
            elif not has_allowed_time:
                repaired["source_frame_time"] = None
        repaired_plan.append(repaired)
    payload["image_plan"] = repaired_plan
    return payload


def _compact_content_assets_for_prompt(content_assets: Dict[str, Any]) -> Dict[str, Any]:
    compact_points = []
    for point in content_assets.get("core_points", []) or []:
        if not isinstance(point, dict):
            continue
        compact_evidence = []
        for evidence in point.get("evidence", []) or []:
            if not isinstance(evidence, dict):
                continue
            compact_evidence.append(
                {
                    "type": evidence.get("type"),
                    "time": evidence.get("time"),
                    "frame_path": evidence.get("frame_path"),
                    "text": _clip(evidence.get("text"), 90),
                }
            )
        compact_points.append(
            {
                "point": _clip(point.get("point")),
                "why_it_matters": _clip(point.get("why_it_matters")),
                "evidence": compact_evidence[:2],
            }
        )

    compact_evidence_items = []
    for item in content_assets.get("source_evidence", []) or []:
        if not isinstance(item, dict):
            continue
        compact_evidence_items.append(
            {
                "claim": _clip(item.get("claim")),
                "source_type": item.get("source_type"),
                "time": item.get("time"),
                "source_path": item.get("source_path"),
                "source_text": _clip(item.get("source_text"), 90),
            }
        )

    return {
        "one_sentence_summary": _clip(content_assets.get("one_sentence_summary"), 220),
        "core_points": compact_points[:6],
        "golden_quotes": content_assets.get("golden_quotes", [])[:5],
        "audience": content_assets.get("audience", [])[:5],
        "pain_points": content_assets.get("pain_points", [])[:5],
        "xiaohongshu_angles": content_assets.get("xiaohongshu_angles", [])[:5],
        "recommended_content_type": content_assets.get("recommended_content_type"),
        "source_evidence": compact_evidence_items[:8],
        "source_metadata": content_assets.get("source_metadata", {}),
    }


def _compact_keyframes_for_prompt(keyframes_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    frames = []
    for frame in keyframes_payload.get("keyframes", []) or []:
        if not isinstance(frame, dict):
            continue
        frames.append(
            {
                "time": frame.get("time"),
                "path": frame.get("path"),
                "score": frame.get("score"),
                "reason": _clip(frame.get("reason"), 120),
                "related_transcript_text": _clip(frame.get("related_transcript_text"), 120),
            }
        )
    return frames[:12]


def _compact_visual_for_prompt(visual_payload: Dict[str, Any]) -> Dict[str, Any]:
    frames = []
    for frame in visual_payload.get("frames", []) or []:
        if not isinstance(frame, dict):
            continue
        frames.append(
            {
                "time": frame.get("time"),
                "path": frame.get("path"),
                "ocr_text": _clip(frame.get("ocr_text"), 80),
                "visual_summary": _clip(frame.get("visual_summary"), 140),
                "screen_text_confidence": frame.get("screen_text_confidence"),
            }
        )
    return {
        "ocr_provider": visual_payload.get("ocr_provider"),
        "warnings": visual_payload.get("warnings", []),
        "frames": frames[:12],
    }


def _source_texts(content_assets: Dict[str, Any]) -> list[str]:
    texts = []
    for item in content_assets.get("source_evidence", []) or []:
        if isinstance(item, dict):
            texts.append(str(item.get("source_text") or ""))
    for point in content_assets.get("core_points", []) or []:
        if not isinstance(point, dict):
            continue
        for evidence in point.get("evidence", []) or []:
            if isinstance(evidence, dict):
                texts.append(str(evidence.get("text") or ""))
    return [clean_text(text) for text in texts if clean_text(text)]


def _contains_long_verbatim(source: str, generated: str, min_chars: int = MIN_VERBATIM_CHARS) -> str:
    source = clean_text(source)
    generated = clean_text(generated)
    if len(source) < min_chars or len(generated) < min_chars:
        return ""
    if source in generated:
        return source[:120]
    for start in range(0, len(source) - min_chars + 1):
        snippet = source[start : start + min_chars]
        if snippet.strip() and snippet in generated:
            return snippet
    return ""


def _visible_generated_texts(payload: Dict[str, Any]) -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = []
    for field in VISIBLE_TEXT_FIELDS:
        if payload.get(field):
            texts.append((field, str(payload[field])))
    for index, title in enumerate(payload.get("titles", []) or []):
        texts.append((f"titles[{index}]", str(title)))
    for index, audience in enumerate(payload.get("target_audience", []) or []):
        texts.append((f"target_audience[{index}]", str(audience)))
    for index, hashtag in enumerate(payload.get("hashtags", []) or []):
        texts.append((f"hashtags[{index}]", str(hashtag)))
    for index, item in enumerate(payload.get("image_plan", []) or []):
        if not isinstance(item, dict):
            continue
        for field in ["caption", "content_point"]:
            if item.get(field):
                texts.append((f"image_plan[{index}].{field}", str(item[field])))
    return [(field, clean_text(text)) for field, text in texts if clean_text(text)]


def _guard_against_verbatim_copy(payload: Dict[str, Any], content_assets: Dict[str, Any]) -> None:
    for source in _source_texts(content_assets):
        for field, generated in _visible_generated_texts(payload):
            match = _contains_long_verbatim(source, generated)
            if match:
                raise PipelineError(
                    code="verbatim_source_copy_detected",
                    message="Generated Xiaohongshu copy contains a long verbatim source fragment. Ask the LLM to rewrite instead of copying subtitles.",
                    step="writing_xhs",
                    details={"matched_fragment": match, "field": field, "min_chars": MIN_VERBATIM_CHARS},
                )


def write_xhs_post(
    metadata: Dict[str, Any],
    content_assets: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    style: str,
    paths: ProjectPaths,
) -> Dict[str, Any]:
    return _write_platform_post(metadata, content_assets, keyframes_payload, visual_payload, style, paths, platform="xhs")


def write_toutiao_post(
    metadata: Dict[str, Any],
    content_assets: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    style: str,
    paths: ProjectPaths,
) -> Dict[str, Any]:
    return _write_platform_post(metadata, content_assets, keyframes_payload, visual_payload, style, paths, platform="toutiao")


def _write_platform_post(
    metadata: Dict[str, Any],
    content_assets: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    style: str,
    paths: ProjectPaths,
    *,
    platform: str,
) -> Dict[str, Any]:
    prompt = PLATFORM_PROMPTS.get(platform, PLATFORM_PROMPTS["xhs"])
    schema = {
        "content_type": "string",
        "target_audience": ["string"],
        "titles": ["至少 5 个中文标题"],
        "cover_text": "string",
        "hook": "string",
        "body": "string, 手机阅读短段落，不逐字搬运字幕",
        "image_plan": [
            {
                "page": "number",
                "role": "cover|point|step|quote|summary",
                "caption": "string",
                "source_frame_time": "number|null",
                "source_frame_path": "string|null",
                "content_point": "string",
            }
        ],
        "hashtags": ["string"],
        "publish_suggestion": "string",
        "source_disclaimer": "string",
    }
    context = {
        "metadata": {
            "title": metadata.get("title"),
            "author": metadata.get("author"),
            "url": metadata.get("url"),
            "duration": metadata.get("duration"),
        },
        "content_assets": _compact_content_assets_for_prompt(content_assets),
        "keyframes": _compact_keyframes_for_prompt(keyframes_payload),
        "visual_analysis": _compact_visual_for_prompt(visual_payload),
        "style": style,
    }
    try:
        payload = llm_client.json_chat(
            [
                {
                    "role": "system",
                    "content": prompt["system"],
                },
                {
                    "role": "user",
                    "content": (
                        prompt["user"] +
                        f"Schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
                        f"Context:\n{json.dumps(context, ensure_ascii=False)}"
                    ),
                },
            ],
            step="writing_xhs",
            temperature=0.45,
        )
    except PipelineError:
        raise
    payload = _normalize_post(payload, keyframes_payload, platform=platform)
    payload = _repair_image_plan_anchors(payload, keyframes_payload)
    require_frame_anchors = _has_frame_anchors(keyframes_payload)
    payload = validate_xhs_post(payload, require_frame_anchors=require_frame_anchors)
    if require_frame_anchors:
        validate_xhs_post_anchors(payload, keyframes_payload, paths)
    _guard_against_verbatim_copy(payload, content_assets)
    if "source_disclaimer" not in payload:
        payload["source_disclaimer"] = "本稿基于公开视频/授权视频的信息进行二次创作，保留来源与时间点用于追溯。"
    write_json(paths.analysis_dir / str(prompt["output_filename"]), payload)
    return payload
