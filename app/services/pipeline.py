from typing import Any, Dict

from app.schemas.models import ProjectStatus
from app.services.content_planner import build_basic_content_assets, build_content_assets
from app.services.errors import PipelineError
from app.services.frame_extractor import extract_keyframes, write_skipped_keyframes
from app.services.image_card_renderer import render_image_cards
from app.services.image_prompt_writer import write_image_prompts
from app.services.ingest import ingest_video
from app.services.platforms import get_platform, platform_values
from app.services.report_writer import write_analysis_asset_package, write_partial_asset_package, write_reports
from app.services.runtime_store import FILE_KIND_TO_PATH, read_json, store, write_json
from app.services.transcript import build_transcript
from app.services.visual_analyzer import analyze_visuals
from app.services.xhs_writer import write_platform_post

DOWNSTREAM_OUTPUT_KINDS = [
    "content_assets",
    *[kind for adapter in platform_values() for kind in adapter.output_kinds()],
    "asset_package",
]
PRODUCE_OUTPUT_KINDS = [*get_platform("xhs").output_kinds(), "asset_package"]
IMAGE_GENERATION_OUTPUT_KINDS = ["image_cards", "asset_package"]
TOUTIAO_PRODUCE_OUTPUT_KINDS = [
    *get_platform("toutiao").output_kinds(),
    "asset_package",
]
TOUTIAO_IMAGE_GENERATION_OUTPUT_KINDS = ["toutiao_image_cards", "asset_package"]
VISUAL_AND_DOWNSTREAM_OUTPUT_KINDS = ["visual_analysis", *DOWNSTREAM_OUTPUT_KINDS]
ANALYZE_LLM_FALLBACK_CODES = {
    "llm_unavailable",
    "missing_dependency",
    "llm_authentication_failed",
    "llm_rate_limited",
    "llm_timeout",
    "llm_network_error",
    "llm_http_error",
    "llm_response_invalid",
    "llm_request_failed",
    "llm_json_parse_failed",
}
TEXT_ONLY_SKIP_REASON = (
    "Text-only analysis mode enabled; skipped keyframe extraction, OCR, screenshots, and image-card generation."
)


def _text_only_prompt_placeholder() -> Dict[str, Any]:
    return {
        "image_prompts": [],
        "skipped": True,
        "skip_reason": "Text-only project; image prompt generation is disabled.",
    }


def _tag_analysis_assets(
    record: Any,
    assets: Dict[str, Any],
    keyframes: Dict[str, Any],
    paths: Any,
) -> Dict[str, Any]:
    tagged = dict(assets)
    tagged["target_platform"] = record.target_platform
    tagged["source_analysis_mode"] = keyframes.get("analysis_mode") or "full"
    if getattr(record, "text_only", False):
        tagged["analysis_mode"] = "text_only"
        tagged["requested_outputs"] = ["article"]
        tagged["skipped_outputs"] = ["keyframes", "ocr", "screenshots", "image_prompts", "image_cards"]
    write_json(paths.analysis_dir / "content-assets.json", tagged)
    return tagged


def _build_keyframes_for_record(project_id: str, record: Any, metadata: Dict[str, Any], transcript: Dict[str, Any], paths: Any) -> Dict[str, Any]:
    if getattr(record, "text_only", False):
        store.set_status(project_id, ProjectStatus.extracting_frames, "Text-only mode: skipping keyframe extraction.")
        keyframes = write_skipped_keyframes(
            metadata,
            transcript,
            record.max_frames,
            paths,
            reason=TEXT_ONLY_SKIP_REASON,
        )
        store.add_output(project_id, "keyframes", paths.analysis_dir / "keyframes.json")
        store.log(
            project_id,
            ProjectStatus.extracting_frames,
            "Text-only mode skipped keyframe extraction.",
            {"frames": 0, "text_only": True},
        )
        return keyframes

    store.set_status(project_id, ProjectStatus.extracting_frames, "Detecting scenes and extracting keyframes.")
    keyframes = extract_keyframes(metadata, transcript, record.max_frames, paths)
    store.add_output(project_id, "keyframes", paths.analysis_dir / "keyframes.json")
    store.log(project_id, ProjectStatus.extracting_frames, "Keyframes completed.", {"frames": keyframes.get("frame_count")})
    return keyframes


def _analyze_visuals_for_record(project_id: str, record: Any, keyframes: Dict[str, Any], paths: Any) -> Dict[str, Any]:
    if getattr(record, "text_only", False):
        store.set_status(project_id, ProjectStatus.analyzing_visuals, "Text-only mode: skipping OCR and visual analysis.")
        visual = analyze_visuals(keyframes, record.language, paths, use_ocr=False)
        for warning in visual.get("warnings", []):
            store.add_warning(project_id, warning)
        store.add_output(project_id, "visual_analysis", paths.analysis_dir / "visual-analysis.json")
        store.log(
            project_id,
            ProjectStatus.analyzing_visuals,
            "Text-only mode skipped OCR and visual analysis.",
            {"ocr_provider": visual.get("ocr_provider"), "warnings": len(visual.get("warnings", [])), "text_only": True},
        )
        return visual

    store.set_status(project_id, ProjectStatus.analyzing_visuals, "Running OCR and visual analysis providers.")
    visual = analyze_visuals(keyframes, record.language, paths, use_ocr=record.use_ocr)
    for warning in visual.get("warnings", []):
        store.add_warning(project_id, warning)
    store.add_output(project_id, "visual_analysis", paths.analysis_dir / "visual-analysis.json")
    store.log(
        project_id,
        ProjectStatus.analyzing_visuals,
        "Visual analysis completed.",
        {"ocr_provider": visual.get("ocr_provider"), "warnings": len(visual.get("warnings", []))},
    )
    return visual


def _record_ingest_warnings(project_id: str, metadata: Dict[str, Any]) -> None:
    for warning in metadata.get("ingest_warnings", []) or []:
        if warning:
            store.add_warning(project_id, str(warning))


def _can_use_basic_analysis_fallback(error: PipelineError) -> bool:
    return error.step == "planning_content" and error.code in ANALYZE_LLM_FALLBACK_CODES


def _pipeline_cancelled(project_id: str) -> bool:
    return store.cancel_requested(project_id)


def _register_standard_outputs(project_id: str) -> None:
    paths = store.paths(project_id)
    output_paths = {kind: paths.project_dir / relative for kind, relative in FILE_KIND_TO_PATH.items()}
    for kind, path in output_paths.items():
        if path.exists():
            store.add_output(project_id, kind, path)


def _clear_downstream_outputs(project_id: str) -> None:
    paths = store.paths(project_id)
    stale_files = [paths.project_dir / FILE_KIND_TO_PATH[kind] for kind in DOWNSTREAM_OUTPUT_KINDS if kind in FILE_KIND_TO_PATH]
    for path in stale_files:
        path.unlink(missing_ok=True)
    store.clear_outputs(project_id, DOWNSTREAM_OUTPUT_KINDS)


def _clear_produce_outputs(project_id: str) -> None:
    _clear_platform_produce_outputs(project_id, "xhs")


def _clear_toutiao_produce_outputs(project_id: str) -> None:
    _clear_platform_produce_outputs(project_id, "toutiao")


def _clear_platform_produce_outputs(project_id: str, platform: str) -> None:
    paths = store.paths(project_id)
    adapter = get_platform(platform)
    kinds = [*adapter.output_kinds(), "asset_package"]
    stale_files = [paths.project_dir / FILE_KIND_TO_PATH[kind] for kind in kinds]
    for path in stale_files:
        path.unlink(missing_ok=True)
    cards_dir = paths.toutiao_cards_dir if platform == "toutiao" else paths.cards_dir
    if adapter.supports_images and cards_dir.exists():
        for path in cards_dir.glob("*.png"):
            path.unlink(missing_ok=True)
    store.clear_outputs(project_id, kinds)


def _clear_image_generation_outputs(project_id: str) -> None:
    paths = store.paths(project_id)
    stale_files = [
        paths.analysis_dir / "image-cards.json",
        paths.analysis_dir / "asset-package.json",
    ]
    for path in stale_files:
        path.unlink(missing_ok=True)
    if paths.cards_dir.exists():
        for path in paths.cards_dir.glob("*.png"):
            path.unlink(missing_ok=True)
    store.clear_outputs(project_id, IMAGE_GENERATION_OUTPUT_KINDS)


def _clear_toutiao_image_generation_outputs(project_id: str) -> None:
    paths = store.paths(project_id)
    stale_files = [
        paths.analysis_dir / "toutiao-image-cards.json",
        paths.analysis_dir / "asset-package.json",
    ]
    for path in stale_files:
        path.unlink(missing_ok=True)
    if paths.toutiao_cards_dir.exists():
        for path in paths.toutiao_cards_dir.glob("*.png"):
            path.unlink(missing_ok=True)
    store.clear_outputs(project_id, TOUTIAO_IMAGE_GENERATION_OUTPUT_KINDS)


def _clear_visual_and_downstream_outputs(project_id: str) -> None:
    paths = store.paths(project_id)
    (paths.analysis_dir / "visual-analysis.json").unlink(missing_ok=True)
    store.clear_outputs(project_id, ["visual_analysis"])
    _clear_downstream_outputs(project_id)
    for cards_dir in (paths.cards_dir, paths.toutiao_cards_dir):
        for path in cards_dir.glob("*.png"):
            path.unlink(missing_ok=True)


def _write_platform_outputs(
    project_id: str,
    record: Any,
    metadata: Dict[str, Any],
    transcript: Dict[str, Any],
    keyframes: Dict[str, Any],
    visual: Dict[str, Any],
    assets: Dict[str, Any],
    paths: Any,
    *,
    render_images: bool,
    source_label: str,
) -> None:
    adapter = get_platform(record.target_platform)
    store.set_status(
        project_id,
        ProjectStatus.producing_article,
        f"Generating {adapter.name} article {source_label}.",
        {"platform": adapter.key},
    )

    def on_validation(report: Dict[str, Any]) -> None:
        store.set_status(
            project_id,
            ProjectStatus.validating_content,
            f"Validating {adapter.name} article structure, grounding, and rewrite degree.",
            {
                "platform": adapter.key,
                "rewrite_count": report.get("rewrite_count", 0),
                "violations": [item.get("code") for item in report.get("violations", [])],
            },
        )

    post = write_platform_post(
        metadata,
        assets,
        keyframes,
        visual,
        record.style,
        paths,
        platform=adapter.key,
        transcript_payload=transcript,
        on_validation=on_validation,
    )
    store.add_output(project_id, adapter.post_json_kind, paths.analysis_dir / adapter.post_filename)
    store.add_output(project_id, adapter.quality_kind, paths.analysis_dir / adapter.quality_filename)

    images_allowed = not record.text_only and adapter.supports_images
    if images_allowed:
        prompts = write_image_prompts(post, keyframes, visual, paths, platform=adapter.key)
        store.add_output(project_id, adapter.image_prompts_kind, paths.analysis_dir / adapter.image_prompts_filename)
    else:
        prompts = _text_only_prompt_placeholder()

    image_cards: Dict[str, Any] = {}
    if render_images and images_allowed:
        store.set_status(
            project_id,
            ProjectStatus.rendering_cards,
            f"Rendering {adapter.name} image cards.",
            {"platform": adapter.key},
        )
        renderer_kwargs: Dict[str, Any] = {}
        if adapter.key == "toutiao":
            renderer_kwargs = {
                "platform": adapter.key,
                "output_filename": adapter.image_cards_filename,
                "cards_dir": paths.toutiao_cards_dir,
            }
        image_cards = render_image_cards(
            metadata,
            assets,
            post,
            keyframes,
            prompts,
            paths,
            **renderer_kwargs,
        )
        store.add_output(project_id, adapter.image_cards_kind, paths.analysis_dir / adapter.image_cards_filename)

    write_reports(
        metadata,
        transcript,
        keyframes,
        visual,
        assets,
        post,
        prompts,
        paths,
        store.get(project_id).warnings,
        image_cards=image_cards,
        platform=adapter.key,
    )
    _register_standard_outputs(project_id)
    if render_images and images_allowed:
        store.set_status(
            project_id,
            ProjectStatus.completed,
            f"{adapter.name} article and image cards completed.",
            {"platform": adapter.key},
        )
    else:
        image_note = " Image generation is disabled." if not images_allowed else " Image generation can be run next."
        store.set_status(
            project_id,
            adapter.completed_status,
            f"{adapter.name} article completed.{image_note}",
            {"platform": adapter.key},
        )


def run_project_pipeline(project_id: str) -> None:
    record = store.get(project_id)
    paths = store.paths(project_id)
    try:
        store.set_status(project_id, ProjectStatus.ingesting, "Fetching video metadata, media, subtitles, and thumbnail.")
        metadata = ingest_video(record.url, record.language, paths, prefer_subtitles_only=record.text_only)
        _record_ingest_warnings(project_id, metadata)
        store.add_output(project_id, "metadata", paths.source_dir / "metadata.json")
        store.log(project_id, ProjectStatus.ingesting, "Ingest completed.", {"title": metadata.get("title")})

        store.set_status(project_id, ProjectStatus.transcribing, "Building normalized transcript timeline.")
        transcript = build_transcript(metadata, record.language, record.use_whisper, paths)
        write_json(paths.source_dir / "metadata.json", metadata)
        store.add_output(project_id, "metadata", paths.source_dir / "metadata.json")
        store.add_output(project_id, "transcript", paths.transcript_dir / "transcript.json")
        store.log(
            project_id,
            ProjectStatus.transcribing,
            "Transcript completed.",
            {"segments": transcript.get("segment_count"), "source": transcript.get("source")},
        )

        keyframes = _build_keyframes_for_record(project_id, record, metadata, transcript, paths)
        visual = _analyze_visuals_for_record(project_id, record, keyframes, paths)

        store.set_status(project_id, ProjectStatus.planning_content, "Generating structured content assets with LLM.")
        assets = build_content_assets(metadata, transcript, keyframes, visual, record.language, record.style, paths)
        assets = _tag_analysis_assets(record, assets, keyframes, paths)
        store.add_output(project_id, "content_assets", paths.analysis_dir / "content-assets.json")
        store.log(project_id, ProjectStatus.planning_content, "Content assets completed.")
        _write_platform_outputs(
            project_id,
            record,
            metadata,
            transcript,
            keyframes,
            visual,
            assets,
            paths,
            render_images=True,
            source_label="from the completed analysis",
        )
    except PipelineError as exc:
        if _pipeline_cancelled(project_id):
            return
        write_partial_asset_package(paths, exc.to_dict(), store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, exc.to_dict())
    except Exception as exc:
        error: Dict[str, Any] = {
            "code": "unexpected_error",
            "message": str(exc),
            "step": store.get(project_id).status,
            "details": {"type": type(exc).__name__},
        }
        if _pipeline_cancelled(project_id):
            return
        write_partial_asset_package(paths, error, store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, error)


def run_project_downstream_pipeline(project_id: str) -> None:
    record = store.get(project_id)
    paths = store.paths(project_id)
    try:
        _clear_downstream_outputs(project_id)
        required_files = {
            "metadata": paths.source_dir / "metadata.json",
            "transcript": paths.transcript_dir / "transcript.json",
            "keyframes": paths.analysis_dir / "keyframes.json",
            "visual_analysis": paths.analysis_dir / "visual-analysis.json",
        }
        missing = [name for name, path in required_files.items() if not path.exists()]
        if missing:
            raise PipelineError(
                code="resume_artifacts_missing",
                message="Cannot rerun downstream generation because required upstream artifacts are missing.",
                step="planning_content",
                details={"missing": missing},
            )

        metadata = read_json(required_files["metadata"])
        transcript = read_json(required_files["transcript"])
        keyframes = read_json(required_files["keyframes"])
        visual = read_json(required_files["visual_analysis"])
        _register_standard_outputs(project_id)

        store.set_status(project_id, ProjectStatus.planning_content, "Rerunning content planning from existing artifacts.")
        assets = build_content_assets(metadata, transcript, keyframes, visual, record.language, record.style, paths)
        assets = _tag_analysis_assets(record, assets, keyframes, paths)
        store.add_output(project_id, "content_assets", paths.analysis_dir / "content-assets.json")
        store.log(project_id, ProjectStatus.planning_content, "Content assets completed from existing artifacts.")
        _write_platform_outputs(
            project_id,
            record,
            metadata,
            transcript,
            keyframes,
            visual,
            assets,
            paths,
            render_images=True,
            source_label="from the downstream rerun",
        )
    except PipelineError as exc:
        if _pipeline_cancelled(project_id):
            return
        write_partial_asset_package(paths, exc.to_dict(), store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, exc.to_dict())
    except Exception as exc:
        error: Dict[str, Any] = {
            "code": "unexpected_error",
            "message": str(exc),
            "step": store.get(project_id).status,
            "details": {"type": type(exc).__name__},
        }
        if _pipeline_cancelled(project_id):
            return
        write_partial_asset_package(paths, error, store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, error)


def run_project_visual_pipeline(project_id: str) -> None:
    record = store.get(project_id)
    paths = store.paths(project_id)
    try:
        required_files = {
            "metadata": paths.source_dir / "metadata.json",
            "transcript": paths.transcript_dir / "transcript.json",
            "keyframes": paths.analysis_dir / "keyframes.json",
        }
        missing = [name for name, path in required_files.items() if not path.exists()]
        if missing:
            raise PipelineError(
                code="resume_artifacts_missing",
                message="Cannot rerun visual analysis because required upstream artifacts are missing.",
                step="analyzing_visuals",
                details={"missing": missing},
            )

        _clear_visual_and_downstream_outputs(project_id)
        metadata = read_json(required_files["metadata"])
        transcript = read_json(required_files["transcript"])
        keyframes = read_json(required_files["keyframes"])
        _register_standard_outputs(project_id)

        store.set_status(project_id, ProjectStatus.analyzing_visuals, "Rerunning OCR and visual analysis from existing keyframes.")
        store.clear_warnings(project_id)
        visual = analyze_visuals(keyframes, record.language, paths, use_ocr=False if record.text_only else record.use_ocr)
        for warning in visual.get("warnings", []):
            store.add_warning(project_id, warning)
        store.add_output(project_id, "visual_analysis", paths.analysis_dir / "visual-analysis.json")
        store.log(
            project_id,
            ProjectStatus.analyzing_visuals,
            "Visual analysis completed from existing keyframes.",
            {"ocr_provider": visual.get("ocr_provider"), "warnings": len(visual.get("warnings", []))},
        )

        store.set_status(project_id, ProjectStatus.planning_content, "Generating structured content assets with refreshed visuals.")
        assets = build_content_assets(metadata, transcript, keyframes, visual, record.language, record.style, paths)
        assets = _tag_analysis_assets(record, assets, keyframes, paths)
        store.add_output(project_id, "content_assets", paths.analysis_dir / "content-assets.json")
        store.log(project_id, ProjectStatus.planning_content, "Content assets completed from refreshed visuals.")
        _write_platform_outputs(
            project_id,
            record,
            metadata,
            transcript,
            keyframes,
            visual,
            assets,
            paths,
            render_images=True,
            source_label="from refreshed visual analysis",
        )
    except PipelineError as exc:
        if _pipeline_cancelled(project_id):
            return
        write_partial_asset_package(paths, exc.to_dict(), store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, exc.to_dict())
    except Exception as exc:
        error: Dict[str, Any] = {
            "code": "unexpected_error",
            "message": str(exc),
            "step": store.get(project_id).status,
            "details": {"type": type(exc).__name__},
        }
        if _pipeline_cancelled(project_id):
            return
        write_partial_asset_package(paths, error, store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, error)


def run_project_analysis_pipeline(project_id: str) -> None:
    record = store.get(project_id)
    paths = store.paths(project_id)
    try:
        store.set_status(project_id, ProjectStatus.ingesting, "Fetching video metadata, media, subtitles, and thumbnail.")
        metadata = ingest_video(record.url, record.language, paths, prefer_subtitles_only=record.text_only)
        _record_ingest_warnings(project_id, metadata)
        store.add_output(project_id, "metadata", paths.source_dir / "metadata.json")
        store.log(project_id, ProjectStatus.ingesting, "Ingest completed.", {"title": metadata.get("title")})

        store.set_status(project_id, ProjectStatus.transcribing, "Building normalized transcript timeline.")
        transcript = build_transcript(metadata, record.language, record.use_whisper, paths)
        write_json(paths.source_dir / "metadata.json", metadata)
        store.add_output(project_id, "metadata", paths.source_dir / "metadata.json")
        store.add_output(project_id, "transcript", paths.transcript_dir / "transcript.json")
        store.log(
            project_id,
            ProjectStatus.transcribing,
            "Transcript completed.",
            {"segments": transcript.get("segment_count"), "source": transcript.get("source")},
        )

        keyframes = _build_keyframes_for_record(project_id, record, metadata, transcript, paths)
        visual = _analyze_visuals_for_record(project_id, record, keyframes, paths)

        store.set_status(project_id, ProjectStatus.planning_content, "Generating structured content assets with LLM.")
        try:
            assets = build_content_assets(metadata, transcript, keyframes, visual, record.language, record.style, paths)
        except PipelineError as exc:
            if not _can_use_basic_analysis_fallback(exc):
                raise
            store.add_warning(
                project_id,
                (
                    "LLM content planning failed during Analyze, so the system generated a conservative local "
                    "basic analysis from real transcript, keyframe, and OCR artifacts. Produce still requires a "
                    "working LLM."
                ),
            )
            store.log(project_id, ProjectStatus.planning_content, "LLM planning failed; using local basic analysis fallback.", exc.to_dict())
            assets = build_basic_content_assets(
                metadata,
                transcript,
                keyframes,
                visual,
                record.language,
                record.style,
                paths,
                fallback_reason=exc.message,
            )
        assets = _tag_analysis_assets(record, assets, keyframes, paths)
        store.add_output(project_id, "content_assets", paths.analysis_dir / "content-assets.json")
        write_analysis_asset_package(metadata, transcript, keyframes, visual, assets, paths, store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        completion_message = (
            "Text-only analysis completed. Review or edit content assets before producing an article."
            if record.text_only
            else f"Analysis completed. Review or edit content assets before producing {get_platform(record.target_platform).name} content."
        )
        store.set_status(project_id, ProjectStatus.analysis_completed, completion_message)
    except PipelineError as exc:
        if _pipeline_cancelled(project_id):
            return
        write_partial_asset_package(paths, exc.to_dict(), store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, exc.to_dict())
    except Exception as exc:
        error: Dict[str, Any] = {
            "code": "unexpected_error",
            "message": str(exc),
            "step": store.get(project_id).status,
            "details": {"type": type(exc).__name__},
        }
        if _pipeline_cancelled(project_id):
            return
        write_partial_asset_package(paths, error, store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, error)


def run_project_produce_pipeline(project_id: str) -> None:
    run_project_platform_produce_pipeline(project_id, "xhs")


def run_project_toutiao_produce_pipeline(project_id: str) -> None:
    run_project_platform_produce_pipeline(project_id, "toutiao")


def run_project_platform_produce_pipeline(project_id: str, platform: str) -> None:
    record = store.get(project_id)
    paths = store.paths(project_id)
    adapter = get_platform(platform)
    try:
        _clear_platform_produce_outputs(project_id, adapter.key)
        required_files = {
            "metadata": paths.source_dir / "metadata.json",
            "transcript": paths.transcript_dir / "transcript.json",
            "keyframes": paths.analysis_dir / "keyframes.json",
            "visual_analysis": paths.analysis_dir / "visual-analysis.json",
            "content_assets": paths.analysis_dir / "content-assets.json",
        }
        missing = [name for name, path in required_files.items() if not path.exists()]
        if missing:
            raise PipelineError(
                code="produce_artifacts_missing",
                message=f"Cannot produce {adapter.name} article because Analyze artifacts are missing.",
                step="producing_article",
                details={"missing": missing, "platform": adapter.key},
            )

        metadata = read_json(required_files["metadata"])
        transcript = read_json(required_files["transcript"])
        keyframes = read_json(required_files["keyframes"])
        visual = read_json(required_files["visual_analysis"])
        assets = read_json(required_files["content_assets"])
        _register_standard_outputs(project_id)
        if record.target_platform != adapter.key:
            record = store.set_target_platform(project_id, adapter.key)
        _write_platform_outputs(
            project_id,
            record,
            metadata,
            transcript,
            keyframes,
            visual,
            assets,
            paths,
            render_images=False,
            source_label="from reviewed analysis assets",
        )
    except PipelineError as exc:
        if _pipeline_cancelled(project_id):
            return
        write_partial_asset_package(paths, exc.to_dict(), store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, exc.to_dict())
    except Exception as exc:
        error: Dict[str, Any] = {
            "code": "unexpected_error",
            "message": str(exc),
            "step": store.get(project_id).status,
            "details": {"type": type(exc).__name__, "platform": adapter.key},
        }
        if _pipeline_cancelled(project_id):
            return
        write_partial_asset_package(paths, error, store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, error)


def run_project_image_generation_pipeline(project_id: str, style: str = "clean") -> None:
    paths = store.paths(project_id)
    try:
        record = store.get(project_id)
        if record.text_only:
            raise PipelineError(
                code="text_only_image_generation_disabled",
                message="Text-only projects do not generate image cards.",
                step="rendering_cards",
                details={"project_id": project_id},
            )
        _clear_image_generation_outputs(project_id)
        required_files = {
            "metadata": paths.source_dir / "metadata.json",
            "transcript": paths.transcript_dir / "transcript.json",
            "keyframes": paths.analysis_dir / "keyframes.json",
            "visual_analysis": paths.analysis_dir / "visual-analysis.json",
            "content_assets": paths.analysis_dir / "content-assets.json",
            "xhs_post_json": paths.analysis_dir / "xiaohongshu-post.json",
            "image_prompts": paths.analysis_dir / "image-prompts.json",
        }
        missing = [name for name, path in required_files.items() if not path.exists()]
        if missing:
            raise PipelineError(
                code="image_generation_artifacts_missing",
                message="Cannot generate image cards because XHS article artifacts are missing.",
                step="rendering_cards",
                details={"missing": missing},
            )

        metadata = read_json(required_files["metadata"])
        transcript = read_json(required_files["transcript"])
        keyframes = read_json(required_files["keyframes"])
        visual = read_json(required_files["visual_analysis"])
        assets = read_json(required_files["content_assets"])
        post = read_json(required_files["xhs_post_json"])
        prompts = read_json(required_files["image_prompts"])
        _register_standard_outputs(project_id)

        store.set_status(project_id, ProjectStatus.rendering_cards, "Rendering finished Xiaohongshu image-card PNG files.", {"platform": "xhs"})
        image_cards = render_image_cards(metadata, assets, post, keyframes, prompts, paths, style=style)
        store.add_output(project_id, "image_cards", paths.analysis_dir / "image-cards.json")

        warnings = store.get(project_id).warnings
        write_reports(metadata, transcript, keyframes, visual, assets, post, prompts, paths, warnings, image_cards=image_cards)
        _register_standard_outputs(project_id)
        store.set_status(project_id, ProjectStatus.completed, "Image generation completed. XHS article and image cards are ready.", {"platform": "xhs"})
    except PipelineError as exc:
        if _pipeline_cancelled(project_id):
            return
        write_partial_asset_package(paths, exc.to_dict(), store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, exc.to_dict())
    except Exception as exc:
        error: Dict[str, Any] = {
            "code": "unexpected_error",
            "message": str(exc),
            "step": store.get(project_id).status,
            "details": {"type": type(exc).__name__},
        }
        if _pipeline_cancelled(project_id):
            return
        write_partial_asset_package(paths, error, store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, error)


def run_project_toutiao_image_generation_pipeline(project_id: str, style: str = "clean") -> None:
    paths = store.paths(project_id)
    try:
        record = store.get(project_id)
        if record.text_only:
            raise PipelineError(
                code="text_only_image_generation_disabled",
                message="Text-only projects do not generate Toutiao image cards.",
                step="rendering_cards",
                details={"project_id": project_id, "platform": "toutiao"},
            )
        _clear_toutiao_image_generation_outputs(project_id)
        required_files = {
            "metadata": paths.source_dir / "metadata.json",
            "transcript": paths.transcript_dir / "transcript.json",
            "keyframes": paths.analysis_dir / "keyframes.json",
            "visual_analysis": paths.analysis_dir / "visual-analysis.json",
            "content_assets": paths.analysis_dir / "content-assets.json",
            "toutiao_post_json": paths.analysis_dir / "toutiao-post.json",
            "toutiao_image_prompts": paths.analysis_dir / "toutiao-image-prompts.json",
        }
        missing = [name for name, path in required_files.items() if not path.exists()]
        if missing:
            raise PipelineError(
                code="image_generation_artifacts_missing",
                message="Cannot generate image cards because Toutiao article artifacts are missing.",
                step="rendering_cards",
                details={"missing": missing, "platform": "toutiao"},
            )

        metadata = read_json(required_files["metadata"])
        transcript = read_json(required_files["transcript"])
        keyframes = read_json(required_files["keyframes"])
        visual = read_json(required_files["visual_analysis"])
        assets = read_json(required_files["content_assets"])
        post = read_json(required_files["toutiao_post_json"])
        prompts = read_json(required_files["toutiao_image_prompts"])
        _register_standard_outputs(project_id)

        store.set_status(project_id, ProjectStatus.rendering_cards, "Rendering finished Toutiao image-card PNG files.", {"platform": "toutiao"})
        image_cards = render_image_cards(
            metadata,
            assets,
            post,
            keyframes,
            prompts,
            paths,
            style=style,
            platform="toutiao",
            output_filename="toutiao-image-cards.json",
            cards_dir=paths.toutiao_cards_dir,
        )
        store.add_output(project_id, "toutiao_image_cards", paths.analysis_dir / "toutiao-image-cards.json")

        warnings = store.get(project_id).warnings
        write_reports(metadata, transcript, keyframes, visual, assets, post, prompts, paths, warnings, image_cards=image_cards, platform="toutiao")
        _register_standard_outputs(project_id)
        store.set_status(project_id, ProjectStatus.completed, "Image generation completed. Toutiao article and image cards are ready.", {"platform": "toutiao"})
    except PipelineError as exc:
        if _pipeline_cancelled(project_id):
            return
        write_partial_asset_package(paths, exc.to_dict(), store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, exc.to_dict())
    except Exception as exc:
        error: Dict[str, Any] = {
            "code": "unexpected_error",
            "message": str(exc),
            "step": store.get(project_id).status,
            "details": {"type": type(exc).__name__, "platform": "toutiao"},
        }
        if _pipeline_cancelled(project_id):
            return
        write_partial_asset_package(paths, error, store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, error)
