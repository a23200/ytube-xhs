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
    assert "XHS_YTDLP_COOKIES_FROM_BROWSER=chrome" in exc_info.value.message
    assert "XHS_YTDLP_COOKIES_FILE" in exc_info.value.message


def test_unclassified_ytdlp_error_does_not_claim_public_url_is_private(tmp_path):
    message = "unexpected extractor response"
    with pytest.raises(PipelineError) as exc_info:
        ingest._raise_ingest_ytdlp_error(message, RuntimeError(message), "https://example.com", "zh", None, "template")
    assert exc_info.value.code == "yt_dlp_failed"
    assert "does not infer" in exc_info.value.message
