import base64
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.services.config import settings
from app.services.errors import PipelineError
from app.services.llm_client import sanitize_llm_url


class ImageClient:
    def __init__(self) -> None:
        self.reload_from_settings()

    def reload_from_settings(self) -> None:
        self.enabled = settings.image_enabled
        self.api_key = settings.image_api_key
        self.base_url = settings.image_base_url
        self.model = settings.image_model
        self.requires_api_key = settings.image_requires_api_key
        self.timeout = settings.image_timeout_ms / 1000.0
        self.size = settings.image_size

    def ensure_available(self, step: str) -> None:
        if not self.enabled:
            raise PipelineError(
                code="image_api_disabled",
                message="External image API is disabled. Enable XHS_IMAGE_ENABLED to use the configured provider.",
                step=step,
            )
        if not self.base_url or not self.model:
            raise PipelineError(
                code="image_api_unconfigured",
                message="Image API base_url or model is not configured.",
                step=step,
            )
        if self.requires_api_key and not self.api_key:
            raise PipelineError(
                code="image_api_unavailable",
                message="Image API key is not configured. Set XHS_IMAGE_API_KEY or disable key requirement for local endpoints.",
                step=step,
            )
        try:
            import httpx  # noqa: F401
            from PIL import Image  # noqa: F401
        except Exception as exc:
            raise PipelineError(
                code="missing_dependency",
                message="httpx and Pillow are required for the external image API provider.",
                step=step,
                details={"dependency": "httpx/Pillow"},
            ) from exc

    def generate_to_file(
        self,
        prompt: str,
        output_path: Path,
        step: str = "rendering_cards",
        *,
        attempts: int = 2,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        self.ensure_available(step)
        import httpx
        from PIL import Image

        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "size": self.size,
            "n": 1,
            "response_format": "b64_json",
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error: Optional[str] = None
        request_timeout = timeout_seconds if timeout_seconds is not None else self.timeout
        for attempt in range(max(1, attempts)):
            try:
                response = httpx.post(
                    f"{self.base_url}/images/generations",
                    headers=headers,
                    json=payload,
                    timeout=request_timeout,
                )
                response.raise_for_status()
                data = response.json()
                item = (data.get("data") or [{}])[0]
                b64_json = item.get("b64_json")
                if b64_json:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(base64.b64decode(b64_json))
                elif item.get("url"):
                    image_response = httpx.get(item["url"], timeout=request_timeout)
                    image_response.raise_for_status()
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(image_response.content)
                else:
                    raise ValueError("Image API response did not contain b64_json or url.")

                with Image.open(output_path) as image:
                    image.verify()
                return {
                    "provider": "openai_compatible_images",
                    "base_url": self.safe_base_url,
                    "model": self.model,
                    "size": self.size,
                    "revised_prompt": item.get("revised_prompt"),
                }
            except Exception as exc:
                last_error = self._format_exception(exc)
                time.sleep(0.8 * (attempt + 1))

        raise PipelineError(
            code="image_api_request_failed",
            message="OpenAI-compatible image generation request failed after retries.",
            step=step,
            details={"error": last_error, "base_url": self.safe_base_url, "model": self.model},
        )

    def self_test(self, real: bool = False) -> Dict[str, Any]:
        if not self.enabled:
            return {
                "ok": True,
                "enabled": False,
                "provider": "pillow_template_v1",
                "message": "External image API is disabled; local Pillow card renderer will be used.",
            }
        try:
            self.ensure_available("image_self_test")
        except PipelineError as exc:
            return {
                "ok": False,
                "enabled": self.enabled,
                "error": exc.to_dict(),
                "base_url": self.safe_base_url,
                "model": self.model,
                "size": self.size,
            }
        if real:
            output_path = settings.runtime_dir / "_self_tests" / "image-self-test.png"
            try:
                result = self.generate_to_file(
                    "A clean minimal editorial illustration for a Chinese content creation dashboard, soft white background, one abstract video frame card, no readable text.",
                    output_path,
                    step="image_self_test",
                )
            except PipelineError as exc:
                return {
                    "ok": False,
                    "enabled": self.enabled,
                    "real_request": True,
                    "error": exc.to_dict(),
                    "base_url": self.safe_base_url,
                    "model": self.model,
                    "size": self.size,
                }
            return {
                "ok": True,
                "enabled": self.enabled,
                "real_request": True,
                "base_url": self.safe_base_url,
                "model": self.model,
                "size": self.size,
                "output_path": str(output_path),
                "provider": result.get("provider"),
                "message": "A real image generation request succeeded.",
            }
        return {
            "ok": True,
            "enabled": self.enabled,
            "base_url": self.safe_base_url,
            "model": self.model,
            "size": self.size,
            "auth_required": self.requires_api_key,
            "note": "Configuration is present. A real image request will run during generate-images.",
        }

    @property
    def safe_base_url(self) -> str:
        return sanitize_llm_url(self.base_url)

    def _sanitize_error(self, message: str) -> str:
        if self.api_key:
            message = message.replace(self.api_key, "[redacted-api-key]")
        return message

    def _format_exception(self, exc: Exception) -> str:
        message = str(exc)
        response = getattr(exc, "response", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)
            try:
                body = response.text
            except Exception:
                body = ""
            if body:
                body = body[:1200]
                message = f"{status_code} {body}" if status_code else body
        return self._sanitize_error(message)


image_client = ImageClient()
