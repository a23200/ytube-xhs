import threading
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.schemas.models import BatchCreate, BatchItemStatus, BatchStatus, ProjectCreate, ProjectStatus
from app.services.batch_manager import BatchManager
from app.services.batch_store import BatchStore
from app.services.platforms import get_platform
from app.services.runtime_store import ProjectStore, write_json


class ImmediateTaskManager:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def submit(self, scope, project_id, target, *args, platform=None, on_queued=None, **kwargs):
        self.events.append((scope, project_id))
        target(project_id, *args, **kwargs)
        return {"queued": False, "queue_position": 0, "scope": scope, "platform": platform}

    def cancel(self, project_id):
        return {"queued_cancelled": False, "running_cancelled": False, "terminated_child_processes": 0}


def _wait_for_batch(store: BatchStore, batch_id: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = store.get(batch_id)
        if record.status in {
            BatchStatus.completed,
            BatchStatus.completed_with_errors,
            BatchStatus.stopped,
            BatchStatus.failed,
        }:
            return record
        time.sleep(0.02)
    raise AssertionError(f"batch did not finish: {store.get(batch_id).model_dump(mode='json')}")


def _complete_analysis(projects: ProjectStore, project_id: str) -> None:
    paths = projects.paths(project_id)
    artifacts = {
        "metadata": (paths.source_dir / "metadata.json", {"title": f"Title {project_id}"}),
        "transcript": (paths.transcript_dir / "transcript.json", {"segments": []}),
        "keyframes": (paths.analysis_dir / "keyframes.json", {"keyframes": []}),
        "visual_analysis": (paths.analysis_dir / "visual-analysis.json", {"frames": []}),
        "content_assets": (paths.analysis_dir / "content-assets.json", {"one_sentence_summary": "summary"}),
    }
    for kind, (path, payload) in artifacts.items():
        write_json(path, payload)
        projects.add_output(project_id, kind, path)
    projects.set_status(project_id, ProjectStatus.analysis_completed, "analysis done")


def _complete_produce(projects: ProjectStore, project_id: str, platform: str) -> None:
    adapter = get_platform(platform)
    paths = projects.paths(project_id)
    document = paths.file_for_kind(adapter.post_docx_kind)
    document.write_bytes(f"docx:{project_id}".encode("utf-8"))
    projects.add_output(project_id, adapter.post_docx_kind, document)
    projects.set_status(project_id, adapter.completed_status, "produce done")


def test_batch_manager_processes_each_video_fully_in_input_order(tmp_path: Path):
    batches = BatchStore(tmp_path)
    projects = ProjectStore(tmp_path)
    tasks = ImmediateTaskManager()
    order: list[tuple[str, str]] = []

    def analyze(project_id: str) -> None:
        order.append(("analyze", projects.get(project_id).url))
        _complete_analysis(projects, project_id)

    def produce(project_id: str, platform: str) -> None:
        order.append(("produce", projects.get(project_id).url))
        _complete_produce(projects, project_id, platform)

    manager = BatchManager(
        batches,
        projects,
        tasks,
        analyze_target=analyze,
        produce_target=produce,
        poll_interval=0.01,
    )
    batch = batches.create(
        BatchCreate(
            urls=["https://example.com/video/1", "https://example.com/video/2", "https://example.com/video/3"],
            target_platform="douyin",
        )
    )
    manager.submit(batch.batch_id)
    result = _wait_for_batch(batches, batch.batch_id)
    manager.shutdown()

    assert result.status == BatchStatus.completed
    assert [item.status for item in result.items] == [BatchItemStatus.completed] * 3
    assert result.document_count == 3
    assert order == [
        ("analyze", "https://example.com/video/1"),
        ("produce", "https://example.com/video/1"),
        ("analyze", "https://example.com/video/2"),
        ("produce", "https://example.com/video/2"),
        ("analyze", "https://example.com/video/3"),
        ("produce", "https://example.com/video/3"),
    ]
    assert [item.document_filename[:4] for item in result.items] == ["001-", "002-", "003-"]
    assert all((batches.paths(batch.batch_id).documents_dir / item.document_filename).exists() for item in result.items)

    archive = batches.build_archive(batch.batch_id)
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
    assert "documents/batch-summary.json" in names
    assert {f"documents/{item.document_filename}" for item in result.items} <= names


def test_batch_manager_records_failure_and_continues_with_next_url(tmp_path: Path):
    batches = BatchStore(tmp_path)
    projects = ProjectStore(tmp_path)
    tasks = ImmediateTaskManager()

    def analyze(project_id: str) -> None:
        project = projects.get(project_id)
        if project.url.endswith("/bad"):
            projects.fail(
                project_id,
                {"code": "source_failed", "message": "source unavailable", "step": "ingest", "details": {}},
            )
            return
        _complete_analysis(projects, project_id)

    manager = BatchManager(
        batches,
        projects,
        tasks,
        analyze_target=analyze,
        produce_target=lambda project_id, platform: _complete_produce(projects, project_id, platform),
        poll_interval=0.01,
    )
    batch = batches.create(
        BatchCreate(
            urls=["https://example.com/bad", "https://example.com/good"],
            target_platform="bilibili",
            continue_on_error=True,
        )
    )
    manager.submit(batch.batch_id)
    result = _wait_for_batch(batches, batch.batch_id)
    manager.shutdown()

    assert result.status == BatchStatus.completed_with_errors
    assert result.failed_count == 1
    assert result.completed_count == 1
    assert result.document_count == 1
    assert result.items[0].error["code"] == "source_failed"
    assert result.items[1].document_filename.startswith("002-")


def test_batch_manager_recovers_persisted_analysis_and_finishes_document(tmp_path: Path):
    batches = BatchStore(tmp_path)
    projects = ProjectStore(tmp_path)
    tasks = ImmediateTaskManager()
    batch = batches.create(BatchCreate(urls=["https://example.com/resume"], target_platform="xhs"))
    project = projects.create(
        ProjectCreate(
            url="https://example.com/resume",
            target_platform="xhs",
            text_only=True,
        )
    )
    _complete_analysis(projects, project.project_id)
    batches.set_item_status(
        batch.batch_id,
        1,
        BatchItemStatus.analyzing,
        project_id=project.project_id,
    )
    batches.mark_running(batch.batch_id)

    manager = BatchManager(
        batches,
        projects,
        tasks,
        analyze_target=lambda project_id: (_ for _ in ()).throw(AssertionError("analysis must not rerun")),
        produce_target=lambda project_id, platform: _complete_produce(projects, project_id, platform),
        poll_interval=0.01,
    )
    manager.recover()
    result = _wait_for_batch(batches, batch.batch_id)
    manager.shutdown()

    assert result.status == BatchStatus.completed
    assert result.items[0].project_id == project.project_id
    assert result.items[0].document_filename.startswith("001-")
    assert [scope for scope, _project_id in tasks.events] == ["produce"]


def test_batch_manager_stop_cancels_current_project_and_leaves_later_urls_uncreated(tmp_path: Path):
    batches = BatchStore(tmp_path)
    projects = ProjectStore(tmp_path)
    tasks = ImmediateTaskManager()
    entered = threading.Event()
    release = threading.Event()

    def blocking_analyze(project_id: str) -> None:
        projects.set_status(project_id, ProjectStatus.ingesting, "blocking test")
        entered.set()
        release.wait(timeout=2)

    manager = BatchManager(
        batches,
        projects,
        tasks,
        analyze_target=blocking_analyze,
        produce_target=lambda project_id, platform: _complete_produce(projects, project_id, platform),
        poll_interval=0.01,
    )
    batch = batches.create(
        BatchCreate(urls=["https://example.com/one", "https://example.com/two"], target_platform="douyin")
    )
    manager.submit(batch.batch_id)
    assert entered.wait(timeout=2)

    cancelled = manager.cancel(batch.batch_id)
    release.set()
    manager.shutdown()

    result = cancelled["batch"]
    assert cancelled["project_cancelled"] is True
    assert result.status == BatchStatus.stopped
    assert [item.status for item in result.items] == [BatchItemStatus.stopped, BatchItemStatus.stopped]
    assert result.items[0].project_id
    assert result.items[1].project_id is None


class FakeBatchManager:
    def submit(self, batch_id):
        return {"queued": False, "queue_position": 0}

    def snapshot(self, batch_id):
        return {"state": "idle", "queue_position": 0}

    def cancel(self, batch_id):
        return {"batch": routes.batch_store.stop(batch_id), "project_cancelled": False}


def test_batch_api_creates_lists_and_downloads_registered_documents(tmp_path: Path, monkeypatch):
    test_store = BatchStore(tmp_path)
    monkeypatch.setattr(routes, "batch_store", test_store)
    monkeypatch.setattr(routes, "batch_manager", FakeBatchManager())
    monkeypatch.setattr(routes.llm_client, "ensure_available", lambda step: None)
    client = TestClient(app)

    created = client.post(
        "/api/batches",
        json={
            "urls": ["https://example.com/one", "https://example.com/two"],
            "target_platform": "toutiao",
            "text_only": True,
        },
    )
    assert created.status_code == 200
    batch_id = created.json()["batch_id"]
    assert created.json()["total_count"] == 2

    source = tmp_path / "article.docx"
    source.write_bytes(b"word-document")
    test_store.copy_document(batch_id, source, "001-first.docx")
    test_store.set_item_status(
        batch_id,
        1,
        BatchItemStatus.completed,
        title="first",
        document_filename="001-first.docx",
    )

    listed = client.get("/api/batches")
    detail = client.get(f"/api/batches/{batch_id}")
    document = client.get(f"/api/batches/{batch_id}/documents/001-first.docx")
    archive = client.get(f"/api/batches/{batch_id}/download")

    assert listed.status_code == 200
    assert listed.json()[0]["batch_id"] == batch_id
    assert detail.json()["progress_percent"] == 50
    assert detail.json()["download_ready"] is True
    assert document.status_code == 200 and document.content == b"word-document"
    assert archive.status_code == 200
    archive_path = tmp_path / "download.zip"
    archive_path.write_bytes(archive.content)
    with zipfile.ZipFile(archive_path) as bundle:
        assert "documents/001-first.docx" in bundle.namelist()
