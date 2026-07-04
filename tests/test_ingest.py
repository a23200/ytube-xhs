import sys
import types
from pathlib import Path

import pytest

from app.services import ingest
from app.services.errors import PipelineError
from app.services.runtime_store import read_json


def _valid_png_bytes() -> bytes:
    import cv2
    import numpy as np

    image = np.zeros((2, 2, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


class FakeThumbnailResponse:
    headers = {"Content-Type": "image/png"}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return _valid_png_bytes()


def test_download_thumbnail_normalizes_supported_images_to_jpg(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        ingest.urllib.request,
        "urlopen",
        lambda request, timeout: FakeThumbnailResponse(),
    )

    thumbnail = ingest._download_thumbnail("https://example.test/thumb.png", tmp_path / "thumbnail.jpg")

    assert thumbnail == tmp_path / "thumbnail.jpg"
    assert thumbnail.exists()
    assert thumbnail.read_bytes().startswith(b"\xff\xd8")


def test_thumbnail_from_video_writes_standard_jpg_path(tmp_path: Path, monkeypatch):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"mp4")

    monkeypatch.setattr(ingest, "require_command", lambda command, step: None)

    def fake_run_command(command, step, timeout):
        output_path = Path(command[-1])
        output_path.write_bytes(b"\xff\xd8\xff\xd9")

    monkeypatch.setattr(ingest, "run_command", fake_run_command)

    thumbnail = ingest._thumbnail_from_video(video, tmp_path / "thumbnail.png")

    assert thumbnail == tmp_path / "thumbnail.jpg"
    assert thumbnail.read_bytes().startswith(b"\xff\xd8")


def test_thumbnail_from_video_returns_none_when_ffmpeg_unavailable(tmp_path: Path, monkeypatch):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"mp4")

    def fail_require(command, step):
        raise PipelineError(code="missing_command", message="ffmpeg missing", step=step)

    monkeypatch.setattr(ingest, "require_command", fail_require)

    assert ingest._thumbnail_from_video(video, tmp_path / "thumbnail.jpg") is None


def test_safe_metadata_summarizes_subtitle_tracks(tmp_path: Path):
    video = tmp_path / "source.mp4"
    subtitle = tmp_path / "subtitles.vtt"
    metadata = ingest._safe_metadata(
        {
            "id": "v1",
            "webpage_url": "https://example.com/video",
            "title": "Title",
            "uploader": "Author",
            "duration": 12,
            "subtitles": {
                "en": [{"ext": "vtt"}, {"ext": "json3"}],
                "zh-Hans": [{"ext": "vtt"}],
            },
            "automatic_captions": {
                "en": [{"ext": "vtt"}],
            },
        },
        video,
        subtitle,
    )

    assert metadata["available_subtitles"] == ["en", "zh-Hans"]
    assert metadata["automatic_captions"] == ["en"]
    assert metadata["subtitle_track_summary"]["available_subtitles"] == {
        "count": 2,
        "languages": ["en", "zh-Hans"],
        "formats_by_language": {"en": ["json3", "vtt"], "zh-Hans": ["vtt"]},
    }
    assert metadata["subtitle_track_summary"]["automatic_captions"]["count"] == 1


def test_normalize_info_rejects_empty_playlist_structurally():
    with pytest.raises(PipelineError) as exc_info:
        ingest._normalize_info({"entries": [], "extractor_key": "YoutubePlaylist"}, "https://example.com/list")

    error = exc_info.value.to_dict()
    assert error["code"] == "yt_dlp_no_info"
    assert error["step"] == "ingest"
    assert error["details"]["extractor"] == "YoutubePlaylist"


def test_normalize_info_rejects_unsupported_payload_type():
    with pytest.raises(PipelineError) as exc_info:
        ingest._normalize_info(["not", "a", "dict"], "https://example.com/video")

    error = exc_info.value.to_dict()
    assert error["code"] == "yt_dlp_no_info"
    assert error["details"]["payload_type"] == "list"


def test_normalize_info_selects_first_usable_playlist_entry():
    info = ingest._normalize_info(
        {
            "entries": [
                None,
                "bad",
                {"id": "v1", "title": "First usable"},
                {"id": "v2", "title": "Second"},
            ]
        },
        "https://example.com/list",
    )

    assert info == {"id": "v1", "title": "First usable"}


def test_find_downloaded_subtitle_accepts_ttml_and_standardizes_name(tmp_path: Path):
    subtitle = tmp_path / "abc123.en.ttml"
    subtitle.write_text("<tt />", encoding="utf-8")

    found = ingest._find_downloaded_subtitle(tmp_path, "abc123")

    assert found == tmp_path / "subtitles.ttml"
    assert found.read_text(encoding="utf-8") == "<tt />"


class FakeYoutubeDL:
    seen_opts = None
    seen_opts_history = []
    error_message = None
    infos = []
    calls = 0

    def __init__(self, opts):
        FakeYoutubeDL.seen_opts = opts
        FakeYoutubeDL.seen_opts_history.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def extract_info(self, url, download=True):
        FakeYoutubeDL.calls += 1
        if FakeYoutubeDL.infos:
            return FakeYoutubeDL.infos.pop(0)
        raise RuntimeError(FakeYoutubeDL.error_message or "boom")


def _install_fake_ytdlp(monkeypatch, error_message: str):
    FakeYoutubeDL.seen_opts = None
    FakeYoutubeDL.seen_opts_history = []
    FakeYoutubeDL.error_message = error_message
    FakeYoutubeDL.infos = []
    FakeYoutubeDL.calls = 0
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))


def _install_fake_ytdlp_sequence(monkeypatch, items):
    FakeYoutubeDL.seen_opts = None
    FakeYoutubeDL.seen_opts_history = []
    FakeYoutubeDL.error_message = None
    FakeYoutubeDL.infos = list(items)
    FakeYoutubeDL.calls = 0
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))


def test_ingest_applies_ytdlp_cookie_settings(tmp_path: Path, monkeypatch):
    paths = ingest.ProjectPaths(tmp_path / "project")
    paths.ensure()
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("# netscape cookies", encoding="utf-8")
    monkeypatch.setattr(ingest.settings, "ytdlp_cookies_file", cookies_file)
    monkeypatch.setattr(ingest.settings, "ytdlp_cookies_from_browser", "chrome:Default")
    monkeypatch.setattr(ingest.settings, "ytdlp_impersonate", None)
    _install_fake_ytdlp(monkeypatch, "generic failure")

    with pytest.raises(PipelineError):
        ingest.ingest_video("https://www.youtube.com/watch?v=test", "zh", paths)

    assert FakeYoutubeDL.seen_opts["cookiefile"] == str(cookies_file)
    assert FakeYoutubeDL.seen_opts["cookiesfrombrowser"] == ("chrome", "Default", None, None)


def test_ingest_reports_youtube_bot_check_with_cookie_guidance(tmp_path: Path, monkeypatch):
    paths = ingest.ProjectPaths(tmp_path / "project")
    paths.ensure()
    monkeypatch.setattr(ingest.settings, "ytdlp_cookies_file", None)
    monkeypatch.setattr(ingest.settings, "ytdlp_cookies_from_browser", None)
    monkeypatch.setattr(ingest.settings, "ytdlp_impersonate", None)
    _install_fake_ytdlp(monkeypatch, "Sign in to confirm you’re not a bot. Use --cookies-from-browser")

    with pytest.raises(PipelineError) as exc_info:
        ingest.ingest_video("https://www.youtube.com/watch?v=test", "zh", paths)

    error = exc_info.value.to_dict()
    assert error["code"] == "youtube_bot_check_required"
    assert "XHS_YTDLP_COOKIES_FROM_BROWSER" in error["message"]
    assert error["details"]["cookies_from_browser_configured"] is False


def test_ingest_reports_media_download_403_with_actionable_guidance(tmp_path: Path, monkeypatch):
    paths = ingest.ProjectPaths(tmp_path / "project")
    paths.ensure()
    monkeypatch.setattr(ingest.settings, "ytdlp_cookies_file", None)
    monkeypatch.setattr(ingest.settings, "ytdlp_cookies_from_browser", "chrome")
    monkeypatch.setattr(ingest.settings, "ytdlp_impersonate", None)
    _install_fake_ytdlp(monkeypatch, "ERROR: unable to download video data: HTTP Error 403: Forbidden")

    with pytest.raises(PipelineError) as exc_info:
        ingest.ingest_video("https://www.youtube.com/watch?v=test", "zh", paths)

    error = exc_info.value.to_dict()
    assert error["code"] == "youtube_media_download_forbidden"
    assert "403 Forbidden" in error["message"]
    assert error["details"]["cookies_from_browser_configured"] is True
    assert error["details"]["impersonate"] is None


def test_ingest_retries_media_403_with_public_android_fallback(tmp_path: Path, monkeypatch):
    paths = ingest.ProjectPaths(tmp_path / "project")
    paths.ensure()
    monkeypatch.setattr(ingest.settings, "ytdlp_cookies_file", None)
    monkeypatch.setattr(ingest.settings, "ytdlp_cookies_from_browser", "chrome")
    monkeypatch.setattr(ingest.settings, "ytdlp_impersonate", None)
    monkeypatch.setattr(ingest, "_download_thumbnail", lambda url, output_path: None)
    FakeYoutubeDL.calls = 0
    FakeYoutubeDL.seen_opts_history = []

    class SequenceYoutubeDL(FakeYoutubeDL):
        def __init__(self, opts):
            super().__init__(opts)
            self.opts = opts

        def extract_info(self, url, download=True):
            FakeYoutubeDL.calls += 1
            if FakeYoutubeDL.calls == 2:
                raise RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")
            if FakeYoutubeDL.calls == 3:
                (paths.source_dir / "nKL7qoIwFfQ.mp4").write_bytes(b"video")
            return {
                "id": "nKL7qoIwFfQ",
                "webpage_url": url,
                "title": "Public video",
                "uploader": "source",
                "duration": 60,
                "subtitles": {},
                "automatic_captions": {},
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=SequenceYoutubeDL))

    metadata = ingest.ingest_video("https://www.youtube.com/watch?v=nKL7qoIwFfQ", "zh", paths)

    assert metadata["video_id"] == "nKL7qoIwFfQ"
    assert metadata["video_file"] == str(paths.source_dir / "nKL7qoIwFfQ.mp4")
    assert metadata["ingest_warnings"]
    fallback_opts = FakeYoutubeDL.seen_opts_history[-1]
    assert fallback_opts["extractor_args"]["youtube"]["player_client"] == ["android"]
    assert fallback_opts["format"] == "18/best[height<=360]/best"
    assert "cookiesfrombrowser" not in fallback_opts
    assert "cookiefile" not in fallback_opts
    assert fallback_opts["writesubtitles"] is False


def test_ingest_retries_format_unavailable_with_public_android_fallback(tmp_path: Path, monkeypatch):
    paths = ingest.ProjectPaths(tmp_path / "project")
    paths.ensure()
    monkeypatch.setattr(ingest.settings, "ytdlp_cookies_file", None)
    monkeypatch.setattr(ingest.settings, "ytdlp_cookies_from_browser", None)
    monkeypatch.setattr(ingest.settings, "ytdlp_impersonate", None)
    monkeypatch.setattr(ingest, "_download_thumbnail", lambda url, output_path: None)
    FakeYoutubeDL.calls = 0
    FakeYoutubeDL.seen_opts_history = []

    class SequenceYoutubeDL(FakeYoutubeDL):
        def __init__(self, opts):
            super().__init__(opts)
            self.opts = opts

        def extract_info(self, url, download=True):
            FakeYoutubeDL.calls += 1
            if FakeYoutubeDL.calls == 2:
                raise RuntimeError("ERROR: [youtube] _htRYoMr8Ew: Requested format is not available. Use --list-formats for a list of available formats")
            if FakeYoutubeDL.calls == 3:
                (paths.source_dir / "_htRYoMr8Ew.mp4").write_bytes(b"video")
            return {
                "id": "_htRYoMr8Ew",
                "webpage_url": url,
                "title": "Format fallback video",
                "uploader": "source",
                "duration": 60,
                "subtitles": {},
                "automatic_captions": {},
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=SequenceYoutubeDL))

    metadata = ingest.ingest_video("https://www.youtube.com/watch?v=_htRYoMr8Ew", "zh", paths)

    assert metadata["video_id"] == "_htRYoMr8Ew"
    assert metadata["video_file"] == str(paths.source_dir / "_htRYoMr8Ew.mp4")
    assert "requested format" in metadata["ingest_warnings"][0].lower() or "format" in metadata["ingest_warnings"][0].lower()
    fallback_opts = FakeYoutubeDL.seen_opts_history[-1]
    assert fallback_opts["extractor_args"]["youtube"]["player_client"] == ["android"]
    assert fallback_opts["format"] == "18/best[height<=360]/best"


def test_ingest_text_only_uses_subtitles_without_media_download(tmp_path: Path, monkeypatch):
    paths = ingest.ProjectPaths(tmp_path / "project")
    paths.ensure()
    monkeypatch.setattr(ingest.settings, "ytdlp_cookies_file", None)
    monkeypatch.setattr(ingest.settings, "ytdlp_cookies_from_browser", None)
    monkeypatch.setattr(ingest.settings, "ytdlp_impersonate", None)
    monkeypatch.setattr(ingest, "_download_thumbnail", lambda url, output_path: None)
    FakeYoutubeDL.calls = 0
    FakeYoutubeDL.seen_opts_history = []

    class SequenceYoutubeDL(FakeYoutubeDL):
        def __init__(self, opts):
            super().__init__(opts)
            self.opts = opts

        def extract_info(self, url, download=True):
            FakeYoutubeDL.calls += 1
            subtitle = paths.source_dir / "textonly.zh-Hans.vtt"
            subtitle.write_text("WEBVTT\n\n00:00.000 --> 00:01.000\n真实字幕\n", encoding="utf-8")
            return {
                "id": "textonly",
                "webpage_url": url,
                "title": "Text only video",
                "uploader": "source",
                "duration": 60,
                "subtitles": {},
                "automatic_captions": {"zh-Hans": [{"ext": "vtt"}]},
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=SequenceYoutubeDL))

    metadata = ingest.ingest_video("https://www.youtube.com/watch?v=textonly", "zh", paths, prefer_subtitles_only=True)

    assert FakeYoutubeDL.calls == 1
    assert metadata["video_id"] == "textonly"
    assert metadata["video_file"] is None
    assert metadata["subtitle_file"] == str(paths.source_dir / "subtitles.vtt")
    assert "Text-only analysis mode" in metadata["ingest_warnings"][0]
    assert read_json(paths.source_dir / "metadata.json")["video_file"] is None


def test_ingest_reports_youtube_tls_network_failure_separately(tmp_path: Path, monkeypatch):
    paths = ingest.ProjectPaths(tmp_path / "project")
    paths.ensure()
    monkeypatch.setattr(ingest.settings, "ytdlp_cookies_file", None)
    monkeypatch.setattr(ingest.settings, "ytdlp_cookies_from_browser", "chrome")
    monkeypatch.setattr(ingest.settings, "ytdlp_impersonate", None)
    _install_fake_ytdlp(
        monkeypatch,
        "ERROR: [youtube] wpb-DrbhEiY: Unable to download API page: EOF occurred in violation of protocol (_ssl.c:1129) (caused by SSLError('EOF occurred in violation of protocol (_ssl.c:1129)'))",
    )

    with pytest.raises(PipelineError) as exc_info:
        ingest.ingest_video("https://www.youtube.com/watch?v=test", "zh", paths)

    error = exc_info.value.to_dict()
    assert error["code"] == "youtube_network_tls_failed"
    assert "network" in error["message"].lower()


def test_ingest_continues_with_real_subtitles_when_media_download_403(tmp_path: Path, monkeypatch):
    paths = ingest.ProjectPaths(tmp_path / "project")
    paths.ensure()
    monkeypatch.setattr(ingest.settings, "ytdlp_cookies_file", None)
    monkeypatch.setattr(ingest.settings, "ytdlp_cookies_from_browser", "chrome")
    monkeypatch.setattr(ingest.settings, "ytdlp_impersonate", None)
    FakeYoutubeDL.calls = 0

    class SequenceYoutubeDL(FakeYoutubeDL):
        def __init__(self, opts):
            super().__init__(opts)
            self.opts = opts

        def extract_info(self, url, download=True):
            FakeYoutubeDL.calls += 1
            subtitle = paths.source_dir / "wpb-DrbhEiY.zh-Hans.vtt"
            subtitle.write_text("WEBVTT\n\n00:00.000 --> 00:01.000\n真实字幕\n", encoding="utf-8")
            if FakeYoutubeDL.calls == 2:
                raise RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")
            return {
                "id": "wpb-DrbhEiY",
                "webpage_url": url,
                "title": "SpaceX上市，背后在玩什么资本游戏?",
                "uploader": "source",
                "duration": 60,
                "subtitles": {},
                "automatic_captions": {"zh-Hans": [{"ext": "vtt"}]},
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=SequenceYoutubeDL))
    monkeypatch.setattr(ingest, "_download_thumbnail", lambda url, output_path: None)

    metadata = ingest.ingest_video("https://www.youtube.com/watch?v=wpb-DrbhEiY", "zh", paths)

    assert metadata["video_id"] == "wpb-DrbhEiY"
    assert metadata["video_file"] is None
    assert metadata["subtitle_file"] == str(paths.source_dir / "subtitles.vtt")
    assert metadata["ingest_warnings"]
    assert read_json(paths.source_dir / "metadata.json")["video_file"] is None
    assert FakeYoutubeDL.calls == 3


def test_apply_impersonation_options_sets_yt_dlp_target(monkeypatch):
    monkeypatch.setattr(ingest.settings, "ytdlp_impersonate", "chrome")

    opts = {}
    ingest._apply_impersonation_options(opts)

    assert str(opts["impersonate"]) == "chrome"
