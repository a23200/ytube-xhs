from dataclasses import dataclass
from typing import Dict, Iterable

from app.schemas.models import ProjectStatus


@dataclass(frozen=True)
class PlatformAdapter:
    key: str
    name: str
    content_type: str
    min_body_chars: int
    max_body_chars: int
    length_guidance: str
    style_guidance: str
    post_filename: str
    markdown_filename: str
    docx_filename: str
    quality_filename: str
    completed_status: ProjectStatus
    supports_images: bool
    source_analysis: str
    automatic_publish: bool = False

    @property
    def post_json_kind(self) -> str:
        return "xhs_post_json" if self.key == "xhs" else f"{self.key}_post_json"

    @property
    def post_md_kind(self) -> str:
        return "xhs_post_md" if self.key == "xhs" else f"{self.key}_post_md"

    @property
    def post_docx_kind(self) -> str:
        return f"{self.key}_post_docx"

    @property
    def quality_kind(self) -> str:
        return f"{self.key}_quality_report"

    @property
    def image_prompts_kind(self) -> str:
        return "image_prompts" if self.key == "xhs" else f"{self.key}_image_prompts"

    @property
    def image_cards_kind(self) -> str:
        return "image_cards" if self.key == "xhs" else f"{self.key}_image_cards"

    @property
    def image_prompts_filename(self) -> str:
        return "image-prompts.json" if self.key == "xhs" else f"{self.key}-image-prompts.json"

    @property
    def image_cards_filename(self) -> str:
        return "image-cards.json" if self.key == "xhs" else f"{self.key}-image-cards.json"

    def output_kinds(self, *, include_images: bool = True) -> list[str]:
        kinds = [self.post_json_kind, self.post_md_kind, self.post_docx_kind, self.quality_kind]
        if include_images and self.supports_images:
            kinds.extend([self.image_prompts_kind, self.image_cards_kind])
        return kinds


PLATFORMS: Dict[str, PlatformAdapter] = {
    "xhs": PlatformAdapter(
        key="xhs",
        name="小红书",
        content_type="可直接发布的生活化原创文章",
        min_body_chars=800,
        max_body_chars=1400,
        length_guidance="正文必须达到 800 至 1400 个有效字符，以自然短段落组织；不足 800 字不能作为完成稿。",
        style_guidance="信息密度高、口语化、适合移动端阅读和收藏转发，避免种草套话与表情符号堆砌。",
        post_filename="xiaohongshu-post.json",
        markdown_filename="xhs-post.md",
        docx_filename="xhs-article.docx",
        quality_filename="xhs-quality-report.json",
        completed_status=ProjectStatus.xhs_completed,
        supports_images=True,
        source_analysis="公开链接可通过项目的合法公开提取链路分析；登录内容需要用户授权 Cookie。",
    ),
    "toutiao": PlatformAdapter(
        key="toutiao",
        name="今日头条",
        content_type="可直接发布的资讯型原创文章",
        min_body_chars=1200,
        max_body_chars=2200,
        length_guidance="正文必须达到 1200 至 2200 个有效字符，以连续自然段展开；不足 1200 字不能作为完成稿。",
        style_guidance="标题清楚，正文重事实、背景、因果和读者关切，少用营销语气，不使用章节小标题。",
        post_filename="toutiao-post.json",
        markdown_filename="toutiao-post.md",
        docx_filename="toutiao-article.docx",
        quality_filename="toutiao-quality-report.json",
        completed_status=ProjectStatus.toutiao_completed,
        supports_images=True,
        source_analysis="公开链接按平台公开访问能力处理；需要登录或官方授权的内容不会绕过风控。",
    ),
    "douyin": PlatformAdapter(
        key="douyin",
        name="抖音",
        content_type="可直接使用的短视频口播文章与发布文案",
        min_body_chars=500,
        max_body_chars=1000,
        length_guidance="正文必须达到 500 至 1000 个有效字符，节奏紧凑，但仍保持完整事实和自然段；不足 500 字不能作为完成稿。",
        style_guidance="口语、短句、画面感强，先抛冲突再解释，避免口号、夸张标题党和生硬专业术语。",
        post_filename="douyin-post.json",
        markdown_filename="douyin-post.md",
        docx_filename="douyin-article.docx",
        quality_filename="douyin-quality-report.json",
        completed_status=ProjectStatus.douyin_completed,
        supports_images=False,
        source_analysis="仅处理可合法公开访问或用户已授权的抖音链接；不绕过登录、风控或地区限制。",
    ),
    "bilibili": PlatformAdapter(
        key="bilibili",
        name="哔哩哔哩",
        content_type="可直接发布的动态或专栏型原创文章",
        min_body_chars=1000,
        max_body_chars=2000,
        length_guidance="正文必须达到 1000 至 2000 个有效字符，以连贯自然段解释事实和观点；不足 1000 字不能作为完成稿。",
        style_guidance="表达真诚、信息扎实、专业概念讲人话，保留必要上下文，不使用报告式章节小标题。",
        post_filename="bilibili-post.json",
        markdown_filename="bilibili-post.md",
        docx_filename="bilibili-article.docx",
        quality_filename="bilibili-quality-report.json",
        completed_status=ProjectStatus.bilibili_completed,
        supports_images=False,
        source_analysis="支持合法公开的哔哩哔哩链接分析；会员、登录、付费或受限内容需要官方授权且不会被绕过。",
    ),
}


def get_platform(platform: str) -> PlatformAdapter:
    key = str(platform or "").strip().lower()
    try:
        return PLATFORMS[key]
    except KeyError:
        raise ValueError(f"Unsupported platform: {platform}") from None


def platform_keys() -> tuple[str, ...]:
    return tuple(PLATFORMS)


def platform_values() -> Iterable[PlatformAdapter]:
    return PLATFORMS.values()


def public_platform_capabilities() -> list[dict]:
    return [
        {
            "key": item.key,
            "name": item.name,
            "content_type": item.content_type,
            "body_length": {"min_chars": item.min_body_chars, "max_chars": item.max_body_chars},
            "length_guidance": item.length_guidance,
            "supports": {
                "source_analysis": item.source_analysis,
                "content_generation": True,
                "markdown_export": True,
                "json_export": True,
                "docx_export": True,
                "image_generation": item.supports_images,
                "automatic_publish": item.automatic_publish,
            },
            "authorization_note": (
                "自动发布尚未接入。若后续启用，只能使用平台官方开放接口和用户自己的有效授权。"
                if not item.automatic_publish
                else "自动发布需要平台官方授权。"
            ),
        }
        for item in platform_values()
    ]
