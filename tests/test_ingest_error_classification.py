import pytest

from app.services import ingest
from app.services.errors import PipelineError


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("Requested format is not available. Use --list-formats for a list of available formats", "yt_dlp_format_unavailable"),
        ("Unable to download video data: HTTP Error 403: Forbidden", "youtube_media_download_forbidden"),
        ("ERROR: [Douyin] 123: Fresh cookies (not necessarily logged in) are needed", "yt_dlp_cookies_required"),
        ("cookies are no longer valid", "yt_dlp_cookies_invalid"),
        ("This video is unavailable in your country", "region_restricted"),
        ("Sign in to confirm your age", "login_required"),
        ("This video is DRM protected", "drm_protected"),
        ("The read operation timed out", "yt_dlp_network_timeout"),
        ("Please update to the latest version of yt-dlp", "yt_dlp_update_required"),
        ("HTTP Error 403: Forbidden while downloading API page", "yt_dlp_access_forbidden"),
        ("HTTP Error 429: Too Many Requests", "yt_dlp_rate_limited"),
        ("Unable to download JSON metadata: HTTP Error 412: Precondition Failed", "yt_dlp_precondition_failed"),
        ("Unsupported URL: https://example.com/share/123", "yt_dlp_unsupported_url"),
        ("Unable to extract video data; please report this issue", "yt_dlp_extractor_changed"),
        ("Connection reset by peer", "yt_dlp_network_error"),
    ],
)
def test_ytdlp_errors_are_structurally_classified(tmp_path, message, code):
    with pytest.raises(PipelineError) as exc_info:
        ingest._raise_ingest_ytdlp_error(message, RuntimeError(message), "https://example.com", "zh", None, "template")
    assert exc_info.value.code == code
    assert exc_info.value.details["error"] == message


def test_fresh_cookie_error_has_public_video_configuration_guidance(tmp_path):
    message = "ERROR: [Douyin] 123: Fresh cookies (not necessarily logged in) are needed"
    with pytest.raises(PipelineError) as exc_info:
        ingest._raise_ingest_ytdlp_error(message, RuntimeError(message), "https://www.douyin.com/video/123", "zh", None, "template")

    assert exc_info.value.code == "yt_dlp_cookies_required"
    assert "Platform Accounts" in exc_info.value.message
    assert "cookies.txt" in exc_info.value.message


def test_unclassified_ytdlp_error_does_not_claim_public_url_is_private(tmp_path):
    message = "unexpected extractor response"
    with pytest.raises(PipelineError) as exc_info:
        ingest._raise_ingest_ytdlp_error(message, RuntimeError(message), "https://example.com", "zh", None, "template")
    assert exc_info.value.code == "yt_dlp_failed"
    assert "does not infer" in exc_info.value.message


def test_known_platform_generic_timeout_reports_url_and_retry_phase():
    message = "ERROR: [generic] Unable to download webpage: The read operation timed out"
    error = RuntimeError(message)
    error._ytxhs_retry_context = {
        "phase": "subtitle_metadata_preflight",
        "attempts": 2,
        "socket_timeout_seconds": 30,
    }
    source_context = {
        "source_platform": "douyin",
        "original_url": "https://v.douyin.com/test/",
        "normalized_url": "https://www.douyin.com/video/7658533907561963193",
        "normalized_host": "www.douyin.com",
    }

    with pytest.raises(PipelineError) as exc_info:
        ingest._raise_ingest_ytdlp_error(
            message,
            error,
            source_context["normalized_url"],
            "zh",
            None,
            "template",
            source_context,
        )

    payload = exc_info.value.to_dict()
    assert payload["code"] == "yt_dlp_generic_extractor_timeout"
    assert payload["details"]["source_platform"] == "douyin"
    assert payload["details"]["normalized_host"] == "www.douyin.com"
    assert payload["details"]["retry"]["phase"] == "subtitle_metadata_preflight"


def test_transient_extractor_timeout_retries_with_context(monkeypatch):
    calls = []

    class TimeoutYoutubeDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url, download=False):
            calls.append((url, download))
            raise RuntimeError("The read operation timed out")

    monkeypatch.setattr(ingest.settings, "ytdlp_extract_attempts", 2)
    monkeypatch.setattr(ingest.time, "sleep", lambda _seconds: None)
    module = type("FakeYtDlp", (), {"YoutubeDL": TimeoutYoutubeDL})

    with pytest.raises(RuntimeError) as exc_info:
        ingest._extract_info_with_retries(
            module,
            {},
            "https://www.youtube.com/watch?v=test",
            download=False,
            phase="subtitle_metadata_preflight",
        )

    context = exc_info.value._ytxhs_retry_context
    assert len(calls) == 2
    assert context["attempts"] == 2
    assert context["phase"] == "subtitle_metadata_preflight"
    assert len(context["failures"]) == 2
