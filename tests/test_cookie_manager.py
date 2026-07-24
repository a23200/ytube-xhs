import os
import pwd
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import cookie_manager


def _cookie_text(*lines: str) -> bytes:
    return ("# Netscape HTTP Cookie File\n" + "\n".join(lines) + "\n").encode()


@pytest.fixture
def isolated_auth(tmp_path, monkeypatch):
    auth_path = tmp_path / "auth"
    monkeypatch.setenv("XHS_YTDLP_AUTH_DIR", str(auth_path))
    for platform in cookie_manager.SUPPORTED_SOURCE_PLATFORMS:
        monkeypatch.delenv(f"XHS_YTDLP_{platform.upper()}_COOKIES_FILE", raising=False)
    monkeypatch.setattr(cookie_manager.settings, "ytdlp_cookies_file", None)
    monkeypatch.setattr(cookie_manager.settings, "ytdlp_cookies_from_browser", None)
    return auth_path


def test_import_filters_platform_domains_and_never_returns_values(isolated_auth):
    content = _cookie_text(
        ".bilibili.com\tTRUE\t/\tTRUE\t4102444800\tSESSDATA\tbili-secret-value",
        ".youtube.com\tTRUE\t/\tTRUE\t4102444800\tLOGIN_INFO\tyoutube-secret-value",
    )

    result = cookie_manager.import_cookie_text("bilibili", content)
    path = cookie_manager.managed_cookie_path("bilibili")

    assert result["status"] == "session_detected"
    assert result["cookie_count"] == 1
    assert result["auth_cookie_count"] == 1
    assert "bili-secret-value" not in str(result)
    assert "youtube-secret-value" not in path.read_text(encoding="utf-8")
    assert "bili-secret-value" in path.read_text(encoding="utf-8")
    assert path.stat().st_mode & 0o777 == 0o600
    assert isolated_auth.stat().st_mode & 0o777 == 0o700


def test_cookie_status_reports_expired_file(isolated_auth):
    cookie_manager.import_cookie_text(
        "douyin",
        _cookie_text(".douyin.com\tTRUE\t/\tTRUE\t100\tsessionid\texpired-secret"),
    )

    status = cookie_manager.cookie_status("douyin")

    assert status["status"] == "expired"
    assert status["valid_cookie_count"] == 0
    assert status["expired_cookie_count"] == 1
    assert "expired-secret" not in str(status)


def test_delete_only_removes_selected_managed_cookie(isolated_auth):
    for platform, domain, name in (
        ("youtube", ".youtube.com", "LOGIN_INFO"),
        ("bilibili", ".bilibili.com", "SESSDATA"),
    ):
        cookie_manager.import_cookie_text(
            platform,
            _cookie_text(f"{domain}\tTRUE\t/\tTRUE\t4102444800\t{name}\tsecret-{platform}"),
        )

    result = cookie_manager.delete_managed_cookie("youtube")

    assert result["status"] == "unconfigured"
    assert not cookie_manager.managed_cookie_path("youtube").exists()
    assert cookie_manager.managed_cookie_path("bilibili").exists()


def test_verify_uses_only_selected_platform_cookie(isolated_auth, monkeypatch):
    cookie_manager.import_cookie_text(
        "bilibili",
        _cookie_text(".bilibili.com\tTRUE\t/\tTRUE\t4102444800\tSESSDATA\tbili-secret"),
    )
    seen = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            seen.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url, download=False):
            seen["url"] = url
            seen["download"] = download
            return {
                "id": "BV1xx411c7mD",
                "title": "公开测试视频",
                "duration": 10,
                "extractor_key": "BiliBili",
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))

    result = cookie_manager.verify_cookie("bilibili", "https://www.bilibili.com/video/BV1xx411c7mD/")

    assert result["verification"]["ok"] is True
    assert seen["cookiefile"] == str(cookie_manager.managed_cookie_path("bilibili"))
    assert seen["url"] == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert seen["download"] is False
    assert "bili-secret" not in str(result)


def test_cookie_error_redaction_removes_headers_and_sensitive_query_values():
    raw = "Cookie: sessionid=secret\nAuthorization: Bearer token\nhttps://x.test/v?token=abc&item=1"

    redacted = cookie_manager.sanitize_error_text(raw)

    assert "sessionid=secret" not in redacted
    assert "Bearer token" not in redacted
    assert "token=abc" not in redacted
    assert "<redacted>" in redacted


def test_discover_chrome_profiles_with_cookie_databases(tmp_path, monkeypatch):
    chrome_root = tmp_path / "Library" / "Application Support" / "Google" / "Chrome"
    default_cookie = chrome_root / "Default" / "Network" / "Cookies"
    profile_cookie = chrome_root / "Profile 2" / "Network" / "Cookies"
    default_cookie.parent.mkdir(parents=True)
    profile_cookie.parent.mkdir(parents=True)
    default_cookie.write_bytes(b"")
    profile_cookie.write_bytes(b"")
    os.utime(profile_cookie, (200, 200))
    os.utime(default_cookie, (100, 100))
    monkeypatch.setattr(cookie_manager, "service_home", lambda: tmp_path)

    assert cookie_manager.discover_browser_profiles("chrome") == ["Profile 2", "Default"]


def test_service_home_uses_account_record_instead_of_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert cookie_manager.service_home() == Path(pwd.getpwuid(os.geteuid()).pw_dir).resolve()


def test_resolved_chrome_profile_uses_service_account_home(tmp_path, monkeypatch):
    monkeypatch.setattr(cookie_manager, "service_home", lambda: tmp_path)

    resolved = cookie_manager._resolved_browser_profile("chrome", "Profile 3")

    assert resolved == tmp_path / "Library" / "Application Support" / "Google" / "Chrome" / "Profile 3"


def test_browser_export_auto_checks_all_detected_profiles(tmp_path, monkeypatch):
    seen_profiles = []

    class FakeYoutubeDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_load_cookies(_cookie_file, browser_spec, _ydl):
        profile = browser_spec[1]
        seen_profiles.append(profile)
        if Path(profile).name == "Profile 1":
            return []
        return [
            types.SimpleNamespace(
                domain=".douyin.com",
                domain_initial_dot=True,
                path="/",
                secure=True,
                expires=4102444800,
                name="sessionid",
                value="auto-profile-secret",
                _rest={},
            )
        ]

    fake_yt_dlp = types.ModuleType("yt_dlp")
    fake_yt_dlp.YoutubeDL = FakeYoutubeDL
    fake_cookies = types.ModuleType("yt_dlp.cookies")
    fake_cookies.load_cookies = fake_load_cookies
    monkeypatch.setitem(sys.modules, "yt_dlp", fake_yt_dlp)
    monkeypatch.setitem(sys.modules, "yt_dlp.cookies", fake_cookies)
    monkeypatch.setattr(cookie_manager, "discover_browser_profiles", lambda _browser: ["Profile 1", "Profile 2"])
    monkeypatch.setattr(cookie_manager, "service_home", lambda: tmp_path)
    output = tmp_path / "export.cookies.txt"

    result = cookie_manager.export_browser_cookie_file("douyin", "chrome", None, output)

    chrome_root = tmp_path / "Library" / "Application Support" / "Google" / "Chrome"
    assert seen_profiles == [str(chrome_root / "Profile 1"), str(chrome_root / "Profile 2")]
    assert result["profile"] == "Profile 2"
    assert result["profiles_checked"] == ["Profile 1", "Profile 2"]
    assert "auto-profile-secret" in output.read_text(encoding="utf-8")


def test_browser_import_runs_in_isolated_worker_and_saves_filtered_cookie(isolated_auth, monkeypatch):
    observed = {}

    def fake_run(command, **kwargs):
        observed.update(kwargs)
        output = Path(command[command.index("--output") + 1])
        output.write_bytes(
            _cookie_text(".douyin.com\tTRUE\t/\tTRUE\t4102444800\tsessionid\tworker-secret")
        )
        return cookie_manager.subprocess.CompletedProcess(
            command,
            0,
            stdout='{"ok": true, "result": {"profile": "Profile 1", "profiles_checked": ["Profile 1"]}}',
            stderr="",
        )

    monkeypatch.setattr(cookie_manager.subprocess, "run", fake_run)
    monkeypatch.setattr(cookie_manager.settings, "ytdlp_browser_cookie_timeout_seconds", 17)

    result = cookie_manager.import_from_browser("douyin", "chrome", "Profile 1")

    assert result["status"] == "session_detected"
    assert result["profile"] == "Profile 1"
    assert result["profiles_checked"] == ["Profile 1"]
    assert result["imported_cookie_count"] == 1
    assert observed["timeout"] == 17
    assert observed["env"]["HOME"] == str(cookie_manager.service_home())
    assert "worker-secret" not in str(result)
    assert cookie_manager.managed_cookie_path("douyin").stat().st_mode & 0o777 == 0o600


def test_browser_import_timeout_returns_actionable_error(isolated_auth, monkeypatch):
    def fake_run(command, **kwargs):
        raise cookie_manager.subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(cookie_manager.subprocess, "run", fake_run)
    monkeypatch.setattr(cookie_manager.settings, "ytdlp_browser_cookie_timeout_seconds", 23)

    with pytest.raises(cookie_manager.CookieManagerError) as exc_info:
        cookie_manager.import_from_browser("douyin", "chrome")

    assert exc_info.value.code == "cookie_browser_import_timeout"
    assert exc_info.value.details["timeout_seconds"] == 23
    assert exc_info.value.details["service_user"]
    assert not cookie_manager.managed_cookie_path("douyin").exists()


def test_cookie_api_requires_confirmation_and_does_not_expose_values(isolated_auth):
    client = TestClient(app)
    content = _cookie_text(".youtube.com\tTRUE\t/\tTRUE\t4102444800\tLOGIN_INFO\tapi-secret")

    rejected = client.post(
        "/api/auth/cookies/youtube/upload",
        files={"file": ("cookies.txt", content, "text/plain")},
    )
    uploaded = client.post(
        "/api/auth/cookies/youtube/upload",
        headers={"X-YTXHS-Cookie-Action": "confirm"},
        files={"file": ("cookies.txt", content, "text/plain")},
    )
    statuses = client.get("/api/auth/cookies")
    deleted = client.delete(
        "/api/auth/cookies/youtube",
        headers={"X-YTXHS-Cookie-Action": "confirm"},
    )

    assert rejected.status_code == 403
    assert uploaded.status_code == 200
    assert uploaded.json()["status"] == "session_detected"
    assert statuses.status_code == 200
    assert statuses.headers["content-type"] == "application/json; charset=utf-8"
    assert len(statuses.json()["platforms"]) == 4
    assert statuses.json()["browser_import"]["service_user"]
    assert statuses.json()["browser_import"]["service_home"]
    assert statuses.json()["browser_import"]["timeout_seconds"] >= 10
    assert deleted.status_code == 200
    assert "api-secret" not in uploaded.text
    assert "api-secret" not in statuses.text
    assert "api-secret" not in deleted.text
