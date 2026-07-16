import fcntl
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.schemas.models import (
    FILE_KIND_TO_PATH,
    SUPPORTED_TARGET_PLATFORMS,
    ProgressLog,
    ProjectCreate,
    ProjectRecord,
    ProjectStatus,
)
from app.services.config import settings

PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
CANCEL_REQUEST_FILENAME = ".cancel-requested.json"
RERUN_READY_STATUSES = {ProjectStatus.failed, ProjectStatus.stopped, ProjectStatus.completed}
PRODUCE_READY_STATUSES = {
    ProjectStatus.analysis_completed,
    ProjectStatus.xhs_completed,
    ProjectStatus.toutiao_completed,
    ProjectStatus.douyin_completed,
    ProjectStatus.bilibili_completed,
    ProjectStatus.stopped,
    ProjectStatus.failed,
    ProjectStatus.completed,
}
IMAGE_GENERATION_READY_STATUSES = {
    ProjectStatus.xhs_completed,
    ProjectStatus.toutiao_completed,
    ProjectStatus.douyin_completed,
    ProjectStatus.bilibili_completed,
    ProjectStatus.failed,
    ProjectStatus.completed,
}
RUNNING_STATUSES = {
    ProjectStatus.queued,
    ProjectStatus.created,
    ProjectStatus.ingesting,
    ProjectStatus.transcribing,
    ProjectStatus.extracting_frames,
    ProjectStatus.analyzing_visuals,
    ProjectStatus.planning_content,
    ProjectStatus.writing_xhs,
    ProjectStatus.producing_article,
    ProjectStatus.validating_content,
    ProjectStatus.rendering_cards,
}
DOWNSTREAM_RERUN_REQUIRED_KINDS = ("metadata", "transcript", "keyframes", "visual_analysis")
VISUAL_RERUN_REQUIRED_KINDS = ("metadata", "transcript", "keyframes")
PRODUCE_REQUIRED_KINDS = ("metadata", "transcript", "keyframes", "visual_analysis", "content_assets")
IMAGE_GENERATION_REQUIRED_KINDS = (
    "metadata",
    "transcript",
    "keyframes",
    "visual_analysis",
    "content_assets",
    "xhs_post_json",
    "image_prompts",
)
TOUTIAO_IMAGE_GENERATION_REQUIRED_KINDS = (
    "metadata",
    "transcript",
    "keyframes",
    "visual_analysis",
    "content_assets",
    "toutiao_post_json",
    "toutiao_image_prompts",
)

LEGACY_STATUS_PLATFORMS = {
    "xhs_completed": "xhs",
    "toutiao_completed": "toutiao",
    "douyin_completed": "douyin",
    "bilibili_completed": "bilibili",
}
LEGACY_PLATFORM_OUTPUT_KINDS = {
    "xhs": "xhs_post_json",
    "toutiao": "toutiao_post_json",
    "douyin": "douyin_post_json",
    "bilibili": "bilibili_post_json",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _platform_from_details(value: Any) -> Optional[str]:
    if not isinstance(value, dict):
        return None
    platform = value.get("platform")
    if platform in SUPPORTED_TARGET_PLATFORMS:
        return str(platform)
    return _platform_from_details(value.get("details"))


def _legacy_target_platform(payload: Dict[str, Any]) -> str:
    configured = payload.get("target_platform")
    if configured in SUPPORTED_TARGET_PLATFORMS:
        return str(configured)
    status = getattr(payload.get("status"), "value", payload.get("status"))
    if status in LEGACY_STATUS_PLATFORMS:
        return LEGACY_STATUS_PLATFORMS[str(status)]
    error_platform = _platform_from_details(payload.get("error"))
    if error_platform:
        return error_platform
    for log in reversed(payload.get("logs") or []):
        if not isinstance(log, dict):
            continue
        platform = _platform_from_details(log.get("details"))
        if platform:
            return platform
    outputs = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}
    generated = [platform for platform, kind in LEGACY_PLATFORM_OUTPUT_KINDS.items() if outputs.get(kind)]
    return generated[0] if len(generated) == 1 else "xhs"


def _record_from_payload(payload: Dict[str, Any]) -> ProjectRecord:
    normalized = dict(payload)
    normalized["target_platform"] = _legacy_target_platform(normalized)
    return ProjectRecord(**normalized)


def _normalize_target_platform(platform: str) -> str:
    normalized = str(platform or "").strip().lower()
    if normalized not in SUPPORTED_TARGET_PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform}")
    return normalized


class ProjectPaths:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.source_dir = project_dir / "source"
        self.transcript_dir = project_dir / "transcript"
        self.frames_dir = project_dir / "frames"
        self.analysis_dir = project_dir / "analysis"
        self.cards_dir = project_dir / "cards"
        self.toutiao_cards_dir = project_dir / "toutiao-cards"

    def ensure(self) -> None:
        for path in [
            self.project_dir,
            self.source_dir,
            self.transcript_dir,
            self.frames_dir,
            self.analysis_dir,
            self.cards_dir,
            self.toutiao_cards_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)

    def status_file(self) -> Path:
        return self.project_dir / "project.json"

    def run_metadata_file(self) -> Path:
        return self.analysis_dir / "run-metadata.json"

    def cancel_file(self) -> Path:
        return self.project_dir / CANCEL_REQUEST_FILENAME

    def file_for_kind(self, kind: str) -> Path:
        if kind not in FILE_KIND_TO_PATH:
            raise KeyError(kind)
        return self.project_dir / FILE_KIND_TO_PATH[kind]


class ProjectStore:
    def __init__(self, runtime_dir: Optional[Path] = None) -> None:
        self.runtime_dir = runtime_dir or settings.runtime_dir
        self.projects_dir = self.runtime_dir / "projects"
        self.locks_dir = self.runtime_dir / ".locks"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        self._locks_guard = threading.Lock()
        self._project_locks: Dict[str, threading.RLock] = {}

    @contextmanager
    def _locked_project(self, project_id: str):
        with self._locks_guard:
            thread_lock = self._project_locks.setdefault(project_id, threading.RLock())
        with thread_lock:
            lock_path = self.locks_dir / f"{project_id}.lock"
            with lock_path.open("a+", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def paths(self, project_id: str) -> ProjectPaths:
        if not PROJECT_ID_RE.fullmatch(project_id):
            raise ValueError("Invalid project_id")
        project_dir = (self.projects_dir / project_id).resolve()
        projects_dir = self.projects_dir.resolve()
        try:
            project_dir.relative_to(projects_dir)
        except ValueError:
            raise ValueError("Invalid project_id") from None
        return ProjectPaths(project_dir)

    def create(self, request: ProjectCreate) -> ProjectRecord:
        project_id = uuid.uuid4().hex[:12]
        paths = self.paths(project_id)
        paths.ensure()
        now = utc_now()
        record = ProjectRecord(
            project_id=project_id,
            url=str(request.url),
            target_platform=request.target_platform,
            language=request.language,
            style=request.style,
            use_whisper=request.use_whisper,
            use_ocr=request.use_ocr,
            text_only=request.text_only,
            max_frames=request.max_frames,
            status=ProjectStatus.created,
            created_at=now,
            updated_at=now,
            logs=[],
            outputs={},
        )
        self._write_record(paths, record)
        self.log(
            project_id,
            ProjectStatus.created,
            "Project created.",
            details={"platform": request.target_platform},
        )
        return self.get(project_id)

    def list(self) -> List[ProjectRecord]:
        records = []
        for path in sorted(self.projects_dir.glob("*/project.json"), reverse=True):
            try:
                records.append(_record_from_payload(read_json(path)))
            except Exception:
                continue
        return records

    def get(self, project_id: str) -> ProjectRecord:
        path = self.paths(project_id).status_file()
        if not path.exists():
            raise FileNotFoundError(project_id)
        return _record_from_payload(read_json(path))

    def delete(self, project_id: str) -> ProjectRecord:
        paths = self.paths(project_id)
        with self._locked_project(project_id):
            if not paths.status_file().exists():
                raise FileNotFoundError(project_id)
            record = _record_from_payload(read_json(paths.status_file()))
            if record.status in RUNNING_STATUSES:
                raise RuntimeError("Cannot delete a project while it is running.")
            shutil.rmtree(paths.project_dir)
            return record

    def set_status(
        self,
        project_id: str,
        status: ProjectStatus,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> ProjectRecord:
        return self.log(project_id, status, message, details=details, set_status=True)

    def set_target_platform(self, project_id: str, platform: str) -> ProjectRecord:
        normalized = _normalize_target_platform(platform)
        paths = self.paths(project_id)
        with self._locked_project(project_id):
            record = _record_from_payload(read_json(paths.status_file()))
            record.target_platform = normalized
            record.updated_at = utc_now()
            self._write_record(paths, record)
            self._write_run_metadata(paths, record)
            return record

    def downstream_rerun_missing_inputs(self, project_id: str) -> List[str]:
        paths = self.paths(project_id)
        record = self.get(project_id)
        return self._missing_downstream_inputs(paths, record)

    def produce_missing_inputs(self, project_id: str) -> List[str]:
        paths = self.paths(project_id)
        record = self.get(project_id)
        return self._missing_produce_inputs(paths, record)

    def visual_rerun_missing_inputs(self, project_id: str) -> List[str]:
        paths = self.paths(project_id)
        record = self.get(project_id)
        return self._missing_visual_inputs(paths, record)

    def image_generation_missing_inputs(self, project_id: str) -> List[str]:
        paths = self.paths(project_id)
        record = self.get(project_id)
        return self._missing_image_generation_inputs(paths, record)

    def platform_image_generation_missing_inputs(self, project_id: str, platform: str = "xhs") -> List[str]:
        paths = self.paths(project_id)
        record = self.get(project_id)
        return self._missing_platform_image_generation_inputs(paths, record, platform)

    def can_start_downstream_rerun(self, project_id: str) -> bool:
        record = self.get(project_id)
        return record.status in RERUN_READY_STATUSES and not self._missing_downstream_inputs(self.paths(project_id), record)

    def can_start_produce(self, project_id: str) -> bool:
        record = self.get(project_id)
        return record.status in PRODUCE_READY_STATUSES and not self._missing_produce_inputs(self.paths(project_id), record)

    def can_start_image_generation(self, project_id: str) -> bool:
        record = self.get(project_id)
        if record.text_only:
            return False
        return record.status in IMAGE_GENERATION_READY_STATUSES and not self._missing_image_generation_inputs(self.paths(project_id), record)

    def can_start_platform_image_generation(self, project_id: str, platform: str = "xhs") -> bool:
        record = self.get(project_id)
        if record.text_only:
            return False
        return record.status in IMAGE_GENERATION_READY_STATUSES and not self._missing_platform_image_generation_inputs(self.paths(project_id), record, platform)

    def can_start_visual_rerun(self, project_id: str) -> bool:
        record = self.get(project_id)
        return record.status in RERUN_READY_STATUSES and not self._missing_visual_inputs(self.paths(project_id), record)

    def can_cancel(self, project_id: str) -> bool:
        return self.get(project_id).status in RUNNING_STATUSES

    def cancel_requested(self, project_id: str) -> bool:
        return self.paths(project_id).cancel_file().exists()

    def _clear_cancel_requested(self, paths: ProjectPaths) -> None:
        paths.cancel_file().unlink(missing_ok=True)

    def cancel(self, project_id: str) -> ProjectRecord:
        paths = self.paths(project_id)
        with self._locked_project(project_id):
            record = _record_from_payload(read_json(paths.status_file()))
            if record.status not in RUNNING_STATUSES:
                return record
            previous_status = record.status.value
            now = utc_now()
            error = {
                "code": "user_stopped",
                "message": "Project was force-stopped by the user. You can inspect existing artifacts or start a new task.",
                "step": previous_status,
                "details": {
                    "previous_status": previous_status,
                    "stopped_at": now,
                    "platform": record.target_platform,
                },
            }
            from app.services.report_writer import write_partial_asset_package

            write_json(paths.cancel_file(), error)
            write_partial_asset_package(paths, error, record.warnings)
            record.status = ProjectStatus.stopped
            record.error = error
            record.updated_at = now
            record.logs.append(
                ProgressLog(
                    time=now,
                    status=ProjectStatus.stopped,
                    message=error["message"],
                    details=error,
                )
            )
            for kind, relative in FILE_KIND_TO_PATH.items():
                if (paths.project_dir / relative).exists():
                    record.outputs[kind] = relative
            self._write_record(paths, record)
            self._write_run_metadata(paths, record)
            return record

    def try_start_downstream_rerun(self, project_id: str) -> Tuple[bool, ProjectRecord, List[str]]:
        paths = self.paths(project_id)
        with self._locked_project(project_id):
            record = _record_from_payload(read_json(paths.status_file()))
            if record.status not in RERUN_READY_STATUSES:
                return False, record, []
            missing_inputs = self._missing_downstream_inputs(paths, record)
            if missing_inputs:
                return False, record, missing_inputs
            self._clear_cancel_requested(paths)
            record.status = ProjectStatus.planning_content
            record.error = None
            record.updated_at = utc_now()
            record.logs.append(
                ProgressLog(
                    time=record.updated_at,
                    status=ProjectStatus.planning_content,
                    message="Downstream rerun queued.",
                    details={"scope": "downstream", "platform": record.target_platform},
                )
            )
            self._write_record(paths, record)
            self._write_run_metadata(paths, record)
            return True, record, []

    def try_start_produce(self, project_id: str) -> Tuple[bool, ProjectRecord, List[str]]:
        return self.try_start_platform_produce(project_id, platform="xhs")

    def try_start_platform_produce(self, project_id: str, platform: str = "xhs") -> Tuple[bool, ProjectRecord, List[str]]:
        paths = self.paths(project_id)
        with self._locked_project(project_id):
            record = _record_from_payload(read_json(paths.status_file()))
            if record.status not in PRODUCE_READY_STATUSES:
                return False, record, []
            missing_inputs = self._missing_produce_inputs(paths, record)
            if missing_inputs:
                return False, record, missing_inputs
            platform = _normalize_target_platform(platform)
            self._clear_cancel_requested(paths)
            record.target_platform = platform
            record.status = ProjectStatus.queued
            record.error = None
            record.updated_at = utc_now()
            record.logs.append(
                ProgressLog(
                    time=record.updated_at,
                    status=ProjectStatus.queued,
                    message="Produce job queued.",
                    details={"scope": "produce", "platform": platform},
                )
            )
            self._write_record(paths, record)
            self._write_run_metadata(paths, record)
            return True, record, []

    def try_start_image_generation(self, project_id: str) -> Tuple[bool, ProjectRecord, List[str]]:
        paths = self.paths(project_id)
        with self._locked_project(project_id):
            record = _record_from_payload(read_json(paths.status_file()))
            if record.text_only:
                return False, record, []
            if record.status not in IMAGE_GENERATION_READY_STATUSES:
                return False, record, []
            missing_inputs = self._missing_image_generation_inputs(paths, record)
            if missing_inputs:
                return False, record, missing_inputs
            self._clear_cancel_requested(paths)
            record.target_platform = "xhs"
            record.status = ProjectStatus.rendering_cards
            record.error = None
            record.updated_at = utc_now()
            record.logs.append(
                ProgressLog(
                    time=record.updated_at,
                    status=ProjectStatus.rendering_cards,
                    message="Image generation job queued.",
                    details={"scope": "image_generation", "platform": "xhs"},
                )
            )
            self._write_record(paths, record)
            self._write_run_metadata(paths, record)
            return True, record, []

    def try_start_platform_image_generation(self, project_id: str, platform: str = "xhs") -> Tuple[bool, ProjectRecord, List[str]]:
        paths = self.paths(project_id)
        with self._locked_project(project_id):
            record = _record_from_payload(read_json(paths.status_file()))
            if record.text_only:
                return False, record, []
            if record.status not in IMAGE_GENERATION_READY_STATUSES:
                return False, record, []
            missing_inputs = self._missing_platform_image_generation_inputs(paths, record, platform)
            if missing_inputs:
                return False, record, missing_inputs
            platform = _normalize_target_platform(platform)
            self._clear_cancel_requested(paths)
            record.target_platform = platform
            record.status = ProjectStatus.rendering_cards
            record.error = None
            record.updated_at = utc_now()
            record.logs.append(
                ProgressLog(
                    time=record.updated_at,
                    status=ProjectStatus.rendering_cards,
                    message="Image generation job queued.",
                    details={"scope": "image_generation", "platform": platform},
                )
            )
            self._write_record(paths, record)
            self._write_run_metadata(paths, record)
            return True, record, []

    def try_start_visual_rerun(self, project_id: str) -> Tuple[bool, ProjectRecord, List[str]]:
        paths = self.paths(project_id)
        with self._locked_project(project_id):
            record = _record_from_payload(read_json(paths.status_file()))
            if record.status not in RERUN_READY_STATUSES:
                return False, record, []
            missing_inputs = self._missing_visual_inputs(paths, record)
            if missing_inputs:
                return False, record, missing_inputs
            self._clear_cancel_requested(paths)
            record.status = ProjectStatus.analyzing_visuals
            record.error = None
            record.updated_at = utc_now()
            record.logs.append(
                ProgressLog(
                    time=record.updated_at,
                    status=ProjectStatus.analyzing_visuals,
                    message="Visual analysis rerun queued.",
                    details={"scope": "visuals_and_downstream", "platform": record.target_platform},
                )
            )
            self._write_record(paths, record)
            self._write_run_metadata(paths, record)
            return True, record, []

    def log(
        self,
        project_id: str,
        status: ProjectStatus,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        set_status: bool = False,
    ) -> ProjectRecord:
        paths = self.paths(project_id)
        with self._locked_project(project_id):
            record = _record_from_payload(read_json(paths.status_file()))
            if paths.cancel_file().exists() and status not in {ProjectStatus.failed, ProjectStatus.stopped}:
                return record
            if set_status:
                record.status = status
                if status not in {ProjectStatus.failed, ProjectStatus.stopped}:
                    record.error = None
            record.updated_at = utc_now()
            record.logs.append(
                ProgressLog(
                    time=record.updated_at,
                    status=status,
                    message=message,
                    details=details,
                )
            )
            self._write_record(paths, record)
            self._write_run_metadata(paths, record)
            return record

    def add_warning(self, project_id: str, warning: str) -> None:
        paths = self.paths(project_id)
        with self._locked_project(project_id):
            record = _record_from_payload(read_json(paths.status_file()))
            if paths.cancel_file().exists():
                return
            if warning not in record.warnings:
                record.warnings.append(warning)
            record.updated_at = utc_now()
            self._write_record(paths, record)
            self._write_run_metadata(paths, record)

    def clear_outputs(self, project_id: str, kinds: Iterable[str]) -> None:
        paths = self.paths(project_id)
        with self._locked_project(project_id):
            record = _record_from_payload(read_json(paths.status_file()))
            if paths.cancel_file().exists():
                return
            for kind in kinds:
                record.outputs.pop(kind, None)
            record.updated_at = utc_now()
            self._write_record(paths, record)
            self._write_run_metadata(paths, record)

    def clear_warnings(self, project_id: str) -> None:
        paths = self.paths(project_id)
        with self._locked_project(project_id):
            record = _record_from_payload(read_json(paths.status_file()))
            if paths.cancel_file().exists():
                return
            record.warnings = []
            record.updated_at = utc_now()
            self._write_record(paths, record)
            self._write_run_metadata(paths, record)

    def mark_stale_running_failed(self, older_than_seconds: int, dry_run: bool = False) -> List[Dict[str, Any]]:
        recovered = []
        cutoff = datetime.now(timezone.utc).timestamp() - max(0, older_than_seconds)
        for status_file in sorted(self.projects_dir.glob("*/project.json")):
            project_id = status_file.parent.name
            with self._locked_project(project_id):
                try:
                    paths = ProjectPaths(status_file.parent)
                    record = _record_from_payload(read_json(status_file))
                    updated_at = parse_time(record.updated_at).timestamp()
                except Exception:
                    continue
                if record.status not in RUNNING_STATUSES or updated_at > cutoff:
                    continue
                error = {
                    "code": "stale_running_project",
                    "message": (
                        "Project was left in a running state past the recovery threshold. "
                        "It was marked failed so existing artifacts can be inspected or rerun."
                    ),
                    "step": record.status.value,
                    "details": {
                        "previous_status": record.status.value,
                        "updated_at": record.updated_at,
                        "older_than_seconds": older_than_seconds,
                        "platform": record.target_platform,
                    },
                }
                recovered.append(
                    {
                        "project_id": record.project_id,
                        "previous_status": record.status.value,
                        "updated_at": record.updated_at,
                        "error": error,
                        "dry_run": dry_run,
                    }
                )
                if dry_run:
                    continue
                record.status = ProjectStatus.failed
                record.error = error
                record.updated_at = utc_now()
                record.logs.append(
                    ProgressLog(
                        time=record.updated_at,
                        status=ProjectStatus.failed,
                        message=error["message"],
                        details=error,
                    )
                )
                self._write_record(paths, record)
                self._write_run_metadata(paths, record)
        return recovered

    def recover_interrupted_projects(self) -> List[Dict[str, Any]]:
        """Recover every running record left by a previous service process."""
        from app.services.report_writer import write_partial_asset_package

        recovered = self.mark_stale_running_failed(older_than_seconds=0, dry_run=False)
        for item in recovered:
            project_id = item["project_id"]
            paths = self.paths(project_id)
            record = self.get(project_id)
            write_partial_asset_package(paths, item["error"], record.warnings)
            for kind in FILE_KIND_TO_PATH:
                path = paths.file_for_kind(kind)
                if path.exists():
                    self.add_output(project_id, kind, path)
        return recovered

    def add_output(self, project_id: str, kind: str, path: Path) -> None:
        if kind not in FILE_KIND_TO_PATH:
            raise KeyError(kind)
        paths = self.paths(project_id)
        expected_path = (paths.project_dir / FILE_KIND_TO_PATH[kind]).resolve()
        output_path = path.resolve()
        if output_path != expected_path:
            raise ValueError(f"Output path for {kind} must be {FILE_KIND_TO_PATH[kind]}")
        if not output_path.exists():
            raise FileNotFoundError(path)
        with self._locked_project(project_id):
            record = _record_from_payload(read_json(paths.status_file()))
            if paths.cancel_file().exists():
                return
            record.outputs[kind] = FILE_KIND_TO_PATH[kind]
            record.updated_at = utc_now()
            self._write_record(paths, record)
            self._write_run_metadata(paths, record)

    def fail(self, project_id: str, error: Dict[str, Any]) -> ProjectRecord:
        paths = self.paths(project_id)
        with self._locked_project(project_id):
            record = _record_from_payload(read_json(paths.status_file()))
            error = dict(error)
            details = dict(error.get("details") or {})
            details.setdefault("platform", record.target_platform)
            error["details"] = details
            if paths.cancel_file().exists() and record.error and record.error.get("code") == "user_stopped":
                return record
            record.status = ProjectStatus.failed
            record.error = error
            record.updated_at = utc_now()
            record.logs.append(
                ProgressLog(
                    time=record.updated_at,
                    status=ProjectStatus.failed,
                    message=error.get("message", "Pipeline failed."),
                    details=error,
                )
            )
            self._write_record(paths, record)
            self._write_run_metadata(paths, record)
            return record

    def _write_record(self, paths: ProjectPaths, record: ProjectRecord) -> None:
        paths.ensure()
        write_json(paths.status_file(), record.model_dump(mode="json"))

    def _write_run_metadata(self, paths: ProjectPaths, record: ProjectRecord) -> None:
        paths.analysis_dir.mkdir(parents=True, exist_ok=True)
        if record.outputs.get("run_metadata") != FILE_KIND_TO_PATH["run_metadata"]:
            record.outputs["run_metadata"] = FILE_KIND_TO_PATH["run_metadata"]
            self._write_record(paths, record)
        payload = record.model_dump(mode="json")
        source_metadata_path = paths.source_dir / "metadata.json"
        if source_metadata_path.exists():
            try:
                source_metadata = read_json(source_metadata_path)
            except Exception:
                source_metadata = {}
            video_summary = {
                "video_id": source_metadata.get("video_id"),
                "title": source_metadata.get("title"),
                "author": source_metadata.get("author"),
                "duration": source_metadata.get("duration"),
                "source_url": source_metadata.get("url") or record.url,
                "thumbnail": source_metadata.get("thumbnail"),
                "thumbnail_file": source_metadata.get("thumbnail_file"),
                "video_file": source_metadata.get("video_file"),
            }
            payload.update(video_summary)
            payload["source_metadata"] = video_summary
        write_json(paths.run_metadata_file(), payload)

    def _missing_downstream_inputs(self, paths: ProjectPaths, record: ProjectRecord) -> List[str]:
        return self._missing_registered_inputs(paths, record, DOWNSTREAM_RERUN_REQUIRED_KINDS)

    def _missing_visual_inputs(self, paths: ProjectPaths, record: ProjectRecord) -> List[str]:
        return self._missing_registered_inputs(paths, record, VISUAL_RERUN_REQUIRED_KINDS)

    def _missing_produce_inputs(self, paths: ProjectPaths, record: ProjectRecord) -> List[str]:
        return self._missing_registered_inputs(paths, record, PRODUCE_REQUIRED_KINDS)

    def _missing_image_generation_inputs(self, paths: ProjectPaths, record: ProjectRecord) -> List[str]:
        return self._missing_registered_inputs(paths, record, IMAGE_GENERATION_REQUIRED_KINDS)

    def _missing_platform_image_generation_inputs(self, paths: ProjectPaths, record: ProjectRecord, platform: str) -> List[str]:
        if platform == "toutiao":
            return self._missing_registered_inputs(paths, record, TOUTIAO_IMAGE_GENERATION_REQUIRED_KINDS)
        return self._missing_registered_inputs(paths, record, IMAGE_GENERATION_REQUIRED_KINDS)

    def _missing_registered_inputs(self, paths: ProjectPaths, record: ProjectRecord, kinds: Iterable[str]) -> List[str]:
        missing = []
        for kind in kinds:
            expected_relative = FILE_KIND_TO_PATH[kind]
            expected_path = paths.project_dir / expected_relative
            if record.outputs.get(kind) != expected_relative or not expected_path.exists():
                missing.append(kind)
        return missing


store = ProjectStore()
