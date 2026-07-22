import fcntl
import os
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.schemas.models import (
    BatchCreate,
    BatchItem,
    BatchItemStatus,
    BatchLog,
    BatchRecord,
    BatchStatus,
)
from app.services.config import settings
from app.services.runtime_store import read_json, utc_now, write_json

BATCH_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
BATCH_TERMINAL_STATUSES = {
    BatchStatus.completed,
    BatchStatus.completed_with_errors,
    BatchStatus.stopped,
    BatchStatus.failed,
}
BATCH_ITEM_TERMINAL_STATUSES = {
    BatchItemStatus.completed,
    BatchItemStatus.failed,
    BatchItemStatus.stopped,
    BatchItemStatus.skipped,
}


class BatchPaths:
    def __init__(self, batch_dir: Path) -> None:
        self.batch_dir = batch_dir
        self.documents_dir = batch_dir / "documents"
        self.status_file = batch_dir / "batch.json"
        self.manifest_file = self.documents_dir / "batch-summary.json"
        self.archive_file = batch_dir / "documents.zip"

    def ensure(self) -> None:
        self.documents_dir.mkdir(parents=True, exist_ok=True)


class BatchStore:
    def __init__(self, runtime_dir: Optional[Path] = None) -> None:
        self.runtime_dir = runtime_dir or settings.runtime_dir
        self.batches_dir = self.runtime_dir / "batches"
        self.locks_dir = self.runtime_dir / ".locks"
        self.batches_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        self._locks_guard = threading.Lock()
        self._batch_locks: Dict[str, threading.RLock] = {}

    @contextmanager
    def _locked_batch(self, batch_id: str):
        with self._locks_guard:
            thread_lock = self._batch_locks.setdefault(batch_id, threading.RLock())
        with thread_lock:
            lock_path = self.locks_dir / f"batch-{batch_id}.lock"
            with lock_path.open("a+", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def paths(self, batch_id: str) -> BatchPaths:
        if not BATCH_ID_RE.fullmatch(batch_id):
            raise ValueError("Invalid batch_id")
        batch_dir = (self.batches_dir / batch_id).resolve()
        try:
            batch_dir.relative_to(self.batches_dir.resolve())
        except ValueError:
            raise ValueError("Invalid batch_id") from None
        return BatchPaths(batch_dir)

    def create(self, request: BatchCreate) -> BatchRecord:
        batch_id = uuid.uuid4().hex[:12]
        paths = self.paths(batch_id)
        paths.ensure()
        now = utc_now()
        record = BatchRecord(
            batch_id=batch_id,
            target_platform=request.target_platform,
            language=request.language,
            style=request.style,
            use_whisper=request.use_whisper,
            use_ocr=request.use_ocr,
            text_only=request.text_only,
            max_frames=request.max_frames,
            continue_on_error=request.continue_on_error,
            status=BatchStatus.queued,
            created_at=now,
            updated_at=now,
            current_index=None,
            total_count=len(request.urls),
            items=[
                BatchItem(index=index, url=str(url), status=BatchItemStatus.pending)
                for index, url in enumerate(request.urls, start=1)
            ],
            logs=[
                BatchLog(
                    time=now,
                    status=BatchStatus.queued,
                    message="Batch created and queued.",
                    details={"total_count": len(request.urls), "platform": request.target_platform},
                )
            ],
        )
        self._write_record(paths, record)
        return self.get(batch_id)

    def get(self, batch_id: str) -> BatchRecord:
        path = self.paths(batch_id).status_file
        if not path.exists():
            raise FileNotFoundError(batch_id)
        return BatchRecord(**read_json(path))

    def list(self) -> List[BatchRecord]:
        records = []
        for path in sorted(self.batches_dir.glob("*/batch.json"), reverse=True):
            try:
                records.append(BatchRecord(**read_json(path)))
            except Exception:
                continue
        return records

    def mark_running(self, batch_id: str, *, resumed: bool = False) -> BatchRecord:
        with self._locked_batch(batch_id):
            paths, record = self._read_locked(batch_id)
            if record.status in BATCH_TERMINAL_STATUSES:
                return record
            record.status = BatchStatus.running
            record.updated_at = utc_now()
            record.logs.append(
                BatchLog(
                    time=record.updated_at,
                    status=record.status,
                    message="Batch resumed after service restart." if resumed else "Batch processing started.",
                )
            )
            self._write_record(paths, record)
            return record

    def set_item_status(
        self,
        batch_id: str,
        index: int,
        status: BatchItemStatus,
        *,
        project_id: Optional[str] = None,
        title: Optional[str] = None,
        document_filename: Optional[str] = None,
        error: Optional[Dict[str, Any]] = None,
    ) -> BatchRecord:
        with self._locked_batch(batch_id):
            paths, record = self._read_locked(batch_id)
            item = self._item(record, index)
            now = utc_now()
            item.status = status
            if project_id is not None:
                item.project_id = project_id
            if title is not None:
                item.title = title
            if document_filename is not None:
                item.document_filename = document_filename
            item.error = error
            if status in {BatchItemStatus.analyzing, BatchItemStatus.producing}:
                item.started_at = item.started_at or now
                item.completed_at = None
                record.current_index = index
            elif status in BATCH_ITEM_TERMINAL_STATUSES:
                item.completed_at = now
            record.updated_at = now
            self._recount(record)
            self._write_record(paths, record)
            return record

    def finish(self, batch_id: str) -> BatchRecord:
        with self._locked_batch(batch_id):
            paths, record = self._read_locked(batch_id)
            if record.status == BatchStatus.stopped:
                return record
            self._recount(record)
            record.current_index = None
            if record.failed_count or record.stopped_count or record.skipped_count:
                record.status = BatchStatus.completed_with_errors
            else:
                record.status = BatchStatus.completed
            record.updated_at = utc_now()
            record.logs.append(
                BatchLog(
                    time=record.updated_at,
                    status=record.status,
                    message="Batch processing completed.",
                    details={
                        "completed": record.completed_count,
                        "failed": record.failed_count,
                        "stopped": record.stopped_count,
                        "skipped": record.skipped_count,
                        "documents": record.document_count,
                    },
                )
            )
            self._write_record(paths, record)
            return record

    def fail(self, batch_id: str, error: Dict[str, Any]) -> BatchRecord:
        with self._locked_batch(batch_id):
            paths, record = self._read_locked(batch_id)
            if record.status == BatchStatus.stopped:
                return record
            record.status = BatchStatus.failed
            record.current_index = None
            record.error = error
            record.updated_at = utc_now()
            record.logs.append(
                BatchLog(
                    time=record.updated_at,
                    status=record.status,
                    message=error.get("message", "Batch worker failed."),
                    details=error,
                )
            )
            self._write_record(paths, record)
            return record

    def stop(self, batch_id: str) -> BatchRecord:
        with self._locked_batch(batch_id):
            paths, record = self._read_locked(batch_id)
            if record.status in BATCH_TERMINAL_STATUSES:
                return record
            now = utc_now()
            for item in record.items:
                if item.status not in BATCH_ITEM_TERMINAL_STATUSES:
                    item.status = BatchItemStatus.stopped
                    item.completed_at = now
                    item.error = {
                        "code": "batch_stopped",
                        "message": "The batch was stopped by the user.",
                        "step": "batch",
                    }
            record.status = BatchStatus.stopped
            record.current_index = None
            record.updated_at = now
            self._recount(record)
            record.logs.append(BatchLog(time=now, status=record.status, message="Batch was stopped by the user."))
            self._write_record(paths, record)
            return record

    def skip_remaining(self, batch_id: str, after_index: int) -> BatchRecord:
        with self._locked_batch(batch_id):
            paths, record = self._read_locked(batch_id)
            now = utc_now()
            for item in record.items:
                if item.index > after_index and item.status == BatchItemStatus.pending:
                    item.status = BatchItemStatus.skipped
                    item.completed_at = now
                    item.error = {
                        "code": "batch_stopped_after_error",
                        "message": "The item was skipped because continue_on_error is disabled.",
                        "step": "batch",
                    }
            record.updated_at = now
            self._recount(record)
            self._write_record(paths, record)
            return record

    def build_archive(self, batch_id: str) -> Path:
        with self._locked_batch(batch_id):
            paths, record = self._read_locked(batch_id)
            registered = {item.document_filename for item in record.items if item.document_filename}
            if not registered:
                raise FileNotFoundError("No batch documents are available")
            descriptor, temporary_name = tempfile.mkstemp(prefix=".documents.", suffix=".zip", dir=paths.batch_dir)
            os.close(descriptor)
            temporary_path = Path(temporary_name)
            try:
                with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for filename in sorted(registered):
                        document_path = self.document_path(record.batch_id, filename)
                        if document_path.exists():
                            archive.write(document_path, arcname=f"documents/{filename}")
                    if paths.manifest_file.exists():
                        archive.write(paths.manifest_file, arcname="documents/batch-summary.json")
                os.replace(temporary_path, paths.archive_file)
            finally:
                temporary_path.unlink(missing_ok=True)
            return paths.archive_file

    def document_path(self, batch_id: str, filename: str) -> Path:
        paths = self.paths(batch_id)
        if Path(filename).name != filename or not filename.lower().endswith(".docx"):
            raise ValueError("Invalid batch document filename")
        document_path = (paths.documents_dir / filename).resolve()
        try:
            document_path.relative_to(paths.documents_dir.resolve())
        except ValueError:
            raise ValueError("Invalid batch document filename") from None
        return document_path

    def copy_document(self, batch_id: str, source: Path, filename: str) -> Path:
        destination = self.document_path(batch_id, filename)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
        return destination

    def resume_candidates(self) -> List[BatchRecord]:
        return [record for record in reversed(self.list()) if record.status in {BatchStatus.queued, BatchStatus.running}]

    def _read_locked(self, batch_id: str) -> tuple[BatchPaths, BatchRecord]:
        paths = self.paths(batch_id)
        if not paths.status_file.exists():
            raise FileNotFoundError(batch_id)
        return paths, BatchRecord(**read_json(paths.status_file))

    @staticmethod
    def _item(record: BatchRecord, index: int) -> BatchItem:
        for item in record.items:
            if item.index == index:
                return item
        raise IndexError(index)

    @staticmethod
    def _recount(record: BatchRecord) -> None:
        record.total_count = len(record.items)
        record.completed_count = sum(item.status == BatchItemStatus.completed for item in record.items)
        record.failed_count = sum(item.status == BatchItemStatus.failed for item in record.items)
        record.stopped_count = sum(item.status == BatchItemStatus.stopped for item in record.items)
        record.skipped_count = sum(item.status == BatchItemStatus.skipped for item in record.items)
        record.document_count = sum(bool(item.document_filename) for item in record.items)

    def _write_record(self, paths: BatchPaths, record: BatchRecord) -> None:
        paths.ensure()
        self._recount(record)
        payload = record.model_dump(mode="json")
        write_json(paths.status_file, payload)
        write_json(paths.manifest_file, payload)
        paths.archive_file.unlink(missing_ok=True)


batch_store = BatchStore()
