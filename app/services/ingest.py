import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from app.services.config import settings
from app.services.errors import PipelineError
from app.services.media_utils import require_command, run_command
from app.services.runtime_store import ProjectPaths, write_json

LANGUAGE_CANDIDATES = {
    "zh": ["zh-Hans", "zh-CN", "zh", "zh-Hant", "en"],
    "cn": ["zh-Hans", "zh-CN", "zh", "zh-Hant", "en"],
    "en": ["en", "en-US", "en-GB"],
}

DEFAULT_YTDLP_FORMAT = "bv*[height<=360]+ba/b[height<=360]/best[height<=360]/best"
PUBLIC_ANDROID_FALLBACK_FORMAT = "18/best[height<=360]/best"
AUDIO_ONLY_FORMAT = "ba[abr<=64]/ba/bestaudio/best"
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".webm", ".opus", ".ogg", ".wav", ".aac"}


def _track_summary(tracks: Dict[str, Any]) -> Dict[str, Any]:
    formats_by_language: Dict[str, list[str]] = {}
    for language, entries in (tracks or {}).items():
        formats = set()
        for entry in entries or []:
            if isinstance(entry, dict) and entry.get("ext"):
                formats.add(str(entry["ext"]))
        formats_by_language[language] = sorted(formats)
    languages = sorted(formats_by_language)
    return {
        "count": len(languages),
        "languages": languages,
        "formats_by_language": formats_by_language,
    }


def _safe_metadata(
    info: Dict[str, Any],
    video_path: Optional[Path],
    subtitle_path: Optional[Path],
    audio_path: Optional[Path] = None,
) -> Dict[str, Any]:
    subtitles = info.get("subtitles") or {}
    automatic_captions = info.get("automatic_captions") or {}
    return {
        "video_id": info.get("id"),
        "url": info.get("webpage_url") or info.get("original_url"),
        "title": info.get("title"),
        "author": info.get("uploader") or info.get("channel") or info.get("creator"),
        "description": info.get("description"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "available_subtitles": sorted(list(subtitles.keys())),
        "automatic_captions": sorted(list(automatic_captions.keys())),
        "subtitle_track_summary": {
            "available_subtitles": _track_summary(subtitles),
            "automatic_captions": _track_summary(automatic_captions),
        },
        "video_file": str(video_path) if video_path else None,
        "audio_file": str(audio_path) if audio_path else None,
        "subtitle_file": str(subtitle_path) if subtitle_path else None,
        "source_extractor": info.get("extractor_key") or info.get("extractor"),
        "source": {
            "webpage_url": info.get("webpage_url"),
            "original_url": info.get("original_url"),
            "license": info.get("license"),
            "upload_date": info.get("upload_date"),
            "channel_id": info.get("channel_id"),
            "uploader_id": info.get("uploader_id"),
        },
    }


def _download_thumbnail(url: Optional[str], output_path: Path) -> Optional[Path]:
    if not url:
        return None
    target = output_path.with_suffix(".jpg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            content_type = response.headers.get("Content-Type", "")
            data = response.read()
            try:
                import cv2
                import numpy as np

                image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                if image is not None:
                    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                    if ok:
                        target.write_bytes(encoded.tobytes())
                        return target
            except Exception:
                pass
            if content_type.split(";")[0].strip().lower() in {"image/jpeg", "image/jpg"} or data.startswith(b"\xff\xd8"):
                target.write_bytes(data)
                return target
            return None
    except Exception:
        return None


def _thumbnail_from_video(video_path: Path, output_path: Path, timestamp: float = 1.0) -> Optional[Path]:
    output_path = output_path.with_suffix(".jpg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        require_command("ffmpeg", "ingest")
        run_command(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(output_path),
            ],
            step="ingest",
            timeout=120,
        )
    except PipelineError:
        return None
    return output_path if output_path.exists() else None


def _language_list(language: str) -> Iterable[str]:
    language = language.lower()
    seen = set()
    for item in LANGUAGE_CANDIDATES.get(language, [language, "zh-Hans", "zh-CN", "zh", "en"]):
        if item not in seen:
            seen.add(item)
            yield item


def _find_downloaded_video(source_dir: Path, video_id: Optional[str]) -> Optional[Path]:
    candidates = []
    for path in source_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v"}:
            if video_id and video_id in path.name:
                return path
            candidates.append(path)
    return candidates[0] if candidates else None


def _find_downloaded_audio(source_dir: Path, video_id: Optional[str]) -> Optional[Path]:
    candidates = []
    for path in source_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() in AUDIO_EXTENSIONS:
            if video_id and video_id in path.name:
                return path
            candidates.append(path)
    return candidates[0] if candidates else None


def _find_downloaded_subtitle(source_dir: Path, video_id: Optional[str]) -> Optional[Path]:
    subtitle_exts = {".vtt", ".srt", ".ass", ".ssa", ".json3", ".srv1", ".srv2", ".srv3", ".ttml", ".dfxp", ".xml"}
    candidates = [
        path
        for path in source_dir.iterdir()
        if path.is_file() and path.suffix.lower() in subtitle_exts and (not video_id or video_id in path.name)
    ]
    if not candidates:
        return None
    preferred = sorted(candidates, key=lambda item: (".vtt" not in item.suffix.lower(), len(item.name)))
    target = source_dir / f"subtitles{preferred[0].suffix.lower()}"
    if preferred[0] != target:
        target.write_bytes(preferred[0].read_bytes())
    return target


def _classified_restriction(message: str) -> Optional[tuple[str, str]]:
    lowered = message.lower()
    classifications = [
        ("drm_protected", ("drm",), "The media is DRM protected and cannot be processed by this project."),
        (
            "region_restricted",
            ("unavailable in your country", "not available in your country", "geo-restricted", "geographic restriction"),
            "The platform reports that this media is unavailable in the current region.",
        ),
        (
            "login_required",
            ("sign in", "login required", "private video", "members-only", "members only", "paid content"),
            "The platform requires login, membership, payment, or owner permission for this media.",
        ),
        (
            "copyright_restricted",
            ("copyright", "copyrighted content"),
            "The platform reports a copyright availability restriction for this media.",
        ),
    ]
    for code, markers, description in classifications:
        if any(marker in lowered for marker in markers):
            return code, description
    return None


def _is_youtube_bot_check_error(message: str) -> bool:
    lowered = message.lower()
    return "confirm you're not a bot" in lowered or "confirm you’re not a bot" in lowered


def _is_media_download_forbidden(message: str) -> bool:
    lowered = message.lower()
    return "unable to download video data" in lowered and ("http error 403" in lowered or "forbidden" in lowered)


def _is_requested_format_unavailable(message: str) -> bool:
    lowered = message.lower()
    return "requested format is not available" in lowered or "use --list-formats for a list of available formats" in lowered


def _is_network_timeout(message: str) -> bool:
    lowered = message.lower()
    return any(term in lowered for term in ("timed out", "timeout", "read operation timed out", "connection timeout"))


def _is_ytdlp_update_required(message: str) -> bool:
    lowered = message.lower()
    return any(
        term in lowered
        for term in (
            "please update to the latest version",
            "update yt-dlp",
            "your version of yt-dlp is out of date",
            "unsupported client version",
        )
    )


def _is_cookie_error(message: str) -> bool:
    lowered = message.lower()
    return any(term in lowered for term in ("cookies are no longer valid", "invalid cookies", "cookie file", "cookiesfrombrowser"))


def _should_try_public_android_fallback(message: str) -> bool:
    return _is_media_download_forbidden(message) or _is_requested_format_unavailable(message)


def _is_youtube_network_tls_error(message: str) -> bool:
    lowered = message.lower()
    return (
        ("unable to download api page" in lowered or "unable to download webpage" in lowered)
        and (
            "eof occurred in violation of protocol" in lowered
            or "sslerror" in lowered
            or "tls" in lowered
        )
    )


def _browser_cookie_spec(value: Optional[str]) -> Optional[tuple[str, Optional[str], Optional[str], Optional[str]]]:
    if not value:
        return None
    parts = str(value).split(":")
    browser = (parts[0] if parts else "").strip()
    if not browser:
        return None
    profile = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    keyring = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
    container = parts[3].strip() if len(parts) > 3 and parts[3].strip() else None
    return (browser, profile, keyring, container)


def _apply_cookie_options(ydl_opts: Dict[str, Any]) -> None:
    if settings.ytdlp_cookies_file:
        ydl_opts["cookiefile"] = str(settings.ytdlp_cookies_file)
    browser_spec = _browser_cookie_spec(settings.ytdlp_cookies_from_browser)
    if browser_spec:
        ydl_opts["cookiesfrombrowser"] = browser_spec


def _apply_impersonation_options(ydl_opts: Dict[str, Any]) -> None:
    if not settings.ytdlp_impersonate:
        return
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget
    except Exception as exc:
        raise PipelineError(
            code="yt_dlp_impersonation_unavailable",
            message="yt-dlp impersonation support is unavailable. Install curl-cffi or unset XHS_YTDLP_IMPERSONATE.",
            step="ingest",
            details={"impersonate": settings.ytdlp_impersonate, "error": str(exc)},
        ) from exc
    ydl_opts["impersonate"] = ImpersonateTarget.from_str(settings.ytdlp_impersonate.lower())


def _yt_dlp_error(code: str, message: str, raw_error: str) -> PipelineError:
    return PipelineError(
        code=code,
        message=message,
        step="ingest",
        details={
            "error": raw_error,
            "cookies_from_browser_configured": bool(settings.ytdlp_cookies_from_browser),
            "cookies_file_configured": bool(settings.ytdlp_cookies_file),
            "impersonate": settings.ytdlp_impersonate,
        },
    )


def _base_ydl_opts(
    output_template: str,
    language: str,
    *,
    download: bool,
    format_selector: str = DEFAULT_YTDLP_FORMAT,
    player_clients: Optional[list[str]] = None,
    use_cookies: bool = True,
    write_subtitles: bool = True,
) -> Dict[str, Any]:
    ydl_opts: Dict[str, Any] = {
        "outtmpl": output_template,
        "format": format_selector,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "restrictfilenames": True,
        "writesubtitles": write_subtitles,
        "writeautomaticsub": write_subtitles,
        "ignoreerrors": False,
        "quiet": True,
        "no_warnings": False,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": settings.ytdlp_socket_timeout_seconds,
        "extractor_args": {
            "youtube": {
                "player_client": player_clients or ["web"],
            }
        },
    }
    if write_subtitles:
        ydl_opts["subtitleslangs"] = list(_language_list(language))
        ydl_opts["subtitlesformat"] = "vtt/best"
    if not download:
        ydl_opts["skip_download"] = True
    if use_cookies:
        _apply_cookie_options(ydl_opts)
    _apply_impersonation_options(ydl_opts)
    return ydl_opts


def _public_android_fallback_opts(output_template: str, language: str) -> Dict[str, Any]:
    """Fallback for public YouTube videos whose web-client media URLs return 403.

    Some public videos expose a progressive 360p MP4 to the Android client even
    when the web client returns SABR/PO-token-gated media URLs that this runtime
    cannot download. Do not send browser cookies on this fallback: Android
    clients do not support account cookies in yt-dlp, and public-only fallback
    should not attempt to bypass login, paid, DRM, or regional restrictions.
    """

    return _base_ydl_opts(
        output_template,
        language,
        download=True,
        format_selector=PUBLIC_ANDROID_FALLBACK_FORMAT,
        player_clients=["android"],
        use_cookies=False,
        write_subtitles=False,
    )


def _audio_only_opts(output_template: str, language: str) -> Dict[str, Any]:
    return _base_ydl_opts(
        output_template,
        language,
        download=True,
        format_selector=AUDIO_ONLY_FORMAT,
        write_subtitles=False,
    )


def _write_text_only_media_fallback_metadata(
    info: Dict[str, Any],
    paths: ProjectPaths,
    *,
    video_path: Optional[Path],
    audio_path: Optional[Path],
    subtitle_path: Optional[Path],
    ingest_warnings: list[str],
    reason: str,
) -> Dict[str, Any]:
    return _write_metadata(
        info,
        paths,
        video_path=video_path,
        subtitle_path=subtitle_path,
        audio_path=audio_path,
        thumbnail_from_video=False,
        ingest_warnings=[
            *ingest_warnings,
            (
                f"Text-only analysis mode could not use subtitles/audio-only directly ({reason}), so it downloaded "
                "the smallest available media fallback for Whisper transcription only. Keyframes, OCR, screenshots, "
                "and image-card generation remain disabled for this run."
            ),
        ],
    )


def _download_text_only_android_fallback(
    yt_dlp_module: Any,
    url: str,
    language: str,
    paths: ProjectPaths,
    output_template: str,
    subtitle_path: Optional[Path],
    ingest_warnings: list[str],
    reason: str,
) -> Dict[str, Any]:
    fallback_opts = _public_android_fallback_opts(output_template, language)
    with yt_dlp_module.YoutubeDL(fallback_opts) as ydl:
        fallback_info = _normalize_info(ydl.extract_info(url, download=True), url)
    video_id = fallback_info.get("id")
    audio_path = _find_downloaded_audio(paths.source_dir, video_id)
    video_path = _find_downloaded_video(paths.source_dir, video_id)
    subtitle_path = _find_downloaded_subtitle(paths.source_dir, video_id) or subtitle_path
    if not audio_path and not video_path:
        raise PipelineError(
            code="media_file_missing",
            message="yt-dlp text-only Android fallback completed but no media file was found.",
            step="ingest",
            details={"source_dir": str(paths.source_dir), "video_id": video_id},
        )
    return _write_text_only_media_fallback_metadata(
        fallback_info,
        paths,
        video_path=video_path,
        audio_path=audio_path,
        subtitle_path=subtitle_path,
        ingest_warnings=ingest_warnings,
        reason=reason,
    )


def _normalize_info(info: Any, url: str) -> Dict[str, Any]:
    if not info:
        raise PipelineError(
            code="yt_dlp_no_info",
            message="yt-dlp returned no video info.",
            step="ingest",
            details={"url": url},
        )
    if not isinstance(info, dict):
        raise PipelineError(
            code="yt_dlp_no_info",
            message="yt-dlp returned an unsupported info payload.",
            step="ingest",
            details={"url": url, "payload_type": type(info).__name__},
        )
    if "entries" not in info:
        return info
    entries = [entry for entry in (info.get("entries") or []) if isinstance(entry, dict)]
    if not entries:
        raise PipelineError(
            code="yt_dlp_no_info",
            message="yt-dlp returned a playlist or collection with no usable video entries.",
            step="ingest",
            details={"url": url, "extractor": info.get("extractor_key") or info.get("extractor")},
        )
    return entries[0]


def _write_metadata(
    info: Dict[str, Any],
    paths: ProjectPaths,
    *,
    video_path: Optional[Path],
    subtitle_path: Optional[Path],
    audio_path: Optional[Path] = None,
    thumbnail_from_video: bool = True,
    ingest_warnings: Optional[list[str]] = None,
) -> Dict[str, Any]:
    thumbnail_path = _download_thumbnail(info.get("thumbnail"), paths.source_dir / "thumbnail.jpg")
    if not thumbnail_path and thumbnail_from_video and video_path:
        thumbnail_path = _thumbnail_from_video(video_path, paths.source_dir / "thumbnail.jpg")
    metadata = _safe_metadata(info, video_path, subtitle_path, audio_path=audio_path)
    if thumbnail_path:
        metadata["thumbnail_file"] = str(thumbnail_path)
    if ingest_warnings:
        metadata["ingest_warnings"] = ingest_warnings

    metadata["compliance_notice"] = (
        "Process only videos you own, public videos you are allowed to analyze, or content you have rights to use. "
        "Do not bypass paywalls, login gates, DRM, or regional restrictions."
    )
    write_json(paths.source_dir / "metadata.json", metadata)
    return metadata


def _raise_ingest_ytdlp_error(message: str, exc: Exception, url: str, language: str, paths: ProjectPaths, output_template: str) -> None:
    if _is_youtube_bot_check_error(message):
        raise _yt_dlp_error(
            "youtube_bot_check_required",
            (
                "YouTube is asking yt-dlp to sign in and confirm this request is not a bot. "
                "Configure XHS_YTDLP_COOKIES_FROM_BROWSER=chrome or XHS_YTDLP_COOKIES_FILE=/path/to/cookies.txt, then retry."
            ),
            message,
        ) from exc
    if _is_media_download_forbidden(message):
        raise _yt_dlp_error(
            "youtube_media_download_forbidden",
            (
                "YouTube returned 403 Forbidden while yt-dlp was downloading the media stream. "
                "The video metadata may be public, but the media URL is blocked for this runtime. "
                "Try exporting a fresh cookies.txt via a browser extension and set XHS_YTDLP_COOKIES_FILE, "
                "or retry from a different network/IP."
            ),
            message,
        ) from exc
    if _is_requested_format_unavailable(message):
        raise _yt_dlp_error(
            "yt_dlp_format_unavailable",
            (
                "yt-dlp could read the media page, but the requested media format is not available. "
                "Update yt-dlp and inspect the video's real formats with yt-dlp --list-formats; this does not mean the public URL is private."
            ),
            message,
        ) from exc
    if _is_cookie_error(message):
        raise _yt_dlp_error(
            "yt_dlp_cookies_invalid",
            "The configured browser cookies or cookies.txt file could not be used. Export fresh cookies from an authorized browser session and retry.",
            message,
        ) from exc
    if _is_network_timeout(message):
        raise _yt_dlp_error(
            "yt_dlp_network_timeout",
            "The yt-dlp request timed out before the platform responded. Retry later or check the target Mac's network path.",
            message,
        ) from exc
    if _is_ytdlp_update_required(message):
        raise _yt_dlp_error(
            "yt_dlp_update_required",
            "The installed yt-dlp version is rejected or too old for the current platform response. Run the fixed project updater and retry.",
            message,
        ) from exc
    if _is_youtube_network_tls_error(message):
        raise _yt_dlp_error(
            "youtube_network_tls_failed",
            (
                "yt-dlp could not complete the TLS/network request to YouTube. "
                "The video may still be public, but this runtime could not download the YouTube webpage/API response. "
                "Retry later, switch network/IP, or provide a cookies.txt file from a browser session."
            ),
            message,
        ) from exc
    restriction = _classified_restriction(message)
    if restriction:
        code, description = restriction
        raise _yt_dlp_error(code, description, message) from exc
    raise _yt_dlp_error(
        code="yt_dlp_failed",
        message="yt-dlp failed for an unclassified reason. Review details.error; the project does not infer that a public URL is private.",
        raw_error=message,
    ) from exc


def ingest_video(
    url: str,
    language: str,
    paths: ProjectPaths,
    *,
    prefer_subtitles_only: bool = False,
) -> Dict[str, Any]:
    paths.ensure()
    try:
        import yt_dlp
    except Exception as exc:
        raise PipelineError(
            code="missing_dependency",
            message="yt-dlp is not installed. Install dependencies from requirements.txt.",
            step="ingest",
        ) from exc

    output_template = str(paths.source_dir / "%(id)s.%(ext)s")
    subtitle_info: Optional[Dict[str, Any]] = None
    subtitle_path: Optional[Path] = None
    ingest_warnings: list[str] = []
    preflight_opts = _base_ydl_opts(output_template, language, download=False)
    try:
        with yt_dlp.YoutubeDL(preflight_opts) as ydl:
            subtitle_info = _normalize_info(ydl.extract_info(url, download=True), url)
        subtitle_path = _find_downloaded_subtitle(paths.source_dir, subtitle_info.get("id"))
        if prefer_subtitles_only and subtitle_path:
            return _write_metadata(
                subtitle_info,
                paths,
                video_path=None,
                subtitle_path=subtitle_path,
                thumbnail_from_video=False,
                ingest_warnings=[
                    (
                        "Text-only analysis mode found a usable subtitle track, so media download was skipped. "
                        "Keyframes, OCR, screenshots, and image-card generation are disabled for this run."
                    )
                ],
            )
    except Exception as exc:
        message = str(exc)
        if _should_try_public_android_fallback(message):
            ingest_warnings.append(
                (
                    "The subtitle preflight step could not use the primary YouTube web-client media format. "
                    "Continuing to media download fallback."
                )
            )
        else:
            _raise_ingest_ytdlp_error(message, exc, url, language, paths, output_template)

    if prefer_subtitles_only:
        audio_opts = _audio_only_opts(output_template, language)
        try:
            with yt_dlp.YoutubeDL(audio_opts) as ydl:
                audio_info = _normalize_info(ydl.extract_info(url, download=True), url)
            video_id = audio_info.get("id")
            audio_path = _find_downloaded_audio(paths.source_dir, video_id)
            video_path = _find_downloaded_video(paths.source_dir, video_id)
            subtitle_path = _find_downloaded_subtitle(paths.source_dir, video_id) or subtitle_path
            if not audio_path and video_path:
                return _write_text_only_media_fallback_metadata(
                    audio_info,
                    paths,
                    video_path=video_path,
                    audio_path=None,
                    subtitle_path=subtitle_path,
                    ingest_warnings=ingest_warnings,
                    reason="audio-only selector produced a media file instead of a standalone audio file",
                )
            if not audio_path:
                raise PipelineError(
                    code="audio_file_missing",
                    message="yt-dlp completed audio-only download but no audio file was found.",
                    step="ingest",
                    details={"source_dir": str(paths.source_dir), "video_id": video_id},
                )
            return _write_metadata(
                audio_info,
                paths,
                video_path=None,
                subtitle_path=subtitle_path,
                audio_path=audio_path,
                thumbnail_from_video=False,
                ingest_warnings=[
                    *ingest_warnings,
                    (
                        "Text-only analysis mode did not find usable subtitles during preflight, so it downloaded "
                        "audio-only for Whisper transcription and skipped full video download, keyframes, OCR, "
                        "screenshots, and image-card generation."
                    ),
                ],
            )
        except PipelineError as exc:
            if exc.code != "audio_file_missing":
                raise
            return _download_text_only_android_fallback(
                yt_dlp,
                url,
                language,
                paths,
                output_template,
                subtitle_path,
                ingest_warnings,
                exc.message,
            )
        except Exception as exc:
            message = str(exc)
            if _should_try_public_android_fallback(message):
                return _download_text_only_android_fallback(
                    yt_dlp,
                    url,
                    language,
                    paths,
                    output_template,
                    subtitle_path,
                    ingest_warnings,
                    message,
                )
            _raise_ingest_ytdlp_error(message, exc, url, language, paths, output_template)

    ydl_opts = _base_ydl_opts(output_template, language, download=True)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:
        message = str(exc)
        if _should_try_public_android_fallback(message):
            fallback_opts = _public_android_fallback_opts(output_template, language)
            try:
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                ingest_warnings.append(
                    (
                        "The primary YouTube web-client media download failed because the requested format was "
                        "unavailable or forbidden. "
                        "Retried with the public Android client without browser cookies and downloaded a low-resolution "
                        "progressive MP4 fallback."
                    )
                )
            except Exception:
                if not (subtitle_info and subtitle_path):
                    _raise_ingest_ytdlp_error(message, exc, url, language, paths, output_template)
            else:
                info = _normalize_info(info, url)
                video_id = info.get("id")
                video_path = _find_downloaded_video(paths.source_dir, video_id)
                subtitle_path = _find_downloaded_subtitle(paths.source_dir, video_id) or subtitle_path
                if not video_path:
                    if subtitle_info and subtitle_path:
                        return _write_metadata(
                            subtitle_info,
                            paths,
                            video_path=None,
                            subtitle_path=subtitle_path,
                            thumbnail_from_video=False,
                            ingest_warnings=[
                                *ingest_warnings,
                                (
                                    "YouTube media fallback completed without a usable local video file, so this run "
                                    "uses public metadata and subtitles only. Keyframe and OCR analysis are skipped "
                                    "because no video file is available."
                                ),
                            ],
                        )
                    raise PipelineError(
                        code="video_file_missing",
                        message="yt-dlp Android fallback completed but no downloaded video file was found.",
                        step="ingest",
                        details={"source_dir": str(paths.source_dir), "video_id": video_id},
                    )
                return _write_metadata(
                    info,
                    paths,
                    video_path=video_path,
                    subtitle_path=subtitle_path,
                    ingest_warnings=ingest_warnings,
                )
            if subtitle_info and subtitle_path:
                metadata = _write_metadata(
                    subtitle_info,
                    paths,
                    video_path=None,
                    subtitle_path=subtitle_path,
                    thumbnail_from_video=False,
                    ingest_warnings=[
                        (
                            "YouTube returned 403 Forbidden while downloading media, so this run uses public metadata "
                            "and subtitles only. Keyframe and OCR analysis are skipped because no video file is available."
                        )
                    ],
                )
                return metadata
        _raise_ingest_ytdlp_error(message, exc, url, language, paths, output_template)

    info = _normalize_info(info, url)

    video_id = info.get("id")
    video_path = _find_downloaded_video(paths.source_dir, video_id)
    subtitle_path = _find_downloaded_subtitle(paths.source_dir, video_id) or subtitle_path

    if not video_path:
        raise PipelineError(
            code="video_file_missing",
            message="yt-dlp completed but no downloaded video file was found.",
            step="ingest",
            details={"source_dir": str(paths.source_dir), "video_id": video_id},
        )

    return _write_metadata(info, paths, video_path=video_path, subtitle_path=subtitle_path, ingest_warnings=ingest_warnings)
