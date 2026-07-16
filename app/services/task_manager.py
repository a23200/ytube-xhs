import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, Optional

from app.services.config import settings

CURRENT_PROJECT_ID: ContextVar[Optional[str]] = ContextVar("ytxhs_project_id", default=None)


@dataclass
class Job:
    project_id: str
    scope: str
    platform: Optional[str]
    target: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: Dict[str, Any]
    started: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    retire_worker: bool = False


class ChildProcessRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: Dict[str, set[subprocess.Popen[Any]]] = {}

    def register(self, project_id: Optional[str], process: subprocess.Popen[Any]) -> None:
        if not project_id:
            return
        with self._lock:
            self._processes.setdefault(project_id, set()).add(process)

    def unregister(self, project_id: Optional[str], process: subprocess.Popen[Any]) -> None:
        if not project_id:
            return
        with self._lock:
            processes = self._processes.get(project_id)
            if not processes:
                return
            processes.discard(process)
            if not processes:
                self._processes.pop(project_id, None)

    def terminate(self, project_id: str, *, grace_seconds: float = 1.5) -> int:
        with self._lock:
            processes = list(self._processes.get(project_id, set()))
        live_processes = [process for process in processes if process.poll() is None]
        for process in live_processes:
            if process.poll() is not None:
                continue
            try:
                if os.name == "posix":
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                else:
                    process.terminate()
            except (OSError, ProcessLookupError):
                continue

        deadline = time.monotonic() + max(0.0, grace_seconds)
        while any(process.poll() is None for process in live_processes) and time.monotonic() < deadline:
            time.sleep(0.05)

        for process in live_processes:
            if process.poll() is not None:
                continue
            try:
                if os.name == "posix":
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    process.kill()
            except (OSError, ProcessLookupError):
                continue
        return len(live_processes)

    def count(self, project_id: str) -> int:
        with self._lock:
            return sum(1 for process in self._processes.get(project_id, set()) if process.poll() is None)


class TaskManager:
    def __init__(self, analyze_workers: int = 1, produce_workers: int = 3) -> None:
        self._limits = {"analyze": max(1, analyze_workers), "produce": max(1, produce_workers)}
        self._queues: Dict[str, Deque[Job]] = {scope: deque() for scope in self._limits}
        self._running: Dict[str, Job] = {}
        self._condition = threading.Condition()
        self._shutdown = False
        self._threads: list[threading.Thread] = []
        for scope, limit in self._limits.items():
            for index in range(limit):
                self._start_worker(scope, index)

    def _start_worker(self, scope: str, index: int) -> None:
        thread = threading.Thread(target=self._worker, args=(scope,), name=f"ytxhs-{scope}-{index}", daemon=True)
        self._threads.append(thread)
        thread.start()

    def submit(
        self,
        scope: str,
        project_id: str,
        target: Callable[..., Any],
        *args: Any,
        platform: Optional[str] = None,
        on_queued: Optional[Callable[[int], None]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if scope not in self._queues:
            raise ValueError(f"Unsupported task scope: {scope}")
        job = Job(project_id, scope, platform, target, args, kwargs)
        with self._condition:
            if project_id in self._running or any(item.project_id == project_id for queue in self._queues.values() for item in queue):
                raise RuntimeError(f"Project already has an active task: {project_id}")
            active_for_scope = sum(1 for item in self._running.values() if item.scope == scope and not item.cancelled.is_set())
            queued = active_for_scope >= self._limits[scope] or bool(self._queues[scope])
            self._queues[scope].append(job)
            position = len(self._queues[scope]) if queued else 0
            if queued and on_queued:
                on_queued(position)
            self._condition.notify_all()
        job.started.wait(timeout=0.15)
        return {"queued": queued, "queue_position": position, "scope": scope, "platform": platform}

    def _worker(self, scope: str) -> None:
        while True:
            with self._condition:
                while not self._queues[scope] and not self._shutdown:
                    self._condition.wait()
                if self._shutdown:
                    return
                job = self._queues[scope].popleft()
                if job.cancelled.is_set():
                    continue
                self._running[job.project_id] = job
                job.started.set()
            token = CURRENT_PROJECT_ID.set(job.project_id)
            retire_worker = False
            try:
                if not job.cancelled.is_set():
                    if self._should_isolate(job):
                        self._run_isolated(job)
                    else:
                        job.target(job.project_id, *job.args, **job.kwargs)
            except Exception as exc:
                self._record_worker_exception(job, exc)
            finally:
                CURRENT_PROJECT_ID.reset(token)
                with self._condition:
                    self._running.pop(job.project_id, None)
                    self._condition.notify_all()
                retire_worker = job.retire_worker
            if retire_worker:
                return

    @staticmethod
    def _record_worker_exception(job: Job, exc: Exception) -> None:
        if job.cancelled.is_set():
            return
        try:
            from app.schemas.models import ProjectStatus
            from app.services.runtime_store import store

            record = store.get(job.project_id)
            if record.status in {
                ProjectStatus.queued,
                ProjectStatus.created,
                ProjectStatus.ingesting,
                ProjectStatus.transcribing,
                ProjectStatus.extracting_frames,
                ProjectStatus.analyzing_visuals,
                ProjectStatus.planning_content,
                ProjectStatus.producing_article,
                ProjectStatus.validating_content,
                ProjectStatus.rendering_cards,
            }:
                store.fail(
                    job.project_id,
                    {
                        "code": "task_worker_failed",
                        "message": "The task worker raised an unexpected error.",
                        "step": record.status.value,
                        "details": {
                            "type": type(exc).__name__,
                            "error": str(exc),
                            "scope": job.scope,
                            "platform": job.platform,
                        },
                    },
                )
        except Exception:
            # The worker pool must stay alive even if the runtime record itself is unavailable.
            return

    @staticmethod
    def _should_isolate(job: Job) -> bool:
        return getattr(job.target, "__module__", "") == "app.services.pipeline"

    def _run_isolated(self, job: Job) -> None:
        target_name = getattr(job.target, "__name__", "")
        command = [
            sys.executable,
            "-m",
            "app.services.job_runner",
            target_name,
            job.project_id,
            *[str(value) for value in job.args],
        ]
        if job.kwargs:
            raise ValueError("Isolated pipeline jobs do not accept keyword arguments.")
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        child_processes.register(job.project_id, process)
        try:
            return_code = process.wait()
        finally:
            child_processes.unregister(job.project_id, process)
        if return_code != 0 and not job.cancelled.is_set():
            from app.schemas.models import ProjectStatus
            from app.services.runtime_store import store

            record = store.get(job.project_id)
            if record.status in {
                ProjectStatus.queued,
                ProjectStatus.created,
                ProjectStatus.ingesting,
                ProjectStatus.transcribing,
                ProjectStatus.extracting_frames,
                ProjectStatus.analyzing_visuals,
                ProjectStatus.planning_content,
                ProjectStatus.producing_article,
                ProjectStatus.validating_content,
                ProjectStatus.rendering_cards,
            }:
                store.fail(
                    job.project_id,
                    {
                        "code": "worker_process_failed",
                        "message": "The isolated task worker exited unexpectedly.",
                        "step": record.status.value,
                        "details": {"returncode": return_code, "scope": job.scope, "platform": job.platform},
                    },
                )

    def cancel(self, project_id: str) -> Dict[str, Any]:
        queued_cancelled = False
        running_cancelled = False
        scope = None
        with self._condition:
            for queue_scope, queue in self._queues.items():
                for job in list(queue):
                    if job.project_id != project_id:
                        continue
                    queue.remove(job)
                    job.cancelled.set()
                    queued_cancelled = True
                    scope = queue_scope
                    break
            running = self._running.get(project_id)
            if running:
                running.cancelled.set()
                running.retire_worker = True
                running_cancelled = True
                scope = running.scope
                # Replace the retiring worker immediately so the next queued task can start.
                self._start_worker(running.scope, len(self._threads))
            self._condition.notify_all()
        terminated = child_processes.terminate(project_id)
        return {
            "queued_cancelled": queued_cancelled,
            "running_cancelled": running_cancelled,
            "scope": scope,
            "terminated_child_processes": terminated,
        }

    def snapshot(self, project_id: str) -> Dict[str, Any]:
        with self._condition:
            running = self._running.get(project_id)
            if running:
                return {
                    "state": "running",
                    "scope": running.scope,
                    "platform": running.platform,
                    "queue_position": 0,
                    "limits": dict(self._limits),
                }
            for scope, queue in self._queues.items():
                for index, job in enumerate(queue):
                    if job.project_id == project_id:
                        return {
                            "state": "queued",
                            "scope": scope,
                            "platform": job.platform,
                            "queue_position": index + 1,
                            "limits": dict(self._limits),
                        }
        return {"state": "idle", "scope": None, "platform": None, "queue_position": 0, "limits": dict(self._limits)}

    def shutdown(self) -> None:
        with self._condition:
            self._shutdown = True
            self._condition.notify_all()

    def cancel_all(self) -> None:
        with self._condition:
            queued = [job for queue in self._queues.values() for job in queue]
            for queue in self._queues.values():
                queue.clear()
            running = list(self._running.values())
            for job in [*queued, *running]:
                job.cancelled.set()
            self._condition.notify_all()
        for job in running:
            child_processes.terminate(job.project_id)


child_processes = ChildProcessRegistry()
task_manager = TaskManager(settings.max_analyze_workers, settings.max_produce_workers)
