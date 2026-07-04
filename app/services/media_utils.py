import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.errors import PipelineError

EXTRA_COMMAND_DIRS = [
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/usr/local/sbin",
    "/opt/local/bin",
    "/opt/local/sbin",
]


def find_command(command: str) -> Optional[str]:
    """Find an executable command, including Homebrew paths often absent in launchd."""
    path = shutil.which(command)
    if path:
        return path
    for directory in EXTRA_COMMAND_DIRS:
        candidate = Path(directory) / command
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def command_search_path() -> str:
    """Return a PATH with Homebrew/MacPorts locations prepended for child processes."""
    existing = os.environ.get("PATH", "")
    parts = [*EXTRA_COMMAND_DIRS]
    parts.extend(part for part in existing.split(os.pathsep) if part)
    seen = set()
    unique_parts = []
    for part in parts:
        if part not in seen:
            seen.add(part)
            unique_parts.append(part)
    return os.pathsep.join(unique_parts)


def command_env() -> Dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = command_search_path()
    return env


def require_command(command: str, step: str) -> str:
    path = find_command(command)
    if not path:
        raise PipelineError(
            code="missing_dependency",
            message=f"Required command is not available: {command}",
            step=step,
            details={"command": command, "searched_paths": EXTRA_COMMAND_DIRS},
        )
    return path


def run_command(args: List[str], step: str, timeout: int = 600) -> subprocess.CompletedProcess:
    resolved_args = list(args)
    if resolved_args:
        command_path = find_command(resolved_args[0])
        if command_path:
            resolved_args[0] = command_path
    try:
        result = subprocess.run(
            resolved_args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=command_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(
            code="command_timeout",
            message=f"Command timed out while running {args[0]}",
            step=step,
            details={"args": resolved_args, "timeout": timeout},
        ) from exc
    if result.returncode != 0:
        raise PipelineError(
            code="command_failed",
            message=f"Command failed while running {args[0]}",
            step=step,
            details={
                "args": resolved_args,
                "returncode": result.returncode,
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-4000:],
            },
        )
    return result


def ffprobe_duration(video_path: Path) -> float:
    ffprobe = require_command("ffprobe", "media_probe")
    result = run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(video_path),
        ],
        step="media_probe",
        timeout=60,
    )
    payload: Dict[str, Any] = json.loads(result.stdout)
    try:
        return float(payload["format"]["duration"])
    except (KeyError, ValueError) as exc:
        raise PipelineError(
            code="duration_unavailable",
            message="Could not read media duration with ffprobe.",
            step="media_probe",
            details={"path": str(video_path), "ffprobe": payload},
        ) from exc
