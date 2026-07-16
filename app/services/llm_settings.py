import os
from pathlib import Path
from typing import Dict

from app.schemas.models import LLMSettingsUpdate
from app.services.config import BASE_DIR, settings
from app.services.llm_client import llm_client, sanitize_llm_url

ENV_PATH = BASE_DIR / ".env"
LLM_ENV_KEYS = {
    "XHS_LLM_BASE_URL",
    "XHS_LLM_MODEL",
    "XHS_LLM_API_KEY",
    "XHS_LLM_REQUIRE_API_KEY",
    "XHS_LLM_MAX_TOKENS",
    "XHS_LLM_TIMEOUT_MS",
    "XHS_LLM_MAX_CHARS",
    "XHS_LLM_RETRY_ATTEMPTS",
}


def _parse_env(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def _quote_env_value(value: str) -> str:
    if value == "":
        return ""
    if any(char.isspace() for char in value) or any(char in value for char in ['"', "#", "'"]):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _write_env_updates(path: Path, updates: Dict[str, str]) -> None:
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen = set()
    lines = []
    for raw_line in existing_lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            lines.append(raw_line)
            continue
        key = raw_line.split("=", 1)[0].strip()
        if key in updates:
            lines.append(f"{key}={_quote_env_value(updates[key])}")
            seen.add(key)
        else:
            lines.append(raw_line)
    if updates:
        if lines and lines[-1].strip():
            lines.append("")
        for key in sorted(updates):
            if key not in seen:
                lines.append(f"{key}={_quote_env_value(updates[key])}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _current_require_api_key_raw(env_values: Dict[str, str]) -> str:
    return os.getenv("XHS_LLM_REQUIRE_API_KEY") or env_values.get("XHS_LLM_REQUIRE_API_KEY") or "auto"


def get_llm_settings() -> Dict[str, object]:
    env_values = _parse_env(ENV_PATH)
    return {
        "base_url": sanitize_llm_url(settings.llm_base_url),
        "model": settings.llm_model,
        "api_key_configured": bool(settings.llm_api_key),
        "api_key_source": "BUSINESS_LLM_API_KEY"
        if os.getenv("BUSINESS_LLM_API_KEY")
        else ("XHS_LLM_API_KEY" if settings.llm_api_key else None),
        "require_api_key": _current_require_api_key_raw(env_values),
        "auth_required": settings.llm_requires_api_key,
        "max_tokens": settings.llm_max_tokens,
        "timeout_ms": settings.llm_timeout_ms,
        "max_chars": settings.llm_max_chars,
        "retry_attempts": settings.llm_retry_attempts,
        "env_path": str(ENV_PATH),
    }


def update_llm_settings(request: LLMSettingsUpdate) -> Dict[str, object]:
    updates: Dict[str, str] = {}
    if request.base_url is not None:
        updates["XHS_LLM_BASE_URL"] = request.base_url.rstrip("/")
    if request.model is not None:
        updates["XHS_LLM_MODEL"] = request.model
    if request.require_api_key is not None:
        normalized = request.require_api_key.strip().lower()
        if normalized not in {"auto", "true", "false"}:
            raise ValueError("require_api_key must be auto, true, or false.")
        updates["XHS_LLM_REQUIRE_API_KEY"] = normalized
    if request.max_tokens is not None:
        updates["XHS_LLM_MAX_TOKENS"] = str(request.max_tokens)
    if request.timeout_ms is not None:
        updates["XHS_LLM_TIMEOUT_MS"] = str(request.timeout_ms)
    if request.max_chars is not None:
        updates["XHS_LLM_MAX_CHARS"] = str(request.max_chars)
    if request.retry_attempts is not None:
        updates["XHS_LLM_RETRY_ATTEMPTS"] = str(request.retry_attempts)
    if request.api_key:
        updates["XHS_LLM_API_KEY"] = request.api_key

    if updates:
        _write_env_updates(ENV_PATH, updates)
        for key, value in updates.items():
            os.environ[key] = value

    if "XHS_LLM_API_KEY" in updates and os.getenv("BUSINESS_LLM_API_KEY"):
        os.environ.pop("BUSINESS_LLM_API_KEY", None)

    settings.llm_api_key = os.getenv("BUSINESS_LLM_API_KEY") or os.getenv("XHS_LLM_API_KEY")
    settings.llm_base_url = os.getenv("XHS_LLM_BASE_URL", settings.llm_base_url).rstrip("/")
    settings.llm_model = os.getenv("XHS_LLM_MODEL", settings.llm_model)
    from app.services.config import _int_env, _llm_requires_api_key

    settings.llm_requires_api_key = _llm_requires_api_key(settings.llm_base_url)
    settings.llm_timeout_ms = _int_env("XHS_LLM_TIMEOUT_MS", settings.llm_timeout_ms)
    settings.llm_max_chars = _int_env("XHS_LLM_MAX_CHARS", settings.llm_max_chars)
    settings.llm_max_tokens = _int_env("XHS_LLM_MAX_TOKENS", settings.llm_max_tokens)
    settings.llm_retry_attempts = min(10, max(1, _int_env("XHS_LLM_RETRY_ATTEMPTS", settings.llm_retry_attempts)))
    llm_client.reload_from_settings()
    return get_llm_settings()
