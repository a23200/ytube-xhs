import re
import time
import urllib.parse
from dataclasses import asdict, dataclass
from typing import Optional

import httpx


SUPPORTED_SOURCE_PLATFORMS = ("youtube", "douyin", "bilibili", "toutiao")

PLATFORM_NAMES = {
    "youtube": "YouTube",
    "douyin": "抖音",
    "bilibili": "哔哩哔哩",
    "toutiao": "今日头条/西瓜视频",
}

PLATFORM_LOGIN_URLS = {
    "youtube": "https://accounts.google.com/ServiceLogin?service=youtube",
    "douyin": "https://www.douyin.com/",
    "bilibili": "https://passport.bilibili.com/login",
    "toutiao": "https://www.toutiao.com/",
}

PLATFORM_DOMAINS = {
    "youtube": ("youtube.com", "youtu.be", "google.com", "googlevideo.com"),
    "douyin": ("douyin.com", "iesdouyin.com", "amemv.com", "byteoversea.com"),
    "bilibili": ("bilibili.com", "b23.tv", "bili2233.cn", "bilivideo.com", "hdslb.com"),
    "toutiao": ("toutiao.com", "ixigua.com", "snssdk.com", "bytedance.com"),
}

SHORT_LINK_HOSTS = {
    "v.douyin.com": "douyin",
    "b23.tv": "bilibili",
    "bili2233.cn": "bilibili",
    "t.toutiao.com": "toutiao",
    "m.toutiao.com": "toutiao",
    "v.ixigua.com": "toutiao",
}

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_TRAILING_PUNCTUATION = ",.;:!?，。；：！？、)]}）】》>"
_DOUYIN_ID_RE = re.compile(r"/(?:video|note|share/video|share/note)/(\d{15,})", re.IGNORECASE)
_BILIBILI_ID_RE = re.compile(r"/video/((?:BV[0-9A-Za-z]+)|(?:av\d+))", re.IGNORECASE)
_YOUTUBE_ID_RE = re.compile(r"^[0-9A-Za-z_-]{6,20}$")


class SourceUrlError(ValueError):
    def __init__(self, code: str, message: str, details: Optional[dict] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class SourceUrl:
    original_url: str
    normalized_url: str
    source_platform: Optional[str]
    original_host: str
    normalized_host: str
    short_link: bool
    redirect_chain: tuple[str, ...] = ()
    redirect_attempts: int = 0

    def diagnostics(self) -> dict:
        payload = asdict(self)
        payload["redirect_chain"] = list(self.redirect_chain)
        return payload


def extract_http_url(value: object) -> str:
    text = str(value or "").strip()
    match = _URL_RE.search(text)
    if not match:
        raise SourceUrlError(
            "source_url_missing",
            "No HTTP or HTTPS video URL was found in the submitted text.",
            {"input_length": len(text)},
        )
    url = match.group(0).rstrip(_TRAILING_PUNCTUATION)
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise SourceUrlError("source_url_invalid", "The submitted video URL is invalid.", {"error": str(exc)}) from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise SourceUrlError("source_url_invalid", "The submitted video URL must use HTTP or HTTPS.")
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, ""))


def _host_matches(host: str, domain: str) -> bool:
    host = host.lower().rstrip(".")
    domain = domain.lower().lstrip(".").rstrip(".")
    return host == domain or host.endswith(f".{domain}")


def source_platform_for_host(host: str) -> Optional[str]:
    normalized = (host or "").lower().rstrip(".")
    if normalized in SHORT_LINK_HOSTS:
        return SHORT_LINK_HOSTS[normalized]
    for platform, domains in PLATFORM_DOMAINS.items():
        if any(_host_matches(normalized, domain) for domain in domains):
            return platform
    return None


def source_platform_for_url(value: object) -> Optional[str]:
    try:
        url = extract_http_url(value)
        return source_platform_for_host(urllib.parse.urlsplit(url).hostname or "")
    except (SourceUrlError, ValueError):
        return None


def platform_domain_matches(platform: str, domain: str) -> bool:
    return any(_host_matches(domain.lstrip("."), item) for item in PLATFORM_DOMAINS.get(platform, ()))


def _canonicalize_youtube(parsed: urllib.parse.SplitResult) -> str:
    host = (parsed.hostname or "").lower()
    path_parts = [part for part in parsed.path.split("/") if part]
    video_id = None
    if _host_matches(host, "youtu.be") and path_parts:
        video_id = path_parts[0]
    elif path_parts and path_parts[0].lower() in {"shorts", "embed", "live"} and len(path_parts) > 1:
        video_id = path_parts[1]
    else:
        values = urllib.parse.parse_qs(parsed.query).get("v") or []
        video_id = values[0] if values else None
    if video_id and _YOUTUBE_ID_RE.fullmatch(video_id):
        return f"https://www.youtube.com/watch?v={video_id}"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def _canonicalize_douyin(parsed: urllib.parse.SplitResult) -> str:
    match = _DOUYIN_ID_RE.search(parsed.path)
    if match:
        return f"https://www.douyin.com/video/{match.group(1)}"
    query = urllib.parse.parse_qs(parsed.query)
    for key in ("modal_id", "item_id", "video_id", "note_id"):
        value = (query.get(key) or [""])[0]
        if re.fullmatch(r"\d{15,}", value):
            return f"https://www.douyin.com/video/{value}"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def _canonicalize_bilibili(parsed: urllib.parse.SplitResult) -> str:
    match = _BILIBILI_ID_RE.search(parsed.path)
    if match:
        return f"https://www.bilibili.com/video/{match.group(1)}"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def canonicalize_source_url(url: str, platform: Optional[str] = None) -> str:
    parsed = urllib.parse.urlsplit(extract_http_url(url))
    platform = platform or source_platform_for_host(parsed.hostname or "")
    if platform == "youtube":
        return _canonicalize_youtube(parsed)
    if platform == "douyin":
        return _canonicalize_douyin(parsed)
    if platform == "bilibili":
        return _canonicalize_bilibili(parsed)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def _resolve_short_link(
    url: str,
    expected_platform: str,
    *,
    timeout_seconds: float,
    attempts: int,
) -> tuple[str, tuple[str, ...], int]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    last_error: Optional[Exception] = None
    attempts = max(1, attempts)
    for attempt in range(1, attempts + 1):
        try:
            timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
            chain_items = []
            current_url = url
            with httpx.Client(follow_redirects=False, timeout=timeout, headers=headers) as client:
                for _redirect_index in range(8):
                    chain_items.append(current_url)
                    with client.stream("GET", current_url) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise SourceUrlError(
                                    "source_url_redirect_failed",
                                    "The platform short link returned a redirect without a destination.",
                                    {"source_platform": expected_platform, "redirect_chain": chain_items},
                                )
                            next_url = urllib.parse.urljoin(current_url, location)
                            next_platform = source_platform_for_url(next_url)
                            if next_platform != expected_platform:
                                raise SourceUrlError(
                                    "source_url_redirect_mismatch",
                                    "The short link redirected outside the expected video platform.",
                                    {
                                        "source_platform": expected_platform,
                                        "final_platform": next_platform,
                                        "redirect_chain": [*chain_items, next_url],
                                    },
                                )
                            current_url = next_url
                            continue
                        response.raise_for_status()
                        final_url = str(response.url)
                        break
                else:
                    raise SourceUrlError(
                        "source_url_redirect_failed",
                        "The platform short link exceeded the redirect limit.",
                        {"source_platform": expected_platform, "redirect_chain": chain_items},
                    )
            chain = tuple(chain_items)
            final_platform = source_platform_for_url(final_url)
            if final_platform != expected_platform:
                raise SourceUrlError(
                    "source_url_redirect_mismatch",
                    "The short link redirected outside the expected video platform.",
                    {
                        "source_platform": expected_platform,
                        "final_platform": final_platform,
                        "redirect_chain": list(chain),
                    },
                )
            return final_url, chain, attempt
        except SourceUrlError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(2.0, 0.5 * (2 ** (attempt - 1))))
    code = "source_url_redirect_timeout" if isinstance(last_error, httpx.TimeoutException) else "source_url_redirect_failed"
    message = (
        "The platform short link did not finish redirecting before the timeout."
        if code == "source_url_redirect_timeout"
        else "The platform short link could not be resolved."
    )
    raise SourceUrlError(
        code,
        message,
        {
            "url": url,
            "source_platform": expected_platform,
            "attempts": attempts,
            "timeout_seconds": timeout_seconds,
            "error": str(last_error or "unknown redirect error"),
        },
    )


def prepare_source_url(
    value: object,
    *,
    resolve_short_links: bool = True,
    redirect_timeout_seconds: float = 12.0,
    redirect_attempts: int = 2,
) -> SourceUrl:
    original_url = extract_http_url(value)
    parsed = urllib.parse.urlsplit(original_url)
    original_host = (parsed.hostname or "").lower()
    platform = source_platform_for_host(original_host)
    short_link = original_host in SHORT_LINK_HOSTS
    redirect_chain: tuple[str, ...] = ()
    attempts = 0
    resolved_url = original_url
    if short_link and platform and resolve_short_links:
        resolved_url, redirect_chain, attempts = _resolve_short_link(
            original_url,
            platform,
            timeout_seconds=max(1.0, redirect_timeout_seconds),
            attempts=max(1, redirect_attempts),
        )
    normalized_url = canonicalize_source_url(resolved_url, platform)
    normalized_host = (urllib.parse.urlsplit(normalized_url).hostname or "").lower()
    normalized_platform = source_platform_for_host(normalized_host)
    if platform and normalized_platform != platform:
        raise SourceUrlError(
            "source_url_platform_mismatch",
            "The normalized URL no longer belongs to the detected source platform.",
            {
                "source_platform": platform,
                "original_url": original_url,
                "normalized_url": normalized_url,
            },
        )
    return SourceUrl(
        original_url=original_url,
        normalized_url=normalized_url,
        source_platform=normalized_platform or platform,
        original_host=original_host,
        normalized_host=normalized_host,
        short_link=short_link,
        redirect_chain=redirect_chain,
        redirect_attempts=attempts,
    )


def extractor_matches_platform(platform: Optional[str], extractor: object) -> bool:
    if not platform or not extractor:
        return True
    normalized = re.sub(r"[^a-z0-9]", "", str(extractor).lower())
    if platform == "youtube":
        return normalized.startswith("youtube")
    if platform == "douyin":
        return normalized.startswith("douyin")
    if platform == "bilibili":
        return normalized.startswith("bilibili")
    if platform == "toutiao":
        return normalized.startswith("toutiao") or normalized.startswith("ixigua")
    return True
