# 错误诊断与解决方案

项目错误采用统一结构，并同时写入单项目 `project.json`、批次 `batch.json` 和状态 API：

```json
{
  "code": "llm_contract_invalid",
  "message": "LLM output for content-assets.json is missing required fields.",
  "step": "planning_content",
  "details": {
    "artifact": "content-assets.json",
    "missing_fields": ["source_evidence"]
  },
  "diagnostic": {
    "title": "LLM 产物字段不完整",
    "actual_error": "missing: [\"source_evidence\"]",
    "location": {},
    "solutions": []
  }
}
```

页面会明确显示错误码、错误阶段、代码组件、实际错误、缺失字段/产物和解决方案。`GET /api/errors/catalog` 可读取当前版本的完整错误目录。

## 1. 视频采集与平台限制

| 错误码 | 实际含义 | 处理方式 |
| --- | --- | --- |
| `youtube_bot_check_required` | YouTube 要求登录确认不是机器人 | 到“平台账号”导入/上传 YouTube Cookie，用失败链接验证；仍失败时切换网络/IP |
| `yt_dlp_cookies_required` | 平台明确要求近期 Cookie | 到“平台账号”选择来源平台，从本机浏览器导入或上传 Netscape `cookies.txt` |
| `yt_dlp_cookies_invalid` | Cookie 过期、格式错误或服务用户无法读取 | 查看平台状态并重新导入；launchd 无法读取钥匙串时改用文件上传 |
| `youtube_media_download_forbidden` | YouTube 视频分片返回 403 | 更新 Cookie、切换网络；纯文案任务优先选有公开字幕的视频 |
| `yt_dlp_access_forbidden` | 平台网页、API 或媒体请求返回 403 | 查看 `actual_error` 确认被拒绝的请求，再更新 Cookie/yt-dlp 或切换网络 |
| `yt_dlp_rate_limited` | 平台返回 429/请求过快 | 暂停请求、降低并发、换稳定网络后重试 |
| `yt_dlp_precondition_failed` | 平台返回 HTTP 412，B站反自动化校验常见 | 在“平台账号”更新并验证 B站 Cookie；浏览器可播但仍失败时切换网络并更新 yt-dlp |
| `yt_dlp_unsupported_url` | URL 未被提取器识别 | 打开链接后复制跳转后的标准视频详情页 URL，并更新 yt-dlp |
| `source_url_redirect_timeout` / `source_url_redirect_failed` | 平台短链未解析到最终详情页 | 查看短链重试详情；浏览器打开后复制最终标准 URL；只有浏览器也失败时再排查网络 |
| `source_url_redirect_mismatch` / `source_url_platform_mismatch` | 跳转或规范化后的域不属于原平台 | 检查失效短链、广告/登录中间页，改用标准详情页 URL |
| `yt_dlp_wrong_extractor` | 已知平台 URL 选中了错误提取器 | 查看 `actual_extractor`、`normalized_url`，更新 yt-dlp 并保留标准链接用于适配 |
| `yt_dlp_generic_extractor_timeout` | 平台链接被 `[generic]` 处理并超时 | 先修复 URL/短链或提取器选择，不要只提高超时；再验证该平台 Cookie |
| `yt_dlp_extractor_changed` | 平台页面结构改变 | 运行固定更新脚本；保留 URL 与完整实际错误用于适配 |
| `yt_dlp_format_unavailable` | 格式选择器没有匹配到媒体 | 更新 yt-dlp，并通过 `yt-dlp --list-formats URL` 获取真实格式 |
| `yt_dlp_network_timeout` / `yt_dlp_network_error` | 专用提取器在页面、字幕、音频或媒体阶段连接失败 | 查看 `details.retry.phase` 和每次底层错误，再验证 Cookie；仅真实网络失败时检查 DNS/代理/防火墙 |
| `douyin_public_share_unavailable` | 抖音短链或公开分享页没有可用结构化数据 | 使用跳转后的 `/video/数字` 标准链接；确认内容未删除且浏览器可播放 |
| `douyin_public_media_download_failed` | 分享页解析成功，但播放地址下载失败 | 查看实际 HTTP/Content-Type 错误，更新 Cookie或切换网络 |
| `yt_dlp_failed` | 未命中已有分类 | 必须查看 `diagnostic.actual_error`；它不代表公开 URL 是私有视频 |

“平台账号”写入的独立 Cookie 会被下一次任务直接读取，无需重启。只有手工修改 `.env` 中的旧版全局 Cookie 环境变量时才需要重启服务。

## 2. 字幕、音频和 Whisper

| 错误码 | 处理方式 |
| --- | --- |
| `no_transcript_source` | 没有字幕，也没有可供 Whisper 使用的本地媒体；启用 Whisper 并先解决媒体下载 |
| `subtitle_parse_failed` | 查看字幕扩展名和实际解析错误；必要时改用 Whisper |
| `whisper_model_unavailable` | 检查模型缓存、网络、磁盘、device 和 compute type |
| `whisper_failed` | 用 `ffprobe` 验证媒体；查看音频解码、内存或模型运行错误 |
| `media_file_missing` / `audio_file_missing` / `video_file_missing` | yt-dlp 返回完成但本地文件不存在；查看 source 目录、格式和下载日志 |

## 3. LLM 与创作底稿

| 错误码 | 处理方式 |
| --- | --- |
| `llm_unavailable` | 填写 API Key、Base URL、模型名；先通过 LLM 自检 |
| `llm_authentication_failed` | 按 HTTP 401/403 检查 Key、模型权限、余额和 Base URL |
| `llm_rate_limited` | 等待限流窗口、降低 Produce 并发、检查额度 |
| `llm_timeout` | 检查接口延迟；适当提高 `XHS_LLM_TIMEOUT_MS`，不要降低质量规则 |
| `llm_network_error` | 检查 DNS、TLS、代理和上游服务状态 |
| `llm_http_error` / `llm_request_failed` | 查看 HTTP 状态、`error_type` 和脱敏 `response_excerpt` |
| `llm_response_invalid` | 接口不是兼容的 `/chat/completions` 响应或正文为空 |
| `llm_json_parse_failed` | 原始输出和 JSON 修复都失败；检查模型 JSON 遵循能力 |
| `llm_contract_invalid` | 查看 `artifact`、`field`、`index`、`missing_fields`；系统会自动定向修复两次，仍失败才终止 |
| `source_anchor_invalid` | LLM 使用了不存在的字幕时间或关键帧路径；系统会定向修复，不能伪造锚点 |

`content-assets.json` 结构修复只补齐字段、类型和真实来源锚点，不会生成本地模板文章，也不会放宽质量要求。

## 4. 文章质量校验

| 错误码 | 说明与处理 |
| --- | --- |
| `body_too_short` | 对比 `actual_chars` 与 `minimum_chars`；系统已最多定向扩写两次，仍失败时检查字幕长度、`max_tokens` 和模型截断 |
| `body_too_long` | 定向压缩，不能直接截断正文 |
| `verbatim_source_copy_detected` | 查看 `field` 和 `matched_fragment`，继续原创改写，不能关闭防搬运校验 |
| `rewrite_degree_below_target` | 估算改写程度低于 70%，需重组叙事、句式和观点顺序 |
| `subheading_detected` | 正文出现小标题，需把标题含义融入连续自然段 |
| `hook_missing` / `hook_too_long` | 开头缺失或超过 100 字 |
| `hook_lacks_contrast` / `mechanical_hook` | 开头缺少真实反差或使用模板化引导语 |
| `repeated_sentence_detected` | 合并重复句和重复观点 |
| `ungrounded_numeric_claim` | 删除无法回溯来源的百分比、比例或人数 |

这些规则属于完成条件。批量任务不会把不合格文章标记成功，也不会通过降低字数、原创度或结构要求来“优化成功率”。

## 5. 依赖、产物和任务进程

| 错误码 | 处理方式 |
| --- | --- |
| `missing_dependency` | 查看 `command`/`dependency`，运行固定更新脚本，再执行 `manage.sh doctor` |
| `command_failed` | 查看 `args`、`returncode` 和 `stderr` |
| `command_timeout` | 检查媒体损坏、磁盘、CPU/内存和命令超时值 |
| `resume_artifacts_missing` | 缺少上游文件，必须重新 Analyze |
| `produce_artifacts_missing` | 查看 `missing`，补齐 Analyze 产物后再 Produce |
| `image_generation_artifacts_missing` | 先完成平台稿和图片提示词 |
| `worker_process_failed` | 查看项目 `logs/worker.err.log`、返回码和服务日志 |
| `stale_running_project` | 服务重启、Mac 休眠/关机或进程中断后超过恢复阈值；按 `previous_status` 和已有产物选择重跑文案或重新 Analyze |
| `batch_item_failed` / `batch_worker_failed` | 查看该条项目 ID、实际错误和批量调度阶段 |
| `unexpected_error` | 保留项目 ID、异常类型、实际错误和服务日志，用于代码修复 |

常用检查命令：

```bash
/opt/ytube-xhs/deploy/macos/manage.sh status
/opt/ytube-xhs/deploy/macos/manage.sh doctor
/opt/ytube-xhs/deploy/macos/manage.sh logs
curl -fsS http://127.0.0.1:8012/api/diagnostics | python3 -m json.tool
curl -fsS http://127.0.0.1:8012/api/llm/self-test | python3 -m json.tool
```

固定更新命令：

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/a23200/ytube-xhs/main/update-macos.sh)"
```
