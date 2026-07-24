import getpass
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from app.services.config import settings
from app.services.source_urls import (
    PLATFORM_LOGIN_URLS,
    PLATFORM_NAMES,
    SUPPORTED_SOURCE_PLATFORMS,
    SourceUrlError,
    extractor_matches_platform,
    platform_domain_matches,
    prepare_source_url,
)


MAX_COOKIE_FILE_BYTES = 5 * 1024 * 1024
SUPPORTED_BROWSER_IMPORTS = ("chrome", "chromium", "brave", "edge", "safari", "firefox")

_MACOS_BROWSER_ROOTS = {
    "chrome": "Google/Chrome",
    "chromium": "Chromium",
    "brave": "BraveSoftware/Brave-Browser",
    "edge": "Microsoft Edge",
}

AUTH_COOKIE_NAMES = {
    "youtube": {"SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO", "__Secure-3PSID", "__Secure-3PAPISID"},
    "douyin": {"sessionid", "sessionid_ss", "sid_guard", "uid_tt", "uid_tt_ss", "passport_csrf_token"},
    "bilibili": {"SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5"},
    "toutiao": {"sessionid", "sessionid_ss", "sid_guard", "uid_tt", "uid_tt_ss", "passport_csrf_token"},
}

_SENSITIVE_HEADER_RE = re.compile(r"(?im)^(cookie|authorization|proxy-authorization)\s*:\s*.*$")
_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)([?&](?:token|access_token|auth|authorization|cookie|session|signature|sig)=)[^&\s]+"
)
_lock = threading.RLock()


class CookieManagerError(ValueError):
    def __init__(self, code: str, message: str, details: Optional[dict] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


@dataclass(frozen=True)
class CookieRow:
    domain: str
    include_subdomains: bool
    path: str
    secure: bool
    expires: int
    name: str
    value: str
    http_only: bool = False

    def netscape_line(self) -> str:
        domain = self.domain
        if self.http_only:
            domain = f"#HttpOnly_{domain}"
        return "\t".join(
            (
                domain,
                "TRUE" if self.include_subdomains else "FALSE",
                self.path or "/",
                "TRUE" if self.secure else "FALSE",
                str(max(0, int(self.expires or 0))),
                self.name,
                self.value,
            )
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def service_user() -> str:
    try:
        import pwd

        return pwd.getpwuid(os.geteuid()).pw_name
    except (ImportError, KeyError, OSError):
        return getpass.getuser()


def service_home() -> Path:
    try:
        import pwd

        home = pwd.getpwuid(os.geteuid()).pw_dir
    except (ImportError, KeyError, OSError):
        home = os.getenv("HOME") or str(Path.home())
    return Path(home).expanduser().resolve()


def _validate_platform(platform: str) -> str:
    normalized = str(platform or "").strip().lower()
    if normalized not in SUPPORTED_SOURCE_PLATFORMS:
        raise CookieManagerError(
            "cookie_platform_unsupported",
            "Cookie management supports YouTube, Douyin, Bilibili, and Toutiao only.",
            {"platform": normalized},
        )
    return normalized


def auth_dir() -> Path:
    configured = os.getenv("XHS_YTDLP_AUTH_DIR", "").strip()
    path = Path(configured).expanduser() if configured else settings.runtime_dir / "auth"
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def managed_cookie_path(platform: str) -> Path:
    return auth_dir() / f"{_validate_platform(platform)}.cookies.txt"


def _browser_profile_root(browser: str) -> Optional[Path]:
    home = service_home()
    if browser == "firefox":
        return home / "Library" / "Application Support" / "Firefox" / "Profiles"
    relative_root = _MACOS_BROWSER_ROOTS.get(browser)
    if relative_root:
        return home / "Library" / "Application Support" / relative_root
    return None


def _resolved_browser_profile(browser: str, profile: Optional[str]) -> Optional[Path]:
    if not profile:
        return None
    profile_path = Path(profile).expanduser()
    if profile_path.is_absolute():
        return profile_path.resolve()
    if ".." in profile_path.parts:
        raise CookieManagerError(
            "cookie_browser_profile_invalid",
            "The browser Profile must be a discovered Profile name or an absolute path.",
            {"browser": browser, "profile": profile},
        )
    root = _browser_profile_root(browser)
    return (root / profile_path).resolve() if root else profile_path.resolve()


def discover_browser_profiles(browser: str) -> list[str]:
    browser = str(browser or "").strip().lower()
    if browser not in SUPPORTED_BROWSER_IMPORTS:
        return []
    if browser == "firefox":
        database_names = ("cookies.sqlite",)
    elif browser in _MACOS_BROWSER_ROOTS:
        database_names = ("Network/Cookies", "Cookies")
    else:
        return []
    root = _browser_profile_root(browser)
    if root is None:
        return []
    if not root.is_dir():
        return []
    found = []
    try:
        directories = [item for item in root.iterdir() if item.is_dir()]
    except OSError:
        return []
    for directory in directories:
        databases = [directory / relative for relative in database_names]
        readable = [path for path in databases if path.is_file() and os.access(path, os.R_OK)]
        if not readable:
            continue
        try:
            modified = max(path.stat().st_mtime for path in readable)
        except OSError:
            modified = 0.0
        found.append((modified, directory.name))
    found.sort(key=lambda item: (-item[0], item[1].lower()))
    return [name for _modified, name in found]


def _configured_platform_cookie_path(platform: str) -> Optional[Path]:
    key = f"XHS_YTDLP_{platform.upper()}_COOKIES_FILE"
    value = os.getenv(key, "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def active_cookie_source(platform: str) -> tuple[Optional[Path], str]:
    platform = _validate_platform(platform)
    managed = managed_cookie_path(platform)
    if managed.exists():
        return managed, "managed"
    configured = _configured_platform_cookie_path(platform)
    if configured:
        return configured, "platform_environment"
    legacy = settings.ytdlp_cookies_file
    if legacy:
        return Path(legacy), "legacy_global"
    return None, "none"


def sanitize_error_text(value: object) -> str:
    text = str(value or "")
    text = _SENSITIVE_HEADER_RE.sub(lambda match: f"{match.group(1)}: <redacted>", text)
    return _SENSITIVE_QUERY_RE.sub(lambda match: f"{match.group(1)}<redacted>", text)


def _parse_cookie_line(raw_line: str, line_number: int) -> Optional[CookieRow]:
    line = raw_line.rstrip("\r\n")
    http_only = False
    if line.startswith("#HttpOnly_"):
        line = line[len("#HttpOnly_") :]
        http_only = True
    elif not line or line.lstrip().startswith("#"):
        return None
    parts = line.split("\t")
    if len(parts) != 7:
        raise CookieManagerError(
            "cookie_file_invalid",
            "The Cookie file is not valid Netscape cookies.txt format.",
            {"line": line_number, "reason": "expected 7 tab-separated fields"},
        )
    domain, include_subdomains, path, secure, expires, name, value = parts
    if not domain or not name or any(char in name + value for char in "\r\n\t"):
        raise CookieManagerError(
            "cookie_file_invalid",
            "The Cookie file contains an invalid Cookie row.",
            {"line": line_number},
        )
    try:
        expires_value = int(expires or 0)
    except ValueError as exc:
        raise CookieManagerError(
            "cookie_file_invalid",
            "The Cookie file contains an invalid expiry timestamp.",
            {"line": line_number},
        ) from exc
    return CookieRow(
        domain=domain,
        include_subdomains=include_subdomains.upper() == "TRUE",
        path=path or "/",
        secure=secure.upper() == "TRUE",
        expires=max(0, expires_value),
        name=name,
        value=value,
        http_only=http_only,
    )


def _rows_from_text(text: str) -> list[CookieRow]:
    return [row for index, line in enumerate(text.splitlines(), start=1) if (row := _parse_cookie_line(line, index))]


def _rows_from_path(path: Path) -> list[CookieRow]:
    if not path.exists() or not path.is_file():
        raise CookieManagerError(
            "cookie_file_missing",
            "The configured Cookie file does not exist.",
            {"path": str(path)},
        )
    try:
        size = path.stat().st_size
        if size > MAX_COOKIE_FILE_BYTES:
            raise CookieManagerError(
                "cookie_file_too_large",
                "The Cookie file is larger than the 5 MB safety limit.",
                {"size_bytes": size},
            )
        return _rows_from_text(path.read_text(encoding="utf-8-sig"))
    except CookieManagerError:
        raise
    except (OSError, UnicodeError) as exc:
        raise CookieManagerError(
            "cookie_file_unreadable",
            "The Cookie file could not be read by the service user.",
            {"path": str(path), "error": sanitize_error_text(exc)},
        ) from exc


def _platform_rows(rows: Iterable[CookieRow], platform: str) -> list[CookieRow]:
    platform = _validate_platform(platform)
    filtered = []
    for row in rows:
        if not platform_domain_matches(platform, row.domain):
            continue
        if platform == "youtube" and row.domain.lstrip(".").endswith("google.com") and row.name not in AUTH_COOKIE_NAMES[platform]:
            continue
        filtered.append(row)
    return filtered


def _atomic_write_cookie_file(path: Path, rows: Iterable[CookieRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temp_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("# Netscape HTTP Cookie File\n")
            handle.write("# Managed by ytube-xhs. Cookie values are never exposed through the API.\n\n")
            for row in rows:
                handle.write(row.netscape_line())
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _verification_path() -> Path:
    return auth_dir() / "verification.json"


def _read_verifications() -> dict:
    path = _verification_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_verifications(payload: dict) -> None:
    path = _verification_path()
    descriptor, temp_name = tempfile.mkstemp(prefix=".verification.", suffix=".tmp", dir=path.parent)
    temporary = Path(temp_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _remember_verification(platform: str, payload: dict) -> None:
    with _lock:
        values = _read_verifications()
        values[platform] = payload
        _write_verifications(values)


def _iso_timestamp(timestamp: Optional[int]) -> Optional[str]:
    if not timestamp:
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _status_from_rows(platform: str, rows: list[CookieRow]) -> dict:
    now = int(datetime.now(timezone.utc).timestamp())
    valid_rows = [row for row in rows if row.expires == 0 or row.expires > now]
    expired_rows = [row for row in rows if row.expires > 0 and row.expires <= now]
    persistent_expiry = [row.expires for row in valid_rows if row.expires > 0]
    auth_names = AUTH_COOKIE_NAMES[platform]
    auth_cookie_count = sum(1 for row in valid_rows if row.name in auth_names)
    if not rows:
        status = "invalid"
        status_label = "没有该平台 Cookie"
    elif not valid_rows:
        status = "expired"
        status_label = "Cookie 已过期"
    elif auth_cookie_count:
        status = "session_detected"
        status_label = "检测到登录会话"
    else:
        status = "readable"
        status_label = "Cookie 可读取"
    return {
        "status": status,
        "status_label": status_label,
        "cookie_count": len(rows),
        "valid_cookie_count": len(valid_rows),
        "expired_cookie_count": len(expired_rows),
        "session_cookie_count": sum(1 for row in valid_rows if row.expires == 0),
        "auth_cookie_count": auth_cookie_count,
        "earliest_expiry": _iso_timestamp(min(persistent_expiry) if persistent_expiry else None),
        "latest_expiry": _iso_timestamp(max(persistent_expiry) if persistent_expiry else None),
        "domains": sorted({row.domain.lstrip(".").lower() for row in rows}),
    }


def cookie_status(platform: str) -> dict:
    platform = _validate_platform(platform)
    path, source = active_cookie_source(platform)
    base = {
        "platform": platform,
        "name": PLATFORM_NAMES[platform],
        "login_url": PLATFORM_LOGIN_URLS[platform],
        "source": source,
        "configured": bool(path),
        "browser_import_available": True,
        "legacy_browser_configured": bool(settings.ytdlp_cookies_from_browser),
        "last_verification": _read_verifications().get(platform),
    }
    if not path:
        if settings.ytdlp_cookies_from_browser:
            return {
                **base,
                "source": "legacy_browser",
                "configured": True,
                "status": "browser_configured",
                "status_label": "浏览器读取待检测",
                "cookie_count": 0,
                "domains": [],
            }
        return {**base, "status": "unconfigured", "status_label": "未配置", "cookie_count": 0, "domains": []}
    base["file_exists"] = path.exists()
    base["file_mode"] = oct(path.stat().st_mode & 0o777) if path.exists() else None
    base["managed_file"] = source == "managed"
    try:
        rows = _platform_rows(_rows_from_path(path), platform)
        return {**base, **_status_from_rows(platform, rows)}
    except CookieManagerError as exc:
        return {
            **base,
            "status": "invalid",
            "status_label": "配置无效",
            "cookie_count": 0,
            "domains": [],
            "error": exc.to_dict(),
        }


def list_cookie_statuses() -> dict:
    return {
        "platforms": [cookie_status(platform) for platform in SUPPORTED_SOURCE_PLATFORMS],
        "supported_browsers": list(SUPPORTED_BROWSER_IMPORTS),
        "browser_profiles": {
            browser: discover_browser_profiles(browser) for browser in SUPPORTED_BROWSER_IMPORTS
        },
        "browser_import": {
            "service_user": service_user(),
            "service_home": str(service_home()),
            "timeout_seconds": settings.ytdlp_browser_cookie_timeout_seconds,
        },
        "auth_dir": str(auth_dir()),
        "security": {
            "cookie_values_exposed": False,
            "file_mode": "0600",
            "passwords_stored": False,
        },
    }


def import_cookie_text(platform: str, content: bytes) -> dict:
    platform = _validate_platform(platform)
    if len(content) > MAX_COOKIE_FILE_BYTES:
        raise CookieManagerError(
            "cookie_file_too_large",
            "The Cookie file is larger than the 5 MB safety limit.",
            {"size_bytes": len(content)},
        )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeError as exc:
        raise CookieManagerError("cookie_file_invalid", "The Cookie file must be UTF-8 text.") from exc
    rows = _platform_rows(_rows_from_text(text), platform)
    if not rows:
        raise CookieManagerError(
            "cookie_platform_domain_missing",
            "The uploaded file contains no Cookie rows for the selected platform.",
            {"platform": platform},
        )
    path = managed_cookie_path(platform)
    with _lock:
        _atomic_write_cookie_file(path, rows)
    result = cookie_status(platform)
    result["imported"] = True
    result["imported_cookie_count"] = len(rows)
    return result


class _CaptureLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, message: object) -> None:
        text = str(message or "")
        if text.startswith("[debug]"):
            self.messages.append(sanitize_error_text(text))

    def warning(self, message: object) -> None:
        self.messages.append(sanitize_error_text(message))

    def error(self, message: object) -> None:
        self.messages.append(sanitize_error_text(message))


def _browser_import_parameters(platform: str, browser: str, profile: Optional[str]) -> tuple[str, str, Optional[str]]:
    platform = _validate_platform(platform)
    browser = str(browser or "").strip().lower()
    if browser not in SUPPORTED_BROWSER_IMPORTS:
        raise CookieManagerError(
            "cookie_browser_unsupported",
            "The selected browser is not supported for Cookie import.",
            {"browser": browser, "supported": list(SUPPORTED_BROWSER_IMPORTS)},
        )
    profile = str(profile or "").strip() or None
    if profile and (len(profile) > 256 or "\x00" in profile):
        raise CookieManagerError("cookie_browser_profile_invalid", "The browser profile name or path is invalid.")
    return platform, browser, profile


def export_browser_cookie_file(
    platform: str,
    browser: str,
    profile: Optional[str],
    output_path: Path,
) -> dict:
    platform, browser, profile = _browser_import_parameters(platform, browser, profile)
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.cookies import load_cookies
    except Exception as exc:
        raise CookieManagerError(
            "cookie_browser_import_failed",
            "The isolated browser Cookie reader could not load yt-dlp.",
            {"browser": browser, "profile": profile, "error": sanitize_error_text(exc)},
        ) from exc

    candidates: list[Optional[str]] = [profile] if profile else list(discover_browser_profiles(browser))
    if not candidates:
        candidates = [None]
    checked = []
    failures = []
    readable_profiles = 0
    for candidate in candidates:
        label = candidate or "automatic"
        checked.append(label)
        logger = _CaptureLogger()
        try:
            resolved_profile = _resolved_browser_profile(browser, candidate)
            browser_spec = (browser, str(resolved_profile) if resolved_profile else None, None, None)
            with YoutubeDL({"quiet": True, "no_warnings": True, "logger": logger}) as ydl:
                jar = load_cookies(None, browser_spec, ydl)
            readable_profiles += 1
            rows = []
            for item in jar:
                row = CookieRow(
                    domain=str(item.domain or ""),
                    include_subdomains=bool(item.domain_initial_dot),
                    path=str(item.path or "/"),
                    secure=bool(item.secure),
                    expires=int(item.expires or 0),
                    name=str(item.name or ""),
                    value=str(item.value or ""),
                    http_only=bool((item._rest or {}).get("HttpOnly")),
                )
                if row.name and platform_domain_matches(platform, row.domain):
                    rows.append(row)
            rows = _platform_rows(rows, platform)
            if not rows:
                continue
            _atomic_write_cookie_file(output_path, rows)
            return {
                "platform": platform,
                "browser": browser,
                "profile": candidate,
                "profiles_checked": checked,
                "cookie_count": len(rows),
            }
        except Exception as exc:
            failures.append(
                {
                    "profile": label,
                    "error": sanitize_error_text(exc),
                    "browser_messages": [message for message in logger.messages[-3:] if message],
                }
            )
    if readable_profiles:
        raise CookieManagerError(
            "cookie_browser_platform_missing",
            (
                f"No {PLATFORM_NAMES[platform]} Cookies were found in the checked {browser} Profiles. "
                "Select the Profile shown by chrome://version, or upload a Netscape cookies.txt file."
            ),
            {
                "platform": platform,
                "browser": browser,
                "profile": profile,
                "profiles_checked": checked,
            },
        )
    raise CookieManagerError(
        "cookie_browser_import_failed",
        (
            "The service could not read any detected browser Profile. Confirm the displayed service user owns the browser "
            "Profile and can unlock the macOS login keychain, or upload a Netscape cookies.txt file."
        ),
        {
            "browser": browser,
            "profile": profile,
            "profiles_checked": checked,
            "browser_failures": failures[-5:],
        },
    )


def _worker_error(completed: subprocess.CompletedProcess[str], browser: str, profile: Optional[str]) -> CookieManagerError:
    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError:
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and error.get("code") and error.get("message"):
        details = dict(error.get("details") or {})
        details.setdefault("service_user", service_user())
        details.setdefault("service_home", str(service_home()))
        return CookieManagerError(str(error["code"]), str(error["message"]), details)
    return CookieManagerError(
        "cookie_browser_import_failed",
        (
            "The browser Cookie reader failed. Confirm the browser Profile and macOS service user; "
            "if launchd cannot unlock the browser keychain, upload a Netscape cookies.txt file instead."
        ),
        {
            "browser": browser,
            "profile": profile,
            "service_user": service_user(),
            "service_home": str(service_home()),
            "error": sanitize_error_text(completed.stderr or completed.stdout or "browser Cookie worker failed")[-2000:],
        },
    )


def import_from_browser(platform: str, browser: str, profile: Optional[str] = None) -> dict:
    platform, browser, profile = _browser_import_parameters(platform, browser, profile)
    timeout_seconds = settings.ytdlp_browser_cookie_timeout_seconds
    project_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix=".browser-import.", dir=auth_dir()) as temporary_dir:
        output_path = Path(temporary_dir) / f"{platform}.cookies.txt"
        command = [
            sys.executable,
            "-m",
            "app.services.browser_cookie_worker",
            "--platform",
            platform,
            "--browser",
            browser,
            "--output",
            str(output_path),
        ]
        if profile:
            command.extend(("--profile", profile))
        try:
            worker_env = {**os.environ, "HOME": str(service_home())}
            completed = subprocess.run(
                command,
                cwd=project_root,
                env=worker_env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CookieManagerError(
                "cookie_browser_import_timeout",
                (
                    f"Reading {browser} Cookies exceeded {timeout_seconds} seconds. Confirm the service runs as the same "
                    "logged-in macOS user and approve any keychain prompt; otherwise upload a Netscape cookies.txt file."
                ),
                {
                    "platform": platform,
                    "browser": browser,
                    "profile": profile,
                    "service_user": service_user(),
                    "service_home": str(service_home()),
                    "timeout_seconds": timeout_seconds,
                    "error": f"browser Cookie import timed out after {timeout_seconds} seconds",
                },
            ) from exc
        except OSError as exc:
            raise CookieManagerError(
                "cookie_browser_import_failed",
                "The service could not start the isolated browser Cookie reader.",
                {
                    "browser": browser,
                    "profile": profile,
                    "service_user": service_user(),
                    "service_home": str(service_home()),
                    "error": sanitize_error_text(exc),
                },
            ) from exc
        if completed.returncode != 0:
            raise _worker_error(completed, browser, profile)
        try:
            worker_payload = json.loads(completed.stdout or "{}")
        except ValueError:
            worker_payload = {}
        worker_result = worker_payload.get("result") if isinstance(worker_payload, dict) else {}
        if not isinstance(worker_result, dict):
            worker_result = {}
        selected_profile = worker_result.get("profile", profile)
        profiles_checked = worker_result.get("profiles_checked") or ([profile] if profile else [])
        if not output_path.exists():
            raise CookieManagerError(
                "cookie_browser_import_failed",
                "The browser Cookie reader exited without producing a Cookie file.",
                {
                    "browser": browser,
                    "profile": profile,
                    "service_user": service_user(),
                    "service_home": str(service_home()),
                },
            )
        rows = _platform_rows(_rows_from_path(output_path), platform)
        if not rows:
            raise CookieManagerError(
                "cookie_browser_platform_missing",
                "The selected browser Profile contains no Cookies for this platform.",
                {
                    "platform": platform,
                    "browser": browser,
                    "profile": profile,
                    "service_user": service_user(),
                    "service_home": str(service_home()),
                },
            )
        with _lock:
            _atomic_write_cookie_file(managed_cookie_path(platform), rows)
    result = cookie_status(platform)
    result.update(
        {
            "imported": True,
            "browser": browser,
            "profile": selected_profile,
            "profiles_checked": profiles_checked,
            "service_user": service_user(),
            "imported_cookie_count": len(rows),
        }
    )
    return result


def delete_managed_cookie(platform: str) -> dict:
    platform = _validate_platform(platform)
    path = managed_cookie_path(platform)
    with _lock:
        path.unlink(missing_ok=True)
        values = _read_verifications()
        values.pop(platform, None)
        _write_verifications(values)
    return cookie_status(platform)


def cookie_options_for_url(url: object) -> dict:
    try:
        prepared = prepare_source_url(url, resolve_short_links=False)
    except SourceUrlError:
        return {}
    platform = prepared.source_platform
    if platform:
        path, source = active_cookie_source(platform)
        if path and path.exists() and os.access(path, os.R_OK):
            return {"cookiefile": str(path), "cookie_source": source, "source_platform": platform}
    if settings.ytdlp_cookies_from_browser:
        return {
            "cookiesfrombrowser": settings.ytdlp_cookies_from_browser,
            "cookie_source": "legacy_browser",
            "source_platform": platform,
        }
    return {"cookie_source": "none", "source_platform": platform}


def verify_cookie(platform: str, url: object) -> dict:
    platform = _validate_platform(platform)
    try:
        prepared = prepare_source_url(
            url,
            redirect_timeout_seconds=getattr(settings, "ytdlp_redirect_timeout_seconds", 12),
            redirect_attempts=getattr(settings, "ytdlp_extract_attempts", 2),
        )
    except SourceUrlError as exc:
        raise CookieManagerError(exc.code, exc.message, exc.details) from exc
    if prepared.source_platform != platform:
        raise CookieManagerError(
            "cookie_verify_platform_mismatch",
            "The verification URL does not belong to the selected platform.",
            {"selected_platform": platform, **prepared.diagnostics()},
        )
    path, source = active_cookie_source(platform)
    if not path or not path.exists():
        raise CookieManagerError(
            "cookie_not_configured",
            "Import or upload a Cookie file for this platform before verification.",
            {"platform": platform},
        )
    try:
        import yt_dlp

        options = {
            "cookiefile": str(path),
            "skip_download": True,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": False,
            "socket_timeout": settings.ytdlp_socket_timeout_seconds,
            "retries": 2,
            "extractor_retries": 2,
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info: Any = ydl.extract_info(prepared.normalized_url, download=False)
        if isinstance(info, dict) and "entries" in info:
            info = next((entry for entry in (info.get("entries") or []) if isinstance(entry, dict)), None)
        if not isinstance(info, dict):
            raise CookieManagerError("cookie_verify_no_info", "yt-dlp returned no video metadata during Cookie verification.")
        extractor = info.get("extractor_key") or info.get("extractor")
        if not extractor_matches_platform(platform, extractor):
            raise CookieManagerError(
                "cookie_verify_wrong_extractor",
                "yt-dlp did not select the expected platform extractor for this URL.",
                {"extractor": extractor, **prepared.diagnostics()},
            )
        verification = {
            "ok": True,
            "verified_at": _utc_now(),
            "source": source,
            "url": prepared.normalized_url,
            "extractor": extractor,
            "video_id": info.get("id"),
            "title": info.get("title"),
            "duration": info.get("duration"),
        }
        _remember_verification(platform, verification)
        return {**cookie_status(platform), "verification": verification}
    except CookieManagerError:
        raise
    except Exception as exc:
        failure = {
            "ok": False,
            "verified_at": _utc_now(),
            "source": source,
            "url": prepared.normalized_url,
            "error": sanitize_error_text(exc),
        }
        _remember_verification(platform, failure)
        raise CookieManagerError(
            "cookie_verification_failed",
            "yt-dlp could not verify this platform Cookie against the supplied video URL.",
            {**failure, **prepared.diagnostics()},
        ) from exc
