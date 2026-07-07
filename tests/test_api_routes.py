import zipfile

from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.schemas.models import ProjectStatus
from app.services.runtime_store import ProjectStore, read_json, write_json


def _write_registered_upstream(test_store: ProjectStore, project_id: str) -> None:
    paths = test_store.paths(project_id)
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
            "available_subtitles": ["en"],
            "automatic_captions": ["en-auto"],
        },
    )
    write_json(
        paths.transcript_dir / "transcript.json",
        {
            "source": "subtitle",
            "segment_count": 1,
            "segments": [{"start": 0.0, "end": 1.0, "text": "字幕", "source": "subtitle"}],
        },
    )
    write_json(
        paths.analysis_dir / "keyframes.json",
        {
            "frame_count": 1,
            "keyframes": [{"time": 0.5, "path": str(frame_path), "score": 0.9, "reason": "test"}],
        },
    )
    write_json(
        paths.analysis_dir / "visual-analysis.json",
        {
            "frames": [
                {
                    "time": 0.5,
                    "path": str(frame_path),
                    "ocr_text": "",
                    "visual_summary": "summary",
                    "detected_objects": [],
                    "screen_text_confidence": 0.0,
                    "ocr_provider": "none",
                    "frame_metrics": {
                        "available": True,
                        "width": 320,
                        "height": 240,
                        "brightness": 100.0,
                        "sharpness": 200.0,
                        "brightness_label": "medium",
                        "sharpness_label": "sharp",
                        "color_tone": "neutral",
                    },
                }
            ]
        },
    )
    for kind in ["metadata", "transcript", "keyframes", "visual_analysis"]:
        test_store.add_output(project_id, kind, paths.file_for_kind(kind))


def _valid_content_assets() -> dict:
    return {
        "one_sentence_summary": "一句话总结",
        "core_points": [
            {
                "point": "观点",
                "why_it_matters": "原因",
                "evidence": [{"type": "transcript", "time": 0.5, "text": "字幕"}],
            }
        ],
        "golden_quotes": [{"quote": "金句", "time": 0.5, "rewrite_note": "已改写"}],
        "chapters": [{"title": "章节", "start": 0.0, "end": 1.0, "summary": "总结"}],
        "steps": [{"step": "步骤", "evidence_time": 0.5}],
        "audience": ["目标用户"],
        "pain_points": ["痛点"],
        "xiaohongshu_angles": ["角度"],
        "recommended_content_type": "清单",
        "source_evidence": [{"claim": "观点", "source_type": "transcript", "time": 0.5, "source_text": "字幕"}],
    }


def test_health_and_diagnostics():
    client = TestClient(app)

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {"ok": True}

    diagnostics = client.get("/api/diagnostics")
    assert diagnostics.status_code == 200
    body = diagnostics.json()
    assert "commands" in body
    assert "modules" in body
    assert "ready_for" in body
    assert "version" in body["commands"]["ffmpeg"]
    assert "configured" in body["llm"]
    assert "configured" in body["image"]
    assert "api_key" not in body["llm"]
    assert "api_key" not in body["image"]

    doctor = client.get("/api/system/doctor")
    assert doctor.status_code == 200
    doctor_body = doctor.json()
    assert "commands" in doctor_body
    assert "modules" in doctor_body
    assert "ready_for" in doctor_body
    assert "configured" in doctor_body["llm"]
    assert "configured" in doctor_body["image"]
    assert "api_key" not in doctor_body["llm"]
    assert "api_key" not in doctor_body["image"]


def test_llm_self_test_route_does_not_expose_key(monkeypatch):
    class FakeClient:
        def self_test(self):
            return {
                "ok": False,
                "error": {"code": "llm_unavailable"},
                "base_url": "https://example.test/v1",
                "model": "model",
            }

    monkeypatch.setattr(routes, "llm_client", FakeClient())
    client = TestClient(app)

    response = client.get("/api/llm/self-test")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "llm_unavailable"
    assert "key" not in str(body).lower()


def test_llm_settings_routes_do_not_expose_plaintext_key(monkeypatch):
    saved_payloads = []

    def fake_get_settings():
        return {
            "base_url": "https://example.test/v1",
            "model": "model-a",
            "api_key_configured": True,
            "api_key_source": "XHS_LLM_API_KEY",
            "require_api_key": "auto",
            "auth_required": True,
            "max_tokens": 512,
            "timeout_ms": 30000,
            "max_chars": 120000,
            "env_path": "/tmp/.env",
        }

    def fake_update_settings(request):
        saved_payloads.append(request)
        return {
            **fake_get_settings(),
            "model": request.model,
            "api_key_configured": bool(request.api_key),
        }

    monkeypatch.setattr(routes, "get_llm_settings", fake_get_settings)
    monkeypatch.setattr(routes, "update_llm_settings", fake_update_settings)
    client = TestClient(app)

    current = client.get("/api/settings/llm")
    saved = client.put(
        "/api/settings/llm",
        json={
            "base_url": "https://example.test/v1",
            "model": "model-b",
            "api_key": "secret-test-key",
            "require_api_key": "true",
            "max_tokens": 1024,
            "timeout_ms": 45000,
            "max_chars": 100000,
        },
    )

    assert current.status_code == 200
    assert current.json()["api_key_configured"] is True
    assert "secret" not in str(current.json()).lower()
    assert saved.status_code == 200
    assert saved.json()["model"] == "model-b"
    assert "secret-test-key" not in str(saved.json())
    assert saved_payloads[0].api_key == "secret-test-key"


def test_image_settings_routes_do_not_expose_plaintext_key(monkeypatch):
    saved_payloads = []

    def fake_get_settings():
        return {
            "enabled": True,
            "base_url": "https://image.example.test/v1",
            "model": "image-model-a",
            "api_key_configured": True,
            "api_key_source": "XHS_IMAGE_API_KEY",
            "require_api_key": "auto",
            "auth_required": True,
            "size": "1024x1024",
            "timeout_ms": 120000,
            "env_path": "/tmp/.env",
            "fallback_renderer": "pillow_template_v1",
        }

    def fake_update_settings(request):
        saved_payloads.append(request)
        return {
            **fake_get_settings(),
            "model": request.model,
            "api_key_configured": bool(request.api_key),
        }

    class FakeImageClient:
        def self_test(self, real=False):
            return {"ok": True, "enabled": True, "model": "image-model-a", "real_request": real}

    monkeypatch.setattr(routes, "get_image_settings", fake_get_settings)
    monkeypatch.setattr(routes, "update_image_settings", fake_update_settings)
    monkeypatch.setattr(routes, "image_client", FakeImageClient())
    client = TestClient(app)

    current = client.get("/api/settings/image")
    saved = client.put(
        "/api/settings/image",
        json={
            "enabled": True,
            "base_url": "https://image.example.test/v1",
            "model": "image-model-b",
            "api_key": "secret-image-key",
            "require_api_key": "true",
            "size": "1024x1024",
            "timeout_ms": 90000,
        },
    )
    self_test = client.get("/api/image/self-test")

    assert current.status_code == 200
    assert current.json()["api_key_configured"] is True
    assert "secret" not in str(current.json()).lower()
    assert saved.status_code == 200
    assert saved.json()["model"] == "image-model-b"
    assert "secret-image-key" not in str(saved.json())
    assert saved_payloads[0].api_key == "secret-image-key"
    assert self_test.status_code == 200
    assert self_test.json()["ok"] is True
    real_self_test = client.get("/api/image/self-test?real=true")
    assert real_self_test.status_code == 200
    assert real_self_test.json()["real_request"] is True


def test_project_file_and_download_routes(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)

    client = TestClient(app)
    response = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    assert response.status_code == 200
    project_id = response.json()["project_id"]

    paths = test_store.paths(project_id)
    write_json(paths.source_dir / "metadata.json", {"title": "真实元数据占位文件"})
    test_store.add_output(project_id, "metadata", paths.source_dir / "metadata.json")
    (paths.analysis_dir / "xhs-post.md").write_text("# ok", encoding="utf-8")
    test_store.add_output(project_id, "xhs_post_md", paths.analysis_dir / "xhs-post.md")

    metadata = client.get(f"/api/projects/{project_id}/files/metadata")
    assert metadata.status_code == 200
    assert metadata.headers["content-type"].startswith("application/json")
    assert metadata.json()["title"] == "真实元数据占位文件"

    markdown = client.get(f"/api/projects/{project_id}/files/xhs_post_md")
    assert markdown.status_code == 200
    assert markdown.text == "# ok"

    write_json(paths.transcript_dir / "transcript.json", {"segments": []})
    missing = client.get(f"/api/projects/{project_id}/files/transcript")
    assert missing.status_code == 404

    archive = client.get(f"/api/projects/{project_id}/download")
    assert archive.status_code == 200
    zip_path = tmp_path / "project.zip"
    zip_path.write_bytes(archive.content)
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert "source/metadata.json" in names
    assert "analysis/xhs-post.md" in names

    frame_0001 = paths.frames_dir / "frame_0001.jpg"
    frame_0002 = paths.frames_dir / "frame_0002.jpg"
    frame_0001.write_bytes(b"\xff\xd8\xff\xd9")
    frame_0002.write_bytes(b"\xff\xd8\xff\xd9")
    (paths.frames_dir / "frame_bad.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (paths.frames_dir / "ignored.png").write_bytes(b"not-a-jpg-frame")
    write_json(
        paths.analysis_dir / "keyframes.json",
        {"keyframes": [{"time": 1.0, "path": str(frame_0001), "score": 0.9, "reason": "registered"}]},
    )
    test_store.add_output(project_id, "keyframes", paths.analysis_dir / "keyframes.json")
    frames_archive = client.get(f"/api/projects/{project_id}/download/frames")
    assert frames_archive.status_code == 200
    frames_zip_path = tmp_path / "frames.zip"
    frames_zip_path.write_bytes(frames_archive.content)
    with zipfile.ZipFile(frames_zip_path) as zf:
        frame_names = set(zf.namelist())
    assert frame_names == {"frames/frame_0001.jpg"}

    registered_frame = client.get(f"/api/projects/{project_id}/frames/frame_0001.jpg")
    stale_frame = client.get(f"/api/projects/{project_id}/frames/frame_0002.jpg")
    assert registered_frame.status_code == 200
    assert stale_frame.status_code == 404


def test_analyze_endpoint_queues_analysis_pipeline(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    called = []
    monkeypatch.setattr(routes, "run_project_analysis_pipeline", lambda project_id: called.append(project_id))

    client = TestClient(app)
    response = client.post(
        "/api/projects/analyze",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "use_ocr": True,
            "max_frames": 8,
        },
    )

    assert response.status_code == 200
    project_id = response.json()["project_id"]
    assert called == [project_id]
    assert test_store.get(project_id).status == "created"
    assert test_store.get(project_id).text_only is False


def test_analyze_endpoint_persists_text_only_flag(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    called = []
    monkeypatch.setattr(routes, "run_project_analysis_pipeline", lambda project_id: called.append(project_id))

    client = TestClient(app)
    response = client.post(
        "/api/projects/analyze",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "use_ocr": True,
            "text_only": True,
            "max_frames": 8,
        },
    )

    assert response.status_code == 200
    project_id = response.json()["project_id"]
    assert called == [project_id]
    assert test_store.get(project_id).text_only is True


def test_produce_endpoint_requires_analysis_artifacts(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    monkeypatch.setattr(routes, "run_project_produce_pipeline", lambda project_id: None)

    client = TestClient(app)
    create = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = create.json()["project_id"]

    response = client.post(f"/api/projects/{project_id}/produce")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "produce_artifacts_missing"
    assert detail["status"] == "created"
    assert detail["missing"]


def test_produce_endpoint_queues_when_analysis_artifacts_exist(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    monkeypatch.setattr(routes.llm_client, "ensure_available", lambda step: None)
    called = []
    monkeypatch.setattr(routes, "run_project_produce_pipeline", lambda project_id: called.append(project_id))

    client = TestClient(app)
    create = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = create.json()["project_id"]
    _write_registered_upstream(test_store, project_id)
    paths = test_store.paths(project_id)
    write_json(paths.analysis_dir / "content-assets.json", _valid_content_assets())
    test_store.add_output(project_id, "content_assets", paths.file_for_kind("content_assets"))
    test_store.set_status(project_id, ProjectStatus.analysis_completed, "ready")

    response = client.post(f"/api/projects/{project_id}/produce")
    duplicate = client.post(f"/api/projects/{project_id}/produce")

    assert response.status_code == 200
    assert response.json() == {"project_id": project_id, "status": "queued", "scope": "produce"}
    assert called == [project_id]
    assert test_store.get(project_id).status == "producing_article"
    assert duplicate.status_code == 409


def test_toutiao_produce_endpoint_queues_when_analysis_artifacts_exist(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    monkeypatch.setattr(routes.llm_client, "ensure_available", lambda step: None)
    called = []
    monkeypatch.setattr(routes, "run_project_toutiao_produce_pipeline", lambda project_id: called.append(project_id))

    client = TestClient(app)
    create = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = create.json()["project_id"]
    _write_registered_upstream(test_store, project_id)
    paths = test_store.paths(project_id)
    write_json(paths.analysis_dir / "content-assets.json", _valid_content_assets())
    test_store.add_output(project_id, "content_assets", paths.file_for_kind("content_assets"))
    test_store.set_status(project_id, ProjectStatus.analysis_completed, "ready")

    response = client.post(f"/api/projects/{project_id}/produce/toutiao")
    duplicate = client.post(f"/api/projects/{project_id}/produce/toutiao")

    assert response.status_code == 200
    assert response.json() == {"project_id": project_id, "status": "queued", "scope": "produce", "platform": "toutiao"}
    assert called == [project_id]
    assert test_store.get(project_id).status == "producing_article"
    assert duplicate.status_code == 409


def test_produce_endpoint_requires_llm_before_queueing(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    called = []
    monkeypatch.setattr(routes, "run_project_produce_pipeline", lambda project_id: called.append(project_id))

    client = TestClient(app)
    create = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = create.json()["project_id"]
    _write_registered_upstream(test_store, project_id)
    paths = test_store.paths(project_id)
    write_json(paths.analysis_dir / "content-assets.json", _valid_content_assets())
    test_store.add_output(project_id, "content_assets", paths.file_for_kind("content_assets"))
    test_store.set_status(project_id, ProjectStatus.analysis_completed, "ready")

    def fail_llm(step):
        from app.services.errors import PipelineError

        raise PipelineError("llm_unavailable", "missing LLM", step)

    monkeypatch.setattr(routes.llm_client, "ensure_available", fail_llm)

    response = client.post(f"/api/projects/{project_id}/produce")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "llm_unavailable"
    assert called == []
    assert test_store.get(project_id).status == "analysis_completed"


def test_generate_images_endpoint_requires_xhs_artifacts(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    monkeypatch.setattr(routes, "run_project_image_generation_pipeline", lambda project_id, style="clean": None)

    client = TestClient(app)
    create = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = create.json()["project_id"]
    _write_registered_upstream(test_store, project_id)
    paths = test_store.paths(project_id)
    write_json(paths.analysis_dir / "content-assets.json", _valid_content_assets())
    test_store.add_output(project_id, "content_assets", paths.file_for_kind("content_assets"))
    test_store.set_status(project_id, ProjectStatus.xhs_completed, "article ready")

    response = client.post(f"/api/projects/{project_id}/generate-images")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "image_generation_artifacts_missing"
    assert detail["status"] == "xhs_completed"
    assert detail["missing"] == ["xhs_post_json", "image_prompts"]


def test_generate_images_endpoint_queues_when_xhs_artifacts_exist(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    called = []
    monkeypatch.setattr(routes, "run_project_image_generation_pipeline", lambda project_id, style="clean": called.append((project_id, style)))

    client = TestClient(app)
    create = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = create.json()["project_id"]
    _write_registered_upstream(test_store, project_id)
    paths = test_store.paths(project_id)
    write_json(paths.analysis_dir / "content-assets.json", _valid_content_assets())
    write_json(paths.analysis_dir / "xiaohongshu-post.json", {"content_type": "清单"})
    write_json(paths.analysis_dir / "image-prompts.json", {"image_prompts": []})
    for kind in ["content_assets", "xhs_post_json", "image_prompts"]:
        test_store.add_output(project_id, kind, paths.file_for_kind(kind))
    test_store.set_status(project_id, ProjectStatus.xhs_completed, "article ready")

    response = client.post(f"/api/projects/{project_id}/generate-images", json={"style": "poster"})
    duplicate = client.post(f"/api/projects/{project_id}/generate-images")

    assert response.status_code == 200
    assert response.json() == {"project_id": project_id, "status": "queued", "scope": "image_generation"}
    assert called == [(project_id, "poster")]
    assert test_store.get(project_id).status == "rendering_cards"
    assert duplicate.status_code == 409


def test_generate_images_rejects_text_only_project_before_artifact_checks(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    monkeypatch.setattr(routes, "run_project_image_generation_pipeline", lambda project_id, style="clean": None)
    monkeypatch.setattr(routes, "run_project_toutiao_image_generation_pipeline", lambda project_id, style="clean": None)

    client = TestClient(app)
    create = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "text_only": True,
            "max_frames": 8,
        },
    )
    project_id = create.json()["project_id"]
    test_store.set_status(project_id, ProjectStatus.xhs_completed, "text-only article ready")

    status = client.get(f"/api/projects/{project_id}/status")
    xhs_response = client.post(f"/api/projects/{project_id}/generate-images")
    toutiao_response = client.post(f"/api/projects/{project_id}/generate-images/toutiao")

    assert status.status_code == 200
    assert status.json()["text_only"] is True
    assert status.json()["can_generate_images"] is False
    assert status.json()["routes"]["xhs"]["can_generate_images"] is False
    assert xhs_response.status_code == 409
    assert xhs_response.json()["detail"]["code"] == "text_only_image_generation_disabled"
    assert toutiao_response.status_code == 409
    assert toutiao_response.json()["detail"]["code"] == "text_only_image_generation_disabled"


def test_toutiao_generate_images_endpoint_queues_when_artifacts_exist(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    called = []
    monkeypatch.setattr(routes, "run_project_toutiao_image_generation_pipeline", lambda project_id, style="clean": called.append((project_id, style)))

    client = TestClient(app)
    create = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = create.json()["project_id"]
    _write_registered_upstream(test_store, project_id)
    paths = test_store.paths(project_id)
    write_json(paths.analysis_dir / "content-assets.json", _valid_content_assets())
    write_json(paths.analysis_dir / "toutiao-post.json", {"content_type": "清单"})
    write_json(paths.analysis_dir / "toutiao-image-prompts.json", {"image_prompts": []})
    for kind in ["content_assets", "toutiao_post_json", "toutiao_image_prompts"]:
        test_store.add_output(project_id, kind, paths.file_for_kind(kind))
    test_store.set_status(project_id, ProjectStatus.toutiao_completed, "article ready")

    response = client.post(f"/api/projects/{project_id}/generate-images/toutiao", json={"style": "poster"})
    duplicate = client.post(f"/api/projects/{project_id}/generate-images/toutiao")

    assert response.status_code == 200
    assert response.json() == {"project_id": project_id, "status": "queued", "scope": "image_generation", "platform": "toutiao"}
    assert called == [(project_id, "poster")]
    assert test_store.get(project_id).status == "rendering_cards"
    assert duplicate.status_code == 409


def test_patch_content_assets_and_card_routes(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)

    client = TestClient(app)
    create = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = create.json()["project_id"]
    _write_registered_upstream(test_store, project_id)
    paths = test_store.paths(project_id)

    saved = client.patch(f"/api/projects/{project_id}/content-assets", json=_valid_content_assets())
    assert saved.status_code == 200
    assert read_json(paths.analysis_dir / "content-assets.json")["one_sentence_summary"] == "一句话总结"

    card_path = paths.cards_dir / "cover.png"
    card_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    write_json(
        paths.analysis_dir / "image-cards.json",
        {
            "cards": [
                {
                    "page": 1,
                    "role": "cover",
                    "title": "封面",
                    "caption": "说明",
                    "source_frame_time": 0.5,
                    "source_frame_path": str(paths.frames_dir / "frame_0001.jpg"),
                    "layout": "vertical_4_5_media_text",
                    "style": "clean",
                    "output_path": str(card_path),
                    "image_prompt": "",
                }
            ],
            "card_count": 1,
        },
    )
    test_store.add_output(project_id, "image_cards", paths.file_for_kind("image_cards"))

    card = client.get(f"/api/projects/{project_id}/cards/cover.png")
    cards_archive = client.get(f"/api/projects/{project_id}/download/cards")

    assert card.status_code == 200
    assert cards_archive.status_code == 200
    zip_path = tmp_path / "cards.zip"
    zip_path.write_bytes(cards_archive.content)
    with zipfile.ZipFile(zip_path) as zf:
        assert set(zf.namelist()) == {"cards/cover.png"}


def test_toutiao_card_routes_download_registered_cards(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)

    client = TestClient(app)
    create = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = create.json()["project_id"]
    _write_registered_upstream(test_store, project_id)
    paths = test_store.paths(project_id)
    card_path = paths.toutiao_cards_dir / "cover.png"
    card_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    write_json(
        paths.analysis_dir / "toutiao-image-cards.json",
        {
            "cards": [
                {
                    "page": 1,
                    "role": "cover",
                    "title": "封面",
                    "caption": "说明",
                    "source_frame_time": 0.5,
                    "source_frame_path": str(paths.frames_dir / "frame_0001.jpg"),
                    "layout": "toutiao_feed_card",
                    "style": "clean",
                    "output_path": str(card_path),
                    "image_prompt": "",
                }
            ],
            "card_count": 1,
        },
    )
    test_store.add_output(project_id, "toutiao_image_cards", paths.file_for_kind("toutiao_image_cards"))

    card = client.get(f"/api/projects/{project_id}/toutiao-cards/cover.png")
    cards_archive = client.get(f"/api/projects/{project_id}/download/toutiao-cards")

    assert card.status_code == 200
    assert cards_archive.status_code == 200
    zip_path = tmp_path / "toutiao-cards.zip"
    zip_path.write_bytes(cards_archive.content)
    with zipfile.ZipFile(zip_path) as zf:
        assert set(zf.namelist()) == {"toutiao-cards/cover.png"}


def test_delete_project_route_removes_completed_project(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)

    client = TestClient(app)
    response = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "use_ocr": False,
            "max_frames": 8,
        },
    )
    project_id = response.json()["project_id"]
    test_store.fail(project_id, {"code": "test_complete", "message": "ready to delete", "step": "completed"})

    deleted = client.delete(f"/api/projects/{project_id}")
    missing = client.get(f"/api/projects/{project_id}")

    assert deleted.status_code == 200
    assert deleted.json() == {"project_id": project_id, "deleted": True}
    assert missing.status_code == 404


def test_delete_project_route_rejects_running_project(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)

    client = TestClient(app)
    response = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = response.json()["project_id"]

    deleted = client.delete(f"/api/projects/{project_id}")

    assert deleted.status_code == 409
    assert deleted.json()["detail"]["code"] == "project_busy"


def test_project_status_exposes_downstream_rerun_readiness(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)

    client = TestClient(app)
    response = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = response.json()["project_id"]

    created_status = client.get(f"/api/projects/{project_id}/status")
    test_store.fail(project_id, {"code": "llm_unavailable", "message": "missing LLM", "step": "planning_content"})
    failed_missing_status = client.get(f"/api/projects/{project_id}/status")
    _write_registered_upstream(test_store, project_id)
    failed_ready_status = client.get(f"/api/projects/{project_id}/status")

    assert created_status.status_code == 200
    created_body = created_status.json()
    assert created_body["status_label"] == "任务已创建"
    assert created_body["status_description"] == "任务已进入队列，准备开始获取视频。"
    assert created_body["progress"]["mode"] == "analyze"
    assert created_body["progress"]["mode_label"] == "解析进度"
    assert created_body["progress"]["current_step_label"] == "任务已创建"
    assert created_body["progress"]["current_step_description"] == "任务已进入队列，准备开始获取视频。"
    assert created_body["progress"]["percent"] >= 1
    assert created_body["progress"]["remaining_seconds"] is not None
    assert "预计时间" in created_body["progress"]["estimate_note"]
    assert created_body["progress"]["steps"][0]["label"] == "任务已创建"
    assert created_body["can_rerun_downstream"] is False
    assert created_body["can_rerun_visuals"] is False
    assert created_body["can_generate_images"] is False
    assert created_body["downstream_rerun_missing_inputs"] == [
        "metadata",
        "transcript",
        "keyframes",
        "visual_analysis",
    ]
    assert created_body["visual_rerun_missing_inputs"] == ["metadata", "transcript", "keyframes"]
    assert failed_missing_status.status_code == 200
    assert failed_missing_status.json()["can_rerun_downstream"] is False
    assert failed_missing_status.json()["can_rerun_visuals"] is False
    assert failed_missing_status.json()["can_generate_images"] is False
    assert failed_missing_status.json()["downstream_rerun_missing_inputs"] == [
        "metadata",
        "transcript",
        "keyframes",
        "visual_analysis",
    ]
    assert failed_missing_status.json()["visual_rerun_missing_inputs"] == ["metadata", "transcript", "keyframes"]
    assert failed_ready_status.status_code == 200
    assert failed_ready_status.json()["can_rerun_downstream"] is True
    assert failed_ready_status.json()["can_rerun_visuals"] is True
    assert failed_ready_status.json()["can_generate_images"] is False
    assert failed_ready_status.json()["downstream_rerun_missing_inputs"] == []
    assert failed_ready_status.json()["visual_rerun_missing_inputs"] == []


def test_project_status_progress_reports_analysis_completed(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)

    client = TestClient(app)
    response = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = response.json()["project_id"]
    _write_registered_upstream(test_store, project_id)
    paths = test_store.paths(project_id)
    write_json(paths.analysis_dir / "content-assets.json", _valid_content_assets())
    test_store.add_output(project_id, "content_assets", paths.file_for_kind("content_assets"))
    test_store.set_status(project_id, ProjectStatus.analysis_completed, "ready")

    status = client.get(f"/api/projects/{project_id}/status")

    assert status.status_code == 200
    body = status.json()
    assert body["status_label"] == "解析完成"
    assert body["progress"]["mode"] == "analyze"
    assert body["progress"]["current_step"] == "analysis_completed"
    assert body["progress"]["current_step_label"] == "解析完成"
    assert body["progress"]["percent"] == 100
    assert body["progress"]["remaining_seconds"] == 0
    assert body["progress"]["eta_confidence"] == "complete"
    assert body["progress"]["completed_steps"] == body["progress"]["total_steps"]


def test_project_status_progress_reports_produce_mode(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)

    client = TestClient(app)
    response = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = response.json()["project_id"]
    _write_registered_upstream(test_store, project_id)
    paths = test_store.paths(project_id)
    write_json(paths.analysis_dir / "content-assets.json", _valid_content_assets())
    test_store.add_output(project_id, "content_assets", paths.file_for_kind("content_assets"))
    test_store.set_status(project_id, ProjectStatus.analysis_completed, "ready")
    started, _record, missing = test_store.try_start_produce(project_id)

    status = client.get(f"/api/projects/{project_id}/status")

    assert started is True
    assert missing == []
    assert status.status_code == 200
    body = status.json()
    assert body["status_label"] == "生成小红书稿"
    assert body["progress"]["mode"] == "produce"
    assert body["progress"]["mode_label"] == "小红书稿进度"
    assert body["progress"]["current_step"] == "producing_article"
    assert body["progress"]["current_step_label"] == "生成小红书稿"
    assert body["progress"]["percent"] >= 1
    assert body["progress"]["remaining_seconds"] is not None
    assert [step["label"] for step in body["progress"]["steps"]] == ["生成小红书稿", "小红书稿完成"]


def test_project_status_progress_reports_toutiao_produce_mode_immediately(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)

    client = TestClient(app)
    response = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = response.json()["project_id"]
    _write_registered_upstream(test_store, project_id)
    paths = test_store.paths(project_id)
    write_json(paths.analysis_dir / "content-assets.json", _valid_content_assets())
    test_store.add_output(project_id, "content_assets", paths.file_for_kind("content_assets"))
    test_store.set_status(project_id, ProjectStatus.analysis_completed, "ready")
    started, _record, missing = test_store.try_start_platform_produce(project_id, platform="toutiao")

    status = client.get(f"/api/projects/{project_id}/status")

    assert started is True
    assert missing == []
    assert status.status_code == 200
    body = status.json()
    assert body["status_label"] == "生成今日头条稿"
    assert body["progress"]["mode"] == "produce"
    assert body["progress"]["mode_label"] == "今日头条稿进度"
    assert body["progress"]["platform"] == "toutiao"
    assert body["progress"]["current_step"] == "producing_article"
    assert body["progress"]["current_step_label"] == "生成今日头条稿"
    assert [step["label"] for step in body["progress"]["steps"]] == ["生成今日头条稿", "今日头条稿完成"]
    assert body["progress"]["steps"][0]["output_kinds_expected"] == ["toutiao_post_json", "toutiao_image_prompts"]


def test_project_status_progress_reports_image_generation_mode(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)

    client = TestClient(app)
    response = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = response.json()["project_id"]
    _write_registered_upstream(test_store, project_id)
    paths = test_store.paths(project_id)
    write_json(paths.analysis_dir / "content-assets.json", _valid_content_assets())
    write_json(paths.analysis_dir / "xiaohongshu-post.json", {"content_type": "清单"})
    write_json(paths.analysis_dir / "image-prompts.json", {"image_prompts": []})
    for kind in ["content_assets", "xhs_post_json", "image_prompts"]:
        test_store.add_output(project_id, kind, paths.file_for_kind(kind))
    test_store.set_status(project_id, ProjectStatus.xhs_completed, "article ready")
    started, _record, missing = test_store.try_start_image_generation(project_id)

    status = client.get(f"/api/projects/{project_id}/status")

    assert started is True
    assert missing == []
    assert status.status_code == 200
    body = status.json()
    assert body["status_label"] == "渲染图文卡片"
    assert body["progress"]["mode"] == "image_generation"
    assert body["progress"]["mode_label"] == "生图进度"
    assert body["progress"]["current_step"] == "rendering_cards"
    assert [step["label"] for step in body["progress"]["steps"]] == ["渲染图文卡片", "图文完成"]


def test_project_verify_route_returns_runtime_verification(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)

    client = TestClient(app)
    response = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = response.json()["project_id"]
    paths = test_store.paths(project_id)
    _write_registered_upstream(test_store, project_id)
    write_json(
        paths.analysis_dir / "asset-package.json",
        {"status": "partial_failed", "error": {"code": "llm_unavailable"}},
    )
    test_store.add_output(project_id, "asset_package", paths.file_for_kind("asset_package"))
    test_store.fail(project_id, {"code": "llm_unavailable", "message": "missing LLM", "step": "planning_content"})

    verification = client.get(f"/api/projects/{project_id}/verify")
    required = client.get(f"/api/projects/{project_id}/verify?require_completed=true")

    assert verification.status_code == 200
    body = verification.json()
    assert body["partial_ok"] is True
    assert body["completed_ok"] is False
    assert body["missing"] == []
    assert body["issues"] == []
    assert required.status_code == 200
    assert required.json()["required_completed"] is True


def test_frames_download_404_when_no_frames(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)

    client = TestClient(app)
    response = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = response.json()["project_id"]

    frames_archive = client.get(f"/api/projects/{project_id}/download/frames")

    assert frames_archive.status_code == 404


def test_frame_routes_require_registered_keyframes_output(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)

    client = TestClient(app)
    response = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = response.json()["project_id"]
    paths = test_store.paths(project_id)
    paths.frames_dir.mkdir(parents=True, exist_ok=True)
    (paths.frames_dir / "frame_0001.jpg").write_bytes(b"\xff\xd8\xff\xd9")

    frames_archive = client.get(f"/api/projects/{project_id}/download/frames")
    frame_file = client.get(f"/api/projects/{project_id}/frames/frame_0001.jpg")

    assert frames_archive.status_code == 404
    assert frame_file.status_code == 404


def test_frame_file_route_requires_existing_project_record(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    orphan_paths = test_store.paths("orphan")
    orphan_paths.frames_dir.mkdir(parents=True)
    (orphan_paths.frames_dir / "frame_0001.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    client = TestClient(app)

    response = client.get("/api/projects/orphan/frames/frame_0001.jpg")

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"


def test_frame_file_route_rejects_unsafe_filename(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    client = TestClient(app)
    response = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = response.json()["project_id"]

    unsafe = client.get(f"/api/projects/{project_id}/frames/..frame.jpg")

    assert unsafe.status_code == 400


def test_frame_file_route_only_serves_standard_frame_names(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    client = TestClient(app)
    response = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = response.json()["project_id"]
    paths = test_store.paths(project_id)
    paths.frames_dir.mkdir(parents=True, exist_ok=True)
    (paths.frames_dir / "frame_bad.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    write_json(paths.analysis_dir / "keyframes.json", {"keyframes": []})
    test_store.add_output(project_id, "keyframes", paths.analysis_dir / "keyframes.json")

    nonstandard = client.get(f"/api/projects/{project_id}/frames/frame_bad.jpg")

    assert nonstandard.status_code == 404


def test_frame_routes_ignore_keyframe_paths_outside_project(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path / "runtime")
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    client = TestClient(app)
    response = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = response.json()["project_id"]
    paths = test_store.paths(project_id)
    external_frame = tmp_path / "frame_0001.jpg"
    external_frame.write_bytes(b"\xff\xd8\xff\xd9")
    write_json(
        paths.analysis_dir / "keyframes.json",
        {"keyframes": [{"time": 1.0, "path": str(external_frame), "score": 0.9, "reason": "outside"}]},
    )
    test_store.add_output(project_id, "keyframes", paths.analysis_dir / "keyframes.json")

    frames_archive = client.get(f"/api/projects/{project_id}/download/frames")
    frame_file = client.get(f"/api/projects/{project_id}/frames/frame_0001.jpg")

    assert frames_archive.status_code == 404
    assert frame_file.status_code == 404


def test_frame_routes_ignore_registered_frames_outside_frames_dir(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    client = TestClient(app)
    response = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = response.json()["project_id"]
    paths = test_store.paths(project_id)
    wrong_dir_frame = paths.source_dir / "frame_0001.jpg"
    wrong_dir_frame.write_bytes(b"\xff\xd8\xff\xd9")
    write_json(
        paths.analysis_dir / "keyframes.json",
        {"keyframes": [{"time": 1.0, "path": str(wrong_dir_frame), "score": 0.9, "reason": "wrong-dir"}]},
    )
    test_store.add_output(project_id, "keyframes", paths.analysis_dir / "keyframes.json")

    frames_archive = client.get(f"/api/projects/{project_id}/download/frames")
    frame_file = client.get(f"/api/projects/{project_id}/frames/frame_0001.jpg")

    assert frames_archive.status_code == 404
    assert frame_file.status_code == 404


def test_project_routes_return_404_for_invalid_project_id(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "store", ProjectStore(tmp_path))
    client = TestClient(app)

    for endpoint in [
        "/api/projects/bad%20id",
        "/api/projects/bad%20id/status",
        "/api/projects/bad%20id/verify",
        "/api/projects/bad%20id/files/metadata",
        "/api/projects/bad%20id/download",
        "/api/projects/bad%20id/download/frames",
        "/api/projects/bad%20id/frames/frame_0001.jpg",
    ]:
        response = client.get(endpoint)
        assert response.status_code == 404
        assert response.json()["detail"] == "Project not found"

    rerun = client.post("/api/projects/bad%20id/rerun/downstream")
    assert rerun.status_code == 404
    assert rerun.json()["detail"] == "Project not found"

    rerun_visuals = client.post("/api/projects/bad%20id/rerun/visuals")
    assert rerun_visuals.status_code == 404
    assert rerun_visuals.json()["detail"] == "Project not found"


def test_cancel_project_endpoint_marks_running_project_failed(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    client = TestClient(app)

    create = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = create.json()["project_id"]
    test_store.set_status(project_id, ProjectStatus.ingesting, "started")

    response = client.post(f"/api/projects/{project_id}/cancel")
    status = client.get(f"/api/projects/{project_id}/status")

    assert response.status_code == 200
    body = response.json()
    assert body["cancelled"] is True
    assert body["status"] == "failed"
    assert body["error"]["code"] == "user_stopped"
    assert status.json()["can_cancel"] is False
    assert test_store.get(project_id).status == ProjectStatus.failed


def test_cancel_project_endpoint_noops_when_not_running(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    client = TestClient(app)

    create = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = create.json()["project_id"]
    test_store.set_status(project_id, ProjectStatus.analysis_completed, "done")

    response = client.post(f"/api/projects/{project_id}/cancel")

    assert response.status_code == 200
    assert response.json()["cancelled"] is False
    assert response.json()["status"] == "analysis_completed"


def test_rerun_downstream_endpoint_queues_existing_project(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    called = []
    monkeypatch.setattr(routes, "run_project_downstream_pipeline", lambda project_id: called.append(project_id))

    client = TestClient(app)
    create = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = create.json()["project_id"]
    test_store.fail(project_id, {"code": "llm_unavailable", "message": "missing LLM", "step": "planning_content"})
    _write_registered_upstream(test_store, project_id)

    response = client.post(f"/api/projects/{project_id}/rerun/downstream")
    duplicate = client.post(f"/api/projects/{project_id}/rerun/downstream")

    assert response.status_code == 200
    assert response.json() == {"project_id": project_id, "status": "queued", "scope": "downstream"}
    assert called == [project_id]
    assert test_store.get(project_id).status == "planning_content"
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "project_busy"


def test_rerun_downstream_endpoint_409_when_upstream_artifacts_missing(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    monkeypatch.setattr(routes, "run_project_downstream_pipeline", lambda project_id: None)

    client = TestClient(app)
    create = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = create.json()["project_id"]
    test_store.fail(project_id, {"code": "llm_unavailable", "message": "missing LLM", "step": "planning_content"})

    response = client.post(f"/api/projects/{project_id}/rerun/downstream")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "resume_artifacts_missing"
    assert detail["status"] == "failed"
    assert detail["missing"] == ["metadata", "transcript", "keyframes", "visual_analysis"]


def test_rerun_downstream_endpoint_409_when_project_is_busy(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    monkeypatch.setattr(routes, "run_project_downstream_pipeline", lambda project_id: None)

    client = TestClient(app)
    create = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = create.json()["project_id"]

    response = client.post(f"/api/projects/{project_id}/rerun/downstream")

    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "created"


def test_rerun_downstream_endpoint_404_for_missing_project(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "store", ProjectStore(tmp_path))
    client = TestClient(app)

    response = client.post("/api/projects/missing/rerun/downstream")

    assert response.status_code == 404


def test_rerun_visuals_endpoint_queues_existing_project(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    called = []
    monkeypatch.setattr(routes, "run_project_visual_pipeline", lambda project_id: called.append(project_id))

    client = TestClient(app)
    create = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = create.json()["project_id"]
    test_store.fail(project_id, {"code": "llm_unavailable", "message": "missing LLM", "step": "planning_content"})
    _write_registered_upstream(test_store, project_id)

    response = client.post(f"/api/projects/{project_id}/rerun/visuals")
    duplicate = client.post(f"/api/projects/{project_id}/rerun/visuals")

    assert response.status_code == 200
    assert response.json() == {"project_id": project_id, "status": "queued", "scope": "visuals_and_downstream"}
    assert called == [project_id]
    assert test_store.get(project_id).status == "analyzing_visuals"
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "project_busy"


def test_rerun_visuals_endpoint_409_when_upstream_artifacts_missing(tmp_path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(routes, "store", test_store)
    monkeypatch.setattr(routes, "run_project_pipeline", lambda project_id: None)
    monkeypatch.setattr(routes, "run_project_visual_pipeline", lambda project_id: None)

    client = TestClient(app)
    create = client.post(
        "/api/projects",
        json={
            "url": "https://example.com/video",
            "language": "zh",
            "style": "干货",
            "use_whisper": True,
            "max_frames": 8,
        },
    )
    project_id = create.json()["project_id"]
    test_store.fail(project_id, {"code": "llm_unavailable", "message": "missing LLM", "step": "planning_content"})

    response = client.post(f"/api/projects/{project_id}/rerun/visuals")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "resume_artifacts_missing"
    assert detail["status"] == "failed"
    assert detail["missing"] == ["metadata", "transcript", "keyframes"]
