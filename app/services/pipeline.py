from typing import Any, Dict

from app.schemas.models import ProjectStatus
from app.services.content_planner import build_content_assets
from app.services.errors import PipelineError
from app.services.frame_extractor import extract_keyframes
from app.services.image_card_renderer import render_image_cards
from app.services.image_prompt_writer import write_image_prompts
from app.services.ingest import ingest_video
from app.services.report_writer import write_analysis_asset_package, write_partial_asset_package, write_reports
from app.services.runtime_store import read_json, store, write_json
from app.services.transcript import build_transcript
from app.services.visual_analyzer import analyze_visuals
from app.services.xhs_writer import write_toutiao_post, write_xhs_post

DOWNSTREAM_OUTPUT_KINDS = [
    "content_assets",
    "xhs_post_json",
    "xhs_post_md",
    "image_prompts",
    "image_cards",
    "toutiao_post_json",
    "toutiao_post_md",
    "toutiao_image_prompts",
    "toutiao_image_cards",
    "asset_package",
]
PRODUCE_OUTPUT_KINDS = ["xhs_post_json", "xhs_post_md", "image_prompts", "image_cards", "asset_package"]
IMAGE_GENERATION_OUTPUT_KINDS = ["image_cards", "asset_package"]
TOUTIAO_PRODUCE_OUTPUT_KINDS = [
    "toutiao_post_json",
    "toutiao_post_md",
    "toutiao_image_prompts",
    "toutiao_image_cards",
    "asset_package",
]
TOUTIAO_IMAGE_GENERATION_OUTPUT_KINDS = ["toutiao_image_cards", "asset_package"]
VISUAL_AND_DOWNSTREAM_OUTPUT_KINDS = ["visual_analysis", *DOWNSTREAM_OUTPUT_KINDS]


def _register_standard_outputs(project_id: str) -> None:
    paths = store.paths(project_id)
    output_paths = {
        "metadata": paths.source_dir / "metadata.json",
        "transcript": paths.transcript_dir / "transcript.json",
        "keyframes": paths.analysis_dir / "keyframes.json",
        "visual_analysis": paths.analysis_dir / "visual-analysis.json",
        "content_assets": paths.analysis_dir / "content-assets.json",
        "xhs_post_json": paths.analysis_dir / "xiaohongshu-post.json",
        "xhs_post_md": paths.analysis_dir / "xhs-post.md",
        "image_prompts": paths.analysis_dir / "image-prompts.json",
        "image_cards": paths.analysis_dir / "image-cards.json",
        "toutiao_post_json": paths.analysis_dir / "toutiao-post.json",
        "toutiao_post_md": paths.analysis_dir / "toutiao-post.md",
        "toutiao_image_prompts": paths.analysis_dir / "toutiao-image-prompts.json",
        "toutiao_image_cards": paths.analysis_dir / "toutiao-image-cards.json",
        "asset_package": paths.analysis_dir / "asset-package.json",
        "run_metadata": paths.analysis_dir / "run-metadata.json",
    }
    for kind, path in output_paths.items():
        if path.exists():
            store.add_output(project_id, kind, path)


def _clear_downstream_outputs(project_id: str) -> None:
    paths = store.paths(project_id)
    stale_files = [
        paths.analysis_dir / "content-assets.json",
        paths.analysis_dir / "xiaohongshu-post.json",
        paths.analysis_dir / "xhs-post.md",
        paths.analysis_dir / "image-prompts.json",
        paths.analysis_dir / "image-cards.json",
        paths.analysis_dir / "toutiao-post.json",
        paths.analysis_dir / "toutiao-post.md",
        paths.analysis_dir / "toutiao-image-prompts.json",
        paths.analysis_dir / "toutiao-image-cards.json",
        paths.analysis_dir / "asset-package.json",
    ]
    for path in stale_files:
        path.unlink(missing_ok=True)
    store.clear_outputs(project_id, DOWNSTREAM_OUTPUT_KINDS)


def _clear_produce_outputs(project_id: str) -> None:
    paths = store.paths(project_id)
    stale_files = [
        paths.analysis_dir / "xiaohongshu-post.json",
        paths.analysis_dir / "xhs-post.md",
        paths.analysis_dir / "image-prompts.json",
        paths.analysis_dir / "image-cards.json",
        paths.analysis_dir / "asset-package.json",
    ]
    for path in stale_files:
        path.unlink(missing_ok=True)
    if paths.cards_dir.exists():
        for path in paths.cards_dir.glob("*.png"):
            path.unlink(missing_ok=True)
    store.clear_outputs(project_id, PRODUCE_OUTPUT_KINDS)


def _clear_toutiao_produce_outputs(project_id: str) -> None:
    paths = store.paths(project_id)
    stale_files = [
        paths.analysis_dir / "toutiao-post.json",
        paths.analysis_dir / "toutiao-post.md",
        paths.analysis_dir / "toutiao-image-prompts.json",
        paths.analysis_dir / "toutiao-image-cards.json",
        paths.analysis_dir / "asset-package.json",
    ]
    for path in stale_files:
        path.unlink(missing_ok=True)
    if paths.toutiao_cards_dir.exists():
        for path in paths.toutiao_cards_dir.glob("*.png"):
            path.unlink(missing_ok=True)
    store.clear_outputs(project_id, TOUTIAO_PRODUCE_OUTPUT_KINDS)


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
    stale_files = [
        paths.analysis_dir / "visual-analysis.json",
        paths.analysis_dir / "content-assets.json",
        paths.analysis_dir / "xiaohongshu-post.json",
        paths.analysis_dir / "xhs-post.md",
        paths.analysis_dir / "image-prompts.json",
        paths.analysis_dir / "image-cards.json",
        paths.analysis_dir / "toutiao-post.json",
        paths.analysis_dir / "toutiao-post.md",
        paths.analysis_dir / "toutiao-image-prompts.json",
        paths.analysis_dir / "toutiao-image-cards.json",
        paths.analysis_dir / "asset-package.json",
    ]
    for path in stale_files:
        path.unlink(missing_ok=True)
    store.clear_outputs(project_id, VISUAL_AND_DOWNSTREAM_OUTPUT_KINDS)


def run_project_pipeline(project_id: str) -> None:
    record = store.get(project_id)
    paths = store.paths(project_id)
    try:
        store.set_status(project_id, ProjectStatus.ingesting, "Fetching video metadata, media, subtitles, and thumbnail.")
        metadata = ingest_video(record.url, record.language, paths)
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

        store.set_status(project_id, ProjectStatus.extracting_frames, "Detecting scenes and extracting keyframes.")
        keyframes = extract_keyframes(metadata, transcript, record.max_frames, paths)
        store.add_output(project_id, "keyframes", paths.analysis_dir / "keyframes.json")
        store.log(project_id, ProjectStatus.extracting_frames, "Keyframes completed.", {"frames": keyframes.get("frame_count")})

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

        store.set_status(project_id, ProjectStatus.planning_content, "Generating structured content assets with LLM.")
        assets = build_content_assets(metadata, transcript, keyframes, visual, record.language, record.style, paths)
        store.add_output(project_id, "content_assets", paths.analysis_dir / "content-assets.json")
        store.log(project_id, ProjectStatus.planning_content, "Content assets completed.")

        store.set_status(project_id, ProjectStatus.writing_xhs, "Writing Xiaohongshu post and image prompts.")
        post = write_xhs_post(metadata, assets, keyframes, visual, record.style, paths)
        prompts = write_image_prompts(post, keyframes, visual, paths)
        store.set_status(project_id, ProjectStatus.rendering_cards, "Rendering Xiaohongshu image cards.")
        render_image_cards(metadata, assets, post, keyframes, prompts, paths)
        warnings = store.get(project_id).warnings
        image_cards = read_json(paths.analysis_dir / "image-cards.json")
        write_reports(metadata, transcript, keyframes, visual, assets, post, prompts, paths, warnings, image_cards=image_cards)
        _register_standard_outputs(project_id)
        store.set_status(project_id, ProjectStatus.completed, "Pipeline completed.")
    except PipelineError as exc:
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
        store.add_output(project_id, "content_assets", paths.analysis_dir / "content-assets.json")
        store.log(project_id, ProjectStatus.planning_content, "Content assets completed from existing artifacts.")

        store.set_status(project_id, ProjectStatus.writing_xhs, "Rerunning Xiaohongshu post and image prompts.")
        post = write_xhs_post(metadata, assets, keyframes, visual, record.style, paths)
        prompts = write_image_prompts(post, keyframes, visual, paths)
        store.set_status(project_id, ProjectStatus.rendering_cards, "Rendering Xiaohongshu image cards from rerun.")
        render_image_cards(metadata, assets, post, keyframes, prompts, paths)
        warnings = store.get(project_id).warnings
        image_cards = read_json(paths.analysis_dir / "image-cards.json")
        write_reports(metadata, transcript, keyframes, visual, assets, post, prompts, paths, warnings, image_cards=image_cards)
        _register_standard_outputs(project_id)
        store.set_status(project_id, ProjectStatus.completed, "Downstream rerun completed.")
    except PipelineError as exc:
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
        visual = analyze_visuals(keyframes, record.language, paths, use_ocr=record.use_ocr)
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
        store.add_output(project_id, "content_assets", paths.analysis_dir / "content-assets.json")
        store.log(project_id, ProjectStatus.planning_content, "Content assets completed from refreshed visuals.")

        store.set_status(project_id, ProjectStatus.writing_xhs, "Writing Xiaohongshu post and image prompts from refreshed visuals.")
        post = write_xhs_post(metadata, assets, keyframes, visual, record.style, paths)
        prompts = write_image_prompts(post, keyframes, visual, paths)
        store.set_status(project_id, ProjectStatus.rendering_cards, "Rendering Xiaohongshu image cards from refreshed visuals.")
        render_image_cards(metadata, assets, post, keyframes, prompts, paths)
        warnings = store.get(project_id).warnings
        image_cards = read_json(paths.analysis_dir / "image-cards.json")
        write_reports(metadata, transcript, keyframes, visual, assets, post, prompts, paths, warnings, image_cards=image_cards)
        _register_standard_outputs(project_id)
        store.set_status(project_id, ProjectStatus.completed, "Visual and downstream rerun completed.")
    except PipelineError as exc:
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
        write_partial_asset_package(paths, error, store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, error)


def run_project_analysis_pipeline(project_id: str) -> None:
    record = store.get(project_id)
    paths = store.paths(project_id)
    try:
        store.set_status(project_id, ProjectStatus.ingesting, "Fetching video metadata, media, subtitles, and thumbnail.")
        metadata = ingest_video(record.url, record.language, paths)
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

        store.set_status(project_id, ProjectStatus.extracting_frames, "Detecting scenes and extracting keyframes.")
        keyframes = extract_keyframes(metadata, transcript, record.max_frames, paths)
        store.add_output(project_id, "keyframes", paths.analysis_dir / "keyframes.json")
        store.log(project_id, ProjectStatus.extracting_frames, "Keyframes completed.", {"frames": keyframes.get("frame_count")})

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

        store.set_status(project_id, ProjectStatus.planning_content, "Generating structured content assets with LLM.")
        assets = build_content_assets(metadata, transcript, keyframes, visual, record.language, record.style, paths)
        store.add_output(project_id, "content_assets", paths.analysis_dir / "content-assets.json")
        write_analysis_asset_package(metadata, transcript, keyframes, visual, assets, paths, store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.set_status(project_id, ProjectStatus.analysis_completed, "Analysis completed. Review or edit content assets before producing XHS cards.")
    except PipelineError as exc:
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
        write_partial_asset_package(paths, error, store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, error)


def run_project_produce_pipeline(project_id: str) -> None:
    record = store.get(project_id)
    paths = store.paths(project_id)
    try:
        _clear_produce_outputs(project_id)
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
                message="Cannot produce XHS article and cards because Analyze artifacts are missing.",
                step="producing_article",
                details={"missing": missing},
            )

        metadata = read_json(required_files["metadata"])
        transcript = read_json(required_files["transcript"])
        keyframes = read_json(required_files["keyframes"])
        visual = read_json(required_files["visual_analysis"])
        assets = read_json(required_files["content_assets"])
        _register_standard_outputs(project_id)

        store.set_status(project_id, ProjectStatus.producing_article, "Generating Xiaohongshu post from reviewed analysis assets.")
        post = write_xhs_post(metadata, assets, keyframes, visual, record.style, paths)
        store.add_output(project_id, "xhs_post_json", paths.analysis_dir / "xiaohongshu-post.json")

        prompts = write_image_prompts(post, keyframes, visual, paths)
        store.add_output(project_id, "image_prompts", paths.analysis_dir / "image-prompts.json")

        warnings = store.get(project_id).warnings
        write_reports(metadata, transcript, keyframes, visual, assets, post, prompts, paths, warnings, image_cards={})
        _register_standard_outputs(project_id)
        store.set_status(project_id, ProjectStatus.xhs_completed, "XHS article completed. Image generation can be run next.")
    except PipelineError as exc:
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
        write_partial_asset_package(paths, error, store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, error)


def run_project_toutiao_produce_pipeline(project_id: str) -> None:
    record = store.get(project_id)
    paths = store.paths(project_id)
    try:
        _clear_toutiao_produce_outputs(project_id)
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
                message="Cannot produce Toutiao article and cards because Analyze artifacts are missing.",
                step="producing_article",
                details={"missing": missing, "platform": "toutiao"},
            )

        metadata = read_json(required_files["metadata"])
        transcript = read_json(required_files["transcript"])
        keyframes = read_json(required_files["keyframes"])
        visual = read_json(required_files["visual_analysis"])
        assets = read_json(required_files["content_assets"])
        _register_standard_outputs(project_id)

        store.set_status(project_id, ProjectStatus.producing_article, "Generating Toutiao post from reviewed analysis assets.", {"platform": "toutiao"})
        post = write_toutiao_post(metadata, assets, keyframes, visual, record.style, paths)
        store.add_output(project_id, "toutiao_post_json", paths.analysis_dir / "toutiao-post.json")

        prompts = write_image_prompts(post, keyframes, visual, paths, platform="toutiao")
        store.add_output(project_id, "toutiao_image_prompts", paths.analysis_dir / "toutiao-image-prompts.json")

        warnings = store.get(project_id).warnings
        write_reports(metadata, transcript, keyframes, visual, assets, post, prompts, paths, warnings, image_cards={}, platform="toutiao")
        _register_standard_outputs(project_id)
        store.set_status(project_id, ProjectStatus.toutiao_completed, "Toutiao article completed. Image generation can be run next.")
    except PipelineError as exc:
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
        write_partial_asset_package(paths, error, store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, error)


def run_project_image_generation_pipeline(project_id: str, style: str = "clean") -> None:
    paths = store.paths(project_id)
    try:
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

        store.set_status(project_id, ProjectStatus.rendering_cards, "Rendering finished Xiaohongshu image-card PNG files.")
        image_cards = render_image_cards(metadata, assets, post, keyframes, prompts, paths, style=style)
        store.add_output(project_id, "image_cards", paths.analysis_dir / "image-cards.json")

        warnings = store.get(project_id).warnings
        write_reports(metadata, transcript, keyframes, visual, assets, post, prompts, paths, warnings, image_cards=image_cards)
        _register_standard_outputs(project_id)
        store.set_status(project_id, ProjectStatus.completed, "Image generation completed. XHS article and image cards are ready.")
    except PipelineError as exc:
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
        write_partial_asset_package(paths, error, store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, error)


def run_project_toutiao_image_generation_pipeline(project_id: str, style: str = "clean") -> None:
    paths = store.paths(project_id)
    try:
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
        store.set_status(project_id, ProjectStatus.completed, "Image generation completed. Toutiao article and image cards are ready.")
    except PipelineError as exc:
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
        write_partial_asset_package(paths, error, store.get(project_id).warnings)
        _register_standard_outputs(project_id)
        store.fail(project_id, error)
