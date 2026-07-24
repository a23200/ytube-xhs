import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from app.schemas.models import (
    FILE_KIND_TO_PATH,
    BatchCreate,
    BatchCreated,
    CookieBrowserImportRequest,
    CookieVerifyRequest,
    ImageSettingsUpdate,
    LLMSettingsUpdate,
    ProjectCreate,
    ProjectCreated,
    ProjectImageGenerateRequest,
    ProjectProduceRequest,
    ProjectStatus,
)
from app.services.article_quality import evaluate_article_quality, quality_error
from app.services.batch_manager import batch_manager
from app.services.batch_store import BATCH_TERMINAL_STATUSES, batch_store
from app.services.contracts import validate_content_assets, validate_xhs_post
from app.services.cookie_manager import (
    MAX_COOKIE_FILE_BYTES,
    CookieManagerError,
    delete_managed_cookie,
    import_cookie_text,
    import_from_browser,
    list_cookie_statuses,
    verify_cookie,
)
from app.services.diagnostics import collect_diagnostics
from app.services.error_diagnostics import diagnose_error, error_catalog
from app.services.errors import PipelineError
from app.services.image_card_renderer import render_image_cards
from app.services.image_client import image_client
from app.services.image_settings import get_image_settings, update_image_settings
from app.services.llm_client import llm_client
from app.services.llm_settings import get_llm_settings, update_llm_settings
from app.services.pipeline import (
    run_project_analysis_pipeline,
    run_project_downstream_pipeline,
    run_project_image_generation_pipeline,
    run_project_pipeline,
    run_project_platform_produce_pipeline,
    run_project_produce_pipeline,
    run_project_toutiao_image_generation_pipeline,
    run_project_toutiao_produce_pipeline,
    run_project_visual_pipeline,
)
from app.services.platforms import get_platform, platform_keys, platform_values, public_platform_capabilities
from app.services.project_verifier import verify_runtime_project
from app.services.report_writer import write_reports
from app.services.runtime_store import parse_time, read_json, store, write_json
from app.services.task_manager import task_manager

router = APIRouter(prefix="/api")
FRAME_FILENAME_RE = re.compile(r"^frame_\d{4}\.jpg$")
CARD_FILENAME_RE = re.compile(r"^(cover|summary|slide_\d{2})\.png$")
DOCX_KIND_RE = re.compile(r"^(xhs|toutiao|douyin|bilibili)_post_docx$")


def _require_cookie_action_confirmation(request: Request) -> None:
    if request.headers.get("X-YTXHS-Cookie-Action") != "confirm":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "cookie_action_confirmation_required",
                "message": "Cookie write actions must come from the account-management UI.",
            },
        )

STATUS_UI: dict[str, dict[str, Any]] = {
    "queued": {
        "label": "排队中",
        "description": "任务已进入受控队列，等待可用执行槽位。",
        "estimate_seconds": 1,
        "outputs": [],
    },
    "created": {
        "label": "任务已创建",
        "description": "任务已进入队列，准备开始获取视频。",
        "estimate_seconds": 2,
        "outputs": [],
    },
    "ingesting": {
        "label": "获取视频信息",
        "description": "正在用 yt-dlp 获取视频信息、字幕、缩略图和媒体文件。",
        "estimate_seconds": 45,
        "outputs": ["metadata"],
    },
    "transcribing": {
        "label": "生成字幕时间轴",
        "description": "正在优先读取原字幕；没有字幕时会尝试 Whisper 转录。",
        "estimate_seconds": 70,
        "outputs": ["transcript"],
    },
    "extracting_frames": {
        "label": "抽取关键帧",
        "description": "正在检测场景并抽取可用于图文的关键画面。",
        "estimate_seconds": 35,
        "outputs": ["keyframes"],
    },
    "analyzing_visuals": {
        "label": "识别画面文字",
        "description": "正在对关键帧做 OCR 和基础视觉分析。",
        "estimate_seconds": 25,
        "outputs": ["visual_analysis"],
    },
    "planning_content": {
        "label": "生成创作底稿",
        "description": "正在用 LLM 提炼事实、观点、受众和选题方向。",
        "estimate_seconds": 15,
        "outputs": ["content_assets"],
    },
    "analysis_completed": {
        "label": "解析完成",
        "description": "创作底稿已完成，可以确认或编辑后产出图文。",
        "estimate_seconds": 1,
        "outputs": ["content_assets", "asset_package"],
    },
    "producing_article": {
        "label": "生成平台稿",
        "description": "正在根据已确认解析生成目标平台文章。",
        "estimate_seconds": 45,
        "outputs": [],
    },
    "validating_content": {
        "label": "校验文章",
        "description": "正在检查钩子、小标题、事实数据、重复片段和改写程度；不合格会定向重写。",
        "estimate_seconds": 30,
        "outputs": [],
    },
    "xhs_completed": {
        "label": "小红书稿完成",
        "description": "文章、Markdown、Word、质量报告和图片提示词已生成，下一步可调用生图 API 渲染 PNG 卡片。",
        "estimate_seconds": 1,
        "outputs": ["xhs_post_json", "xhs_post_md", "xhs_post_docx", "xhs_quality_report", "image_prompts", "asset_package"],
    },
    "toutiao_completed": {
        "label": "今日头条稿完成",
        "description": "今日头条文章、Markdown、Word、质量报告和图片提示词已生成，下一步可调用生图 API 渲染 PNG 卡片。",
        "estimate_seconds": 1,
        "outputs": ["toutiao_post_json", "toutiao_post_md", "toutiao_post_docx", "toutiao_quality_report", "toutiao_image_prompts", "asset_package"],
    },
    "douyin_completed": {
        "label": "抖音稿完成",
        "description": "抖音口播文章、Markdown、Word 和质量报告已生成。",
        "estimate_seconds": 1,
        "outputs": ["douyin_post_json", "douyin_post_md", "douyin_post_docx", "douyin_quality_report", "asset_package"],
    },
    "bilibili_completed": {
        "label": "哔哩哔哩稿完成",
        "description": "哔哩哔哩文章、Markdown、Word 和质量报告已生成。",
        "estimate_seconds": 1,
        "outputs": ["bilibili_post_json", "bilibili_post_md", "bilibili_post_docx", "bilibili_quality_report", "asset_package"],
    },
    "writing_xhs": {
        "label": "写入文章文件",
        "description": "正在写入小红书文章、Markdown 和图片提示词。",
        "estimate_seconds": 25,
        "outputs": ["xhs_post_json", "xhs_post_md", "image_prompts", "asset_package"],
    },
    "rendering_cards": {
        "label": "渲染图文卡片",
        "description": "正在把文章和关键帧渲染成小红书竖版 PNG 卡片。",
        "estimate_seconds": 20,
        "outputs": ["image_cards"],
    },
    "completed": {
        "label": "图文完成",
        "description": "文章、图文卡片和下载素材已准备好。",
        "estimate_seconds": 1,
        "outputs": [
            "metadata",
            "transcript",
            "keyframes",
            "visual_analysis",
            "content_assets",
            "xhs_post_json",
            "xhs_post_md",
            "xhs_post_docx",
            "xhs_quality_report",
            "image_prompts",
            "image_cards",
            "toutiao_post_json",
            "toutiao_post_md",
            "toutiao_post_docx",
            "toutiao_quality_report",
            "toutiao_image_prompts",
            "toutiao_image_cards",
            "asset_package",
            "run_metadata",
        ],
    },
    "failed": {
        "label": "处理失败",
        "description": "任务中断，请查看错误信息和已生成产物。",
        "estimate_seconds": 1,
        "outputs": [],
    },
    "stopped": {
        "label": "已停止",
        "description": "任务已由用户强制停止，已有中间产物保留。",
        "estimate_seconds": 1,
        "outputs": [],
    },
}

PLATFORM_STATUS_OVERRIDES: dict[str, dict[str, dict[str, Any]]] = {
    "toutiao": {
        "producing_article": {
            "label": "生成今日头条稿",
            "description": "正在根据已确认解析生成今日头条标题、正文和配图计划。",
            "outputs": ["toutiao_post_json", "toutiao_image_prompts"],
        },
        "rendering_cards": {
            "label": "渲染今日头条卡片",
            "description": "正在把今日头条文章和关键帧渲染成 PNG 卡片。",
            "outputs": ["toutiao_image_cards"],
        },
        "completed": {
            "outputs": [
                "metadata",
                "transcript",
                "keyframes",
                "visual_analysis",
                "content_assets",
                "toutiao_post_json",
                "toutiao_post_md",
                "toutiao_image_prompts",
                "toutiao_image_cards",
                "asset_package",
                "run_metadata",
            ],
        },
    },
    "xhs": {
        "producing_article": {
            "label": "生成小红书稿",
            "description": "正在根据已确认解析生成小红书标题、正文、标签和配图计划。",
            "outputs": ["xhs_post_json", "image_prompts"],
        },
        "rendering_cards": {
            "label": "渲染图文卡片",
            "description": "正在把小红书文章和关键帧渲染成竖版 PNG 卡片。",
            "outputs": ["image_cards"],
        },
        "completed": {
            "outputs": [
                "metadata",
                "transcript",
                "keyframes",
                "visual_analysis",
                "content_assets",
                "xhs_post_json",
                "xhs_post_md",
                "image_prompts",
                "image_cards",
                "asset_package",
                "run_metadata",
            ],
        },
    },
}

for _adapter in platform_values():
    _platform_outputs = _adapter.output_kinds(include_images=False)
    PLATFORM_STATUS_OVERRIDES.setdefault(_adapter.key, {}).update(
        {
            "queued": {
                "label": f"{_adapter.name}任务排队中",
                "description": "任务正在等待文章生成执行槽位。",
                "outputs": [],
            },
            "producing_article": {
                "label": f"生成{_adapter.name}稿",
                "description": f"正在生成{_adapter.name}标题、开头钩子和连续自然段正文。",
                "outputs": [_adapter.post_json_kind],
            },
            "validating_content": {
                "label": f"校验{_adapter.name}稿",
                "description": "正在校验无小标题、钩子、来源数据和改写程度。",
                "outputs": [_adapter.post_json_kind, _adapter.quality_kind],
            },
            _adapter.completed_status.value: {
                "label": f"{_adapter.name}稿完成",
                "description": f"{_adapter.name}文章、Markdown、Word 和质量报告已生成。",
                "outputs": [*_platform_outputs, "asset_package"],
            },
        }
    )

ANALYZE_FLOW = ["created", "ingesting", "transcribing", "extracting_frames", "analyzing_visuals", "planning_content", "analysis_completed"]
IMAGE_GENERATION_FLOW = ["rendering_cards", "completed"]
LEGACY_PRODUCE_FLOW = ["writing_xhs", "rendering_cards", "completed"]
PLATFORM_COMPLETED_STATUSES = {adapter.completed_status.value for adapter in platform_values()}
PRODUCE_STATUSES = {"queued", "producing_article", "validating_content", *PLATFORM_COMPLETED_STATUSES}
IMAGE_GENERATION_STATUSES = {"rendering_cards", "completed"}
LEGACY_PRODUCE_STATUSES = {"writing_xhs", "rendering_cards", "completed"}


def _produce_flow(platform: str) -> list[str]:
    return ["queued", "producing_article", "validating_content", get_platform(platform).completed_status.value]


def _get_existing_project(project_id: str):
    try:
        return store.get(project_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Project not found") from None


def _paths_for_existing_project(project_id: str):
    _get_existing_project(project_id)
    return store.paths(project_id)


def _registered_frame_paths(project_id: str) -> list[Path]:
    record = _get_existing_project(project_id)
    if record.outputs.get("keyframes") != FILE_KIND_TO_PATH["keyframes"]:
        return []
    paths = store.paths(project_id)
    keyframes_path = paths.file_for_kind("keyframes")
    if not keyframes_path.exists():
        return []
    try:
        keyframes_payload = read_json(keyframes_path)
    except Exception:
        return []

    project_root = paths.project_dir.resolve()
    allowed: list[Path] = []
    seen = set()
    for item in keyframes_payload.get("keyframes", []) or []:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        raw_path = Path(str(item["path"]))
        candidate = raw_path if raw_path.is_absolute() else paths.project_dir / raw_path
        try:
            resolved = candidate.resolve()
            resolved.relative_to(project_root)
        except (OSError, ValueError):
            continue
        try:
            resolved.relative_to(paths.frames_dir.resolve())
        except ValueError:
            continue
        if not resolved.is_file() or not FRAME_FILENAME_RE.fullmatch(resolved.name):
            continue
        if resolved in seen:
            continue
        allowed.append(resolved)
        seen.add(resolved)
    return sorted(allowed, key=lambda path: path.name)


def _registered_card_paths(project_id: str) -> list[Path]:
    record = _get_existing_project(project_id)
    if record.outputs.get("image_cards") != FILE_KIND_TO_PATH["image_cards"]:
        return []
    paths = store.paths(project_id)
    image_cards_path = paths.file_for_kind("image_cards")
    if not image_cards_path.exists():
        return []
    try:
        image_cards_payload = read_json(image_cards_path)
    except Exception:
        return []

    project_root = paths.project_dir.resolve()
    allowed: list[Path] = []
    seen = set()
    for item in image_cards_payload.get("cards", []) or []:
        if not isinstance(item, dict) or not item.get("output_path"):
            continue
        raw_path = Path(str(item["output_path"]))
        candidate = raw_path if raw_path.is_absolute() else paths.project_dir / raw_path
        try:
            resolved = candidate.resolve()
            resolved.relative_to(project_root)
            resolved.relative_to(paths.cards_dir.resolve())
        except (OSError, ValueError):
            continue
        if not resolved.is_file() or not CARD_FILENAME_RE.fullmatch(resolved.name):
            continue
        if resolved in seen:
            continue
        allowed.append(resolved)
        seen.add(resolved)
    return sorted(allowed, key=lambda path: path.name)


def _registered_platform_card_paths(project_id: str, platform: str = "xhs") -> list[Path]:
    if platform != "toutiao":
        return _registered_card_paths(project_id)
    record = _get_existing_project(project_id)
    if record.outputs.get("toutiao_image_cards") != FILE_KIND_TO_PATH["toutiao_image_cards"]:
        return []
    paths = store.paths(project_id)
    image_cards_path = paths.file_for_kind("toutiao_image_cards")
    if not image_cards_path.exists():
        return []
    try:
        image_cards_payload = read_json(image_cards_path)
    except Exception:
        return []

    project_root = paths.project_dir.resolve()
    allowed: list[Path] = []
    seen = set()
    for item in image_cards_payload.get("cards", []) or []:
        if not isinstance(item, dict) or not item.get("output_path"):
            continue
        raw_path = Path(str(item["output_path"]))
        candidate = raw_path if raw_path.is_absolute() else paths.project_dir / raw_path
        try:
            resolved = candidate.resolve()
            resolved.relative_to(project_root)
            resolved.relative_to(paths.toutiao_cards_dir.resolve())
        except (OSError, ValueError):
            continue
        if not resolved.is_file() or not CARD_FILENAME_RE.fullmatch(resolved.name):
            continue
        if resolved in seen:
            continue
        allowed.append(resolved)
        seen.add(resolved)
    return sorted(allowed, key=lambda path: path.name)


def _load_upstream_project_payloads(project_id: str) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    paths = _paths_for_existing_project(project_id)
    files = {
        "metadata": paths.file_for_kind("metadata"),
        "transcript": paths.file_for_kind("transcript"),
        "keyframes": paths.file_for_kind("keyframes"),
        "visual_analysis": paths.file_for_kind("visual_analysis"),
    }
    missing = [kind for kind, path in files.items() if not path.exists()]
    if missing:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "required_artifacts_missing",
                "message": "Project is missing required artifacts.",
                "missing": missing,
            },
        )
    return (
        read_json(files["metadata"]),
        read_json(files["transcript"]),
        read_json(files["keyframes"]),
        read_json(files["visual_analysis"]),
    )


def _load_required_project_payloads(project_id: str) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    paths = _paths_for_existing_project(project_id)
    metadata, transcript, keyframes, visual = _load_upstream_project_payloads(project_id)
    content_assets_path = paths.file_for_kind("content_assets")
    if not content_assets_path.exists():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "required_artifacts_missing",
                "message": "Project is missing required artifacts.",
                "missing": ["content_assets"],
            },
        )
    return metadata, transcript, keyframes, visual, read_json(content_assets_path)


def _status_value(value: Any) -> str:
    return getattr(value, "value", str(value))


def _platform_from_details(value: Any) -> Optional[str]:
    if not isinstance(value, dict):
        return None
    platform = value.get("platform")
    if platform in platform_keys():
        return str(platform)
    nested = value.get("details")
    if isinstance(nested, dict):
        return _platform_from_details(nested)
    return None


def _message_platform(message: Any) -> Optional[str]:
    text = str(message or "")
    aliases = {
        "xhs": ("Xiaohongshu", "XHS", "小红书"),
        "toutiao": ("Toutiao", "今日头条"),
        "douyin": ("Douyin", "抖音"),
        "bilibili": ("Bilibili", "哔哩哔哩", "B站"),
    }
    for platform, markers in aliases.items():
        if any(marker in text for marker in markers):
            return platform
    return None


def _last_logged_platform(record: Any) -> Optional[str]:
    error_platform = _platform_from_details(record.error)
    if _status_value(record.status) in {"failed", "stopped"} and error_platform:
        return error_platform
    for log in reversed(record.logs):
        platform = _platform_from_details(getattr(log, "details", None)) or _message_platform(getattr(log, "message", ""))
        if platform:
            return platform
    return error_platform


def _record_platform(record: Any) -> str:
    configured = getattr(record, "target_platform", None)
    if configured in platform_keys():
        return str(configured)
    status = _status_value(record.status)
    for adapter in platform_values():
        if status == adapter.completed_status.value:
            return adapter.key

    logged_platform = _last_logged_platform(record)
    if status in {"queued", "producing_article", "validating_content", "rendering_cards", "completed", "failed", "stopped"} and logged_platform:
        return logged_platform

    outputs = record.outputs or {}
    platforms_with_outputs = [adapter.key for adapter in platform_values() if any(outputs.get(kind) for kind in adapter.output_kinds())]
    if len(platforms_with_outputs) == 1:
        return platforms_with_outputs[0]
    return logged_platform or "xhs"


def _status_ui_for_step(step: str, platform: str = "xhs") -> dict[str, Any]:
    ui = dict(STATUS_UI.get(step, STATUS_UI["created"]))
    ui["outputs"] = list(ui.get("outputs", []))
    override = PLATFORM_STATUS_OVERRIDES.get(platform, {}).get(step)
    if override:
        ui.update({key: value for key, value in override.items() if key != "outputs"})
        if "outputs" in override:
            ui["outputs"] = list(override["outputs"])
    return ui


def _safe_parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return parse_time(str(value))
    except (TypeError, ValueError):
        return None


def _seconds_between(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if not start or not end:
        return None
    return max(0.0, (end - start).total_seconds())


def _log_platform(log: Any) -> Optional[str]:
    return _platform_from_details(getattr(log, "details", None)) or _message_platform(getattr(log, "message", ""))


def _log_scope(log: Any) -> Optional[str]:
    details = getattr(log, "details", None)
    if not isinstance(details, dict):
        return None
    scope = details.get("scope")
    return str(scope) if scope else None


def _progress_logs(record: Any, mode: str, platform: str) -> list[Any]:
    logs = list(record.logs)
    if mode == "produce":
        for index in range(len(logs) - 1, -1, -1):
            log = logs[index]
            if (
                _status_value(log.status) == "queued"
                and _log_scope(log) == "produce"
                and _log_platform(log) == platform
            ):
                return logs[index:]
        for index in range(len(logs) - 1, -1, -1):
            log = logs[index]
            if _status_value(log.status) in {"producing_article", "writing_xhs"} and _log_platform(log) == platform:
                return logs[index:]
    elif mode == "image_generation":
        for index in range(len(logs) - 1, -1, -1):
            log = logs[index]
            if (
                _status_value(log.status) == "rendering_cards"
                and _log_scope(log) == "image_generation"
                and _log_platform(log) == platform
            ):
                return logs[index:]
        for index in range(len(logs) - 1, -1, -1):
            log = logs[index]
            if _status_value(log.status) == "rendering_cards" and _log_platform(log) == platform:
                return logs[index:]
    return logs


def _first_log_time(logs: list[Any], status: str) -> Optional[datetime]:
    for log in logs:
        if _status_value(log.status) == status:
            return _safe_parse_time(log.time)
    return None


def _next_distinct_log_time(logs: list[Any], flow: list[str], status: str) -> Optional[datetime]:
    statuses_after = set(flow[flow.index(status) + 1 :]) if status in flow else set()
    if not statuses_after:
        return None
    first_seen = False
    for log in logs:
        log_status = _status_value(log.status)
        if log_status == status:
            first_seen = True
            continue
        if first_seen and log_status in statuses_after:
            return _safe_parse_time(log.time)
    return None


def _progress_flow(record: Any) -> tuple[str, str, list[str], str]:
    status = _status_value(record.status)
    logged_statuses = {_status_value(log.status) for log in record.logs}
    error_step = str((record.error or {}).get("step") or "")
    output_kinds = record.outputs or {}
    platform = _record_platform(record)
    adapter = get_platform(platform)
    produce_seen = bool(logged_statuses & PRODUCE_STATUSES or any(output_kinds.get(item.post_json_kind) for item in platform_values()))
    image_generation_seen = bool(logged_statuses & IMAGE_GENERATION_STATUSES or output_kinds.get("image_cards") or output_kinds.get("toutiao_image_cards"))
    legacy_full_pipeline = "writing_xhs" in logged_statuses and "producing_article" not in logged_statuses

    if status == "queued" and not _last_logged_platform(record):
        return "analyze", "解析进度", ["queued", *ANALYZE_FLOW], platform

    if status == "analysis_completed":
        return "analyze", "解析进度", ANALYZE_FLOW, platform
    if legacy_full_pipeline and (status in LEGACY_PRODUCE_STATUSES or error_step in LEGACY_PRODUCE_STATUSES):
        return "produce", "图文产出进度", LEGACY_PRODUCE_FLOW, platform
    if status in PLATFORM_COMPLETED_STATUSES:
        return "produce", f"{adapter.name}稿进度", _produce_flow(platform), platform
    if status in PRODUCE_STATUSES or (status == "failed" and error_step in PRODUCE_STATUSES):
        return "produce", f"{adapter.name}稿进度", _produce_flow(platform), platform
    if status in IMAGE_GENERATION_STATUSES or (status == "failed" and error_step in IMAGE_GENERATION_STATUSES):
        mode_label = "今日头条生图进度" if platform == "toutiao" else "生图进度"
        return "image_generation", mode_label, IMAGE_GENERATION_FLOW, platform
    if status == "completed" and image_generation_seen:
        mode_label = "今日头条生图进度" if platform == "toutiao" else "生图进度"
        return "image_generation", mode_label, IMAGE_GENERATION_FLOW, platform
    if status == "completed" and produce_seen:
        return "produce", f"{adapter.name}稿进度", _produce_flow(platform), platform
    return "analyze", "解析进度", ANALYZE_FLOW, platform


def _active_progress_step(record: Any, flow: list[str], logs: list[Any]) -> str:
    status = _status_value(record.status)
    if status in {"failed", "stopped"}:
        error_step = str((record.error or {}).get("step") or "")
        if error_step in flow:
            return error_step
        for log in reversed(logs):
            log_status = _status_value(log.status)
            if log_status in flow:
                return log_status
        return flow[0]
    if status in flow:
        return status
    if status == "completed":
        return flow[-1]
    return flow[0]


def _stage_elapsed_seconds(record: Any, logs: list[Any], flow: list[str], step: str, now: datetime) -> Optional[float]:
    started_at = _first_log_time(logs, step)
    if not started_at:
        return None
    ended_at = _next_distinct_log_time(logs, flow, step)
    terminal_statuses = {"completed", "analysis_completed", "failed", "stopped", *PLATFORM_COMPLETED_STATUSES}
    if ended_at is None and _status_value(record.status) in terminal_statuses:
        ended_at = _safe_parse_time(record.updated_at)
    return _seconds_between(started_at, ended_at or now)


def _build_project_progress(record: Any) -> dict[str, Any]:
    mode, mode_label, flow, platform = _progress_flow(record)
    progress_logs = _progress_logs(record, mode, platform)
    status = _status_value(record.status)
    active_step = _active_progress_step(record, flow, progress_logs)
    active_index = flow.index(active_step) if active_step in flow else 0
    now = datetime.now(timezone.utc)
    outputs = record.outputs or {}

    started_at = _safe_parse_time(record.created_at)
    if mode == "produce":
        started_at = (
            _first_log_time(progress_logs, "queued")
            or _first_log_time(progress_logs, "producing_article")
            or _first_log_time(progress_logs, "writing_xhs")
            or started_at
        )
    if mode == "image_generation":
        started_at = _first_log_time(progress_logs, "rendering_cards") or started_at
    updated_at = _safe_parse_time(record.updated_at) or now
    terminal_statuses = {"completed", "analysis_completed", "failed", "stopped", *PLATFORM_COMPLETED_STATUSES}
    elapsed_seconds = _seconds_between(started_at, now if status not in terminal_statuses else updated_at)

    total_estimate = sum(float(_status_ui_for_step(step, platform)["estimate_seconds"]) for step in flow)
    completed_weight = sum(float(_status_ui_for_step(step, platform)["estimate_seconds"]) for step in flow[:active_index])
    active_estimate = float(_status_ui_for_step(active_step, platform)["estimate_seconds"])
    active_elapsed = _stage_elapsed_seconds(record, progress_logs, flow, active_step, now) or 0.0
    active_fraction = min(0.92, active_elapsed / max(active_estimate, 1.0))

    terminal_successes = {"completed", "analysis_completed", *PLATFORM_COMPLETED_STATUSES}
    if status in terminal_successes and active_step == flow[-1]:
        percent = 100
        remaining_seconds: Optional[float] = 0.0
        eta_confidence = "complete"
    elif status in {"failed", "stopped"}:
        percent = round(min(99.0, ((completed_weight + active_estimate * 0.5) / max(total_estimate, 1.0)) * 100))
        remaining_seconds = None
        eta_confidence = "failed"
    else:
        percent = max(1, round(((completed_weight + active_estimate * active_fraction) / max(total_estimate, 1.0)) * 100))
        remaining_estimate = max(active_estimate - active_elapsed, min(active_estimate * 0.3, 15.0))
        remaining_estimate += sum(float(_status_ui_for_step(step, platform)["estimate_seconds"]) for step in flow[active_index + 1 :])
        elapsed_done_estimate = max(completed_weight + min(active_elapsed, active_estimate), 1.0)
        scale = max(1.0, min(3.0, (elapsed_seconds or 0.0) / elapsed_done_estimate))
        remaining_seconds = round(remaining_estimate * scale, 1)
        eta_confidence = "low" if scale > 1.8 or active_elapsed > active_estimate * 1.5 else "medium"

    steps = []
    completed_steps = 0
    for index, step in enumerate(flow):
        ui = _status_ui_for_step(step, platform)
        if status in {"failed", "stopped"} and step == active_step:
            step_state = "stopped" if status == "stopped" else "failed"
        elif status in terminal_successes and step == flow[-1]:
            step_state = "done"
        elif index < active_index:
            step_state = "done"
        elif index == active_index:
            step_state = "running"
        else:
            step_state = "pending"
        if step_state == "done":
            completed_steps += 1
        expected_outputs = list(ui["outputs"])
        if getattr(record, "text_only", False):
            expected_outputs = [
                kind
                for kind in expected_outputs
                if kind not in {item for adapter_item in platform_values() for item in [adapter_item.image_prompts_kind, adapter_item.image_cards_kind]}
            ]
        ready_outputs = [kind for kind in expected_outputs if outputs.get(kind)]
        steps.append(
            {
                "status": step,
                "label": ui["label"],
                "description": ui["description"],
                "state": step_state,
                "elapsed_seconds": _stage_elapsed_seconds(record, progress_logs, flow, step, now),
                "estimated_seconds": ui["estimate_seconds"],
                "outputs_ready": len(ready_outputs),
                "outputs_expected": len(expected_outputs),
                "output_kinds_ready": ready_outputs,
                "output_kinds_expected": expected_outputs,
            }
        )

    current_ui = _status_ui_for_step(active_step, platform)
    return {
        "mode": mode,
        "mode_label": mode_label,
        "platform": platform,
        "percent": percent,
        "current_step": active_step,
        "current_step_label": current_ui["label"],
        "current_step_description": current_ui["description"],
        "started_at": started_at.isoformat() if started_at else None,
        "elapsed_seconds": elapsed_seconds,
        "estimated_total_seconds": None if remaining_seconds is None or elapsed_seconds is None else round(elapsed_seconds + remaining_seconds, 1),
        "remaining_seconds": remaining_seconds,
        "eta_confidence": eta_confidence,
        "estimate_note": "预计时间按当前阶段、真实已用时和默认阶段耗时估算；视频长度、网络、Whisper 与 OCR 会影响实际耗时。",
        "completed_steps": completed_steps,
        "total_steps": len(flow),
        "steps": steps,
    }


@router.get("/health")
def health() -> dict:
    return {"ok": True}


@router.get("/diagnostics")
def diagnostics() -> dict:
    return collect_diagnostics()


@router.get("/system/doctor")
def system_doctor() -> dict:
    return collect_diagnostics()


@router.get("/settings/llm")
def read_llm_settings() -> dict:
    return get_llm_settings()


@router.put("/settings/llm")
def save_llm_settings(request: LLMSettingsUpdate) -> dict:
    try:
        return update_llm_settings(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/llm/self-test")
def llm_self_test() -> dict:
    return llm_client.self_test()


@router.get("/settings/image")
def read_image_settings() -> dict:
    return get_image_settings()


@router.put("/settings/image")
def save_image_settings(request: ImageSettingsUpdate) -> dict:
    try:
        return update_image_settings(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/image/self-test")
def image_self_test(real: bool = False) -> dict:
    return image_client.self_test(real=real)


@router.get("/auth/cookies")
def read_cookie_statuses() -> dict:
    return list_cookie_statuses()


@router.post("/auth/cookies/import-browser")
def import_browser_cookies(request_body: CookieBrowserImportRequest, request: Request) -> dict:
    _require_cookie_action_confirmation(request)
    try:
        return import_from_browser(request_body.platform, request_body.browser, request_body.profile)
    except CookieManagerError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@router.post("/auth/cookies/verify")
def verify_platform_cookies(request_body: CookieVerifyRequest, request: Request) -> dict:
    _require_cookie_action_confirmation(request)
    try:
        return verify_cookie(request_body.platform, request_body.url)
    except CookieManagerError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@router.post("/auth/cookies/{platform}/upload")
async def upload_platform_cookies(platform: str, request: Request, file: UploadFile = File(...)) -> dict:
    _require_cookie_action_confirmation(request)
    content = await file.read(MAX_COOKIE_FILE_BYTES + 1)
    try:
        return import_cookie_text(platform, content)
    except CookieManagerError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@router.delete("/auth/cookies/{platform}")
def delete_platform_cookies(platform: str, request: Request) -> dict:
    _require_cookie_action_confirmation(request)
    try:
        return delete_managed_cookie(platform)
    except CookieManagerError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc


@router.get("/platforms")
def list_platforms() -> dict:
    return {"platforms": public_platform_capabilities()}


@router.get("/errors/catalog")
def list_error_catalog() -> dict[str, Any]:
    return {"errors": error_catalog()}


def _get_existing_batch(batch_id: str):
    try:
        return batch_store.get(batch_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Batch not found") from exc


def _batch_response(record: Any) -> dict[str, Any]:
    execution = batch_manager.snapshot(record.batch_id)
    processed = record.completed_count + record.failed_count + record.stopped_count + record.skipped_count
    progress_percent = round((processed / record.total_count) * 100) if record.total_count else 0
    paths = batch_store.paths(record.batch_id)
    payload = record.model_dump(mode="json")
    payload["error"] = diagnose_error(payload["error"]) if payload.get("error") else None
    for item in payload.get("items", []):
        if item.get("error"):
            item["error"] = diagnose_error(item["error"])
    payload.update(
        {
            "progress_percent": progress_percent,
            "processed_count": processed,
            "can_cancel": record.status not in BATCH_TERMINAL_STATUSES,
            "download_ready": record.document_count > 0,
            "output_directory": str(paths.documents_dir),
            "execution": execution,
        }
    )
    return payload


@router.post("/batches", response_model=BatchCreated)
def create_batch(request: BatchCreate) -> BatchCreated:
    try:
        llm_client.ensure_available("producing_article")
    except PipelineError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc
    record = batch_store.create(request)
    queued = batch_manager.submit(record.batch_id)
    current = batch_store.get(record.batch_id)
    return BatchCreated(
        batch_id=current.batch_id,
        status=current.status,
        total_count=current.total_count,
        queue_position=queued["queue_position"],
    )


@router.get("/batches")
def list_batches() -> list[dict[str, Any]]:
    return [_batch_response(record) for record in batch_store.list()]


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    return _batch_response(_get_existing_batch(batch_id))


@router.post("/batches/{batch_id}/cancel")
def cancel_batch(batch_id: str) -> dict[str, Any]:
    _get_existing_batch(batch_id)
    result = batch_manager.cancel(batch_id)
    return {
        "batch": _batch_response(result["batch"]),
        "project_cancelled": result["project_cancelled"],
    }


@router.get("/batches/{batch_id}/download")
def download_batch_documents(batch_id: str):
    record = _get_existing_batch(batch_id)
    if record.document_count < 1:
        raise HTTPException(status_code=404, detail="No batch Word documents are available yet")
    try:
        archive_path = batch_store.build_archive(batch_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        archive_path,
        filename=f"batch-{batch_id}-documents.zip",
        media_type="application/zip",
    )


@router.get("/batches/{batch_id}/documents/{filename}")
def download_batch_document(batch_id: str, filename: str):
    record = _get_existing_batch(batch_id)
    registered = {item.document_filename for item in record.items if item.document_filename}
    if filename not in registered:
        raise HTTPException(status_code=404, detail="Batch document not found")
    try:
        document_path = batch_store.document_path(batch_id, filename)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Batch document not found") from exc
    if not document_path.exists():
        raise HTTPException(status_code=404, detail="Batch document not found")
    return FileResponse(
        document_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def _queue_details(project_id: str, scope: str, platform: Optional[str], position: int) -> None:
    label = get_platform(platform).name if platform else "解析"
    store.set_status(
        project_id,
        ProjectStatus.queued,
        f"{label} task is waiting for an execution slot.",
        {"scope": scope, "platform": platform, "queue_position": position},
    )


def _submit_project_task(
    scope: str,
    project_id: str,
    target: Any,
    *args: Any,
    platform: Optional[str] = None,
) -> dict:
    try:
        return task_manager.submit(
            scope,
            project_id,
            target,
            *args,
            platform=platform,
            on_queued=lambda position: _queue_details(project_id, scope, platform, position),
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "project_busy", "message": str(exc), "status": _get_existing_project(project_id).status},
        ) from exc


@router.post("/projects", response_model=ProjectCreated)
def create_project(request: ProjectCreate, background_tasks: BackgroundTasks) -> ProjectCreated:
    record = store.create(request)
    _submit_project_task(
        "analyze",
        record.project_id,
        run_project_pipeline,
        platform=record.target_platform,
    )
    return ProjectCreated(
        project_id=record.project_id,
        status=record.status,
        target_platform=record.target_platform,
    )


@router.post("/projects/analyze", response_model=ProjectCreated)
def analyze_project(request: ProjectCreate, background_tasks: BackgroundTasks) -> ProjectCreated:
    record = store.create(request)
    _submit_project_task(
        "analyze",
        record.project_id,
        run_project_analysis_pipeline,
        platform=record.target_platform,
    )
    return ProjectCreated(
        project_id=record.project_id,
        status=record.status,
        target_platform=record.target_platform,
    )


@router.post("/projects/{project_id}/produce")
def produce_project(project_id: str, background_tasks: BackgroundTasks, request: Optional[ProjectProduceRequest] = None) -> dict:
    _get_existing_project(project_id)
    paths = store.paths(project_id)
    if request and request.content_assets is not None:
        _metadata, transcript, keyframes, _visual = _load_upstream_project_payloads(project_id)
        try:
            validated = validate_content_assets(request.content_assets)
            from app.services.source_anchors import validate_content_asset_anchors

            validate_content_asset_anchors(validated, transcript, keyframes, paths)
        except PipelineError as exc:
            raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
        write_json(paths.analysis_dir / "content-assets.json", validated)
        store.add_output(project_id, "content_assets", paths.analysis_dir / "content-assets.json")

    if not store.can_start_produce(project_id):
        record = _get_existing_project(project_id)
        missing_inputs = store.produce_missing_inputs(project_id)
        if missing_inputs:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "produce_artifacts_missing",
                    "message": "Project is missing required Analyze artifacts for Produce.",
                    "status": record.status,
                    "missing": missing_inputs,
                },
            )
        raise HTTPException(
            status_code=409,
            detail={"code": "project_busy", "message": "Project is not ready to produce.", "status": record.status},
        )

    try:
        llm_client.ensure_available("producing_article")
    except PipelineError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc

    started, record, missing_inputs = store.try_start_produce(project_id)
    if not started:
        if missing_inputs:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "produce_artifacts_missing",
                    "message": "Project is missing required Analyze artifacts for Produce.",
                    "status": record.status,
                    "missing": missing_inputs,
                },
            )
        raise HTTPException(
            status_code=409,
            detail={"code": "project_busy", "message": "Project is not ready to produce.", "status": record.status},
        )
    _submit_project_task("produce", project_id, run_project_produce_pipeline, platform="xhs")
    return {"project_id": project_id, "status": "queued", "scope": "produce"}


@router.post("/projects/{project_id}/produce/toutiao")
def produce_project_toutiao(project_id: str, background_tasks: BackgroundTasks, request: Optional[ProjectProduceRequest] = None) -> dict:
    _get_existing_project(project_id)
    paths = store.paths(project_id)
    if request and request.content_assets is not None:
        _metadata, transcript, keyframes, _visual = _load_upstream_project_payloads(project_id)
        try:
            validated = validate_content_assets(request.content_assets)
            from app.services.source_anchors import validate_content_asset_anchors

            validate_content_asset_anchors(validated, transcript, keyframes, paths)
        except PipelineError as exc:
            raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
        write_json(paths.analysis_dir / "content-assets.json", validated)
        store.add_output(project_id, "content_assets", paths.analysis_dir / "content-assets.json")

    if not store.can_start_produce(project_id):
        record = _get_existing_project(project_id)
        missing_inputs = store.produce_missing_inputs(project_id)
        if missing_inputs:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "produce_artifacts_missing",
                    "message": "Project is missing required Analyze artifacts for Toutiao Produce.",
                    "status": record.status,
                    "missing": missing_inputs,
                },
            )
        raise HTTPException(
            status_code=409,
            detail={"code": "project_busy", "message": "Project is not ready to produce Toutiao content.", "status": record.status},
        )

    try:
        llm_client.ensure_available("producing_article")
    except PipelineError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc

    started, record, missing_inputs = store.try_start_platform_produce(project_id, platform="toutiao")
    if not started:
        if missing_inputs:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "produce_artifacts_missing",
                    "message": "Project is missing required Analyze artifacts for Toutiao Produce.",
                    "status": record.status,
                    "missing": missing_inputs,
                },
            )
        raise HTTPException(
            status_code=409,
            detail={"code": "project_busy", "message": "Project is not ready to produce Toutiao content.", "status": record.status},
        )
    _submit_project_task("produce", project_id, run_project_toutiao_produce_pipeline, platform="toutiao")
    return {"project_id": project_id, "status": "queued", "scope": "produce", "platform": "toutiao"}


@router.post("/projects/{project_id}/produce/platform/{platform}")
def produce_project_for_platform(
    project_id: str,
    platform: str,
    request: Optional[ProjectProduceRequest] = None,
) -> dict:
    try:
        adapter = get_platform(platform)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "unsupported_platform", "message": str(exc), "supported": list(platform_keys())},
        ) from exc
    _get_existing_project(project_id)
    paths = store.paths(project_id)
    if request and request.content_assets is not None:
        _metadata, transcript, keyframes, _visual = _load_upstream_project_payloads(project_id)
        try:
            validated = validate_content_assets(request.content_assets)
            from app.services.source_anchors import validate_content_asset_anchors

            validate_content_asset_anchors(validated, transcript, keyframes, paths)
        except PipelineError as exc:
            raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
        write_json(paths.analysis_dir / "content-assets.json", validated)
        store.add_output(project_id, "content_assets", paths.analysis_dir / "content-assets.json")

    if not store.can_start_produce(project_id):
        record = _get_existing_project(project_id)
        missing_inputs = store.produce_missing_inputs(project_id)
        if missing_inputs:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "produce_artifacts_missing",
                    "message": f"Project is missing required Analyze artifacts for {adapter.name} Produce.",
                    "status": record.status,
                    "missing": missing_inputs,
                    "platform": adapter.key,
                },
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "project_busy",
                "message": f"Project is not ready to produce {adapter.name} content.",
                "status": record.status,
                "platform": adapter.key,
            },
        )
    try:
        llm_client.ensure_available("producing_article")
    except PipelineError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc
    started, record, missing_inputs = store.try_start_platform_produce(project_id, platform=adapter.key)
    if not started:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "project_busy" if not missing_inputs else "produce_artifacts_missing",
                "message": f"Project is not ready to produce {adapter.name} content.",
                "status": record.status,
                "missing": missing_inputs,
                "platform": adapter.key,
            },
        )
    result = _submit_project_task(
        "produce",
        project_id,
        run_project_platform_produce_pipeline,
        adapter.key,
        platform=adapter.key,
    )
    return {
        "project_id": project_id,
        "status": "queued" if result["queued"] else "started",
        "scope": "produce",
        "platform": adapter.key,
        "queue_position": result["queue_position"],
    }


@router.post("/projects/{project_id}/generate-images")
def generate_project_images(project_id: str, background_tasks: BackgroundTasks, request: Optional[ProjectImageGenerateRequest] = None) -> dict:
    record = _get_existing_project(project_id)
    if record.text_only:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "text_only_image_generation_disabled",
                "message": "This project is in text-only mode; image cards are intentionally disabled.",
                "status": record.status,
            },
        )
    if not store.can_start_image_generation(project_id):
        record = _get_existing_project(project_id)
        missing_inputs = store.image_generation_missing_inputs(project_id)
        if missing_inputs:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "image_generation_artifacts_missing",
                    "message": "Project is missing required XHS artifacts for image generation.",
                    "status": record.status,
                    "missing": missing_inputs,
                },
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "project_busy",
                "message": "Project is not ready to generate images.",
                "status": record.status,
            },
        )

    started, record, missing_inputs = store.try_start_image_generation(project_id)
    if not started:
        if missing_inputs:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "image_generation_artifacts_missing",
                    "message": "Project is missing required XHS artifacts for image generation.",
                    "status": record.status,
                    "missing": missing_inputs,
                },
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "project_busy",
                "message": "Project is not ready to generate images.",
                "status": record.status,
            },
        )

    style = (request.style if request else None) or "clean"
    _submit_project_task("produce", project_id, run_project_image_generation_pipeline, style, platform="xhs")
    return {"project_id": project_id, "status": "queued", "scope": "image_generation"}


@router.post("/projects/{project_id}/generate-images/toutiao")
def generate_project_toutiao_images(project_id: str, background_tasks: BackgroundTasks, request: Optional[ProjectImageGenerateRequest] = None) -> dict:
    record = _get_existing_project(project_id)
    if record.text_only:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "text_only_image_generation_disabled",
                "message": "This project is in text-only mode; Toutiao image cards are intentionally disabled.",
                "status": record.status,
                "platform": "toutiao",
            },
        )
    if not store.can_start_platform_image_generation(project_id, "toutiao"):
        record = _get_existing_project(project_id)
        missing_inputs = store.platform_image_generation_missing_inputs(project_id, "toutiao")
        if missing_inputs:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "image_generation_artifacts_missing",
                    "message": "Project is missing required Toutiao artifacts for image generation.",
                    "status": record.status,
                    "missing": missing_inputs,
                    "platform": "toutiao",
                },
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "project_busy",
                "message": "Project is not ready to generate Toutiao images.",
                "status": record.status,
                "platform": "toutiao",
            },
        )

    started, record, missing_inputs = store.try_start_platform_image_generation(project_id, "toutiao")
    if not started:
        if missing_inputs:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "image_generation_artifacts_missing",
                    "message": "Project is missing required Toutiao artifacts for image generation.",
                    "status": record.status,
                    "missing": missing_inputs,
                    "platform": "toutiao",
                },
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "project_busy",
                "message": "Project is not ready to generate Toutiao images.",
                "status": record.status,
                "platform": "toutiao",
            },
        )

    style = (request.style if request else None) or "clean"
    _submit_project_task("produce", project_id, run_project_toutiao_image_generation_pipeline, style, platform="toutiao")
    return {"project_id": project_id, "status": "queued", "scope": "image_generation", "platform": "toutiao"}


@router.patch("/projects/{project_id}/content-assets")
def update_content_assets(project_id: str, payload: Dict[str, Any]) -> dict:
    paths = _paths_for_existing_project(project_id)
    _metadata, transcript, keyframes, _visual = _load_upstream_project_payloads(project_id)
    try:
        validated = validate_content_assets(payload)
        from app.services.source_anchors import validate_content_asset_anchors

        validate_content_asset_anchors(validated, transcript, keyframes, paths)
    except PipelineError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
    write_json(paths.analysis_dir / "content-assets.json", validated)
    store.add_output(project_id, "content_assets", paths.analysis_dir / "content-assets.json")
    store.log(project_id, _get_existing_project(project_id).status, "Content assets updated from workbench.", details={"artifact": "content-assets.json"})
    return {"project_id": project_id, "kind": "content_assets", "saved": True}


@router.patch("/projects/{project_id}/xhs-post")
def update_xhs_post(project_id: str, payload: Dict[str, Any]) -> dict:
    return _update_platform_post(project_id, "xhs", payload)


@router.patch("/projects/{project_id}/toutiao-post")
def update_toutiao_post(project_id: str, payload: Dict[str, Any]) -> dict:
    return _update_platform_post(project_id, "toutiao", payload)


@router.patch("/projects/{project_id}/platform/{platform}/post")
def update_platform_post(project_id: str, platform: str, payload: Dict[str, Any]) -> dict:
    try:
        get_platform(platform)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "unsupported_platform", "message": str(exc)}) from exc
    return _update_platform_post(project_id, platform, payload)


def _update_platform_post(project_id: str, platform: str, payload: Dict[str, Any]) -> dict:
    adapter = get_platform(platform)
    paths = _paths_for_existing_project(project_id)
    metadata, transcript, keyframes, visual, assets = _load_required_project_payloads(project_id)
    try:
        require_frame_anchors = bool(keyframes.get("keyframes"))
        validated = validate_xhs_post(payload, require_frame_anchors=require_frame_anchors)
        from app.services.source_anchors import validate_xhs_post_anchors

        if require_frame_anchors:
            validate_xhs_post_anchors(validated, keyframes, paths)
        report = evaluate_article_quality(validated, assets, transcript, platform=adapter.key)
        if not report["passed"]:
            raise quality_error(report)
    except PipelineError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
    validated["platform"] = adapter.key
    validated["platform_name"] = adapter.name
    validated["quality"] = {
        "estimated_rewrite_degree": report["similarity"]["estimated_rewrite_degree"],
        "rewrite_count": 0,
        "report": adapter.quality_filename,
        "note": report["policy"]["originality_note"],
    }
    write_json(paths.analysis_dir / adapter.post_filename, validated)
    write_json(paths.analysis_dir / adapter.quality_filename, report)
    store.add_output(project_id, adapter.post_json_kind, paths.analysis_dir / adapter.post_filename)
    store.add_output(project_id, adapter.quality_kind, paths.analysis_dir / adapter.quality_filename)
    prompts_path = paths.analysis_dir / adapter.image_prompts_filename
    cards_path = paths.analysis_dir / adapter.image_cards_filename
    prompts = read_json(prompts_path) if adapter.supports_images and prompts_path.exists() else {"image_prompts": []}
    image_cards = read_json(cards_path) if adapter.supports_images and cards_path.exists() else {}
    write_reports(
        metadata,
        transcript,
        keyframes,
        visual,
        assets,
        validated,
        prompts,
        paths,
        _get_existing_project(project_id).warnings,
        image_cards=image_cards,
        platform=adapter.key,
    )
    store.add_output(project_id, adapter.post_md_kind, paths.analysis_dir / adapter.markdown_filename)
    store.add_output(project_id, adapter.post_docx_kind, paths.analysis_dir / adapter.docx_filename)
    store.add_output(project_id, "asset_package", paths.analysis_dir / "asset-package.json")
    store.log(
        project_id,
        _get_existing_project(project_id).status,
        f"{adapter.name} post updated from workbench and passed quality validation.",
        details={"artifact": adapter.post_filename, "platform": adapter.key},
    )
    return {"project_id": project_id, "kind": adapter.post_json_kind, "saved": True, "quality": report}


@router.patch("/projects/{project_id}/image-cards")
def update_image_cards(project_id: str, payload: Dict[str, Any]) -> dict:
    paths = _paths_for_existing_project(project_id)
    metadata, transcript, keyframes, visual, assets = _load_required_project_payloads(project_id)
    xhs_path = paths.analysis_dir / "xiaohongshu-post.json"
    prompts_path = paths.analysis_dir / "image-prompts.json"
    if not xhs_path.exists():
        raise HTTPException(status_code=409, detail={"code": "xhs_post_missing", "message": "Produce article before editing image cards."})
    xhs_post = read_json(xhs_path)
    prompts = read_json(prompts_path) if prompts_path.exists() else {"image_prompts": []}
    cards = payload.get("cards") if isinstance(payload, dict) else None
    if isinstance(cards, list):
        plan_by_page = {item.get("page"): item for item in xhs_post.get("image_plan", []) if isinstance(item, dict)}
        for card in cards:
            if not isinstance(card, dict):
                continue
            page = card.get("page")
            plan_item = plan_by_page.get(page)
            if not plan_item:
                continue
            if card.get("title"):
                plan_item["caption"] = card["title"]
            if card.get("caption"):
                plan_item["content_point"] = card["caption"]
    image_cards = render_image_cards(metadata, assets, xhs_post, keyframes, prompts, paths, style=str(payload.get("style") or "clean"))
    write_json(paths.analysis_dir / "xiaohongshu-post.json", xhs_post)
    store.add_output(project_id, "xhs_post_json", paths.analysis_dir / "xiaohongshu-post.json")
    store.add_output(project_id, "image_cards", paths.analysis_dir / "image-cards.json")
    write_reports(metadata, transcript, keyframes, visual, assets, xhs_post, prompts, paths, _get_existing_project(project_id).warnings, image_cards=image_cards)
    store.add_output(project_id, "xhs_post_md", paths.analysis_dir / "xhs-post.md")
    store.add_output(project_id, "asset_package", paths.analysis_dir / "asset-package.json")
    store.log(project_id, _get_existing_project(project_id).status, "Image cards updated and rerendered.", details={"cards": image_cards.get("card_count", 0)})
    return {"project_id": project_id, "kind": "image_cards", "saved": True, "card_count": image_cards.get("card_count", 0)}


@router.patch("/projects/{project_id}/toutiao-image-cards")
def update_toutiao_image_cards(project_id: str, payload: Dict[str, Any]) -> dict:
    paths = _paths_for_existing_project(project_id)
    metadata, transcript, keyframes, visual, assets = _load_required_project_payloads(project_id)
    post_path = paths.analysis_dir / "toutiao-post.json"
    prompts_path = paths.analysis_dir / "toutiao-image-prompts.json"
    if not post_path.exists():
        raise HTTPException(status_code=409, detail={"code": "toutiao_post_missing", "message": "Produce Toutiao article before editing image cards."})
    post = read_json(post_path)
    prompts = read_json(prompts_path) if prompts_path.exists() else {"image_prompts": []}
    cards = payload.get("cards") if isinstance(payload, dict) else None
    if isinstance(cards, list):
        plan_by_page = {item.get("page"): item for item in post.get("image_plan", []) if isinstance(item, dict)}
        for card in cards:
            if not isinstance(card, dict):
                continue
            page = card.get("page")
            plan_item = plan_by_page.get(page)
            if not plan_item:
                continue
            if card.get("title"):
                plan_item["caption"] = card["title"]
            if card.get("caption"):
                plan_item["content_point"] = card["caption"]
    image_cards = render_image_cards(
        metadata,
        assets,
        post,
        keyframes,
        prompts,
        paths,
        style=str(payload.get("style") or "clean"),
        platform="toutiao",
        output_filename="toutiao-image-cards.json",
        cards_dir=paths.toutiao_cards_dir,
    )
    write_json(paths.analysis_dir / "toutiao-post.json", post)
    store.add_output(project_id, "toutiao_post_json", paths.analysis_dir / "toutiao-post.json")
    store.add_output(project_id, "toutiao_image_cards", paths.analysis_dir / "toutiao-image-cards.json")
    write_reports(metadata, transcript, keyframes, visual, assets, post, prompts, paths, _get_existing_project(project_id).warnings, image_cards=image_cards, platform="toutiao")
    store.add_output(project_id, "toutiao_post_md", paths.analysis_dir / "toutiao-post.md")
    store.add_output(project_id, "asset_package", paths.analysis_dir / "asset-package.json")
    store.log(project_id, _get_existing_project(project_id).status, "Toutiao image cards updated and rerendered.", details={"cards": image_cards.get("card_count", 0)})
    return {"project_id": project_id, "kind": "toutiao_image_cards", "saved": True, "card_count": image_cards.get("card_count", 0)}


@router.post("/projects/{project_id}/rerun/downstream")
def rerun_project_downstream(project_id: str, background_tasks: BackgroundTasks) -> dict:
    existing = _get_existing_project(project_id)
    started, record, missing_inputs = store.try_start_downstream_rerun(project_id)
    if not started:
        if missing_inputs:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "resume_artifacts_missing",
                    "message": "Project is missing required upstream artifacts for downstream rerun.",
                    "status": record.status,
                    "missing": missing_inputs,
                },
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "project_busy",
                "message": "Project is not ready for downstream rerun.",
                "status": record.status,
            },
        )
    _submit_project_task(
        "analyze",
        project_id,
        run_project_downstream_pipeline,
        platform=existing.target_platform,
    )
    return {
        "project_id": project_id,
        "status": "queued",
        "scope": "downstream",
        "platform": existing.target_platform,
    }


@router.post("/projects/{project_id}/rerun/visuals")
def rerun_project_visuals(project_id: str, background_tasks: BackgroundTasks) -> dict:
    existing = _get_existing_project(project_id)
    started, record, missing_inputs = store.try_start_visual_rerun(project_id)
    if not started:
        if missing_inputs:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "resume_artifacts_missing",
                    "message": "Project is missing required upstream artifacts for visual rerun.",
                    "status": record.status,
                    "missing": missing_inputs,
                },
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "project_busy",
                "message": "Project is not ready for visual rerun.",
                "status": record.status,
            },
        )
    _submit_project_task(
        "analyze",
        project_id,
        run_project_visual_pipeline,
        platform=existing.target_platform,
    )
    return {
        "project_id": project_id,
        "status": "queued",
        "scope": "visuals_and_downstream",
        "platform": existing.target_platform,
    }


@router.get("/projects")
def list_projects() -> list:
    return [record.model_dump(mode="json") for record in store.list()]


@router.get("/projects/{project_id}")
def get_project(project_id: str) -> dict:
    return _get_existing_project(project_id).model_dump(mode="json")


@router.delete("/projects/{project_id}")
def delete_project(project_id: str) -> dict:
    paths = _paths_for_existing_project(project_id)
    try:
        record = store.delete(project_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail={"code": "project_busy", "message": str(exc)}) from exc
    archive_dir = paths.project_dir.parent / "_downloads"
    for archive_path in [
        archive_dir / f"{project_id}.zip",
        archive_dir / f"{project_id}_frames.zip",
        archive_dir / f"{project_id}_cards.zip",
        archive_dir / f"{project_id}_toutiao_cards.zip",
    ]:
        archive_path.unlink(missing_ok=True)
    return {"project_id": record.project_id, "deleted": True}


@router.post("/projects/{project_id}/cancel")
def cancel_project(project_id: str) -> dict:
    _get_existing_project(project_id)
    record = store.cancel(project_id)
    execution = task_manager.cancel(project_id)
    if record.error and record.error.get("code") == "user_stopped":
        return {
            "project_id": record.project_id,
            "status": record.status,
            "cancelled": True,
            "error": record.error,
            "execution": execution,
        }
    return {
        "project_id": record.project_id,
        "status": record.status,
        "cancelled": False,
        "message": "Project is not running.",
        "execution": execution,
    }


@router.get("/projects/{project_id}/status")
def get_project_status(project_id: str) -> dict:
    record = _get_existing_project(project_id)
    progress = _build_project_progress(record)
    execution = task_manager.snapshot(project_id)
    current_status_ui = _status_ui_for_step(_status_value(record.status), str(progress.get("platform") or "xhs"))
    produce_missing = store.produce_missing_inputs(project_id)
    can_produce = store.can_start_produce(project_id)
    routes = {}
    for adapter in platform_values():
        can_generate_images = adapter.supports_images and store.can_start_platform_image_generation(project_id, adapter.key)
        image_missing = store.platform_image_generation_missing_inputs(project_id, adapter.key) if adapter.supports_images else []
        routes[adapter.key] = {
            "platform": adapter.key,
            "name": adapter.name,
            "can_produce": can_produce,
            "produce_missing_inputs": produce_missing,
            "supports_image_generation": adapter.supports_images,
            "can_generate_images": can_generate_images,
            "image_generation_missing_inputs": image_missing,
            "automatic_publish": adapter.automatic_publish,
            "authorization_note": "自动发布尚未接入；只支持合法来源分析、平台格式生成和文件导出。",
            "outputs": {
                "post_json": record.outputs.get(adapter.post_json_kind),
                "post_md": record.outputs.get(adapter.post_md_kind),
                "post_docx": record.outputs.get(adapter.post_docx_kind),
                "quality_report": record.outputs.get(adapter.quality_kind),
                "image_prompts": record.outputs.get(adapter.image_prompts_kind) if adapter.supports_images else None,
                "image_cards": record.outputs.get(adapter.image_cards_kind) if adapter.supports_images else None,
            },
        }
    return {
        "project_id": record.project_id,
        "status": record.status,
        "target_platform": record.target_platform,
        "text_only": record.text_only,
        "status_label": current_status_ui["label"],
        "status_description": current_status_ui["description"],
        "updated_at": record.updated_at,
        "logs": [log.model_dump(mode="json") for log in record.logs],
        "error": diagnose_error(record.error) if record.error else None,
        "outputs": record.outputs,
        "warnings": record.warnings,
        "progress": progress,
        "execution": execution,
        "can_cancel": store.can_cancel(project_id),
        "can_rerun_downstream": store.can_start_downstream_rerun(project_id),
        "downstream_rerun_missing_inputs": store.downstream_rerun_missing_inputs(project_id),
        "can_produce": store.can_start_produce(project_id),
        "produce_missing_inputs": store.produce_missing_inputs(project_id),
        "can_generate_images": store.can_start_image_generation(project_id),
        "image_generation_missing_inputs": store.image_generation_missing_inputs(project_id),
        "routes": routes,
        "can_rerun_visuals": store.can_start_visual_rerun(project_id),
        "visual_rerun_missing_inputs": store.visual_rerun_missing_inputs(project_id),
    }


@router.get("/projects/{project_id}/verify")
def verify_project_outputs(project_id: str, require_completed: bool = False) -> dict:
    paths = _paths_for_existing_project(project_id)
    result = verify_runtime_project(paths.project_dir)
    if require_completed and not result["completed_ok"]:
        result = {**result, "required_completed": True}
    return result


@router.get("/projects/{project_id}/files/{kind}")
def get_project_file(project_id: str, kind: str):
    if kind not in FILE_KIND_TO_PATH:
        raise HTTPException(status_code=404, detail="Unsupported file kind")
    record = _get_existing_project(project_id)
    if record.outputs.get(kind) != FILE_KIND_TO_PATH[kind]:
        raise HTTPException(status_code=404, detail="File is not available yet")
    paths = store.paths(project_id)
    file_path = paths.file_for_kind(kind)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File is not available yet")
    if file_path.suffix == ".json":
        return Response(file_path.read_text(encoding="utf-8"), media_type="application/json")
    if file_path.suffix == ".md":
        return Response(file_path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")
    if file_path.suffix == ".docx":
        platform_match = DOCX_KIND_RE.fullmatch(kind)
        platform = platform_match.group(1) if platform_match else "platform"
        return FileResponse(
            file_path,
            filename=f"{platform}-{project_id}-article.docx",
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    return FileResponse(file_path, filename=file_path.name)


@router.get("/projects/{project_id}/download")
def download_project(project_id: str):
    paths = _paths_for_existing_project(project_id)
    if not paths.project_dir.exists():
        raise HTTPException(status_code=404, detail="Project directory not found")

    archive_dir = paths.project_dir.parent / "_downloads"
    archive_dir.mkdir(exist_ok=True)
    archive_base = archive_dir / f"{project_id}"
    zip_path = Path(shutil.make_archive(str(archive_base), "zip", paths.project_dir))
    return FileResponse(zip_path, filename=f"{project_id}.zip", media_type="application/zip")


@router.get("/projects/{project_id}/download/frames")
def download_project_frames(project_id: str):
    paths = _paths_for_existing_project(project_id)

    frame_paths = _registered_frame_paths(project_id)
    if not frame_paths:
        raise HTTPException(status_code=404, detail="No frame images are available yet")

    archive_dir = paths.project_dir.parent / "_downloads"
    archive_dir.mkdir(exist_ok=True)
    zip_path = archive_dir / f"{project_id}_frames.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for frame_path in frame_paths:
            archive.write(frame_path, arcname=f"frames/{frame_path.name}")
    return FileResponse(zip_path, filename=f"{project_id}-frames.zip", media_type="application/zip")


@router.get("/projects/{project_id}/download/cards")
def download_project_cards(project_id: str):
    paths = _paths_for_existing_project(project_id)

    card_paths = _registered_card_paths(project_id)
    if not card_paths:
        raise HTTPException(status_code=404, detail="No image card PNG files are available yet")

    archive_dir = paths.project_dir.parent / "_downloads"
    archive_dir.mkdir(exist_ok=True)
    zip_path = archive_dir / f"{project_id}_cards.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for card_path in card_paths:
            archive.write(card_path, arcname=f"cards/{card_path.name}")
    return FileResponse(zip_path, filename=f"{project_id}-cards.zip", media_type="application/zip")


@router.get("/projects/{project_id}/download/toutiao-cards")
def download_project_toutiao_cards(project_id: str):
    paths = _paths_for_existing_project(project_id)

    card_paths = _registered_platform_card_paths(project_id, "toutiao")
    if not card_paths:
        raise HTTPException(status_code=404, detail="No Toutiao image card PNG files are available yet")

    archive_dir = paths.project_dir.parent / "_downloads"
    archive_dir.mkdir(exist_ok=True)
    zip_path = archive_dir / f"{project_id}_toutiao_cards.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for card_path in card_paths:
            archive.write(card_path, arcname=f"toutiao-cards/{card_path.name}")
    return FileResponse(zip_path, filename=f"{project_id}-toutiao-cards.zip", media_type="application/zip")


@router.get("/projects/{project_id}/frames/{filename}")
def get_project_frame(project_id: str, filename: str):
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not FRAME_FILENAME_RE.fullmatch(filename):
        raise HTTPException(status_code=404, detail="Frame not found")
    frame_path = next((path for path in _registered_frame_paths(project_id) if path.name == filename), None)
    if frame_path is None:
        raise HTTPException(status_code=404, detail="Frame not found")
    return FileResponse(frame_path)


@router.get("/projects/{project_id}/cards/{filename}")
def get_project_card(project_id: str, filename: str):
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not CARD_FILENAME_RE.fullmatch(filename):
        raise HTTPException(status_code=404, detail="Card not found")
    card_path = next((path for path in _registered_card_paths(project_id) if path.name == filename), None)
    if card_path is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return FileResponse(card_path, media_type="image/png")


@router.get("/projects/{project_id}/toutiao-cards/{filename}")
def get_project_toutiao_card(project_id: str, filename: str):
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not CARD_FILENAME_RE.fullmatch(filename):
        raise HTTPException(status_code=404, detail="Card not found")
    card_path = next((path for path in _registered_platform_card_paths(project_id, "toutiao") if path.name == filename), None)
    if card_path is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return FileResponse(card_path, media_type="image/png")
