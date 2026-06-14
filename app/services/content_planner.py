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


def build_content_assets(
    metadata: Dict[str, Any],
    transcript_payload: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    language: str,
    style: str,
    paths: ProjectPaths,
) -> Dict[str, Any]:
    context = {
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
    schema = {
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
    payload = _normalize_content_assets(payload)
    payload = validate_content_assets(payload)
    validate_content_asset_anchors(payload, transcript_payload, keyframes_payload, paths)
    _guard_against_verbatim_copy(payload, transcript_payload)
    payload["source_metadata"] = {
        "url": metadata.get("url"),
        "title": metadata.get("title"),
        "author": metadata.get("author"),
        "video_id": metadata.get("video_id"),
    }
    write_json(paths.analysis_dir / "content-assets.json", payload)
    return payload
