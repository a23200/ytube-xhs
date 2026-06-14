import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from app.services.errors import PipelineError


def require_command(command: str, step: str) -> str:
    path = shutil.which(command)
    if not path:
        raise PipelineError(
            code="missing_dependency",
            message=f"Required command is not available: {command}",
            step=step,
            details={"command": command},
        )
    return path


def run_command(args: List[str], step: str, timeout: int = 600) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(
            code="command_timeout",
            message=f"Command timed out while running {args[0]}",
            step=step,
            details={"args": args, "timeout": timeout},
        ) from exc
    if result.returncode != 0:
        raise PipelineError(
            code="command_failed",
            message=f"Command failed while running {args[0]}",
            step=step,
            details={
                "args": args,
                "returncode": result.returncode,
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-4000:],
            },
        )
    return result


def ffprobe_duration(video_path: Path) -> float:
    require_command("ffprobe", "media_probe")
    result = run_command(
        [
            "ffprobe",
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

