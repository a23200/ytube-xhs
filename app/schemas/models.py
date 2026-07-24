from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.services.source_urls import extract_http_url

SUPPORTED_TARGET_PLATFORMS = ("xhs", "toutiao", "douyin", "bilibili")
TargetPlatform = Literal["xhs", "toutiao", "douyin", "bilibili"]
SourcePlatform = Literal["youtube", "douyin", "bilibili", "toutiao"]


class ProjectStatus(str, Enum):
    queued = "queued"
    created = "created"
    ingesting = "ingesting"
    transcribing = "transcribing"
    extracting_frames = "extracting_frames"
    analyzing_visuals = "analyzing_visuals"
    planning_content = "planning_content"
    analysis_completed = "analysis_completed"
    writing_xhs = "writing_xhs"
    producing_article = "producing_article"
    validating_content = "validating_content"
    xhs_completed = "xhs_completed"
    toutiao_completed = "toutiao_completed"
    douyin_completed = "douyin_completed"
    bilibili_completed = "bilibili_completed"
    rendering_cards = "rendering_cards"
    completed = "completed"
    stopped = "stopped"
    failed = "failed"


class ProjectCreate(BaseModel):
    url: HttpUrl
    target_platform: TargetPlatform = "xhs"
    language: str = Field(default="zh", min_length=2, max_length=16)
    style: str = Field(default="干货", max_length=32)
    use_whisper: bool = True
    use_ocr: bool = True
    text_only: bool = False
    max_frames: int = Field(default=12, ge=8, le=20)

    @field_validator("url", mode="before")
    @classmethod
    def extract_shared_url(cls, value: object) -> str:
        return extract_http_url(value)


class ProjectCreated(BaseModel):
    project_id: str
    status: ProjectStatus
    target_platform: TargetPlatform


class ProjectProduceRequest(BaseModel):
    content_assets: Optional[Dict[str, Any]] = None
    style: Optional[str] = Field(default=None, max_length=32)
    selected_frame_paths: Optional[List[str]] = None
    selected_frame_times: Optional[List[float]] = None
    title_preference: Optional[str] = Field(default=None, max_length=240)
    card_style: Optional[str] = Field(default="clean", max_length=64)


class ProjectImageGenerateRequest(BaseModel):
    style: Optional[str] = Field(default="clean", max_length=64)


class BatchStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    completed_with_errors = "completed_with_errors"
    stopped = "stopped"
    failed = "failed"


class BatchItemStatus(str, Enum):
    pending = "pending"
    analyzing = "analyzing"
    producing = "producing"
    completed = "completed"
    failed = "failed"
    stopped = "stopped"
    skipped = "skipped"


class BatchCreate(BaseModel):
    urls: List[HttpUrl] = Field(min_length=1, max_length=50)
    target_platform: TargetPlatform = "xhs"
    language: str = Field(default="zh", min_length=2, max_length=16)
    style: str = Field(default="干货", max_length=32)
    use_whisper: bool = True
    use_ocr: bool = False
    text_only: bool = True
    max_frames: int = Field(default=12, ge=8, le=20)
    continue_on_error: bool = True

    @field_validator("urls", mode="before")
    @classmethod
    def extract_shared_urls(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)):
            return value
        return [extract_http_url(item) for item in value]


class BatchItem(BaseModel):
    index: int = Field(ge=1)
    url: str
    status: BatchItemStatus = BatchItemStatus.pending
    project_id: Optional[str] = None
    title: Optional[str] = None
    document_filename: Optional[str] = None
    error: Optional[Dict[str, Any]] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class BatchLog(BaseModel):
    time: str
    status: BatchStatus
    message: str
    details: Optional[Dict[str, Any]] = None


class BatchRecord(BaseModel):
    batch_id: str
    target_platform: TargetPlatform
    language: str
    style: str
    use_whisper: bool
    use_ocr: bool
    text_only: bool
    max_frames: int
    continue_on_error: bool
    status: BatchStatus
    created_at: str
    updated_at: str
    current_index: Optional[int] = None
    total_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    stopped_count: int = 0
    skipped_count: int = 0
    document_count: int = 0
    items: List[BatchItem] = Field(default_factory=list)
    logs: List[BatchLog] = Field(default_factory=list)
    error: Optional[Dict[str, Any]] = None


class BatchCreated(BaseModel):
    batch_id: str
    status: BatchStatus
    total_count: int
    queue_position: int = 0


class ProgressLog(BaseModel):
    time: str
    status: ProjectStatus
    message: str
    details: Optional[Dict[str, Any]] = None


class ProjectRecord(BaseModel):
    project_id: str
    url: str
    target_platform: TargetPlatform = "xhs"
    language: str
    style: str
    use_whisper: bool
    use_ocr: bool = True
    text_only: bool = False
    max_frames: int
    status: ProjectStatus
    created_at: str
    updated_at: str
    logs: List[ProgressLog] = Field(default_factory=list)
    error: Optional[Dict[str, Any]] = None
    outputs: Dict[str, str] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    source: str
    importance: float = Field(default=0.0, ge=0.0, le=1.0)


class Keyframe(BaseModel):
    time: float
    path: str
    score: float
    reason: str
    related_transcript_text: str = ""


class LLMSettingsUpdate(BaseModel):
    base_url: Optional[str] = Field(default=None, max_length=512)
    model: Optional[str] = Field(default=None, max_length=128)
    api_key: Optional[str] = Field(default=None, max_length=4096)
    require_api_key: Optional[str] = Field(default=None, max_length=16)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=64000)
    timeout_ms: Optional[int] = Field(default=None, ge=1000, le=600000)
    max_chars: Optional[int] = Field(default=None, ge=1000, le=2000000)
    retry_attempts: Optional[int] = Field(default=None, ge=1, le=10)


class ImageSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    base_url: Optional[str] = Field(default=None, max_length=512)
    model: Optional[str] = Field(default=None, max_length=128)
    api_key: Optional[str] = Field(default=None, max_length=4096)
    require_api_key: Optional[str] = Field(default=None, max_length=16)
    size: Optional[str] = Field(default=None, max_length=64)
    timeout_ms: Optional[int] = Field(default=None, ge=1000, le=600000)


class CookieBrowserImportRequest(BaseModel):
    platform: SourcePlatform
    browser: str = Field(default="chrome", min_length=1, max_length=32)
    profile: Optional[str] = Field(default=None, max_length=256)


class CookieVerifyRequest(BaseModel):
    platform: SourcePlatform
    url: str = Field(min_length=8, max_length=4096)

    @field_validator("url", mode="before")
    @classmethod
    def extract_verification_url(cls, value: object) -> str:
        return extract_http_url(value)


FILE_KIND_TO_PATH = {
    "metadata": "source/metadata.json",
    "transcript": "transcript/transcript.json",
    "keyframes": "analysis/keyframes.json",
    "visual_analysis": "analysis/visual-analysis.json",
    "content_assets": "analysis/content-assets.json",
    "xhs_post_json": "analysis/xiaohongshu-post.json",
    "xhs_post_md": "analysis/xhs-post.md",
    "xhs_post_docx": "analysis/xhs-article.docx",
    "xhs_quality_report": "analysis/xhs-quality-report.json",
    "image_prompts": "analysis/image-prompts.json",
    "image_cards": "analysis/image-cards.json",
    "toutiao_post_json": "analysis/toutiao-post.json",
    "toutiao_post_md": "analysis/toutiao-post.md",
    "toutiao_post_docx": "analysis/toutiao-article.docx",
    "toutiao_quality_report": "analysis/toutiao-quality-report.json",
    "toutiao_image_prompts": "analysis/toutiao-image-prompts.json",
    "toutiao_image_cards": "analysis/toutiao-image-cards.json",
    "douyin_post_json": "analysis/douyin-post.json",
    "douyin_post_md": "analysis/douyin-post.md",
    "douyin_post_docx": "analysis/douyin-article.docx",
    "douyin_quality_report": "analysis/douyin-quality-report.json",
    "bilibili_post_json": "analysis/bilibili-post.json",
    "bilibili_post_md": "analysis/bilibili-post.md",
    "bilibili_post_docx": "analysis/bilibili-article.docx",
    "bilibili_quality_report": "analysis/bilibili-quality-report.json",
    "asset_package": "analysis/asset-package.json",
    "run_metadata": "analysis/run-metadata.json",
}
