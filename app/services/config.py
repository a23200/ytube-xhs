import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME_DIR = BASE_DIR / "runtime"


try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

if load_dotenv:
    load_dotenv(BASE_DIR / ".env", override=False)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _path_env(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    if not raw:
        return default.resolve()
    path = Path(raw)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def _api_requires_key(base_url: str, env_name: str) -> bool:
    raw = os.getenv(env_name, "auto").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    try:
        host = urlsplit(base_url).hostname or ""
    except ValueError:
        host = ""
    return host not in {"localhost", "127.0.0.1", "::1"}


def _llm_requires_api_key(base_url: str) -> bool:
    return _api_requires_key(base_url, "XHS_LLM_REQUIRE_API_KEY")


def _image_requires_api_key(base_url: str) -> bool:
    return _api_requires_key(base_url, "XHS_IMAGE_REQUIRE_API_KEY")


class Settings:
    runtime_dir: Path
    llm_api_key: Optional[str]
    llm_base_url: str
    llm_model: str
    llm_requires_api_key: bool
    llm_timeout_ms: int
    llm_max_chars: int
    llm_max_tokens: int
    llm_retry_attempts: int
    image_api_key: Optional[str]
    image_base_url: str
    image_model: str
    image_requires_api_key: bool
    image_timeout_ms: int
    image_size: str
    image_enabled: bool
    whisper_model: str
    whisper_device: str
    whisper_compute_type: str
    ocr_provider: str
    ytdlp_cookies_file: Optional[Path]
    ytdlp_cookies_from_browser: Optional[str]
    ytdlp_impersonate: Optional[str]
    ytdlp_socket_timeout_seconds: int
    ytdlp_redirect_timeout_seconds: int
    ytdlp_extract_attempts: int
    ytdlp_browser_cookie_timeout_seconds: int
    max_analyze_workers: int
    max_produce_workers: int

    def __init__(self) -> None:
        self.runtime_dir = _path_env("XHS_RUNTIME_DIR", DEFAULT_RUNTIME_DIR)
        self.llm_api_key = os.getenv("BUSINESS_LLM_API_KEY") or os.getenv("XHS_LLM_API_KEY")
        self.llm_base_url = os.getenv("XHS_LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.llm_model = os.getenv("XHS_LLM_MODEL", "deepseek-chat")
        self.llm_requires_api_key = _llm_requires_api_key(self.llm_base_url)
        self.llm_timeout_ms = _int_env("XHS_LLM_TIMEOUT_MS", 90000)
        self.llm_max_chars = _int_env("XHS_LLM_MAX_CHARS", 80000)
        self.llm_max_tokens = _int_env("XHS_LLM_MAX_TOKENS", 3000)
        self.llm_retry_attempts = min(10, max(1, _int_env("XHS_LLM_RETRY_ATTEMPTS", 3)))
        self.image_api_key = os.getenv("XHS_IMAGE_API_KEY")
        self.image_base_url = os.getenv("XHS_IMAGE_BASE_URL", "").rstrip("/")
        self.image_model = os.getenv("XHS_IMAGE_MODEL", "")
        self.image_requires_api_key = _image_requires_api_key(self.image_base_url)
        self.image_timeout_ms = _int_env("XHS_IMAGE_TIMEOUT_MS", 120000)
        self.image_size = os.getenv("XHS_IMAGE_SIZE", "1024x1024")
        self.image_enabled = os.getenv("XHS_IMAGE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        self.whisper_model = os.getenv("XHS_WHISPER_MODEL", "base")
        self.whisper_device = os.getenv("XHS_WHISPER_DEVICE", "auto")
        self.whisper_compute_type = os.getenv("XHS_WHISPER_COMPUTE_TYPE", "default")
        self.ocr_provider = os.getenv("XHS_OCR_PROVIDER", "auto").lower()
        raw_cookies_file = os.getenv("XHS_YTDLP_COOKIES_FILE")
        self.ytdlp_cookies_file = _path_env("XHS_YTDLP_COOKIES_FILE", Path()) if raw_cookies_file else None
        self.ytdlp_cookies_from_browser = os.getenv("XHS_YTDLP_COOKIES_FROM_BROWSER") or None
        self.ytdlp_impersonate = os.getenv("XHS_YTDLP_IMPERSONATE") or None
        self.ytdlp_socket_timeout_seconds = min(120, max(5, _int_env("XHS_YTDLP_SOCKET_TIMEOUT_SECONDS", 30)))
        self.ytdlp_redirect_timeout_seconds = min(60, max(3, _int_env("XHS_YTDLP_REDIRECT_TIMEOUT_SECONDS", 12)))
        self.ytdlp_extract_attempts = min(4, max(1, _int_env("XHS_YTDLP_EXTRACT_ATTEMPTS", 2)))
        self.ytdlp_browser_cookie_timeout_seconds = min(
            180,
            max(10, _int_env("XHS_YTDLP_BROWSER_COOKIE_TIMEOUT_SECONDS", 45)),
        )
        self.max_analyze_workers = max(1, _int_env("YTXHS_MAX_ANALYZE_WORKERS", 1))
        self.max_produce_workers = max(1, _int_env("YTXHS_MAX_PRODUCE_WORKERS", 3))


settings = Settings()
