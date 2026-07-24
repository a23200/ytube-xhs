import os
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
    assert len(statuses.json()["platforms"]) == 4
    assert deleted.status_code == 200
    assert "api-secret" not in uploaded.text
    assert "api-secret" not in statuses.text
    assert "api-secret" not in deleted.text
