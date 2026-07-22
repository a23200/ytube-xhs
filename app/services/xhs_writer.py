import json
from typing import Any, Callable, Dict, List, Optional

from app.services.article_quality import evaluate_article_quality, quality_error
from app.services.contracts import validate_xhs_post
from app.services.errors import PipelineError
from app.services.llm_client import llm_client
from app.services.platforms import get_platform, platform_values
from app.services.runtime_store import ProjectPaths, write_json
from app.services.source_anchors import validate_xhs_post_anchors
from app.services.text_utils import clean_text

MIN_VERBATIM_CHARS = 24
CONTRACT_REPAIR_CODES = {"llm_contract_invalid", "source_anchor_invalid"}
MAX_CONTRACT_REPAIRS = 2
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
HOOK_INSTRUCTION = (
    "请先写一个不超过100个中文字符的开头钩子。语气短促、有冲击力、口语化，突出事实中的反差、冲突或悬念。"
    "专业概念必须换成生活中的大白话，让小学生和老年人都能理解。不得捏造冲突、数据或结论。"
)
COMMON_ARTICLE_RULES = (
    "正文只能使用连贯自然段，全程不得出现任何小标题，包括序号标题、背景、原因、总结、写在最后、Markdown 标题、"
    "加粗标题或独占一行的概括性短句。不要出现“本视频/这条视频/第几秒/拆解/整理”等报告感表达。"
    "不能逐字照搬字幕或来源文章，不得新增来源中不存在的人物、数字、因果和结论。"
    "可以把来源中真实存在的百分比转成“每 X 个特定群体中，大约就有 1 个”，但必须保留口径、范围和时间；"
    "只有来源同时给出总体人数或精确人数时，才可换算为“约 XX 万人、家庭或用户”，否则禁止猜测总体。"
)


def _platform_prompt(platform: str) -> Dict[str, str]:
    adapter = get_platform(platform)
    image_guidance = (
        "image_plan 要服务最终原创文章，可使用关键帧作为事实和视觉参考，但不能做成视频截图复盘。"
        if adapter.supports_images
        else "image_plan 仅作为内部内容节奏记录，不会触发图片生成。"
    )
    return {
        "name": adapter.name,
        "artifact": adapter.post_filename,
        "output_filename": adapter.post_filename,
        "system": (
            f"你是{adapter.name}原创内容编辑。你要基于来源提供的事实、情绪和方向进行二次创作，产出{adapter.content_type}，"
            "而不是字幕摘要、逐帧复述或侵权搬运文案。必须保留来源信息和时间点用于内部追溯。"
            f"{adapter.style_guidance}{COMMON_ARTICLE_RULES}返回严格 JSON。"
        ),
        "user": (
            f"基于 content_assets 生成 {adapter.post_filename}。{HOOK_INSTRUCTION}"
            f"{adapter.length_guidance}{COMMON_ARTICLE_RULES}{image_guidance}"
            "必须阅读 transcript_timeline 的完整时间线，覆盖来源中有实质信息的开头、中段和结尾主题；"
            "即使 content_assets 标记为本地降级，也不得只围绕其中少量证据点写短文。"
            "如果 keyframes 非空，image_plan 每一项的 source_frame_path/source_frame_time 只能使用 keyframes 中真实存在的值。"
            "如果 keyframes 为空，source_frame_time 和 source_frame_path 必须为 null。"
            "返回字段必须匹配 schema，不能省略任何字段。\n\n"
        ),
    }


PLATFORM_PROMPTS = {adapter.key: _platform_prompt(adapter.key) for adapter in platform_values()}


def _post_schema() -> Dict[str, Any]:
    return {
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


def _compact_transcript_for_prompt(
    transcript_payload: Optional[Dict[str, Any]],
    *,
    max_segments: int = 180,
    max_text_chars: int = 240,
) -> Dict[str, Any]:
    if not isinstance(transcript_payload, dict):
        return {"source": None, "language": None, "segment_count": 0, "segments_in_prompt": 0, "segments": []}
    source_segments = [item for item in transcript_payload.get("segments", []) or [] if isinstance(item, dict)]
    if len(source_segments) <= max_segments:
        selected = source_segments
    else:
        indices = sorted(
            {
                round(index * (len(source_segments) - 1) / (max_segments - 1))
                for index in range(max_segments)
            }
        )
        selected = [source_segments[index] for index in indices]
    segments = [
        {
            "start": item.get("start"),
            "end": item.get("end"),
            "text": _clip(item.get("text"), max_text_chars),
        }
        for item in selected
        if clean_text(str(item.get("text") or ""))
    ]
    return {
        "source": transcript_payload.get("source"),
        "language": transcript_payload.get("language"),
        "segment_count": len(source_segments),
        "segments_in_prompt": len(segments),
        "segments": segments,
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
                    message="Generated platform copy contains a long verbatim source fragment. The LLM must rewrite instead of copying subtitles.",
                    step="writing_xhs",
                    details={"matched_fragment": match, "field": field, "min_chars": MIN_VERBATIM_CHARS},
                )


def _post_context(
    metadata: Dict[str, Any],
    content_assets: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    style: str,
    transcript_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "metadata": {
            "title": metadata.get("title"),
            "author": metadata.get("author"),
            "url": metadata.get("url"),
            "duration": metadata.get("duration"),
        },
        "content_assets": _compact_content_assets_for_prompt(content_assets),
        "transcript_timeline": _compact_transcript_for_prompt(transcript_payload),
        "keyframes": _compact_keyframes_for_prompt(keyframes_payload),
        "visual_analysis": _compact_visual_for_prompt(visual_payload),
        "style": style,
    }


def _validate_post_payload(
    payload: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    paths: ProjectPaths,
    *,
    platform: str,
) -> Dict[str, Any]:
    payload = _normalize_post(payload, keyframes_payload, platform=platform)
    payload = _repair_image_plan_anchors(payload, keyframes_payload)
    require_frame_anchors = _has_frame_anchors(keyframes_payload)
    payload = validate_xhs_post(payload, require_frame_anchors=require_frame_anchors)
    if require_frame_anchors:
        validate_xhs_post_anchors(payload, keyframes_payload, paths)
    return payload


def _repair_post_contract(
    payload: Dict[str, Any],
    violation: PipelineError,
    metadata: Dict[str, Any],
    content_assets: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    style: str,
    platform: str,
    transcript_payload: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    prompt = PLATFORM_PROMPTS[platform]
    return llm_client.json_chat(
        [
            {
                "role": "system",
                "content": (
                    f"你是{prompt['name']}平台稿 JSON 修复器。修复完整 JSON，使它严格满足 schema 和 validation error。"
                    "不得删除字段、降低字数与原创度要求、添加小标题或编造事实。只可使用 context 中的真实字幕时间和关键帧路径。"
                    "保留已有正确内容，补齐缺失字段并修正类型或锚点，返回严格 JSON。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Schema:\n{json.dumps(_post_schema(), ensure_ascii=False)}\n\n"
                    f"Validation error:\n{json.dumps(violation.to_dict(), ensure_ascii=False)}\n\n"
                    f"Context:\n{json.dumps(_post_context(metadata, content_assets, keyframes_payload, visual_payload, style, transcript_payload), ensure_ascii=False)}\n\n"
                    f"Draft JSON:\n{json.dumps(payload, ensure_ascii=False)}"
                ),
            },
        ],
        step="writing_xhs",
        temperature=0.0,
    )


def _validate_or_repair_post(
    payload: Dict[str, Any],
    metadata: Dict[str, Any],
    content_assets: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    style: str,
    paths: ProjectPaths,
    *,
    platform: str,
    transcript_payload: Optional[Dict[str, Any]],
    max_repairs: int = MAX_CONTRACT_REPAIRS,
) -> Dict[str, Any]:
    current = payload
    for repair_attempt in range(max_repairs + 1):
        try:
            return _validate_post_payload(current, keyframes_payload, paths, platform=platform)
        except PipelineError as exc:
            if exc.code not in CONTRACT_REPAIR_CODES:
                raise
            if repair_attempt >= max_repairs:
                details = dict(exc.details)
                details.update(
                    {
                        "repair_attempts": repair_attempt,
                        "received_fields": sorted(current) if isinstance(current, dict) else [],
                        "platform": platform,
                    }
                )
                raise PipelineError(exc.code, exc.message, exc.step, details) from exc
            current = _repair_post_contract(
                current,
                exc,
                metadata,
                content_assets,
                keyframes_payload,
                visual_payload,
                style,
                platform,
                transcript_payload,
            )
    raise RuntimeError("Unreachable platform post contract repair state")


def _rewrite_post_for_quality(
    payload: Dict[str, Any],
    metadata: Dict[str, Any],
    content_assets: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    style: str,
    platform: str,
    quality_report: Dict[str, Any],
    transcript_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    prompt = PLATFORM_PROMPTS[platform]
    return llm_client.json_chat(
        [
            {
                "role": "system",
                "content": (
                    f"你是{prompt['name']}文章质量审校。请根据机器校验报告定向重写完整平台稿 JSON。"
                    "保留来源事实、数字口径、人物、因果、标题数量、image_plan 页数以及所有真实来源锚点。"
                    "正文只能由连贯自然段构成，不得使用任何小标题。开头必须在100个中文字符以内，有真实反差、冲突或悬念，"
                    f"专业词换成大白话。{get_platform(platform).length_guidance}"
                    "不得连续照搬来源原文24个字符以上，不得编造人口总量或数据，返回严格 JSON。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "逐条修复 quality_report.violations，并重新检查所有可发布字段。不要简单删除小标题导致上下文断裂，"
                    "要把标题含义自然融入前后段落。证据原文只允许存在于内部 evidence 字段，不得出现在可发布字段。"
                    "\n\n"
                    f"Schema:\n{json.dumps(_post_schema(), ensure_ascii=False)}\n\n"
                    f"Quality report:\n{json.dumps(quality_report, ensure_ascii=False)}\n\n"
                    f"Context:\n{json.dumps(_post_context(metadata, content_assets, keyframes_payload, visual_payload, style, transcript_payload), ensure_ascii=False)}\n\n"
                    f"Draft JSON:\n{json.dumps(payload, ensure_ascii=False)}"
                ),
            },
        ],
        step="writing_xhs",
        temperature=0.2,
    )


def _guard_or_rewrite_post(
    payload: Dict[str, Any],
    metadata: Dict[str, Any],
    content_assets: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    style: str,
    paths: ProjectPaths,
    *,
    platform: str,
    transcript_payload: Optional[Dict[str, Any]] = None,
    on_validation: Optional[Callable[[Dict[str, Any]], None]] = None,
    max_rewrites: int = 2,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    current = payload
    for rewrite_count in range(max_rewrites + 1):
        field_violation: Optional[PipelineError] = None
        try:
            _guard_against_verbatim_copy(current, content_assets)
        except PipelineError as exc:
            field_violation = exc
        report = evaluate_article_quality(
            current,
            content_assets,
            transcript_payload,
            platform=platform,
            rewrite_count=rewrite_count,
        )
        write_json(paths.analysis_dir / get_platform(platform).quality_filename, report)
        if field_violation:
            precise = field_violation.to_dict()
            report["violations"] = [item for item in report["violations"] if item.get("code") != precise["code"]]
            report["violations"].insert(0, precise)
            report["passed"] = False
        if on_validation:
            on_validation(report)
        if report["passed"]:
            return current, report
        if rewrite_count >= max_rewrites:
            raise quality_error(report)
        current = _rewrite_post_for_quality(
            current,
            metadata,
            content_assets,
            keyframes_payload,
            visual_payload,
            style,
            platform,
            report,
            transcript_payload,
        )
        current = _validate_or_repair_post(
            current,
            metadata,
            content_assets,
            keyframes_payload,
            visual_payload,
            style,
            paths,
            platform=platform,
            transcript_payload=transcript_payload,
        )
    raise RuntimeError("Unreachable quality validation state")


def write_xhs_post(
    metadata: Dict[str, Any],
    content_assets: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    style: str,
    paths: ProjectPaths,
    transcript_payload: Optional[Dict[str, Any]] = None,
    on_validation: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    return write_platform_post(
        metadata,
        content_assets,
        keyframes_payload,
        visual_payload,
        style,
        paths,
        platform="xhs",
        transcript_payload=transcript_payload,
        on_validation=on_validation,
    )


def write_toutiao_post(
    metadata: Dict[str, Any],
    content_assets: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    style: str,
    paths: ProjectPaths,
    transcript_payload: Optional[Dict[str, Any]] = None,
    on_validation: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    return write_platform_post(
        metadata,
        content_assets,
        keyframes_payload,
        visual_payload,
        style,
        paths,
        platform="toutiao",
        transcript_payload=transcript_payload,
        on_validation=on_validation,
    )


def write_platform_post(
    metadata: Dict[str, Any],
    content_assets: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    style: str,
    paths: ProjectPaths,
    *,
    platform: str,
    transcript_payload: Optional[Dict[str, Any]] = None,
    on_validation: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    adapter = get_platform(platform)
    prompt = PLATFORM_PROMPTS[adapter.key]
    schema = _post_schema()
    context = _post_context(metadata, content_assets, keyframes_payload, visual_payload, style, transcript_payload)
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
    payload = _validate_or_repair_post(
        payload,
        metadata,
        content_assets,
        keyframes_payload,
        visual_payload,
        style,
        paths,
        platform=platform,
        transcript_payload=transcript_payload,
    )
    payload, quality_report = _guard_or_rewrite_post(
        payload,
        metadata,
        content_assets,
        keyframes_payload,
        visual_payload,
        style,
        paths,
        platform=platform,
        transcript_payload=transcript_payload,
        on_validation=on_validation,
    )
    if "source_disclaimer" not in payload:
        payload["source_disclaimer"] = "本稿基于公开视频/授权视频的信息进行二次创作，保留来源与时间点用于追溯。"
    payload["platform"] = adapter.key
    payload["platform_name"] = adapter.name
    payload["quality"] = {
        "estimated_rewrite_degree": quality_report["similarity"]["estimated_rewrite_degree"],
        "rewrite_count": quality_report["rewrite_count"],
        "report": adapter.quality_filename,
        "note": quality_report["policy"]["originality_note"],
    }
    write_json(paths.analysis_dir / adapter.post_filename, payload)
    write_json(paths.analysis_dir / adapter.quality_filename, quality_report)
    return payload
