from typing import Any, Dict, Iterable, List

from app.services.errors import PipelineError

IMAGE_PROMPT_REQUIRED_KEYWORDS = ["构图", "主体", "背景", "色调", "留白"]
NEGATIVE_PROMPT_COPY_TERMS = ["复刻", "截图"]
IMAGE_PROMPT_FORBIDDEN_COPY_TERMS = ["直接复刻", "复刻截图", "照搬截图", "还原截图", "原样截图", "复制截图"]


def _missing(payload: Dict[str, Any], fields: Iterable[str]) -> List[str]:
    return [field for field in fields if field not in payload or payload[field] in (None, "", [])]


def _missing_keys(payload: Dict[str, Any], fields: Iterable[str]) -> List[str]:
    return [field for field in fields if field not in payload]


def _ensure_list(payload: Dict[str, Any], field: str, step: str, artifact: str, min_items: int = 1) -> None:
    value = payload.get(field)
    if not isinstance(value, list) or len(value) < min_items:
        raise PipelineError(
            code="llm_contract_invalid",
            message=f"LLM output for {artifact} must contain a non-empty list field: {field}.",
            step=step,
            details={"artifact": artifact, "field": field, "value_type": type(value).__name__},
        )


def _ensure_required(payload: Dict[str, Any], fields: Iterable[str], step: str, artifact: str) -> None:
    missing = _missing(payload, fields)
    if missing:
        raise PipelineError(
            code="llm_contract_invalid",
            message=f"LLM output for {artifact} is missing required fields.",
            step=step,
            details={"artifact": artifact, "missing_fields": missing},
        )


def _ensure_keys(payload: Dict[str, Any], fields: Iterable[str], step: str, artifact: str) -> None:
    missing = _missing_keys(payload, fields)
    if missing:
        raise PipelineError(
            code="llm_contract_invalid",
            message=f"LLM output for {artifact} is missing required fields.",
            step=step,
            details={"artifact": artifact, "missing_fields": missing},
        )


def _ensure_object(value: Any, step: str, artifact: str, field: str, index: int) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise PipelineError(
            code="llm_contract_invalid",
            message=f"Each item in {field} for {artifact} must be an object.",
            step=step,
            details={"artifact": artifact, "field": field, "index": index, "value_type": type(value).__name__},
        )
    return value


def _ensure_item_required(item: Dict[str, Any], fields: Iterable[str], step: str, artifact: str, field: str, index: int) -> None:
    missing = _missing(item, fields)
    if missing:
        raise PipelineError(
            code="llm_contract_invalid",
            message=f"Item {index} in {field} for {artifact} is missing required fields.",
            step=step,
            details={"artifact": artifact, "field": field, "index": index, "missing_fields": missing},
        )


def _ensure_item_keys(item: Dict[str, Any], fields: Iterable[str], step: str, artifact: str, field: str, index: int) -> None:
    missing = _missing_keys(item, fields)
    if missing:
        raise PipelineError(
            code="llm_contract_invalid",
            message=f"Item {index} in {field} for {artifact} is missing required fields.",
            step=step,
            details={"artifact": artifact, "field": field, "index": index, "missing_fields": missing},
        )


def _ensure_object_list_items(
    payload: Dict[str, Any],
    field: str,
    item_fields: Iterable[str],
    step: str,
    artifact: str,
) -> None:
    for index, value in enumerate(payload.get(field, [])):
        item = _ensure_object(value, step, artifact, field, index)
        _ensure_item_required(item, item_fields, step, artifact, field, index)


def _has_source_anchor(item: Dict[str, Any]) -> bool:
    return item.get("time") not in (None, "") or item.get("frame_path") not in (None, "") or item.get("path") not in (None, "")


def validate_content_assets(payload: Dict[str, Any]) -> Dict[str, Any]:
    artifact = "content-assets.json"
    step = "planning_content"
    _ensure_required(
        payload,
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
        step,
        artifact,
    )
    for field in [
        "core_points",
        "golden_quotes",
        "chapters",
        "steps",
        "audience",
        "pain_points",
        "xiaohongshu_angles",
        "source_evidence",
    ]:
        _ensure_list(payload, field, step, artifact)
    _ensure_object_list_items(payload, "core_points", ["point", "evidence"], step, artifact)
    _ensure_object_list_items(payload, "golden_quotes", ["quote", "rewrite_note"], step, artifact)
    _ensure_object_list_items(payload, "chapters", ["title", "summary"], step, artifact)
    _ensure_object_list_items(payload, "steps", ["step"], step, artifact)
    _ensure_object_list_items(payload, "source_evidence", ["claim", "source_type", "source_text"], step, artifact)
    for index, point in enumerate(payload["core_points"]):
        evidence = point.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise PipelineError(
                code="llm_contract_invalid",
                message="Each core point must include at least one source evidence item.",
                step=step,
                details={"artifact": artifact, "field": "core_points", "index": index},
            )
        for evidence_index, evidence_item in enumerate(evidence):
            evidence_object = _ensure_object(evidence_item, step, artifact, "core_points.evidence", evidence_index)
            _ensure_item_required(
                evidence_object,
                ["type", "text"],
                step,
                artifact,
                "core_points.evidence",
                evidence_index,
            )
            if not _has_source_anchor(evidence_object):
                raise PipelineError(
                    code="llm_contract_invalid",
                    message="Each core point evidence item must include a source time or frame path.",
                    step=step,
                    details={
                        "artifact": artifact,
                        "field": "core_points.evidence",
                        "index": index,
                        "evidence_index": evidence_index,
                    },
                )
    for index, item in enumerate(payload["source_evidence"]):
        source_item = _ensure_object(item, step, artifact, "source_evidence", index)
        if source_item.get("time") in (None, "") and source_item.get("source_path") in (None, ""):
            raise PipelineError(
                code="llm_contract_invalid",
                message="Each source_evidence item must include a source time or source path.",
                step=step,
                details={"artifact": artifact, "field": "source_evidence", "index": index},
            )
    return payload


def validate_xhs_post(payload: Dict[str, Any], require_frame_anchors: bool = True) -> Dict[str, Any]:
    artifact = "xiaohongshu-post.json"
    step = "writing_xhs"
    _ensure_required(
        payload,
        [
            "content_type",
            "target_audience",
            "titles",
            "cover_text",
            "hook",
            "body",
            "image_plan",
            "hashtags",
            "publish_suggestion",
        ],
        step,
        artifact,
    )
    _ensure_list(payload, "target_audience", step, artifact)
    _ensure_list(payload, "titles", step, artifact, min_items=5)
    _ensure_list(payload, "image_plan", step, artifact)
    _ensure_list(payload, "hashtags", step, artifact)
    _ensure_object_list_items(
        payload,
        "image_plan",
        ["page", "role", "caption", "content_point"],
        step,
        artifact,
    )
    if require_frame_anchors:
        for index, item in enumerate(payload["image_plan"]):
            if item.get("source_frame_time") in (None, "") and item.get("source_frame_path") in (None, ""):
                raise PipelineError(
                    code="llm_contract_invalid",
                    message="Each image_plan item must reference a source frame time or source frame path.",
                    step=step,
                    details={"artifact": artifact, "field": "image_plan", "index": index},
                )
    return payload


def validate_image_prompts(payload: Dict[str, Any]) -> Dict[str, Any]:
    artifact = "image-prompts.json"
    step = "writing_xhs"
    _ensure_keys(payload, ["image_prompts"], step, artifact)
    _ensure_list(payload, "image_prompts", step, artifact)
    for index, item in enumerate(payload["image_prompts"]):
        if not isinstance(item, dict):
            raise PipelineError(
                code="llm_contract_invalid",
                message="Each image prompt item must be an object.",
                step=step,
                details={"artifact": artifact, "index": index, "value_type": type(item).__name__},
            )
        _ensure_item_keys(
            item,
            [
                "page",
                "role",
                "caption",
                "source_frame_time",
                "visual_reference",
                "image_prompt",
                "negative_prompt",
            ],
            step,
            artifact,
            "image_prompts",
            index,
        )
        _ensure_item_required(
            item,
            [
                "page",
                "role",
                "caption",
                "visual_reference",
                "image_prompt",
                "negative_prompt",
            ],
            step,
            artifact,
            "image_prompts",
            index,
        )
        image_prompt = str(item.get("image_prompt") or "")
        missing_keywords = [keyword for keyword in IMAGE_PROMPT_REQUIRED_KEYWORDS if keyword not in image_prompt]
        if missing_keywords:
            raise PipelineError(
                code="llm_contract_invalid",
                message="Image prompt must describe composition, subject, background, tone, and text whitespace.",
                step=step,
                details={
                    "artifact": artifact,
                    "field": "image_prompts",
                    "index": index,
                    "missing_keywords": missing_keywords,
                },
            )
        forbidden_copy_terms = [term for term in IMAGE_PROMPT_FORBIDDEN_COPY_TERMS if term in image_prompt]
        if forbidden_copy_terms:
            raise PipelineError(
                code="llm_contract_invalid",
                message="Image prompt must transform source frames into original visuals, not request screenshot recreation.",
                step=step,
                details={
                    "artifact": artifact,
                    "field": "image_prompts",
                    "index": index,
                    "forbidden_terms": forbidden_copy_terms,
                },
            )
        negative_prompt = str(item.get("negative_prompt") or "")
        if not any(term in negative_prompt for term in NEGATIVE_PROMPT_COPY_TERMS):
            raise PipelineError(
                code="llm_contract_invalid",
                message="Negative prompt must explicitly avoid recreating source screenshots.",
                step=step,
                details={"artifact": artifact, "field": "image_prompts", "index": index},
            )
    return payload
