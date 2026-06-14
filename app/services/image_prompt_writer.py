import json
from typing import Any, Dict, List, Optional

from app.services.contracts import (
    IMAGE_PROMPT_FORBIDDEN_COPY_TERMS,
    NEGATIVE_PROMPT_COPY_TERMS,
    validate_image_prompts,
)
from app.services.errors import PipelineError
from app.services.llm_client import llm_client
from app.services.runtime_store import ProjectPaths, write_json
from app.services.source_anchors import validate_image_prompt_anchors

PLATFORM_PROMPT_META = {
    "xhs": {
        "name": "小红书",
        "filename": "image-prompts.json",
        "page_label": "小红书图文页",
        "fallback_prompt": "原创小红书图文视觉",
        "system": "你是小红书图文视觉提示词设计师。不能要求直接复刻视频截图；要把参考帧转化为原创图文视觉。",
    },
    "toutiao": {
        "name": "今日头条",
        "filename": "toutiao-image-prompts.json",
        "page_label": "今日头条资讯图文页",
        "fallback_prompt": "原创今日头条资讯图文视觉",
        "system": "你是今日头条资讯图文视觉提示词设计师。不能要求直接复刻视频截图；要把参考帧转化为原创信息流图文视觉。",
    },
}


def _as_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _safe_text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _clip(value: Any, limit: int = 160) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _valid_prompt_items(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    required = ["page", "role", "caption", "source_frame_time", "visual_reference", "image_prompt", "negative_prompt"]
    for item in value:
        if not isinstance(item, dict):
            return False
        if any(field not in item for field in required):
            return False
    return True


def _page_number(value: Any, fallback: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return fallback


def _without_forbidden_copy_terms(text: str) -> str:
    repaired = text
    for term in IMAGE_PROMPT_FORBIDDEN_COPY_TERMS:
        repaired = repaired.replace(term, "将参考帧转化为原创视觉")
    return repaired


def _merge_prompt_item(fallback: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(fallback)
    for key, value in item.items():
        if value not in (None, "", []):
            merged[key] = value
    return merged


def _repair_prompt_text(item: Dict[str, Any], plan_item: Dict[str, Any], platform: str = "xhs") -> str:
    meta = PLATFORM_PROMPT_META.get(platform, PLATFORM_PROMPT_META["xhs"])
    caption = _safe_text(item.get("caption") or plan_item.get("caption"), "本页要点")
    content_point = _safe_text(plan_item.get("content_point") or item.get("visual_reference"), caption)
    prompt = _without_forbidden_copy_terms(_safe_text(item.get("image_prompt")))
    if not prompt:
        prompt = f"{meta['fallback_prompt']}，用画面表达“{caption}”。"

    required_segments = {
        "构图": f"构图：竖版 3:4 {meta['page_label']}，上方标题区与下方信息区分明，围绕“{caption}”组织画面。",
        "主体": f"主体：以“{content_point}”为核心，使用原创插画、信息卡片或抽象场景表达。",
        "背景": "背景：干净浅色背景，可加入抽象视频帧、时间轴、便签或场景元素，但不复制来源画面。",
        "色调": "色调：明亮自然、低饱和、适合阅读，重点信息可用少量强调色。",
        "留白": "文字留白区：顶部、右侧或卡片内部预留清晰留白，用于标题、正文和页码。",
    }
    missing_segments = [segment for keyword, segment in required_segments.items() if keyword not in prompt]
    if missing_segments:
        prompt = prompt.rstrip("。；; ") + "。" + " ".join(missing_segments)
    return prompt


def _repair_image_prompts(
    payload: Dict[str, Any],
    xhs_post: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    platform: str = "xhs",
) -> Dict[str, Any]:
    fallback = _fallback_image_prompts(xhs_post, keyframes_payload, platform=platform)
    fallback_items = [item for item in fallback.get("image_prompts", []) if isinstance(item, dict)]
    llm_items = [item for item in payload.get("image_prompts", []) or [] if isinstance(item, dict)]
    llm_by_page: Dict[int, Dict[str, Any]] = {
        _page_number(item.get("page"), index + 1): item for index, item in enumerate(llm_items)
    }
    plan_by_page: Dict[int, Dict[str, Any]] = {
        _page_number(item.get("page"), index + 1): item
        for index, item in enumerate(xhs_post.get("image_plan", []) or [])
        if isinstance(item, dict)
    }

    repaired_items: List[Dict[str, Any]] = []
    if fallback_items:
        for index, fallback_item in enumerate(fallback_items):
            page = _page_number(fallback_item.get("page"), index + 1)
            item = _merge_prompt_item(fallback_item, llm_by_page.get(page, {}))
            plan_item = plan_by_page.get(page, {})
            item["image_prompt"] = _repair_prompt_text(item, plan_item, platform=platform)
            negative_prompt = _safe_text(item.get("negative_prompt"))
            if not negative_prompt:
                negative_prompt = "不要直接复刻截图，不要低清、过曝、杂乱文字或侵权 logo。"
            if not any(term in negative_prompt for term in NEGATIVE_PROMPT_COPY_TERMS):
                negative_prompt = negative_prompt.rstrip("。；; ") + "，不要直接复刻截图。"
            item["negative_prompt"] = negative_prompt
            repaired_items.append(item)
    else:
        for index, item in enumerate(llm_items):
            plan_item = plan_by_page.get(_page_number(item.get("page"), index + 1), {})
            item = dict(item)
            item["image_prompt"] = _repair_prompt_text(item, plan_item, platform=platform)
            negative_prompt = _safe_text(item.get("negative_prompt"), "不要直接复刻截图。")
            if not any(term in negative_prompt for term in NEGATIVE_PROMPT_COPY_TERMS):
                negative_prompt = negative_prompt.rstrip("。；; ") + "，不要直接复刻截图。"
            item["negative_prompt"] = negative_prompt
            repaired_items.append(item)

    return {"image_prompts": repaired_items}


def _fallback_image_prompts(xhs_post: Dict[str, Any], keyframes_payload: Dict[str, Any], platform: str = "xhs") -> Dict[str, Any]:
    meta = PLATFORM_PROMPT_META.get(platform, PLATFORM_PROMPT_META["xhs"])
    frames = _keyframes(keyframes_payload)
    image_plan = [item for item in xhs_post.get("image_plan", []) or [] if isinstance(item, dict)]
    prompts = []
    for index, item in enumerate(image_plan):
        frame = _frame_for_index(frames, index)
        source_time = _as_float(item.get("source_frame_time"))
        source_path = str(item.get("source_frame_path") or frame.get("path") or "")
        if source_time is None:
            source_time = frame.get("time")
        role = _safe_text(item.get("role"), "point")
        caption = _safe_text(item.get("caption"), f"第 {index + 1} 页")
        content_point = _safe_text(item.get("content_point"), "基于原始信息二次创作出的文章观点")
        prompts.append(
            {
                "page": item.get("page") or index + 1,
                "role": role,
                "caption": caption,
                "source_frame_time": source_time,
                "source_frame_path": source_path or None,
                "visual_reference": (
                    f"参考关键帧时间 {source_time}s；用于表达“{content_point}”。"
                    if source_time is not None or source_path
                    else f"无可用关键帧；基于文章要点生成原创文字视觉，用于表达“{content_point}”。"
                ),
                "image_prompt": (
                    f"{meta['fallback_prompt']}，构图为上方标题、下方三到四个信息点，主体围绕“{caption}”展开，"
                    "背景保持干净的信息图质感，色调明亮自然，画面右侧或上方预留文字留白区。"
                ),
                "negative_prompt": "不要直接复刻视频截图，不要照搬画面中的 logo 或杂乱文字，不要低清、过曝、过度锐化。",
            }
        )
    if not prompts and frames:
        frame = frames[0]
        prompts.append(
            {
                "page": 1,
                "role": "cover",
                "caption": "原创观点封面",
                "source_frame_time": frame.get("time"),
                "source_frame_path": frame.get("path"),
                "visual_reference": f"参考关键帧时间 {frame.get('time')}s；用于生成原创文章封面。",
                "image_prompt": f"{meta['fallback_prompt']}封面，构图为主体居中、标题在上方，主体清晰，背景简洁，色调明亮，四周留白充足。",
                "negative_prompt": "不要直接复刻截图，不要低清，不要杂乱文字。",
            }
        )
    return {"image_prompts": prompts}


def _normalize_image_prompts(
    payload: Dict[str, Any],
    xhs_post: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    platform: str = "xhs",
) -> Dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    if _valid_prompt_items(payload.get("image_prompts")):
        return payload
    return _fallback_image_prompts(xhs_post, keyframes_payload, platform=platform)


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
                "reason": _clip(frame.get("reason"), 100),
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
                "visual_summary": _clip(frame.get("visual_summary"), 120),
            }
        )
    return {
        "ocr_provider": visual_payload.get("ocr_provider"),
        "warnings": visual_payload.get("warnings", []),
        "frames": frames[:12],
    }


def write_image_prompts(
    xhs_post: Dict[str, Any],
    keyframes_payload: Dict[str, Any],
    visual_payload: Dict[str, Any],
    paths: ProjectPaths,
    platform: str = "xhs",
) -> Dict[str, Any]:
    meta = PLATFORM_PROMPT_META.get(platform, PLATFORM_PROMPT_META["xhs"])
    schema = {
        "image_prompts": [
            {
                "page": "number",
                "role": "string",
                "caption": "string",
                "source_frame_time": "number|null",
                "visual_reference": "string",
                "image_prompt": f"string, 原创{meta['name']}图文视觉，包含构图、主体、背景、色调、文字留白区",
                "negative_prompt": "string, 避免直接复刻截图、侵权 logo、低清、杂乱文字等",
            }
        ]
    }
    context = {
        "post_image_plan": xhs_post.get("image_plan", []),
        "keyframes": _compact_keyframes_for_prompt(keyframes_payload),
        "visual_analysis": _compact_visual_for_prompt(visual_payload),
        "platform": meta["name"],
    }
    try:
        payload = llm_client.json_chat(
            [
                {
                    "role": "system",
                    "content": (
                        f"{meta['system']}"
                        "每张图说明构图、主体、背景、色调、文字留白区。返回严格 JSON。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "为 image_plan 生成图片提示词。若 source_frame_time 或 source_frame_path 存在，必须保留为视觉参考来源；"
                        "若二者为 null，说明这是纯字幕/文字路线，不要编造关键帧时间或截图路径。"
                        "返回字段必须匹配 schema，不能省略任何字段。"
                        f"\n\nSchema:\n{json.dumps(schema, ensure_ascii=False)}"
                        f"\n\nContext:\n{json.dumps(context, ensure_ascii=False)}"
                    ),
                },
            ],
            step="writing_xhs",
            temperature=0.35,
            attempts=1,
            timeout_seconds=20,
            max_tokens=4000,
        )
    except PipelineError:
        payload = {}
    payload = _normalize_image_prompts(payload, xhs_post, keyframes_payload, platform=platform)
    payload = _repair_image_prompts(payload, xhs_post, keyframes_payload, platform=platform)
    payload = validate_image_prompts(payload)
    validate_image_prompt_anchors(payload, keyframes_payload, paths)
    write_json(paths.analysis_dir / str(meta["filename"]), payload)
    return payload
