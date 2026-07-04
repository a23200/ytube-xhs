import json
from typing import Any, Dict, List

from app.services.contracts import validate_content_assets
from app.services.errors import PipelineError
from app.services.llm_client import llm_client
from app.services.runtime_store import ProjectPaths, write_json
from app.services.source_anchors import validate_content_asset_anchors
from app.services.text_utils import clean_text

MIN_VERBATIM_CHARS = 24
def _compact_transcript(transcript_payload: Dict[str, Any], max_segments: int = 160) -> List[Dict[str, Any]]:
    segments = transcript_payload.get("segments", [])
    if len(segments) <= max_segments:
        return segments
    stride = max(1, len(segments) // max_segments)
    sampled = segments[::stride][:max_segments]
    if segments[-1] not in sampled:
        sampled.append(segments[-1])
    return sampled

def _normalize_content_assets(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise PipelineError(
            code="llm_contract_invalid",
            message="LLM output for content-assets.json must be a JSON object.",
            step="planning_content",
            details={"artifact": "content-assets.json", "payload_type": type(payload).__name__},
        )
    return payload


def _content_assets_schema() -> Dict[str, Any]:
    return {
        "one_sentence_summary": "string",
        "core_points": [
            {
                "point": "string",
                "why_it_matters": "string",
                "evidence": [
                    {
                        "type": "transcript|keyframe|ocr",
                        "time": "number, required unless frame_path is provided",
                        "frame_path": "string|null",
                        "text": "string",
                    }
                ],
            }
        ],
        "golden_quotes": [{"quote": "string", "time": "number|null", "rewrite_note": "string"}],
        "chapters": [{"title": "string", "start": "number|null", "end": "number|null", "summary": "string"}],
        "steps": [{"step": "string", "evidence_time": "number|null"}],
        "audience": ["string"],
        "pain_points": ["string"],
        "xiaohongshu_angles": ["string"],
        "recommended_content_type": "string",
        "source_evidence": [
            {
                "claim": "string",
                "source_type": "string",
                "time": "number, required unless source_path is provided",
                "source_path": "string|null",
                "source_text": "string",
            }
        ],
    }


def _content_assets_context(
    metadata: Dict[str, Any],
    transcript_payload: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    language: str,
    style: str,
) -> Dict[str, Any]:
    return {
        "metadata": {
            "video_id": metadata.get("video_id"),
            "url": metadata.get("url"),
            "title": metadata.get("title"),
            "author": metadata.get("author"),
            "description": metadata.get("description"),
            "duration": metadata.get("duration"),
        },
        "transcript_segments": _compact_transcript(transcript_payload),
        "keyframes": keyframes_payload.get("keyframes", []),
        "visual_analysis": visual_payload,
        "style": style,
        "language": language,
    }


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


def _source_texts(transcript_payload: Dict[str, Any], payload: Dict[str, Any]) -> list[str]:
    texts = []
    for segment in transcript_payload.get("segments", []) or []:
        if isinstance(segment, dict):
            texts.append(str(segment.get("text") or ""))
    for item in payload.get("source_evidence", []) or []:
        if isinstance(item, dict):
            texts.append(str(item.get("source_text") or ""))
    for point in payload.get("core_points", []) or []:
        if not isinstance(point, dict):
            continue
        for evidence in point.get("evidence", []) or []:
            if isinstance(evidence, dict):
                texts.append(str(evidence.get("text") or ""))

    cleaned = []
    seen = set()
    for text in texts:
        normalized = clean_text(text)
        if normalized and normalized not in seen:
            cleaned.append(normalized)
            seen.add(normalized)
    return cleaned


def _generated_content_texts(payload: Dict[str, Any]) -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = []
    for field in ["one_sentence_summary", "recommended_content_type"]:
        if payload.get(field):
            texts.append((field, str(payload[field])))

    for index, point in enumerate(payload.get("core_points", []) or []):
        if not isinstance(point, dict):
            continue
        for field in ["point", "why_it_matters"]:
            if point.get(field):
                texts.append((f"core_points[{index}].{field}", str(point[field])))

    for index, quote in enumerate(payload.get("golden_quotes", []) or []):
        if not isinstance(quote, dict):
            continue
        for field in ["quote", "rewrite_note"]:
            if quote.get(field):
                texts.append((f"golden_quotes[{index}].{field}", str(quote[field])))

    for index, chapter in enumerate(payload.get("chapters", []) or []):
        if not isinstance(chapter, dict):
            continue
        for field in ["title", "summary"]:
            if chapter.get(field):
                texts.append((f"chapters[{index}].{field}", str(chapter[field])))

    for index, step in enumerate(payload.get("steps", []) or []):
        if isinstance(step, dict) and step.get("step"):
            texts.append((f"steps[{index}].step", str(step["step"])))

    for field in ["audience", "pain_points", "xiaohongshu_angles"]:
        for index, item in enumerate(payload.get(field, []) or []):
            texts.append((f"{field}[{index}]", str(item)))

    for index, item in enumerate(payload.get("source_evidence", []) or []):
        if isinstance(item, dict) and item.get("claim"):
            texts.append((f"source_evidence[{index}].claim", str(item["claim"])))

    return [(field, clean_text(text)) for field, text in texts if clean_text(text)]


def _guard_against_verbatim_copy(payload: Dict[str, Any], transcript_payload: Dict[str, Any]) -> None:
    for source in _source_texts(transcript_payload, payload):
        for field, generated in _generated_content_texts(payload):
            match = _contains_long_verbatim(source, generated)
            if match:
                raise PipelineError(
                    code="verbatim_source_copy_detected",
                    message=(
                        "Generated content assets contain a long verbatim source fragment. "
                        "Ask the LLM to rewrite and keep original text only in evidence fields."
                    ),
                    step="planning_content",
                    details={"matched_fragment": match, "field": field, "min_chars": MIN_VERBATIM_CHARS},
                )


def _validate_content_assets_payload(
    payload: Dict[str, Any],
    transcript_payload: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    paths: ProjectPaths,
) -> Dict[str, Any]:
    payload = _normalize_content_assets(payload)
    payload = validate_content_assets(payload)
    validate_content_asset_anchors(payload, transcript_payload, keyframes_payload, paths)
    return payload


def _rewrite_content_assets_for_verbatim(
    payload: Dict[str, Any],
    transcript_payload: Dict[str, Any],
    metadata: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    language: str,
    style: str,
    violation: PipelineError,
) -> Dict[str, Any]:
    """Ask the LLM to repair only generated fields after a verbatim-copy hit."""

    return llm_client.json_chat(
        [
            {
                "role": "system",
                "content": (
                    "你是版权安全改写审校。你会收到一个 content-assets.json 草稿，其中某些可发布/可展示字段"
                    "逐字照搬了原字幕。请返回修复后的完整 JSON：保留事实、时间点、证据字段和结构，但把所有生成字段"
                    "改成原创中文表达。source_evidence.source_text 与 core_points.evidence.text 可以保留原文作为证据；"
                    "除此之外，任何字段都不能连续照搬原文 24 个字符以上。不要新增无来源事实，返回严格 JSON。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "修复这个 content-assets.json 草稿。重点修复 violation.field 指向的字段，并顺手检查所有标题、总结、"
                    "观点、金句、步骤、受众、痛点、角度和 claim，确保它们都是改写后的原创表达；原文只允许留在 evidence/source_text。"
                    "\n\n"
                    f"Schema:\n{json.dumps(_content_assets_schema(), ensure_ascii=False)}\n\n"
                    f"Violation:\n{json.dumps(violation.to_dict(), ensure_ascii=False)}\n\n"
                    f"Original context:\n{json.dumps(_content_assets_context(metadata, transcript_payload, keyframes_payload, visual_payload, language, style), ensure_ascii=False)}\n\n"
                    f"Draft JSON:\n{json.dumps(payload, ensure_ascii=False)}"
                ),
            },
        ],
        step="planning_content",
        temperature=0.15,
    )


def _guard_or_rewrite_content_assets(
    payload: Dict[str, Any],
    transcript_payload: Dict[str, Any],
    metadata: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    language: str,
    style: str,
    paths: ProjectPaths,
) -> Dict[str, Any]:
    try:
        _guard_against_verbatim_copy(payload, transcript_payload)
        return payload
    except PipelineError as exc:
        if exc.code != "verbatim_source_copy_detected":
            raise
        repaired = _rewrite_content_assets_for_verbatim(
            payload,
            transcript_payload,
            metadata,
            keyframes_payload,
            visual_payload,
            language,
            style,
            exc,
        )
        repaired = _validate_content_assets_payload(repaired, transcript_payload, keyframes_payload, paths)
        _guard_against_verbatim_copy(repaired, transcript_payload)
        return repaired


def _first_transcript_segment(transcript_payload: Dict[str, Any]) -> Dict[str, Any]:
    for segment in transcript_payload.get("segments", []) or []:
        if isinstance(segment, dict) and clean_text(str(segment.get("text") or "")):
            return segment
    return {"start": 0.0, "end": 0.0, "text": "未找到可用字幕片段。"}


def _segment_at(transcript_payload: Dict[str, Any], index: int) -> Dict[str, Any]:
    segments = [segment for segment in transcript_payload.get("segments", []) or [] if isinstance(segment, dict)]
    if not segments:
        return _first_transcript_segment(transcript_payload)
    return segments[min(max(index, 0), len(segments) - 1)]


def _short_text(value: Any, *, max_chars: int = 72) -> str:
    text = clean_text(str(value or ""))
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _time_of(segment: Dict[str, Any]) -> float:
    try:
        return float(segment.get("start") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def build_basic_content_assets(
    metadata: Dict[str, Any],
    transcript_payload: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    language: str,
    style: str,
    paths: ProjectPaths,
    *,
    fallback_reason: str = "",
) -> Dict[str, Any]:
    """Create a source-grounded analysis package without an LLM for Analyze mode.

    This is intentionally conservative: it only summarizes the existence of
    transcript/keyframe/OCR evidence and keeps original transcript text in
    evidence fields, not in generated claims. Produce mode still requires a real
    LLM and must not use this fallback to create publishable copy.
    """

    segments = [segment for segment in transcript_payload.get("segments", []) or [] if isinstance(segment, dict)]
    first = _segment_at(transcript_payload, 0)
    middle = _segment_at(transcript_payload, len(segments) // 2 if segments else 0)
    last = _segment_at(transcript_payload, len(segments) - 1 if segments else 0)
    title = clean_text(str(metadata.get("title") or "这个视频"))
    frame_count = len(keyframes_payload.get("keyframes", []) or [])
    visual_frames = visual_payload.get("frames", []) or []
    first_visual = next((frame for frame in visual_frames if isinstance(frame, dict)), {})
    ocr_text = _short_text(first_visual.get("ocr_text"), max_chars=80) if first_visual else ""
    visual_summary = _short_text(first_visual.get("visual_summary"), max_chars=80) if first_visual else ""

    core_points = [
        {
            "point": "围绕视频主题提炼出一个可继续编辑的事实底稿",
            "why_it_matters": "当前结果来自本地规则降级，可先核对来源证据，再配置稳定 LLM 生成更完整的原创表达。",
            "evidence": [{"type": "transcript", "time": _time_of(first), "text": str(first.get("text") or "")}],
        },
        {
            "point": "字幕时间轴已生成，可作为后续内容改写和观点提炼的主要依据",
            "why_it_matters": "有时间点证据能降低误读风险，也方便人工回看原视频语境。",
            "evidence": [{"type": "transcript", "time": _time_of(middle), "text": str(middle.get("text") or "")}],
        },
    ]
    if frame_count and first_visual:
        frame_path = first_visual.get("path") or (keyframes_payload.get("keyframes") or [{}])[0].get("path")
        frame_time = first_visual.get("time") or (keyframes_payload.get("keyframes") or [{}])[0].get("time") or 0.0
        core_points.append(
            {
                "point": "关键帧与 OCR 已完成，可辅助判断画面语境",
                "why_it_matters": "画面证据适合用于后续选图、卡片构图和补充文字信息。",
                "evidence": [
                    {
                        "type": "keyframe",
                        "time": frame_time,
                        "frame_path": frame_path,
                        "text": visual_summary or ocr_text or "关键帧证据",
                    }
                ],
            }
        )

    payload = {
        "one_sentence_summary": f"《{_short_text(title, max_chars=40)}》已完成基础解析，可基于真实字幕和关键帧继续编辑。",
        "core_points": core_points,
        "golden_quotes": [
            {
                "quote": "先核对证据，再生成适合平台的原创表达。",
                "time": _time_of(first),
                "rewrite_note": "本地降级生成的提示语，不引用原字幕。",
            }
        ],
        "chapters": [
            {
                "title": "开头信息",
                "start": _time_of(first),
                "end": first.get("end"),
                "summary": "视频开头片段已提取，可用于确认主题和语境。",
            },
            {
                "title": "中段信息",
                "start": _time_of(middle),
                "end": middle.get("end"),
                "summary": "视频中段片段已提取，可用于补充主要论述。",
            },
            {
                "title": "结尾信息",
                "start": _time_of(last),
                "end": last.get("end"),
                "summary": "视频结尾片段已提取，可用于核对收束信息。",
            },
        ],
        "steps": [
            {"step": "核对字幕证据并删掉不适合发布的原文表达。", "evidence_time": _time_of(first)},
            {"step": "根据目标平台重新组织观点、标题和封面信息。", "evidence_time": _time_of(middle)},
        ],
        "audience": ["需要把视频素材整理成图文底稿的创作者"],
        "pain_points": ["LLM 暂时不可用或超时，无法自动生成完整原创选题"],
        "xiaohongshu_angles": [f"{style}型基础解析", "先证据后改写的内容工作流"],
        "recommended_content_type": f"{style}型二次创作底稿",
        "source_evidence": [
            {
                "claim": "基础解析使用了字幕时间轴中的开头证据",
                "source_type": "transcript",
                "time": _time_of(first),
                "source_text": str(first.get("text") or ""),
            },
            {
                "claim": "基础解析使用了字幕时间轴中的中段证据",
                "source_type": "transcript",
                "time": _time_of(middle),
                "source_text": str(middle.get("text") or ""),
            },
        ],
        "analysis_mode": "local_basic_fallback",
        "fallback_reason": fallback_reason,
        "fallback_notice": (
            "这是 Analyze 阶段的本地基础解析，不是可直接发布文案。请配置稳定 LLM 后执行 Produce，"
            "或先人工编辑 content-assets.json。"
        ),
        "source_metadata": {
            "url": metadata.get("url"),
            "title": metadata.get("title"),
            "author": metadata.get("author"),
            "video_id": metadata.get("video_id"),
        },
    }
    if frame_count and first_visual:
        payload["source_evidence"].append(
            {
                "claim": "基础解析使用了关键帧或 OCR 证据",
                "source_type": "keyframe",
                "time": first_visual.get("time") or (keyframes_payload.get("keyframes") or [{}])[0].get("time") or 0.0,
                "source_path": first_visual.get("path") or (keyframes_payload.get("keyframes") or [{}])[0].get("path"),
                "source_text": visual_summary or ocr_text or "关键帧证据",
            }
        )

    payload = validate_content_assets(payload)
    validate_content_asset_anchors(payload, transcript_payload, keyframes_payload, paths)
    _guard_against_verbatim_copy(payload, transcript_payload)
    write_json(paths.analysis_dir / "content-assets.json", payload)
    return payload


def build_content_assets(
    metadata: Dict[str, Any],
    transcript_payload: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    language: str,
    style: str,
    paths: ProjectPaths,
) -> Dict[str, Any]:
    context = _content_assets_context(metadata, transcript_payload, keyframes_payload, visual_payload, language, style)
    schema = _content_assets_schema()
    payload = llm_client.json_chat(
        [
            {
                "role": "system",
                "content": (
                    "你是严谨的二次创作选题策划师。你的任务不是拆解视频结构，而是从字幕、关键帧和 OCR 中提炼"
                    "可用于原创文章图文的事实锚点、观点方向、读者场景和表达角度；"
                    "metadata 的 description 只用于来源留存，不可把简介中的外链或无关话题当成视频内容。"
                    "不要编造视频没有的信息。每个核心观点尽量绑定字幕时间点或关键帧时间点。"
                    "除 source_evidence 外，所有字段都要用自己的话归纳，不要写成“第几秒出现了什么”的视频复述。"
                    "所有输出用中文，返回严格 JSON。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "根据以下原视频材料生成 content_assets.json。目标是为后续原创图文提供创作底稿："
                    "先理解原视频的信息、情绪、受众和方向，再提炼可二次创作的观点、选题角度、痛点和事实依据。"
                    "不要把最终输出写成视频拆解、逐段复述或字幕摘要；金句可以改写和提炼，不要逐字照搬字幕。"
                    "返回字段必须匹配 schema，不能省略任何字段。\n\n"
                    f"Schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
                    f"Context:\n{json.dumps(context, ensure_ascii=False)}"
                ),
            },
        ],
        step="planning_content",
        temperature=0.2,
    )
    payload = _validate_content_assets_payload(payload, transcript_payload, keyframes_payload, paths)
    payload = _guard_or_rewrite_content_assets(
        payload,
        transcript_payload,
        metadata,
        keyframes_payload,
        visual_payload,
        language,
        style,
        paths,
    )
    payload["source_metadata"] = {
        "url": metadata.get("url"),
        "title": metadata.get("title"),
        "author": metadata.get("author"),
        "video_id": metadata.get("video_id"),
    }
    write_json(paths.analysis_dir / "content-assets.json", payload)
    return payload
