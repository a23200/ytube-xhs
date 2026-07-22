const app = document.querySelector("#app");
const modalRoot = document.querySelector("#modal-root");

const STATUS_META = {
  queued: { label: "排队中", hint: "等待可用执行槽位" },
  created: { label: "任务已创建", hint: "准备进入解析队列" },
  ingesting: { label: "获取视频信息", hint: "yt-dlp 获取元数据、字幕和媒体" },
  transcribing: { label: "生成字幕时间轴", hint: "优先原字幕，无字幕时走 Whisper" },
  extracting_frames: { label: "抽取关键帧", hint: "场景检测并筛选清晰画面" },
  analyzing_visuals: { label: "识别画面文字", hint: "OCR 和基础视觉分析" },
  planning_content: { label: "生成创作底稿", hint: "提炼事实、观点、受众和选题方向" },
  analysis_completed: { label: "解析完成", hint: "可确认/编辑后产出图文" },
  producing_article: { label: "生成平台稿", hint: "生成标题、开头钩子和连续自然段正文" },
  validating_content: { label: "校验文章", hint: "检查小标题、钩子、来源数据和改写程度" },
  xhs_completed: { label: "小红书稿完成", hint: "文章和图片提示词已生成，等待独立生图 API 渲染 PNG" },
  toutiao_completed: { label: "今日头条稿完成", hint: "文章和图片提示词已生成，等待独立生图 API 渲染 PNG" },
  douyin_completed: { label: "抖音稿完成", hint: "文章、Word 和质量报告已生成" },
  bilibili_completed: { label: "哔哩哔哩稿完成", hint: "文章、Word 和质量报告已生成" },
  writing_xhs: { label: "写入文章文件", hint: "写入 JSON、Markdown 和提示词" },
  rendering_cards: { label: "渲染图文卡片", hint: "生成小红书竖版 PNG 卡片" },
  completed: { label: "图文完成", hint: "文章、卡片和下载素材已准备好" },
  failed: { label: "处理失败", hint: "查看错误原因和已生成产物" },
  stopped: { label: "已停止", hint: "任务已停止，中间产物保留" },
};

const STATUS_STEPS = [
  ["created", STATUS_META.created.label, STATUS_META.created.hint],
  ["ingesting", STATUS_META.ingesting.label, STATUS_META.ingesting.hint],
  ["transcribing", STATUS_META.transcribing.label, STATUS_META.transcribing.hint],
  ["extracting_frames", STATUS_META.extracting_frames.label, STATUS_META.extracting_frames.hint],
  ["analyzing_visuals", STATUS_META.analyzing_visuals.label, STATUS_META.analyzing_visuals.hint],
  ["planning_content", STATUS_META.planning_content.label, STATUS_META.planning_content.hint],
  ["analysis_completed", STATUS_META.analysis_completed.label, STATUS_META.analysis_completed.hint],
  ["queued", STATUS_META.queued.label, STATUS_META.queued.hint],
  ["producing_article", STATUS_META.producing_article.label, STATUS_META.producing_article.hint],
  ["validating_content", STATUS_META.validating_content.label, STATUS_META.validating_content.hint],
  ["xhs_completed", STATUS_META.xhs_completed.label, STATUS_META.xhs_completed.hint],
  ["toutiao_completed", STATUS_META.toutiao_completed.label, STATUS_META.toutiao_completed.hint],
  ["douyin_completed", STATUS_META.douyin_completed.label, STATUS_META.douyin_completed.hint],
  ["bilibili_completed", STATUS_META.bilibili_completed.label, STATUS_META.bilibili_completed.hint],
  ["writing_xhs", STATUS_META.writing_xhs.label, STATUS_META.writing_xhs.hint],
  ["rendering_cards", STATUS_META.rendering_cards.label, STATUS_META.rendering_cards.hint],
  ["completed", STATUS_META.completed.label, STATUS_META.completed.hint],
];

const STAGE_OUTPUTS = {
  ingesting: ["metadata"],
  transcribing: ["transcript"],
  extracting_frames: ["keyframes"],
  analyzing_visuals: ["visual_analysis"],
  planning_content: ["content_assets"],
  analysis_completed: ["content_assets", "asset_package"],
  producing_article: ["xhs_post_json", "image_prompts"],
  xhs_completed: ["xhs_post_json", "xhs_post_md", "xhs_post_docx", "xhs_quality_report", "image_prompts", "asset_package"],
  toutiao_completed: ["toutiao_post_json", "toutiao_post_md", "toutiao_post_docx", "toutiao_quality_report", "toutiao_image_prompts", "asset_package"],
  douyin_completed: ["douyin_post_json", "douyin_post_md", "douyin_post_docx", "douyin_quality_report", "asset_package"],
  bilibili_completed: ["bilibili_post_json", "bilibili_post_md", "bilibili_post_docx", "bilibili_quality_report", "asset_package"],
  writing_xhs: ["xhs_post_json", "xhs_post_md", "image_prompts", "asset_package"],
  rendering_cards: ["image_cards"],
  completed: [
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
};

const ROUTE_STATUS_OVERRIDES = {
  toutiao: {
    producing_article: { label: "生成今日头条稿", hint: "生成今日头条标题、正文和配图计划" },
    rendering_cards: { label: "渲染今日头条卡片", hint: "生成今日头条 PNG 卡片" },
  },
  xhs: {
    producing_article: { label: "生成小红书稿", hint: "生成小红书标题、正文、标签和配图计划" },
    rendering_cards: { label: "渲染图文卡片", hint: "生成小红书竖版 PNG 卡片" },
  },
  douyin: {
    queued: { label: "抖音任务排队中", hint: "等待文章生成槽位" },
    producing_article: { label: "生成抖音稿", hint: "生成口播文章和发布文案" },
    validating_content: { label: "校验抖音稿", hint: "检查钩子、事实和改写程度" },
  },
  bilibili: {
    queued: { label: "哔哩哔哩任务排队中", hint: "等待文章生成槽位" },
    producing_article: { label: "生成哔哩哔哩稿", hint: "生成动态或专栏型文章" },
    validating_content: { label: "校验哔哩哔哩稿", hint: "检查结构、事实和改写程度" },
  },
};

const ROUTE_STAGE_OUTPUTS = {
  toutiao: {
    producing_article: ["toutiao_post_json", "toutiao_image_prompts"],
    rendering_cards: ["toutiao_image_cards"],
    completed: [
      "metadata",
      "transcript",
      "keyframes",
      "visual_analysis",
      "content_assets",
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
  xhs: {
    producing_article: ["xhs_post_json", "image_prompts"],
    rendering_cards: ["image_cards"],
    completed: [
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
      "asset_package",
      "run_metadata",
    ],
  },
};

const FILE_KINDS = [
  ["metadata", "视频信息", "source/metadata.json"],
  ["transcript", "字幕时间轴", "transcript/transcript.json"],
  ["keyframes", "关键帧清单", "analysis/keyframes.json"],
  ["visual_analysis", "视觉/OCR 分析", "analysis/visual-analysis.json"],
  ["content_assets", "创作底稿", "analysis/content-assets.json"],
  ["xhs_post_json", "小红书稿 JSON", "analysis/xiaohongshu-post.json"],
  ["xhs_post_md", "小红书稿 Markdown", "analysis/xhs-post.md"],
  ["xhs_post_docx", "小红书稿 Word", "analysis/xhs-article.docx"],
  ["xhs_quality_report", "小红书质量报告", "analysis/xhs-quality-report.json"],
  ["image_prompts", "图片提示词", "analysis/image-prompts.json"],
  ["image_cards", "图文卡片清单", "analysis/image-cards.json"],
  ["toutiao_post_json", "今日头条稿 JSON", "analysis/toutiao-post.json"],
  ["toutiao_post_md", "今日头条稿 Markdown", "analysis/toutiao-post.md"],
  ["toutiao_post_docx", "今日头条稿 Word", "analysis/toutiao-article.docx"],
  ["toutiao_quality_report", "今日头条质量报告", "analysis/toutiao-quality-report.json"],
  ["toutiao_image_prompts", "今日头条图片提示词", "analysis/toutiao-image-prompts.json"],
  ["toutiao_image_cards", "今日头条卡片清单", "analysis/toutiao-image-cards.json"],
  ["douyin_post_json", "抖音稿 JSON", "analysis/douyin-post.json"],
  ["douyin_post_md", "抖音稿 Markdown", "analysis/douyin-post.md"],
  ["douyin_post_docx", "抖音稿 Word", "analysis/douyin-article.docx"],
  ["douyin_quality_report", "抖音质量报告", "analysis/douyin-quality-report.json"],
  ["bilibili_post_json", "哔哩哔哩稿 JSON", "analysis/bilibili-post.json"],
  ["bilibili_post_md", "哔哩哔哩稿 Markdown", "analysis/bilibili-post.md"],
  ["bilibili_post_docx", "哔哩哔哩稿 Word", "analysis/bilibili-article.docx"],
  ["bilibili_quality_report", "哔哩哔哩质量报告", "analysis/bilibili-quality-report.json"],
  ["asset_package", "完整素材包", "analysis/asset-package.json"],
  ["run_metadata", "运行元数据", "analysis/run-metadata.json"],
];

const READY_FOR_LABELS = {
  ingest: "视频获取",
  subtitle_transcript: "原字幕解析",
  whisper_transcript: "Whisper 转录",
  frame_extraction: "关键帧抽取",
  ocr: "OCR 识别",
  llm_generation: "LLM 图文生成",
  image_generation: "生图卡片生成",
};

const DETAIL_TABS = [
  ["overview", "概览"],
  ["transcript", "字幕"],
  ["keyframes", "关键帧"],
  ["visual", "OCR / 视觉"],
  ["assets", "创作底稿"],
  ["xhs", "小红书稿"],
  ["toutiao", "今日头条稿"],
  ["douyin", "抖音稿"],
  ["bilibili", "哔哩哔哩稿"],
  ["files", "文件下载"],
];

const CONTENT_ROUTES = {
  xhs: {
    key: "xhs",
    label: "小红书",
    shortLabel: "XHS",
    postJson: "xhs_post_json",
    postMd: "xhs_post_md",
    postDocx: "xhs_post_docx",
    quality: "xhs_quality_report",
    prompts: "image_prompts",
    cards: "image_cards",
    completedStatus: "xhs_completed",
    supportsImages: true,
    producePath: "produce",
    imagePath: "generate-images",
    postPatchPath: "xhs-post",
    cardsPatchPath: "image-cards",
    cardFilePath: "cards",
    cardsDownloadPath: "download/cards",
    bodyCopyId: "xhs-body",
    title: "小红书文章 + 图文卡片",
    emptyText: "配置 LLM 后点击左侧“一键产出图文”。",
    postPreviewTitle: "小红书稿预览",
    saveMessage: "小红书文章已保存，Markdown 与 asset-package 已同步更新。",
    articleReadyToast: "小红书稿已完成，已调用独立生图 API。",
    produceToast: "已开始生成小红书稿；完成后会自动调用独立生图 API。",
    llmMissingToast: "LLM 未配置，不能生成小红书文章。请先到 LLM API 设置页配置。",
    emptyPostTitle: "小红书稿尚不可用",
  },
  toutiao: {
    key: "toutiao",
    label: "今日头条",
    shortLabel: "头条",
    postJson: "toutiao_post_json",
    postMd: "toutiao_post_md",
    postDocx: "toutiao_post_docx",
    quality: "toutiao_quality_report",
    prompts: "toutiao_image_prompts",
    cards: "toutiao_image_cards",
    completedStatus: "toutiao_completed",
    supportsImages: true,
    producePath: "produce/toutiao",
    imagePath: "generate-images/toutiao",
    postPatchPath: "toutiao-post",
    cardsPatchPath: "toutiao-image-cards",
    cardFilePath: "toutiao-cards",
    cardsDownloadPath: "download/toutiao-cards",
    bodyCopyId: "toutiao-body",
    title: "今日头条文章 + 图文卡片",
    emptyText: "配置 LLM 后点击左侧“一键产出图文”。",
    postPreviewTitle: "今日头条稿预览",
    saveMessage: "今日头条文章已保存，Markdown 与 asset-package 已同步更新。",
    articleReadyToast: "今日头条稿已完成，已调用独立生图 API。",
    produceToast: "已开始生成今日头条稿；完成后会自动调用独立生图 API。",
    llmMissingToast: "LLM 未配置，不能生成今日头条文章。请先到 LLM API 设置页配置。",
    emptyPostTitle: "今日头条稿尚不可用",
  },
  douyin: {
    key: "douyin",
    label: "抖音",
    shortLabel: "抖音",
    postJson: "douyin_post_json",
    postMd: "douyin_post_md",
    postDocx: "douyin_post_docx",
    quality: "douyin_quality_report",
    prompts: "",
    cards: "",
    completedStatus: "douyin_completed",
    supportsImages: false,
    producePath: "produce/platform/douyin",
    imagePath: "",
    postPatchPath: "platform/douyin/post",
    cardsPatchPath: "",
    cardFilePath: "",
    cardsDownloadPath: "",
    bodyCopyId: "douyin-body",
    title: "抖音文章",
    emptyText: "配置 LLM 后点击左侧“一键产出平台稿”。",
    postPreviewTitle: "抖音稿预览",
    saveMessage: "抖音文章已保存，Markdown、Word 和质量报告已同步更新。",
    articleReadyToast: "抖音稿已完成。",
    produceToast: "已开始生成抖音稿。",
    llmMissingToast: "LLM 未配置，不能生成抖音文章。请先到 LLM API 设置页配置。",
    emptyPostTitle: "抖音稿尚不可用",
  },
  bilibili: {
    key: "bilibili",
    label: "哔哩哔哩",
    shortLabel: "B站",
    postJson: "bilibili_post_json",
    postMd: "bilibili_post_md",
    postDocx: "bilibili_post_docx",
    quality: "bilibili_quality_report",
    prompts: "",
    cards: "",
    completedStatus: "bilibili_completed",
    supportsImages: false,
    producePath: "produce/platform/bilibili",
    imagePath: "",
    postPatchPath: "platform/bilibili/post",
    cardsPatchPath: "",
    cardFilePath: "",
    cardsDownloadPath: "",
    bodyCopyId: "bilibili-body",
    title: "哔哩哔哩文章",
    emptyText: "配置 LLM 后点击左侧“一键产出平台稿”。",
    postPreviewTitle: "哔哩哔哩稿预览",
    saveMessage: "哔哩哔哩文章已保存，Markdown、Word 和质量报告已同步更新。",
    articleReadyToast: "哔哩哔哩稿已完成。",
    produceToast: "已开始生成哔哩哔哩稿。",
    llmMissingToast: "LLM 未配置，不能生成哔哩哔哩文章。请先到 LLM API 设置页配置。",
    emptyPostTitle: "哔哩哔哩稿尚不可用",
  },
};

const BATCH_STATUS_META = {
  queued: { label: "等待执行", hint: "批次已进入顺序队列" },
  running: { label: "顺序处理中", hint: "当前只处理一条视频" },
  completed: { label: "全部完成", hint: "全部 Word 文档已输出" },
  completed_with_errors: { label: "部分完成", hint: "部分链接失败，其余文档已输出" },
  stopped: { label: "已停止", hint: "批次已由用户停止" },
  failed: { label: "批次失败", hint: "批次调度器异常终止" },
};

const BATCH_ITEM_STATUS_META = {
  pending: "等待处理",
  analyzing: "解析视频",
  producing: "生成文章",
  completed: "Word 已完成",
  failed: "处理失败",
  stopped: "已停止",
  skipped: "已跳过",
};

const sessionProjectId = window.sessionStorage.getItem("xhs.activeProjectId") || window.localStorage.getItem("xhs.activeProjectId") || "";
const sessionContentRoute = window.sessionStorage.getItem("xhs.activeContentRoute") || window.localStorage.getItem("xhs.activeContentRoute") || "xhs";
if (sessionProjectId) window.sessionStorage.setItem("xhs.activeProjectId", sessionProjectId);
if (CONTENT_ROUTES[sessionContentRoute]) window.sessionStorage.setItem("xhs.activeContentRoute", sessionContentRoute);

const state = {
  health: null,
  projects: [],
  summaries: new Map(),
  activeProjectId: sessionProjectId,
  activeStatus: null,
  detail: null,
  workbenchDetail: null,
  llmSettings: null,
  imageSettings: null,
  detailTab: "overview",
  activeContentRoute: CONTENT_ROUTES[sessionContentRoute] ? sessionContentRoute : "xhs",
  routeContextProjectId: "",
  transcriptQuery: "",
  pollTimer: null,
  pendingImageGenerationProjectId: "",
  pendingImageGenerationRoute: "",
  modalFrames: [],
  batches: [],
  batchDetail: null,
  batchPollTimer: null,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function prettyJson(value) {
  return escapeHtml(JSON.stringify(value ?? {}, null, 2));
}

function pathFor(route) {
  return route + (route.startsWith("/") ? "" : "");
}

function statusClass(status) {
  if (["completed", "analysis_completed", "xhs_completed", "toutiao_completed", "douyin_completed", "bilibili_completed"].includes(status)) return "status-ok";
  if (["failed", "stopped"].includes(status)) return "status-error";
  if (!status) return "";
  return `status-${String(status).replace(/[^a-z0-9_-]/gi, "")}`;
}

function batchStatusClass(status) {
  if (status === "completed") return "status-ok";
  if (status === "completed_with_errors") return "status-warning";
  if (["stopped", "failed"].includes(status)) return "status-error";
  return "status-producing_article";
}

function batchStatusPill(status) {
  const meta = BATCH_STATUS_META[status] || { label: status || "未知状态" };
  return `<span class="status-pill ${batchStatusClass(status)}">${escapeHtml(meta.label)}</span>`;
}

function batchItemStatusPill(status) {
  const className = status === "completed"
    ? "status-ok"
    : (["failed", "stopped", "skipped"].includes(status) ? "status-error" : "status-producing_article");
  return `<span class="status-pill ${className}">${escapeHtml(BATCH_ITEM_STATUS_META[status] || status || "未知")}</span>`;
}

function isBatchRunning(status) {
  return ["queued", "running"].includes(status);
}

function platformFromDetails(details) {
  if (!details || typeof details !== "object") return "";
  if (CONTENT_ROUTES[details.platform]) return details.platform;
  return platformFromDetails(details.details);
}

function platformFromStatusData(statusData) {
  if (!statusData || typeof statusData !== "object") return "";
  if (CONTENT_ROUTES[statusData.target_platform]) return statusData.target_platform;
  if (CONTENT_ROUTES[statusData.record?.target_platform]) return statusData.record.target_platform;
  if (CONTENT_ROUTES[statusData.progress?.platform]) return statusData.progress.platform;
  const logs = Array.isArray(statusData.logs) ? statusData.logs : [];
  for (let index = logs.length - 1; index >= 0; index -= 1) {
    const platform = platformFromDetails(logs[index]?.details);
    if (platform) return platform;
  }
  const outputs = statusData.outputs || statusData.record?.outputs || {};
  if (outputs.toutiao_post_json || outputs.toutiao_post_md || outputs.toutiao_image_prompts || outputs.toutiao_image_cards) return "toutiao";
  if (outputs.douyin_post_json || outputs.douyin_post_md) return "douyin";
  if (outputs.bilibili_post_json || outputs.bilibili_post_md) return "bilibili";
  if (outputs.xhs_post_json || outputs.xhs_post_md || outputs.image_prompts || outputs.image_cards) return "xhs";
  return "";
}

function statusMeta(status, route = null, statusData = null) {
  const platform = platformFromStatusData(statusData) || route?.key || "";
  return {
    ...(STATUS_META[status] || { label: String(status || "未知状态"), hint: "等待后端更新状态" }),
    ...(ROUTE_STATUS_OVERRIDES[platform]?.[status] || {}),
  };
}

function statusLabel(status, route = null, statusData = null) {
  return statusMeta(status, route, statusData).label || String(status || "未知状态");
}

function statusHint(status, route = null, statusData = null) {
  return statusMeta(status, route, statusData).hint || "等待后端更新状态";
}

function statusPill(status, extraClass = "", route = null, statusData = null) {
  return `<span class="status-pill ${statusClass(status)} ${extraClass}">${escapeHtml(statusLabel(status, route, statusData))}</span>`;
}

function stageOutputsForStep(step, route = null, statusData = null) {
  const platform = platformFromStatusData(statusData) || route?.key || "";
  return ROUTE_STAGE_OUTPUTS[platform]?.[step] || STAGE_OUTPUTS[step] || [];
}

function currentContentRoute() {
  return CONTENT_ROUTES[state.activeContentRoute] || CONTENT_ROUTES.xhs;
}

function setContentRoute(routeKey) {
  state.activeContentRoute = CONTENT_ROUTES[routeKey] ? routeKey : "xhs";
  window.sessionStorage.setItem("xhs.activeContentRoute", state.activeContentRoute);
}

function routePost(detail, route = currentContentRoute()) {
  return detail?.files?.[route.postJson];
}

function routeCards(detail, route = currentContentRoute()) {
  return route.cards ? detail?.files?.[route.cards] : null;
}

function routePrompts(detail, route = currentContentRoute()) {
  return route.prompts ? detail?.files?.[route.prompts] : null;
}

function routeStatus(status, route = currentContentRoute()) {
  return status?.routes?.[route.key] || {};
}

function routeHasPost(detail, route = currentContentRoute()) {
  return Boolean(routePost(detail, route));
}

function routeHasCards(detail, route = currentContentRoute()) {
  return Boolean(routeCards(detail, route));
}

function routeOutputReady(outputs, route = currentContentRoute()) {
  return Boolean(outputs?.[route.postJson] || outputs?.[route.postMd]);
}

function routeCardsReady(outputs, route = currentContentRoute()) {
  return Boolean(outputs?.[route.cards]);
}

function isTextOnlyProject(detail = state.workbenchDetail) {
  return Boolean(detail?.record?.text_only || detail?.files?.content_assets?.analysis_mode === "text_only");
}

function isRunning(status) {
  return Boolean(status && !["analysis_completed", "xhs_completed", "toutiao_completed", "douyin_completed", "bilibili_completed", "completed", "failed", "stopped"].includes(status));
}

function shouldContinuePolling(projectId, status) {
  const route = CONTENT_ROUTES[state.pendingImageGenerationRoute] || currentContentRoute();
  return isRunning(status) || (status === route.completedStatus && state.pendingImageGenerationProjectId === projectId);
}

function safeDate(value) {
  if (!value) return "n/a";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString();
}

function secondsLabel(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return "n/a";
  const total = Math.max(0, Number(seconds));
  if (total < 60) return `${total.toFixed(total < 10 ? 1 : 0)} 秒`;
  const minutes = Math.floor(total / 60);
  const rest = Math.round(total % 60);
  if (minutes < 60) return `${minutes} 分 ${rest} 秒`;
  const hours = Math.floor(minutes / 60);
  return `${hours} 小时 ${minutes % 60} 分`;
}

function estimateLabel(seconds, confidence) {
  if (confidence === "complete") return "已完成";
  if (confidence === "failed" || seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return "无法估算";
  return `约 ${secondsLabel(seconds)}`;
}

function durationBetween(start, end) {
  if (!start || !end) return null;
  const startDate = new Date(start);
  const endDate = new Date(end);
  if (Number.isNaN(startDate.getTime()) || Number.isNaN(endDate.getTime())) return null;
  return Math.max(0, (endDate.getTime() - startDate.getTime()) / 1000);
}

function frameFilename(path) {
  return String(path || "").split(/[\\/]/).pop();
}

function textSnippet(text, length = 120) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  if (value.length <= length) return value;
  return `${value.slice(0, length - 1)}...`;
}

function errorText(error) {
  const detail = error?.body?.detail ?? error?.detail ?? error;
  if (typeof detail === "string") return detail;
  if (detail?.diagnostic) {
    const title = detail.diagnostic.title || detail.code || "处理失败";
    const actual = detail.diagnostic.actual_error || detail.message || "";
    return actual && actual !== title ? `${title}：${actual}` : title;
  }
  if (detail?.code === "youtube_media_download_forbidden") {
    return "YouTube 媒体流返回 403。链接可以公开查看，但当前运行环境被 YouTube 拒绝下载视频分片；系统会优先尝试使用真实字幕继续分析，若没有字幕则需要导出最新 cookies.txt 或换网络/IP 后重试。";
  }
  if (detail?.code === "youtube_bot_check_required") {
    return "YouTube 要求登录确认不是机器人。请配置浏览器 Cookie（例如 XHS_YTDLP_COOKIES_FROM_BROWSER=chrome）或导出的 cookies.txt 后重试。";
  }
  if (detail?.code === "yt_dlp_cookies_required") {
    return "平台要求使用最新浏览器 Cookie，即使公开视频也可能需要。服务与 Chrome 使用同一登录用户时可设置 XHS_YTDLP_COOKIES_FROM_BROWSER=chrome；无人值守服务建议导出最新 cookies.txt 并设置 XHS_YTDLP_COOKIES_FILE，重启后重试。";
  }
  if (detail?.code === "youtube_network_tls_failed") {
    return "当前运行环境到 YouTube 的网络/TLS 请求失败，视频可能仍是公开的。请稍后重试、换网络/IP，或配置浏览器导出的 cookies.txt 后再跑。";
  }
  if (detail?.code === "llm_contract_invalid") {
    return "LLM 返回内容不符合真实产物格式，系统已停止生成，避免用模板或 demo 内容冒充分析。请重试或检查 LLM 配置。";
  }
  const llmDetails = detail?.details || {};
  const llmContext = [
    llmDetails.http_status ? `HTTP ${llmDetails.http_status}` : "",
    llmDetails.model ? `模型 ${llmDetails.model}` : "",
    llmDetails.attempts_made ? `已尝试 ${llmDetails.attempts_made}/${llmDetails.attempt_limit || llmDetails.attempts_made} 次` : "",
  ].filter(Boolean).join("，");
  if (detail?.code === "llm_authentication_failed") {
    return `LLM 鉴权失败${llmContext ? `（${llmContext}）` : ""}。请核对 API Key、模型权限、账户余额和 Base URL；系统不会用本地低质量文章代替。`;
  }
  if (detail?.code === "llm_rate_limited") {
    return `LLM 限流${llmContext ? `（${llmContext}）` : ""}。请稍后重试，或检查接口额度并降低并发任务数。`;
  }
  if (detail?.code === "llm_timeout") {
    return `LLM 请求超时${llmContext ? `（${llmContext}）` : ""}。请检查接口延迟/网络，必要时在 LLM 设置中增大超时时间后重试。`;
  }
  if (detail?.code === "llm_network_error") {
    return `无法连接 LLM 接口${llmContext ? `（${llmContext}）` : ""}。请检查 Base URL、DNS、TLS、网络和服务端可用性。`;
  }
  if (detail?.code === "llm_http_error") {
    return `LLM 接口返回错误${llmContext ? `（${llmContext}）` : ""}。请查看项目日志中的脱敏响应摘要，核对模型名、请求额度和接口兼容性。`;
  }
  if (detail?.code === "llm_response_invalid") {
    return `LLM 响应格式无效${llmContext ? `（${llmContext}）` : ""}。接口虽返回成功，但不是兼容的 chat/completions 结构，请检查模型和 OpenAI-compatible 配置。`;
  }
  if (detail?.message) return `${detail.code ? `${detail.code}: ` : ""}${detail.message}`;
  if (error?.message) return error.message;
  return JSON.stringify(detail);
}

function errorPayload(error) {
  return error?.body?.detail ?? error?.detail ?? error ?? {};
}

function diagnosticLocationText(location = {}) {
  const parts = [
    location.stage_label,
    location.component,
    location.platform ? `输出目标 ${location.platform}` : "",
    location.artifact ? `产物 ${location.artifact}` : "",
    location.field ? `字段 ${location.field}` : "",
    location.index !== undefined ? `索引 ${location.index}` : "",
    location.evidence_index !== undefined ? `证据索引 ${location.evidence_index}` : "",
    location.command ? `命令 ${location.command}` : "",
  ].filter(Boolean);
  return parts.join(" · ");
}

function renderErrorDiagnostic(error, { compact = false } = {}) {
  const detail = errorPayload(error);
  const diagnostic = detail?.diagnostic || {};
  const title = diagnostic.title || detail.code || "处理失败";
  const actual = diagnostic.actual_error || detail.message || errorText(error);
  const location = diagnostic.location || { step: detail.step };
  const locationText = diagnosticLocationText(location) || detail.step || "未知阶段";
  const solutions = Array.isArray(diagnostic.solutions) && diagnostic.solutions.length
    ? diagnostic.solutions
    : ["查看实际错误和项目日志，保留项目 ID 后按对应阶段排查。"];
  const missing = location.missing_fields || location.missing;
  const missingText = missing ? (Array.isArray(missing) ? missing.join(", ") : String(missing)) : "";
  const retryLabel = diagnostic.retryable === false ? "需先修复配置或输入" : "修复后可重试";
  if (compact) {
    return `
      <div class="batch-error-diagnostic">
        <b>${escapeHtml(title)}</b>
        <span class="mono">${escapeHtml(detail.code || "unknown_error")} · ${escapeHtml(locationText)}</span>
        <p title="${escapeHtml(actual)}">${escapeHtml(textSnippet(actual, 260))}</p>
        ${missingText ? `<p><strong>缺失：</strong>${escapeHtml(missingText)}</p>` : ""}
        <details>
          <summary>解决方案</summary>
          <ol>${solutions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ol>
        </details>
      </div>
    `;
  }
  return `
    <div class="error-diagnostic" role="alert">
      <div class="error-diagnostic-header">
        <div><span>处理失败</span><h4>${escapeHtml(title)}</h4></div>
        <span class="mini-pill status-error">${escapeHtml(retryLabel)}</span>
      </div>
      <div class="error-diagnostic-meta">
        <div><span>错误码</span><code>${escapeHtml(detail.code || "unknown_error")}</code></div>
        <div><span>错误位置</span><b>${escapeHtml(locationText)}</b></div>
        ${missingText ? `<div><span>缺失字段/产物</span><code>${escapeHtml(missingText)}</code></div>` : ""}
      </div>
      <div class="error-diagnostic-block">
        <b>实际错误</b>
        <pre>${escapeHtml(actual)}</pre>
      </div>
      ${diagnostic.cause ? `<div class="error-diagnostic-block"><b>问题原因</b><p>${escapeHtml(diagnostic.cause)}</p></div>` : ""}
      <div class="error-diagnostic-block">
        <b>解决方案</b>
        <ol>${solutions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ol>
      </div>
      <details class="error-raw-details">
        <summary>查看完整错误详情</summary>
        <pre>${escapeHtml(JSON.stringify(detail, null, 2))}</pre>
      </details>
    </div>
  `;
}

function logMessageZh(message) {
  const map = {
    "Project created.": "任务已创建。",
    "Fetching video metadata, media, subtitles, and thumbnail.": "正在获取视频信息、媒体、字幕和缩略图。",
    "Ingest completed.": "视频信息获取完成。",
    "Building normalized transcript timeline.": "正在生成统一字幕时间轴。",
    "Transcript completed.": "字幕时间轴生成完成。",
    "Detecting scenes and extracting keyframes.": "正在检测场景并抽取关键帧。",
    "Keyframes completed.": "关键帧抽取完成。",
    "Text-only mode: skipping keyframe extraction.": "纯文案模式：跳过关键帧抽取。",
    "Text-only mode skipped keyframe extraction.": "纯文案模式已跳过关键帧抽取。",
    "Text-only mode: skipping OCR and visual analysis.": "纯文案模式：跳过 OCR 和视觉分析。",
    "Text-only mode skipped OCR and visual analysis.": "纯文案模式已跳过 OCR 和视觉分析。",
    "Running OCR and visual analysis providers.": "正在运行 OCR 和视觉分析。",
    "Visual analysis completed.": "视觉/OCR 分析完成。",
    "No video file is available; continuing with transcript-only analysis.": "没有可用视频文件，已改用真实字幕继续分析。",
    "Generating structured content assets with LLM.": "正在用 LLM 生成真实创作底稿。",
    "Analysis completed. Review or edit content assets before producing XHS cards.": "解析完成。请确认或编辑创作底稿后再产出图文。",
    "Text-only analysis completed. Review or edit content assets before producing an article.": "纯文案解析完成。请确认或编辑创作底稿后再产出文章。",
    "Produce job queued.": "图文产出任务已排队。",
    "Generating Xiaohongshu post from reviewed analysis assets.": "正在根据已确认解析生成小红书稿。",
    "Generating Toutiao post from reviewed analysis assets.": "正在根据已确认解析生成今日头条稿。",
    "XHS article completed. Image generation can be run next.": "小红书稿已完成，接下来会调用独立生图 API。",
    "Toutiao article completed. Image generation can be run next.": "今日头条稿已完成，接下来会调用独立生图 API。",
    "Text-only XHS article completed. Image generation is disabled.": "纯文案小红书文章已完成，不会生成图片卡片。",
    "Text-only Toutiao article completed. Image generation is disabled.": "纯文案今日头条文章已完成，不会生成图片卡片。",
    "Image generation job queued.": "生图任务已排队。",
    "Rendering finished Xiaohongshu image-card PNG files.": "正在渲染小红书图文卡片 PNG。",
    "Rendering finished Toutiao image-card PNG files.": "正在渲染今日头条图文卡片 PNG。",
    "Image generation completed. XHS article and image cards are ready.": "生图完成，文章和图片卡片已准备好。",
    "Image generation completed. Toutiao article and image cards are ready.": "生图完成，今日头条文章和图片卡片已准备好。",
    "Produce completed. XHS article and image cards are ready.": "图文产出完成，文章和图片卡片已准备好。",
    "Pipeline completed.": "完整处理链路已完成。",
    "Content assets updated from workbench.": "已从工作台保存创作底稿。",
    "XHS post updated from workbench.": "已从工作台保存小红书稿。",
    "Toutiao post updated from workbench.": "已从工作台保存今日头条稿。",
    "Image cards updated and rerendered.": "图文卡片已保存并重新渲染。",
    "Toutiao image cards updated and rerendered.": "今日头条图文卡片已保存并重新渲染。",
    "Downstream rerun queued.": "文案重跑任务已排队。",
    "Visual analysis rerun queued.": "视觉/OCR 重跑任务已排队。",
  };
  return map[message] || message || "";
}

function warningZh(message) {
  const map = {
    "No-subtitle videos require faster-whisper plus ffmpeg.": "无字幕视频需要 faster-whisper 和 ffmpeg。",
    "No OCR provider is available; install PaddleOCR or tesseract to extract screen text.": "当前没有可用 OCR 提供方；安装 PaddleOCR 或 tesseract 后可提取画面文字。",
    "LLM generation requires BUSINESS_LLM_API_KEY or XHS_LLM_API_KEY. For local OpenAI-compatible endpoints without auth, set XHS_LLM_REQUIRE_API_KEY=false.": "LLM 图文生成需要 BUSINESS_LLM_API_KEY 或 XHS_LLM_API_KEY；如果本地 OpenAI-compatible 服务不需要鉴权，请设置 XHS_LLM_REQUIRE_API_KEY=false。",
    "LLM generation requires httpx and a reachable OpenAI-compatible chat completions endpoint.": "LLM 图文生成需要 httpx，并且需要可访问的 OpenAI-compatible chat completions 接口。",
  };
  return map[message] || message || "";
}

function readyForLabel(key) {
  return READY_FOR_LABELS[key] || key;
}

async function apiRequest(path, options = {}) {
  const headers = options.body ? { "Content-Type": "application/json", ...(options.headers || {}) } : options.headers;
  const response = await fetch(path, { ...options, headers });
  const text = await response.text();
  let body = text;
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json") && text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!response.ok) {
    const error = new Error(typeof body === "string" ? body : body?.detail?.message || body?.detail || response.statusText);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

const apiGet = (path) => apiRequest(path);
const apiPost = (path, body = {}) => apiRequest(path, { method: "POST", body: JSON.stringify(body) });
const apiPut = (path, body = {}) => apiRequest(path, { method: "PUT", body: JSON.stringify(body) });
const apiPatch = (path, body = {}) => apiRequest(path, { method: "PATCH", body: JSON.stringify(body) });
const apiDelete = (path) => apiRequest(path, { method: "DELETE" });

async function apiFile(projectId, kind) {
  const response = await fetch(`/api/projects/${projectId}/files/${kind}`);
  if (!response.ok) {
    const error = new Error(response.statusText);
    error.status = response.status;
    throw error;
  }
  if (kind.endsWith("_post_md")) return response.text();
  if (kind.endsWith("_post_docx")) return response.blob();
  return response.json();
}

function isBinaryFileKind(kind) {
  return kind.endsWith("_post_docx");
}

function routeInfo() {
  const path = window.location.pathname === "/" ? "/dashboard" : window.location.pathname;
  const params = new URLSearchParams(window.location.search);
  if (path === "/dashboard") return { name: "dashboard", title: "生产工作台", mark: "台", tab: params.get("tab") };
  if (path === "/batches") return { name: "batches", title: "批量队列", mark: "列" };
  const batchMatch = path.match(/^\/batches\/([^/]+)$/);
  if (batchMatch) {
    return {
      name: "batch-detail",
      title: "批次详情",
      mark: "列",
      batchId: decodeURIComponent(batchMatch[1]),
    };
  }
  if (path === "/projects") return { name: "projects", title: "历史项目", mark: "项", tab: params.get("tab") };
  const detailMatch = path.match(/^\/projects\/([^/]+)$/);
  if (detailMatch) {
    return {
      name: "project-detail",
      title: "项目详情",
      mark: "详",
      projectId: decodeURIComponent(detailMatch[1]),
      tab: params.get("tab") || "overview",
    };
  }
  if (path === "/settings/llm") return { name: "llm", title: "LLM 配置", mark: "LLM" };
  if (path === "/settings/runtime") return { name: "runtime", title: "运行诊断", mark: "诊" };
  return { name: "not-found", title: "页面不存在", mark: "404" };
}

function navigate(path) {
  window.history.pushState({}, "", pathFor(path));
  renderRoute();
}

function currentNavClass(name) {
  const route = routeInfo();
  if (name === "dashboard" && route.name === "dashboard") return "active";
  if (name === "batches" && ["batches", "batch-detail"].includes(route.name)) return "active";
  if (name === "projects" && ["projects", "project-detail"].includes(route.name)) return "active";
  if (name === "llm" && route.name === "llm") return "active";
  if (name === "runtime" && route.name === "runtime") return "active";
  return "";
}

function shell({ title, subtitle, mark, actions = "", body = "" }) {
  document.title = `${title} · 视频图文生产工作台`;
  app.innerHTML = `
    <div class="app-layout">
      <aside class="sidebar">
        <div class="brand">
          <span class="brand-mark">X</span>
          <div>
            <h1>视频图文生产</h1>
            <p>Multi-platform Content Workbench</p>
          </div>
        </div>
        <nav class="nav-group" aria-label="主导航">
          <div class="nav-title">工作区</div>
          <a class="nav-link ${currentNavClass("dashboard")}" href="/dashboard" data-route="/dashboard">
            <span class="nav-mark">台</span><span>生产工作台</span>
          </a>
          <a class="nav-link ${currentNavClass("batches")}" href="/batches" data-route="/batches">
            <span class="nav-mark">列</span><span>批量队列</span>
          </a>
          <a class="nav-link ${currentNavClass("projects")}" href="/projects" data-route="/projects">
            <span class="nav-mark">项</span><span>历史项目</span>
          </a>
          <div class="nav-title">设置</div>
          <a class="nav-link ${currentNavClass("llm")}" href="/settings/llm" data-route="/settings/llm">
            <span class="nav-mark">L</span><span>LLM 配置</span>
          </a>
          <a class="nav-link ${currentNavClass("runtime")}" href="/settings/runtime" data-route="/settings/runtime">
            <span class="nav-mark">诊</span><span>运行诊断</span>
          </a>
        </nav>
        <div class="sidebar-footer">
          <b>合规边界</b><br />
          仅处理用户有权处理的视频；所有稿件保留来源、标题、作者和时间点。
        </div>
      </aside>
      <main class="main-shell">
        <header class="topbar">
          <div class="topbar-title">
            <span class="route-mark">${escapeHtml(mark || "X")}</span>
            <div>
              <h2>${escapeHtml(title)}</h2>
              <p>${escapeHtml(subtitle || "")}</p>
            </div>
          </div>
          <div class="topbar-actions">
            <span id="api-health-pill" class="status-pill ${state.health?.ok ? "status-ok" : ""}">
              API ${state.health?.ok ? "在线" : "检查中"}
            </span>
            ${actions}
          </div>
        </header>
        <section class="workspace">${body}</section>
      </main>
    </div>
  `;
}

function loadingPanel(title = "加载中") {
  return `
    <section class="panel pad">
      <div class="section-title">
        <h3>${escapeHtml(title)}</h3>
        <p>正在读取真实后端数据。</p>
      </div>
      <div class="stack">
        <div class="loading-line"></div>
        <div class="loading-line" style="width: 84%"></div>
        <div class="loading-line" style="width: 62%"></div>
      </div>
    </section>
  `;
}

function emptyState(message, detail = "") {
  return `<div class="empty-state"><b>${escapeHtml(message)}</b>${detail ? `<br />${escapeHtml(detail)}` : ""}</div>`;
}

function warningState(message) {
  return `<div class="warning-state">${escapeHtml(message)}</div>`;
}

function errorState(message) {
  return `<div class="error-state">${escapeHtml(message)}</div>`;
}

async function refreshHealth() {
  try {
    state.health = await apiGet("/api/health");
  } catch {
    state.health = { ok: false };
  }
  const pill = document.querySelector("#api-health-pill");
  if (pill) {
    pill.textContent = `API ${state.health.ok ? "在线" : "离线"}`;
    pill.className = `status-pill ${state.health.ok ? "status-ok" : "status-error"}`;
  }
}

async function loadProjects() {
  const projects = await apiGet("/api/projects");
  state.projects = [...projects].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
  await hydrateProjectSummaries(state.projects.slice(0, 30));
  return state.projects;
}

async function loadBatches() {
  const batches = await apiGet("/api/batches");
  state.batches = [...batches].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
  return state.batches;
}

async function loadBatch(batchId) {
  const batch = await apiGet(`/api/batches/${batchId}`);
  state.batchDetail = batch;
  return batch;
}

async function hydrateProjectSummaries(projects) {
  await Promise.allSettled(projects.map((project) => hydrateProjectSummary(project)));
}

async function hydrateProjectSummary(project) {
  if (!project?.project_id) return null;
  const cached = state.summaries.get(project.project_id) || {};
  const summary = {
    ...cached,
    title: cached.title || project.project_id,
    author: cached.author || "",
    duration: cached.duration ?? null,
    thumbnail: cached.thumbnail || "",
    frameCount: cached.frameCount ?? 0,
    xhsReady: Boolean(project.outputs?.xhs_post_json),
    toutiaoReady: Boolean(project.outputs?.toutiao_post_json),
    cardCount: cached.cardCount ?? 0,
    toutiaoCardCount: cached.toutiaoCardCount ?? 0,
    textOnly: Boolean(project.text_only),
  };
  if (project.outputs?.metadata && !cached.metadataLoaded) {
    try {
      const metadata = await apiFile(project.project_id, "metadata");
      summary.title = metadata.title || summary.title;
      summary.author = metadata.author || summary.author;
      summary.duration = metadata.duration ?? summary.duration;
      summary.thumbnail = metadata.thumbnail || summary.thumbnail;
      summary.metadataLoaded = true;
    } catch {
      summary.metadataLoaded = false;
    }
  }
  if (project.outputs?.keyframes && !cached.keyframesLoaded) {
    try {
      const keyframes = await apiFile(project.project_id, "keyframes");
      summary.frameCount = keyframes.frame_count ?? keyframes.keyframes?.length ?? summary.frameCount;
      summary.keyframesLoaded = true;
    } catch {
      summary.keyframesLoaded = false;
    }
  }
  if (project.outputs?.image_cards && !cached.cardsLoaded) {
    try {
      const imageCards = await apiFile(project.project_id, "image_cards");
      summary.cardCount = imageCards.card_count ?? imageCards.cards?.length ?? summary.cardCount;
      summary.cardsLoaded = true;
    } catch {
      summary.cardsLoaded = false;
    }
  }
  if (project.outputs?.toutiao_image_cards && !cached.toutiaoCardsLoaded) {
    try {
      const imageCards = await apiFile(project.project_id, "toutiao_image_cards");
      summary.toutiaoCardCount = imageCards.card_count ?? imageCards.cards?.length ?? summary.toutiaoCardCount;
      summary.toutiaoCardsLoaded = true;
    } catch {
      summary.toutiaoCardsLoaded = false;
    }
  }
  state.summaries.set(project.project_id, summary);
  return summary;
}

async function loadStatus(projectId) {
  const status = await apiGet(`/api/projects/${projectId}/status`);
  if (projectId === state.activeProjectId) state.activeStatus = status;
  return status;
}

async function loadDetail(projectId) {
  const [record, status] = await Promise.all([apiGet(`/api/projects/${projectId}`), loadStatus(projectId)]);
  const outputs = status.outputs || record.outputs || {};
  const files = {};
  await Promise.allSettled(
    FILE_KINDS.map(async ([kind]) => {
      if (!outputs[kind] || isBinaryFileKind(kind)) return;
      files[kind] = await apiFile(projectId, kind);
    }),
  );
  state.detail = { projectId, record, status, files };
  state.activeProjectId = projectId;
  window.sessionStorage.setItem("xhs.activeProjectId", projectId);
  await hydrateProjectSummary(record);
  return state.detail;
}

async function loadWorkbenchDetail(projectId) {
  if (!projectId) {
    state.workbenchDetail = null;
    return null;
  }
  const [record, status] = await Promise.all([apiGet(`/api/projects/${projectId}`), loadStatus(projectId)]);
  const outputs = status.outputs || record.outputs || {};
  const files = {};
  await Promise.allSettled(
    FILE_KINDS.map(async ([kind]) => {
      if (!outputs[kind] || isBinaryFileKind(kind)) return;
      files[kind] = await apiFile(projectId, kind);
    }),
  );
  state.workbenchDetail = { projectId, record, status, files };
  if (state.routeContextProjectId !== projectId && CONTENT_ROUTES[record.target_platform]) {
    setContentRoute(record.target_platform);
  }
  state.routeContextProjectId = projectId;
  await hydrateProjectSummary(record);
  return state.workbenchDetail;
}

function renderDashboardSkeleton() {
  shell({
    title: "视频图文生产工作台",
    mark: "D",
    subtitle: "左侧输入视频链接，两步产出平台图文稿和图片卡片。",
    actions: `<button class="ghost-button" data-action="refresh-dashboard" type="button">刷新</button>`,
    body: `<div class="workbench-layout">${loadingPanel("输入区")}${loadingPanel("解析与产出")}</div>`,
  });
}

async function renderDashboard() {
  renderDashboardSkeleton();
  try {
    const [settings] = await Promise.all([
      apiGet("/api/settings/llm").catch(() => null),
      loadProjects(),
      state.activeProjectId ? loadStatus(state.activeProjectId).catch(() => null) : null,
    ]);
    state.llmSettings = settings;
    if (state.activeProjectId) {
      await loadWorkbenchDetail(state.activeProjectId).catch(() => {
        state.workbenchDetail = null;
      });
    }
  } catch (error) {
    shell({
      title: "视频图文生产工作台",
      mark: "D",
      subtitle: "后端数据读取失败。",
      body: errorState(errorText(error)),
    });
    return;
  }
  shell({
    title: "视频图文生产工作台",
    mark: "D",
    subtitle: "一键分析解析，确认后再一键产出图文。",
    actions: `<button class="ghost-button" data-action="refresh-dashboard" type="button">刷新</button>`,
    body: `
      <div class="workbench-layout">
        <aside class="input-rail">
          ${renderCreateJobPanel()}
          ${renderWorkbenchStatusCard()}
          ${renderRecentProjectsCompact()}
        </aside>
        <div class="workbench-main">
          ${renderAnalysisReadablePanel(state.workbenchDetail)}
          ${renderProducePanel(state.workbenchDetail)}
        </div>
      </div>
    `,
  });
  if (state.activeProjectId && shouldContinuePolling(state.activeProjectId, state.activeStatus?.status)) {
    startPolling(state.activeProjectId);
  }
}

function renderCreateJobPanel() {
  const detail = state.workbenchDetail;
  const route = currentContentRoute();
  const status = detail?.status?.status || state.activeStatus?.status || "";
  const hasAnalysis = Boolean(detail?.files?.content_assets);
  const hasPost = routeHasPost(detail, route);
  const hasCards = routeHasCards(detail, route);
  const defaultTextOnly = true;
  const textOnly = detail ? isTextOnlyProject(detail) : defaultTextOnly;
  const selectedRouteStatus = routeStatus(detail?.status || state.activeStatus, route);
  const llmReady = state.llmSettings ? (!state.llmSettings.auth_required || state.llmSettings.api_key_configured) : false;
  const canProduceArticle = Boolean(detail?.status?.can_produce && hasAnalysis && !isRunning(status) && llmReady);
  const canGenerateImages = Boolean(route.supportsImages && !textOnly && selectedRouteStatus.can_generate_images && hasPost && !hasCards && !isRunning(status));
  const produceReady = canProduceArticle || canGenerateImages;
  const produceLabel = textOnly || !route.supportsImages ? "一键产出文章" : (canGenerateImages ? "继续生成图片卡片" : "一键产出图文");
  const llmNote = llmReady
    ? `LLM 已配置，可在“LLM 配置”页自检连通性；${route.label}稿生成依赖实时接口稳定性。${textOnly ? "当前项目为纯文案模式，不会生成图片卡片。" : ""}`
    : "LLM 未配置：可分析解析；文章生成不能伪造。纯文案模式只产出文章，不调用生图。";
  return `
    <section class="panel workbench-control-card">
      <div class="panel-header">
        <div>
          <h3>视频链接</h3>
          <p>输入公开视频或已授权视频，先分析解析，再产出图文。</p>
        </div>
      </div>
      <div class="panel-body">
        <form id="project-form" class="form-grid">
          <label class="field">
            视频 URL
            <textarea id="url" name="url" rows="4" placeholder="https://www.youtube.com/watch?v=..." required></textarea>
          </label>
          <div class="field-row">
            <label class="field">
              语言
              <select id="language" name="language">
                <option value="zh">zh</option>
                <option value="en">en</option>
                <option value="auto">auto</option>
              </select>
            </label>
            <label class="field">
              内容风格
              <select id="style" name="style">
                <option value="干货">干货</option>
                <option value="教程">教程</option>
                <option value="测评">测评</option>
                <option value="观点">观点</option>
                <option value="清单">清单</option>
              </select>
            </label>
          </div>
          <label class="field">
            最大关键帧数量
            <input id="max_frames" name="max_frames" type="number" min="8" max="20" value="12" disabled />
          </label>
          <div class="check-row">
            <label class="check-field">
              <input id="text_only" name="text_only" type="checkbox" checked />
              <span>仅提取文案/字幕，不抽关键帧、不 OCR、不生成图片</span>
            </label>
            <label class="check-field">
              <input id="use_whisper" name="use_whisper" type="checkbox" checked />
              <span>无字幕时启用 Whisper</span>
            </label>
            <label class="check-field">
              <input id="use_ocr" name="use_ocr" type="checkbox" disabled />
              <span>启用 OCR 识别</span>
            </label>
          </div>
          <div class="route-selector" aria-label="图文路线">
            ${Object.values(CONTENT_ROUTES).map((item) => `
              <button class="route-option ${route.key === item.key ? "active" : ""}" data-action="set-content-route" data-route-key="${item.key}" type="button">
                <span>${escapeHtml(item.label)}</span>
                <small>${routeOutputReady(detail?.status?.outputs, item) ? "稿件已生成" : "待生成"}</small>
              </button>
            `).join("")}
          </div>
          <button id="submit-button" class="button" type="submit" ${isRunning(status) ? "disabled" : ""}>一键分析解析</button>
          <button id="produce-button" class="ghost-button produce-button" data-action="produce-project" type="button" ${produceReady ? "" : "disabled"}>
            ${escapeHtml(produceLabel)}
          </button>
          <div class="small-text">
            ${escapeHtml(llmNote)}
          </div>
          <div id="create-error"></div>
        </form>
      </div>
    </section>
  `;
}

function syncTextOnlyControls(form) {
  if (!form) return;
  const textOnly = Boolean(form.elements.text_only?.checked);
  const maxFrames = form.elements.max_frames;
  const useOcr = form.elements.use_ocr;
  if (maxFrames) maxFrames.disabled = textOnly;
  if (useOcr) {
    useOcr.disabled = textOnly;
    if (textOnly) {
      useOcr.checked = false;
    } else if (!useOcr.checked) {
      useOcr.checked = true;
    }
  }
}

function renderWorkbenchStatusCard() {
  const status = state.activeStatus;
  if (!state.activeProjectId || !status) {
    return `
      <section id="workbench-status-card" class="panel pad">
        <div class="section-title">
          <h3>两步流程</h3>
          <p>Analyze 生成创作底稿，Produce 生成文章，生图 API 渲染 PNG 卡片。</p>
        </div>
        <div class="step-rail">
          <div class="step-card active"><b>1</b><span>一键分析解析</span></div>
          <div class="step-card"><b>2</b><span>一键产出平台稿</span></div>
          <div class="step-card"><b>3</b><span>独立生图 API 出卡片</span></div>
        </div>
      </section>
    `;
  }
  return `
    <section id="workbench-status-card" class="panel pad stack">
      <div class="row between wrap">
        <div class="section-title" style="margin:0">
          <h3>当前任务</h3>
          <p class="mono">${escapeHtml(state.activeProjectId)}</p>
        </div>
        ${statusPill(status.status, "", currentContentRoute(), status)}
      </div>
      ${renderProgressSummary(status)}
      ${status.execution?.state === "queued" ? `<p class="small-text">当前队列位置：${Number(status.execution.queue_position || 0)}；可同时在其他窗口提交不同项目。</p>` : ""}
      ${status.error ? renderErrorDiagnostic(status.error) : ""}
      ${status.can_cancel ? `<button class="danger-button" data-action="cancel-project" data-project-id="${escapeHtml(state.activeProjectId)}" type="button">强制停止</button>` : ""}
      ${renderStatusTimeline(status)}
      ${renderProgressLogPanel(status)}
    </section>
  `;
}

function renderProgressSummary(statusData) {
  const progress = statusData?.progress;
  if (!progress) return "";
  const percent = Math.max(0, Math.min(100, Number(progress.percent || 0)));
  const remaining = estimateLabel(progress.remaining_seconds, progress.eta_confidence);
  const note = progress.eta_confidence === "low" ? "预计时间波动较大" : "预计时间会随处理进度更新";
  return `
    <div class="progress-summary">
      <div class="row between wrap">
        <div>
          <span class="progress-eyebrow">${escapeHtml(progress.mode_label || "执行进度")}</span>
          <h4>当前阶段：${escapeHtml(progress.current_step_label || statusLabel(statusData.status, currentContentRoute(), statusData))}</h4>
          <p>${escapeHtml(progress.current_step_description || statusHint(statusData.status, currentContentRoute(), statusData))}</p>
        </div>
        <strong>${percent}%</strong>
      </div>
      <div class="progress-bar" aria-label="任务进度">
        <span class="progress-fill" style="width:${percent}%"></span>
      </div>
      <div class="progress-metrics">
        <div><span>已用时</span><b>${escapeHtml(secondsLabel(progress.elapsed_seconds))}</b></div>
        <div><span>预计剩余</span><b>${escapeHtml(remaining)}</b></div>
        <div><span>阶段</span><b>${Number(progress.completed_steps || 0)}/${Number(progress.total_steps || 0)}</b></div>
      </div>
      <p class="small-text">${escapeHtml(note)}。${escapeHtml(progress.estimate_note || "")}</p>
    </div>
  `;
}

function renderRecentProjectsCompact() {
  const projects = state.projects.slice(0, 4);
  return `
    <section class="panel pad">
      <div class="row between">
        <div class="section-title" style="margin:0"><h3>最近项目</h3><p>真实 runtime 记录</p></div>
        <a class="ghost-button" href="/projects" data-route="/projects">全部</a>
      </div>
      <div class="recent-list">
        ${projects.length ? projects.map((project) => {
          const summary = state.summaries.get(project.project_id) || {};
          return `
            <button class="recent-project ${state.activeProjectId === project.project_id ? "active" : ""}" data-action="select-project" data-project-id="${project.project_id}" type="button">
              <span>${escapeHtml(textSnippet(summary.title || project.project_id, 42))}</span>
              <small>${escapeHtml(statusLabel(project.status, null, project))} · ${summary.textOnly ? "纯文案" : `${Number(summary.frameCount || 0)} 帧 · ${Number(summary.cardCount || 0)} 卡`}</small>
            </button>
          `;
        }).join("") : emptyState("暂无项目", "提交链接后会出现在这里。")}
      </div>
    </section>
  `;
}

function renderAnalysisReadablePanel(detail) {
  if (!detail) {
    return `
      <section class="panel pad analysis-panel">
        <div class="section-title">
          <h3>信息提炼</h3>
          <p>点击左侧“一键分析解析”后，这里会展示视频信息、创作底稿、来源字幕和关键帧依据。</p>
        </div>
        ${emptyState("等待视频分析", "这里不会展示假数据；只有后端生成真实 runtime 产物后才会出现内容。")}
      </section>
    `;
  }
  const metadata = detail.files.metadata || {};
  const assets = detail.files.content_assets;
  const transcript = detail.files.transcript;
  const frames = mergedFrames(detail).slice(0, 8);
  const textOnly = isTextOnlyProject(detail);
  return `
    <section id="analysis-readable-panel" class="panel pad analysis-panel">
      <div class="row between wrap">
        <div class="section-title">
          <h3>信息提炼结果</h3>
          <p>${escapeHtml(textOnly ? "纯文案模式：基于字幕/文案解析创作底稿，不抽关键帧、不 OCR、不生成图片。" : "普通创作者可读的创作底稿，可编辑后保存并作为原创图文产出输入。")}</p>
        </div>
        <div class="row wrap">
          <button class="ghost-button" data-action="save-content-assets" type="button" ${assets ? "" : "disabled"}>保存创作底稿</button>
          <a class="ghost-button" href="/projects/${detail.projectId}" data-route="/projects/${detail.projectId}">详情</a>
        </div>
      </div>
      ${detail.status.error ? renderErrorDiagnostic(detail.status.error) : ""}
      ${renderWorkbenchMetadata(metadata, detail)}
      ${assets ? renderEditableAssets(assets) : emptyState("创作底稿尚未生成", "Analyze 完成后会生成 content-assets.json。")}
      ${transcript ? renderTranscriptSummary(transcript) : ""}
      ${!textOnly && frames.length ? renderKeyframeSummaryStrip(detail, frames) : ""}
      <div id="analysis-save-message"></div>
    </section>
  `;
}

function renderWorkbenchMetadata(metadata, detail) {
  const title = metadata.title || detail.projectId;
  const thumbnail = metadata.thumbnail;
  return `
    <div class="video-summary-card">
      <div class="thumbnail">
        ${thumbnail ? `<img src="${escapeHtml(thumbnail)}" alt="${escapeHtml(title)}" />` : ""}
      </div>
      <div class="stack">
        <div class="row between wrap">
          <h4>${escapeHtml(title)}</h4>
          ${statusPill(detail.status.status, "", currentContentRoute(), detail.status)}
        </div>
          <div class="meta-grid compact-meta">
            <div class="meta-item"><span>作者</span><b>${escapeHtml(metadata.author || "未知")}</b></div>
            <div class="meta-item"><span>时长</span><b>${escapeHtml(secondsLabel(metadata.duration))}</b></div>
            <div class="meta-item"><span>解析模式</span><b>${detail.record.text_only ? "纯文案" : "图文"}</b></div>
            <div class="meta-item"><span>URL</span><a href="${escapeHtml(metadata.url || detail.record.url)}" target="_blank" rel="noreferrer">${escapeHtml(textSnippet(metadata.url || detail.record.url, 48))}</a></div>
          </div>
      </div>
    </div>
  `;
}

function renderEditableAssets(assets) {
  return `
    <div class="editable-assets">
      <label class="field">一句话总结
        <textarea id="asset-summary" data-asset-field="one_sentence_summary">${escapeHtml(assets.one_sentence_summary || "")}</textarea>
      </label>
      <div class="editable-grid">
        ${renderEditableObjectList("核心观点", "core_points", assets.core_points, "point", "why_it_matters")}
        ${renderEditableObjectList("金句", "golden_quotes", assets.golden_quotes, "quote", "rewrite_note")}
      </div>
      <div class="editable-grid">
        ${renderEditableStringList("受众", "audience", assets.audience)}
        ${renderEditableStringList("痛点", "pain_points", assets.pain_points)}
        ${renderEditableStringList("平台选题角度", "xiaohongshu_angles", assets.xiaohongshu_angles)}
      </div>
      <details class="asset-details">
        <summary>来源证据时间点</summary>
        ${renderAssetList("来源证据", assets.source_evidence, (item) => `<h4>${escapeHtml(item.claim)}</h4><p class="small-text">${escapeHtml(item.source_type)} · ${escapeHtml(item.time)}s · ${escapeHtml(item.source_text || item.source_path || "")}</p>`)}
      </details>
    </div>
  `;
}

function renderEditableObjectList(title, field, items, primaryKey, secondaryKey) {
  return `
    <div class="editable-section" data-edit-section="${field}">
      <h4>${escapeHtml(title)}</h4>
      ${(items || []).map((item, index) => `
        <article class="editable-item">
          <textarea data-edit-field="${field}" data-index="${index}" data-key="${primaryKey}">${escapeHtml(item?.[primaryKey] || "")}</textarea>
          <textarea data-edit-field="${field}" data-index="${index}" data-key="${secondaryKey}">${escapeHtml(item?.[secondaryKey] || "")}</textarea>
        </article>
      `).join("") || `<p class="small-text">暂无</p>`}
    </div>
  `;
}

function renderEditableStringList(title, field, items) {
  return `
    <label class="field editable-section">${escapeHtml(title)}
      <textarea data-string-list="${field}" placeholder="一行一个">${escapeHtml((items || []).join("\n"))}</textarea>
    </label>
  `;
}

function renderTranscriptSummary(transcript) {
  const segments = transcript.segments || [];
  return `
    <div class="summary-block">
      <div class="row between wrap">
        <h4>来源字幕依据</h4>
        <span class="mini-pill">${segments.length} 段</span>
      </div>
      <div class="summary-list">
        ${segments.slice(0, 8).map((segment) => `
          <div class="summary-row">
            <span class="mono">${Number(segment.start || 0).toFixed(1)}s</span>
            <p>${escapeHtml(textSnippet(segment.text, 150))}</p>
          </div>
        `).join("") || '<p class="small-text">暂无字幕片段</p>'}
      </div>
    </div>
  `;
}

function renderKeyframeSummaryStrip(detail, frames) {
  return `
    <div class="summary-block">
      <div class="row between wrap">
        <h4>关键帧依据</h4>
        <span class="mini-pill">${frames.length} 张预览</span>
      </div>
      <div class="keyframe-strip">
        ${frames.map((frame) => `
          <article class="keyframe-chip">
            <img src="/api/projects/${detail.projectId}/frames/${escapeHtml(frame.filename)}" alt="${escapeHtml(frame.filename)}" />
            <b>${Number(frame.time || 0).toFixed(1)}s</b>
            <span>${escapeHtml(textSnippet(frame.visual?.ocr_text || frame.related_transcript_text || frame.reason, 46))}</span>
          </article>
        `).join("")}
      </div>
    </div>
  `;
}

function renderProducePanel(detail) {
  const route = currentContentRoute();
  const textOnly = isTextOnlyProject(detail);
  if (!detail) {
    return `
      <section id="produce-panel" class="panel pad produce-panel">
        <div class="section-title">
          <h3>图文产出</h3>
          <p>完成 Analyze 并配置 LLM 后，先生成文章，再由独立生图 API 渲染 PNG 卡片。</p>
        </div>
        ${emptyState("尚未产出图文", "不会用 prompt 或示例图冒充成品 PNG。")}
      </section>
    `;
  }
  const post = routePost(detail, route);
  const cards = routeCards(detail, route);
  return `
    <section id="produce-panel" class="panel pad produce-panel">
      <div class="row between wrap">
        <div class="section-title">
          <h3>${escapeHtml(textOnly ? `${route.label}文章` : route.title)}</h3>
          <p>${escapeHtml(textOnly || !route.supportsImages ? "根据字幕/文案解析生成文章、Word 和质量报告；该路线不生成图片。" : "文章由 Produce 生成；PNG 卡片由独立生图 API 渲染，可编辑后重新出图。")}</p>
        </div>
        <div class="row wrap">
          <button class="ghost-button" data-action="save-post" type="button" ${post ? "" : "disabled"}>保存文章</button>
          ${textOnly || !route.supportsImages ? "" : `<button class="ghost-button" data-action="save-image-cards" type="button" ${cards ? "" : "disabled"}>保存卡片</button>`}
        </div>
      </div>
      ${post ? renderArticleEditor(post) : emptyState("文章尚未生成", route.emptyText)}
      ${post ? renderQualitySummary(detail.files?.[route.quality], post) : ""}
      ${!textOnly && route.supportsImages && cards ? renderImageCardGallery(detail, cards, route) : ""}
      ${renderWorkbenchDownloads(detail, route)}
      <div id="produce-save-message"></div>
    </section>
  `;
}

function renderQualitySummary(report, post) {
  if (!report) return "";
  const similarity = report.similarity || {};
  const data = report.data_concretization || {};
  const bodyLength = report.body_length || {};
  const rewritePercent = Math.round(Number(similarity.estimated_rewrite_degree || 0) * 100);
  const bodyLengthLabel = bodyLength.actual_chars === undefined
    ? "未记录"
    : `${Number(bodyLength.actual_chars)} / ${Number(bodyLength.minimum_chars || 0)}-${Number(bodyLength.maximum_chars || 0)} 字`;
  return `
    <div class="summary-block">
      <div class="row between wrap">
        <h4>文章质量校验</h4>
        <span class="mini-pill ${report.passed ? "status-ok" : "status-error"}">${report.passed ? "已通过" : "未通过"}</span>
      </div>
      <div class="meta-grid compact-meta">
        <div class="meta-item"><span>正文有效字数</span><b>${escapeHtml(bodyLengthLabel)}</b></div>
        <div class="meta-item"><span>估算改写程度</span><b>${rewritePercent}%</b></div>
        <div class="meta-item"><span>最长重复片段</span><b>${Number(similarity.longest_common_fragment_chars || 0)} 字</b></div>
        <div class="meta-item"><span>定向重写次数</span><b>${Number(report.rewrite_count || 0)}</b></div>
        <div class="meta-item"><span>数据转换追溯</span><b>${Number((data.generated_ratio_expressions || []).length + (data.generated_population_expressions || []).length)} 项</b></div>
      </div>
      <p class="small-text">${escapeHtml(report.policy?.originality_note || "改写程度是文本估算，不是平台原创认证。")}</p>
    </div>
  `;
}

function renderArticleEditor(post) {
  return `
    <div class="xhs-editor">
      <label class="field">标题候选
        <textarea data-post-field="titles" placeholder="一行一个标题">${escapeHtml((post.titles || []).join("\n"))}</textarea>
      </label>
      <label class="field">封面文案
        <input data-post-field="cover_text" value="${escapeHtml(post.cover_text || "")}" />
      </label>
      <label class="field">开头钩子
        <textarea data-post-field="hook">${escapeHtml(post.hook || "")}</textarea>
      </label>
      <label class="field">正文
        <textarea data-post-field="body" class="long-editor">${escapeHtml(post.body || "")}</textarea>
      </label>
      <label class="field">标签
        <textarea data-post-field="hashtags" placeholder="一行一个标签">${escapeHtml((post.hashtags || []).join("\n"))}</textarea>
      </label>
      <div class="asset-item"><h4>发布建议</h4><p>${escapeHtml(post.publish_suggestion || "")}</p></div>
    </div>
  `;
}

function renderImageCardGallery(detail, imageCards, route = currentContentRoute()) {
  const cards = imageCards.cards || [];
  return `
    <div class="image-card-gallery" id="image-card-gallery">
      <div class="row between wrap">
        <h4>${escapeHtml(route.label)}图文卡片 PNG</h4>
        <span class="mini-pill">${cards.length} 张卡片</span>
      </div>
      <div class="card-grid">
        ${cards.map((card, index) => {
          const filename = frameFilename(card.output_path);
          return `
            <article class="image-card-preview" data-card-index="${index}">
              <img src="/api/projects/${detail.projectId}/${route.cardFilePath}/${escapeHtml(filename)}?v=${encodeURIComponent(detail.record.updated_at || "")}" alt="${escapeHtml(card.title || filename)}" />
              <div class="card-edit">
                <span class="mini-pill">P${escapeHtml(card.page)} · ${escapeHtml(card.role)}</span>
                <label class="field">图片标题
                  <input data-card-field="title" data-card-index="${index}" value="${escapeHtml(card.title || "")}" />
                </label>
                <label class="field">图片说明
                  <textarea data-card-field="caption" data-card-index="${index}">${escapeHtml(card.caption || "")}</textarea>
                </label>
                <p class="small-text">来源关键帧：${escapeHtml(card.source_frame_time ?? "n/a")}s</p>
                <a class="ghost-button" href="/api/projects/${detail.projectId}/${route.cardFilePath}/${escapeHtml(filename)}" target="_blank" rel="noreferrer">下载 PNG</a>
              </div>
            </article>
          `;
        }).join("") || emptyState("暂无卡片", `产出步骤会在 runtime/projects/{id}/${route.cardFilePath}/ 生成 PNG。`)}
      </div>
    </div>
  `;
}

function renderWorkbenchDownloads(detail, route = currentContentRoute()) {
  const outputs = detail.status.outputs || {};
  const textOnly = isTextOnlyProject(detail);
  const downloadButton = (ready, label, href) => ready
    ? `<a class="ghost-button" href="${href}">${label}</a>`
    : `<span class="ghost-button disabled" aria-disabled="true">${label}</span>`;
  return `
    <div class="download-strip">
      ${downloadButton(outputs[route.postMd], "Markdown", `/api/projects/${detail.projectId}/files/${route.postMd}`)}
      ${downloadButton(outputs[route.postJson], "文章 JSON", `/api/projects/${detail.projectId}/files/${route.postJson}`)}
      ${downloadButton(outputs[route.postDocx], "Word", `/api/projects/${detail.projectId}/files/${route.postDocx}`)}
      ${downloadButton(outputs[route.quality], "质量报告", `/api/projects/${detail.projectId}/files/${route.quality}`)}
      ${textOnly || !route.supportsImages ? "" : `
        ${downloadButton(outputs[route.cards], "卡片 JSON", `/api/projects/${detail.projectId}/files/${route.cards}`)}
        ${downloadButton(outputs[route.cards], "卡片 ZIP", `/api/projects/${detail.projectId}/${route.cardsDownloadPath}`)}
        ${downloadButton(outputs.keyframes, "关键帧 ZIP", `/api/projects/${detail.projectId}/download/frames`)}
      `}
      <a class="button" href="/api/projects/${detail.projectId}/download">完整 ZIP</a>
    </div>
  `;
}

function renderCurrentTaskPanel() {
  if (!state.activeProjectId || !state.activeStatus) {
    return `
      <section class="panel">
        <div class="panel-header">
          <div>
            <h3>任务状态机</h3>
            <p>创建任务后在这里查看每个阶段的状态、耗时和产物登记。</p>
          </div>
        </div>
        <div class="panel-body">${emptyState("尚无当前任务", "粘贴视频 URL 并开始处理。")}</div>
      </section>
    `;
  }
  return `
    <section id="current-task-panel" class="panel">
      <div class="panel-header">
        <div>
          <h3>任务状态机</h3>
          <p class="mono">${escapeHtml(state.activeProjectId)}</p>
        </div>
        <div class="row wrap">
          ${statusPill(state.activeStatus.status, "", currentContentRoute(), state.activeStatus)}
          ${state.activeStatus.can_cancel ? `<button class="danger-button" data-action="cancel-project" data-project-id="${escapeHtml(state.activeProjectId)}" type="button">强制停止</button>` : ""}
          <a class="ghost-button" href="/projects/${state.activeProjectId}" data-route="/projects/${state.activeProjectId}">查看详情</a>
        </div>
      </div>
      <div class="panel-body stack">
        ${state.activeStatus.error ? renderErrorDiagnostic(state.activeStatus.error) : ""}
        ${renderStatusTimeline(state.activeStatus)}
      </div>
    </section>
  `;
}

function renderStatusTimeline(statusData) {
  if (statusData?.progress?.steps?.length) {
    return `
      <div class="timeline">
        ${statusData.progress.steps.map((step) => {
          const klass = {
            done: "done",
            running: "active",
            failed: "failed",
            pending: "",
          }[step.state] || "";
          const elapsed = step.elapsed_seconds === null || step.elapsed_seconds === undefined ? "等待" : secondsLabel(step.elapsed_seconds);
          const outputsLabel = step.outputs_expected ? `${step.outputs_ready}/${step.outputs_expected} 产物` : "无产物";
          return `
            <div class="timeline-step ${klass}">
              <span class="timeline-dot"></span>
              <div><b>${escapeHtml(step.label)}</b><span>${escapeHtml(step.description)}</span></div>
              <span class="mini-pill">${escapeHtml(elapsed)} · ${escapeHtml(outputsLabel)}</span>
            </div>
          `;
        }).join("")}
      </div>
    `;
  }
  const currentStatus = statusData?.status || "created";
  const errorStep = statusData?.error?.step;
  const activeStep = currentStatus === "failed" ? errorStep || statusData?.logs?.at(-1)?.status || "created" : currentStatus;
  const activeIndex = STATUS_STEPS.findIndex(([step]) => step === activeStep);
  const outputs = statusData?.outputs || {};
  return `
    <div class="timeline">
      ${STATUS_STEPS.map(([step, label, hint], index) => {
        const meta = statusMeta(step, currentContentRoute(), statusData);
        const stageOutputs = stageOutputsForStep(step, currentContentRoute(), statusData);
        const readyCount = stageOutputs.filter((kind) => outputs[kind]).length;
        const first = firstLogTime(statusData, step);
        const next = nextLogTime(statusData, step);
        const elapsed = first ? secondsLabel(durationBetween(first, next || statusData.updated_at || new Date().toISOString())) : "等待";
        const isDone = currentStatus === "completed" || (activeIndex >= 0 && index < activeIndex);
        const isActive = step === activeStep && currentStatus !== "completed";
        const isFailed = currentStatus === "failed" && step === activeStep;
        const klass = `${isDone ? "done" : ""} ${isActive ? "active" : ""} ${isFailed ? "failed" : ""}`;
        const countLabel = stageOutputs.length ? `${readyCount}/${stageOutputs.length} 产物` : "等待";
        return `
          <div class="timeline-step ${klass}">
            <span class="timeline-dot"></span>
            <div><b>${escapeHtml(meta.label || label)}</b><span>${escapeHtml(meta.hint || hint)}</span></div>
            <span class="mini-pill">${escapeHtml(elapsed)} · ${escapeHtml(countLabel)}</span>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function firstLogTime(statusData, step) {
  return (statusData?.logs || []).find((log) => log.status === step)?.time || null;
}

function nextLogTime(statusData, step) {
  const logs = statusData?.logs || [];
  const currentIndex = logs.findIndex((log) => log.status === step);
  if (currentIndex < 0) return null;
  const next = logs.slice(currentIndex + 1).find((log) => log.status !== step);
  return next?.time || null;
}

function renderProgressLogPanel(statusData) {
  return `
    <section id="progress-log-panel" class="panel">
      <div class="panel-header">
        <div>
          <h3>实时进度日志</h3>
          <p>页面自动轮询 /api/projects/{id}/status；错误和详情保留原始信息，方便排查。</p>
        </div>
        <button class="ghost-button" data-action="copy-logs" type="button">复制日志</button>
      </div>
      <div class="panel-body">
        ${renderLogTable(statusData?.logs || [])}
      </div>
    </section>
  `;
}

function renderLogTable(logs) {
  if (!logs.length) return emptyState("暂无日志", "任务提交后会显示后端实时进度日志。");
  return `
    <div class="table-wrap">
      <table id="logs" class="log-table">
        <thead><tr><th>时间</th><th>阶段</th><th>消息</th><th>详情</th></tr></thead>
        <tbody>
          ${logs.map((log) => `
            <tr>
              <td class="mono subtle">${escapeHtml(safeDate(log.time))}</td>
              <td>${statusPill(log.status, "", currentContentRoute(), { logs: [log] })}</td>
              <td>${escapeHtml(logMessageZh(log.message))}</td>
              <td class="mono small-text">${log.details ? escapeHtml(JSON.stringify(log.details)) : ""}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderRecentProjectsPanel() {
  return `
    <section class="panel">
      <div class="panel-header">
        <div>
          <h3>最近项目</h3>
          <p>真实读取 /api/projects，标题和关键帧数量来自已生成产物。</p>
        </div>
        <a class="ghost-button" href="/projects" data-route="/projects">全部项目</a>
      </div>
      <div class="panel-body">
        ${renderProjectTable(state.projects.slice(0, 8), { compact: true })}
      </div>
    </section>
  `;
}

function renderProjectTable(projects, options = {}) {
  if (!projects.length) return emptyState("暂无项目", "在生产工作台创建第一个真实处理任务。");
  return `
    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>项目 / 视频</th>
            <th>状态</th>
            <th>创建时间</th>
            <th>耗时</th>
            <th>关键帧</th>
            <th>图文稿</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          ${projects.map((project) => {
            const summary = state.summaries.get(project.project_id) || {};
            const elapsed = secondsLabel(durationBetween(project.created_at, project.updated_at));
            const title = summary.title || project.project_id;
            const running = isRunning(project.status);
            return `
              <tr>
                <td>
                  <div class="stack" style="gap: 3px">
                    <a class="text-button" href="/projects/${project.project_id}" data-route="/projects/${project.project_id}">
                      ${escapeHtml(textSnippet(title, options.compact ? 48 : 76))}
                    </a>
                    <span class="mono small-text">${escapeHtml(project.project_id)} · ${escapeHtml(project.url || "")}</span>
                  </div>
                </td>
                <td>${statusPill(project.status, "", null, project)}</td>
                <td class="small-text">${escapeHtml(safeDate(project.created_at))}</td>
                <td class="small-text">${escapeHtml(elapsed)}</td>
                <td>${summary.textOnly ? "纯文案" : Number(summary.frameCount || 0)}</td>
                <td>${renderProjectRoutePills(project.outputs || {})}</td>
                <td>
                  <div class="row wrap">
                    <a class="ghost-button" href="/projects/${project.project_id}" data-route="/projects/${project.project_id}">查看</a>
                    <a class="ghost-button" href="/api/projects/${project.project_id}/download">ZIP</a>
                    ${running ? `<button class="danger-button" data-action="cancel-project" data-project-id="${project.project_id}" type="button">停止</button>` : ""}
                    <button class="danger-button" data-action="delete-project" data-project-id="${project.project_id}" ${running ? "disabled" : ""} type="button">删除</button>
                  </div>
                </td>
              </tr>
            `;
          }).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderProjectRoutePills(outputs) {
  const ready = Object.values(CONTENT_ROUTES).filter((route) => routeOutputReady(outputs, route));
  if (!ready.length) return '<span class="mini-pill">未生成</span>';
  return ready.map((route) => `<span class="mini-pill status-ok">${escapeHtml(route.shortLabel)}</span>`).join(" ");
}

function renderBatchCreateForm() {
  return `
    <section class="panel batch-create-panel">
      <div class="panel-header">
        <div>
          <h3>新建顺序队列</h3>
          <p>按输入顺序逐条完成解析、文章生成和 Word 归档。</p>
        </div>
      </div>
      <div class="panel-body">
        <form id="batch-form" class="form-grid">
          <label class="field">
            视频链接
            <textarea id="batch-urls" name="urls" rows="10" placeholder="每行一个视频链接" required></textarea>
          </label>
          <div class="field-row">
            <label class="field">
              文章平台
              <select id="batch-target-platform" name="target_platform">
                ${Object.values(CONTENT_ROUTES).map((route) => `<option value="${route.key}">${escapeHtml(route.label)}</option>`).join("")}
              </select>
            </label>
            <label class="field">
              内容风格
              <select id="batch-style" name="style">
                <option value="干货">干货</option>
                <option value="教程">教程</option>
                <option value="测评">测评</option>
                <option value="观点">观点</option>
                <option value="清单">清单</option>
              </select>
            </label>
          </div>
          <div class="field-row">
            <label class="field">
              语言
              <select id="batch-language" name="language">
                <option value="zh">zh</option>
                <option value="en">en</option>
                <option value="auto">auto</option>
              </select>
            </label>
            <label class="field">
              最大关键帧数量
              <input id="batch-max-frames" name="max_frames" type="number" min="8" max="20" value="12" disabled />
            </label>
          </div>
          <div class="check-row">
            <label class="check-field">
              <input id="batch-text-only" name="text_only" type="checkbox" checked />
              <span>纯文案模式</span>
            </label>
            <label class="check-field">
              <input id="batch-use-whisper" name="use_whisper" type="checkbox" checked />
              <span>无字幕时启用 Whisper</span>
            </label>
            <label class="check-field">
              <input id="batch-use-ocr" name="use_ocr" type="checkbox" disabled />
              <span>启用 OCR</span>
            </label>
            <label class="check-field">
              <input id="batch-continue-on-error" name="continue_on_error" type="checkbox" checked />
              <span>单条失败后继续</span>
            </label>
          </div>
          <button id="start-batch-button" class="button" type="submit">开始顺序处理</button>
          <div id="batch-create-error"></div>
        </form>
      </div>
    </section>
  `;
}

function renderBatchTable(batches) {
  if (!batches.length) return emptyState("暂无批量队列", "输入多条视频链接后开始第一个批次。");
  return `
    <div class="table-wrap">
      <table class="data-table batch-table">
        <thead><tr><th>批次</th><th>平台</th><th>进度</th><th>状态</th><th>文档</th><th>操作</th></tr></thead>
        <tbody>
          ${batches.map((batch) => `
            <tr>
              <td>
                <a class="text-button mono" href="/batches/${batch.batch_id}" data-route="/batches/${batch.batch_id}">${escapeHtml(batch.batch_id)}</a>
                <div class="small-text">${escapeHtml(safeDate(batch.created_at))}</div>
              </td>
              <td>${escapeHtml(CONTENT_ROUTES[batch.target_platform]?.label || batch.target_platform)}</td>
              <td>
                <b>${Number(batch.processed_count || 0)} / ${Number(batch.total_count || 0)}</b>
                <div class="small-text">成功 ${Number(batch.completed_count || 0)} · 失败 ${Number(batch.failed_count || 0)}</div>
              </td>
              <td>${batchStatusPill(batch.status)}</td>
              <td>${Number(batch.document_count || 0)} 个 Word</td>
              <td>
                <div class="row wrap">
                  <a class="ghost-button" href="/batches/${batch.batch_id}" data-route="/batches/${batch.batch_id}">查看</a>
                  ${batch.download_ready ? `<a class="ghost-button" href="/api/batches/${batch.batch_id}/download">下载</a>` : ""}
                  ${batch.can_cancel ? `<button class="danger-button" data-action="cancel-batch" data-batch-id="${batch.batch_id}" type="button">停止</button>` : ""}
                </div>
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderBatchListPanel() {
  return `
    <section id="batch-list-panel" class="panel">
      <div class="panel-header">
        <div>
          <h3>批次记录</h3>
          <p>队列、单条失败和 Word 输出均持久化到 runtime。</p>
        </div>
      </div>
      <div class="panel-body">${renderBatchTable(state.batches)}</div>
    </section>
  `;
}

async function renderBatches() {
  shell({
    title: "批量队列",
    mark: "列",
    subtitle: "多条视频按顺序解析并生成 Word 文档。",
    actions: `<button class="ghost-button" data-action="refresh-batches" type="button">刷新</button>`,
    body: `<div class="grid two">${renderBatchCreateForm()}${loadingPanel("批次记录")}</div>`,
  });
  try {
    await Promise.all([loadBatches(), apiGet("/api/settings/llm").then((value) => { state.llmSettings = value; }).catch(() => null)]);
    shell({
      title: "批量队列",
      mark: "列",
      subtitle: "多条视频按顺序解析并生成 Word 文档。",
      actions: `<button class="ghost-button" data-action="refresh-batches" type="button">刷新</button>`,
      body: `<div class="grid two batch-layout">${renderBatchCreateForm()}${renderBatchListPanel()}</div>`,
    });
    if (state.batches.some((batch) => isBatchRunning(batch.status))) startBatchPolling();
  } catch (error) {
    shell({ title: "批量队列", mark: "列", subtitle: "批次记录读取失败。", body: errorState(errorText(error)) });
  }
}

function renderBatchDetailBody(batch) {
  const current = batch.items.find((item) => item.index === batch.current_index);
  return `
    <div id="batch-detail-body" class="stack">
      <section class="panel pad">
        <div class="row between wrap">
          <div>
            <div class="progress-eyebrow">${escapeHtml(CONTENT_ROUTES[batch.target_platform]?.label || batch.target_platform)} · ${Number(batch.total_count || 0)} 条视频</div>
            <h3 class="batch-title">${escapeHtml(batch.batch_id)}</h3>
            <p class="muted">${escapeHtml(BATCH_STATUS_META[batch.status]?.hint || "")}</p>
          </div>
          ${batchStatusPill(batch.status)}
        </div>
        <div class="progress-bar batch-progress"><span class="progress-fill" style="width: ${Math.max(0, Math.min(100, Number(batch.progress_percent || 0)))}%"></span></div>
        <div class="progress-metrics">
          <div><span>总进度</span><b>${Number(batch.processed_count || 0)} / ${Number(batch.total_count || 0)}</b></div>
          <div><span>当前任务</span><b>${current ? `${current.index}. ${BATCH_ITEM_STATUS_META[current.status] || current.status}` : "无"}</b></div>
          <div><span>Word 文档</span><b>${Number(batch.document_count || 0)}</b></div>
        </div>
        <div class="batch-output-path"><span>输出目录</span><code>${escapeHtml(batch.output_directory || "")}</code></div>
      </section>
      <section class="panel">
        <div class="panel-header">
          <div><h3>视频处理顺序</h3><p>上一条完成或失败后，才会进入下一条。</p></div>
        </div>
        <div class="table-wrap">
          <table class="data-table batch-items-table">
            <thead><tr><th>顺序</th><th>视频</th><th>阶段</th><th>项目</th><th>文档 / 错误</th></tr></thead>
            <tbody>
              ${batch.items.map((item) => `
                <tr class="${item.index === batch.current_index ? "batch-current-row" : ""}">
                  <td class="mono" data-label="顺序">${String(item.index).padStart(3, "0")}</td>
                  <td data-label="视频"><div class="truncate batch-url-cell" title="${escapeHtml(item.url)}">${escapeHtml(item.title || item.url)}</div></td>
                  <td data-label="阶段">${batchItemStatusPill(item.status)}</td>
                  <td data-label="项目">${item.project_id ? `<a class="text-button mono" href="/projects/${item.project_id}" data-route="/projects/${item.project_id}">${escapeHtml(item.project_id)}</a>` : '<span class="muted">待创建</span>'}</td>
                  <td data-label="文档 / 错误">
                    ${item.document_filename ? `<a class="text-button" href="/api/batches/${batch.batch_id}/documents/${encodeURIComponent(item.document_filename)}">${escapeHtml(item.document_filename)}</a>` : ""}
                    ${item.error ? renderErrorDiagnostic(item.error, { compact: true }) : ""}
                  </td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  `;
}

async function renderBatchDetail(batchId) {
  shell({
    title: "批次详情",
    mark: "列",
    subtitle: batchId,
    actions: `<a class="ghost-button" href="/batches" data-route="/batches">返回队列</a>`,
    body: loadingPanel("批次详情"),
  });
  try {
    const batch = await loadBatch(batchId);
    const actions = `
      <a class="ghost-button" href="/batches" data-route="/batches">返回队列</a>
      ${batch.download_ready ? `<a class="button" href="/api/batches/${batch.batch_id}/download">下载 Word 文件夹</a>` : ""}
      ${batch.can_cancel ? `<button class="danger-button" data-action="cancel-batch" data-batch-id="${batch.batch_id}" type="button">停止批次</button>` : ""}
    `;
    shell({
      title: "批次详情",
      mark: "列",
      subtitle: `${batch.batch_id} · ${BATCH_STATUS_META[batch.status]?.label || batch.status}`,
      actions,
      body: renderBatchDetailBody(batch),
    });
    if (isBatchRunning(batch.status)) startBatchPolling(batchId);
  } catch (error) {
    shell({ title: "批次详情", mark: "列", subtitle: batchId, body: errorState(errorText(error)) });
  }
}

async function renderProjects() {
  shell({
    title: "历史项目",
    mark: "项",
    subtitle: "历史项目、产物状态、下载和删除。",
    actions: `<button class="ghost-button" data-action="refresh-projects" type="button">刷新</button>`,
    body: loadingPanel("历史项目"),
  });
  try {
    await loadProjects();
    shell({
      title: "历史项目",
      mark: "项",
      subtitle: "项目列表来自真实 runtime 记录。",
      actions: `<button class="ghost-button" data-action="refresh-projects" type="button">刷新</button>`,
      body: `<section class="panel pad">${renderProjectTable(state.projects)}</section>`,
    });
  } catch (error) {
    shell({
      title: "历史项目",
      mark: "项",
      subtitle: "项目读取失败。",
      body: errorState(errorText(error)),
    });
  }
}

async function renderProjectDetail(projectId) {
  state.detailTab = routeInfo().tab || state.detailTab || "overview";
  if (!DETAIL_TABS.some(([tab]) => tab === state.detailTab)) state.detailTab = "overview";
  shell({
    title: "项目详情",
    mark: "详",
    subtitle: projectId,
    actions: `<a class="ghost-button" href="/projects" data-route="/projects">返回项目</a>`,
    body: loadingPanel("项目详情"),
  });
  try {
    await loadDetail(projectId);
  } catch (error) {
    shell({
      title: "项目详情",
      mark: "详",
      subtitle: projectId,
      body: errorState(errorText(error)),
    });
    return;
  }
  const detail = state.detail;
  const summary = state.summaries.get(projectId) || {};
  shell({
    title: summary.title || "项目详情",
    mark: "详",
    subtitle: `${projectId} · ${statusLabel(detail.record.status, currentContentRoute(), detail.status)}`,
    actions: renderDetailActions(detail),
    body: `
      <div class="grid">
        ${renderDetailHero(detail)}
        ${renderDetailTabs(projectId)}
        <section id="detail-tab-content">${renderDetailTabContent(detail)}</section>
      </div>
    `,
  });
  if (isRunning(detail.status.status)) {
    startPolling(projectId);
  }
}

function renderDetailActions(detail) {
  const projectId = detail.projectId;
  const canCancel = Boolean(detail.status?.can_cancel || isRunning(detail.status?.status));
  return `
    <button class="ghost-button" data-action="verify-project" data-project-id="${projectId}" type="button">校验</button>
    <button class="danger-button" data-action="cancel-project" data-project-id="${projectId}" ${canCancel ? "" : "disabled"} type="button">强制停止</button>
    <button class="ghost-button" data-action="rerun-visuals" data-project-id="${projectId}" ${detail.status.can_rerun_visuals ? "" : "disabled"} type="button">重跑视觉</button>
    <button class="ghost-button" data-action="rerun-downstream" data-project-id="${projectId}" ${detail.status.can_rerun_downstream ? "" : "disabled"} type="button">重跑文案</button>
    <a class="button" href="/api/projects/${projectId}/download">下载 ZIP</a>
  `;
}

function renderDetailHero(detail) {
  const metadata = detail.files.metadata || {};
  const summary = state.summaries.get(detail.projectId) || {};
  const title = metadata.title || summary.title || detail.projectId;
  const thumbnail = metadata.thumbnail || summary.thumbnail;
  const duration = metadata.duration ?? summary.duration;
  const warnings = detail.status.warnings || detail.record.warnings || [];
  return `
    <section class="panel pad">
      <div class="detail-hero">
        <div class="thumbnail">
          ${thumbnail ? `<img src="${escapeHtml(thumbnail)}" alt="${escapeHtml(title)}" />` : ""}
        </div>
        <div class="stack">
          <div class="row between wrap">
            <div>
              <h3 style="margin: 0 0 6px">${escapeHtml(title)}</h3>
              <div class="small-text">${escapeHtml(metadata.author || summary.author || "作者未知")}</div>
            </div>
            ${statusPill(detail.status.status, "", currentContentRoute(), detail.status)}
          </div>
          ${detail.status.error ? renderErrorDiagnostic(detail.status.error) : ""}
          ${warnings.length ? warningState(warnings.join(" / ")) : ""}
          <div class="meta-grid">
            <div class="meta-item"><span>项目 ID</span><b class="mono">${escapeHtml(detail.projectId)}</b></div>
            <div class="meta-item"><span>视频时长</span><b>${escapeHtml(secondsLabel(duration))}</b></div>
            <div class="meta-item"><span>生成耗时</span><b>${escapeHtml(secondsLabel(durationBetween(detail.record.created_at, detail.record.updated_at)))}</b></div>
            <div class="meta-item"><span>来源 URL</span><a href="${escapeHtml(metadata.url || detail.record.url)}" target="_blank" rel="noreferrer">${escapeHtml(textSnippet(metadata.url || detail.record.url, 72))}</a></div>
            <div class="meta-item"><span>创建时间</span><b>${escapeHtml(safeDate(detail.record.created_at))}</b></div>
            <div class="meta-item"><span>更新时间</span><b>${escapeHtml(safeDate(detail.record.updated_at))}</b></div>
          </div>
        </div>
      </div>
    </section>
  `;
}

function renderDetailTabs(projectId) {
  return `
    <div class="tabs" role="tablist">
      ${DETAIL_TABS.map(([tab, label]) => `
        <button class="tab-button ${state.detailTab === tab ? "active" : ""}" data-tab="${tab}" data-project-id="${projectId}" type="button">
          ${escapeHtml(label)}
        </button>
      `).join("")}
    </div>
  `;
}

function renderDetailTabContent(detail) {
  switch (state.detailTab) {
    case "transcript":
      return renderTranscriptTab(detail);
    case "keyframes":
      return renderKeyframesTab(detail);
    case "visual":
      return renderVisualTab(detail);
    case "assets":
      return renderAssetsTab(detail);
    case "xhs":
      return renderPostTab(detail, CONTENT_ROUTES.xhs);
    case "toutiao":
      return renderPostTab(detail, CONTENT_ROUTES.toutiao);
    case "douyin":
      return renderPostTab(detail, CONTENT_ROUTES.douyin);
    case "bilibili":
      return renderPostTab(detail, CONTENT_ROUTES.bilibili);
    case "files":
      return renderFilesTab(detail);
    case "overview":
    default:
      return renderOverviewTab(detail);
  }
}

function renderOverviewTab(detail) {
  const outputs = detail.status.outputs || {};
  return `
    <div class="grid two">
      <section class="panel pad stack">
        <div class="section-title">
          <h3>状态与产物</h3>
          <p>每个产物只在后端登记且文件存在时显示为可用。</p>
        </div>
        ${renderStatusTimeline(detail.status)}
        <div class="download-grid">
          ${FILE_KINDS.map(([kind, label, path]) => `
            <div class="download-card">
              <b>${escapeHtml(label)}</b>
              <span class="mono">${escapeHtml(path)}</span>
              ${outputs[kind] ? '<span class="mini-pill status-ok">已生成</span>' : '<span class="mini-pill">等待中</span>'}
            </div>
          `).join("")}
        </div>
      </section>
      <section class="panel pad stack">
        <div class="section-title">
          <h3>实时进度日志</h3>
          <p>失败任务仍可查看已生成中间产物。</p>
        </div>
        ${renderLogTable(detail.status.logs || [])}
      </section>
    </div>
  `;
}

function renderTranscriptTab(detail) {
  const transcript = detail.files.transcript;
  if (!transcript) return `<section class="panel pad">${emptyState("字幕尚不可用", "等待“生成字幕时间轴”阶段完成。")}</section>`;
  const query = state.transcriptQuery.trim().toLowerCase();
  const segments = (transcript.segments || []).filter((segment) => {
    if (!query) return true;
    return [segment.text, segment.source, segment.start, segment.end].join(" ").toLowerCase().includes(query);
  });
  return `
    <section class="panel pad">
      <div class="search-row">
        <input id="transcript-search" type="search" value="${escapeHtml(state.transcriptQuery)}" placeholder="搜索字幕文本、来源或时间点" />
        <span class="status-pill">${segments.length}/${transcript.segments?.length || 0} 段</span>
      </div>
      ${segments.length ? `
        <div class="table-wrap">
          <table class="data-table">
            <thead><tr><th>开始</th><th>结束</th><th>字幕文本</th><th>来源</th><th>重要度</th></tr></thead>
            <tbody>
              ${segments.map((segment) => `
                <tr>
                  <td class="mono">${Number(segment.start || 0).toFixed(3)}s</td>
                  <td class="mono">${Number(segment.end || 0).toFixed(3)}s</td>
                  <td>${escapeHtml(segment.text)}</td>
                  <td class="small-text">${escapeHtml(segment.source)}</td>
                  <td>${Number(segment.importance || 0).toFixed(2)}</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      ` : emptyState("没有匹配的字幕片段", "换一个关键词试试。")}
    </section>
  `;
}

function mergedFrames(detail) {
  const frames = detail.files.keyframes?.keyframes || [];
  const visualByFile = new Map();
  for (const item of detail.files.visual_analysis?.frames || []) {
    const filename = frameFilename(item.path);
    if (filename) visualByFile.set(filename, item);
  }
  return frames.map((frame, index) => {
    const filename = frameFilename(frame.path);
    return { ...frame, filename, index, visual: visualByFile.get(filename) || null };
  });
}

function renderKeyframesTab(detail) {
  const frames = mergedFrames(detail);
  state.modalFrames = frames;
  if (!detail.files.keyframes) return `<section class="panel pad">${emptyState("关键帧尚不可用", "等待“抽取关键帧”阶段完成。")}</section>`;
  if (!frames.length) {
    const reason = detail.files.keyframes.skip_reason || "后端没有登记可下载的关键帧。";
    return `<section class="panel pad">${emptyState("没有关键帧", reason)}</section>`;
  }
  return `
    <section class="panel pad">
      <div class="section-title">
        <h3>关键帧素材</h3>
        <p>图片来自 /api/projects/{id}/frames/{filename}，点击可放大查看 OCR 与关联字幕。</p>
      </div>
      <div id="frames" class="frames-grid">
        ${frames.map((frame) => `
          <article class="frame-card">
            <button class="frame-button" data-action="open-frame" data-frame-index="${frame.index}" type="button">
              <img src="/api/projects/${detail.projectId}/frames/${escapeHtml(frame.filename)}" alt="frame ${escapeHtml(frame.filename)}" loading="lazy" />
            </button>
            <div class="frame-body">
              <div class="row between">
                <b>${Number(frame.time || 0).toFixed(2)}s</b>
                <span class="mini-pill">评分 ${Number(frame.score || 0).toFixed(2)}</span>
              </div>
              <p class="small-text">${escapeHtml(textSnippet(frame.reason, 110))}</p>
              <p class="small-text">${escapeHtml(textSnippet(frame.related_transcript_text, 140))}</p>
            </div>
          </article>
        `).join("")}
      </div>
    </section>
  `;
}

function renderVisualTab(detail) {
  const visual = detail.files.visual_analysis;
  if (!visual) return `<section class="panel pad">${emptyState("OCR / 视觉分析尚不可用", "等待“识别画面文字”阶段完成。")}</section>`;
  const frames = visual.frames || [];
  return `
    <section class="panel pad stack">
      <div class="row between wrap">
        <div class="section-title">
          <h3>OCR / 视觉分析</h3>
          <p>实际 OCR：${escapeHtml(visual.ocr_provider || "unknown")} · 请求 OCR：${escapeHtml(visual.requested_ocr_provider || "n/a")}</p>
        </div>
        <span class="status-pill ${visual.warnings?.length ? "status-warning" : "status-ok"}">${visual.warnings?.length || 0} 条警告</span>
      </div>
      ${visual.warnings?.length ? warningState(visual.warnings.join(" / ")) : ""}
      ${visual.skipped ? emptyState("已跳过 OCR / 视觉分析", visual.skip_reason || "当前项目为纯文案模式。") : ""}
      <div class="split-list">
        ${frames.map((frame) => `
          <article class="asset-item">
            <div class="row between wrap">
              <h4>${Number(frame.time || 0).toFixed(2)}s · ${escapeHtml(frameFilename(frame.path))}</h4>
              <span class="mini-pill">文字置信度 ${Number(frame.screen_text_confidence || 0).toFixed(2)}</span>
            </div>
            <p><b>OCR:</b> ${escapeHtml(frame.ocr_text || "未识别到文字")}</p>
            <p><b>画面摘要:</b> ${escapeHtml(frame.visual_summary || "n/a")}</p>
            <p class="small-text"><b>画面指标:</b> ${escapeHtml(frame.frame_metrics ? JSON.stringify(frame.frame_metrics) : "n/a")}</p>
          </article>
        `).join("") || emptyState("暂无视觉帧记录", "visual-analysis.json 中没有 frames。")}
      </div>
    </section>
  `;
}

function renderAssetsTab(detail) {
  const assets = detail.files.content_assets;
  if (!assets) return `<section class="panel pad">${emptyState("创作底稿尚不可用", "等待“生成创作底稿”阶段完成。")}</section>`;
  return `
    <div class="grid two">
      <section class="panel pad stack">
        <div class="section-title"><h3>创作底稿</h3><p>这里保留事实锚点、读者场景和选题方向，最终稿会转化为原创表达。</p></div>
        <div class="asset-item"><h4>一句话总结</h4><p>${escapeHtml(assets.one_sentence_summary)}</p></div>
        ${renderAssetList("核心观点", assets.core_points, (item) => `
          <h4>${escapeHtml(item.point)}</h4>
          <p>${escapeHtml(item.why_it_matters || "")}</p>
          <p class="small-text">${escapeHtml((item.evidence || []).map((e) => `${e.type}@${e.time}s ${e.text || e.source_text || ""}`).join(" / "))}</p>
        `)}
        ${renderAssetList("金句", assets.golden_quotes, (item) => `<h4>${escapeHtml(item.quote)}</h4><p class="small-text">${escapeHtml(item.time)}s · ${escapeHtml(item.rewrite_note || "")}</p>`)}
      </section>
      <section class="panel pad stack">
        ${renderAssetList("章节", assets.chapters, (item) => `<h4>${escapeHtml(item.title)}</h4><p>${escapeHtml(item.summary || "")}</p><p class="small-text">${escapeHtml(item.start)}s - ${escapeHtml(item.end)}s</p>`)}
        ${renderAssetList("步骤", assets.steps, (item) => `<h4>${escapeHtml(item.step)}</h4><p class="small-text">evidence_time: ${escapeHtml(item.evidence_time)}</p>`)}
        ${renderChipBlock("受众", assets.audience)}
        ${renderChipBlock("痛点", assets.pain_points)}
        ${renderChipBlock("平台选题角度", assets.xiaohongshu_angles)}
        ${renderAssetList("来源证据", assets.source_evidence, (item) => `<h4>${escapeHtml(item.claim)}</h4><p class="small-text">${escapeHtml(item.source_type)} · ${escapeHtml(item.time)}s · ${escapeHtml(item.source_text || item.source_path || "")}</p>`)}
      </section>
    </div>
  `;
}

function renderAssetList(title, items, renderer) {
  if (!items?.length) return `<div class="asset-item"><h4>${escapeHtml(title)}</h4><p class="small-text">暂无数据</p></div>`;
  return `
    <div class="section-title"><h3>${escapeHtml(title)}</h3></div>
    <div class="split-list">
      ${items.map((item) => `<article class="asset-item">${renderer(item)}</article>`).join("")}
    </div>
  `;
}

function renderChipBlock(title, items) {
  return `
    <div class="asset-item">
      <h4>${escapeHtml(title)}</h4>
      <div class="chip-list">${(items || []).map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("") || '<span class="small-text">暂无数据</span>'}</div>
    </div>
  `;
}

function renderPostTab(detail, route) {
  const post = routePost(detail, route);
  if (!post) return `<section class="panel pad">${emptyState(route.emptyPostTitle, "配置 LLM 并重跑文案生成后，这里会显示标题、正文和配图规划。")}</section>`;
  const textOnly = isTextOnlyProject(detail);
  const prompts = routePrompts(detail, route)?.image_prompts || [];
  const quality = detail.files?.[route.quality];
  return `
    <div class="grid two">
      <section class="panel pad post-preview">
        <div class="row between wrap">
          <div class="section-title"><h3>${escapeHtml(route.postPreviewTitle)}</h3><p>正文适合直接复制，仍建议发布前人工复核事实与授权。</p></div>
          <button class="ghost-button" data-action="copy-text" data-copy-target="${escapeHtml(route.bodyCopyId)}" type="button">复制正文</button>
        </div>
        ${renderChipBlock("标题候选", post.titles)}
        <div class="asset-item"><h4>封面文案</h4><p>${escapeHtml(post.cover_text)}</p></div>
        <div class="asset-item"><h4>开头钩子</h4><p>${escapeHtml(post.hook)}</p></div>
        <div id="${escapeHtml(route.bodyCopyId)}" class="post-body">${escapeHtml(post.body)}</div>
        ${renderChipBlock("标签", post.hashtags)}
        <div class="asset-item"><h4>发布建议</h4><p>${escapeHtml(post.publish_suggestion || "")}</p></div>
        ${renderQualitySummary(quality, post)}
        <div class="download-strip">
          ${detail.status.outputs?.[route.postDocx]
            ? `<a class="ghost-button" href="/api/projects/${detail.projectId}/files/${route.postDocx}">下载 Word</a>`
            : '<span class="ghost-button disabled" aria-disabled="true">下载 Word</span>'}
          ${detail.status.outputs?.[route.postMd]
            ? `<a class="ghost-button" href="/api/projects/${detail.projectId}/files/${route.postMd}">下载 Markdown</a>`
            : '<span class="ghost-button disabled" aria-disabled="true">下载 Markdown</span>'}
        </div>
      </section>
      ${textOnly || !route.supportsImages ? "" : `<section class="panel pad stack">
        ${renderAssetList("配图规划", post.image_plan, (item) => `
          <h4>第 ${escapeHtml(item.page)} 页 · ${escapeHtml(item.role)} · ${escapeHtml(item.caption)}</h4>
          <p>${escapeHtml(item.content_point || "")}</p>
          <p class="small-text">来源关键帧：${escapeHtml(item.source_frame_time)}s</p>
        `)}
        ${renderAssetList("图片提示词", prompts, (item) => `
          <h4>第 ${escapeHtml(item.page)} 页 · ${escapeHtml(item.caption)}</h4>
          <p>${escapeHtml(item.image_prompt)}</p>
          <p class="small-text"><b>反向提示词:</b> ${escapeHtml(item.negative_prompt)}</p>
        `)}
      </section>`}
    </div>
  `;
}

function renderFilesTab(detail) {
  const outputs = detail.status.outputs || {};
  const textOnly = isTextOnlyProject(detail);
  return `
    <section class="panel pad stack">
      <div class="section-title">
        <h3>文件与下载</h3>
        <p>文件下载全部走后端 /files/{kind}、完整 ZIP 或素材 ZIP。</p>
      </div>
      <div class="download-grid">
        <a id="download-all" class="download-card" href="/api/projects/${detail.projectId}/download">
          <b>完整 ZIP</b><span>runtime/projects/${escapeHtml(detail.projectId)}</span>
        </a>
        ${textOnly ? "" : (outputs.keyframes ? `<a id="download-frames-zip" class="download-card" href="/api/projects/${detail.projectId}/download/frames">
          <b>关键帧素材 ZIP</b><span>frames/*.jpg</span>
        </a>` : `<div id="download-frames-zip" class="download-card disabled" aria-disabled="true">
          <b>关键帧素材 ZIP</b><span>frames/*.jpg</span>
        </div>`)}
        ${FILE_KINDS.map(([kind, label, path]) => outputs[kind] ? `
          <a id="download-${kind.replaceAll("_", "-")}" class="download-card" href="/api/projects/${detail.projectId}/files/${kind}">
            <b>${escapeHtml(label)}</b>
            <span class="mono">${escapeHtml(path)}</span>
          </a>
        ` : `
          <div id="download-${kind.replaceAll("_", "-")}" class="download-card disabled" aria-disabled="true">
            <b>${escapeHtml(label)}</b>
            <span class="mono">${escapeHtml(path)}</span>
          </div>
        `).join("")}
      </div>
      <pre class="json-view">${prettyJson(outputs)}</pre>
    </section>
  `;
}

async function renderLlmSettings() {
  shell({
    title: "文案与生图 API 配置",
    mark: "LLM",
    subtitle: "文案 LLM 和生图 API 分开配置，不在前端回显 API Key。",
    body: loadingPanel("LLM 配置"),
  });
  let settings;
  let imageSettings;
  try {
    [settings, imageSettings] = await Promise.all([apiGet("/api/settings/llm"), apiGet("/api/settings/image")]);
    state.imageSettings = imageSettings;
  } catch (error) {
    shell({ title: "文案与生图 API 配置", mark: "LLM", subtitle: "设置读取失败。", body: errorState(errorText(error)) });
    return;
  }
  shell({
    title: "文案与生图 API 配置",
    mark: "LLM",
    subtitle: "保存后写入 .env 并刷新后端 provider；文案和生图互不混用。",
    actions: `
      <button class="ghost-button" data-action="test-llm" type="button">测试文案连接</button>
      <button class="ghost-button" data-action="test-image-api" type="button">检查生图配置</button>
      <button class="ghost-button" data-action="test-image-api-real" type="button">真实生成测试图</button>
    `,
    body: `
      <div class="grid two">
        <section class="panel">
          <div class="panel-header">
            <div><h3>文案 LLM 配置</h3><p>生成目标平台文章、标题、标签和必要的图片提示词。</p></div>
            <span class="status-pill ${settings.api_key_configured ? "status-ok" : "status-warning"}">${settings.api_key_configured ? "密钥已配置" : "密钥未配置"}</span>
          </div>
          <div class="panel-body">
            <form id="llm-settings-form" class="form-grid">
              <label class="field">接口地址 Base URL<input name="base_url" value="${escapeHtml(settings.base_url || "")}" placeholder="https://api.openai.com/v1" /></label>
              <label class="field">模型名称<input name="model" value="${escapeHtml(settings.model || "")}" placeholder="gpt-4o-mini" /></label>
              <label class="field">API Key<input name="api_key" type="password" autocomplete="new-password" placeholder="留空保持当前密钥" /></label>
              <div class="field-row">
                <label class="field">是否要求 API Key
                  <select name="require_api_key">
                    ${["auto", "true", "false"].map((item) => `<option value="${item}" ${settings.require_api_key === item ? "selected" : ""}>${item}</option>`).join("")}
                  </select>
                </label>
                <label class="field">最大 Tokens<input name="max_tokens" type="number" min="1" max="64000" value="${escapeHtml(settings.max_tokens || 1200)}" /></label>
              </div>
              <div class="field-row">
                <label class="field">超时时间（毫秒）<input name="timeout_ms" type="number" min="1000" max="600000" value="${escapeHtml(settings.timeout_ms || 60000)}" /></label>
                <label class="field">最大输入字符<input name="max_chars" type="number" min="1000" max="2000000" value="${escapeHtml(settings.max_chars || 60000)}" /></label>
              </div>
              <label class="field">失败重试次数<input name="retry_attempts" type="number" min="1" max="10" value="${escapeHtml(settings.retry_attempts || 3)}" /></label>
              <button class="button" type="submit">保存配置</button>
              <div id="llm-settings-message"></div>
            </form>
          </div>
        </section>
        <section class="panel pad stack">
          <div class="section-title"><h3>连接自检</h3><p>调用 /api/llm/self-test，结果不会包含密钥明文。</p></div>
          <div class="meta-grid">
            <div class="meta-item"><span>需要鉴权</span><b>${escapeHtml(settings.auth_required)}</b></div>
            <div class="meta-item"><span>密钥来源</span><b>${escapeHtml(settings.api_key_source || "none")}</b></div>
            <div class="meta-item"><span>环境文件</span><b class="mono">${escapeHtml(settings.env_path || "")}</b></div>
          </div>
          <pre id="llm-self-test-result" class="json-view">${prettyJson({ status: "waiting" })}</pre>
        </section>
      </div>
      <div class="grid two" style="margin-top: 16px">
        <section class="panel">
          <div class="panel-header">
            <div><h3>生图 API 配置</h3><p>用于外部 Images API；未启用时使用本地模板生成 PNG。</p></div>
            <span class="status-pill ${imageSettings.enabled ? (imageSettings.api_key_configured || !imageSettings.auth_required ? "status-ok" : "status-warning") : "status-warning"}">
              ${imageSettings.enabled ? "外部生图已启用" : "使用本地模板"}
            </span>
          </div>
          <div class="panel-body">
            <form id="image-settings-form" class="form-grid">
              <label class="check-field">
                <input name="enabled" type="checkbox" ${imageSettings.enabled ? "checked" : ""} />
                <span>启用外部生图 API</span>
              </label>
              <label class="field">接口地址 Base URL<input name="base_url" value="${escapeHtml(imageSettings.base_url || "")}" placeholder="https://api.openai.com/v1" /></label>
              <label class="field">生图模型<input name="model" value="${escapeHtml(imageSettings.model || "")}" placeholder="gpt-image-1 / dall-e-3 / provider-model" /></label>
              <label class="field">API Key<input name="api_key" type="password" autocomplete="new-password" placeholder="留空保持当前密钥" /></label>
              <div class="field-row">
                <label class="field">是否要求 API Key
                  <select name="require_api_key">
                    ${["auto", "true", "false"].map((item) => `<option value="${item}" ${imageSettings.require_api_key === item ? "selected" : ""}>${item}</option>`).join("")}
                  </select>
                </label>
                <label class="field">图片尺寸<input name="size" value="${escapeHtml(imageSettings.size || "1024x1024")}" placeholder="1024x1024" /></label>
              </div>
              <label class="field">超时时间（毫秒）<input name="timeout_ms" type="number" min="1000" max="600000" value="${escapeHtml(imageSettings.timeout_ms || 120000)}" /></label>
              <button class="button" type="submit">保存生图配置</button>
              <div id="image-settings-message"></div>
            </form>
          </div>
        </section>
        <section class="panel pad stack">
          <div class="section-title"><h3>生图自检</h3><p>“检查配置”只验证环境变量和依赖；“真实生成测试图”会调用外部 Images API 并产生一次真实用量。</p></div>
          <div class="meta-grid">
            <div class="meta-item"><span>启用外部 API</span><b>${escapeHtml(imageSettings.enabled)}</b></div>
            <div class="meta-item"><span>需要鉴权</span><b>${escapeHtml(imageSettings.auth_required)}</b></div>
            <div class="meta-item"><span>密钥来源</span><b>${escapeHtml(imageSettings.api_key_source || "none")}</b></div>
            <div class="meta-item"><span>默认渲染器</span><b>${escapeHtml(imageSettings.fallback_renderer || "pillow_template_v1")}</b></div>
          </div>
          <pre id="image-self-test-result" class="json-view">${prettyJson({ status: "waiting" })}</pre>
        </section>
      </div>
    `,
  });
}

async function renderRuntimeDoctor() {
  shell({
    title: "运行诊断",
    mark: "RT",
    subtitle: "yt-dlp、ffmpeg、Whisper、PySceneDetect、OpenCV、OCR、LLM 和 runtime 权限诊断。",
    actions: `<button class="ghost-button" data-action="refresh-runtime" type="button">刷新</button>`,
    body: loadingPanel("运行诊断"),
  });
  try {
    const doctor = await apiGet("/api/system/doctor");
    shell({
      title: "运行诊断",
      mark: "RT",
      subtitle: "全部数据来自 /api/system/doctor，不展示假状态。",
      actions: `<button class="ghost-button" data-action="refresh-runtime" type="button">刷新</button>`,
      body: renderDoctorBody(doctor),
    });
  } catch (error) {
    shell({ title: "运行诊断", mark: "RT", subtitle: "诊断失败。", body: errorState(errorText(error)) });
  }
}

function renderDoctorBody(doctor) {
  return `
    <div class="grid">
      ${doctor.warnings?.length ? warningState(doctor.warnings.map(warningZh).join(" / ")) : ""}
      <section class="kpi-grid">
        ${Object.entries(doctor.ready_for || {}).map(([key, value]) => `
          <div class="kpi"><b>${value ? "可用" : "缺失"}</b><span>${escapeHtml(readyForLabel(key))}</span></div>
        `).join("")}
      </section>
      <div class="grid two">
        <section class="panel pad">
          <div class="section-title"><h3>系统命令</h3><p>系统命令路径和版本。</p></div>
          <div class="diagnostic-matrix">${Object.entries(doctor.commands || {}).map(([name, item]) => renderDiagnosticCard(name, item)).join("")}</div>
        </section>
        <section class="panel pad">
          <div class="section-title"><h3>Python 模块</h3><p>运行链路依赖模块。</p></div>
          <div class="diagnostic-matrix">${Object.entries(doctor.modules || {}).map(([name, item]) => renderDiagnosticCard(name, item)).join("")}</div>
        </section>
      </div>
      <div class="grid two">
        <section class="panel pad">
          <div class="section-title"><h3>OCR 提供方</h3></div>
          <pre class="json-view">${prettyJson(doctor.ocr || {})}</pre>
        </section>
        <section class="panel pad">
          <div class="section-title"><h3>LLM 提供方</h3></div>
          <pre class="json-view">${prettyJson(doctor.llm || {})}</pre>
        </section>
      </div>
      <div class="grid two">
        <section class="panel pad">
          <div class="section-title"><h3>生图提供方</h3></div>
          <pre class="json-view">${prettyJson(doctor.image || {})}</pre>
        </section>
        <section class="panel pad">
          <div class="section-title"><h3>Runtime 目录</h3></div>
          <pre class="json-view">${prettyJson(doctor.runtime || {})}</pre>
        </section>
      </div>
    </div>
  `;
}

function renderDiagnosticCard(name, item) {
  return `
    <article class="diagnostic-card">
      <div class="row between">
        <b>${escapeHtml(name)}</b>
        <span class="mini-pill ${item.available ? "status-ok" : "status-error"}">${item.available ? "可用" : "缺失"}</span>
      </div>
      <code>${escapeHtml(item.path || item.version || item.error || "n/a")}</code>
    </article>
  `;
}

function renderNotFound() {
  shell({
    title: "页面不存在",
    mark: "404",
    subtitle: "这个路由还没有定义。",
    body: `<section class="panel pad">${emptyState("页面不存在", "返回生产工作台继续操作。")}<p><a class="button" href="/dashboard" data-route="/dashboard">返回生产工作台</a></p></section>`,
  });
}

async function renderRoute() {
  stopPolling();
  await refreshHealth();
  const route = routeInfo();
  if (route.name === "dashboard") return renderDashboard();
  if (route.name === "batches") return renderBatches();
  if (route.name === "batch-detail") return renderBatchDetail(route.batchId);
  if (route.name === "projects") return renderProjects();
  if (route.name === "project-detail") return renderProjectDetail(route.projectId);
  if (route.name === "llm") return renderLlmSettings();
  if (route.name === "runtime") return renderRuntimeDoctor();
  return renderNotFound();
}

function startPolling(projectId) {
  stopPolling();
  state.pollTimer = window.setInterval(async () => {
    try {
      const status = await loadStatus(projectId);
      const route = CONTENT_ROUTES[state.pendingImageGenerationRoute] || currentContentRoute();
      const selectedRouteStatus = routeStatus(status, route);
      const textOnly = Boolean(status.text_only || state.workbenchDetail?.record?.text_only);
      if (!textOnly && route.supportsImages && status.status === route.completedStatus && state.pendingImageGenerationProjectId === projectId) {
        if (selectedRouteStatus.can_generate_images && !status.outputs?.[route.cards]) {
          try {
            await apiPost(`/api/projects/${projectId}/${route.imagePath}`, { style: "clean" });
            toast(route.articleReadyToast);
          } catch (error) {
            state.pendingImageGenerationProjectId = "";
            state.pendingImageGenerationRoute = "";
            toast(`生图 API 启动失败：${errorText(error)}`);
            await renderRoute();
            return;
          }
        } else {
          state.pendingImageGenerationProjectId = "";
          state.pendingImageGenerationRoute = "";
        }
      }
      if (textOnly || !route.supportsImages || ["completed", "failed", "stopped"].includes(status.status)) {
        state.pendingImageGenerationProjectId = "";
        state.pendingImageGenerationRoute = "";
      }
      if (!shouldContinuePolling(projectId, status.status)) {
        stopPolling();
        await renderRoute();
        return;
      }
      if (routeInfo().name === "dashboard") {
        await loadWorkbenchDetail(projectId).catch(() => null);
        const statusCard = document.querySelector("#workbench-status-card");
        const analysisPanel = document.querySelector("#analysis-readable-panel");
        const producePanel = document.querySelector("#produce-panel");
        if (statusCard) statusCard.outerHTML = renderWorkbenchStatusCard();
        if (analysisPanel) analysisPanel.outerHTML = renderAnalysisReadablePanel(state.workbenchDetail);
        if (producePanel) producePanel.outerHTML = renderProducePanel(state.workbenchDetail);
      }
    } catch {
      stopPolling();
    }
  }, 1800);
}

function stopPolling() {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
  if (state.batchPollTimer) {
    window.clearInterval(state.batchPollTimer);
    state.batchPollTimer = null;
  }
}

function startBatchPolling(batchId = "") {
  if (state.batchPollTimer) window.clearInterval(state.batchPollTimer);
  state.batchPollTimer = window.setInterval(async () => {
    try {
      if (batchId) {
        const batch = await loadBatch(batchId);
        if (!isBatchRunning(batch.status)) {
          stopPolling();
          await renderBatchDetail(batchId);
          return;
        }
        const body = document.querySelector("#batch-detail-body");
        if (body) body.outerHTML = renderBatchDetailBody(batch);
        return;
      }
      await loadBatches();
      const panel = document.querySelector("#batch-list-panel");
      if (panel) panel.outerHTML = renderBatchListPanel();
      if (!state.batches.some((batch) => isBatchRunning(batch.status))) stopPolling();
    } catch {
      stopPolling();
    }
  }, 1800);
}

async function submitProject(form) {
  const submitButton = form.querySelector("#submit-button");
  const errorBox = form.querySelector("#create-error");
  submitButton.disabled = true;
  submitButton.textContent = "分析中...";
  errorBox.innerHTML = "";
  const payload = {
    url: form.elements.url.value.trim(),
    target_platform: currentContentRoute().key,
    language: form.elements.language.value,
    style: form.elements.style.value,
    max_frames: Number(form.elements.max_frames.value || 12),
    use_whisper: form.elements.use_whisper.checked,
    use_ocr: form.elements.use_ocr.checked,
    text_only: form.elements.text_only.checked,
  };
  try {
    const created = await apiPost("/api/projects/analyze", payload);
    state.activeProjectId = created.project_id;
    window.sessionStorage.setItem("xhs.activeProjectId", created.project_id);
    await loadProjects();
    await loadStatus(created.project_id);
    toast(`已开始分析解析：${created.project_id}`);
    await renderDashboard();
  } catch (error) {
    errorBox.innerHTML = errorState(errorText(error));
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "一键分析解析";
  }
}

function syncBatchTextOnlyControls(form) {
  if (!form) return;
  const textOnly = Boolean(form.elements.text_only?.checked);
  const maxFrames = form.elements.max_frames;
  const useOcr = form.elements.use_ocr;
  if (maxFrames) maxFrames.disabled = textOnly;
  if (useOcr) {
    useOcr.disabled = textOnly;
    if (textOnly) useOcr.checked = false;
    else if (!useOcr.checked) useOcr.checked = true;
  }
}

async function submitBatch(form) {
  const button = form.querySelector("#start-batch-button");
  const errorBox = form.querySelector("#batch-create-error");
  const urls = String(form.elements.urls.value || "")
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  if (!urls.length) {
    errorBox.innerHTML = errorState("请至少输入一个视频链接。");
    return;
  }
  button.disabled = true;
  button.textContent = "正在创建...";
  errorBox.innerHTML = "";
  const payload = {
    urls,
    target_platform: form.elements.target_platform.value,
    language: form.elements.language.value,
    style: form.elements.style.value,
    max_frames: Number(form.elements.max_frames.value || 12),
    use_whisper: form.elements.use_whisper.checked,
    use_ocr: form.elements.use_ocr.checked,
    text_only: form.elements.text_only.checked,
    continue_on_error: form.elements.continue_on_error.checked,
  };
  try {
    const created = await apiPost("/api/batches", payload);
    toast(`批量队列已创建：${created.batch_id}`);
    navigate(`/batches/${created.batch_id}`);
  } catch (error) {
    errorBox.innerHTML = errorState(errorText(error));
    button.disabled = false;
    button.textContent = "开始顺序处理";
  }
}

async function cancelBatch(batchId) {
  if (!window.confirm("确定停止整个批次吗？当前任务和所有待处理链接都会停止，已生成的 Word 会保留。")) return;
  try {
    await apiPost(`/api/batches/${batchId}/cancel`);
    toast("批次已停止，现有文档已保留");
    await renderRoute();
  } catch (error) {
    toast(errorText(error));
  }
}

async function produceActiveProject() {
  if (!state.activeProjectId) {
    toast("请先完成一键分析解析。");
    return;
  }
  const route = currentContentRoute();
  const detail = state.workbenchDetail;
  const status = detail?.status || state.activeStatus || {};
  const selectedRouteStatus = routeStatus(status, route);
  const hasPost = routeHasPost(detail, route);
  const hasCards = routeHasCards(detail, route);
  const textOnly = isTextOnlyProject(detail);
  try {
    if (!textOnly && route.supportsImages && hasPost && !hasCards && selectedRouteStatus.can_generate_images) {
      await apiPost(`/api/projects/${state.activeProjectId}/${route.imagePath}`, { style: "clean" });
      state.pendingImageGenerationProjectId = "";
      state.pendingImageGenerationRoute = "";
      toast("已调用独立生图 API，开始生成图片卡片。");
      await renderDashboard();
      return;
    }
    if (!state.llmSettings || (state.llmSettings.auth_required && !state.llmSettings.api_key_configured)) {
      toast(route.llmMissingToast);
      return;
    }
    const contentAssets = collectContentAssetsFromEditors();
    state.pendingImageGenerationProjectId = textOnly || !route.supportsImages ? "" : state.activeProjectId;
    state.pendingImageGenerationRoute = textOnly || !route.supportsImages ? "" : route.key;
    await apiPost(`/api/projects/${state.activeProjectId}/${route.producePath}`, { content_assets: contentAssets });
    toast(textOnly || !route.supportsImages ? `已开始生成${route.label}文章。` : route.produceToast);
    await renderDashboard();
  } catch (error) {
    state.pendingImageGenerationProjectId = "";
    state.pendingImageGenerationRoute = "";
    toast(errorText(error));
    await renderDashboard();
  }
}

function collectContentAssetsFromEditors() {
  const detail = state.workbenchDetail;
  const assets = structuredClone(detail?.files?.content_assets || {});
  const summary = document.querySelector("#asset-summary");
  if (summary) assets.one_sentence_summary = summary.value.trim();

  document.querySelectorAll("[data-edit-field]").forEach((node) => {
    const field = node.dataset.editField;
    const index = Number(node.dataset.index);
    const key = node.dataset.key;
    if (!Array.isArray(assets[field]) || !assets[field][index]) return;
    assets[field][index][key] = node.value.trim();
  });

  document.querySelectorAll("[data-string-list]").forEach((node) => {
    const field = node.dataset.stringList;
    assets[field] = node.value.split(/\n+/).map((item) => item.trim()).filter(Boolean);
  });
  return assets;
}

async function saveContentAssets() {
  if (!state.activeProjectId || !state.workbenchDetail?.files?.content_assets) return;
  const message = document.querySelector("#analysis-save-message");
  try {
    await apiPatch(`/api/projects/${state.activeProjectId}/content-assets`, collectContentAssetsFromEditors());
    if (message) message.innerHTML = `<div class="empty-state">解析结果已保存，会作为下一次“一键产出图文”的输入。</div>`;
    toast("解析结果已保存");
    await loadWorkbenchDetail(state.activeProjectId);
  } catch (error) {
    if (message) message.innerHTML = errorState(errorText(error));
    toast(errorText(error));
  }
}

function collectPostFromEditors(route = currentContentRoute()) {
  const detail = state.workbenchDetail;
  const post = structuredClone(routePost(detail, route) || {});
  const titles = document.querySelector('[data-post-field="titles"]');
  const coverText = document.querySelector('[data-post-field="cover_text"]');
  const hook = document.querySelector('[data-post-field="hook"]');
  const body = document.querySelector('[data-post-field="body"]');
  const hashtags = document.querySelector('[data-post-field="hashtags"]');
  if (titles) post.titles = titles.value.split(/\n+/).map((item) => item.trim()).filter(Boolean);
  if (coverText) post.cover_text = coverText.value.trim();
  if (hook) post.hook = hook.value.trim();
  if (body) post.body = body.value.trim();
  if (hashtags) post.hashtags = hashtags.value.split(/\n+/).map((item) => item.trim()).filter(Boolean);
  return post;
}

async function savePost() {
  const route = currentContentRoute();
  if (!state.activeProjectId || !routePost(state.workbenchDetail, route)) return;
  const message = document.querySelector("#produce-save-message");
  try {
    await apiPatch(`/api/projects/${state.activeProjectId}/${route.postPatchPath}`, collectPostFromEditors(route));
    if (message) message.innerHTML = `<div class="empty-state">${escapeHtml(route.saveMessage)}</div>`;
    toast("文章已保存");
    await loadWorkbenchDetail(state.activeProjectId);
  } catch (error) {
    if (message) message.innerHTML = errorState(errorText(error));
    toast(errorText(error));
  }
}

function collectImageCardEdits() {
  const route = currentContentRoute();
  const cards = structuredClone(routeCards(state.workbenchDetail, route)?.cards || []);
  document.querySelectorAll("[data-card-field]").forEach((node) => {
    const index = Number(node.dataset.cardIndex);
    const field = node.dataset.cardField;
    if (!cards[index]) return;
    cards[index][field] = node.value.trim();
  });
  return { cards, style: "clean" };
}

async function saveImageCards() {
  const route = currentContentRoute();
  if (!state.activeProjectId || !routeCards(state.workbenchDetail, route)) return;
  const message = document.querySelector("#produce-save-message");
  try {
    await apiPatch(`/api/projects/${state.activeProjectId}/${route.cardsPatchPath}`, collectImageCardEdits());
    await loadWorkbenchDetail(state.activeProjectId);
    if (message) message.innerHTML = `<div class="empty-state">图片卡片已重新渲染为 PNG。</div>`;
    toast("卡片已重新渲染");
    await renderDashboard();
  } catch (error) {
    if (message) message.innerHTML = errorState(errorText(error));
    toast(errorText(error));
  }
}

async function saveLlmSettings(form) {
  const message = form.querySelector("#llm-settings-message");
  const button = form.querySelector("button[type='submit']");
  button.disabled = true;
  button.textContent = "保存中...";
  const data = new FormData(form);
  const payload = {
    base_url: String(data.get("base_url") || "").trim(),
    model: String(data.get("model") || "").trim(),
    require_api_key: String(data.get("require_api_key") || "auto"),
    max_tokens: Number(data.get("max_tokens") || 1200),
    timeout_ms: Number(data.get("timeout_ms") || 60000),
    max_chars: Number(data.get("max_chars") || 60000),
    retry_attempts: Number(data.get("retry_attempts") || 3),
  };
  const apiKey = String(data.get("api_key") || "").trim();
  if (apiKey) payload.api_key = apiKey;
  try {
    await apiPut("/api/settings/llm", payload);
    message.innerHTML = `<div class="empty-state">配置已保存。API Key 未在前端回显。</div>`;
    toast("LLM 配置已保存");
  } catch (error) {
    message.innerHTML = errorState(errorText(error));
  } finally {
    button.disabled = false;
    button.textContent = "保存配置";
  }
}

async function runLlmSelfTest() {
  const output = document.querySelector("#llm-self-test-result");
  if (!output) return;
  output.textContent = JSON.stringify({ status: "running" }, null, 2);
  try {
    const result = await apiGet("/api/llm/self-test");
    output.textContent = JSON.stringify(result, null, 2);
  } catch (error) {
    output.textContent = errorText(error);
  }
}

async function saveImageSettings(form) {
  const message = form.querySelector("#image-settings-message");
  const button = form.querySelector("button[type='submit']");
  button.disabled = true;
  button.textContent = "保存中...";
  const data = new FormData(form);
  const payload = {
    enabled: data.get("enabled") === "on",
    base_url: String(data.get("base_url") || "").trim(),
    model: String(data.get("model") || "").trim(),
    require_api_key: String(data.get("require_api_key") || "auto"),
    size: String(data.get("size") || "1024x1024").trim(),
    timeout_ms: Number(data.get("timeout_ms") || 120000),
  };
  const apiKey = String(data.get("api_key") || "").trim();
  if (apiKey) payload.api_key = apiKey;
  try {
    const saved = await apiPut("/api/settings/image", payload);
    state.imageSettings = saved;
    message.innerHTML = `<div class="empty-state">生图配置已保存。API Key 未在前端回显。</div>`;
    toast("生图 API 配置已保存");
  } catch (error) {
    message.innerHTML = errorState(errorText(error));
  } finally {
    button.disabled = false;
    button.textContent = "保存生图配置";
  }
}

async function runImageSelfTest(real = false) {
  const output = document.querySelector("#image-self-test-result");
  if (!output) return;
  output.textContent = JSON.stringify({ status: real ? "generating_test_image" : "checking_config" }, null, 2);
  try {
    const result = await apiGet(`/api/image/self-test${real ? "?real=true" : ""}`);
    output.textContent = JSON.stringify(result, null, 2);
  } catch (error) {
    output.textContent = errorText(error);
  }
}

async function deleteProject(projectId) {
  if (!window.confirm(`确认删除项目 ${projectId}？运行中的任务不会被删除。`)) return;
  try {
    await apiDelete(`/api/projects/${projectId}`);
    state.summaries.delete(projectId);
    if (state.activeProjectId === projectId) {
      state.activeProjectId = "";
      state.activeStatus = null;
      window.sessionStorage.removeItem("xhs.activeProjectId");
    }
    toast(`项目已删除：${projectId}`);
    if (routeInfo().name === "project-detail") {
      navigate("/projects");
    } else {
      await renderRoute();
    }
  } catch (error) {
    toast(errorText(error));
  }
}

async function cancelProject(projectId) {
  if (!window.confirm("确定要强制停止这个任务吗？已生成的中间产物会保留。")) return;
  try {
    await apiPost(`/api/projects/${projectId}/cancel`);
    toast("已发送强制停止，任务状态已释放");
    state.pendingImageGenerationProjectId = null;
    await renderRoute();
  } catch (error) {
    toast(errorText(error));
  }
}

async function rerunProject(projectId, scope) {
  const endpoint = scope === "visuals" ? "visuals" : "downstream";
  try {
    await apiPost(`/api/projects/${projectId}/rerun/${endpoint}`);
    state.activeProjectId = projectId;
    window.sessionStorage.setItem("xhs.activeProjectId", projectId);
    toast(scope === "visuals" ? "已开始重跑视觉/OCR" : "已开始重跑文案生成");
    await renderRoute();
  } catch (error) {
    toast(errorText(error));
  }
}

async function selectWorkbenchProject(projectId) {
  state.activeProjectId = projectId;
  window.sessionStorage.setItem("xhs.activeProjectId", projectId);
  await renderDashboard();
}

async function verifyProject(projectId) {
  try {
    const result = await apiGet(`/api/projects/${projectId}/verify`);
    openModal("产物校验", `<pre id="verification" class="json-view">${prettyJson(result)}</pre>`);
  } catch (error) {
    openModal("产物校验失败", errorState(errorText(error)));
  }
}

function openFrame(index) {
  const frame = state.modalFrames[Number(index)];
  if (!frame || !state.detail?.projectId) return;
  const src = `/api/projects/${state.detail.projectId}/frames/${frame.filename}`;
  openModal(
    `关键帧 ${frame.filename}`,
    `
      <img src="${escapeHtml(src)}" alt="${escapeHtml(frame.filename)}" />
      <div class="meta-grid">
        <div class="meta-item"><span>时间点</span><b>${Number(frame.time || 0).toFixed(3)}s</b></div>
        <div class="meta-item"><span>评分</span><b>${Number(frame.score || 0).toFixed(3)}</b></div>
        <div class="meta-item"><span>OCR</span><b>${escapeHtml(textSnippet(frame.visual?.ocr_text || "n/a", 120))}</b></div>
      </div>
      <div class="asset-item"><h4>关联字幕</h4><p>${escapeHtml(frame.related_transcript_text || "")}</p></div>
      <div class="asset-item"><h4>画面摘要</h4><p>${escapeHtml(frame.visual?.visual_summary || "")}</p></div>
    `,
  );
}

function openModal(title, body) {
  modalRoot.innerHTML = `
    <div class="modal-backdrop" data-action="close-modal">
      <section class="modal" role="dialog" aria-modal="true" aria-label="${escapeHtml(title)}">
        <div class="modal-header">
          <h3 style="margin: 0">${escapeHtml(title)}</h3>
          <button class="ghost-button" data-action="close-modal" type="button">关闭</button>
        </div>
        <div class="modal-body">${body}</div>
      </section>
    </div>
  `;
}

function closeModal() {
  modalRoot.innerHTML = "";
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制");
  } catch {
    toast("复制失败，请手动选择文本。");
  }
}

function toast(message) {
  const existing = document.querySelector(".toast");
  if (existing) existing.remove();
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = message;
  document.body.appendChild(node);
  window.setTimeout(() => node.remove(), 3200);
}

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (form.id === "project-form") {
    event.preventDefault();
    submitProject(form);
  }
  if (form.id === "batch-form") {
    event.preventDefault();
    submitBatch(form);
  }
  if (form.id === "llm-settings-form") {
    event.preventDefault();
    saveLlmSettings(form);
  }
  if (form.id === "image-settings-form") {
    event.preventDefault();
    saveImageSettings(form);
  }
});

document.addEventListener("click", async (event) => {
  const routeLink = event.target.closest("[data-route]");
  if (routeLink) {
    event.preventDefault();
    navigate(routeLink.dataset.route);
    return;
  }

  const tabButton = event.target.closest("[data-tab]");
  if (tabButton) {
    state.detailTab = tabButton.dataset.tab;
    const projectId = tabButton.dataset.projectId;
    window.history.replaceState({}, "", `/projects/${projectId}?tab=${state.detailTab}`);
    const content = document.querySelector("#detail-tab-content");
    if (content && state.detail) {
      content.innerHTML = renderDetailTabContent(state.detail);
      document.querySelectorAll(".tab-button").forEach((button) => {
        button.classList.toggle("active", button.dataset.tab === state.detailTab);
      });
    }
    return;
  }

  const actionNode = event.target.closest("[data-action]");
  if (!actionNode) return;
  const action = actionNode.dataset.action;
  if (action === "close-modal") {
    if (event.target.classList.contains("modal-backdrop") || event.target.closest("button")) closeModal();
  }
  if (action === "refresh-dashboard" || action === "refresh-batches" || action === "refresh-projects" || action === "refresh-runtime") await renderRoute();
  if (action === "set-content-route") {
    setContentRoute(actionNode.dataset.routeKey);
    await renderDashboard();
    return;
  }
  if (action === "select-project") await selectWorkbenchProject(actionNode.dataset.projectId);
  if (action === "produce-project") await produceActiveProject();
  if (action === "save-content-assets") await saveContentAssets();
  if (action === "save-post") await savePost();
  if (action === "save-image-cards") await saveImageCards();
  if (action === "copy-logs") {
    const logs = state.activeStatus?.logs || state.detail?.status?.logs || [];
    await copyText(logs.map((log) => `${safeDate(log.time)} ${statusLabel(log.status, currentContentRoute(), { logs: [log] })} ${logMessageZh(log.message)}`).join("\n"));
  }
  if (action === "copy-text") {
    const target = document.querySelector(`#${actionNode.dataset.copyTarget}`);
    if (target) await copyText(target.textContent || "");
  }
  if (action === "delete-project") await deleteProject(actionNode.dataset.projectId);
  if (action === "cancel-project") await cancelProject(actionNode.dataset.projectId);
  if (action === "cancel-batch") await cancelBatch(actionNode.dataset.batchId);
  if (action === "rerun-visuals") await rerunProject(actionNode.dataset.projectId, "visuals");
  if (action === "rerun-downstream") await rerunProject(actionNode.dataset.projectId, "downstream");
  if (action === "verify-project") await verifyProject(actionNode.dataset.projectId);
  if (action === "open-frame") openFrame(actionNode.dataset.frameIndex);
  if (action === "test-llm") await runLlmSelfTest();
  if (action === "test-image-api") await runImageSelfTest(false);
  if (action === "test-image-api-real") await runImageSelfTest(true);
});

document.addEventListener("input", (event) => {
  if (event.target.id === "transcript-search") {
    state.transcriptQuery = event.target.value;
    const content = document.querySelector("#detail-tab-content");
    if (content && state.detail) content.innerHTML = renderDetailTabContent(state.detail);
    const input = document.querySelector("#transcript-search");
    if (input) {
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }
  }
  if (event.target.id === "text_only") {
    syncTextOnlyControls(event.target.form);
  }
  if (event.target.id === "batch-text-only") {
    syncBatchTextOnlyControls(event.target.form);
  }
});

window.addEventListener("popstate", renderRoute);

renderRoute();
