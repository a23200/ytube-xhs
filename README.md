# 视频链接转小红书图文稿生产系统

这是一个真实可运行的生产型项目骨架，不是只调用 LLM 的视频总结 Demo。新版主界面是两步式视频图文生产工作台：

视频链接 → 一键分析解析 → 可读文字解析 / 可编辑内容资产 → 一键产出图文 → 小红书文章 → 独立生图 API 渲染 PNG 图文卡片 → JSON / Markdown / 图片素材下载

## 合规边界

- 只处理用户自有、已授权或公开且允许分析的视频内容。
- 不绕过付费、登录、DRM、地域限制或其他访问控制。
- 不生成侵权搬运内容；小红书文案必须是改写和二次整理，不逐字照搬字幕。
- 写入内容资产和小红书稿前会做本地长段原文复制检测；若总结、观点、金句、正文、开头、封面文案等发布字段包含过长连续来源片段，会结构化失败并要求重写。用于追溯的 `source_text` / `evidence.text` 字段允许保留原文证据。
- `scripts/verify_project.py --require-completed` 会复查完成态 JSON 和 Markdown，避免旧产物或手工修改后的产物绕过防搬运校验。
- 所有产物保留来源 URL、标题、作者、时间点和素材路径，方便追溯。

## 文件结构

```text
app/
  main.py
  api/
    routes.py
  schemas/
    models.py
  services/
    diagnostics.py
    ingest.py
    transcript.py
    frame_extractor.py
    visual_analyzer.py
    content_planner.py
    xhs_writer.py
    image_prompt_writer.py
    image_card_renderer.py
    report_writer.py
    llm_client.py
    pipeline.py
    runtime_store.py
  web/
    index.html
    styles.css
    app.js
runtime/
  projects/
tests/
requirements.txt
requirements-optional.txt
.env.example
```

## 安装

系统依赖：

```bash
brew install ffmpeg
```

Python 依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

如果需要处理无字幕视频或启用 OCR：

```bash
python -m pip install -r requirements-optional.txt
python -m pip install paddlepaddle
```

OCR Provider 支持：

```bash
export XHS_OCR_PROVIDER=auto      # auto | paddleocr | tesseract | none
brew install tesseract            # 可选轻量 OCR fallback
brew install tesseract-lang       # 可选，启用 chi_sim 等更多 OCR 语言
```

复制环境变量：

```bash
cp .env.example .env
```

应用启动时会自动读取项目根目录 `.env`，已有系统环境变量优先级更高。
`XHS_RUNTIME_DIR` 支持绝对路径或相对路径；相对路径会按项目根目录解析，默认写入 `./runtime`。

配置 OpenAI-compatible LLM：

```bash
export BUSINESS_LLM_API_KEY="your_key"
export XHS_LLM_BASE_URL="https://api.openai.com/v1"
export XHS_LLM_MODEL="gpt-4o-mini"
```

如果使用本机 OpenAI-compatible 服务，例如 Ollama 或 vLLM 的 `/v1/chat/completions` 兼容接口，且服务不需要鉴权：

```bash
export XHS_LLM_BASE_URL="http://127.0.0.1:11434/v1"
export XHS_LLM_MODEL="qwen2.5:7b"
export XHS_LLM_REQUIRE_API_KEY=false
```

小上下文本地模型可以调低单次输出上限，避免模型把 JSON 写到上下文窗口末尾：

```bash
export XHS_LLM_MAX_TOKENS=512
```

如果 Homebrew 版 Ollama 推理 runner 不可用，也可以直接用 `llama.cpp` 的 OpenAI-compatible server 读取本地 GGUF：

```bash
llama-server -m /path/to/model.gguf --host 127.0.0.1 --port 8081 -c 4096 -ngl 0 -np 1 --chat-template chatml
export XHS_LLM_BASE_URL="http://127.0.0.1:8081/v1"
export XHS_LLM_MODEL="your-model-alias-or-name"
export XHS_LLM_REQUIRE_API_KEY=false
export XHS_LLM_MAX_TOKENS=512
```

两步式工作台里，LLM 未配置时仍可执行 Analyze：系统会基于真实 metadata、字幕、关键帧和 OCR 生成可读的基础 `content-assets.json`。Produce 必须依赖真实 LLM；未配置或自检失败时会明确报错，不会伪造小红书文章。图片卡片渲染已拆到独立 `generate-images` API，只有小红书稿和图片提示词真实存在后才允许执行。

运行本机能力诊断：

```bash
python scripts/doctor.py
python scripts/doctor.py --require-full
```

默认模式只要求真实上游链路具备运行条件：`yt-dlp`/`ffmpeg` ingest、字幕时间轴和关键帧抽取。`--require-full` 会额外要求 Whisper fallback、OCR Provider 和 LLM 生成都 ready；适合正式跑 completed 验收前使用。诊断输出和 `/api/diagnostics` 一致，会包含 `ffmpeg`、`ffprobe`、`tesseract` 的路径和版本首行，但不会输出密钥明文。

## 启动

本地开发：

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Web UI：

[http://localhost:8000](http://localhost:8000)

可视化操作台路由：

- `/dashboard`：首页即两步式生产工作台，左侧输入视频链接，先“一键分析解析”，确认/编辑后再“一键产出图文”。
- `/projects`：历史项目表格，支持查看、下载 ZIP、删除非运行态项目。
- `/projects/{project_id}`：项目详情页，按 Overview / Transcript / Keyframes / OCR / Content Assets / XHS Post / Files Tabs 查看真实产物。
- `/settings/llm`：文案 LLM 与生图 API 分开配置、自检和最后错误展示；API Key 不在前端回显。
- `/settings/runtime`：运行环境诊断，检查 yt-dlp、ffmpeg、faster-whisper、PySceneDetect、OpenCV、OCR Provider、LLM Provider、生图 Provider 和 runtime 目录权限。

操作台支持：

- 提交视频 URL、语言、内容风格、最大关键帧数量、Whisper 开关和 OCR 开关。
- 点击“一键分析解析”调用真实 Analyze 流程，完成后展示视频信息、一句话总结、章节、核心观点、金句、受众、痛点、选题角度、字幕摘要和关键帧摘要。
- 支持编辑并保存 `content-assets.json`，保存后的内容会作为 Produce 输入。
- 点击“一键产出图文”会先调用真实 Produce 流程生成 `xiaohongshu-post.json`、`xhs-post.md` 和 `image-prompts.json`；文章完成后前端自动调用独立生图 API 渲染 `image-cards.json` 和 `cards/*.png`。
- 自动轮询 `/api/projects/{id}/status`，展示中文状态机、当前阶段、进度条、已用时、预计剩余时间、进度日志、产物数量和错误信息；预计时间按阶段默认耗时与真实已用时动态估算，视频长度、网络、Whisper 和 OCR 会影响实际耗时。
- failed 项目仍可查看已经真实生成的 metadata、transcript、keyframes、visual-analysis 等中间产物。
- 搜索 transcript segments，预览关键帧图片，查看 OCR 文本、视觉摘要、内容资产包、小红书稿和 PNG 图文卡片。
- 编辑标题候选、封面文案、正文、hashtags，以及卡片标题/说明。
- 复制小红书正文，下载完整 ZIP、JSON、Markdown、关键帧素材 ZIP 和图文卡片 ZIP。
- 配置文案 LLM 后可从详情页重跑下游文案生成；配置并启用生图 API 后，出卡片阶段会请求外部 Images API 生成原创底图，再叠加标题说明生成 PNG。
- 未启用生图 API 时，系统仍使用本地 Pillow 模板把关键帧和文案生成 PNG 卡片。
- 运行 doctor、LLM self-test 和 Image self-test，结果来自后端真实接口，不使用 mock 数据。

API 文档：

[http://localhost:8000/docs](http://localhost:8000/docs)

## Mac mini 生产部署

如果要在另一台 Mac mini 上独立运行，不依赖 Codex 或开发机，请使用部署包和 `launchd` 方案：

从 GitHub 公开仓库一键安装：

```bash
YTXHS_REF=macmini-v20260704.6 bash -c "$(curl -fsSL https://raw.githubusercontent.com/a23200/ytube-xhs/macmini-v20260704.6/install-from-github-macos.sh)"
```

或者生成离线部署包：

```bash
./scripts/package_macos_deploy.sh
```

把 `dist/ytube-xhs-macmini-*.tar.gz` 传到目标机器后执行：

```bash
sudo deploy/macos/install_macos.sh --app-dir /opt/ytube-xhs --port 8012 --service-user "$USER"
```

完整步骤见 [`docs/mac-mini-deployment.md`](docs/mac-mini-deployment.md)。生产默认建议端口为 `8012`，开发示例仍使用 `8000`。

安装后常用启动/自检：

```bash
/opt/ytube-xhs/start.sh
/opt/ytube-xhs/start.sh restart
/opt/ytube-xhs/start.sh status
```

终端同步运行一个真实任务：

```bash
python scripts/run_project.py "https://www.youtube.com/watch?v=..." --language zh --style 干货 --max-frames 12
```

如果当前环境没有配置 LLM，但你希望验收真实上游链路和 truthful partial package：

```bash
python scripts/run_project.py "https://www.youtube.com/watch?v=..." --allow-partial
```

该命令会创建 runtime 项目、同步执行完整 pipeline、打印 `project_id`、状态、输出文件登记和 `scripts/verify_project.py` 校验结果。

如果你已经有一个上游链路成功、但因缺少 LLM 或 OCR 失败的历史项目，可以直接复用已有 runtime 产物继续跑：

```bash
# 配置 LLM 后，只重跑内容资产、小红书稿和图片提示词
python scripts/run_project.py --rerun-downstream {project_id}

# 安装或切换 OCR Provider 后，重跑视觉/OCR，并刷新后续内容生成
python scripts/run_project.py --rerun-visuals {project_id}
```

这两个恢复命令同样会输出状态、错误、已登记产物和 verifier 结果；如果缺少必要上游产物，会返回非 0 并报告 `missing_inputs`。

如果服务进程在任务运行中退出，`project.json` 可能停留在 `ingesting`、`transcribing`、`extracting_frames`、`analyzing_visuals`、`planning_content`、`writing_xhs`、`producing_article` 或 `rendering_cards` 等运行态。可以先 dry-run 检查，再把超过阈值的运行态任务标记为结构化失败，并保留已有真实产物：

```bash
python scripts/recover_stale_projects.py --older-than-seconds 3600 --dry-run
python scripts/recover_stale_projects.py --older-than-seconds 3600
```

恢复命令会生成 truthful partial `asset-package.json`，登记磁盘上已经存在的标准产物，之后可继续用 `/status` 查看、用 `/verify` 校验，或在上游产物足够时执行 `--rerun-downstream` / `--rerun-visuals`。

## API

### GET `/api/health`

返回服务存活状态。

### GET `/api/diagnostics`

返回运行环境诊断，不暴露密钥明文。用于确认本机是否具备 ingest、Whisper 转录、关键帧抽取、OCR 和 LLM 生成能力。

示例字段：

```json
{
  "commands": {
    "ffmpeg": {"available": true},
    "ffprobe": {"available": true},
    "tesseract": {"available": true, "version": "tesseract 5.5.2"}
  },
  "modules": {
    "yt_dlp": {"available": true},
    "faster_whisper": {"available": false},
    "paddleocr": {"available": false}
  },
  "llm": {"configured": false, "auth_required": true, "api_key_env": "missing"},
  "ocr": {
    "configured_provider": "auto",
    "tesseract_languages": {
      "available": true,
      "key_languages": {"eng": true, "chi_sim": true, "chi_tra": true, "osd": true}
    }
  },
  "ready_for": {
    "ingest": true,
    "whisper_transcript": true,
    "frame_extraction": true,
    "ocr": true,
    "llm_generation": false
  }
}
```

### GET `/api/system/doctor`

返回与 `/api/diagnostics` 相同的诊断结构，供新版操作台的 `/settings/runtime` 页面使用。

### GET `/api/settings/llm`

返回当前 LLM 配置的脱敏视图：

```json
{
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-4o-mini",
  "api_key_configured": false,
  "api_key_source": null,
  "require_api_key": "auto",
  "auth_required": true,
  "max_tokens": 1200,
  "timeout_ms": 60000,
  "max_chars": 60000
}
```

不会返回 API Key 明文。

### PUT `/api/settings/llm`

保存 OpenAI-compatible LLM 配置到项目根目录 `.env`，并刷新当前进程内的 LLM client。`api_key` 留空表示保持当前密钥；如果传入新 key，接口仍只返回 `api_key_configured`，不回显明文。

请求示例：

```json
{
  "base_url": "http://127.0.0.1:8081/v1",
  "model": "qwen2.5:0.5b",
  "api_key": "",
  "require_api_key": "false",
  "max_tokens": 512,
  "timeout_ms": 60000,
  "max_chars": 60000
}
```

### GET `/api/llm/self-test`

对当前 OpenAI-compatible LLM 配置做极小 JSON 请求自测，不暴露密钥明文。
LLM 请求失败时，结构化错误会脱敏 API key 和 `Bearer ...` token，避免状态接口或日志泄露密钥。

未配置 key 时返回：

```json
{
  "ok": false,
  "error": {
    "code": "llm_unavailable",
    "step": "llm_self_test"
  },
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-4o-mini"
}
```

配置 LLM 后，建议先调用这个接口，确认 `ok=true` 后再运行完整任务或 downstream rerun。

### GET `/api/settings/image`

返回当前生图 API 配置的脱敏视图。生图配置与文案 LLM 配置分离：

```json
{
  "enabled": false,
  "base_url": "",
  "model": "",
  "api_key_configured": false,
  "api_key_source": null,
  "require_api_key": "auto",
  "auth_required": false,
  "size": "1024x1024",
  "timeout_ms": 120000,
  "fallback_renderer": "pillow_template_v1"
}
```

不会返回 API Key 明文。`enabled=false` 时，`POST /api/projects/{id}/generate-images` 会使用本地 Pillow 模板生成 PNG 卡片；`enabled=true` 时会在生图阶段请求 OpenAI-compatible Images API 生成原创底图，再叠加标题和说明。

### PUT `/api/settings/image`

保存外部生图 API 配置到项目根目录 `.env`，并刷新当前进程内的 image provider。`api_key` 留空表示保持当前密钥。

请求示例：

```json
{
  "enabled": true,
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-image-1",
  "api_key": "",
  "require_api_key": "auto",
  "size": "1024x1024",
  "timeout_ms": 120000
}
```

### GET `/api/image/self-test`

对生图配置做轻量自检，不生成实际图片、不暴露密钥。未启用外部生图 API 时会返回 `ok=true`，并说明当前使用本地 `pillow_template_v1` 渲染器。

如果需要确认外部 OpenAI-compatible Images API 真的能出图，可调用：

```bash
curl "http://127.0.0.1:8012/api/image/self-test?real=true"
```

`real=true` 会向外部生图服务发起一次真实 `/images/generations` 请求，并把测试图保存到 `runtime/_self_tests/image-self-test.png`。该请求会产生真实 API 用量；返回结果仍不会暴露 API Key 明文。

### POST `/api/projects`

保留兼容入口：创建项目并按旧完整 pipeline 后台运行。新版工作台优先使用 `POST /api/projects/analyze` 和 `POST /api/projects/{id}/produce` 两步式入口。

请求：

```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "language": "zh",
  "style": "干货",
  "use_whisper": true,
  "use_ocr": true,
  "max_frames": 12
}
```

返回：

```json
{
  "project_id": "abc123",
  "status": "created"
}
```

### POST `/api/projects/analyze`

两步式工作台的第一步。真实执行：

```text
yt-dlp ingest
→ transcript
→ keyframes
→ OCR / visual-analysis
→ source-bound content-assets
→ analysis_completed
```

Analyze 不生成小红书文章，也不要求 LLM 已配置。它会把可读的解析结果写入 `analysis/content-assets.json`，供用户确认或编辑。

请求：

```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "language": "zh",
  "style": "干货",
  "use_whisper": true,
  "use_ocr": true,
  "max_frames": 12
}
```

### POST `/api/projects/{id}/produce`

两步式工作台的 XHS 文章阶段。基于已保存的 `content-assets.json` 真实生成：

```text
xiaohongshu-post.json
→ image-prompts.json
→ asset-package.json
→ xhs-post.md
→ xhs_completed
```

Produce 必须使用真实 OpenAI-compatible LLM；LLM 不可用时会返回结构化错误，不会伪造文章。PNG 图文卡片不在这个接口里生成。

可选请求体：

```json
{
  "content_assets": {},
  "style": "干货",
  "selected_frame_paths": ["frames/frame_0001.jpg"],
  "title_preference": "收藏型标题",
  "card_style": "clean"
}
```

### POST `/api/projects/{id}/generate-images`

独立生图/出卡片阶段。它不会调用 LLM 生成文章，只读取已存在的 `xiaohongshu-post.json`、`image-prompts.json`、关键帧和内容资产，然后用 `ImageCardRenderer` 渲染 PNG：

```text
cards/*.png
→ image-cards.json
→ asset-package.json
→ completed
```

缺少 XHS 文章或图片提示词时返回 409 `image_generation_artifacts_missing`，不会用示例图或 prompt 冒充 PNG。

可选请求体：

```json
{
  "style": "clean"
}
```

### PATCH `/api/projects/{id}/content-assets`

保存用户编辑后的解析结果。会重新校验内容资产结构和来源锚点，确保核心观点仍绑定真实字幕时间点或关键帧。

### PATCH `/api/projects/{id}/xhs-post`

保存用户编辑后的小红书文章 JSON，并同步刷新 `xhs-post.md` 和 `asset-package.json`。

### PATCH `/api/projects/{id}/image-cards`

保存卡片标题/说明等最小编辑，并使用 Pillow 模板重新渲染 `cards/*.png`。

返回 `saved=true` 和重新渲染后的 `card_count`。

### GET `/api/projects`

返回所有任务记录。

### GET `/api/projects/{id}`

返回完整任务记录。

### DELETE `/api/projects/{id}`

删除非运行态项目目录和已生成下载缓存。运行中项目会返回 409 `project_busy`，避免任务仍在写文件时被删除。

### GET `/api/projects/{id}/status`

返回当前状态、进度日志、错误、warning、可用输出、`can_produce`、`produce_missing_inputs`、`can_generate_images`、`image_generation_missing_inputs`、`can_rerun_downstream`、`downstream_rerun_missing_inputs`、`can_rerun_visuals` 和 `visual_rerun_missing_inputs`。
`can_produce=true` 表示任务已完成 Analyze 所需产物，前端可以开放“一键产出图文”。
`can_generate_images=true` 表示小红书文章和图片提示词已经真实生成，前端可以调用独立生图 API 渲染 PNG 卡片。
`can_rerun_downstream=true` 表示任务当前处于 `failed` 或 `completed`，并且 downstream rerun 需要的 `metadata`、`transcript`、`keyframes`、`visual_analysis` 都已经登记且文件存在。
`can_rerun_visuals=true` 表示任务当前处于 `failed` 或 `completed`，并且 visual rerun 需要的 `metadata`、`transcript`、`keyframes` 都已经登记且文件存在。

状态机：

```text
created
ingesting
transcribing
extracting_frames
analyzing_visuals
planning_content
analysis_completed
producing_article
xhs_completed
rendering_cards
completed
failed
```

### GET `/api/projects/{id}/verify`

返回和 `scripts/verify_project.py` 一致的 runtime 产物校验结果，包括：

```json
{
  "ok": true,
  "completed_ok": false,
  "partial_ok": true,
  "missing": [],
  "issues": [],
  "summary": {
    "transcript_segments": 4,
    "keyframe_count": 8,
    "frame_files": 8
  }
}
```

可加 `?require_completed=true` 标记调用方要求完成态验收；未完成时仍返回 200 和完整校验详情，便于 Web UI 展示原因。

### POST `/api/projects/{id}/rerun/downstream`

从已有 runtime 中间产物继续重跑后半段：

```text
metadata + transcript + keyframes + visual-analysis
→ content-assets
→ xiaohongshu-post
→ image-prompts
→ image-cards PNG
→ asset-package + markdown
```

适用场景：

- 首次任务已完成 ingest / transcript / keyframes / visual-analysis，但因为 LLM key 缺失失败。
- 配置 LLM 环境变量后，不想重新下载视频和抽帧。
- 调整 LLM 模型、base URL 或 prompt 后重跑文案生成。

只允许在任务 `failed` 或 `completed` 后发起；如果任务仍在 ingest、transcript、extract、visual analysis、planning 或 writing 阶段，会返回 409 `project_busy`，避免多个任务同时写同一个 runtime 目录。

如果缺少上游文件或输出登记，会在排队前返回 409 `resume_artifacts_missing`，不会进入运行态，也不会伪造下游产物。
重跑开始时会清理旧的下游产物登记和文件，包括 `content-assets.json`、`xiaohongshu-post.json`、`image-prompts.json`、`asset-package.json` 和 `xhs-post.md`，避免新的失败状态继续暴露上一次生成的旧稿。

### POST `/api/projects/{id}/rerun/visuals`

从已有 runtime 中间产物重跑视觉/OCR，并继续刷新后续内容：

```text
metadata + transcript + keyframes
→ visual-analysis
→ content-assets
→ xiaohongshu-post
→ image-prompts
→ image-cards PNG
→ asset-package + markdown
```

适用场景：

- 首次任务运行时没有 PaddleOCR/Tesseract，只生成了明确 OCR warning。
- 后续安装 OCR Provider 或切换 `XHS_OCR_PROVIDER` 后，不想重新下载视频、转录和抽帧。
- 希望用新的 OCR 结果刷新内容资产包、小红书稿和图片提示词。

只允许在任务 `failed` 或 `completed` 后发起；如果任务仍在运行中，会返回 409 `project_busy`。
如果缺少 `metadata`、`transcript` 或 `keyframes`，会返回 409 `resume_artifacts_missing`。
重跑开始时会清理旧的 `visual-analysis.json` 和所有下游产物，重新写入视觉分析；如果后续 LLM 仍未配置，会真实停在 `planning_content`，但保留刚刷新的 `visual-analysis.json` 和 partial package，不会伪造文案产物。

### GET `/api/projects/{id}/files/{kind}`

只返回已经登记在 `project.json.outputs` 的标准产物；磁盘上存在但未登记的旧文件会返回 404，避免状态页和下载内容不一致。

`kind` 支持：

```text
metadata
transcript
keyframes
visual_analysis
content_assets
xhs_post_json
xhs_post_md
image_prompts
image_cards
asset_package
run_metadata
```

### GET `/api/projects/{id}/download`

下载完整 ZIP。

### GET `/api/projects/{id}/download/frames`

下载关键帧图片素材 ZIP。仅当 `keyframes` 已登记在 `project.json.outputs` 后开放，并且只打包 `analysis/keyframes.json` 中登记、位于当前项目 `frames/` 目录内、文件名符合 `frame_0001.jpg` 这类标准格式的关键帧；没有可用关键帧图片时返回 404。

### GET `/api/projects/{id}/frames/{filename}`

读取单张关键帧图片。和素材 ZIP 一样，只有 `keyframes` 产物登记成功，且图片路径出现在当前 `analysis/keyframes.json` 中时才会暴露，避免临时图片或旧图片残留被当成当前任务素材。

### GET `/api/projects/{id}/download/cards`

下载已渲染的图文卡片 PNG ZIP。只有 `image_cards` 已登记且 `cards/*.png` 存在时开放。

### GET `/api/projects/{id}/cards/{filename}`

读取单张图文卡片 PNG。支持 `cover.png`、`summary.png`、`slide_01.png` 这类已登记文件名。

## Runtime 输出

每个任务生成：

```text
runtime/projects/{project_id}/
  project.json
  source/
    metadata.json
    thumbnail.jpg
    subtitles.vtt 或 transcript_source.txt
    audio.mp3
    {video_id}.mp4
  transcript/
    transcript.json
  frames/
    frame_0001.jpg
    frame_0002.jpg
  cards/
    cover.png
    slide_01.png
    summary.png
  analysis/
    keyframes.json
    visual-analysis.json
    content-assets.json
    xiaohongshu-post.json
    image-prompts.json
    image-cards.json
    asset-package.json
    xhs-post.md
    run-metadata.json
```

缩略图会优先从远端下载并规范化为 `source/thumbnail.jpg`；远端缩略图不可用时，会用 ffmpeg 从已下载视频抽一帧作为回退缩略图。无字幕时会抽取 `audio.mp3` 并调用 faster-whisper。若 optional 依赖或模型不可用，任务会进入 `failed`，错误会写入 `project.json` 和 `analysis/run-metadata.json`。
`source/metadata.json` 会保留 yt-dlp 返回的 `available_subtitles`、`automatic_captions` 语言列表，以及 `subtitle_track_summary` 中的字幕/自动字幕数量、语言和格式摘要，便于判断 transcript 使用了原字幕还是需要 Whisper fallback。
Whisper 相关错误会区分依赖缺失、模型加载失败和转录失败：`missing_dependency` 表示未安装 `faster-whisper`，`whisper_model_unavailable` 通常对应模型下载/缓存/设备/compute type 配置问题，`whisper_failed` 表示模型已加载但音频转录失败；错误详情会包含 `model`、`device`、`compute_type`、`language` 和 `audio_file` 便于定位。

Analyze 完成后即使没有 LLM，也会生成基于真实来源的基础 `analysis/content-assets.json` 和 partial `analysis/asset-package.json`。Produce 阶段如果 LLM 失败，系统只保留已经真实生成的上游产物和错误信息，不伪造小红书稿、图片提示词或 PNG 图文卡片。
Produce 成功后状态会先到 `xhs_completed`，表示文章、Markdown 和图片提示词已经完成；随后调用 `POST /api/projects/{id}/generate-images` 才会进入 `rendering_cards` 并生成 PNG 图文卡片，完成后状态为 `completed`。

## 测试

```bash
source .venv/bin/activate
pytest
```

Lint / 语法检查：

```bash
ruff check app tests scripts
python -m compileall app tests scripts
node --check app/web/app.js
```

Runtime 项目产物校验：

```bash
python scripts/doctor.py
python scripts/doctor.py --require-full
python scripts/verify_project.py runtime/projects/{project_id}
python scripts/verify_project.py runtime/projects/{project_id} --require-completed
```

校验脚本会检查：

- 必需文件是否存在。
- `project.json.outputs` 是否只登记已存在的标准产物，并且路径是项目内的相对路径。
- 已存在的标准产物是否都登记到了 `project.json.outputs`，避免 API 暴露状态和磁盘产物不一致。
- `metadata.json` 是否保留标题、作者、URL、视频 ID、时长、本地媒体路径、可用字幕语言列表和自动字幕语言列表；若远端提供缩略图，本地缩略图会规范化为 `source/thumbnail.jpg` 并被校验。
- `transcript.json` 段落数组与 `segment_count` 是否一致。
- `keyframes.json` 的关键帧数量是否和 `frames/frame_*.jpg` 对齐。
- `visual-analysis.json` 是否覆盖每张关键帧。
- 完成态 `content-assets.json` 的核心观点证据是否绑定来源时间点或关键帧路径，`source_evidence` 是否保留来源锚点。
- 完成态来源锚点是否真实：字幕证据时间必须落在字幕段附近，关键帧/OCR/视觉证据时间或路径必须对应已抽取关键帧，避免 LLM 编造不可追溯证据。
- 完成态 `content-assets.json`、`xiaohongshu-post.json` 和 `xhs-post.md` 的发布字段是否包含过长来源原文片段；原文只能作为来源证据保留，不能进入可发布文案。
- 完成态项目是否包含合格的 `content-assets.json`、`xiaohongshu-post.json`、`image-prompts.json`、`image-cards.json`、`cards/*.png`、`asset-package.json` 和 Markdown 章节。
- `image-prompts.json` 是否明确包含构图、主体、背景、色调和文字留白区，并在负向提示词里避免直接复刻截图。

## 真实链路验收建议

1. 找一个你有权处理且可公开访问的 YouTube URL。
2. 启动服务并打开 `/dashboard`。
3. 在左侧粘贴 URL，点击“一键分析解析”。
4. 等状态到 `analysis_completed`，确认右侧能看到视频信息、文字解析、字幕摘要和关键帧摘要。
5. 配置 LLM 并通过 `/settings/llm` 自检。
6. 回到 `/dashboard`，点击“一键产出图文”。
7. 状态会先到 `xhs_completed`，随后前端自动调用独立生图 API；等状态到 `completed`，确认可以预览文章和 PNG 图文卡片。
8. 用 `/api/projects/{id}/status` 或 `/projects/{id}` 查看状态和详情。
9. 验证以下文件存在：

```text
source/metadata.json
transcript/transcript.json
frames/frame_0001.jpg
cards/cover.png
analysis/keyframes.json
analysis/visual-analysis.json
analysis/content-assets.json
analysis/xiaohongshu-post.json
analysis/image-prompts.json
analysis/image-cards.json
analysis/asset-package.json
analysis/xhs-post.md
analysis/run-metadata.json
```

也可以直接运行：

```bash
python scripts/verify_project.py runtime/projects/{project_id} --require-completed
```

## 当前环境已验证链路

在本机用真实公开视频 `https://www.youtube.com/watch?v=jNQXAC9IVRw` 验证过：

- `yt-dlp` 成功下载视频、字幕和缩略图。
- `source/metadata.json` 标题为 `Me at the zoo`，作者为 `jawed`，并保留 yt-dlp 返回的可用字幕和自动字幕语言列表。
- `transcript/transcript.json` 由字幕生成，共 4 段。
- `frames/` 生成 8 张关键帧。
- `analysis/keyframes.json` 和 `analysis/visual-analysis.json` 已生成。
- `faster-whisper` 已安装，并用真实 MP4 强制无字幕路径验证过：ffmpeg 抽取 `audio.mp3`，`faster-whisper:tiny` 生成 2 段转录，产物在 `runtime/projects/whisper_verify/`。
- Tesseract 5.5.2 和 `tesseract-lang` 已安装并作为轻量 OCR fallback 运行；当前未安装 PaddleOCR，但 Tesseract 已具备 `eng`、`chi_sim`、`chi_tra`、`osd` 等语言数据。真实项目 `2ff838435e8c` 已用 `python scripts/run_project.py --rerun-visuals 2ff838435e8c --allow-partial` 刷新到 `ocr_provider=tesseract`、8 帧视觉分析，当前 OCR warning 为空。
- 本地 `llama.cpp` `llama-server` 已用 `qwen2.5:0.5b` GGUF 权重验证 OpenAI-compatible `/v1/chat/completions`，配置为 `XHS_LLM_BASE_URL=http://127.0.0.1:8081/v1`、`XHS_LLM_REQUIRE_API_KEY=false`、`XHS_LLM_MAX_TOKENS=512`。
- `python scripts/doctor.py --require-full` 在上述本地 LLM 配置下通过，`ready_for.llm_generation=true`。
- 已从现有真实中间产物执行 `python scripts/run_project.py --rerun-downstream 2ff838435e8c`，生成 `content-assets.json`、`xiaohongshu-post.json`、`image-prompts.json`、`image-cards.json`、`cards/*.png`、`asset-package.json` 和 `xhs-post.md`。
- `python scripts/verify_project.py runtime/projects/2ff838435e8c --require-completed` 已通过，`completed_ok=true`、`missing=[]`、`issues=[]`。
- `/api/projects/{id}/download` 已验证可下载 ZIP，包含视频、字幕、关键帧和分析文件。
- `/api/projects/{id}/download/frames` 可单独下载关键帧图片素材 ZIP。
- `/api/projects/{id}/download/cards` 可单独下载图文卡片 PNG ZIP。
- 已用本地 FastAPI 服务对真实项目 `2ff838435e8c` 验证过 API：`/status`、`/verify`、`/files/metadata`、`/files/xhs_post_json`、`/files/xhs_post_md`、`/files/image_cards`、`/frames/frame_0001.jpg`、`/cards/cover.png`、`/download/frames`、`/download/cards` 和 `/download` 均可用。
- 已用浏览器验证两步式工作台：`/dashboard` 左侧可提交真实 YouTube URL，“一键分析解析”后能创建项目并展示真实中间产物；`/projects/2ff838435e8c` 的 Transcript / Keyframes / OCR / Content Assets / XHS Post / Files Tabs 均能读取真实产物，`/settings/llm` 和 `/settings/runtime` 可打开并读取真实后端接口。
- Analyze 模式在未配置 LLM 时可以停在 `analysis_completed` 并生成基础 `content-assets.json`；Produce 阶段若 LLM 不可用会明确失败，不会伪造文章或卡片。
- 已实现 `POST /api/projects/{id}/rerun/downstream`，配置 LLM 后可从已有中间产物继续生成下游内容，无需重新 ingest。
- 已实现 `POST /api/projects/{id}/rerun/visuals` 和 CLI `--rerun-visuals`，安装 OCR 后可复用已有关键帧刷新视觉分析和后续内容。
- 已用真实项目执行 `python scripts/run_project.py --rerun-visuals 2ff838435e8c --allow-partial`，确认可复用已有 metadata、字幕和 8 张标准关键帧刷新 Tesseract `visual-analysis.json`。
- 已实现 `scripts/recover_stale_projects.py`，服务意外退出后可把卡在运行态的旧任务恢复为可查看、可校验、可重跑的 truthful failed 状态。
- 已用终端同步入口验证真实链路：`python scripts/run_project.py "https://www.youtube.com/watch?v=jNQXAC9IVRw" --max-frames 8 --allow-partial` 生成项目 `runtime/projects/2ff838435e8c/`，后续补齐 LLM 后通过 completed 验收。

## 已实现能力

- `yt-dlp` 真实下载视频、字幕、缩略图并保存 metadata。
- 字幕优先，支持 VTT/SRT/ASS/SSA/JSON3，以及 YouTube SRV XML、TTML/DFXP/XML 字幕清洗、去重和短句合并。
- 无字幕时抽取音频并调用 faster-whisper；依赖不可用时结构化失败。
- PySceneDetect + ffmpeg 抽帧，OpenCV 过滤黑屏、低清和重复帧。
- OCR Provider 抽象，支持 PaddleOCR、Tesseract fallback 和禁用模式；不可用时写 warning，不伪造 OCR。
- 视觉分析会为每张关键帧写入 OpenCV 计算的分辨率、亮度、清晰度和色调指标；第一版不伪造物体识别或视觉模型摘要。
- OpenAI-compatible LLM Provider，支持超时、重试、JSON 解析修复。
- `POST /api/projects/analyze` 两步式解析入口：不依赖 LLM，产出可读解析和可编辑 `content-assets.json`。
- `POST /api/projects/{id}/produce` 文章产出入口：依赖真实 LLM，产出小红书文章、Markdown 和图片提示词，完成状态为 `xhs_completed`。
- `POST /api/projects/{id}/generate-images` 独立生图入口：读取已生成文章、图片提示词和关键帧，调用 `ImageCardRenderer` 渲染 PNG 卡片。
- `ImageCardRenderer` 默认使用 Pillow 后端模板生成 1080x1350、4:5 的 `cover.png`、`slide_*.png`、`summary.png`；如果启用 `XHS_IMAGE_ENABLED=true`，会先请求外部 OpenAI-compatible Images API 生成原创底图，再做本地版式合成。
- LLM 下游产物有结构合约校验：核心观点必须有证据，图片计划必须绑定来源帧或内容点，图片提示词必须包含构图、参考和负向提示词字段。
- 内容资产、小红书稿写入前有长段来源文本逐字搬运拦截，离线 verifier 也会复查 JSON 和 Markdown 完成态产物，避免把字幕原文直接塞进发布字段。
- 生成 `content-assets.json`、`xiaohongshu-post.json`、图片提示词、`image-cards.json`、PNG 图文卡片、资产包和 Markdown；其中文章与 PNG 卡片由两个独立 API 阶段产出。
- Product Design 新版两步式设计方案已记录在 `docs/product-design-two-step-workbench.md`，前端按“视频图文内容生产工作台”落地。
- FastAPI 状态机、进度日志、文件下载和多路由可视化操作台。
- `/api/diagnostics` / `/api/system/doctor` 运行环境诊断。
- `/api/settings/llm` LLM 配置读写和 `/api/llm/self-test` 自检，不回显 API Key 明文。
- `/api/settings/image` 生图 API 配置读写和 `/api/image/self-test` 自检，不回显 API Key 明文。
- 下游重跑 API，可从已有真实中间产物恢复 LLM 生成步骤。
- CLI 支持 `--rerun-downstream` 和 `--rerun-visuals`，便于在服务外恢复已有项目。

## 风险点

- YouTube 可访问性受网络、地区和平台策略影响；系统不会绕过限制。
- `paddlepaddle` 安装方式与平台有关，建议按官方命令安装匹配 wheel。
- `faster-whisper` 首次运行会下载模型，耗时和磁盘占用取决于模型大小；如果生产环境无法联网，需提前准备模型缓存并设置合适的 `XHS_WHISPER_MODEL`、`XHS_WHISPER_DEVICE`、`XHS_WHISPER_COMPUTE_TYPE`。
- LLM 输出质量依赖你配置的模型；模型必须支持中文和较长上下文。
- 长视频处理时间较长，当前任务在进程内后台执行，下一阶段应接入队列。
- 当前项目已在 Python 3.9 上跑通基础测试，但 `yt-dlp` 提示 Python 3.9 支持即将弃用；建议生产环境使用 Python 3.10+。
- macOS 上同时安装 OpenCV 和 PyAV/faster-whisper 依赖时，可能出现 FFmpeg 动态库重复加载 warning；本机测试可继续运行，生产环境建议用干净 Python 3.10+ 虚拟环境或容器固定依赖。

## 下一阶段计划

- 增加 Celery/RQ 或 Dramatiq 队列，支持任务取消和并发控制。
- 增加视觉模型 Provider，用关键帧生成更强的画面摘要和物体识别。
- 增加本地 Ollama / vLLM Provider。
- 增加字幕语言选择和多字幕合并。
- 增加项目清理、重跑单步、批量导出和团队审稿流。
