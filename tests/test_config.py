from app.services import config
from app.services.config import BASE_DIR, Settings


def test_dotenv_loader_is_available():
    assert config.load_dotenv is not None


def test_settings_read_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("XHS_RUNTIME_DIR", str(tmp_path / "rt"))
    monkeypatch.setenv("XHS_LLM_API_KEY", "xhs-key")
    monkeypatch.setenv("BUSINESS_LLM_API_KEY", "business-key")
    monkeypatch.setenv("XHS_LLM_BASE_URL", "https://llm.example/v1/")
    monkeypatch.setenv("XHS_LLM_MODEL", "model-a")
    monkeypatch.setenv("XHS_LLM_TIMEOUT_MS", "1234")
    monkeypatch.setenv("XHS_LLM_MAX_CHARS", "4567")
    monkeypatch.setenv("XHS_LLM_MAX_TOKENS", "321")
    monkeypatch.setenv("XHS_WHISPER_MODEL", "tiny")
    monkeypatch.setenv("XHS_WHISPER_DEVICE", "cpu")
    monkeypatch.setenv("XHS_WHISPER_COMPUTE_TYPE", "int8")
    monkeypatch.setenv("XHS_OCR_PROVIDER", "tesseract")

    settings = Settings()

    assert settings.runtime_dir == (tmp_path / "rt").resolve()
    assert settings.llm_api_key == "business-key"
    assert settings.llm_base_url == "https://llm.example/v1"
    assert settings.llm_model == "model-a"
    assert settings.llm_requires_api_key is True
    assert settings.llm_timeout_ms == 1234
    assert settings.llm_max_chars == 4567
    assert settings.llm_max_tokens == 321
    assert settings.whisper_model == "tiny"
    assert settings.whisper_device == "cpu"
    assert settings.whisper_compute_type == "int8"
    assert settings.ocr_provider == "tesseract"


def test_settings_invalid_int_env_uses_default(monkeypatch):
    monkeypatch.setenv("XHS_LLM_TIMEOUT_MS", "not-an-int")
    settings = Settings()

    assert settings.llm_timeout_ms == 60000


def test_relative_runtime_dir_is_resolved_from_project_root(monkeypatch):
    monkeypatch.setenv("XHS_RUNTIME_DIR", "custom-runtime")
    settings = Settings()

    assert settings.runtime_dir == (BASE_DIR / "custom-runtime").resolve()


def test_local_llm_endpoint_can_skip_api_key_in_auto_mode(monkeypatch):
    monkeypatch.delenv("BUSINESS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("XHS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("XHS_LLM_REQUIRE_API_KEY", raising=False)
    monkeypatch.setenv("XHS_LLM_BASE_URL", "http://127.0.0.1:11434/v1")

    settings = Settings()

    assert settings.llm_api_key is None
    assert settings.llm_requires_api_key is False


def test_llm_require_api_key_can_be_disabled_explicitly(monkeypatch):
    monkeypatch.delenv("BUSINESS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("XHS_LLM_API_KEY", raising=False)
    monkeypatch.setenv("XHS_LLM_REQUIRE_API_KEY", "false")
    monkeypatch.setenv("XHS_LLM_BASE_URL", "https://llm.example/v1")

    settings = Settings()

    assert settings.llm_requires_api_key is False
