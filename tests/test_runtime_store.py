from pathlib import Path

import pytest

from app.schemas.models import ProjectCreate, ProjectStatus
from app.services.runtime_store import ProjectStore, read_json, write_json


def _write_registered_upstream(store: ProjectStore, project_id: str) -> None:
    paths = store.paths(project_id)
    write_json(paths.source_dir / "metadata.json", {"video_id": "v1"})
    write_json(paths.transcript_dir / "transcript.json", {"segments": []})
    write_json(paths.analysis_dir / "keyframes.json", {"keyframes": []})
    write_json(paths.analysis_dir / "visual-analysis.json", {"frames": []})
    for kind in ["metadata", "transcript", "keyframes", "visual_analysis"]:
        store.add_output(project_id, kind, paths.file_for_kind(kind))


def _write_registered_visual_upstream(store: ProjectStore, project_id: str) -> None:
    paths = store.paths(project_id)
    write_json(paths.source_dir / "metadata.json", {"video_id": "v1"})
    write_json(paths.transcript_dir / "transcript.json", {"segments": []})
    write_json(paths.analysis_dir / "keyframes.json", {"keyframes": []})
    for kind in ["metadata", "transcript", "keyframes"]:
        store.add_output(project_id, kind, paths.file_for_kind(kind))


def test_project_store_creates_status_and_outputs(tmp_path: Path):
    store = ProjectStore(tmp_path)
    record = store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="干货",
            use_whisper=True,
            max_frames=8,
        )
    )
    assert record.status == ProjectStatus.created
    assert record.outputs["run_metadata"] == "analysis/run-metadata.json"

    paths = store.paths(record.project_id)
    output = paths.analysis_dir / "xhs-post.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("# ok", encoding="utf-8")
    store.add_output(record.project_id, "xhs_post_md", output)

    updated = store.set_status(record.project_id, ProjectStatus.completed, "done")
    assert updated.status == ProjectStatus.completed
    assert updated.outputs["xhs_post_md"] == "analysis/xhs-post.md"
    assert updated.outputs["run_metadata"] == "analysis/run-metadata.json"
    assert paths.run_metadata_file().exists()


def test_project_store_run_metadata_includes_source_traceability(tmp_path: Path):
    store = ProjectStore(tmp_path)
    record = store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="干货",
            use_whisper=True,
            max_frames=8,
        )
    )
    paths = store.paths(record.project_id)
    video_file = paths.source_dir / "source.mp4"
    thumbnail_file = paths.source_dir / "thumbnail.jpg"
    video_file.write_bytes(b"mp4")
    thumbnail_file.write_bytes(b"jpg")
    write_json(
        paths.source_dir / "metadata.json",
        {
            "video_id": "v1",
            "url": "https://example.com/video",
            "title": "Title",
            "author": "Author",
            "duration": 12,
            "thumbnail": "https://example.com/thumb.jpg",
            "thumbnail_file": str(thumbnail_file),
            "video_file": str(video_file),
        },
    )

    store.add_output(record.project_id, "metadata", paths.source_dir / "metadata.json")

    run_metadata = read_json(paths.run_metadata_file())
    assert run_metadata["video_id"] == "v1"
    assert run_metadata["title"] == "Title"
    assert run_metadata["author"] == "Author"
    assert run_metadata["duration"] == 12
    assert run_metadata["source_url"] == "https://example.com/video"
    assert run_metadata["source_metadata"]["video_file"] == str(video_file)


def test_project_store_clears_old_error_when_status_recovers(tmp_path: Path):
    store = ProjectStore(tmp_path)
    record = store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="干货",
            use_whisper=True,
            max_frames=8,
        )
    )

    failed = store.fail(record.project_id, {"code": "old_failure", "message": "old", "step": "planning_content"})
    assert failed.status == ProjectStatus.failed
    assert failed.error["code"] == "old_failure"

    recovered = store.set_status(record.project_id, ProjectStatus.planning_content, "retrying")

    assert recovered.status == ProjectStatus.planning_content
    assert recovered.error is None


def test_project_store_try_start_downstream_rerun_only_when_ready(tmp_path: Path):
    store = ProjectStore(tmp_path)
    record = store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="干货",
            use_whisper=True,
            max_frames=8,
        )
    )

    busy_started, busy, busy_missing = store.try_start_downstream_rerun(record.project_id)
    assert busy_started is False
    assert busy.status == ProjectStatus.created
    assert busy_missing == []
    assert store.can_start_downstream_rerun(record.project_id) is False

    failed = store.fail(record.project_id, {"code": "llm_unavailable", "message": "missing", "step": "planning_content"})
    assert failed.status == ProjectStatus.failed
    assert store.can_start_downstream_rerun(record.project_id) is False
    assert store.downstream_rerun_missing_inputs(record.project_id) == [
        "metadata",
        "transcript",
        "keyframes",
        "visual_analysis",
    ]

    missing_started, missing_record, missing_inputs = store.try_start_downstream_rerun(record.project_id)
    assert missing_started is False
    assert missing_record.status == ProjectStatus.failed
    assert missing_inputs == ["metadata", "transcript", "keyframes", "visual_analysis"]

    _write_registered_upstream(store, record.project_id)
    assert store.can_start_downstream_rerun(record.project_id) is True

    queued_started, queued, queued_missing = store.try_start_downstream_rerun(record.project_id)
    assert queued_started is True
    assert queued_missing == []
    assert queued.status == ProjectStatus.planning_content
    assert queued.error is None
    assert queued.logs[-1].message == "Downstream rerun queued."
    assert store.can_start_downstream_rerun(record.project_id) is False

    duplicate_started, duplicate, duplicate_missing = store.try_start_downstream_rerun(record.project_id)
    assert duplicate_started is False
    assert duplicate_missing == []
    assert duplicate.status == ProjectStatus.planning_content
    assert len(duplicate.logs) == len(queued.logs)


def test_project_store_try_start_visual_rerun_only_needs_pre_visual_artifacts(tmp_path: Path):
    store = ProjectStore(tmp_path)
    record = store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="干货",
            use_whisper=True,
            max_frames=8,
        )
    )

    busy_started, busy, busy_missing = store.try_start_visual_rerun(record.project_id)
    assert busy_started is False
    assert busy.status == ProjectStatus.created
    assert busy_missing == []
    assert store.can_start_visual_rerun(record.project_id) is False

    failed = store.fail(record.project_id, {"code": "llm_unavailable", "message": "missing", "step": "planning_content"})
    assert failed.status == ProjectStatus.failed
    assert store.can_start_visual_rerun(record.project_id) is False
    assert store.visual_rerun_missing_inputs(record.project_id) == ["metadata", "transcript", "keyframes"]

    missing_started, missing_record, missing_inputs = store.try_start_visual_rerun(record.project_id)
    assert missing_started is False
    assert missing_record.status == ProjectStatus.failed
    assert missing_inputs == ["metadata", "transcript", "keyframes"]

    _write_registered_visual_upstream(store, record.project_id)
    assert store.can_start_visual_rerun(record.project_id) is True
    assert store.visual_rerun_missing_inputs(record.project_id) == []

    queued_started, queued, queued_missing = store.try_start_visual_rerun(record.project_id)
    assert queued_started is True
    assert queued_missing == []
    assert queued.status == ProjectStatus.analyzing_visuals
    assert queued.error is None
    assert queued.logs[-1].message == "Visual analysis rerun queued."
    assert store.can_start_visual_rerun(record.project_id) is False


def test_project_store_cancel_running_project_releases_status_and_blocks_late_writes(tmp_path: Path):
    store = ProjectStore(tmp_path)
    record = store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="干货",
            use_whisper=True,
            max_frames=8,
        )
    )
    paths = store.paths(record.project_id)
    store.set_status(record.project_id, ProjectStatus.ingesting, "started")

    cancelled = store.cancel(record.project_id)

    assert cancelled.status == ProjectStatus.failed
    assert cancelled.error["code"] == "user_stopped"
    assert cancelled.error["details"]["previous_status"] == "ingesting"
    assert paths.cancel_file().exists()
    assert store.can_cancel(record.project_id) is False

    late = store.set_status(record.project_id, ProjectStatus.transcribing, "late write")
    assert late.status == ProjectStatus.failed
    assert late.error["code"] == "user_stopped"

    write_json(paths.source_dir / "metadata.json", {"video_id": "v1"})
    store.add_output(record.project_id, "metadata", paths.source_dir / "metadata.json")
    assert "metadata" not in store.get(record.project_id).outputs


def test_project_store_retry_clears_cancel_marker_when_inputs_ready(tmp_path: Path):
    store = ProjectStore(tmp_path)
    record = store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="干货",
            use_whisper=True,
            max_frames=8,
        )
    )
    paths = store.paths(record.project_id)
    _write_registered_upstream(store, record.project_id)
    write_json(paths.analysis_dir / "content-assets.json", {"ok": True})
    store.add_output(record.project_id, "content_assets", paths.analysis_dir / "content-assets.json")

    store.set_status(record.project_id, ProjectStatus.producing_article, "started")
    store.cancel(record.project_id)
    assert paths.cancel_file().exists()

    started, queued, missing = store.try_start_produce(record.project_id)

    assert started is True
    assert missing == []
    assert queued.status == ProjectStatus.producing_article
    assert queued.error is None
    assert not paths.cancel_file().exists()


def test_project_store_marks_stale_running_projects_failed(tmp_path: Path):
    store = ProjectStore(tmp_path)
    running = store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="干货",
            use_whisper=True,
            max_frames=8,
        )
    )
    completed = store.create(
        ProjectCreate(
            url="https://example.com/video2",
            language="zh",
            style="干货",
            use_whisper=True,
            max_frames=8,
        )
    )
    store.set_status(running.project_id, ProjectStatus.ingesting, "started")
    store.set_status(completed.project_id, ProjectStatus.completed, "done")

    dry_run = store.mark_stale_running_failed(older_than_seconds=0, dry_run=True)
    assert [item["project_id"] for item in dry_run] == [running.project_id]
    assert store.get(running.project_id).status == ProjectStatus.ingesting

    recovered = store.mark_stale_running_failed(older_than_seconds=0)
    updated = store.get(running.project_id)

    assert [item["project_id"] for item in recovered] == [running.project_id]
    assert updated.status == ProjectStatus.failed
    assert updated.error["code"] == "stale_running_project"
    assert updated.error["details"]["previous_status"] == "ingesting"
    assert store.get(completed.project_id).status == ProjectStatus.completed


def test_project_store_rejects_missing_output_file(tmp_path: Path):
    store = ProjectStore(tmp_path)
    record = store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="干货",
            use_whisper=True,
            max_frames=8,
        )
    )

    with pytest.raises(FileNotFoundError):
        store.add_output(record.project_id, "metadata", store.paths(record.project_id).source_dir / "metadata.json")


def test_project_store_rejects_unknown_or_nonstandard_output_paths(tmp_path: Path):
    store = ProjectStore(tmp_path)
    record = store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="干货",
            use_whisper=True,
            max_frames=8,
        )
    )
    paths = store.paths(record.project_id)
    wrong_path = paths.analysis_dir / "metadata.json"
    wrong_path.parent.mkdir(parents=True, exist_ok=True)
    wrong_path.write_text("{}", encoding="utf-8")
    standard_path = paths.source_dir / "metadata.json"
    standard_path.parent.mkdir(parents=True, exist_ok=True)
    standard_path.write_text("{}", encoding="utf-8")

    with pytest.raises(KeyError):
        store.add_output(record.project_id, "unknown", standard_path)

    with pytest.raises(ValueError):
        store.add_output(record.project_id, "metadata", wrong_path)


def test_project_store_rejects_unsafe_project_ids(tmp_path: Path):
    store = ProjectStore(tmp_path)

    for project_id in ["../escape", "nested/project", "", "bad id"]:
        with pytest.raises(ValueError):
            store.paths(project_id)
