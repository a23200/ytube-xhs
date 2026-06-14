import os
from pathlib import Path
from typing import Dict

from app.schemas.models import ImageSettingsUpdate
from app.services.config import BASE_DIR, _image_requires_api_key, _int_env, settings
from app.services.llm_client import sanitize_llm_url

ENV_PATH = BASE_DIR / ".env"


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
        if key:
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
    return os.getenv("XHS_IMAGE_REQUIRE_API_KEY") or env_values.get("XHS_IMAGE_REQUIRE_API_KEY") or "auto"


def get_image_settings() -> Dict[str, object]:
    env_values = _parse_env(ENV_PATH)
    return {
        "enabled": settings.image_enabled,
        "base_url": sanitize_llm_url(settings.image_base_url),
        "model": settings.image_model,
        "api_key_configured": bool(settings.image_api_key),
        "api_key_source": "XHS_IMAGE_API_KEY" if settings.image_api_key else None,
        "require_api_key": _current_require_api_key_raw(env_values),
        "auth_required": settings.image_requires_api_key,
        "size": settings.image_size,
        "timeout_ms": settings.image_timeout_ms,
        "env_path": str(ENV_PATH),
        "fallback_renderer": "pillow_template_v1",
    }


def update_image_settings(request: ImageSettingsUpdate) -> Dict[str, object]:
    updates: Dict[str, str] = {}
    if request.enabled is not None:
        updates["XHS_IMAGE_ENABLED"] = "true" if request.enabled else "false"
    if request.base_url is not None:
        updates["XHS_IMAGE_BASE_URL"] = request.base_url.rstrip("/")
    if request.model is not None:
        updates["XHS_IMAGE_MODEL"] = request.model
    if request.require_api_key is not None:
        normalized = request.require_api_key.strip().lower()
        if normalized not in {"auto", "true", "false"}:
            raise ValueError("require_api_key must be auto, true, or false.")
        updates["XHS_IMAGE_REQUIRE_API_KEY"] = normalized
    if request.size is not None:
        updates["XHS_IMAGE_SIZE"] = request.size
    if request.timeout_ms is not None:
        updates["XHS_IMAGE_TIMEOUT_MS"] = str(request.timeout_ms)
    if request.api_key:
        updates["XHS_IMAGE_API_KEY"] = request.api_key

    if updates:
        _write_env_updates(ENV_PATH, updates)
        for key, value in updates.items():
            os.environ[key] = value

    settings.image_enabled = os.getenv("XHS_IMAGE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    settings.image_api_key = os.getenv("XHS_IMAGE_API_KEY")
    settings.image_base_url = os.getenv("XHS_IMAGE_BASE_URL", settings.image_base_url).rstrip("/")
    settings.image_model = os.getenv("XHS_IMAGE_MODEL", settings.image_model)
    settings.image_requires_api_key = _image_requires_api_key(settings.image_base_url)
    settings.image_timeout_ms = _int_env("XHS_IMAGE_TIMEOUT_MS", settings.image_timeout_ms)
    settings.image_size = os.getenv("XHS_IMAGE_SIZE", settings.image_size)
    from app.services.image_client import image_client

    image_client.reload_from_settings()
    return get_image_settings()
