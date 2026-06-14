# Product Design: Two-Step Video-To-XHS Workbench

## 1. 页面布局

首页 `/dashboard` 是主生产工作台，不再以工程日志为中心。页面采用左侧固定输入面板 + 右侧双产物区：

- 左侧输入区：视频 URL、语言、风格、关键帧数量、Whisper/OCR 开关、两个主按钮。
- 右侧上半区：文字解析结果，面向普通内容创作者阅读。
- 右侧下半区：图文产出结果，展示小红书文章和图文卡片 PNG。

保留 `/projects`、`/projects/:id`、`/settings/llm`、`/settings/runtime`，但本阶段视觉重点在 `/dashboard`。

## 2. 两步式流程设计

Step 1 Analyze：

视频链接 → metadata → transcript → keyframes → OCR/visual → content-assets → `analysis_completed`

用户动作是“一键分析解析”。Analyze 可以在 LLM 未配置时完成基础解析，不能伪造下游小红书文章。

Step 2 Produce：

用户确认/编辑 content-assets → 生成 xiaohongshu-post → image-prompts → image-card PNG → `completed`

用户动作是“一键产出图文”。Produce 必须依赖真实 LLM；LLM 不可用时显示明确错误。

## 3. 左侧输入面板设计

左侧是稳定的生产控制区，宽度约 360px：

- 视频 URL 大输入框。
- 语言：zh / en / auto。
- 内容风格：干货 / 教程 / 测评 / 观点 / 清单。
- 最大关键帧数量。
- Whisper / OCR 开关。
- 主按钮一：一键分析解析。
- 主按钮二：一键产出图文。
- 当前 project_id、状态、最近错误。

按钮状态：

- 未分析：Analyze 可用，Produce disabled。
- Analyze 运行中：Analyze loading，Produce disabled。
- `analysis_completed`：Analyze 可重跑，Produce 可用。
- LLM 未配置：Produce disabled 或点击后明确提示配置 LLM。
- Produce 运行中：Produce loading。
- `completed`：Produce 可重跑，下载可用。

## 4. 右侧文字解析面板设计

文字解析面板不是 JSON dump，按内容生产者阅读顺序组织：

- 视频信息：缩略图、标题、作者、时长、URL。
- 一句话总结。
- 章节结构。
- 核心观点。
- 金句。
- 步骤。
- 受众、痛点、小红书选题角度。
- 来源证据时间点。
- 字幕摘要，可搜索。
- 关键帧摘要：缩略图、时间点、OCR 文本、关联字幕。

支持最小编辑：

- 编辑一句话总结。
- 编辑核心观点。
- 编辑金句。
- 编辑小红书选题角度。
- 保存解析结果。
- 重新分析。
- 确认解析结果。

## 5. 图文产出面板设计

图文产出面板在 Produce 后显示：

- 标题候选。
- 封面文案。
- Hook。
- 正文。
- Hashtags。
- 发布建议。
- 文章 Markdown / JSON 下载。

支持最小编辑：

- 编辑标题候选。
- 编辑封面文案。
- 编辑正文。
- 编辑 hashtags。
- 保存修改。
- 重新生成图文。

## 6. 图片卡片预览设计

每张图文卡片用统一小红书竖图比例，默认 4:5：

- 封面图卡：大标题、视频标题、来源帧视觉区。
- 正文滑图卡片：短句标题、说明、关键帧裁切背景、时间点。
- 总结图卡：总结标题、要点列表、统一背景。

每张卡片显示：

- PNG 预览。
- 页码和用途。
- 图片标题。
- 图片说明。
- 来源关键帧时间。
- 下载按钮。

本阶段用后端模板渲染 PNG，不接真实生图模型；可保留 image_prompt 作为后续生图输入。

## 7. 编辑区设计

编辑不做复杂协同，只做本地项目级保存：

- 使用 textarea / compact cards。
- 保存后 PATCH 对应 JSON。
- Produce 时读取保存后的 content-assets / xhs-post。
- 卡片标题和说明可编辑并重新渲染。

## 8. 下载区设计

Dashboard 和项目详情 Files 都提供：

- xhs-post.md。
- xiaohongshu-post.json。
- asset-package.json。
- image-cards.json。
- 单张 card PNG。
- cards ZIP。
- frames ZIP。
- 完整项目 ZIP。

## 9. 状态设计

Empty：

- 未分析时提示“粘贴视频链接开始分析”。

Loading：

- Analyze 显示当前阶段和 progress log。
- Produce 显示 producing_article / rendering_cards。

Failed：

- 显示后端 error code、message、step。
- 如果已有上游产物，仍可查看解析结果。

Completed：

- Analyze 完成为 `analysis_completed`。
- Produce 完成为 `completed`。

## 10. 组件规范

- `WorkbenchShell`
- `InputRail`
- `StepButtons`
- `AnalysisReadablePanel`
- `EditableAssetSection`
- `TranscriptSummary`
- `KeyframeSummaryStrip`
- `ProducePanel`
- `XhsArticleEditor`
- `ImageCardGallery`
- `DownloadStrip`
- `ProgressConsole`
- `StatusBadge`

整体视觉保持浅色、清爽、高密度，使用 8px 圆角、细边框、紧凑表格和稳定按钮状态。
