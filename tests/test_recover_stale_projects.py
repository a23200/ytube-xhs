from pathlib import Path

from app.schemas.models import ProjectCreate, ProjectStatus
from app.services.runtime_store import ProjectStore, read_json, write_json
from scripts import recover_stale_projects


def test_recover_stale_projects_writes_partial_package_and_registers_outputs(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(recover_stale_projects, "store", test_store)
    record = test_store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="干货",
            use_whisper=True,
            max_frames=8,
        )
    )
    paths = test_store.paths(record.project_id)
    video_path = paths.source_dir / "source.mp4"
    frame_path = paths.frames_dir / "frame_0001.jpg"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"mp4")
    frame_path.write_bytes(b"jpg")
    write_json(
        paths.source_dir / "metadata.json",
        {
            "video_id": "v1",
            "url": "https://example.com/video",
            "title": "Title",
            "author": "Author",
            "duration": 12,
            "video_file": str(video_path),
        },
    )
    write_json(paths.analysis_dir / "keyframes.json", {"frame_count": 1, "keyframes": []})
    test_store.add_output(record.project_id, "metadata", paths.file_for_kind("metadata"))
    test_store.set_status(record.project_id, ProjectStatus.extracting_frames, "extracting")

    result = recover_stale_projects.recover_stale_projects(older_than_seconds=0)
    updated = test_store.get(record.project_id)
    package = read_json(paths.analysis_dir / "asset-package.json")

    assert result["recovered_count"] == 1
    assert result["projects"][0]["project_id"] == record.project_id
    assert "metadata" in result["projects"][0]["registered_outputs"]
    assert "asset_package" in result["projects"][0]["registered_outputs"]
    assert updated.status == ProjectStatus.failed
    assert updated.error["code"] == "stale_running_project"
    assert updated.outputs["asset_package"] == "analysis/asset-package.json"
    assert package["status"] == "partial_failed"
    assert package["error"]["details"]["previous_status"] == "extracting_frames"


def test_recover_stale_projects_dry_run_does_not_mutate(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(recover_stale_projects, "store", test_store)
    record = test_store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="干货",
            use_whisper=True,
            max_frames=8,
        )
    )
    test_store.set_status(record.project_id, ProjectStatus.ingesting, "started")

    result = recover_stale_projects.recover_stale_projects(older_than_seconds=0, dry_run=True)

    assert result["recovered_count"] == 1
    assert result["projects"][0]["dry_run"] is True
    assert test_store.get(record.project_id).status == ProjectStatus.ingesting
