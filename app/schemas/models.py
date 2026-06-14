from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl


class ProjectStatus(str, Enum):
    created = "created"
    ingesting = "ingesting"
    transcribing = "transcribing"
    extracting_frames = "extracting_frames"
    analyzing_visuals = "analyzing_visuals"
    planning_content = "planning_content"
    analysis_completed = "analysis_completed"
    writing_xhs = "writing_xhs"
    producing_article = "producing_article"
    xhs_completed = "xhs_completed"
    toutiao_completed = "toutiao_completed"
    rendering_cards = "rendering_cards"
    completed = "completed"
    failed = "failed"


class ProjectCreate(BaseModel):
    url: HttpUrl
    language: str = Field(default="zh", min_length=2, max_length=16)
    style: str = Field(default="干货", max_length=32)
    use_whisper: bool = True
    use_ocr: bool = True
    max_frames: int = Field(default=12, ge=8, le=20)


class ProjectCreated(BaseModel):
    project_id: str
    status: ProjectStatus


class ProjectProduceRequest(BaseModel):
    content_assets: Optional[Dict[str, Any]] = None
    style: Optional[str] = Field(default=None, max_length=32)
    selected_frame_paths: Optional[List[str]] = None
    selected_frame_times: Optional[List[float]] = None
    title_preference: Optional[str] = Field(default=None, max_length=240)
    card_style: Optional[str] = Field(default="clean", max_length=64)


class ProjectImageGenerateRequest(BaseModel):
    style: Optional[str] = Field(default="clean", max_length=64)


class ProgressLog(BaseModel):
    time: str
    status: ProjectStatus
    message: str
    details: Optional[Dict[str, Any]] = None


class ProjectRecord(BaseModel):
    project_id: str
    url: str
    language: str
    style: str
    use_whisper: bool
    use_ocr: bool = True
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


class ImageSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    base_url: Optional[str] = Field(default=None, max_length=512)
    model: Optional[str] = Field(default=None, max_length=128)
    api_key: Optional[str] = Field(default=None, max_length=4096)
    require_api_key: Optional[str] = Field(default=None, max_length=16)
    size: Optional[str] = Field(default=None, max_length=64)
    timeout_ms: Optional[int] = Field(default=None, ge=1000, le=600000)


FILE_KIND_TO_PATH = {
    "metadata": "source/metadata.json",
    "transcript": "transcript/transcript.json",
    "keyframes": "analysis/keyframes.json",
    "visual_analysis": "analysis/visual-analysis.json",
    "content_assets": "analysis/content-assets.json",
    "xhs_post_json": "analysis/xiaohongshu-post.json",
    "xhs_post_md": "analysis/xhs-post.md",
    "image_prompts": "analysis/image-prompts.json",
    "image_cards": "analysis/image-cards.json",
    "toutiao_post_json": "analysis/toutiao-post.json",
    "toutiao_post_md": "analysis/toutiao-post.md",
    "toutiao_image_prompts": "analysis/toutiao-image-prompts.json",
    "toutiao_image_cards": "analysis/toutiao-image-cards.json",
    "asset_package": "analysis/asset-package.json",
    "run_metadata": "analysis/run-metadata.json",
}
