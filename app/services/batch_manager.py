import re
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional

from app.schemas.models import BatchItemStatus, BatchStatus, ProjectCreate, ProjectStatus
from app.services.batch_store import BATCH_TERMINAL_STATUSES, BatchStore, batch_store
from app.services.pipeline import run_project_analysis_pipeline, run_project_platform_produce_pipeline
from app.services.platforms import get_platform
from app.services.runtime_store import ProjectStore, read_json, store
from app.services.task_manager import TaskManager, task_manager

FILENAME_FORBIDDEN_RE = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")


class BatchManager:
    def __init__(
        self,
        batches: BatchStore,
        projects: ProjectStore,
        tasks: TaskManager,
        *,
        analyze_target: Callable[..., None] = run_project_analysis_pipeline,
        produce_target: Callable[..., None] = run_project_platform_produce_pipeline,
        poll_interval: float = 0.5,
    ) -> None:
        self.batches = batches
        self.projects = projects
        self.tasks = tasks
        self.analyze_target = analyze_target
        self.produce_target = produce_target
        self.poll_interval = max(0.01, poll_interval)
        self._condition = threading.Condition()
        self._queue: Deque[str] = deque()
        self._queued_ids: set[str] = set()
        self._running_batch_id: Optional[str] = None
        self._shutdown = False
        self._thread = threading.Thread(target=self._worker, name="ytxhs-batch-queue", daemon=True)
        self._thread.start()

    def submit(self, batch_id: str) -> Dict[str, Any]:
        with self._condition:
            if batch_id == self._running_batch_id or batch_id in self._queued_ids:
                return {"queued": True, "queue_position": self._position(batch_id)}
            self._queue.append(batch_id)
            self._queued_ids.add(batch_id)
            position = len(self._queue) if self._running_batch_id else max(0, len(self._queue) - 1)
            self._condition.notify_all()
            return {"queued": position > 0, "queue_position": position}

    def recover(self) -> None:
        for record in self.batches.resume_candidates():
            self.submit(record.batch_id)

    def cancel(self, batch_id: str) -> Dict[str, Any]:
        record = self.batches.get(batch_id)
        current = next((item for item in record.items if item.index == record.current_index), None)
        stopped = self.batches.stop(batch_id)
        project_cancelled = False
        if current and current.project_id:
            try:
                if self.projects.can_cancel(current.project_id):
                    self.projects.cancel(current.project_id)
                    self.tasks.cancel(current.project_id)
                    project_cancelled = True
            except (FileNotFoundError, ValueError):
                pass
        with self._condition:
            if batch_id in self._queued_ids:
                self._queue = deque(item for item in self._queue if item != batch_id)
                self._queued_ids.discard(batch_id)
            self._condition.notify_all()
        return {"batch": stopped, "project_cancelled": project_cancelled}

    def snapshot(self, batch_id: str) -> Dict[str, Any]:
        with self._condition:
            if batch_id == self._running_batch_id:
                return {"state": "running", "queue_position": 0}
            return {
                "state": "queued" if batch_id in self._queued_ids else "idle",
                "queue_position": self._position(batch_id),
            }

    def shutdown(self) -> None:
        with self._condition:
            self._shutdown = True
            self._condition.notify_all()

    def _position(self, batch_id: str) -> int:
        try:
            return list(self._queue).index(batch_id) + (1 if self._running_batch_id else 0)
        except ValueError:
            return 0

    def _worker(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._shutdown:
                    self._condition.wait()
                if self._shutdown:
                    return
                batch_id = self._queue.popleft()
                self._queued_ids.discard(batch_id)
                self._running_batch_id = batch_id
            try:
                self._process_batch(batch_id)
            except Exception as exc:
                self.batches.fail(
                    batch_id,
                    {
                        "code": "batch_worker_failed",
                        "message": "The batch worker raised an unexpected error.",
                        "step": "batch",
                        "details": {"type": type(exc).__name__, "error": str(exc)},
                    },
                )
            finally:
                with self._condition:
                    self._running_batch_id = None
                    self._condition.notify_all()

    def _process_batch(self, batch_id: str) -> None:
        initial = self.batches.get(batch_id)
        if initial.status in BATCH_TERMINAL_STATUSES:
            return
        self.batches.mark_running(batch_id, resumed=initial.status == BatchStatus.running)
        for index in range(1, initial.total_count + 1):
            if self._shutdown or self.batches.get(batch_id).status == BatchStatus.stopped:
                return
            item = next(item for item in self.batches.get(batch_id).items if item.index == index)
            if item.status == BatchItemStatus.completed:
                continue
            if item.status in {BatchItemStatus.failed, BatchItemStatus.stopped, BatchItemStatus.skipped}:
                if not initial.continue_on_error:
                    self.batches.skip_remaining(batch_id, index)
                    break
                continue
            succeeded = self._process_item(batch_id, index)
            if not succeeded and not initial.continue_on_error:
                self.batches.skip_remaining(batch_id, index)
                break
        if self._shutdown or self.batches.get(batch_id).status == BatchStatus.stopped:
            return
        self.batches.finish(batch_id)

    def _process_item(self, batch_id: str, index: int) -> bool:
        batch = self.batches.get(batch_id)
        item = next(item for item in batch.items if item.index == index)
        try:
            project = self._resolve_project(batch_id, index, item)
            if project.status == get_platform(batch.target_platform).completed_status:
                return self._archive_completed_project(batch_id, index, project.project_id)
            if project.status in {ProjectStatus.failed, ProjectStatus.stopped}:
                self._fail_item_from_project(batch_id, index, project)
                return False

            if project.status != ProjectStatus.analysis_completed:
                self.batches.set_item_status(
                    batch_id,
                    index,
                    BatchItemStatus.analyzing,
                    project_id=project.project_id,
                )
                self.tasks.submit(
                    "analyze",
                    project.project_id,
                    self.analyze_target,
                    platform=batch.target_platform,
                    on_queued=lambda position: self.projects.set_status(
                        project.project_id,
                        ProjectStatus.queued,
                        "Batch analysis task is waiting for an execution slot.",
                        {"scope": "analyze", "queue_position": position, "batch_id": batch_id},
                    ),
                )
                project = self._wait_for_project(batch_id, project.project_id, {ProjectStatus.analysis_completed, ProjectStatus.failed, ProjectStatus.stopped})
                if project is None:
                    return False
                if project.status != ProjectStatus.analysis_completed:
                    self._fail_item_from_project(batch_id, index, project)
                    return False

            started, project, missing = self.projects.try_start_platform_produce(project.project_id, batch.target_platform)
            if not started:
                raise RuntimeError(f"Batch project is not ready for Produce; missing={missing}, status={project.status.value}")
            self.batches.set_item_status(
                batch_id,
                index,
                BatchItemStatus.producing,
                project_id=project.project_id,
            )
            self.tasks.submit(
                "produce",
                project.project_id,
                self.produce_target,
                batch.target_platform,
                platform=batch.target_platform,
                on_queued=lambda position: self.projects.set_status(
                    project.project_id,
                    ProjectStatus.queued,
                    "Batch Produce task is waiting for an execution slot.",
                    {
                        "scope": "produce",
                        "platform": batch.target_platform,
                        "queue_position": position,
                        "batch_id": batch_id,
                    },
                ),
            )
            project = self._wait_for_project(
                batch_id,
                project.project_id,
                {get_platform(batch.target_platform).completed_status, ProjectStatus.failed, ProjectStatus.stopped},
            )
            if project is None:
                return False
            if project.status != get_platform(batch.target_platform).completed_status:
                self._fail_item_from_project(batch_id, index, project)
                return False
            return self._archive_completed_project(batch_id, index, project.project_id)
        except Exception as exc:
            self.batches.set_item_status(
                batch_id,
                index,
                BatchItemStatus.failed,
                project_id=item.project_id,
                error={
                    "code": "batch_item_failed",
                    "message": "The batch item could not complete.",
                    "step": "batch",
                    "details": {"type": type(exc).__name__, "error": str(exc)},
                },
            )
            return False

    def _resolve_project(self, batch_id: str, index: int, item: Any):
        if item.project_id:
            return self.projects.get(item.project_id)
        batch = self.batches.get(batch_id)
        project = self.projects.create(
            ProjectCreate(
                url=item.url,
                target_platform=batch.target_platform,
                language=batch.language,
                style=batch.style,
                use_whisper=batch.use_whisper,
                use_ocr=batch.use_ocr,
                text_only=batch.text_only,
                max_frames=batch.max_frames,
            )
        )
        self.batches.set_item_status(
            batch_id,
            index,
            BatchItemStatus.analyzing,
            project_id=project.project_id,
        )
        return project

    def _wait_for_project(self, batch_id: str, project_id: str, terminal_statuses: set[ProjectStatus]):
        while not self._shutdown:
            if self.batches.get(batch_id).status == BatchStatus.stopped:
                return None
            project = self.projects.get(project_id)
            if project.status in terminal_statuses:
                return project
            time.sleep(self.poll_interval)
        return None

    def _archive_completed_project(self, batch_id: str, index: int, project_id: str) -> bool:
        batch = self.batches.get(batch_id)
        adapter = get_platform(batch.target_platform)
        paths = self.projects.paths(project_id)
        source = paths.file_for_kind(adapter.post_docx_kind)
        if not source.exists():
            raise FileNotFoundError(f"Completed project is missing Word output: {source}")
        metadata_path = paths.file_for_kind("metadata")
        metadata = read_json(metadata_path) if metadata_path.exists() else {}
        title = str(metadata.get("title") or f"video-{index}").strip()
        filename = self._document_filename(index, title)
        self.batches.copy_document(batch_id, source, filename)
        self.batches.set_item_status(
            batch_id,
            index,
            BatchItemStatus.completed,
            project_id=project_id,
            title=title,
            document_filename=filename,
        )
        return True

    def _fail_item_from_project(self, batch_id: str, index: int, project: Any) -> None:
        status = BatchItemStatus.stopped if project.status == ProjectStatus.stopped else BatchItemStatus.failed
        self.batches.set_item_status(
            batch_id,
            index,
            status,
            project_id=project.project_id,
            error=project.error
            or {
                "code": "project_failed",
                "message": "The project did not produce a Word document.",
                "step": project.status.value,
            },
        )

    @staticmethod
    def _document_filename(index: int, title: str) -> str:
        cleaned = FILENAME_FORBIDDEN_RE.sub("_", title)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._") or f"video-{index}"
        cleaned = cleaned[:80].rstrip(" ._") or f"video-{index}"
        return f"{index:03d}-{cleaned}.docx"


batch_manager = BatchManager(batch_store, store, task_manager)
