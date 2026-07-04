from pathlib import Path

from app.schemas.models import ProjectCreate
from app.services import content_planner, image_card_renderer, image_prompt_writer, pipeline, xhs_writer
from app.services.errors import PipelineError
from app.services.runtime_store import ProjectStore, read_json, write_json
from scripts.verify_project import verify_project


def _write_test_jpeg(path: Path) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 240), (180, 196, 210)).save(path, format="JPEG")


def _visual_frame(frame_path: Path, *, ocr_text: str = "", summary: str = "summary", provider: str = "none") -> dict:
    return {
        "time": 0.5,
        "path": str(frame_path),
        "ocr_text": ocr_text,
        "visual_summary": summary,
        "detected_objects": [],
        "screen_text_confidence": 0.91 if ocr_text else 0.0,
        "ocr_provider": provider,
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


def _write_resume_artifacts(test_store: ProjectStore, project_id: str) -> None:
    paths = test_store.paths(project_id)
    video_path = paths.source_dir / "source.mp4"
    frame_path = paths.frames_dir / "frame_0001.jpg"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"mp4")
    _write_test_jpeg(frame_path)
    write_json(
        paths.source_dir / "metadata.json",
        {
            "video_id": "v1",
            "url": "https://example.com/video",
            "title": "source",
            "author": "author",
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
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "text": "这是一段原始证据文本，需要被改写成适合小红书的表达。",
                    "source": "subtitle:subtitles.vtt",
                }
            ],
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
            "frames": [_visual_frame(frame_path)],
            "warnings": [],
        },
    )
    for kind in ["metadata", "transcript", "keyframes", "visual_analysis"]:
        test_store.add_output(project_id, kind, paths.file_for_kind(kind))


class FakeLLMClient:
    def json_chat(self, messages, step, temperature=0.2, **kwargs):
        if step == "planning_content":
            return {
                "one_sentence_summary": "一句话总结",
                "core_points": [
                    {
                        "point": "观点",
                        "why_it_matters": "原因",
                        "evidence": [
                            {
                                "type": "transcript",
                                "time": 0.5,
                                "text": "这是一段原始证据文本，需要被改写成适合小红书的表达。",
                            }
                        ],
                    }
                ],
                "golden_quotes": [{"quote": "改写金句", "time": 0.5, "rewrite_note": "已改写"}],
                "chapters": [{"title": "章节", "start": 0.0, "end": 1.0, "summary": "总结"}],
                "steps": [{"step": "步骤", "evidence_time": 0.5}],
                "audience": ["目标用户"],
                "pain_points": ["痛点"],
                "xiaohongshu_angles": ["角度"],
                "recommended_content_type": "清单",
                "source_evidence": [
                    {
                        "claim": "观点",
                        "source_type": "transcript",
                        "time": 0.5,
                        "source_text": "这是一段原始证据文本，需要被改写成适合小红书的表达。",
                    }
                ],
            }
        if step == "writing_xhs":
            content = messages[-1]["content"]
            if "image_plan" in content and "image_prompts" in content:
                return {
                    "image_prompts": [
                        {
                            "page": 1,
                            "role": "cover",
                            "caption": "封面",
                            "source_frame_time": 0.5,
                            "visual_reference": "参考关键帧",
                            "image_prompt": "原创小红书封面，构图为主体居中，主体清晰，背景干净，色调明亮，右侧留白放标题。",
                            "negative_prompt": "不要直接复刻截图，不要低清，不要杂乱文字。",
                        }
                    ]
                }
            return {
                "content_type": "清单",
                "target_audience": ["目标用户"],
                "titles": ["标题1", "标题2", "标题3", "标题4", "标题5"],
                "cover_text": "封面文案",
                "hook": "开头用自己的话提炼重点。",
                "body": "这条内容可以整理成三个行动提醒，先看证据，再做判断，最后形成自己的表达。",
                "image_plan": [
                    {
                        "page": 1,
                        "role": "cover",
                        "caption": "封面",
                        "source_frame_time": 0.5,
                        "content_point": "观点",
                    }
                ],
                "hashtags": ["#干货"],
                "publish_suggestion": "晚上发布",
            }
        raise AssertionError(f"Unexpected LLM step: {step}")


class FakeLLMClientWithLooseImagePrompt(FakeLLMClient):
    def json_chat(self, messages, step, temperature=0.2, **kwargs):
        if step == "writing_xhs" and "image_plan" in messages[-1]["content"] and "image_prompts" in messages[-1]["content"]:
            return {
                "image_prompts": [
                    {
                        "page": 1,
                        "role": "cover",
                        "caption": "封面",
                        "source_frame_time": 0.5,
                        "visual_reference": "参考关键帧",
                        "image_prompt": "原创小红书封面，画面左侧放信息卡片，背景干净，色调明亮，右侧放标题。",
                        "negative_prompt": "不要低清。",
                    }
                ]
            }
        return super().json_chat(messages, step, temperature=temperature, **kwargs)


class FakeLLMClientWithImagePromptFailure(FakeLLMClient):
    def json_chat(self, messages, step, temperature=0.2, **kwargs):
        if step == "writing_xhs" and "image_plan" in messages[-1]["content"] and "image_prompts" in messages[-1]["content"]:
            raise PipelineError(
                code="llm_request_failed",
                message="OpenAI-compatible chat completion request failed after retries.",
                step=step,
                details={"error": "simulated timeout"},
            )
        return super().json_chat(messages, step, temperature=temperature, **kwargs)


def _patch_fake_llm(monkeypatch) -> None:
    fake_llm = FakeLLMClient()
    monkeypatch.setattr(content_planner, "llm_client", fake_llm)
    monkeypatch.setattr(xhs_writer, "llm_client", fake_llm)
    monkeypatch.setattr(image_prompt_writer, "llm_client", fake_llm)


class LocalImageClient:
    enabled = False


def _patch_local_image_renderer(monkeypatch) -> None:
    monkeypatch.setattr(image_card_renderer, "image_client", LocalImageClient())


def test_downstream_rerun_fails_when_upstream_artifacts_missing(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(pipeline, "store", test_store)
    record = test_store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="干货",
            use_whisper=True,
            max_frames=8,
        )
    )

    pipeline.run_project_downstream_pipeline(record.project_id)

    updated = test_store.get(record.project_id)
    assert updated.status == "failed"
    assert updated.error["code"] == "resume_artifacts_missing"
    assert "metadata" in updated.error["details"]["missing"]


def test_downstream_rerun_clears_stale_downstream_outputs_on_failure(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(pipeline, "store", test_store)
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
    stale_files = {
        "content_assets": paths.analysis_dir / "content-assets.json",
        "xhs_post_json": paths.analysis_dir / "xiaohongshu-post.json",
        "xhs_post_md": paths.analysis_dir / "xhs-post.md",
        "image_prompts": paths.analysis_dir / "image-prompts.json",
        "asset_package": paths.analysis_dir / "asset-package.json",
    }
    for kind, path in stale_files.items():
        if path.suffix == ".md":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# stale", encoding="utf-8")
        else:
            write_json(path, {"stale": True})
        test_store.add_output(record.project_id, kind, path)

    pipeline.run_project_downstream_pipeline(record.project_id)

    updated = test_store.get(record.project_id)
    assert updated.status == "failed"
    assert updated.error["code"] == "resume_artifacts_missing"
    for kind in stale_files:
        if kind != "asset_package":
            assert kind not in updated.outputs
            assert not stale_files[kind].exists()
    assert updated.outputs["asset_package"] == "analysis/asset-package.json"
    partial_package = read_json(paths.analysis_dir / "asset-package.json")
    assert partial_package["status"] == "partial_failed"
    assert partial_package["content_assets"] is None
    assert partial_package["xiaohongshu_post"] is None


def test_downstream_rerun_uses_existing_artifacts(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(pipeline, "store", test_store)
    record = test_store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="清单",
            use_whisper=True,
            max_frames=8,
        )
    )
    paths = test_store.paths(record.project_id)
    _write_resume_artifacts(test_store, record.project_id)
    _patch_fake_llm(monkeypatch)
    _patch_local_image_renderer(monkeypatch)

    pipeline.run_project_downstream_pipeline(record.project_id)

    updated = test_store.get(record.project_id)
    assert updated.status == "completed"
    assert updated.outputs["content_assets"] == "analysis/content-assets.json"
    assert updated.outputs["xhs_post_md"] == "analysis/xhs-post.md"
    assert (paths.analysis_dir / "content-assets.json").exists()
    assert (paths.analysis_dir / "xiaohongshu-post.json").exists()
    assert (paths.analysis_dir / "image-prompts.json").exists()
    assert (paths.analysis_dir / "xhs-post.md").exists()
    assert read_json(paths.analysis_dir / "asset-package.json")["content_assets"]["one_sentence_summary"] == "一句话总结"
    assert verify_project(paths.project_dir)["completed_ok"] is True


def test_analysis_pipeline_uses_local_basic_assets_when_llm_fails(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(pipeline, "store", test_store)
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
    video_path.write_bytes(b"mp4")
    _write_test_jpeg(frame_path)

    def fake_ingest(url, language, paths, **kwargs):
        payload = {
            "video_id": "v1",
            "url": url,
            "title": "source",
            "author": "author",
            "duration": 12,
            "video_file": str(video_path),
            "available_subtitles": ["en"],
            "automatic_captions": [],
        }
        write_json(paths.source_dir / "metadata.json", payload)
        return payload

    def fake_transcript(metadata, language, use_whisper, paths):
        payload = {
            "source": "subtitle",
            "segment_count": 1,
            "segments": [{"start": 0.0, "end": 1.0, "text": "这是一段原始证据文本，需要被二次整理。", "source": "subtitle"}],
        }
        write_json(paths.transcript_dir / "transcript.json", payload)
        return payload

    def fake_keyframes(metadata, transcript, max_frames, paths):
        payload = {
            "frame_count": 1,
            "keyframes": [{"time": 0.5, "path": str(frame_path), "score": 0.9, "reason": "test"}],
        }
        write_json(paths.analysis_dir / "keyframes.json", payload)
        return payload

    def fake_visual(keyframes, language, paths, use_ocr=True):
        payload = {"frames": [_visual_frame(frame_path)], "warnings": []}
        write_json(paths.analysis_dir / "visual-analysis.json", payload)
        return payload

    monkeypatch.setattr(pipeline, "ingest_video", fake_ingest)
    monkeypatch.setattr(pipeline, "build_transcript", fake_transcript)
    monkeypatch.setattr(pipeline, "extract_keyframes", fake_keyframes)
    monkeypatch.setattr(pipeline, "analyze_visuals", fake_visual)
    monkeypatch.setattr(
        pipeline,
        "build_content_assets",
        lambda *args, **kwargs: (_ for _ in ()).throw(PipelineError("llm_unavailable", "missing LLM", "planning_content")),
    )

    pipeline.run_project_analysis_pipeline(record.project_id)

    updated = test_store.get(record.project_id)
    assert updated.status == "analysis_completed"
    assert updated.error is None
    assert updated.outputs["metadata"] == "source/metadata.json"
    assert updated.outputs["transcript"] == "transcript/transcript.json"
    assert updated.outputs["keyframes"] == "analysis/keyframes.json"
    assert updated.outputs["visual_analysis"] == "analysis/visual-analysis.json"
    assert updated.outputs["content_assets"] == "analysis/content-assets.json"
    assert "xhs_post_json" not in updated.outputs
    assert updated.warnings
    package = read_json(paths.analysis_dir / "asset-package.json")
    assert package["status"] == "analysis_completed"
    assets = read_json(paths.analysis_dir / "content-assets.json")
    assert assets["analysis_mode"] == "local_basic_fallback"
    assert "LLM" in assets["fallback_notice"]
    assert not (paths.analysis_dir / "xiaohongshu-post.json").exists()


def test_analysis_pipeline_continues_from_transcript_only_ingest(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(pipeline, "store", test_store)
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

    def fake_ingest(url, language, paths, **kwargs):
        subtitle = paths.source_dir / "subtitles.vtt"
        subtitle.write_text("WEBVTT\n\n00:00.000 --> 00:01.000\n真实字幕\n", encoding="utf-8")
        payload = {
            "video_id": "v1",
            "url": url,
            "title": "source",
            "author": "author",
            "duration": 12,
            "video_file": None,
            "subtitle_file": str(subtitle),
            "ingest_warnings": ["media 403; transcript-only"],
        }
        write_json(paths.source_dir / "metadata.json", payload)
        return payload

    _patch_fake_llm(monkeypatch)
    monkeypatch.setattr(pipeline, "ingest_video", fake_ingest)

    pipeline.run_project_analysis_pipeline(record.project_id)

    updated = test_store.get(record.project_id)
    assert updated.status == "analysis_completed"
    assert updated.outputs["metadata"] == "source/metadata.json"
    assert updated.outputs["transcript"] == "transcript/transcript.json"
    assert updated.outputs["keyframes"] == "analysis/keyframes.json"
    assert updated.outputs["visual_analysis"] == "analysis/visual-analysis.json"
    assert updated.outputs["content_assets"] == "analysis/content-assets.json"
    assert read_json(paths.analysis_dir / "keyframes.json")["skipped"] is True
    assert read_json(paths.analysis_dir / "visual-analysis.json")["skipped"] is True
    assert read_json(paths.analysis_dir / "content-assets.json")["one_sentence_summary"] == "一句话总结"
    assert "media 403; transcript-only" in updated.warnings


def test_analysis_pipeline_text_only_skips_keyframes_and_ocr(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(pipeline, "store", test_store)
    record = test_store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="干货",
            use_whisper=True,
            use_ocr=True,
            text_only=True,
            max_frames=8,
        )
    )
    paths = test_store.paths(record.project_id)

    def fake_ingest(url, language, paths, **kwargs):
        assert kwargs["prefer_subtitles_only"] is True
        video_path = paths.source_dir / "source.mp4"
        video_path.write_bytes(b"mp4")
        payload = {
            "video_id": "v1",
            "url": url,
            "title": "source",
            "author": "author",
            "duration": 12,
            "video_file": str(video_path),
            "available_subtitles": ["zh-Hans"],
            "automatic_captions": [],
        }
        write_json(paths.source_dir / "metadata.json", payload)
        return payload

    def fake_transcript(metadata, language, use_whisper, paths):
        payload = {
            "source": "subtitle",
            "segment_count": 1,
            "segments": [{"start": 0.0, "end": 1.0, "text": "真实字幕文案", "source": "subtitle"}],
        }
        write_json(paths.transcript_dir / "transcript.json", payload)
        return payload

    def fail_extract_keyframes(*args, **kwargs):
        raise AssertionError("extract_keyframes should not run in text-only mode")

    def fake_visual(keyframes, language, paths, use_ocr=True):
        assert use_ocr is False
        assert keyframes["skipped"] is True
        payload = {
            "ocr_provider": "none",
            "requested_ocr_provider": "none",
            "ocr_enabled": False,
            "warnings": [keyframes["skip_reason"]],
            "frames": [],
            "skipped": True,
            "skip_reason": keyframes["skip_reason"],
        }
        write_json(paths.analysis_dir / "visual-analysis.json", payload)
        return payload

    monkeypatch.setattr(pipeline, "ingest_video", fake_ingest)
    monkeypatch.setattr(pipeline, "build_transcript", fake_transcript)
    monkeypatch.setattr(pipeline, "extract_keyframes", fail_extract_keyframes)
    monkeypatch.setattr(pipeline, "analyze_visuals", fake_visual)
    _patch_fake_llm(monkeypatch)

    pipeline.run_project_analysis_pipeline(record.project_id)

    updated = test_store.get(record.project_id)
    assert updated.status == "analysis_completed"
    assert updated.text_only is True
    assert updated.outputs["keyframes"] == "analysis/keyframes.json"
    assert updated.outputs["visual_analysis"] == "analysis/visual-analysis.json"
    assert "image_cards" not in updated.outputs
    assert read_json(paths.analysis_dir / "keyframes.json")["analysis_mode"] == "text_only"
    assert read_json(paths.analysis_dir / "keyframes.json")["frame_count"] == 0
    assert read_json(paths.analysis_dir / "visual-analysis.json")["skipped"] is True
    assets = read_json(paths.analysis_dir / "content-assets.json")
    assert assets["analysis_mode"] == "text_only"
    assert assets["requested_outputs"] == ["article"]
    assert not list(paths.frames_dir.glob("*.jpg"))


def test_analysis_pipeline_does_not_fallback_for_contract_errors(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(pipeline, "store", test_store)
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
    video_path.write_bytes(b"mp4")
    _write_test_jpeg(frame_path)

    def fake_ingest(url, language, paths, **kwargs):
        payload = {
            "video_id": "v1",
            "url": url,
            "title": "source",
            "author": "author",
            "duration": 12,
            "video_file": str(video_path),
            "available_subtitles": ["en"],
            "automatic_captions": [],
            "ingest_warnings": ["download used fallback"],
        }
        write_json(paths.source_dir / "metadata.json", payload)
        return payload

    def fake_transcript(metadata, language, use_whisper, paths):
        payload = {
            "source": "subtitle",
            "segment_count": 1,
            "segments": [{"start": 0.0, "end": 1.0, "text": "这是一段原始证据文本，需要被二次整理。", "source": "subtitle"}],
        }
        write_json(paths.transcript_dir / "transcript.json", payload)
        return payload

    def fake_keyframes(metadata, transcript, max_frames, paths):
        payload = {
            "frame_count": 1,
            "keyframes": [{"time": 0.5, "path": str(frame_path), "score": 0.9, "reason": "test"}],
        }
        write_json(paths.analysis_dir / "keyframes.json", payload)
        return payload

    def fake_visual(keyframes, language, paths, use_ocr=True):
        payload = {"frames": [_visual_frame(frame_path)], "warnings": []}
        write_json(paths.analysis_dir / "visual-analysis.json", payload)
        return payload

    monkeypatch.setattr(pipeline, "ingest_video", fake_ingest)
    monkeypatch.setattr(pipeline, "build_transcript", fake_transcript)
    monkeypatch.setattr(pipeline, "extract_keyframes", fake_keyframes)
    monkeypatch.setattr(pipeline, "analyze_visuals", fake_visual)
    monkeypatch.setattr(
        pipeline,
        "build_content_assets",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            PipelineError("llm_contract_invalid", "LLM returned invalid content assets.", "planning_content")
        ),
    )

    pipeline.run_project_analysis_pipeline(record.project_id)

    updated = test_store.get(record.project_id)
    assert updated.status == "failed"
    assert updated.error["code"] == "llm_contract_invalid"
    assert "download used fallback" in updated.warnings
    assert "content_assets" not in updated.outputs
    assert not (paths.analysis_dir / "content-assets.json").exists()


def test_produce_pipeline_requires_llm_and_does_not_fake_outputs(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(pipeline, "store", test_store)
    record = test_store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="清单",
            use_whisper=True,
            max_frames=8,
        )
    )
    paths = test_store.paths(record.project_id)
    _write_resume_artifacts(test_store, record.project_id)
    write_json(
        paths.analysis_dir / "content-assets.json",
        {
            "one_sentence_summary": "一句话总结",
            "core_points": [
                {
                    "point": "观点",
                    "why_it_matters": "原因",
                    "evidence": [{"type": "transcript", "time": 0.5, "text": "这是一段原始证据文本，需要被改写成适合小红书的表达。"}],
                }
            ],
            "golden_quotes": [{"quote": "改写金句", "time": 0.5, "rewrite_note": "已改写"}],
            "chapters": [{"title": "章节", "start": 0.0, "end": 1.0, "summary": "总结"}],
            "steps": [{"step": "步骤", "evidence_time": 0.5}],
            "audience": ["目标用户"],
            "pain_points": ["痛点"],
            "xiaohongshu_angles": ["角度"],
            "recommended_content_type": "清单",
            "source_evidence": [
                {
                    "claim": "观点",
                    "source_type": "transcript",
                    "time": 0.5,
                    "source_text": "这是一段原始证据文本，需要被改写成适合小红书的表达。",
                }
            ],
        },
    )
    test_store.add_output(record.project_id, "content_assets", paths.file_for_kind("content_assets"))

    def fake_write_xhs(*args, **kwargs):
        raise PipelineError("llm_unavailable", "missing LLM", "producing_article")

    monkeypatch.setattr(pipeline, "write_xhs_post", fake_write_xhs)

    pipeline.run_project_produce_pipeline(record.project_id)

    updated = test_store.get(record.project_id)
    assert updated.status == "failed"
    assert updated.error["code"] == "llm_unavailable"
    assert not (paths.analysis_dir / "xiaohongshu-post.json").exists()
    assert not (paths.analysis_dir / "image-cards.json").exists()
    assert not list(paths.cards_dir.glob("*.png"))


def test_produce_pipeline_generates_post_markdown_and_image_prompts(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(pipeline, "store", test_store)
    record = test_store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="清单",
            use_whisper=True,
            max_frames=8,
        )
    )
    paths = test_store.paths(record.project_id)
    _write_resume_artifacts(test_store, record.project_id)
    _patch_fake_llm(monkeypatch)
    _patch_local_image_renderer(monkeypatch)

    write_json(
        paths.analysis_dir / "content-assets.json",
        {
            "one_sentence_summary": "一句话总结",
            "core_points": [
                {
                    "point": "观点",
                    "why_it_matters": "原因",
                    "evidence": [{"type": "transcript", "time": 0.5, "text": "这是一段原始证据文本，需要被改写成适合小红书的表达。"}],
                }
            ],
            "golden_quotes": [{"quote": "改写金句", "time": 0.5, "rewrite_note": "已改写"}],
            "chapters": [{"title": "章节", "start": 0.0, "end": 1.0, "summary": "总结"}],
            "steps": [{"step": "步骤", "evidence_time": 0.5}],
            "audience": ["目标用户"],
            "pain_points": ["痛点"],
            "xiaohongshu_angles": ["角度"],
            "recommended_content_type": "清单",
            "source_evidence": [
                {
                    "claim": "观点",
                    "source_type": "transcript",
                    "time": 0.5,
                    "source_text": "这是一段原始证据文本，需要被改写成适合小红书的表达。",
                }
            ],
        },
    )
    test_store.add_output(record.project_id, "content_assets", paths.file_for_kind("content_assets"))

    pipeline.run_project_produce_pipeline(record.project_id)

    updated = test_store.get(record.project_id)
    assert updated.status == "xhs_completed"
    assert updated.outputs["xhs_post_json"] == "analysis/xiaohongshu-post.json"
    assert updated.outputs["xhs_post_md"] == "analysis/xhs-post.md"
    assert updated.outputs["image_prompts"] == "analysis/image-prompts.json"
    assert updated.outputs["asset_package"] == "analysis/asset-package.json"
    assert "image_cards" not in updated.outputs
    assert not (paths.analysis_dir / "image-cards.json").exists()
    assert not (paths.cards_dir / "cover.png").exists()
    package = read_json(paths.analysis_dir / "asset-package.json")
    assert package["materials"]["card_paths"] == []
    assert verify_project(paths.project_dir)["completed_ok"] is False


def test_text_only_produce_pipeline_generates_article_without_image_prompts(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(pipeline, "store", test_store)
    record = test_store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="清单",
            use_whisper=True,
            use_ocr=True,
            text_only=True,
            max_frames=8,
        )
    )
    paths = test_store.paths(record.project_id)
    write_json(
        paths.source_dir / "metadata.json",
        {
            "video_id": "v1",
            "url": "https://example.com/video",
            "title": "source",
            "author": "author",
            "duration": 12,
            "video_file": None,
            "available_subtitles": ["zh-Hans"],
            "automatic_captions": [],
        },
    )
    write_json(
        paths.transcript_dir / "transcript.json",
        {
            "source": "subtitle",
            "segment_count": 1,
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "text": "这是一段原始证据文本，需要被改写成适合小红书的表达。",
                    "source": "subtitle:subtitles.vtt",
                }
            ],
        },
    )
    write_json(
        paths.analysis_dir / "keyframes.json",
        {
            "frame_count": 0,
            "keyframes": [],
            "skipped": True,
            "analysis_mode": "text_only",
            "skip_reason": "Text-only analysis mode enabled.",
        },
    )
    write_json(
        paths.analysis_dir / "visual-analysis.json",
        {
            "frames": [],
            "warnings": ["Text-only analysis mode enabled."],
            "skipped": True,
            "skip_reason": "Text-only analysis mode enabled.",
            "ocr_provider": "none",
        },
    )
    write_json(
        paths.analysis_dir / "content-assets.json",
        {
            "analysis_mode": "text_only",
            "one_sentence_summary": "一句话总结",
            "core_points": [
                {
                    "point": "观点",
                    "why_it_matters": "原因",
                    "evidence": [{"type": "transcript", "time": 0.5, "text": "这是一段原始证据文本，需要被改写成适合小红书的表达。"}],
                }
            ],
            "golden_quotes": [{"quote": "改写金句", "time": 0.5, "rewrite_note": "已改写"}],
            "chapters": [{"title": "章节", "start": 0.0, "end": 1.0, "summary": "总结"}],
            "steps": [{"step": "步骤", "evidence_time": 0.5}],
            "audience": ["目标用户"],
            "pain_points": ["痛点"],
            "xiaohongshu_angles": ["角度"],
            "recommended_content_type": "清单",
            "source_evidence": [
                {
                    "claim": "观点",
                    "source_type": "transcript",
                    "time": 0.5,
                    "source_text": "这是一段原始证据文本，需要被改写成适合小红书的表达。",
                }
            ],
        },
    )
    for kind in ["metadata", "transcript", "keyframes", "visual_analysis", "content_assets"]:
        test_store.add_output(record.project_id, kind, paths.file_for_kind(kind))
    _patch_fake_llm(monkeypatch)

    pipeline.run_project_produce_pipeline(record.project_id)

    updated = test_store.get(record.project_id)
    assert updated.status == "xhs_completed"
    assert updated.outputs["xhs_post_json"] == "analysis/xiaohongshu-post.json"
    assert updated.outputs["xhs_post_md"] == "analysis/xhs-post.md"
    assert "image_prompts" not in updated.outputs
    assert "image_cards" not in updated.outputs
    assert not (paths.analysis_dir / "image-prompts.json").exists()
    assert not (paths.analysis_dir / "image-cards.json").exists()
    post = read_json(paths.analysis_dir / "xiaohongshu-post.json")
    assert post["image_plan"][0]["source_frame_time"] is None
    assert post["image_plan"][0]["source_frame_path"] is None
    package = read_json(paths.analysis_dir / "asset-package.json")
    assert package["image_prompts"] == []
    assert package["image_cards"] == []


def test_toutiao_pipeline_generates_independent_post_prompts_and_cards(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(pipeline, "store", test_store)
    record = test_store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="清单",
            use_whisper=True,
            max_frames=8,
        )
    )
    paths = test_store.paths(record.project_id)
    _write_resume_artifacts(test_store, record.project_id)
    _patch_fake_llm(monkeypatch)
    _patch_local_image_renderer(monkeypatch)

    write_json(
        paths.analysis_dir / "content-assets.json",
        {
            "one_sentence_summary": "一句话总结",
            "core_points": [
                {
                    "point": "观点",
                    "why_it_matters": "原因",
                    "evidence": [{"type": "transcript", "time": 0.5, "text": "这是一段原始证据文本，需要被改写成适合小红书的表达。"}],
                }
            ],
            "golden_quotes": [{"quote": "改写金句", "time": 0.5, "rewrite_note": "已改写"}],
            "chapters": [{"title": "章节", "start": 0.0, "end": 1.0, "summary": "总结"}],
            "steps": [{"step": "步骤", "evidence_time": 0.5}],
            "audience": ["目标用户"],
            "pain_points": ["痛点"],
            "xiaohongshu_angles": ["角度"],
            "recommended_content_type": "清单",
            "source_evidence": [
                {
                    "claim": "观点",
                    "source_type": "transcript",
                    "time": 0.5,
                    "source_text": "这是一段原始证据文本，需要被改写成适合小红书的表达。",
                }
            ],
        },
    )
    test_store.add_output(record.project_id, "content_assets", paths.file_for_kind("content_assets"))

    pipeline.run_project_toutiao_produce_pipeline(record.project_id)

    produced = test_store.get(record.project_id)
    assert produced.status == "toutiao_completed"
    assert produced.outputs["toutiao_post_json"] == "analysis/toutiao-post.json"
    assert produced.outputs["toutiao_post_md"] == "analysis/toutiao-post.md"
    assert produced.outputs["toutiao_image_prompts"] == "analysis/toutiao-image-prompts.json"
    assert "xhs_post_json" not in produced.outputs
    assert not (paths.analysis_dir / "xiaohongshu-post.json").exists()

    pipeline.run_project_toutiao_image_generation_pipeline(record.project_id)

    completed = test_store.get(record.project_id)
    assert completed.status == "completed"
    assert completed.outputs["toutiao_image_cards"] == "analysis/toutiao-image-cards.json"
    assert "image_cards" not in completed.outputs
    assert (paths.toutiao_cards_dir / "cover.png").exists()
    assert not (paths.cards_dir / "cover.png").exists()
    package = read_json(paths.analysis_dir / "asset-package.json")
    assert package["materials"]["toutiao_card_paths"] == [str(paths.toutiao_cards_dir / "cover.png")]


def test_produce_pipeline_repairs_loose_llm_image_prompt_contract(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(pipeline, "store", test_store)
    record = test_store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="清单",
            use_whisper=True,
            max_frames=8,
        )
    )
    paths = test_store.paths(record.project_id)
    _write_resume_artifacts(test_store, record.project_id)
    fake_llm = FakeLLMClientWithLooseImagePrompt()
    monkeypatch.setattr(content_planner, "llm_client", fake_llm)
    monkeypatch.setattr(xhs_writer, "llm_client", fake_llm)
    monkeypatch.setattr(image_prompt_writer, "llm_client", fake_llm)

    write_json(
        paths.analysis_dir / "content-assets.json",
        {
            "one_sentence_summary": "一句话总结",
            "core_points": [
                {
                    "point": "观点",
                    "why_it_matters": "原因",
                    "evidence": [{"type": "transcript", "time": 0.5, "text": "这是一段原始证据文本，需要被改写成适合小红书的表达。"}],
                }
            ],
            "golden_quotes": [{"quote": "改写金句", "time": 0.5, "rewrite_note": "已改写"}],
            "chapters": [{"title": "章节", "start": 0.0, "end": 1.0, "summary": "总结"}],
            "steps": [{"step": "步骤", "evidence_time": 0.5}],
            "audience": ["目标用户"],
            "pain_points": ["痛点"],
            "xiaohongshu_angles": ["角度"],
            "recommended_content_type": "清单",
            "source_evidence": [
                {
                    "claim": "观点",
                    "source_type": "transcript",
                    "time": 0.5,
                    "source_text": "这是一段原始证据文本，需要被改写成适合小红书的表达。",
                }
            ],
        },
    )
    test_store.add_output(record.project_id, "content_assets", paths.file_for_kind("content_assets"))

    pipeline.run_project_produce_pipeline(record.project_id)

    updated = test_store.get(record.project_id)
    assert updated.status == "xhs_completed"
    prompts = read_json(paths.analysis_dir / "image-prompts.json")
    prompt_text = prompts["image_prompts"][0]["image_prompt"]
    for keyword in ["构图", "主体", "背景", "色调", "留白"]:
        assert keyword in prompt_text
    assert "直接复刻" not in prompt_text
    assert "复刻" in prompts["image_prompts"][0]["negative_prompt"]


def test_produce_pipeline_falls_back_when_image_prompt_llm_fails(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(pipeline, "store", test_store)
    record = test_store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="清单",
            use_whisper=True,
            max_frames=8,
        )
    )
    paths = test_store.paths(record.project_id)
    _write_resume_artifacts(test_store, record.project_id)
    fake_llm = FakeLLMClientWithImagePromptFailure()
    monkeypatch.setattr(content_planner, "llm_client", fake_llm)
    monkeypatch.setattr(xhs_writer, "llm_client", fake_llm)
    monkeypatch.setattr(image_prompt_writer, "llm_client", fake_llm)

    write_json(
        paths.analysis_dir / "content-assets.json",
        {
            "one_sentence_summary": "一句话总结",
            "core_points": [
                {
                    "point": "观点",
                    "why_it_matters": "原因",
                    "evidence": [{"type": "transcript", "time": 0.5, "text": "这是一段原始证据文本，需要被改写成适合小红书的表达。"}],
                }
            ],
            "golden_quotes": [{"quote": "改写金句", "time": 0.5, "rewrite_note": "已改写"}],
            "chapters": [{"title": "章节", "start": 0.0, "end": 1.0, "summary": "总结"}],
            "steps": [{"step": "步骤", "evidence_time": 0.5}],
            "audience": ["目标用户"],
            "pain_points": ["痛点"],
            "xiaohongshu_angles": ["角度"],
            "recommended_content_type": "清单",
            "source_evidence": [
                {
                    "claim": "观点",
                    "source_type": "transcript",
                    "time": 0.5,
                    "source_text": "这是一段原始证据文本，需要被改写成适合小红书的表达。",
                }
            ],
        },
    )
    test_store.add_output(record.project_id, "content_assets", paths.file_for_kind("content_assets"))

    pipeline.run_project_produce_pipeline(record.project_id)

    updated = test_store.get(record.project_id)
    assert updated.status == "xhs_completed"
    prompts = read_json(paths.analysis_dir / "image-prompts.json")
    assert prompts["image_prompts"]
    prompt_text = prompts["image_prompts"][0]["image_prompt"]
    for keyword in ["构图", "主体", "背景", "色调", "留白"]:
        assert keyword in prompt_text


def test_image_generation_pipeline_generates_png_cards_from_xhs_artifacts(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(pipeline, "store", test_store)
    record = test_store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="清单",
            use_whisper=True,
            max_frames=8,
        )
    )
    paths = test_store.paths(record.project_id)
    _write_resume_artifacts(test_store, record.project_id)
    _patch_fake_llm(monkeypatch)

    write_json(
        paths.analysis_dir / "content-assets.json",
        {
            "one_sentence_summary": "一句话总结",
            "core_points": [
                {
                    "point": "观点",
                    "why_it_matters": "原因",
                    "evidence": [{"type": "transcript", "time": 0.5, "text": "这是一段原始证据文本，需要被改写成适合小红书的表达。"}],
                }
            ],
            "golden_quotes": [{"quote": "改写金句", "time": 0.5, "rewrite_note": "已改写"}],
            "chapters": [{"title": "章节", "start": 0.0, "end": 1.0, "summary": "总结"}],
            "steps": [{"step": "步骤", "evidence_time": 0.5}],
            "audience": ["目标用户"],
            "pain_points": ["痛点"],
            "xiaohongshu_angles": ["角度"],
            "recommended_content_type": "清单",
            "source_evidence": [
                {
                    "claim": "观点",
                    "source_type": "transcript",
                    "time": 0.5,
                    "source_text": "这是一段原始证据文本，需要被改写成适合小红书的表达。",
                }
            ],
        },
    )
    test_store.add_output(record.project_id, "content_assets", paths.file_for_kind("content_assets"))
    pipeline.run_project_produce_pipeline(record.project_id)

    _patch_local_image_renderer(monkeypatch)
    pipeline.run_project_image_generation_pipeline(record.project_id)

    updated = test_store.get(record.project_id)
    assert updated.status == "completed"
    assert updated.outputs["image_cards"] == "analysis/image-cards.json"
    image_cards = read_json(paths.analysis_dir / "image-cards.json")
    assert image_cards["card_count"] == 1
    assert (paths.cards_dir / "cover.png").exists()
    assert read_json(paths.analysis_dir / "asset-package.json")["materials"]["card_paths"] == [str(paths.cards_dir / "cover.png")]
    assert verify_project(paths.project_dir)["completed_ok"] is True


def test_image_generation_falls_back_to_local_cards_when_external_image_api_fails(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(pipeline, "store", test_store)
    record = test_store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="清单",
            use_whisper=True,
            max_frames=8,
        )
    )
    paths = test_store.paths(record.project_id)
    _write_resume_artifacts(test_store, record.project_id)
    _patch_fake_llm(monkeypatch)

    write_json(
        paths.analysis_dir / "content-assets.json",
        {
            "one_sentence_summary": "一句话总结",
            "core_points": [
                {
                    "point": "观点",
                    "why_it_matters": "原因",
                    "evidence": [{"type": "transcript", "time": 0.5, "text": "这是一段原始证据文本，需要被改写成适合小红书的表达。"}],
                }
            ],
            "golden_quotes": [{"quote": "改写金句", "time": 0.5, "rewrite_note": "已改写"}],
            "chapters": [{"title": "章节", "start": 0.0, "end": 1.0, "summary": "总结"}],
            "steps": [{"step": "步骤", "evidence_time": 0.5}],
            "audience": ["目标用户"],
            "pain_points": ["痛点"],
            "xiaohongshu_angles": ["角度"],
            "recommended_content_type": "清单",
            "source_evidence": [
                {
                    "claim": "观点",
                    "source_type": "transcript",
                    "time": 0.5,
                    "source_text": "这是一段原始证据文本，需要被改写成适合小红书的表达。",
                }
            ],
        },
    )
    test_store.add_output(record.project_id, "content_assets", paths.file_for_kind("content_assets"))
    pipeline.run_project_produce_pipeline(record.project_id)

    class FailingImageClient:
        enabled = True

        def generate_to_file(self, prompt, output_path, **kwargs):
            raise PipelineError(
                code="image_api_request_failed",
                message="OpenAI-compatible image generation request failed after retries.",
                step="rendering_cards",
                details={"error": "simulated failure"},
            )

    monkeypatch.setattr(image_card_renderer, "image_client", FailingImageClient())
    pipeline.run_project_image_generation_pipeline(record.project_id)

    updated = test_store.get(record.project_id)
    assert updated.status == "completed"
    image_cards = read_json(paths.analysis_dir / "image-cards.json")
    assert image_cards["renderer"] == "pillow_template_v1_with_external_image_fallback"
    assert image_cards["cards"][0]["image_generation"]["fallback"] is True
    assert image_cards["cards"][0]["image_generation"]["error"]["code"] == "image_api_request_failed"
    assert (paths.cards_dir / "cover.png").exists()
    assert verify_project(paths.project_dir)["completed_ok"] is True


def test_visual_rerun_refreshes_visuals_and_downstream_outputs(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(pipeline, "store", test_store)
    record = test_store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="清单",
            use_whisper=True,
            max_frames=8,
        )
    )
    paths = test_store.paths(record.project_id)
    _write_resume_artifacts(test_store, record.project_id)
    store_record = test_store.fail(record.project_id, {"code": "llm_unavailable", "message": "old", "step": "planning_content"})
    assert store_record.status == "failed"
    test_store.add_warning(record.project_id, "old OCR warning")
    write_json(paths.analysis_dir / "content-assets.json", {"stale": True})
    test_store.add_output(record.project_id, "content_assets", paths.analysis_dir / "content-assets.json")

    def fake_analyze_visuals(keyframes, language, paths, use_ocr=True):
        payload = {
            "ocr_provider": "fake",
            "warnings": ["fresh OCR warning"],
            "frames": [
                _visual_frame(
                    Path(keyframes["keyframes"][0]["path"]),
                    ocr_text="新识别文字",
                    summary="fresh visual summary",
                    provider="fake",
                )
            ],
        }
        write_json(paths.analysis_dir / "visual-analysis.json", payload)
        return payload

    monkeypatch.setattr(pipeline, "analyze_visuals", fake_analyze_visuals)
    _patch_fake_llm(monkeypatch)
    _patch_local_image_renderer(monkeypatch)

    pipeline.run_project_visual_pipeline(record.project_id)

    updated = test_store.get(record.project_id)
    assert updated.status == "completed"
    assert updated.warnings == ["fresh OCR warning"]
    assert updated.outputs["visual_analysis"] == "analysis/visual-analysis.json"
    assert updated.outputs["content_assets"] == "analysis/content-assets.json"
    visual = read_json(paths.analysis_dir / "visual-analysis.json")
    assert visual["ocr_provider"] == "fake"
    assert visual["frames"][0]["ocr_text"] == "新识别文字"
    assert read_json(paths.analysis_dir / "content-assets.json")["one_sentence_summary"] == "一句话总结"
    assert verify_project(paths.project_dir)["completed_ok"] is True


def test_visual_rerun_keeps_refreshed_visuals_when_downstream_fails(tmp_path: Path, monkeypatch):
    test_store = ProjectStore(tmp_path)
    monkeypatch.setattr(pipeline, "store", test_store)
    record = test_store.create(
        ProjectCreate(
            url="https://example.com/video",
            language="zh",
            style="清单",
            use_whisper=True,
            max_frames=8,
        )
    )
    paths = test_store.paths(record.project_id)
    _write_resume_artifacts(test_store, record.project_id)

    def fake_analyze_visuals(keyframes, language, paths, use_ocr=True):
        payload = {
            "ocr_provider": "fake",
            "warnings": [],
            "frames": [
                _visual_frame(
                    Path(keyframes["keyframes"][0]["path"]),
                    ocr_text="新识别文字",
                    summary="fresh visual summary",
                    provider="fake",
                )
            ],
        }
        write_json(paths.analysis_dir / "visual-analysis.json", payload)
        return payload

    def fail_content_assets(*args, **kwargs):
        raise PipelineError("llm_unavailable", "missing LLM", "planning_content")

    monkeypatch.setattr(pipeline, "analyze_visuals", fake_analyze_visuals)
    monkeypatch.setattr(pipeline, "build_content_assets", fail_content_assets)

    pipeline.run_project_visual_pipeline(record.project_id)

    updated = test_store.get(record.project_id)
    assert updated.status == "failed"
    assert updated.error["code"] == "llm_unavailable"
    assert updated.outputs["visual_analysis"] == "analysis/visual-analysis.json"
    assert updated.outputs["asset_package"] == "analysis/asset-package.json"
    assert "content_assets" not in updated.outputs
    assert read_json(paths.analysis_dir / "visual-analysis.json")["ocr_provider"] == "fake"
    assert read_json(paths.analysis_dir / "asset-package.json")["status"] == "partial_failed"
