import re
from collections import Counter
from typing import Any, Dict, List, Optional

from app.services.errors import PipelineError
from app.services.platforms import get_platform
from app.services.text_utils import clean_text

MIN_VERBATIM_CHARS = 24
MIN_REWRITE_DEGREE = 0.70
NGRAM_SIZE = 8
HOOK_MAX_CHARS = 100

MECHANICAL_HOOK_PREFIXES = ("本文将", "本文主要", "下面我们", "接下来我们", "今天我们来", "这篇文章将")
CONTRAST_MARKERS = (
    "却",
    "但",
    "没想到",
    "竟然",
    "反而",
    "不是",
    "而是",
    "看似",
    "结果",
    "问题是",
    "真相",
    "明明",
    "偏偏",
    "原来",
    "到底",
    "为什么",
    "如果",
)
GENERIC_HEADINGS = {
    "背景",
    "原因",
    "总结",
    "结论",
    "写在最后",
    "最后",
    "前言",
    "正文",
    "核心观点",
    "具体来说",
    "为什么这么说",
}
ORDERED_HEADING_RE = re.compile(
    r"^(?:[一二三四五六七八九十]+[、.．]|第[一二三四五六七八九十]+[部分章节]|\d{1,2}[、.．、)）])\s*\S+"
)
MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s*\S+")
BOLD_HEADING_RE = re.compile(r"^(?:\*\*|__)[^*_]{1,30}(?:\*\*|__)$")
PERCENT_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*%")
RATIO_RE = re.compile(r"每\s*(\d+(?:\.\d+)?)\s*个[^，。；;]{0,30}?\s*(?:就|中|里)?\s*(?:有|约有|大约有)?\s*1\s*个")
WAN_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*万\s*(人|户|个家庭|个用户|名用户|家庭|用户)")
SOURCE_POPULATION_RE = re.compile(
    r"(?<!\d)(\d[\d,]*(?:\.\d+)?)\s*(万)?\s*(人|户|个家庭|个用户|名用户|家庭|用户)"
)
SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")


def _normalized_chars(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", clean_text(str(value or ""))).lower()


def article_body_length(value: Any) -> int:
    return len(_normalized_chars(value))


def _source_texts(content_assets: Dict[str, Any], transcript_payload: Optional[Dict[str, Any]] = None) -> List[str]:
    texts: List[str] = []
    if transcript_payload:
        for segment in transcript_payload.get("segments", []) or []:
            if isinstance(segment, dict) and segment.get("text"):
                texts.append(str(segment["text"]))
    for item in content_assets.get("source_evidence", []) or []:
        if isinstance(item, dict) and item.get("source_text"):
            texts.append(str(item["source_text"]))
    for point in content_assets.get("core_points", []) or []:
        if not isinstance(point, dict):
            continue
        for evidence in point.get("evidence", []) or []:
            if isinstance(evidence, dict) and evidence.get("text"):
                texts.append(str(evidence["text"]))
    seen = set()
    result = []
    for text in texts:
        normalized = clean_text(text)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def visible_post_text(payload: Dict[str, Any]) -> str:
    chunks: List[str] = []
    for field in ["cover_text", "hook", "body", "publish_suggestion"]:
        if payload.get(field):
            chunks.append(str(payload[field]))
    chunks.extend(str(item) for item in payload.get("titles", []) or [] if item)
    chunks.extend(str(item) for item in payload.get("hashtags", []) or [] if item)
    for item in payload.get("image_plan", []) or []:
        if not isinstance(item, dict):
            continue
        chunks.extend(str(item[field]) for field in ["caption", "content_point"] if item.get(field))
    return "\n".join(chunks)


def find_subheadings(body: str) -> List[Dict[str, Any]]:
    violations = []
    lines = str(body or "").splitlines()
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        plain = re.sub(r"^[\s*_#]+|[\s*_]+$", "", line)
        reason = ""
        if MARKDOWN_HEADING_RE.match(line):
            reason = "markdown_heading"
        elif BOLD_HEADING_RE.match(line):
            reason = "bold_heading"
        elif ORDERED_HEADING_RE.match(line):
            reason = "ordered_heading"
        elif plain in GENERIC_HEADINGS:
            reason = "generic_heading"
        elif (
            len(plain) <= 18
            and index + 1 < len(lines)
            and lines[index + 1].strip()
            and not re.search(r"[。！？!?，,：:；;]$", plain)
            and (index == 0 or not lines[index - 1].strip())
        ):
            reason = "standalone_short_heading"
        if reason:
            violations.append({"line": index + 1, "text": line[:80], "reason": reason})
    return violations


def _longest_common_fragment(source: str, generated: str, minimum: int = MIN_VERBATIM_CHARS) -> str:
    source_norm = _normalized_chars(source)
    generated_norm = _normalized_chars(generated)
    if len(source_norm) < minimum or len(generated_norm) < minimum:
        return ""

    low = minimum
    high = min(len(source_norm), len(generated_norm))
    best = ""
    while low <= high:
        length = (low + high) // 2
        match = ""
        for start in range(0, len(generated_norm) - length + 1):
            candidate = generated_norm[start : start + length]
            if candidate in source_norm:
                match = candidate
                break
        if match:
            best = match
            low = length + 1
        else:
            high = length - 1
    return best


def _ngram_containment(source: str, generated: str, n: int = NGRAM_SIZE) -> float:
    source_norm = _normalized_chars(source)
    generated_norm = _normalized_chars(generated)
    if len(source_norm) < n or len(generated_norm) < n:
        return 0.0
    source_grams = {source_norm[index : index + n] for index in range(len(source_norm) - n + 1)}
    generated_grams = {generated_norm[index : index + n] for index in range(len(generated_norm) - n + 1)}
    if not generated_grams:
        return 0.0
    return len(source_grams & generated_grams) / len(generated_grams)


def _repeated_sentences(text: str) -> List[Dict[str, Any]]:
    sentences = [clean_text(item) for item in SENTENCE_SPLIT_RE.split(str(text or ""))]
    normalized = [_normalized_chars(item) for item in sentences if len(_normalized_chars(item)) >= 12]
    counts = Counter(normalized)
    return [{"text": sentence[:100], "count": count} for sentence, count in counts.items() if count > 1]


def _context(text: str, start: int, end: int, radius: int = 36) -> str:
    return clean_text(text[max(0, start - radius) : min(len(text), end + radius)])


def _population_category(unit: str) -> str:
    if unit in {"户", "个家庭", "家庭"}:
        return "households"
    if unit in {"个用户", "名用户", "用户"}:
        return "users"
    return "people"


def build_data_concretization_report(source_text: str, generated_text: str) -> Dict[str, Any]:
    source_percentages = []
    for match in PERCENT_RE.finditer(source_text):
        value = float(match.group(1))
        suggested_ratio = round(100 / value) if 0 < value <= 100 else None
        source_percentages.append(
            {
                "raw": match.group(0),
                "value": value,
                "source_context": _context(source_text, match.start(), match.end()),
                "suggested_every_x": suggested_ratio,
                "note": "只有原始语境明确特定群体时，才可写成每 X 个该群体约有 1 个。",
            }
        )

    generated_ratios = []
    for match in RATIO_RE.finditer(generated_text):
        every_x = float(match.group(1))
        implied_percent = 100 / every_x if every_x else None
        evidence = None
        if implied_percent is not None:
            candidates = [
                item for item in source_percentages if abs(float(item["value"]) - implied_percent) <= max(0.6, float(item["value"]) * 0.08)
            ]
            evidence = candidates[0] if candidates else None
        generated_ratios.append(
            {
                "expression": match.group(0),
                "every_x": every_x,
                "implied_percent": round(implied_percent, 3) if implied_percent is not None else None,
                "grounded": evidence is not None,
                "source": evidence,
            }
        )

    source_populations = []
    for match in SOURCE_POPULATION_RE.finditer(source_text):
        value = float(match.group(1).replace(",", ""))
        people_equivalent = value * (10000 if match.group(2) else 1)
        source_populations.append(
            {
                "raw": match.group(0),
                "value": value,
                "unit_scale": "wan" if match.group(2) else "one",
                "population_category": _population_category(match.group(3)),
                "people_equivalent": people_equivalent,
                "wan_equivalent": people_equivalent / 10000,
                "source_context": _context(source_text, match.start(), match.end()),
            }
        )

    generated_wan = []
    for match in WAN_RE.finditer(generated_text):
        value = float(match.group(1))
        category = _population_category(match.group(2))
        people_equivalent = value * 10000
        evidence = next(
            (
                item
                for item in source_populations
                if item["population_category"] == category
                and abs(float(item["people_equivalent"]) - people_equivalent) <= max(1.0, people_equivalent * 0.001)
            ),
            None,
        )
        grounded = evidence is not None
        generated_wan.append(
            {
                "expression": match.group(0),
                "value": value,
                "population_category": category,
                "people_equivalent": people_equivalent,
                "grounded": grounded,
                "source": evidence,
                "reason": "equivalent_source_population" if grounded else "source_total_or_exact_value_not_found",
            }
        )

    generated_percentages = []
    source_values = [float(item["value"]) for item in source_percentages]
    for match in PERCENT_RE.finditer(generated_text):
        value = float(match.group(1))
        grounded = any(abs(value - source_value) < 0.001 for source_value in source_values)
        generated_percentages.append({"expression": match.group(0), "value": value, "grounded": grounded})

    ungrounded = [item for item in [*generated_ratios, *generated_wan, *generated_percentages] if not item["grounded"]]
    return {
        "source_percentages": source_percentages,
        "source_population_values": source_populations,
        "generated_ratio_expressions": generated_ratios,
        "generated_population_expressions": generated_wan,
        "generated_percentage_expressions": generated_percentages,
        "ungrounded_numeric_claims": ungrounded,
    }


def evaluate_article_quality(
    payload: Dict[str, Any],
    content_assets: Dict[str, Any],
    transcript_payload: Optional[Dict[str, Any]] = None,
    *,
    platform: str,
    rewrite_count: int = 0,
) -> Dict[str, Any]:
    source_parts = _source_texts(content_assets, transcript_payload)
    source_text = "\n".join(source_parts)
    generated_text = visible_post_text(payload)
    body = str(payload.get("body") or "")
    body_length = article_body_length(body)
    adapter = get_platform(platform)
    hook = clean_text(str(payload.get("hook") or ""))
    headings = find_subheadings(body)
    mechanical = next((prefix for prefix in MECHANICAL_HOOK_PREFIXES if hook.startswith(prefix)), "")
    has_contrast = any(marker in hook for marker in CONTRAST_MARKERS)

    longest = _longest_common_fragment(source_text, generated_text)
    ngram = _ngram_containment(source_text, generated_text)
    generated_length = max(1, len(_normalized_chars(generated_text)))
    longest_ratio = len(longest) / generated_length
    repeated = _repeated_sentences(body)
    estimated_similarity = max(ngram, longest_ratio)
    rewrite_degree = max(0.0, 1.0 - estimated_similarity)
    data_report = build_data_concretization_report(source_text, generated_text)

    violations: List[Dict[str, Any]] = []
    if not hook:
        violations.append({"code": "hook_missing", "field": "hook", "message": "开头钩子不能为空。"})
    if len(hook) > HOOK_MAX_CHARS:
        violations.append(
            {"code": "hook_too_long", "field": "hook", "message": "开头钩子超过 100 个字符。", "length": len(hook)}
        )
    if mechanical:
        violations.append(
            {"code": "mechanical_hook", "field": "hook", "message": "开头使用了机械化引导语。", "matched": mechanical}
        )
    if hook and not has_contrast:
        violations.append(
            {
                "code": "hook_lacks_contrast",
                "field": "hook",
                "message": "开头没有可检测的反差、冲突或悬念结构。",
                "accepted_markers": list(CONTRAST_MARKERS),
            }
        )
    if headings:
        violations.append(
            {"code": "subheading_detected", "field": "body", "message": "正文包含小标题。", "matches": headings}
        )
    if body_length < adapter.min_body_chars:
        violations.append(
            {
                "code": "body_too_short",
                "field": "body",
                "message": f"正文只有 {body_length} 个有效字符，{adapter.name}完成稿至少需要 {adapter.min_body_chars} 个。",
                "actual_chars": body_length,
                "minimum_chars": adapter.min_body_chars,
                "maximum_chars": adapter.max_body_chars,
            }
        )
    if body_length > adapter.max_body_chars:
        violations.append(
            {
                "code": "body_too_long",
                "field": "body",
                "message": f"正文有 {body_length} 个有效字符，超过{adapter.name}完成稿上限 {adapter.max_body_chars} 个。",
                "actual_chars": body_length,
                "minimum_chars": adapter.min_body_chars,
                "maximum_chars": adapter.max_body_chars,
            }
        )
    if longest and len(longest) >= MIN_VERBATIM_CHARS:
        violations.append(
            {
                "code": "verbatim_source_copy_detected",
                "field": "visible_content",
                "message": "可发布内容包含较长的来源原文片段。",
                "matched_fragment": longest[:160],
                "match_chars": len(longest),
            }
        )
    if rewrite_degree < MIN_REWRITE_DEGREE:
        violations.append(
            {
                "code": "rewrite_degree_below_target",
                "field": "visible_content",
                "message": "相对来源的估算改写程度低于 70%。",
                "estimated_rewrite_degree": round(rewrite_degree, 4),
            }
        )
    if repeated:
        violations.append(
            {"code": "repeated_sentence_detected", "field": "body", "message": "正文存在重复句。", "matches": repeated}
        )
    if data_report["ungrounded_numeric_claims"]:
        violations.append(
            {
                "code": "ungrounded_numeric_claim",
                "field": "visible_content",
                "message": "数据具象化表达无法回溯到来源中的百分比、总体或精确人数。",
                "matches": data_report["ungrounded_numeric_claims"],
            }
        )

    return {
        "platform": platform,
        "passed": not violations,
        "policy": {
            "hook_max_chars": HOOK_MAX_CHARS,
            "minimum_rewrite_degree": MIN_REWRITE_DEGREE,
            "verbatim_min_chars": MIN_VERBATIM_CHARS,
            "ngram_size": NGRAM_SIZE,
            "body_min_chars": adapter.min_body_chars,
            "body_max_chars": adapter.max_body_chars,
            "originality_note": "这是可解释的文本改写程度估算，不是版权或平台原创认证。",
        },
        "hook": {"text": hook, "chars": len(hook), "has_contrast_marker": has_contrast, "mechanical_prefix": mechanical or None},
        "body_length": {
            "actual_chars": body_length,
            "minimum_chars": adapter.min_body_chars,
            "maximum_chars": adapter.max_body_chars,
            "within_range": adapter.min_body_chars <= body_length <= adapter.max_body_chars,
        },
        "structure": {"subheadings": headings},
        "similarity": {
            "ngram_containment": round(ngram, 4),
            "longest_common_fragment": longest[:200],
            "longest_common_fragment_chars": len(longest),
            "estimated_similarity": round(estimated_similarity, 4),
            "estimated_rewrite_degree": round(rewrite_degree, 4),
            "repeated_sentences": repeated,
        },
        "data_concretization": data_report,
        "rewrite_count": rewrite_count,
        "violations": violations,
    }


def quality_error(report: Dict[str, Any]) -> PipelineError:
    violations = report.get("violations", []) or []
    primary = violations[0] if violations else {"code": "article_quality_failed", "message": "文章质量校验失败。"}
    code = str(primary.get("code") or "article_quality_failed")
    message = str(primary.get("message") or "文章质量校验失败。")
    nested_details = primary.get("details") if isinstance(primary.get("details"), dict) else {}
    return PipelineError(
        code=code,
        message=message,
        step="validating_content",
        details={
            **nested_details,
            **{key: value for key, value in primary.items() if key not in {"code", "message", "details", "step"}},
            "quality_report": report,
        },
    )
