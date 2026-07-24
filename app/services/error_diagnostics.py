from __future__ import annotations

import json
from typing import Any, Dict, Iterable


STAGE_META: Dict[str, tuple[str, str]] = {
    "batch": ("批量调度", "app/services/batch_manager.py"),
    "queued": ("任务排队", "app/services/task_manager.py"),
    "created": ("任务创建", "app/services/runtime_store.py"),
    "ingest": ("视频采集", "app/services/ingest.py / yt-dlp"),
    "ingesting": ("视频采集", "app/services/ingest.py / yt-dlp"),
    "media_probe": ("媒体探测", "app/services/media_utils.py / ffprobe"),
    "transcribing": ("字幕与转录", "app/services/transcript.py"),
    "extracting_frames": ("关键帧提取", "app/services/frame_extractor.py / ffmpeg"),
    "analyzing_visuals": ("OCR 与画面分析", "app/services/visual_analyzer.py"),
    "planning_content": ("创作底稿生成", "app/services/content_planner.py / LLM"),
    "producing_article": ("平台文章生成", "app/services/xhs_writer.py / LLM"),
    "writing_xhs": ("平台文章生成", "app/services/xhs_writer.py / LLM"),
    "validating_content": ("文章质量校验", "app/services/article_quality.py"),
    "rendering_cards": ("图片卡片生成", "app/services/image_card_renderer.py / image API"),
    "llm_self_test": ("LLM 自检", "app/services/llm_client.py"),
}


def _rule(
    title: str,
    cause: str,
    solutions: Iterable[str],
    *,
    retryable: bool = True,
) -> Dict[str, Any]:
    return {
        "title": title,
        "cause": cause,
        "solutions": list(solutions),
        "retryable": retryable,
    }


ERROR_RULES: Dict[str, Dict[str, Any]] = {
    "youtube_bot_check_required": _rule(
        "YouTube 要求人机验证",
        "当前网络出口或 Cookie 会话被 YouTube 判定需要登录确认。公开视频也可能触发。",
        [
            "在运行服务的同一 macOS 用户下登录 Chrome 的 YouTube，并确认视频可正常播放。",
            "打开“平台账号”，为 YouTube 导入或上传最新 Netscape cookies.txt，并用失败链接验证。",
            "若有效 Cookie 仍被拒绝，切换家庭宽带或手机热点后重试。",
        ],
    ),
    "yt_dlp_cookies_required": _rule(
        "平台要求新鲜 Cookie",
        "平台提取器明确要求近期浏览器 Cookie，链接公开不代表媒体接口允许匿名抓取。",
        [
            "打开“平台账号”，选择对应来源平台并从目标 Mac 的浏览器导入。",
            "launchd 无法读取浏览器钥匙串时，上传最新 Netscape cookies.txt。",
            "确认 Cookie 来自目标平台已登录且能播放该链接的浏览器会话。",
        ],
    ),
    "yt_dlp_cookies_invalid": _rule(
        "Cookie 无效或无法读取",
        "Cookie 已过期、格式错误、浏览器配置目录不匹配，或 launchd 用户无权读取浏览器数据。",
        [
            "在“平台账号”查看失效平台，重新导入或上传 Netscape cookies.txt。",
            "从浏览器导入失败时检查浏览器/Profile；macOS 钥匙串受限时改用文件上传。",
            "用同一失败链接点击“验证 Cookie”，确认实际 extractor 和底层错误。",
        ],
    ),
    "youtube_media_download_forbidden": _rule(
        "YouTube 媒体流返回 403",
        "页面可能公开，但视频分片 URL 对当前 Cookie、客户端或网络出口拒绝访问。",
        [
            "更新 Cookie 后重试。",
            "切换网络/IP；若仅需文案，优先使用有公开字幕的视频。",
            "运行固定更新脚本更新 yt-dlp 和项目后再复测。",
        ],
    ),
    "yt_dlp_access_forbidden": _rule(
        "平台请求返回 403",
        "平台网页、接口或媒体请求拒绝当前客户端，常见于 Cookie、请求签名或网络出口限制。",
        [
            "查看“实际错误”确认是网页、接口还是媒体地址返回 403。",
            "更新目标平台 Cookie，并切换网络/IP 复测。",
            "运行固定更新脚本获取最新提取器；若仍失败，保留链接和原始错误用于适配。",
        ],
    ),
    "yt_dlp_rate_limited": _rule(
        "平台请求被限流",
        "目标平台返回 429 或明确的请求过于频繁提示。",
        [
            "暂停批量提交一段时间后重试。",
            "降低并发并切换稳定网络/IP。",
            "使用目标平台正常浏览器会话导出的最新 Cookie。",
        ],
    ),
    "yt_dlp_precondition_failed": _rule(
        "平台返回 412 前置条件失败",
        "平台反自动化校验拒绝了当前 Cookie、客户端指纹或网络会话；B站较常见。",
        [
            "在“平台账号”更新对应来源平台 Cookie，并用同一链接验证。",
            "确认浏览器能正常播放该链接；仍返回 412 时切换网络/IP 并更新 yt-dlp。",
            "查看实际错误确认专用 extractor 已选中，不要把 412 当成链接私有。",
        ],
    ),
    "yt_dlp_unsupported_url": _rule(
        "链接格式不受支持",
        "yt-dlp 没有识别该 URL，可能是复制了中间页、失效短链或平台页面格式已变化。",
        [
            "在浏览器打开链接并复制跳转后的标准视频详情页 URL。",
            "运行固定更新脚本更新 yt-dlp 后重试。",
            "若标准详情页仍失败，记录链接和实际错误以补充平台适配。",
        ],
        retryable=False,
    ),
    "source_url_missing": _rule(
        "没有识别到视频链接",
        "提交内容中没有 HTTP/HTTPS URL。",
        ["粘贴 YouTube、抖音、哔哩哔哩或今日头条的视频详情页/分享链接。"],
        retryable=False,
    ),
    "source_url_invalid": _rule(
        "视频链接格式无效",
        "链接缺少有效协议或主机名，无法进入平台解析。",
        ["在浏览器打开视频后重新复制完整地址，确保以 http:// 或 https:// 开头。"],
        retryable=False,
    ),
    "source_url_redirect_timeout": _rule(
        "平台短链跳转超时",
        "抖音、B站或头条短链在受控重试后仍未返回最终视频详情页，尚未进入 yt-dlp 平台提取器。",
        [
            "查看实际错误中的短链、重试次数和超时秒数。",
            "在浏览器打开短链，复制跳转后的标准视频详情页 URL 再提交。",
            "若浏览器同样打开缓慢，再检查网络、DNS、代理或平台可达性。",
        ],
    ),
    "source_url_redirect_failed": _rule(
        "平台短链解析失败",
        "短链返回 HTTP 错误、连接失败或无有效最终地址。",
        ["浏览器打开短链确认未失效；复制最终详情页 URL，或切换网络后重试。"],
    ),
    "source_url_redirect_mismatch": _rule(
        "短链跳转到非预期平台",
        "最终地址不属于原短链平台，系统为避免错误 Cookie 注入而停止。",
        ["确认链接未失效或被重定向到广告/登录中间页，再复制标准视频详情页。"],
        retryable=False,
    ),
    "source_url_platform_mismatch": _rule(
        "链接平台识别不一致",
        "URL 规范化前后的来源平台不一致。",
        ["使用标准视频详情页 URL；保留原链接和规范化 URL 用于修复平台规则。"],
        retryable=False,
    ),
    "yt_dlp_wrong_extractor": _rule(
        "yt-dlp 选中了错误提取器",
        "系统已识别来源平台，但 yt-dlp 没有使用对应的 YouTube/Douyin/BiliBili/Toutiao 提取器。",
        [
            "查看实际 extractor、规范化 URL 和来源平台，确认不是搜索页、活动页或登录中间页。",
            "运行固定更新脚本更新 yt-dlp；标准详情页仍失败时保留错误用于适配。",
        ],
    ),
    "yt_dlp_generic_extractor_timeout": _rule(
        "平台链接被 Generic 提取器处理并超时",
        "已知平台 URL 没有进入平台专用提取器；常见原因是短链未正确跳转、复制了中间页或 yt-dlp 提取器过旧。",
        [
            "先查看规范化 URL 和 normalized_host，确认它确实是平台标准详情页。",
            "运行固定更新脚本更新 yt-dlp，并在“平台账号”验证该平台 Cookie。",
            "若实际错误仍以 [generic] 开头，保留链接和完整错误用于补充 URL 适配，不要仅增加超时。",
        ],
    ),
    "yt_dlp_extractor_changed": _rule(
        "平台页面结构已变化",
        "提取器无法找到预期字段，通常是平台更新了网页或接口。",
        [
            "运行固定更新脚本更新 yt-dlp 和项目。",
            "确认使用标准视频详情页而不是搜索页或活动页。",
            "保留实际错误和 URL，用于更新对应平台提取逻辑。",
        ],
    ),
    "yt_dlp_format_unavailable": _rule(
        "请求的视频格式不存在",
        "页面能读取，但当前格式选择器没有匹配到可下载格式。",
        [
            "更新 yt-dlp 后重试。",
            "用 yt-dlp --list-formats 检查该链接实际提供的格式。",
            "若是公开视频，保留格式列表以调整项目格式选择器。",
        ],
    ),
    "yt_dlp_network_timeout": _rule(
        "平台请求超时",
        "平台专用提取器在页面、字幕、音频或媒体阶段经过受控重试后仍超时。",
        [
            "查看 details.retry.phase、attempts 和 normalized_url，确认具体超时阶段。",
            "在“平台账号”验证该来源平台 Cookie，确认浏览器可稳定播放同一链接。",
            "只有浏览器也超时时再检查 DNS、代理、防火墙、网络出口或平台限流。",
        ],
    ),
    "youtube_network_tls_failed": _rule(
        "YouTube TLS 请求失败",
        "当前运行环境无法完成到 YouTube 网页或 API 的 TLS 连接。",
        [
            "切换网络/IP 后重试。",
            "检查系统时间、DNS、代理和证书链。",
            "配置有效 cookies.txt 后再次测试。",
        ],
    ),
    "yt_dlp_update_required": _rule(
        "yt-dlp 版本被平台拒绝",
        "平台响应表明当前提取器或客户端版本过旧。",
        ["运行固定更新脚本，再重启服务并重试。"],
    ),
    "yt_dlp_failed": _rule(
        "yt-dlp 未分类失败",
        "原始错误尚未命中已知分类。此错误本身不代表链接私有。",
        [
            "先查看“实际错误”，不要只依据 yt_dlp_failed 判断原因。",
            "将项目 ID、URL 和完整实际错误用于补充分类型规则。",
            "按实际错误中的 HTTP 状态、Cookie、格式或 extractor 提示处理后重试。",
        ],
    ),
    "yt_dlp_network_error": _rule(
        "平台网络连接失败",
        "连接被重置、拒绝、DNS 解析失败或网络不可达。",
        ["检查 DNS、代理、防火墙和网络出口；确认浏览器能打开链接后重试。"],
    ),
    "yt_dlp_no_info": _rule(
        "平台未返回视频信息",
        "提取器没有得到可用视频对象，或集合/播放列表中没有可处理条目。",
        ["使用单条标准视频详情页 URL，更新 yt-dlp，并查看实际 extractor 错误。"],
    ),
    "yt_dlp_impersonation_unavailable": _rule(
        "yt-dlp 浏览器模拟不可用",
        "配置了 XHS_YTDLP_IMPERSONATE，但运行环境没有 curl-cffi 等模拟依赖。",
        ["安装对应可选依赖，或清除 XHS_YTDLP_IMPERSONATE 后重启。"],
        retryable=False,
    ),
    "drm_protected": _rule("内容受 DRM 保护", "媒体使用 DRM，项目不会绕过。", ["改用有合法下载/分析权限的无 DRM 来源。"], retryable=False),
    "region_restricted": _rule("内容存在地区限制", "平台明确限制当前地区访问。", ["在内容合法可用的地区和网络环境中处理。"], retryable=False),
    "login_required": _rule("内容需要登录或权限", "平台要求登录、会员、付费或所有者权限。", ["仅使用有权访问的账号 Cookie；项目不会绕过权限。"], retryable=False),
    "copyright_restricted": _rule("内容受版权可用性限制", "平台明确返回版权限制。", ["改用已授权且可处理的来源。"], retryable=False),
    "douyin_public_share_unavailable": _rule(
        "抖音公开分享页无法解析",
        "短链未解析出视频 ID，或公开分享页未暴露可用视频数据。",
        [
            "在浏览器打开短链，复制跳转后的标准 /video/数字 URL。",
            "更新项目后重试，并检查该链接是否已删除或仅登录可见。",
            "若浏览器可看但仍失败，保留页面 URL 和实际错误用于适配。",
        ],
    ),
    "douyin_public_media_download_failed": _rule(
        "抖音公开媒体下载失败",
        "分享页已解析，但公开播放地址返回异常内容、超时或拒绝访问。",
        [
            "更新 Cookie 或切换网络后重试。",
            "确认浏览器中该视频未删除、未限时且可以完整播放。",
            "查看实际错误中的 Content-Type 或 HTTP 错误定位媒体接口。",
        ],
    ),
    "no_transcript_source": _rule(
        "没有可用字幕或音频",
        "视频没有可下载字幕，并且没有可供 Whisper 转录的本地音视频文件。",
        [
            "启用 Whisper，并确保 ffmpeg、ffprobe 和 faster-whisper 已安装。",
            "更新 Cookie 以允许下载音频，或改用有公开字幕的视频。",
        ],
        retryable=False,
    ),
    "subtitle_parse_failed": _rule(
        "字幕文件解析失败",
        "下载到的字幕格式、编码或内容不符合解析器预期。",
        [
            "查看实际错误和字幕文件扩展名。",
            "更新项目后重试；必要时启用 Whisper 从音频重新转录。",
        ],
    ),
    "whisper_model_unavailable": _rule(
        "Whisper 模型不可用",
        "模型未下载、缓存损坏或当前网络无法下载模型。",
        [
            "确认 faster-whisper 已安装，并预先下载配置的模型。",
            "检查服务用户的模型缓存目录权限和可用磁盘空间。",
        ],
    ),
    "whisper_failed": _rule(
        "Whisper 转录失败",
        "音频解码、模型加载、内存或转录过程发生错误。",
        [
            "查看实际错误，确认是音频、模型还是内存问题。",
            "用 ffprobe 检查本地媒体，确认 ffmpeg 可解码。",
            "关闭其他重任务或改用更小的 Whisper 模型后重试。",
        ],
    ),
    "media_file_missing": _rule("本地媒体文件缺失", "下载或回退完成后没有找到可用音视频文件。", ["查看 source 目录、格式选择和 yt-dlp 实际错误后重新 Analyze。"]),
    "audio_file_missing": _rule("本地音频文件缺失", "纯文案音频下载完成后没有找到音频文件。", ["检查格式选择、source 目录和媒体下载错误，必要时使用低清视频回退。"]),
    "video_file_missing": _rule("本地视频文件缺失", "提取器返回成功，但项目目录中没有可用视频。", ["查看 yt-dlp 输出文件名、格式和磁盘空间后重新 Analyze。"]),
    "empty_transcript": _rule("转录结果为空", "字幕或 Whisper 没有生成有效文本段。", ["确认视频含可听语音、语言配置正确且音频未损坏。"]),
    "llm_unavailable": _rule(
        "LLM 未配置",
        "缺少 API Key、Base URL、模型名或客户端依赖。",
        ["在 LLM 配置页填写有效配置，先通过 LLM 自检，再重跑文案。"],
        retryable=False,
    ),
    "llm_authentication_failed": _rule(
        "LLM 鉴权失败",
        "API Key 无效、模型无权限、账户状态异常或 Base URL 与密钥不匹配。",
        [
            "在 LLM 配置页重新填写 Key、Base URL 和模型名。",
            "先运行 LLM 自检；确认 HTTP 401/403 已消失后再提交批量任务。",
            "检查账户余额、模型访问权限和接口供应商状态。",
        ],
        retryable=False,
    ),
    "llm_rate_limited": _rule(
        "LLM 限流",
        "接口额度、每分钟请求数或并发限制已触发。",
        ["降低并发，等待限流窗口恢复；检查账户额度后重跑文案。"],
    ),
    "llm_timeout": _rule(
        "LLM 响应超时",
        "模型在配置的超时时间内没有返回完整响应。",
        [
            "检查接口延迟和网络稳定性。",
            "在 LLM 设置中适当增加超时时间，但不要降低文章质量要求。",
            "减少同时运行的 Produce 任务后重试。",
        ],
    ),
    "llm_network_error": _rule(
        "无法连接 LLM 接口",
        "Base URL、DNS、TLS、代理或上游服务不可达。",
        ["先运行 LLM 自检，按实际网络错误检查 Base URL、DNS、TLS 和上游状态。"],
    ),
    "llm_http_error": _rule(
        "LLM 接口返回 HTTP 错误",
        "上游拒绝请求、服务异常、模型名错误或账户额度不足。",
        ["查看实际错误中的 HTTP 状态和脱敏响应摘要，修正配置或等待上游恢复。"],
    ),
    "llm_request_failed": _rule(
        "LLM 请求重试后仍失败",
        "请求没有命中更具体的网络、鉴权、限流或 HTTP 分类。",
        ["查看实际错误中的 error_type、HTTP 状态和响应摘要，再按对应上游问题处理。"],
    ),
    "llm_response_invalid": _rule(
        "LLM 响应结构无效",
        "接口返回成功，但内容不是兼容的 chat/completions 响应或正文为空。",
        ["确认 Base URL 和模型兼容 OpenAI chat/completions；先通过 LLM 自检。"],
    ),
    "llm_json_parse_failed": _rule(
        "LLM 返回内容无法解析为 JSON",
        "模型输出和自动 JSON 修复结果都不是合法 JSON。",
        ["查看脱敏的 raw/repaired 摘要，确认模型支持稳定 JSON 输出后重跑。"],
    ),
    "llm_contract_invalid": _rule(
        "LLM 产物字段不完整",
        "LLM 返回了 JSON，但缺少必填字段、列表项或字段类型不正确。系统不会用不完整产物继续。",
        [
            "查看定位中的 artifact、field、index 和 missing_fields。",
            "系统会自动定向修复；仍失败时先检查模型是否严格遵循 JSON 指令，再重跑对应阶段。",
            "不要手工删除质量约束；缺失字段必须由模型补齐或人工在创作底稿中补齐。",
        ],
    ),
    "source_anchor_invalid": _rule(
        "来源证据锚点无效",
        "LLM 给出的字幕时间或关键帧路径无法对应真实采集产物。",
        ["查看 artifact、field、index 和错误时间/路径；重跑文案让模型仅使用现有字幕和关键帧锚点。"],
    ),
    "scene_detection_failed": _rule("场景检测失败", "OpenCV 无法打开视频或读取有效画面。", ["用 ffprobe/ffmpeg 检查媒体完整性和编码，重新下载后再 Analyze。"]),
    "no_valid_keyframes": _rule("没有有效关键帧", "视频解码或场景筛选后没有可用画面。", ["检查视频编码和时长；仅需文章时启用纯文案模式。"]),
    "no_valid_visual_frames": _rule("没有可分析画面", "关键帧文件不存在、损坏或无法读取。", ["重跑视觉；若关键帧本身缺失，重新 Analyze。"]),
    "duration_unavailable": _rule("无法读取媒体时长", "ffprobe 没有返回有效 duration。", ["查看 ffprobe 输出，确认媒体文件完整且 ffprobe 已安装。"]),
    "body_too_short": _rule(
        "文章字数不足",
        "质量校验发现正文有效字符少于目标平台下限。",
        [
            "查看 actual_chars 与 minimum_chars 的差值。",
            "系统已执行定向扩写；仍不足时重跑文案并检查上游字幕是否过短或 LLM 输出是否被截断。",
            "如响应经常截断，检查 LLM max_tokens 和供应商输出上限。",
        ],
    ),
    "body_too_long": _rule(
        "文章字数超限",
        "正文有效字符超过目标平台上限。",
        ["重跑文案进行定向压缩；不要直接截断，以免破坏事实和段落。"],
    ),
    "verbatim_source_copy_detected": _rule(
        "检测到较长原文照搬",
        "可发布字段连续复用了来源原文，未达到二次创作要求。",
        ["查看 matched_fragment 和 field；系统会定向改写，仍失败时重跑文案而不是关闭原创度校验。"],
    ),
    "rewrite_degree_below_target": _rule(
        "估算改写程度低于 70%",
        "生成内容与来源文本的可解释相似度过高。",
        ["重跑文案，让模型重新组织叙事、句式和观点顺序；不得通过删除证据或降低阈值规避。"],
    ),
    "subheading_detected": _rule(
        "正文包含小标题",
        "文章出现 Markdown、序号、加粗或独占一行的小标题。",
        ["系统会把小标题含义融入连续自然段；仍失败时重跑文案。"],
    ),
    "hook_missing": _rule("开头钩子缺失", "LLM 没有生成 hook。", ["重跑文案并检查模型结构遵循能力。"]),
    "hook_too_long": _rule("开头钩子超过 100 字", "hook 超出硬性长度限制。", ["重跑文案进行定向压缩。"]),
    "hook_lacks_contrast": _rule("开头缺少反差或冲突", "hook 未检测到真实反差、冲突或悬念结构。", ["重跑文案定向改写开头。"]),
    "mechanical_hook": _rule("开头表达机械化", "hook 使用了模板化引导语。", ["重跑文案，改为基于来源事实的口语化开场。"]),
    "repeated_sentence_detected": _rule("正文存在重复句", "质量校验检测到重复表达。", ["重跑文案合并重复信息。"]),
    "ungrounded_numeric_claim": _rule("数据表达无法回溯来源", "文章中的比例、百分比或人数无法与来源证据匹配。", ["删除或改写无依据数字，只保留来源可验证的数据口径。"]),
    "missing_dependency": _rule(
        "缺少本地依赖",
        "运行阶段找不到必需命令或 Python 模块。",
        [
            "查看 details.command 或 dependency 确认缺失项。",
            "运行固定更新脚本安装/修复依赖，然后执行 manage.sh doctor。",
        ],
        retryable=False,
    ),
    "image_api_disabled": _rule("生图功能已关闭", "XHS_IMAGE_ENABLED 未启用。", ["需要生图时启用并配置图片 API；纯文案任务无需处理。"], retryable=False),
    "image_api_unconfigured": _rule("生图 API 未配置", "缺少图片接口 Base URL、模型或密钥。", ["在设置页补齐图片 API 并通过自检。"], retryable=False),
    "image_api_unavailable": _rule("生图客户端不可用", "缺少 HTTP 客户端依赖。", ["运行固定更新脚本安装依赖。"], retryable=False),
    "image_api_request_failed": _rule("生图 API 请求失败", "图片接口超时、拒绝请求或返回无效图片。", ["查看实际 HTTP 错误、模型、额度和响应摘要后重试。"]),
    "text_only_image_generation_disabled": _rule("纯文案任务不生成图片", "该任务主动启用了 text_only。", ["需要图片时新建非纯文案任务。"], retryable=False),
    "command_timeout": _rule("本地命令超时", "ffmpeg、ffprobe 或其他子进程超过限定时间。", ["查看 args 和 timeout，检查媒体损坏、磁盘和系统负载后重试。"]),
    "command_failed": _rule("本地命令执行失败", "子进程返回非零退出码。", ["查看实际错误中的 stderr、returncode 和 args，修复依赖或媒体问题后重试。"]),
    "resume_artifacts_missing": _rule("重跑所需产物缺失", "上游 metadata、transcript、keyframes 或 visual-analysis 文件不存在。", ["从 Analyze 重新执行，不能只重跑下游文案。"], retryable=False),
    "produce_artifacts_missing": _rule("出稿所需产物缺失", "Analyze 阶段的必需产物未生成或文件已丢失。", ["查看 missing 列表，从 Analyze 重新运行缺失阶段。"], retryable=False),
    "image_generation_artifacts_missing": _rule("生图所需产物缺失", "平台稿、提示词或上游分析产物不完整。", ["先完成对应平台 Produce，再执行生图。"], retryable=False),
    "stale_running_project": _rule(
        "任务进程中断后被恢复器标记失败",
        "服务重启、Mac 休眠/关机、进程退出或长时间无状态更新，使任务超过恢复阈值。",
        [
            "查看 previous_status、updated_at 和服务日志，确认中断发生在哪个阶段。",
            "若 Analyze 产物齐全，使用“重跑文案”；若上游产物缺失，重新 Analyze。",
            "检查 launchd/uvicorn 日志、系统休眠和磁盘空间，避免再次中断。",
        ],
    ),
    "worker_process_failed": _rule(
        "隔离任务进程异常退出",
        "执行 Analyze/Produce 的子进程非正常退出，常见于进程崩溃、系统杀进程或启动导入错误。",
        ["查看项目 worker 日志路径和实际错误；检查内存、依赖和服务日志后重试。"],
    ),
    "task_worker_failed": _rule("任务工作线程异常", "任务调度线程捕获到未处理异常。", ["查看 actual_error、scope 和 platform，修复对应组件后重试。"]),
    "batch_item_failed": _rule("批量条目调度失败", "批量管理器未能创建、启动、等待或归档该项目。", ["查看 actual_error 和项目 ID；进入单项目详情确认失败阶段。"]),
    "batch_worker_failed": _rule("批量调度器异常", "批次循环发生未处理异常。", ["查看 actual_error，重启服务后重新提交未完成链接。"]),
    "unexpected_error": _rule("未处理的程序异常", "代码路径抛出了未转换为 PipelineError 的异常。", ["保留项目 ID、异常类型和实际错误；检查服务日志并修复对应组件。"]),
    "project_failed": _rule("项目未生成 Word", "子项目结束时没有完成目标平台稿。", ["进入项目详情查看更早的真实错误和已有产物。"]),
    "batch_stopped": _rule("批次已停止", "用户停止了批次，当前及待处理条目不会继续。", ["需要继续时重新提交未完成 URL。"], retryable=False),
    "batch_stopped_after_error": _rule("批次因单条失败停止", "continue_on_error=false，首个失败后后续条目被跳过。", ["修复首个失败，或启用“单条失败后继续”重新提交。"], retryable=False),
    "user_stopped": _rule("任务已被用户停止", "用户点击了强制停止。", ["检查已有产物；需要继续时新建任务或使用可用的重跑入口。"], retryable=False),
}


DEFAULT_RULE = _rule(
    "处理失败",
    "该错误尚未建立专用说明。",
    ["查看错误码、阶段、实际错误和 details；保留项目 ID 后按具体组件排查。"],
)


def _clip(value: Any, limit: int = 4000) -> str:
    if isinstance(value, str):
        text = value.strip()
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _first_value(details: Dict[str, Any], fields: Iterable[str]) -> str:
    for field in fields:
        value = details.get(field)
        if value not in (None, "", [], {}):
            return _clip(value)
    return ""


def _quality_actual(details: Dict[str, Any]) -> str:
    report = details.get("quality_report")
    if not isinstance(report, dict):
        return ""
    violations = report.get("violations") or []
    parts = []
    for item in violations[:5]:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "quality_error")
        message = str(item.get("message") or "")
        parts.append(f"{code}: {message}" if message else code)
    return "; ".join(parts)


def _actual_error(message: str, details: Dict[str, Any]) -> str:
    actual = _first_value(
        details,
        (
            "error",
            "stderr",
            "response_excerpt",
            "raw",
            "repaired",
            "reason",
        ),
    )
    if actual:
        return actual
    if details.get("actual_extractor"):
        return (
            f"actual_extractor={_clip(details.get('actual_extractor'))}; "
            f"source_platform={_clip(details.get('source_platform'))}; "
            f"normalized_url={_clip(details.get('normalized_url'))}"
        )
    quality = _quality_actual(details)
    if quality:
        return quality
    missing_fields = details.get("missing_fields") or details.get("missing")
    if missing_fields:
        return f"missing: {_clip(missing_fields)}"
    return message


def _location(step: str, details: Dict[str, Any]) -> Dict[str, Any]:
    stage_label, component = STAGE_META.get(step, (step or "未知阶段", "未定位组件"))
    result: Dict[str, Any] = {
        "step": step or None,
        "stage_label": stage_label,
        "component": component,
    }
    for field in (
        "platform",
        "source_platform",
        "normalized_host",
        "actual_extractor",
        "artifact",
        "field",
        "index",
        "evidence_index",
        "missing_fields",
        "missing",
        "command",
        "returncode",
        "previous_status",
    ):
        value = details.get(field)
        if value not in (None, "", [], {}):
            result[field] = value
    return result


def diagnose_error(error: Any) -> Dict[str, Any]:
    if not isinstance(error, dict):
        original = error
        error = {
            "code": "unexpected_error",
            "message": str(original),
            "step": None,
            "details": {"type": type(original).__name__},
        }
    payload = dict(error)
    code = str(payload.get("code") or "unexpected_error")
    message = str(payload.get("message") or "处理失败。")
    step = str(payload.get("step") or "")
    details = dict(payload.get("details") or {})
    rule = ERROR_RULES.get(code, DEFAULT_RULE)
    payload["code"] = code
    payload["message"] = message
    payload["step"] = step or None
    payload["details"] = details
    payload["diagnostic"] = {
        "title": rule["title"],
        "cause": rule["cause"],
        "actual_error": _actual_error(message, details),
        "location": _location(step, details),
        "solutions": list(rule["solutions"]),
        "retryable": bool(rule["retryable"]),
    }
    return payload


def error_catalog() -> list[Dict[str, Any]]:
    return [
        {
            "code": code,
            "title": rule["title"],
            "cause": rule["cause"],
            "solutions": list(rule["solutions"]),
            "retryable": bool(rule["retryable"]),
        }
        for code, rule in sorted(ERROR_RULES.items())
    ]
