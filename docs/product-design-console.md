# Product Design: Video-to-XHS Content Operations Console

## 1. 页面信息架构

操作台采用五个主工作区：

- Dashboard：创建任务、查看当前任务状态、实时日志、最近项目。
- Projects：历史项目表格，支持查看、下载、删除。
- Project Detail：项目详情，按产物分 Tabs 浏览。
- LLM Settings：OpenAI-compatible LLM API 预留配置、自检和错误查看。
- Runtime Doctor：本机依赖、OCR、LLM、runtime 目录诊断。

路由结构：

- `/` 和 `/dashboard`
- `/projects`
- `/projects/:id`
- `/settings/llm`
- `/settings/runtime`

## 2. 操作台布局

整体为浅色高密度生产工具布局：

- 左侧固定 Sidebar：产品名、导航、当前运行状态。
- 顶部 Topbar：当前页面标题、API 状态、刷新入口。
- 主内容区：按页面渲染工作区，宽屏使用多列网格，移动端单列。
- 详情页：顶部项目摘要，下面固定 Tabs。

## 3. 视觉风格

风格关键词：专业、清爽、高密度、流水线控制台。

参考气质：Linear、Vercel、Notion、Stripe Dashboard、Cursor。

避免：

- 营销首屏和 hero。
- 花哨大屏和装饰渐变。
- 传统后台模板感。
- 空洞卡片堆叠。

## 4. 组件规范

- Sidebar：图标字母标识 + 文本导航，当前路由高亮。
- Topbar：标题、说明、健康状态、操作按钮。
- CreateJobPanel：紧凑表单，URL 独占一行，配置项两列。
- StatusTimeline：8 段状态机，显示状态、耗时、产物数。
- ProgressLog：表格式日志，支持复制。
- ProjectTable：历史任务高密度表格。
- ProjectDetailTabs：Overview / Transcript / Keyframes / OCR / Content Assets / XHS Post / Files。
- KeyframeGrid：图片网格，点击查看大图和元信息。
- TranscriptViewer：搜索 + 时间轴段落表。
- XhsPostPreview：标题、封面、正文、配图计划、标签，正文可复制。
- DownloadPanel：JSON、Markdown、ZIP、素材下载。
- LLMSettingsForm：配置项编辑，API key 不回显。
- RuntimeDoctorPanel：依赖矩阵和 ready 状态。

## 5. 色彩规范

- 背景：`#f7f8fb`
- 面板：`#ffffff`
- 主文本：`#101828`
- 次文本：`#667085`
- 边框：`#e4e7ec`
- 强调色：`#2563eb`
- 成功：`#0f766e`
- 警告：`#b54708`
- 失败：`#b42318`
- 代码区：`#111827`

## 6. 表格 / 卡片 / Tabs / 状态机

表格：

- 行高 48-56px。
- 状态、产物、操作列固定视觉权重。
- 空状态直接说明如何创建第一个任务。

卡片：

- 8px 圆角。
- 只用于面板、列表项、模态，不做卡片套卡片。

Tabs：

- 横向 segmented tabs。
- 当前 tab 使用浅蓝背景和蓝色文字。

状态机：

- 每个阶段为独立步骤。
- 当前阶段蓝色，完成绿色，失败红色，未开始灰色。
- 展示耗时与产物数，不编造不存在的产物。

## 7. 空状态 / 加载态 / 错误态

空状态：

- Dashboard：提示粘贴视频链接创建任务。
- Projects：提示暂无项目。
- Detail Tab：如果对应文件未生成，说明“产物尚不可用”。

加载态：

- 表格和详情区显示 `Loading...` 行。
- 提交任务按钮进入 disabled + 文案变化。

错误态：

- 使用红色 error panel。
- 显示后端 `code`、`message`、`step` 和 details。
- failed 项目仍然允许浏览已生成的真实中间产物。

## 8. 任务详情页布局

顶部摘要：

- 缩略图、标题、作者、URL、时长、状态、project_id。
- 下载 ZIP / 重跑视觉 / 重跑下游 / 校验。

Tabs：

- Overview：元数据、状态、错误、warnings、产物登记。
- Transcript：字幕时间轴、搜索。
- Keyframes：关键帧网格、评分、字幕关联、放大预览。
- OCR / Visual：OCR 文本、visual summary、confidence、warning。
- Content Assets：摘要、观点、金句、章节、步骤、受众、证据。
- XHS Post：标题候选、封面文案、hook、正文、配图规划、标签、发布建议。
- Files：所有文件下载和完整 ZIP。

## 9. 项目历史页布局

高密度表格字段：

- 项目 / 视频标题
- 状态
- 创建时间
- 更新时间
- 耗时
- 关键帧
- 小红书稿是否生成
- 操作：查看、下载、删除

删除只调用真实 API，并且运行中任务不允许删除。

## 10. LLM 配置页布局

只做 API 预留和自检：

- Base URL
- Model
- API Key 是否已配置
- Require API Key: auto / true / false
- Max Tokens
- Timeout
- 保存配置
- 测试连接
- self-test 结果

API Key 输入框不回显；留空代表保持当前密钥。

## 11. Runtime 诊断页布局

展示：

- runtime 目录可写性。
- ffmpeg / ffprobe / tesseract。
- fastapi / yt-dlp / faster-whisper / PySceneDetect / OpenCV / httpx / PaddleOCR。
- OCR provider。
- LLM provider。
- ready_for 矩阵。

所有数据来自 `/api/system/doctor` 或 `/api/diagnostics`，不展示假状态。
