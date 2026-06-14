import json
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.services.config import settings
from app.services.errors import PipelineError

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
TRIM_MARKER = "\n...[trimmed]...\n"
SENSITIVE_QUERY_KEYS = {"api_key", "apikey", "key", "token", "access_token", "authorization", "auth", "secret"}
SENSITIVE_QUERY_RE = re.compile(
    r"([?&](?:api_key|apikey|key|token|access_token|authorization|auth|secret)=)[^&\s]+",
    re.IGNORECASE,
)


def sanitize_llm_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not parts.query:
        return value
    sanitized_query = urlencode(
        [
            (key, "[redacted]" if key.lower() in SENSITIVE_QUERY_KEYS else item_value)
            for key, item_value in parse_qsl(parts.query, keep_blank_values=True)
        ],
        doseq=True,
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, sanitized_query, parts.fragment))


class LLMClient:
    def __init__(self) -> None:
        self.reload_from_settings()

    def reload_from_settings(self) -> None:
        self.api_key = settings.llm_api_key
        self.base_url = settings.llm_base_url
        self.model = settings.llm_model
        self.requires_api_key = settings.llm_requires_api_key
        self.timeout = settings.llm_timeout_ms / 1000.0
        self.max_chars = settings.llm_max_chars
        self.max_tokens = settings.llm_max_tokens

    def ensure_available(self, step: str) -> None:
        if self.requires_api_key and not self.api_key:
            raise PipelineError(
                code="llm_unavailable",
                message=(
                    "LLM API key is not configured. Set BUSINESS_LLM_API_KEY or XHS_LLM_API_KEY, "
                    "plus XHS_LLM_BASE_URL and XHS_LLM_MODEL for an OpenAI-compatible endpoint. "
                    "For local OpenAI-compatible endpoints that do not need authentication, set "
                    "XHS_LLM_REQUIRE_API_KEY=false."
                ),
                step=step,
            )
        try:
            import httpx  # noqa: F401
        except Exception as exc:
            raise PipelineError(
                code="missing_dependency",
                message="httpx is not installed, so the OpenAI-compatible LLM provider cannot run.",
                step=step,
                details={"dependency": "httpx"},
            ) from exc

    def chat_text(
        self,
        messages: List[Dict[str, str]],
        step: str,
        temperature: float = 0.3,
        force_json: bool = False,
        attempts: int = 3,
        timeout_seconds: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        self.ensure_available(step)
        import httpx

        trimmed_messages = self._trim_messages(messages)
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": trimmed_messages,
            "temperature": temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
        }
        if force_json:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error: Optional[str] = None
        request_timeout = timeout_seconds if timeout_seconds is not None else self.timeout
        for attempt in range(max(1, attempts)):
            try:
                response = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=request_timeout,
                )
                if response.status_code >= 400 and force_json and "response_format" in payload:
                    payload.pop("response_format", None)
                    response = httpx.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=request_timeout,
                    )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
            except Exception as exc:
                last_error = self._sanitize_error(str(exc))
                time.sleep(0.8 * (attempt + 1))

        raise PipelineError(
            code="llm_request_failed",
            message="OpenAI-compatible chat completion request failed after retries.",
            step=step,
            details={"error": last_error, "base_url": self.safe_base_url, "model": self.model},
        )

    def json_chat(
        self,
        messages: List[Dict[str, str]],
        step: str,
        temperature: float = 0.2,
        attempts: int = 3,
        timeout_seconds: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        content = self.chat_text(
            messages,
            step=step,
            temperature=temperature,
            force_json=True,
            attempts=attempts,
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
        )
        try:
            return self._parse_json(content)
        except ValueError:
            repaired = self.chat_text(
                [
                    {
                        "role": "system",
                        "content": "You repair malformed JSON. Return valid JSON only, no prose.",
                    },
                    {
                        "role": "user",
                        "content": (
                            "Repair this response into one valid JSON object without adding facts:\n\n"
                            + content[: self.max_chars]
                        ),
                    },
                ],
                step=step,
                temperature=0.0,
                force_json=True,
                attempts=attempts,
                timeout_seconds=timeout_seconds,
                max_tokens=max_tokens,
            )
            try:
                return self._parse_json(repaired)
            except ValueError as exc:
                raise PipelineError(
                    code="llm_json_parse_failed",
                    message="LLM response could not be parsed as JSON, and repair failed.",
                    step=step,
                    details={
                        "raw": self._safe_error_excerpt(content),
                        "repaired": self._safe_error_excerpt(repaired),
                    },
                ) from exc

    def self_test(self) -> Dict[str, Any]:
        try:
            payload = self.json_chat(
                [
                    {
                        "role": "system",
                        "content": "Return JSON only.",
                    },
                    {
                        "role": "user",
                        "content": 'Return exactly this JSON shape with ok true: {"ok": true}',
                    },
                ],
                step="llm_self_test",
                temperature=0.0,
            )
        except PipelineError as exc:
            return {
                "ok": False,
                "error": exc.to_dict(),
                "base_url": self.safe_base_url,
                "model": self.model,
            }
        return {
            "ok": bool(payload.get("ok")),
            "response": payload,
            "base_url": self.safe_base_url,
            "model": self.model,
            "auth_required": self.requires_api_key,
        }

    @property
    def safe_base_url(self) -> str:
        return sanitize_llm_url(self.base_url)

    def _trim_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        budget = max(0, self.max_chars)
        if budget <= 0:
            return []

        selected: Dict[int, Dict[str, str]] = {}
        used = 0
        system_indices = [index for index, message in enumerate(messages) if message.get("role") == "system"]

        for index in system_indices:
            remaining = budget - used
            if remaining <= 0:
                break
            content = str(messages[index].get("content", ""))
            clipped = self._clip_content(content, remaining)
            selected[index] = {**messages[index], "content": clipped}
            used += len(clipped)
            if len(clipped) < len(content):
                break

        remaining = budget - used
        if remaining <= 0:
            return [selected[index] for index in sorted(selected)]

        for index in range(len(messages) - 1, -1, -1):
            if index in selected or messages[index].get("role") == "system":
                continue
            content = str(messages[index].get("content", ""))
            if len(content) > remaining:
                selected[index] = {**messages[index], "content": self._clip_content(content, remaining)}
                break
            selected[index] = {**messages[index], "content": content}
            remaining -= len(content)
            if remaining <= 0:
                break

        return [selected[index] for index in sorted(selected)]

    def _clip_content(self, content: str, limit: int) -> str:
        if limit <= 0:
            return ""
        if len(content) <= limit:
            return content
        if limit <= len(TRIM_MARKER) + 2:
            return content[:limit]
        remaining = limit - len(TRIM_MARKER)
        head_length = max(1, remaining // 2)
        tail_length = max(1, remaining - head_length)
        return content[:head_length] + TRIM_MARKER + content[-tail_length:]

    def _parse_json(self, content: str) -> Dict[str, Any]:
        content = content.strip()
        block = JSON_BLOCK_RE.search(content)
        if block:
            content = block.group(1).strip()
        if not content.startswith("{"):
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                content = content[start : end + 1]
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("Expected a JSON object.")
        return parsed

    def _sanitize_error(self, message: str) -> str:
        if self.api_key:
            message = message.replace(self.api_key, "[redacted]")
        message = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", message)
        return SENSITIVE_QUERY_RE.sub(r"\1[redacted]", message)

    def _safe_error_excerpt(self, value: str, limit: int = 2000) -> str:
        return self._sanitize_error(str(value)[:limit])


llm_client = LLMClient()
