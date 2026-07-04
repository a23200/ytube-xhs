import importlib
import importlib.metadata
import importlib.util
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from app.services.config import settings
from app.services.llm_client import sanitize_llm_url
from app.services.media_utils import command_env, find_command


def _command_version(command: str) -> Optional[str]:
    command_name = Path(command).name
    version_flag = "--version" if command_name == "tesseract" else "-version"
    try:
        result = subprocess.run(
            [command, version_flag],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env=command_env(),
        )
    except Exception:
        return None
    output = (result.stdout or result.stderr or "").strip()
    if not output:
        return None
    return output.splitlines()[0].strip() or None


def _command_status(command: str) -> Dict[str, Any]:
    path = find_command(command)
    return {
        "available": bool(path),
        "path": path,
        "version": _command_version(path) if path else None,
    }


def _tesseract_language_status(command_path: Optional[str]) -> Dict[str, Any]:
    if not command_path:
        return {"available": False, "languages": [], "key_languages": {}, "error": "tesseract command is not available."}
    try:
        result = subprocess.run(
            [command_path, "--list-langs"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env=command_env(),
        )
    except Exception as exc:
        return {"available": False, "languages": [], "key_languages": {}, "error": f"{type(exc).__name__}: {exc}"}
    if result.returncode != 0:
        return {
            "available": False,
            "languages": [],
            "key_languages": {},
            "error": (result.stderr or result.stdout or "").strip()[-500:],
        }
    languages = sorted(
        line.strip()
        for line in (result.stdout or "").splitlines()
        if line.strip() and not line.lower().startswith("list of available languages")
    )
    key_languages = {language: language in languages for language in ["eng", "chi_sim", "chi_tra", "osd"]}
    return {"available": bool(languages), "languages": languages, "key_languages": key_languages, "error": None}


def _distribution_version(package_names: Iterable[str]) -> Optional[str]:
    for package_name in package_names:
        try:
            return importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


def _module_status(module_name: str, package_names: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    try:
        spec = importlib.util.find_spec(module_name)
    except Exception as exc:
        return {
            "available": False,
            "version": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if spec is None:
        return {
            "available": False,
            "version": None,
            "error": f"ModuleNotFoundError: No module named '{module_name}'",
        }
    candidates = list(package_names or [])
    candidates.extend([module_name, module_name.replace("_", "-")])
    version = _distribution_version(candidates)
    return {"available": True, "version": version, "error": None}


def _runtime_status(runtime_dir: Path) -> Dict[str, Any]:
    try:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        probe = runtime_dir / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {"available": True, "path": str(runtime_dir), "writable": True, "error": None}
    except Exception as exc:
        return {
            "available": False,
            "path": str(runtime_dir),
            "writable": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def collect_diagnostics() -> Dict[str, Any]:
    commands = {
        "ffmpeg": _command_status("ffmpeg"),
        "ffprobe": _command_status("ffprobe"),
        "tesseract": _command_status("tesseract"),
    }
    tesseract_languages = _tesseract_language_status(commands["tesseract"]["path"])
    modules = {
        "fastapi": _module_status("fastapi"),
        "yt_dlp": _yt_dlp_status(),
        "scenedetect": _module_status("scenedetect"),
        "cv2": _module_status("cv2", ["opencv-python", "opencv-python-headless", "opencv-contrib-python"]),
        "httpx": _module_status("httpx"),
        "faster_whisper": _module_status("faster_whisper", ["faster-whisper"]),
        "paddleocr": _module_status("paddleocr"),
    }
    llm = {
        "configured": bool(settings.llm_api_key),
        "auth_required": settings.llm_requires_api_key,
        "base_url": sanitize_llm_url(settings.llm_base_url),
        "model": settings.llm_model,
        "timeout_ms": settings.llm_timeout_ms,
        "max_chars": settings.llm_max_chars,
        "max_tokens": settings.llm_max_tokens,
        "api_key_env": "configured" if settings.llm_api_key else "missing",
        "readiness_check": "configuration_only",
        "self_test_endpoint": "/api/llm/self-test",
    }
    image = {
        "enabled": settings.image_enabled,
        "configured": bool(settings.image_base_url and settings.image_model),
        "auth_required": settings.image_requires_api_key,
        "base_url": sanitize_llm_url(settings.image_base_url),
        "model": settings.image_model,
        "size": settings.image_size,
        "timeout_ms": settings.image_timeout_ms,
        "api_key_env": "configured" if settings.image_api_key else "missing",
        "fallback_renderer": "pillow_template_v1",
        "readiness_check": "configuration_only",
        "self_test_endpoint": "/api/image/self-test",
    }
    ocr = {
        "configured_provider": settings.ocr_provider,
        "paddleocr_available": modules["paddleocr"]["available"],
        "tesseract_available": commands["tesseract"]["available"],
        "tesseract_languages": tesseract_languages,
    }
    ready_for = {
        "ingest": modules["yt_dlp"]["available"] and commands["ffmpeg"]["available"],
        "subtitle_transcript": True,
        "whisper_transcript": modules["faster_whisper"]["available"] and commands["ffmpeg"]["available"],
        "frame_extraction": (
            commands["ffmpeg"]["available"]
            and commands["ffprobe"]["available"]
            and modules["scenedetect"]["available"]
            and modules["cv2"]["available"]
        ),
        "ocr": modules["paddleocr"]["available"] or (commands["tesseract"]["available"] and tesseract_languages["available"]),
        "llm_generation": (llm["configured"] or not settings.llm_requires_api_key) and modules["httpx"]["available"],
        "image_generation": (
            not settings.image_enabled
            or (image["configured"] and (bool(settings.image_api_key) or not settings.image_requires_api_key) and modules["httpx"]["available"])
        ),
    }
    warnings = []
    if not ready_for["whisper_transcript"]:
        warnings.append("No-subtitle videos require faster-whisper plus ffmpeg.")
    if not ready_for["ocr"]:
        warnings.append("No OCR provider is available; install PaddleOCR or tesseract to extract screen text.")
    if not ready_for["llm_generation"]:
        if settings.llm_requires_api_key and not settings.llm_api_key:
            warnings.append(
                "LLM generation requires BUSINESS_LLM_API_KEY or XHS_LLM_API_KEY. "
                "For local OpenAI-compatible endpoints without auth, set XHS_LLM_REQUIRE_API_KEY=false."
            )
        else:
            warnings.append("LLM generation requires httpx and a reachable OpenAI-compatible chat completions endpoint.")
    if settings.image_enabled and not ready_for["image_generation"]:
        warnings.append("External image generation requires XHS_IMAGE_BASE_URL, XHS_IMAGE_MODEL, httpx, and API key when required.")

    return {
        "runtime": _runtime_status(settings.runtime_dir),
        "commands": commands,
        "modules": modules,
        "llm": llm,
        "image": image,
        "ocr": ocr,
        "ready_for": ready_for,
        "warnings": warnings,
    }


def _yt_dlp_status() -> Dict[str, Any]:
    status = _module_status("yt_dlp", ["yt-dlp"])
    status["cookies"] = {
        "from_browser": settings.ytdlp_cookies_from_browser or None,
        "file_configured": bool(settings.ytdlp_cookies_file),
        "file_exists": bool(settings.ytdlp_cookies_file and settings.ytdlp_cookies_file.exists()),
    }
    status["impersonate"] = settings.ytdlp_impersonate or None
    return status
